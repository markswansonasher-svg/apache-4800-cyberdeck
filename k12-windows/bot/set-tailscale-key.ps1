# ---------------------------------------------------------------------------
# Tailscale API key for the Discord bot's  /mc invite  (makes a one-time link that
# shares ONLY this PC with a friend's own Tailscale - they never see your other devices).
#
# Get one: https://login.tailscale.com/admin/settings/keys > "Generate access token"
# Access tokens expire (you pick 1-90 days). When invites stop working, run this again.
#
# Checked with Tailscale, then saved as the Windows user variable TS_API_KEY (never in a
# file, never shown). Type  remove  instead of a key to delete it.
# ---------------------------------------------------------------------------
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
function Good($m) { Write-Host "OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }

if ((Read-Host 'Open the Tailscale keys page in your browser? [Y/n]').Trim() -notmatch '^n') {
    Start-Process 'https://login.tailscale.com/admin/settings/keys'
    Write-Host 'There: "Generate access token...", any description (e.g. deck invites), pick an expiry, Generate, Copy.'
}
$sec = Read-Host 'Paste the access token (tskey-api-..., hidden)' -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)).Trim()
$sec = $null
if ($key -eq 'remove') { [Environment]::SetEnvironmentVariable('TS_API_KEY', $null, 'User'); Warn 'Removed. /mc invite goes back to "share it by hand".'; $restart = $true }
elseif ($key -notmatch '^tskey-api-[A-Za-z0-9_-]{10,200}$') { Warn 'That isn''t an API access token (they start with tskey-api-). Note: an "auth key" (tskey-auth-) is a different thing.'; return }
else {
    Write-Host 'Checking it with Tailscale...'
    try { $devs = (Invoke-RestMethod -TimeoutSec 20 -Headers @{ Authorization = "Bearer $key" } 'https://api.tailscale.com/api/v2/tailnet/-/devices').devices }
    catch { Warn "Tailscale didn't accept it (HTTP $($_.Exception.Response.StatusCode.value__)). Copy it again, or make a new one."; return }
    $ip = (& 'C:\Program Files\Tailscale\tailscale.exe' ip -4 2>$null | Select-Object -First 1)
    $me = @($devs | Where-Object { @($_.addresses) -contains $ip })
    if ($me) { Good "Key works. This PC is in your tailnet as $($me[0].hostname) ($ip)." }
    else { Warn "Key works, but this PC ($ip) isn't in that tailnet - is Tailscale signed in here with the same account?" }
    [Environment]::SetEnvironmentVariable('TS_API_KEY', $key, 'User')
    $key = $null
    Good 'Saved (Windows user variable TS_API_KEY).'
    $restart = $true
}
if ($restart) {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match 'deckhost\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    if (Test-Path 'C:\deck\bot\venv\Scripts\pythonw.exe') {
        Start-Process 'C:\deck\bot\venv\Scripts\pythonw.exe' -ArgumentList '"C:\deck\bot\deckhost.py"' -WorkingDirectory 'C:\deck\bot'
        Good 'Bot restarted. In Discord: /mc invite @friend TheirMinecraftName'
    }
}
