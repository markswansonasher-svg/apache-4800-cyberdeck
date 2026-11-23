# ---------------------------------------------------------------------------
# Saves the free keys for Claude's asset library (the "assets" MCP server).
#
#   Freesound   (sound effects)   https://freesound.org/apiv2/apply      -> "Client secret/Api key"
#   Poly Pizza  (low-poly models) https://poly.pizza/settings/api        -> API key
#   Sketchfab   (model downloads) https://sketchfab.com/settings/password -> "API Token" at the bottom
#
# Pick one, paste its key (hidden while you type). Each key is checked with its site,
# then saved as a Windows user variable. It isn't written to any file, shown, or kept in
# PowerShell history. Claude picks new keys up straight away - no restart needed.
# Paste  remove  instead of a key to delete it.
# ---------------------------------------------------------------------------
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$sites = [ordered]@{
    '1' = @{ Name = 'Freesound';  Var = 'FREESOUND_API_KEY';  Page = 'https://freesound.org/apiv2/apply'
             Test = { param($k) Invoke-RestMethod -TimeoutSec 20 -Headers @{ Authorization = "Token $k" } -Uri 'https://freesound.org/apiv2/search/text/?query=door&page_size=1' } }
    '2' = @{ Name = 'Poly Pizza'; Var = 'POLYPIZZA_API_KEY';  Page = 'https://poly.pizza/settings/api'
             Test = { param($k) Invoke-RestMethod -TimeoutSec 20 -Headers @{ 'x-auth-token' = $k } -Uri 'https://api.poly.pizza/v1.1/search/chair?Limit=1' } }
    '3' = @{ Name = 'Sketchfab';  Var = 'SKETCHFAB_API_TOKEN'; Page = 'https://sketchfab.com/settings/password'
             Test = { param($k) Invoke-RestMethod -TimeoutSec 20 -Headers @{ Authorization = "Token $k" } -Uri 'https://api.sketchfab.com/v3/me' } }
}
while ($true) {
    Write-Host "`nAsset library keys:"
    foreach ($n in $sites.Keys) {
        $s = $sites[$n]
        $state = if ([Environment]::GetEnvironmentVariable($s.Var, 'User')) { 'set' } else { 'not set' }
        Write-Host ("  {0}. {1,-11} {2}" -f $n, $s.Name, $state)
    }
    $pick = Read-Host "`nWhich one? (1-3, or Enter to finish)"
    if (-not $pick) { break }
    $s = $sites[$pick.Trim()]
    if (-not $s) { continue }
    if ((Read-Host "Open the $($s.Name) key page in your browser? (y/n)") -match '^y') { Start-Process $s.Page }
    $sec = Read-Host "Paste your $($s.Name) key (hidden)" -AsSecureString
    $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)).Trim()
    $sec = $null
    if ($key -eq 'remove') { [Environment]::SetEnvironmentVariable($s.Var, $null, 'User'); Write-Host 'Removed.' -ForegroundColor Yellow; continue }
    if ($key -notmatch '^[\x21-\x7E]{10,300}$') { Write-Host "That doesn't look like a key (spaces or odd characters, or too short) - copy it again." -ForegroundColor Yellow; continue }
    Write-Host "Checking it with $($s.Name)..."
    try {
        $null = & $s.Test $key
        Write-Host "$($s.Name) accepted the key." -ForegroundColor Green
    } catch {
        $code = $_.Exception.Response.StatusCode.value__
        if ($code -in 400, 401, 403) { Write-Host "$($s.Name) refused that key (HTTP $code). Copy it again." -ForegroundColor Red; $key = $null; continue }
        Write-Host "Couldn't check it ($($_.Exception.Message)). Saving it anyway." -ForegroundColor Yellow
    }
    [Environment]::SetEnvironmentVariable($s.Var, $key, 'User')
    $key = $null
    Write-Host 'Saved.' -ForegroundColor Green
}
