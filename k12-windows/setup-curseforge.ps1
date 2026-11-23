# ---------------------------------------------------------------------------
# Apache 4800 cyberdeck - CurseForge API key for modpacks  (normal user)
#
# Run with: setup-curseforge.cmd   Safe to run again (to change or remove the key).
#
#   * Asks for your CurseForge API key (free: https://console.curseforge.com/)
#   * Checks it with CurseForge
#   * Saves it to C:\deck\secrets\cf_api_key.secret, readable only by you,
#     admins and Windows itself. Never on the USB drive.
# The Pi needs the key too (for previews): on the Pi run  sudo deck-curseforge-setup
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
function Say($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "!!  $msg" -ForegroundColor Yellow }
$SecretDir  = 'C:\deck\secrets'
$SecretFile = Join-Path $SecretDir 'cf_api_key.secret'

Write-Host @"

 CurseForge API key
 ------------------
 1. Open https://console.curseforge.com/ and sign in (free account).
 2. Go to "API keys" and copy your key. It starts with  `$2a`$10`$
 3. Paste it below (right-click pastes). It stays hidden while you type.
    Type  remove  to delete the saved key instead.

"@
$sec = Read-Host 'CurseForge API key' -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)).Trim()

if ($key -eq 'remove') {
    if (Test-Path $SecretFile) { Set-Content -Path $SecretFile -Value '' -NoNewline; Write-Host 'Key removed. Modpacks will not start until you add one again.' }
    else { Write-Host 'No key was saved.' }
    Read-Host 'Press Enter to close'; exit 0
}
if ($key.Length -lt 20 -or $key.Length -gt 200 -or $key -match '\s|["''`]') {
    Warn "That doesn't look like a CurseForge API key (it's usually 60 characters starting with `$2a`$10`$)."
    Read-Host 'Press Enter to close'; exit 1
}
if ($key.StartsWith('$$')) {
    Warn 'Paste the key exactly as CurseForge shows it - no doubled $ signs needed here.'
    Read-Host 'Press Enter to close'; exit 1
}

Say 'Checking the key with CurseForge'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 -Uri 'https://api.curseforge.com/v1/games/432' -Headers @{ 'x-api-key' = $key; 'Accept' = 'application/json' }
    if ($r.StatusCode -ne 200) { throw "HTTP $($r.StatusCode)" }
    Write-Host 'CurseForge accepted the key.' -ForegroundColor Green
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 403 -or $code -eq 401) { Warn 'CurseForge refused that key. Copy it again from console.curseforge.com.'; Read-Host 'Press Enter to close'; exit 1 }
    Warn "Couldn't check the key ($($_.Exception.Message)). No internet? Saving it anyway."
}

Say "Saving it to $SecretFile"
New-Item -ItemType Directory -Force $SecretDir | Out-Null
# Only you, administrators and SYSTEM can read the folder (Docker Desktop runs as you).
icacls $SecretDir /inheritance:r /grant "${env:USERNAME}:(OI)(CI)F" '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' | Out-Null
[IO.File]::WriteAllText($SecretFile, $key, (New-Object Text.UTF8Encoding $false))   # no newline, no BOM
$key = $null

Write-Host @"

============================================================
 Done. The Minecraft server can now install CurseForge modpacks.
 Next:
   * On the Pi:  sudo deck-curseforge-setup   (same key; for previews)
   * Deck Panel > Modpacks: paste a pack link.
============================================================
"@
Read-Host 'Press Enter to close'
