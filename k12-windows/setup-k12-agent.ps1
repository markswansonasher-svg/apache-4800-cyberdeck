# ---------------------------------------------------------------------------
# Apache 4800 cyberdeck - PC agent setup  (asks for admin)
#
# Run with: setup-k12-agent.cmd   (after setup-k12-network + install-apps,
#                                 and after setup-pi.sh has run on the Pi)
# Safe to run again.
#
# Lets the Pi start/stop Minecraft, read temperatures and switch travel mode:
#   * OpenSSH Server, key-only, reachable ONLY from the Pi (10.48.0.2).
#     The Pi's key is locked to deck-agent.ps1 - it can't open a shell.
#   * LibreHardwareMonitor (temperatures) with its web page on port 8085,
#     reachable ONLY from the case network; starts at sign-in
#   * Task "DeckStartDocker" so the agent can start Docker Desktop
#   * Docker Desktop starts at sign-in; checks Windows auto sign-in
#   * The "Deck" app: Start menu + desktop shortcut, optionally opens at sign-in
# ---------------------------------------------------------------------------
param([string]$DeskUser = $env:USERNAME)
$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -DeskUser `"$DeskUser`""
    exit
}
function Say($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "!!  $msg" -ForegroundColor Yellow }

$PiIP     = '10.48.0.2'
$AgentDir = 'C:\deck\agent'
$LhmDir   = 'C:\deck\lhm'
$Root     = Split-Path $PSScriptRoot -Parent        # the USB drive's cyberdeck folder
$NotCase  = @('0.0.0.0-10.47.255.255', '10.48.1.0-255.255.255.255', '::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
$isAdminUser = ($env:USERNAME -eq $DeskUser)        # elevated by consent = same account = admin
$profileDir = (Get-CimInstance Win32_UserProfile | Where-Object { $_.LocalPath -like "*\$DeskUser" } | Select-Object -First 1).LocalPath
if (-not $profileDir) { $profileDir = "C:\Users\$DeskUser" }

Write-Host "Setting up the deck agent for Windows user: $DeskUser"
if (-not $isAdminUser) {
    Warn "$DeskUser isn't an administrator (you typed another account's password)."
    Warn 'Start/stop/temps will work, but travel mode needs the PC user to be an admin.'
}

# --- 1. agent script ----------------------------------------------------------
Say "Installing deck-agent to $AgentDir"
New-Item -ItemType Directory -Force $AgentDir | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'agent\deck-agent.ps1') $AgentDir -Force
# the agent's own copy of deck-compose (it must not run a script a normal user could change)
Copy-Item (Join-Path $PSScriptRoot 'game-servers\deck-compose.ps1') $AgentDir -Force
# Only admins may change the script the Pi's key runs.
icacls $AgentDir /inheritance:r /grant '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' "${DeskUser}:(OI)(CI)RX" '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
if (-not $isAdminUser) { icacls $AgentDir /grant "${DeskUser}:(OI)(CI)M" | Out-Null }   # standard user: agent writes its log here

# Minecraft server files: newest compose.yml (modpacks, save time on stop) + helper scripts
if (Test-Path 'C:\deck\game-servers') {
    foreach ($f in 'compose.yml', 'deck-compose.ps1', 'start-minecraft.cmd', 'stop-minecraft.cmd', 'backup-minecraft.ps1') {
        Copy-Item (Join-Path $PSScriptRoot "game-servers\$f") 'C:\deck\game-servers' -Force
    }
    Write-Host 'Updated the server files in C:\deck\game-servers (your .env is untouched).'
} else { Warn 'C:\deck\game-servers missing - run install-apps.cmd first.' }

# --- 2. OpenSSH Server ------------------------------------------------------------
Say 'OpenSSH Server (key only, Pi only)'
$cap = Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH.Server*'
if ($cap.State -ne 'Installed') {
    Write-Host 'Installing (takes a minute)...'
    Add-WindowsCapability -Online -Name $cap.Name | Out-Null
}
Set-Service sshd -StartupType Automatic
Start-Service sshd      # first start creates C:\ProgramData\ssh\sshd_config + host keys

$cfgPath = 'C:\ProgramData\ssh\sshd_config'
$lines = [System.Collections.Generic.List[string]](Get-Content $cfgPath)
function Set-SshOption([string]$name, [string]$value) {
    $i = 0; $done = $false
    for (; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\s*Match\s') { break }
        if ($lines[$i] -match "^\s*#?\s*$name\s") { $lines[$i] = "$name $value"; $done = $true; break }
    }
    if (-not $done) { $lines.Insert($i, "$name $value") }
}
Set-SshOption 'PubkeyAuthentication' 'yes'
Set-SshOption 'PasswordAuthentication' 'no'
Set-Content -Path $cfgPath -Value $lines -Encoding ASCII
Restart-Service sshd

