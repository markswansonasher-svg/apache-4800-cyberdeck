# ---------------------------------------------------------------------------
# Apache 4800 cyberdeck - the walkthrough installer (started by Install.cmd)
#
# A menu that sets up each part of the deck in the right order and shows what's done:
#   1. The mini PC      -> k12-windows\setup-pc.ps1 (apps, Docker, Minecraft, network)
#   2. The Raspberry Pi -> copies pi5\ to the Pi over SSH and runs setup-pi.sh there
#   3. Link PC <-> Pi   -> lets the Pi start/stop Minecraft and read temperatures
#   4. The laptop       -> laptop-windows\setup-laptop.ps1
#   5. Game-dev extras  -> dev-extras\install-dev-extras.ps1 (optional)
#   6. Try it first     -> the demo with pretend hardware (no deck needed)
#   7. Run the tests
#
# Normal user, not admin: steps ask for admin themselves when they need it.
# Every step is safe to run again.
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Continue'
$Repo = Split-Path $PSScriptRoot -Parent
$Kit = 'C:\deck\setup-kit'
$PiIP = '10.48.0.2'
$StateFile = Join-Path $env:LOCALAPPDATA 'apache-4800-cyberdeck\installer.json'
try { $Host.UI.RawUI.WindowTitle = 'Apache 4800 cyberdeck - installer' } catch { }

