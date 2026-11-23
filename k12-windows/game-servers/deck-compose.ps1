# ---------------------------------------------------------------------------
# deck-compose - runs "docker compose" with the right settings.
#
#   .env                 your settings (EULA, players, ...)
#   packs\<name>.env     one modpack's settings, written by the deck
#   active-pack.txt      which pack is on (missing or empty = vanilla)
#
# It merges .env + the active pack (the pack wins) into .env.active and runs
#   docker compose --env-file .env.active <what you asked>
# Used by the deck agent and by start/stop-minecraft.cmd, so they always agree.
#   powershell -File deck-compose.ps1 up -d
# The agent runs its own copy in C:\deck\agent (only admins can change that one).
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Continue'
$ComposeArgs = @($args)
$Dir     = if (Test-Path (Join-Path $PSScriptRoot 'compose.yml')) { $PSScriptRoot } else { 'C:\deck\game-servers' }
$SlugRe  = '^[a-z0-9][a-z0-9-]{0,79}$'
$Secret  = 'C:\deck\secrets\cf_api_key.secret'

$active = ''
$af = Join-Path $Dir 'active-pack.txt'
if (Test-Path $af) {
    $s = "$(Get-Content $af -Raw -ErrorAction SilentlyContinue)".Trim()
    if ($s -cmatch $SlugRe -and (Test-Path (Join-Path $Dir "packs\$s.env"))) { $active = $s }
}

$vals = [ordered]@{}
function Read-EnvFile([string]$path) {
    if (-not (Test-Path $path)) { return }
    foreach ($l in Get-Content $path) {
        if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { $vals[$Matches[1]] = $Matches[2] }
    }
}
Read-EnvFile (Join-Path $Dir '.env')
if ($active) { Read-EnvFile (Join-Path $Dir "packs\$active.env") }
$lines = @('# Made by deck-compose.ps1 - edit .env or packs\*.env instead.')
foreach ($k in $vals.Keys) { $lines += "$k=$($vals[$k])" }
Set-Content -Path (Join-Path $Dir '.env.active') -Value $lines -Encoding ASCII

# folders and files compose mounts must exist
$data = if ($active) { "data\packs\$active" } else { 'data\minecraft' }
$dl   = if ($active) { "downloads\$active" } else { 'downloads\vanilla' }
foreach ($p in $data, $dl, 'imports') { New-Item -ItemType Directory -Force (Join-Path $Dir $p) | Out-Null }
if (-not (Test-Path $Secret)) {
    # empty placeholder so vanilla starts; setup-curseforge.cmd puts the real key here
    New-Item -ItemType Directory -Force (Split-Path $Secret) | Out-Null
    New-Item -ItemType File -Force $Secret | Out-Null
}

Push-Location $Dir
try { docker compose --env-file .env.active @ComposeArgs } finally { Pop-Location }
exit $LASTEXITCODE
