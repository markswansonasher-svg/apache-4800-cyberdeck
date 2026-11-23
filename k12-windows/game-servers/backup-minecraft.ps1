# Zips the active world to C:\deck\backups (keeps the newest 10 of each world).
# Vanilla: minecraft-<date>.zip   Modpack: pack-<name>-<date>.zip
# Saves are paused during the copy so the world isn't half-written.
$ErrorActionPreference = 'Stop'
$active = ''
$af = Join-Path $PSScriptRoot 'active-pack.txt'
if (Test-Path $af) {
    $s = "$(Get-Content $af -Raw)".Trim()
    if ($s -cmatch '^[a-z0-9][a-z0-9-]{0,79}$' -and (Test-Path (Join-Path $PSScriptRoot "packs\$s.env"))) { $active = $s }
}
$src    = if ($active) { Join-Path $PSScriptRoot "data\packs\$active" } else { Join-Path $PSScriptRoot 'data\minecraft' }
$prefix = if ($active) { "pack-$active" } else { 'minecraft' }
$dest = 'C:\deck\backups'
New-Item -ItemType Directory -Force $dest | Out-Null
if (-not (Test-Path $src)) { Write-Host "No world yet at $src"; exit 1 }

$running = [bool]((docker ps --filter 'name=^minecraft$' --format '{{.Names}}') -eq 'minecraft')
if ($running) { docker exec minecraft rcon-cli save-off | Out-Null; docker exec minecraft rcon-cli save-all flush | Out-Null; Start-Sleep 3 }
try {
    $zip = Join-Path $dest ("$prefix-{0}.zip" -f (Get-Date -Format 'yyyyMMdd-HHmm'))
    $items = Get-ChildItem $src | Where-Object { $_.Name -notin 'logs', 'cache', 'libraries', 'versions' }
    Compress-Archive -Path $items.FullName -DestinationPath $zip -CompressionLevel Optimal
    Write-Host "Backup: $zip"
} finally {
    if ($running) { docker exec minecraft rcon-cli save-on | Out-Null }
}
$re = '^' + [regex]::Escape($prefix) + '-\d{8}-\d{4,6}\.zip$'
Get-ChildItem $dest -Filter '*.zip' | Where-Object { $_.Name -match $re } | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 10 | Remove-Item
