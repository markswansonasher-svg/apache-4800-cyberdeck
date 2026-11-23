# ---------------------------------------------------------------------------
# Apache 4800 cyberdeck - PC apps and user settings  (run as NORMAL user)
#
# Run with: install-apps.cmd   (don't "Run as administrator" - Spotify refuses
# to install elevated; winget asks for admin itself when an app needs it)
#
#   * winget: PowerToys, Docker Desktop, Discord, Spotify, Tailscale, Git
#   * Windows Mono audio ON (the M-Track Solo input 2 is mono)
#   * WSL2 limits so Docker/Minecraft can't starve the desktop
#   * Copies the Minecraft server files to C:\deck\game-servers
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Continue'
function Say($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "!!  $msg" -ForegroundColor Yellow }

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Warn 'winget not found. Update "App Installer" from the Microsoft Store, then run this again.'
    Read-Host 'Press Enter to close'; exit 1
}

# --- apps -------------------------------------------------------------------
$apps = [ordered]@{
    'Microsoft.PowerToys'  = 'PowerToys (Mouse Without Borders)'
    'Docker.DockerDesktop' = 'Docker Desktop (Minecraft server)'
    'Tailscale.Tailscale'  = 'Tailscale (reach the deck from anywhere)'
    'Git.Git'              = 'Git (CI runner)'
    'Discord.Discord'      = 'Discord'
    'Spotify.Spotify'      = 'Spotify'
}
# Every winget answer goes to C:\deck\setup-log.txt, so a failure can be looked up afterwards.
$log = 'C:\deck\setup-log.txt'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Write-Log([string]$t) { Add-Content -Path $log -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $t" -Encoding UTF8 }
function Test-Installed([string]$id) {
    [bool](winget list --id $id -e --accept-source-agreements 2>$null | Select-String -SimpleMatch $id)
}
# A brand-new Windows often hasn't downloaded winget's app list yet: refresh it first.
Say 'Refreshing the winget app list'
winget source update --name winget 2>&1 | Out-Host
Write-Log "winget $(winget --version) source update -> exit $LASTEXITCODE"

$failed = @()
foreach ($id in $apps.Keys) {
    Say "Installing $($apps[$id])"
    if (Test-Installed $id) { Write-Host 'Already installed.'; continue }
    $ok = $false
    foreach ($try in 1, 2) {
        # --source winget: skip the Microsoft Store source, which often errors on a new PC
        winget install --id $id -e --source winget --accept-source-agreements --accept-package-agreements 2>&1 |
               Tee-Object -Variable shown | Out-Host
        $code = $LASTEXITCODE
        $why = ($shown | ForEach-Object { "$_".Trim() } | Where-Object { $_ -and $_ -notmatch '^[-\\|/ ]+$|[\u2580-\u259F]' } | Select-Object -Last 3) -join ' / '
        Write-Log ("{0} try {1} -> exit 0x{2:X8} {3}" -f $id, $try, $code, $why)
        if ($code -eq 0 -or (Test-Installed $id)) { $ok = $true; break }
        if ($try -eq 1) { Warn ('That failed (code 0x{0:X8}). Refreshing winget and trying once more...' -f $code)
                          winget source update --name winget 2>&1 | Out-Null; Start-Sleep 3 }
    }
    if (-not $ok) {
        Warn ("$id didn't install (code 0x{0:X8}): $why" -f $code)
        $failed += $id
    }
}
if ($failed) {
    Warn "Didn't install: $($failed -join ', '). Details: $log"
    Warn 'Check the internet works (open a web page), then run this again - it skips what is installed.'
}

# --- mono audio -------------------------------------------------------------
Say 'Windows Mono audio: ON'
$k = 'HKCU:\Software\Microsoft\Multimedia\Audio'
if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }
Set-ItemProperty -Path $k -Name AccessibilityMonoMixState -Value 1 -Type DWord
Write-Host 'Takes effect after you sign out and back in (or Settings > Accessibility > Audio > Mono audio).'

# --- WSL2 limits ------------------------------------------------------------
Say 'WSL2 limits for Docker (8 threads, 20 GB RAM)'
$wslcfg = Join-Path $env:USERPROFILE '.wslconfig'
if (Test-Path $wslcfg) {
    Warn "$wslcfg already exists - left alone. Suggested contents are in wslconfig.example."
} else {
    Copy-Item (Join-Path $PSScriptRoot 'wslconfig.example') $wslcfg
    Write-Host "Wrote $wslcfg"
}

# --- game server files ------------------------------------------------------
Say 'Copying Minecraft server files to C:\deck\game-servers'
$dst = 'C:\deck\game-servers'
New-Item -ItemType Directory -Force $dst | Out-Null
foreach ($f in 'compose.yml', 'deck-compose.ps1', 'start-minecraft.cmd', 'stop-minecraft.cmd', 'minecraft-console.cmd', 'backup-minecraft.ps1', 'backup-minecraft.cmd') {
    Copy-Item (Join-Path $PSScriptRoot "game-servers\$f") $dst -Force
}
if (-not (Test-Path "$dst\.env")) {
    Copy-Item (Join-Path $PSScriptRoot 'game-servers\env.example') "$dst\.env"
    Write-Host "Created $dst\.env  (open it and read the EULA line before starting the server)"
} else { Write-Host "$dst\.env already exists - kept your settings." }

Write-Host @"

============================================================
 Apps done.  Next:
  1. Open Docker Desktop once, accept its terms, and in Settings > General
     tick "Start Docker Desktop when you sign in".
  2. Edit C:\deck\game-servers\.env  (EULA, version, memory), then run
     C:\deck\game-servers\start-minecraft.cmd
  3. Open Tailscale and sign in.
  4. PowerToys > Mouse Without Borders: see README.
  5. Optional: CI runner -> ci-runner\install-runner.cmd
  6. After the Pi is set up: setup-k12-agent.cmd (lets the Pi start
     Minecraft, read temperatures, and sets up automatic sign-in).
============================================================
"@
Read-Host 'Press Enter to close'
