# ---------------------------------------------------------------------------
# Apache 4800 cyberdeck - set up the whole mini PC in one go  (NORMAL user)
#
# Run with: install-pc.cmd (in the cyberdeck folder). Don't "Run as administrator":
# it asks for admin itself for the parts that need it.
# Safe to run again: it skips what's done and carries on where it stopped,
# including after the restart in the middle.
#
#   1. Checks the PC (Windows version, winget, internet, virtualization, space)
#   2. Admin: WSL (Docker needs it) + case network, wake-up, power
#      (setup-k12-network.ps1)
#   3. Apps: Docker Desktop, PowerToys, Tailscale, Git, Discord, Spotify, mono
#      audio, WSL limits, Minecraft files (install-apps.ps1)
#   4. Restart, then carries on by itself after you sign in
#   5. Starts Docker Desktop and downloads the Minecraft server
#   6. CurseForge key (setup-curseforge.ps1)
#   7. Minecraft settings: EULA, who may join, operators
#   8. Admin: links the PC to the Pi (setup-k12-agent.ps1) - only once the
#      Pi is set up; run this again then and it picks up here
#
#   install-pc.cmd -Check   only looks: says what's done and what it would do
#   install-pc.cmd -Redo    starts again from step 1 (each step is safe to redo)
# ---------------------------------------------------------------------------
param([string]$Stage = '', [switch]$Check, [switch]$Redo, [switch]$Resumed,
      [switch]$DoWsl, [switch]$DoNetwork)
$ErrorActionPreference = 'Continue'

$DeckHome = if ($env:DECK_SETUP_HOME) { $env:DECK_SETUP_HOME } else { 'C:\deck' }   # tests point this elsewhere
$Kit      = Join-Path $DeckHome 'setup-kit'
$Progress = Join-Path $DeckHome 'setup-progress.json'
$PiIP     = '10.48.0.2'
$Steps    = [ordered]@{
    'wsl'      = 'WSL (what Docker runs on)'
    'network'  = 'Case network, wake-up and power'
    'apps'     = 'Apps and Minecraft files'
    'restart'  = 'Restart'
    'docker'   = 'Docker Desktop + Minecraft server download'
    'curseforge' = 'CurseForge key'
    'settings' = 'Minecraft settings (EULA, who may join)'
    'agent'    = 'Link to the Pi'
}
$DockerExe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
$DockerCli = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$McImages  = @('itzg/minecraft-server:latest', 'itzg/minecraft-server:java17')   # vanilla + 1.20.1 packs

function Say($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "!!  $msg" -ForegroundColor Yellow }
function Good($msg) { Write-Host "OK  $msg" -ForegroundColor Green }
function Ask-YesNo([string]$q, [bool]$def = $true) {
    $hint = if ($def) { '[Y/n]' } else { '[y/N]' }
    while ($true) {
        $a = (Read-Host "$q $hint").Trim()
        if (-not $a) { return $def }
        if ($a -match '^(y|yes)$') { return $true }
        if ($a -match '^(n|no)$')  { return $false }
        Warn 'Type y or n.'
    }
}
function Test-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
function Invoke-Quiet([string]$exe, [string[]]$a) {
    # runs a program, hides its output, returns its exit code (never throws)
    try { $null = & $exe @a 2>&1; return $LASTEXITCODE } catch { return -1 }
}