function Say($m)  { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Good($m) { Write-Host "OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Pause-Key { Read-Host "`nPress Enter to go back to the menu" | Out-Null }
function Ask([string]$q, [bool]$def = $true) {
    $hint = if ($def) { '[Y/n]' } else { '[y/N]' }
    while ($true) {
        $a = (Read-Host "$q $hint").Trim()
        if (-not $a) { return $def }
        if ($a -match '^(y|yes)$') { return $true }
        if ($a -match '^(n|no)$') { return $false }
    }
}
function Ask-Text([string]$q, [string]$def) {
    $a = (Read-Host "$q [$def]").Trim()
    if ($a) { $a } else { $def }
}
function Get-State {
    $s = @{ pi_host = 'deckpi.local'; pi_user = '' }
    if (Test-Path $StateFile) { try { (Get-Content $StateFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $s[$_.Name] = $_.Value } } catch { } }
    $s
}
function Save-State($s) {
    New-Item -ItemType Directory -Force (Split-Path $StateFile) | Out-Null
    [pscustomobject]$s | ConvertTo-Json | Set-Content $StateFile -Encoding UTF8
}
function Test-Port([string]$h, [int]$port, [int]$ms = 1500) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $ok = $c.BeginConnect($h, $port, $null, $null).AsyncWaitHandle.WaitOne($ms)
        $r = $ok -and $c.Connected; $c.Close(); return $r
    } catch { return $false }
}
function Run-Ps([string]$file, [string[]]$a = @()) {
    # runs a step's script in this window (so its questions show here)
    & powershell -NoProfile -ExecutionPolicy Bypass -File $file @a
}

# --- what's done -----------------------------------------------------------------------
function Get-Status {
    $st = [ordered]@{}
    $prog = 'C:\deck\setup-progress.json'
    $done = @(); if (Test-Path $prog) { try { $done = @((Get-Content $prog -Raw | ConvertFrom-Json).done) } catch { } }
    $st.pc = if ($done -contains 'agent') { 'done' } elseif ($done.Count) { "$($done.Count) of 8 steps done" } else { 'not started' }
    $st.pi = if (Test-Port $PiIP 8080 800) { 'done (the Deck Panel answers)' } elseif (Test-Path (Join-Path $Kit 'pi5\deck-pi.pub')) { 'set up (not reachable right now)' } else { 'not started' }
    $st.link = if ($done -contains 'agent') { 'done' } else { 'after 1 and 2' }
    $st.laptop = if ((Get-NetIPAddress -IPAddress '10.48.0.20' -ErrorAction SilentlyContinue)) { 'done (this is the laptop)' } else { 'run it on the laptop' }
    $st.extras = if (Test-Path (Join-Path $env:USERPROFILE 'mcp-servers\assets\server.py')) { 'installed' } else { 'optional' }
    $st
}

# --- 1. the mini PC -----------------------------------------------------------------------
function Step-PC {
    Say 'The mini PC'
    Write-Host @'
This sets up the PC that runs Minecraft. It:
  - checks the PC (Windows, virtualization, space)
  - turns on WSL and sets up the case network (asks for admin)
  - installs Docker Desktop, Tailscale, PowerToys, Git and more
  - restarts once, then carries on by itself after you sign in
  - downloads the Minecraft server and asks the Minecraft questions (EULA, who may join)
Before you start: connect the PC to Wi-Fi and plug its Ethernet into the case switch.
'@
    if (-not (Ask 'Start?')) { return }
    Run-Ps (Join-Path $Repo 'k12-windows\setup-pc.ps1')
}

# --- 2. the Pi --------------------------------------------------------------------------
function Step-Pi {
    Say 'The Raspberry Pi'
    Write-Host @'
First, flash the Pi's SD card with Raspberry Pi Imager (https://www.raspberrypi.com/software/):
  - Raspberry Pi OS Lite (64-bit)
  - Settings: hostname  deckpi , a username + password, your Wi-Fi, and "Enable SSH"
Put the card in the Pi, plug its Ethernet into the case switch and power it on (give it 2 minutes).

This step then copies the Pi software over and runs its setup (Tailscale, Uptime Kuma, the
Deck Panel, the watchdog, the Discord bot, travel mode). You type the Pi's password when asked.
'@
    if (-not (Get-Command ssh -ErrorAction SilentlyContinue) -or -not (Get-Command scp -ErrorAction SilentlyContinue)) {
        Warn 'Windows'' SSH client is missing: Settings > System > Optional features > add "OpenSSH Client", then try again.'; return
    }
    if (-not (Ask 'Is the Pi flashed and switched on?')) { return }
    $s = Get-State
    $s.pi_host = Ask-Text 'The Pi''s name or address' $s.pi_host
    $s.pi_user = Ask-Text 'Your username on the Pi' $(if ($s.pi_user) { $s.pi_user } else { 'pi' })
    if ($s.pi_user -notmatch '^[a-z_][a-z0-9_-]{0,31}$') { Warn 'That isn''t a Linux username.'; return }
    if ($s.pi_host -notmatch '^[A-Za-z0-9.\-:]+$') { Warn 'That isn''t a host name or address.'; return }
    Save-State $s
    $target = "$($s.pi_user)@$($s.pi_host)"
    Write-Host "Looking for the Pi at $($s.pi_host)..."
    if (-not (Test-Port $s.pi_host 22 4000)) {
        Warn "No SSH answer from $($s.pi_host). Check it's on, on the same Wi-Fi as this PC, and SSH was enabled in Imager. Then try again (or use its IP address)."
        return
    }
    Good 'Found it.'
    Say 'Copying the Pi software (type the Pi password if asked)'
    & scp -r -o StrictHostKeyChecking=accept-new (Join-Path $Repo 'pi5') "${target}:~/"
    if ($LASTEXITCODE -ne 0) { Warn 'Copying failed - check the username and password, then try again.'; return }
    Say 'Running setup-pi.sh on the Pi (a few minutes; it asks for the password again for sudo)'
    & ssh -t -o StrictHostKeyChecking=accept-new $target 'sudo bash ~/pi5/setup-pi.sh'
    if ($LASTEXITCODE -ne 0) { Warn 'setup-pi.sh stopped with an error (see above). Fix it and run this step again - it is safe to repeat.'; return }
    Say 'Fetching the Pi''s public key for the PC link'
    New-Item -ItemType Directory -Force (Join-Path $Kit 'pi5') | Out-Null
    & scp -o StrictHostKeyChecking=accept-new "${target}:~/pi5/deck-pi.pub" (Join-Path $Kit 'pi5\deck-pi.pub')
    if ($LASTEXITCODE -eq 0) { Good 'Got it. Next: 3. Link PC <-> Pi (on the mini PC).' }
    else { Warn 'Couldn''t fetch it - the PC link can also read it from the Pi over the case cable.' }
    Write-Host "`nOn your phone: install Tailscale, sign in with the same account, then open http://deckpi:8080"
}

# --- 3. link ------------------------------------------------------------------------------
function Step-Link {
    Say 'Link the PC and the Pi'
    Write-Host @'
Run this on the mini PC, after steps 1 and 2. It lets the Pi start and stop Minecraft, read
temperatures and switch travel mode - and nothing else (SSH key locked to one script,
only from the Pi's case-network address). It also installs the Deck app.
'@
    if (-not (Ask 'Start?')) { return }
    Run-Ps (Join-Path $Repo 'k12-windows\setup-pc.ps1')
    $s = Get-State
    if ($s.pi_user -and (Ask "Run the last part on the Pi for you now (sudo deck-link-k12 $env:USERNAME)?")) {
        & ssh -t "$($s.pi_user)@$($s.pi_host)" "sudo deck-link-k12 $env:USERNAME"
    } else {
        Write-Host "On the Pi, run:  sudo deck-link-k12 $env:USERNAME"
    }
}

# --- 4. laptop ----------------------------------------------------------------------------
function Step-Laptop {
    Say 'The laptop'
    Write-Host 'Run this on the laptop, with its Ethernet in the case''s side-panel jack 2. It sets 10.48.0.20,'
    Write-Host 'installs PowerToys (Mouse Without Borders) and tests the link to the PC and the Pi.'
    if (-not (Ask 'Is this the laptop, and is it plugged in?')) { return }
    Run-Ps (Join-Path $Repo 'laptop-windows\setup-laptop.ps1')
}

# --- 5. extras ----------------------------------------------------------------------------
function Step-Extras { Run-Ps (Join-Path $Repo 'dev-extras\install-dev-extras.ps1') }

# --- 6. demo ------------------------------------------------------------------------------
function Step-Demo {
    Say 'Try it first (pretend hardware)'
    Write-Host '  a. The Deck Panel (phone view) in your browser - needs Python'
    Write-Host '  b. The PC''s Deck app'
    $c = (Read-Host 'a or b').Trim().ToLower()
    if ($c -eq 'a') {
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
            if (Ask 'Python is needed. Install it with winget?') { winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements }
            $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
        }
        Start-Process (Join-Path $Repo 'try-the-panel.cmd') -WorkingDirectory $Repo
    } elseif ($c -eq 'b') { Start-Process (Join-Path $Repo 'try-the-k12-app.cmd') -WorkingDirectory $Repo }
}

# --- 7. tests -----------------------------------------------------------------------------
function Step-Tests {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Warn 'The tests need Python (winget install Python.Python.3.12).'; return }
    Push-Location $Repo; & python (Join-Path $Repo 'tests\run_tests.py'); Pop-Location
}