# --- 3. the Pi's key -----------------------------------------------------------------
Say "Adding the Pi's key"
$pub = $null
$usbKey = Join-Path $Root 'pi5\deck-pi.pub'
if (Test-Path $usbKey) { $pub = (Get-Content $usbKey -Raw).Trim(); Write-Host "Found $usbKey" }
if (-not $pub) {
    try { $pub = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://${PiIP}:8080/api/pubkey").Content.Trim()
          Write-Host 'Fetched the key from the Pi over the case network.' } catch { }
}
while (-not ($pub -match '^ssh-ed25519 [A-Za-z0-9+/=]{60,80}( \S+)?$')) {
    if ($pub) { Warn "That isn't an ed25519 public key." }
    Write-Host 'On the Pi run:   sudo cat /var/lib/deck/.ssh/id_ed25519.pub'
    $pub = (Read-Host 'Paste the line it prints (starts with ssh-ed25519)').Trim()
}
$blob = ($pub -split ' ')[1]
$agentCmd = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\deck\agent\deck-agent.ps1'
$entry = "restrict,from=`"$PiIP`",command=`"$agentCmd`" ssh-ed25519 $blob deck@deckpi"

if ($isAdminUser) {
    $ak = 'C:\ProgramData\ssh\administrators_authorized_keys'
} else {
    $ak = Join-Path $profileDir '.ssh\authorized_keys'
    New-Item -ItemType Directory -Force (Split-Path $ak) | Out-Null
}
$keep = @()
if (Test-Path $ak) { $keep = @(Get-Content $ak | Where-Object { $_ -and ($_ -notmatch 'deck@deckpi') -and ($_ -notmatch [regex]::Escape($blob)) }) }
Set-Content -Path $ak -Value ($keep + $entry) -Encoding ASCII
if ($isAdminUser) {
    # sshd ignores this file unless only Administrators + SYSTEM can touch it
    icacls $ak /inheritance:r /grant '*S-1-5-32-544:F' '*S-1-5-18:F' | Out-Null
} else {
    icacls $ak /inheritance:r /grant "${DeskUser}:F" '*S-1-5-18:F' '*S-1-5-32-544:F' | Out-Null
}
Write-Host "Key added to $ak (locked to deck-agent, from $PiIP only)."