# --- progress file: which steps are done ------------------------------------------
function Get-Progress {
    $p = @{ done = @(); bootAtApps = '' }
    if (Test-Path $Progress) {
        try {
            $j = Get-Content $Progress -Raw | ConvertFrom-Json
            $p.done = @($j.done | Where-Object { $Steps.Contains("$_") } | ForEach-Object { "$_" })
            if ($j.bootAtApps -is [string]) { $p.bootAtApps = $j.bootAtApps }
        } catch { }
    }
    return $p
}
function Save-Progress($p) {
    New-Item -ItemType Directory -Force (Split-Path $Progress) | Out-Null
    $json = [pscustomobject]@{ done = @($p.done); bootAtApps = $p.bootAtApps } | ConvertTo-Json
    [IO.File]::WriteAllText($Progress, $json, (New-Object Text.UTF8Encoding $false))
}
function Test-Done([string]$step) { return (Get-Progress).done -contains $step }
function Set-Done([string]$step) {
    if (-not $Steps.Contains($step)) { return }
    $p = Get-Progress
    if ($p.done -notcontains $step) { $p.done = @($p.done) + $step }
    Save-Progress $p
}
function Get-BootTime { return (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss') }

# --- small helpers the steps (and the tests) use -------------------------------------
function Test-McName([string]$n) { return $n -cmatch '^[A-Za-z0-9_]{3,16}$' }
function Split-McNames([string]$text) {
    # "Steve, alex_2" -> @('Steve','alex_2'); $null if any name isn't a Minecraft username
    $names = @($text -split '[,\s]+' | Where-Object { $_ })
    foreach ($n in $names) { if (-not (Test-McName $n)) { return $null } }
    return , $names
}
function Get-EnvValue([string]$path, [string]$name) {
    foreach ($l in (Get-Content $path)) { if ($l -cmatch "^$name=(.*)$") { return $Matches[1].Trim() } }
    return $null
}
function Set-EnvValue([string]$path, [string]$name, [string]$value) {
    if ($value -match '[\r\n]') { throw 'one line only' }
    $lines = @(Get-Content $path); $hit = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -cmatch "^$name=") { $lines[$i] = "$name=$value"; $hit = $true }
    }
    if (-not $hit) { $lines += "$name=$value" }
    Set-Content -Path $path -Value $lines -Encoding ASCII
}
function Get-PiKey {
    # The Pi's public key: on the drive (pi5\deck-pi.pub) or from the Pi over the case cable
    foreach ($f in (Join-Path $Kit 'pi5\deck-pi.pub'), (Join-Path (Split-Path $PSScriptRoot -Parent) 'pi5\deck-pi.pub')) {
        if (Test-Path $f) { return (Get-Content $f -Raw).Trim() }
    }
    try { return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 4 "http://${PiIP}:8080/api/pubkey").Content.Trim() } catch { return $null }
}

function Test-Internet {
    # Works = a secure page loads. Also compares this PC's clock with the web's: a wrong
    # clock makes secure downloads (every winget install) fail.
    $r = @{ ok = $false; clockOk = $true; clockText = '' }
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 -Method Head 'https://www.microsoft.com'
        $r.ok = $true
        $web = [DateTime]::Parse($resp.Headers['Date']).ToUniversalTime()
        $off = [math]::Abs(((Get-Date).ToUniversalTime() - $web).TotalMinutes)
        $r.clockOk = $off -lt 5
        $r.clockText = if ($r.clockOk) { 'right' } else { "off by $([math]::Round($off)) minutes - Settings > Time & language > Set time automatically, Sync now" }
    } catch [System.Net.WebException] {
        # a certificate error with a working connection is usually the clock
        if ($_.Exception.Status -eq 'TrustFailure') { $r.ok = $true; $r.clockOk = $false; $r.clockText = 'secure sites fail - check the date, time and time zone' }
    } catch { }
    return $r
}
function Test-Winget {
    # Not just "is it there": can it actually find an app? (A new PC often can't yet.)
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
    $null = Invoke-Quiet 'winget' @('source', 'update', '--name', 'winget')
    $out = try { winget search --id Git.Git -e --source winget --accept-source-agreements 2>&1 | Out-String } catch { '' }
    return ($LASTEXITCODE -eq 0 -and $out -match 'Git\.Git')
}
function Repair-Winget {
    Write-Host 'winget is there but cannot fetch apps. Repairing it...'
    try { Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop } catch { }
    if (Test-Winget) { return $true }
    Write-Host 'Resetting its app list needs admin - click Yes.'
    try {
        Start-Process powershell.exe -Verb RunAs -Wait -ErrorAction Stop -ArgumentList @('-NoProfile', '-Command',
            'winget source reset --force; winget source update --name winget; Start-Sleep 2')
    } catch { Warn 'Admin permission was refused.' }
    return (Test-Winget)
}

if ($Stage -eq 'lib') { return }     # tests load the functions above without running anything

