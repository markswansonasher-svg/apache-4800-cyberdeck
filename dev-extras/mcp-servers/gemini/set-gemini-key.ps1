# ---------------------------------------------------------------------------
# Saves your Gemini API key for Claude's Gemini tools.
#
#   1. Make a key at https://aistudio.google.com  ("Get API key")
#   2. Run this, paste the key (it stays hidden while you type)
#   3. Restart Claude so it sees the key
#
# The key is checked with Google, then saved as the Windows user variable
# GEMINI_API_KEY. It isn't written to any file, shown, or kept in PowerShell history.
# Run again to replace it; type  remove  to delete it.
# ---------------------------------------------------------------------------
$sec = Read-Host 'Paste your Gemini API key (hidden)' -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)).Trim()
if ($key -eq 'remove') {
    [Environment]::SetEnvironmentVariable('GEMINI_API_KEY', $null, 'User')
    Write-Host 'Removed. Restart Claude.' -ForegroundColor Yellow; return
}
# only a basic sanity check (no spaces, a sensible length): Google's own check below decides.
# Keys come in more than one format (e.g. AIza... and newer, longer ones with dots).
if ($key -notmatch '^[\x21-\x7E]{20,300}$') {
    Write-Host "That doesn't look like an API key (it has spaces or odd characters, or it's too short) - copy it again from aistudio.google.com." -ForegroundColor Yellow; return
}
Write-Host 'Checking it with Google...'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # the key goes in a header, not in the web address
    $r = Invoke-RestMethod -TimeoutSec 20 -Headers @{ 'x-goog-api-key' = $key } -Uri 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=200'
    $n = @($r.models | Where-Object { $_.name -like 'models/gemini-*' }).Count
    Write-Host "Google accepted the key ($n Gemini models available)." -ForegroundColor Green
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -in 400, 401, 403) { Write-Host "Google refused that key (HTTP $code). Copy it again from aistudio.google.com." -ForegroundColor Red; return }
    Write-Host "Couldn't check it ($($_.Exception.Message)). Saving it anyway." -ForegroundColor Yellow
}
[Environment]::SetEnvironmentVariable('GEMINI_API_KEY', $key, 'User')
$key = $null; $sec = $null
Write-Host 'Saved. Now fully quit and reopen Claude so the Gemini tools see the key.' -ForegroundColor Green