# --- 4. firewall --------------------------------------------------------------------
Say 'Firewall: SSH (22) and temperatures (8085) only from the case network'
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Set-NetFirewallRule -RemoteAddress $PiIP
Get-NetFirewallRule -DisplayName 'Cyberdeck - agent ports, case network only' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
# Block beats allow in Windows Firewall, so this wins over any prompt you click later.
New-NetFirewallRule -DisplayName 'Cyberdeck - agent ports, case network only' -Direction Inbound -Action Block `
    -Protocol TCP -LocalPort 22, 8085 -RemoteAddress $NotCase -Profile Any | Out-Null
Write-Host 'Done. (The "Cyberdeck - case network" rule already allows the Pi in.)'

# --- 5. LibreHardwareMonitor ----------------------------------------------------------
Say 'LibreHardwareMonitor (temperatures for the Pi)'
$exe = Join-Path $LhmDir 'LibreHardwareMonitor.exe'
if (-not (Test-Path $exe)) {
    $found = $null
    $search = @("$profileDir\AppData\Local\Microsoft\WinGet\Packages", 'C:\Program Files\WinGet\Packages', 'C:\Program Files')
    $find = { foreach ($s in $search) { if (Test-Path $s) {
                  Get-ChildItem $s -Recurse -Filter 'LibreHardwareMonitor.exe' -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1 } } }
    $found = & $find | Select-Object -First 1
    if (-not $found -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        winget install --id LibreHardwareMonitor.LibreHardwareMonitor -e --source winget --accept-source-agreements --accept-package-agreements
        $found = & $find | Select-Object -First 1
    }
    if ($found) {
        New-Item -ItemType Directory -Force $LhmDir | Out-Null
        Copy-Item (Join-Path $found.DirectoryName '*') $LhmDir -Recurse -Force
        Write-Host "Copied LibreHardwareMonitor to $LhmDir"
    }
}
if (Test-Path $exe) {
    # its settings file: start minimised to tray, web server on 8085
    $conf = Join-Path $LhmDir 'LibreHardwareMonitor.config'
    if (Test-Path $conf) { [xml]$x = Get-Content $conf -Raw }
    else { [xml]$x = '<?xml version="1.0" encoding="utf-8"?><configuration><appSettings /></configuration>' }
    $as = $x.configuration.SelectSingleNode('appSettings')
    if (-not $as) { $as = $x.configuration.AppendChild($x.CreateElement('appSettings')) }
    $want = [ordered]@{ startMinMenuItem = 'true'; minTrayMenuItem = 'true'; minCloseMenuItem = 'true'
                        runWebServerMenuItem = 'true'; listenerPort = '8085' }
    foreach ($k in $want.Keys) {
        $n = $as.SelectSingleNode("add[@key='$k']")
        if (-not $n) { $n = $as.AppendChild($x.CreateElement('add')); $n.SetAttribute('key', $k) }
        $n.SetAttribute('value', $want[$k])
    }
    $x.Save($conf)

    # start at sign-in, elevated (it needs admin to read the sensors)
    $act = New-ScheduledTaskAction -Execute $exe -WorkingDirectory $LhmDir
    $trg = New-ScheduledTaskTrigger -AtLogOn -User $DeskUser
    $prn = New-ScheduledTaskPrincipal -UserId $DeskUser -LogonType Interactive -RunLevel Highest
    $set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName 'DeckLibreHardwareMonitor' -Action $act -Trigger $trg -Principal $prn -Settings $set -Force | Out-Null
    if (-not (Get-Process LibreHardwareMonitor -ErrorAction SilentlyContinue)) { Start-ScheduledTask -TaskName 'DeckLibreHardwareMonitor' }
    Write-Host 'LibreHardwareMonitor starts at sign-in with its web server on port 8085.'
    Write-Host 'If it asks to install the "PawnIO" driver, say yes - that is how it reads the CPU temperature.'
} else {
    Warn 'LibreHardwareMonitor not found. Download the zip from'
    Warn '  https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases'
    Warn "extract it to $LhmDir and run this script again."
}

# --- 6. Docker Desktop: start task + start at sign-in -----------------------------------
Say 'Docker Desktop'
$dd = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
if (Test-Path $dd) {
    $act = New-ScheduledTaskAction -Execute $dd
    $prn = New-ScheduledTaskPrincipal -UserId $DeskUser -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName 'DeckStartDocker' -Action $act -Principal $prn -Force `
        -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries) | Out-Null
    Write-Host 'Task DeckStartDocker created (the agent uses it if Docker is not running).'
    $ddSet = @("$profileDir\AppData\Roaming\Docker\settings-store.json", "$profileDir\AppData\Roaming\Docker\settings.json") | Where-Object { Test-Path $_ }
    foreach ($f in $ddSet) {
        try {
            $j = Get-Content $f -Raw | ConvertFrom-Json
            $key = if ($f -like '*settings-store.json') { 'AutoStart' } else { 'autoStart' }
            $j | Add-Member -NotePropertyName $key -NotePropertyValue $true -Force
            # no byte-order mark: Docker's JSON reader rejects it
            [IO.File]::WriteAllText($f, ($j | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding $false))
            Write-Host "Docker Desktop will start at sign-in ($f)."
        } catch { Warn "Couldn't edit $f - tick Settings > General > 'Start Docker Desktop when you sign in'." }
    }
    if (-not $ddSet) { Warn "Open Docker Desktop once, then tick Settings > General > 'Start Docker Desktop when you sign in'." }
} else { Warn 'Docker Desktop not installed - run install-apps.cmd first.' }