# =====================================================================================
#  ADMIN PART (this same script, started elevated by the normal part)
# =====================================================================================
if ($Stage -eq 'admin') {
    if (-not (Test-Admin)) { Warn 'This part needs admin.'; exit 1 }
    if ($DoWsl) {
        Say 'Turning on WSL (Docker Desktop runs Linux containers in it)'
        $feat = @(foreach ($n in 'Microsoft-Windows-Subsystem-Linux', 'VirtualMachinePlatform') { Get-WindowsOptionalFeature -Online -FeatureName $n -ErrorAction SilentlyContinue })
        if ($feat.Count -eq 2 -and -not ($feat | Where-Object State -ne 'Enabled')) {
            Write-Host 'Already on.'
        } else {
            & wsl.exe --install --no-distribution
            if ($LASTEXITCODE -ne 0) {
                Warn 'wsl --install did not work; turning the Windows features on directly.'
                Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart | Out-Null
                Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart | Out-Null
            }
            Write-Host 'WSL is on after the restart that comes later.'
        }
        Set-Done 'wsl'
    }
    if ($DoNetwork) {
        Say 'Case network, wake-up and power (setup-k12-network)'
        Write-Host '(When it says "Press Enter to close", press Enter - this installer does the "Next" steps for you.)' -ForegroundColor DarkGray
        try {
            & (Join-Path $PSScriptRoot 'setup-k12-network.ps1')
            Set-Done 'network'
        } catch {
            Warn "The network step stopped: $($_.Exception.Message)"
            Read-Host 'Press Enter to carry on with the rest'
        }
    }
    exit 0
}

# =====================================================================================
#  CHECK THE PC
# =====================================================================================
function Get-PcFacts {
    $f = [ordered]@{}
    $os = Get-CimInstance Win32_OperatingSystem
    $f.Windows   = "$($os.Caption) (build $([Environment]::OSVersion.Version.Build))"
    $f.WinOK     = [Environment]::OSVersion.Version.Build -ge 19041 -and [Environment]::Is64BitOperatingSystem
    $f.Winget    = [bool](Get-Command winget -ErrorAction SilentlyContinue)
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $cs  = Get-CimInstance Win32_ComputerSystem
    $f.CPU       = $cpu.Name.Trim()
    $f.VirtOK    = [bool]($cs.HypervisorPresent -or $cpu.VirtualizationFirmwareEnabled)
    $f.RamGB     = [math]::Round($cs.TotalPhysicalMemory / 1GB)
    $f.FreeGB    = [math]::Round((Get-PSDrive C).Free / 1GB)
    $f.Ethernet  = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.NdisPhysicalMedium -eq 14 }).Count
    $net = Test-Internet
    $f.Internet  = $net.ok
    $f.ClockOK   = $net.clockOk
    $f.ClockText = $net.clockText
    $f.Docker    = Test-Path $DockerExe
    return $f
}
function Show-Facts($f) {
    $row = { param($ok, $name, $text) Write-Host ('  {0} {1,-16} {2}' -f $(if ($ok) { '[ok]' } else { '[!!]' }), $name, $text) -ForegroundColor $(if ($ok) { 'Gray' } else { 'Yellow' }) }
    & $row $f.WinOK    'Windows'        $f.Windows
    & $row $f.Winget   'winget'         $(if ($f.Winget) { 'found' } else { 'missing (installs apps)' })
    & $row $f.Internet 'Internet'       $(if ($f.Internet) { 'working' } else { 'no connection' })
    if ($f.Internet) { & $row $f.ClockOK 'Clock' $f.ClockText }
    & $row $f.VirtOK   'Virtualization' $(if ($f.VirtOK) { 'on' } else { 'off in the BIOS (AMD: "SVM Mode") - Docker needs it' })
    & $row ($f.RamGB -ge 24) 'Memory'   "$($f.RamGB) GB$(if ($f.RamGB -lt 24) { ' - Docker is allowed 20 GB; lower memory= in %USERPROFILE%\.wslconfig after setup' })"
    & $row ($f.FreeGB -ge 40) 'Free space' "$($f.FreeGB) GB on C:$(if ($f.FreeGB -lt 40) { ' - modpacks and backups want 40 GB+' })"
    & $row ($f.Ethernet -ge 1) 'Ethernet'  "$($f.Ethernet) port(s)$(if ($f.Ethernet -lt 1) { ' - the case switch needs one' })"
    & $row $true       'CPU'            $f.CPU
}
function Show-Progress {
    $p = Get-Progress
    foreach ($k in $Steps.Keys) {
        $done = $p.done -contains $k
        Write-Host ('  {0} {1}' -f $(if ($done) { '[done]' } else { '[    ]' }), $Steps[$k]) -ForegroundColor $(if ($done) { 'Green' } else { 'Gray' })
    }
}

