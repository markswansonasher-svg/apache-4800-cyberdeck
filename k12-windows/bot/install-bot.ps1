# ---------------------------------------------------------------------------
# Installs the deck's Discord bot + watchdog on THIS PC (for a deck without a Pi, or
# until the Pi is set up). Normal user, no admin. Safe to run again (it updates).
#
#   1. copies deckhost.py + the Pi's deckbot/deckwatch/decklib to C:\deck\bot
#   2. makes a private Python environment there with discord.py
#   3. starts it at sign-in, with no window (Startup folder shortcut)
#   4. connects it to your Discord server (set-discord-bot.ps1), then starts it
#
# When the Pi is set up later, turn this off (the Pi runs the bot then):
#   install-bot.ps1 -Remove
# ---------------------------------------------------------------------------
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$Dest = 'C:\deck\bot'
$Kit = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent        # ...\k12-windows\bot -> kit root
$Startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'Deck bot.lnk'

function Say($m)  { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Good($m) { Write-Host "OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Stop-Bot { Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match 'deckhost\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force } }

if ($Remove) {
    Stop-Bot; Remove-Item $Startup -ErrorAction SilentlyContinue
    Good 'Stopped, and it no longer starts at sign-in. (C:\deck\bot and the token are kept; run without -Remove to turn it back on.)'
    return
}

# --- 1. files -------------------------------------------------------------------------
Say "Copying the bot to $Dest"
$pi5 = @((Join-Path $Kit 'pi5'), 'C:\deck\setup-kit\pi5') | Where-Object { Test-Path (Join-Path $_ 'deckbot.py') } | Select-Object -First 1
if (-not $pi5) { Warn "Can't find the Pi's files (pi5\deckbot.py) next to this kit."; exit 1 }
New-Item -ItemType Directory -Force $Dest, (Join-Path $Dest 'state') | Out-Null
Stop-Bot
foreach ($f in 'decklib.py', 'deckwatch.py', 'deckbot.py') { Copy-Item (Join-Path $pi5 $f) $Dest -Force }
# (run from C:\deck\bot itself - the Deck app's "Turn PC copy on" - these are already there)
if ((Resolve-Path $PSScriptRoot).Path.TrimEnd('\') -ne $Dest) {
    foreach ($f in 'deckhost.py', 'set-discord-bot.ps1', 'set-tailscale-key.ps1', 'install-bot.ps1') { Copy-Item (Join-Path $PSScriptRoot $f) $Dest -Force }
}
Good 'Copied.'

# --- 2. Python + discord.py -------------------------------------------------------------
Say 'Python environment with discord.py'
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
$py = @((Get-Command py -ErrorAction SilentlyContinue), (Get-Command python -ErrorAction SilentlyContinue)) | Where-Object { $_ -and $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1
if (-not $py) {
    if ((Read-Host 'Python 3 is needed. Install it with winget? [Y/n]').Trim() -match '^n') { exit 1 }
    winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    $py = Get-Command python -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1
    if (-not $py) { Warn 'Python still not found - open a new window and run this again.'; exit 1 }
}
$venv = Join-Path $Dest 'venv'
if (-not (Test-Path (Join-Path $venv 'Scripts\python.exe'))) {
    $a = if ($py.Name -like 'py*.exe' -and $py.Name -notlike 'python*') { @('-3', '-m', 'venv', $venv) } else { @('-m', 'venv', $venv) }
    & $py.Source @a
}
& (Join-Path $venv 'Scripts\python.exe') -m pip install --quiet --disable-pip-version-check --upgrade "discord.py>=2.3,<3"
if ($LASTEXITCODE -ne 0) { Warn 'Installing discord.py failed (internet?). Run this again.'; exit 1 }
Good 'discord.py installed.'

# --- 3. start at sign-in -------------------------------------------------------------------
Say 'Start at sign-in (no window)'
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($Startup)
$lnk.TargetPath = Join-Path $venv 'Scripts\pythonw.exe'
$lnk.Arguments = "`"$Dest\deckhost.py`""
$lnk.WorkingDirectory = $Dest
$lnk.Description = 'Cyberdeck Discord bot + watchdog (PC mode)'
$lnk.Save()
Good 'Added to your Startup folder.'

# --- 4. connect to Discord + start --------------------------------------------------------
if (-not [Environment]::GetEnvironmentVariable('DISCORD_TOKEN', 'User') -or -not (Test-Path (Join-Path $Dest 'config.json'))) {
    Say 'Connect it to your Discord server'
    & (Join-Path $Dest 'set-discord-bot.ps1')
} else {
    Start-Process (Join-Path $venv 'Scripts\pythonw.exe') -ArgumentList "`"$Dest\deckhost.py`"" -WorkingDirectory $Dest
    Good 'Bot started. Log: C:\deck\bot\state\deckhost.log'
}