# --- menu ---------------------------------------------------------------------------------
Get-ChildItem $Repo -Recurse -File -Include *.ps1, *.cmd, *.py, *.cs -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
while ($true) {
    Clear-Host
    $st = Get-Status
    Write-Host @"

   APACHE 4800 // CYBERDECK  -  setup

   A Raspberry Pi 5 and a mini PC in a case: the Pi runs the Deck Panel (phone + screen),
   a watchdog and a Discord bot; the PC runs a Minecraft server in Docker. Each step below
   explains itself and asks before changing anything. Do them in order.

"@ -ForegroundColor Gray
    $rows = @(
        @('1', 'The mini PC', $st.pc),
        @('2', 'The Raspberry Pi', $st.pi),
        @('3', 'Link PC <-> Pi', $st.link),
        @('4', 'The laptop (optional)', $st.laptop),
        @('5', 'Game-dev extras for Claude (optional)', $st.extras),
        @('6', 'Try it first - demo, no hardware', ''),
        @('7', 'Run the tests', ''),
        @('8', 'Discord bot on the PC (no Pi needed)', $(if (Test-Path 'C:\deck\bot\config.json') { 'installed' } else { 'optional' })))
    foreach ($r in $rows) {
        $c = if ($r[2] -match '^done|installed') { 'Green' } elseif ($r[2]) { 'DarkGray' } else { 'Gray' }
        Write-Host ("   {0}. {1,-40}" -f $r[0], $r[1]) -NoNewline; Write-Host $r[2] -ForegroundColor $c
    }
    Write-Host "`n   R. Read the full guide (README)    Q. Quit"
    $c = (Read-Host "`nPick a step").Trim().ToUpper()
    switch ($c) {
        '1' { Step-PC; Pause-Key }
        '2' { Step-Pi; Pause-Key }
        '3' { Step-Link; Pause-Key }
        '4' { Step-Laptop; Pause-Key }
        '5' { Step-Extras; Pause-Key }
        '6' { Step-Demo; Pause-Key }
        '7' { Step-Tests; Pause-Key }
        '8' { Run-Ps (Join-Path $Repo 'k12-windows\bot\install-bot.ps1'); Pause-Key }
        'R' { Start-Process (Join-Path $Repo 'README.md') }
        'Q' { return }
    }
}