# --- 7. automatic sign-in -------------------------------------------------------------------
Say 'Automatic sign-in (needed so Docker runs after the Pi wakes the PC)'
$wl = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
if ($wl.AutoAdminLogon -eq '1') {
    Write-Host 'Already on.'
} else {
    Write-Host @'
Not on yet. Without it, a woken PC sits at the login screen and Minecraft can't start.
Easiest safe way: Microsoft's "Autologon" tool (stores the password encrypted).
You type the password into it yourself - this script never sees it.
Trade-off: anyone who powers on the PC lands on your desktop.
'@
    $ans = Read-Host 'Install and open Autologon now? [Y/n]'
    if ($ans -notmatch '^[nN]') {
        winget install --id Microsoft.Sysinternals.Autologon -e --source winget --accept-source-agreements --accept-package-agreements
        $al = Get-ChildItem "$profileDir\AppData\Local\Microsoft\WinGet\Packages", 'C:\Program Files\WinGet\Packages' -Recurse `
                -Filter 'Autologon64.exe' -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($al) { Start-Process $al.FullName } else { Warn 'Run "Autologon" from the Start menu (or Win+R: autologon).' }
        Write-Host 'In Autologon: check the user name, type your Windows password, click Enable.'
        Write-Host '(Signing in with a PIN only? Use your Microsoft account password there.)'
    }
}

# --- 8. the Deck app (the panel on the PC's own screen) ------------------------------------
Say 'Deck app (Start menu + desktop shortcut)'
$AppDir = 'C:\deck\app'
New-Item -ItemType Directory -Force $AppDir | Out-Null
# an open Deck app keeps Deck.exe locked: close it so it can be updated
Get-Process Deck -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq "$AppDir\Deck.exe" } | Stop-Process -Force -ErrorAction SilentlyContinue
foreach ($f in 'Deck.ps1', 'deck.ico', 'build-app.ps1', 'devtools.default.json') { Copy-Item (Join-Path $PSScriptRoot "app\$f") $AppDir -Force }
# the app's source: every .cs (DeckApp.cs, DeckLocal.cs = no-Pi mode, DevTools.cs = Dev tools page). Your devtools.json list is never overwritten.
Copy-Item (Join-Path $PSScriptRoot 'app\*.cs') $AppDir -Force
# Deck.exe: the native app (small, no browser). Built here with the C# compiler Windows already has.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$AppDir\build-app.ps1" -Out "$AppDir\Deck.exe"
$NativeApp = ($LASTEXITCODE -eq 0) -and (Test-Path "$AppDir\Deck.exe")
if (-not $NativeApp) { Warn 'The Deck app could not be built - the Deck shortcut opens the panel in Edge instead.' }
# the signed-in user's folders (this window may be running as another admin account)
if ($isAdminUser) {
    $desk = [Environment]::GetFolderPath('Desktop'); $menu = [Environment]::GetFolderPath('Programs')
    $boot = [Environment]::GetFolderPath('Startup')
} else {
    $desk = Join-Path $profileDir 'Desktop'
    $menu = Join-Path $profileDir 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs'
    $boot = Join-Path $menu 'Startup'
}
function New-DeckShortcut([string]$path, [string]$extra) {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($path)
    if ($NativeApp) {
        $s.TargetPath = "$AppDir\Deck.exe"
        $s.Arguments = $(if ($extra) { '--at-sign-in' } else { '' })
        $s.WindowStyle = 1
    } else {
        # fallback: the panel in an Edge app window
        $s.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$AppDir\Deck.ps1`" $extra".Trim()
        $s.WindowStyle = 7      # starts minimized, so no console window flashes up
    }
    $s.IconLocation = "$AppDir\deck.ico,0"
    $s.Description = 'Cyberdeck control: Minecraft, temperatures, console, Wi-Fi, pack up'
    $s.WorkingDirectory = $AppDir
    $s.Save()
}
foreach ($dir in @($desk, $menu)) {
    if (Test-Path $dir) { New-DeckShortcut (Join-Path $dir 'Deck.lnk') ''; Write-Host "Shortcut: $dir\Deck.lnk" }
}
$startLnk = Join-Path $boot 'Deck.lnk'
$ans = Read-Host 'Open the Deck app every time Windows signs in (good if the case screen is the PC''s)? [Y/n]'
if ($ans -notmatch '^[nN]') {
    New-Item -ItemType Directory -Force $boot | Out-Null
    New-DeckShortcut $startLnk '-AtSignIn'
    Write-Host 'The Deck app opens at sign-in (it waits for the Pi to start).'
} elseif (Test-Path $startLnk) {
    Remove-Item $startLnk -Force; Write-Host 'Removed the open-at-sign-in shortcut.'
}

# --- done ---------------------------------------------------------------------------------
$info = Join-Path $Root 'k12-info.txt'
if (Test-Path $info) {
    $t = Get-Content $info | Where-Object { $_ -notmatch '^(K12 user|PC user|Link the Pi)' }
    Set-Content $info -Encoding ASCII -Value ($t + "PC user           : $DeskUser" + "Link the Pi       : sudo deck-link-k12 $DeskUser")
}
Write-Host @"

============================================================
 Agent installed.  Now on the Pi (SSH or keyboard), run:

     sudo deck-link-k12 $DeskUser

 It tests the link and prints "Linked." when it works.
 Then open the Deck Panel - temperatures appear within a minute.
============================================================
"@
Read-Host 'Press Enter to close'