Write-Host @'

 ==========================================================
   Apache 4800 cyberdeck - mini PC setup
 ==========================================================
'@ -ForegroundColor Cyan

if ($Check) {
    Say 'This PC'
    Show-Facts (Get-PcFacts)
    Say "Setup progress ($Progress)"
    Show-Progress
    Write-Host "`nNothing was changed (-Check only looks)."
    exit 0
}

if (Test-Admin) {
    Warn 'This window is running as administrator. Close it and double-click install-pc.cmd normally:'
    Warn 'some apps (Spotify) refuse to install as admin. The installer asks for admin itself when it needs it.'
    Read-Host 'Press Enter to close'; exit 1
}

# --- copy the kit to C:\deck\setup-kit, so it can carry on after the restart without the USB drive
$here = (Resolve-Path (Split-Path $PSScriptRoot -Parent)).Path.TrimEnd('\')
if ($here -ne $Kit) {
    Say "Copying the setup files to $Kit"
    New-Item -ItemType Directory -Force (Join-Path $Kit 'pi5') | Out-Null
    Copy-Item (Join-Path $here 'k12-windows') $Kit -Recurse -Force
    foreach ($f in 'install-pc.cmd', 'README.md') { if (Test-Path (Join-Path $here $f)) { Copy-Item (Join-Path $here $f) $Kit -Force } }
    $pub = Join-Path $here 'pi5\deck-pi.pub'
    if (Test-Path $pub) { Copy-Item $pub (Join-Path $Kit 'pi5') -Force }
    Get-ChildItem $Kit -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
    $fwd = @{}; foreach ($k in 'Redo', 'Resumed') { if ((Get-Variable $k).Value) { $fwd[$k] = $true } }
    & (Join-Path $Kit 'k12-windows\setup-pc.ps1') @fwd
    exit $LASTEXITCODE
}

if ($Redo) { Remove-Item $Progress -Force -ErrorAction SilentlyContinue; Write-Host 'Starting again from the top.' }
if ($Resumed) { Write-Host 'Welcome back - carrying on after the restart.' -ForegroundColor Green }

# =====================================================================================
#  1. CHECKS
# =====================================================================================
Say 'Checking this PC'
$facts = Get-PcFacts
if (-not $facts.Winget) {
    Write-Host 'winget is missing - trying to turn it on...'
    try { Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop } catch { }
    $facts.Winget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
}
Show-Facts $facts
$stop = @()
if (-not $facts.WinOK)    { $stop += 'Windows 10 (2004 or newer) or Windows 11, 64-bit, is needed.' }
if (-not $facts.Winget)   { $stop += 'winget is missing: open the Microsoft Store, update "App Installer", then run this again.' }
if (-not $facts.Internet) { $stop += 'No internet: connect to Wi-Fi, then run this again.' }
if ($facts.Internet -and -not $facts.ClockOK) { $stop += "The clock is wrong ($($facts.ClockText)). Downloads fail until it's right." }
if (-not $stop -and -not (Test-Done 'apps')) {
    Write-Host 'Checking winget can download apps...'
    if (-not (Test-Winget) -and -not (Repair-Winget)) {
        $stop += 'winget still cannot download apps. Its Microsoft Store page is opening: click Update (or Get) on "App Installer", then run this again.'
        Start-Process 'ms-windows-store://pdp/?productid=9NBLGGH4NNS1'
    } else { Good 'winget works.' }
}
if ($stop) { foreach ($s in $stop) { Warn $s }; Read-Host 'Press Enter to close'; exit 1 }
if (-not $facts.VirtOK) {
    Warn 'Virtualization looks off. Docker (the Minecraft server) will not start without it.'
    Warn 'Turn on "SVM Mode" in the BIOS (tap DEL at power-on > Advanced > CPU). You can carry on and do it at the restart.'
}
Say 'Progress'
Show-Progress

# =====================================================================================
#  2. ADMIN PART: WSL + network
# =====================================================================================
$needWsl = -not (Test-Done 'wsl'); $needNet = -not (Test-Done 'network')
if ($needNet) {
    # One Ethernet port + no Wi-Fi + that port is the internet = this step would cut the internet
    $eth  = @(Get-NetAdapter -Physical | Where-Object { $_.NdisPhysicalMedium -eq 14 -and $_.Status -eq 'Up' })
    $wifi = @(Get-NetAdapter -Physical | Where-Object { $_.NdisPhysicalMedium -eq 9 -and $_.Status -eq 'Up' })
    $ethNet = @($eth | Where-Object { (Get-NetIPConfiguration -InterfaceIndex $_.ifIndex -ErrorAction SilentlyContinue).IPv4DefaultGateway })
    if ($ethNet.Count -gt 0 -and $wifi.Count -eq 0) {
        Warn 'Your internet comes through the Ethernet cable, and there is no Wi-Fi connected.'
        Warn 'The network step turns the Ethernet port into the case network (no internet on it).'
        Warn 'Connect to Wi-Fi first, and plug the Ethernet port into the case switch.'
        if (-not (Ask-YesNo 'Skip the network step for now (run this again later to do it)?' $true)) { $needNet = $true }
        else { $needNet = $false; Write-Host 'Skipped for now.' }
    }
}
if ($needWsl -or $needNet) {
    Say 'Admin part: Windows will ask for permission (click Yes)'
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Stage', 'admin')
    if ($needWsl) { $a += '-DoWsl' }; if ($needNet) { $a += '-DoNetwork' }
    try { Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList $a -ErrorAction Stop }
    catch { Warn 'Admin permission was refused. Run install-pc.cmd again and click Yes.'; Read-Host 'Press Enter to close'; exit 1 }
    if ($needWsl -and -not (Test-Done 'wsl')) { Warn 'WSL did not finish - see the admin window. Run this again.'; Read-Host 'Press Enter to close'; exit 1 }
    if ($needNet -and -not (Test-Done 'network')) { Warn 'The network step did not finish. The rest carries on; run this again to retry it.' }
}

# =====================================================================================
#  3. APPS (as you, not admin)
# =====================================================================================
if (-not (Test-Done 'apps')) {
    if (-not (Test-Internet).ok) {
        Warn 'The internet stopped working after the network step.'
        Warn 'That happens when the Ethernet cable was your internet: that port is now the case network.'
        Warn 'Connect to Wi-Fi (taskbar > Wi-Fi), then run install-pc.cmd again - it carries on from here.'
        Read-Host 'Press Enter to close'; exit 1
    }
    Say 'Apps and Minecraft files (install-apps) - this takes a while'
    Write-Host '(When it says "Press Enter to close", press Enter - this installer carries on with the next steps.)' -ForegroundColor DarkGray
    & (Join-Path $PSScriptRoot 'install-apps.ps1')
    if (Test-Path $DockerExe) {
        $p = Get-Progress; $p.bootAtApps = Get-BootTime; Save-Progress $p
        Set-Done 'apps'
    } else {
        Warn "Docker Desktop didn't install. What winget said is in $DeckHome\setup-log.txt."
        Warn "Run install-pc.cmd again to retry (it skips what's done)."
        Read-Host 'Press Enter to close'; exit 1
    }
}

# =====================================================================================
#  4. RESTART (WSL, Fast Startup off and Docker's user group all need one)
# =====================================================================================
if (-not (Test-Done 'restart')) {
    $p = Get-Progress
    if ($p.bootAtApps -and $p.bootAtApps -ne (Get-BootTime)) {
        Set-Done 'restart'      # restarted since the apps went in
    } else {
        Say 'Restart needed'
        $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Resumed"
        Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'CyberdeckSetup' -Value $cmd
        Write-Host 'The installer opens again by itself after you sign back in, and finishes the setup.'
        if (-not $facts.VirtOK) { Write-Host 'While it restarts: tap DEL for the BIOS and turn on SVM Mode (and the settings in README Step 2).' -ForegroundColor Yellow }
        if (Ask-YesNo 'Restart now?' $true) { Restart-Computer -Force; exit 0 }
        Write-Host 'OK - restart when you are ready.'
        Read-Host 'Press Enter to close'; exit 0
    }
}
Remove-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'CyberdeckSetup' -ErrorAction SilentlyContinue

# =====================================================================================
#  5. DOCKER + MINECRAFT SERVER DOWNLOAD
# =====================================================================================
if (-not (Test-Done 'docker')) {
    Say 'Starting Docker Desktop'
    $cli = (Get-Command docker -ErrorAction SilentlyContinue).Source
    if (-not $cli) { $cli = $DockerCli }
    if ((Invoke-Quiet $cli @('info')) -ne 0) {
        if (-not (Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue)) { Start-Process $DockerExe }
        Write-Host 'In the Docker window: click Accept on its terms. Signing in to Docker is NOT needed (Skip).'
        Write-Host 'Waiting for Docker to be ready (up to 10 minutes)' -NoNewline
        $ok = $false
        for ($i = 0; $i -lt 120; $i++) {
            Start-Sleep 5; Write-Host '.' -NoNewline
            if ((Invoke-Quiet $cli @('info')) -eq 0) { $ok = $true; break }
        }
        Write-Host ''
        if (-not $ok) {
            Warn "Docker isn't ready. Check its window (terms accepted? a WSL update it asks for?)."
            Warn 'If it says virtualization is off: BIOS > SVM Mode = Enabled. Then run install-pc.cmd again.'
            Read-Host 'Press Enter to close'; exit 1
        }
    }
    Good 'Docker is running.'
    Say 'Downloading the Minecraft server (about 1 GB, once)'
    foreach ($img in $McImages) {
        & $cli pull $img
        if ($LASTEXITCODE -ne 0) { Warn "Couldn't download $img - it downloads on the first start instead." }
    }
    Set-Done 'docker'
}

# =====================================================================================
#  6. CURSEFORGE KEY
# =====================================================================================
if (-not (Test-Done 'curseforge')) {
    $secret = Join-Path $DeckHome 'secrets\cf_api_key.secret'
    if ((Test-Path $secret) -and (Get-Item $secret).Length -gt 0) {
        Write-Host "`nCurseForge key already saved."; Set-Done 'curseforge'
    } else {
        Say 'CurseForge key (modpacks need it)'
        if (Ask-YesNo 'Add your CurseForge API key now? (You can skip and run this again later.)' $true) {
            & (Join-Path $PSScriptRoot 'setup-curseforge.ps1')
            if ((Test-Path $secret) -and (Get-Item $secret).Length -gt 0) { Set-Done 'curseforge' }
        } else { Write-Host 'Skipped for now. Vanilla Minecraft works without it.' }
    }
}

# =====================================================================================
#  7. MINECRAFT SETTINGS
# =====================================================================================
$envFile = Join-Path $DeckHome 'game-servers\.env'
if (-not (Test-Done 'settings') -and (Test-Path $envFile)) {
    Say 'Minecraft server settings'
    if ((Get-EnvValue $envFile 'MC_EULA') -ne 'TRUE') {
        Write-Host "Minecraft's server only starts once you agree to its EULA: https://aka.ms/MinecraftEULA"
        if (Ask-YesNo 'Have you read it, and do you agree to it?' $false) { Set-EnvValue $envFile 'MC_EULA' 'TRUE'; Good 'EULA: agreed.' }
        else { Write-Host 'Left as not agreed. The server will not start until MC_EULA=TRUE in .env (or run this again).' }
    }
    if (-not (Get-EnvValue $envFile 'MC_WHITELIST_PLAYERS')) {
        Write-Host 'Only listed players can join. Type Minecraft usernames, separated by commas (yours first).'
        while ($true) {
            $names = Split-McNames (Read-Host 'Players (Enter to do this later)')
            if ($null -eq $names) { Warn 'Usernames are 3-16 letters, numbers or _ .'; continue }
            break
        }
        if ($names.Count) {
            Set-EnvValue $envFile 'MC_WHITELIST_PLAYERS' ($names -join ',')
            if (-not (Get-EnvValue $envFile 'MC_OPS')) {
                while ($true) {
                    $ops = Split-McNames (Read-Host "Operators (server admins) [$($names[0])]")
                    if ($null -eq $ops) { Warn 'Usernames are 3-16 letters, numbers or _ .'; continue }
                    if (-not $ops.Count) { $ops = @($names[0]) }
                    break
                }
                Set-EnvValue $envFile 'MC_OPS' ($ops -join ',')
            }
            Good "Players: $($names -join ', ')"
        }
    }
    Write-Host "More settings (difficulty, message of the day, time zone): $envFile"
    if ((Get-EnvValue $envFile 'MC_EULA') -eq 'TRUE') { Set-Done 'settings' }
}

# =====================================================================================
#  8. LINK TO THE PI
# =====================================================================================
if (-not (Test-Done 'agent')) {
    Say 'Linking the PC to the Pi'
    $key = Get-PiKey
    if (-not $key) {
        Write-Host 'The Pi is not set up yet (or not on the case cable). That is fine:'
        Write-Host 'once the Pi is set up (README Step 4), run install-pc.cmd again and it does this last step.'
    } else {
        Write-Host 'Found the Pi. Windows will ask for permission (click Yes); answer the questions in that window.'
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$(Join-Path $PSScriptRoot 'setup-k12-agent.ps1')`"", '-DeskUser', "`"$env:USERNAME`"")
        try {
            Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList $a -ErrorAction Stop
            if (Test-Path (Join-Path $DeckHome 'agent\deck-agent.ps1')) { Set-Done 'agent' }
        } catch { Warn 'Admin permission was refused. Run install-pc.cmd again to retry.' }
    }
} elseif (-not $Resumed) {
    if (Ask-YesNo "`nEverything is set up. Update the PC's deck files from this kit (after you change the cyberdeck files)?" $false) {
        $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$(Join-Path $PSScriptRoot 'setup-k12-agent.ps1')`"", '-DeskUser', "`"$env:USERNAME`"")
        try { Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList $a -ErrorAction Stop } catch { Warn 'Admin permission was refused.' }
    }
}

# =====================================================================================
#  SUMMARY
# =====================================================================================
Say 'Where things stand'
Show-Progress
$left = @($Steps.Keys | Where-Object { -not (Test-Done $_) })
$info = Join-Path $Kit 'k12-info.txt'
if (Test-Path $info) { $mac = (Get-Content $info | Where-Object { $_ -like 'MAC*' }) -replace '^[^:]*:\s*', ''; if ($mac) { Write-Host "`nPC MAC address (the Pi wakes the PC with it): $mac" } }
Write-Host @"

 Things only you can do (README has the details):
   * BIOS (DEL at power-on): Wake on LAN on, Restore on AC power loss = Power On,
     ErP off, SVM Mode on, a lower power mode if there is one
   * Tailscale: open it and sign in
   * PowerToys > Mouse Without Borders (README Step 3)
   * Shure MOTIV Mix: MV7+ noise reduction + auto level
   * On the Pi, once linked:  sudo deck-link-k12 $env:USERNAME
                              sudo deck-curseforge-setup   (same CurseForge key)
"@
if ($left) {
    Write-Host " Not done yet: $(($left | ForEach-Object { $Steps[$_] }) -join '; ')" -ForegroundColor Yellow
    Write-Host ' Run install-pc.cmd again to carry on - it skips what is done.' -ForegroundColor Yellow
} else {
    Write-Host ' All done. Start Minecraft from the Deck Panel, the Deck app, or C:\deck\game-servers\start-minecraft.cmd' -ForegroundColor Green
}
Read-Host "`nPress Enter to close"
