# ---------------------------------------------------------------------------
# Deck - the cyberdeck's app on the PC's own screen.
#
# Opens the Deck Panel (served by the Pi) in its own window, laid out for a big
# screen: Minecraft, temperatures, players, the live server console, Wi-Fi,
# pack up and backups all at once. Same page as on your phone, so both always
# match.
#
# Started by the "Deck" shortcut (Start menu / desktop) and, if you chose it,
# at sign-in (-AtSignIn: waits quietly for the Pi to finish booting).
# Installed to C:\deck\app by setup-k12-agent.cmd.
# ---------------------------------------------------------------------------
param([switch]$AtSignIn, [string]$Pi = '10.48.0.2', [int]$Port = 8080)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms

$Url = "http://${Pi}:$Port/?k12=1"
# its own Edge profile: remembers the window size, separate from your normal browsing
$EdgeData = Join-Path $env:LOCALAPPDATA 'DeckApp'

function Pi-Up {
    $c = New-Object Net.Sockets.TcpClient
    try { return ($c.ConnectAsync($Pi, $Port).Wait(1500) -and $c.Connected) }
    catch { return $false }
    finally { $c.Close() }
}

# At sign-in the Pi may still be starting (it boots slower than the PC): wait up to 3 min.
$deadline = (Get-Date).AddSeconds($(if ($AtSignIn) { 180 } else { 6 }))
while (-not (Pi-Up)) {
    if ((Get-Date) -lt $deadline) { Start-Sleep -Seconds 2; continue }
    $msg = "Can't reach the deck's Pi ($Pi).`n`n" +
           "- Is the Pi switched on? It takes about a minute to start.`n" +
           "- Is its network cable plugged into the case switch?`n`n" +
           "Press Retry when it's ready."
    $a = [Windows.Forms.MessageBox]::Show($msg, 'Deck', 'RetryCancel', 'Warning')
    if ($a -ne 'Retry') { exit 1 }
    $deadline = (Get-Date).AddSeconds(6)
}

$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) {
    # --app: a plain window with no tabs or address bar, like an installed app
    Start-Process $edge -ArgumentList @("--app=$Url", "--user-data-dir=`"$EdgeData`"", '--start-maximized',
                                        '--no-first-run', '--no-default-browser-check', '--disable-features=Translate')
} else {
    Start-Process $Url    # no Edge: the default browser works too, just with tabs
}
