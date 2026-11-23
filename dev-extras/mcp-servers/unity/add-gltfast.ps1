# ---------------------------------------------------------------------------
# Adds Unity's glTFast package to Unity projects, so .glb / .gltf models (Poly Haven, Sketchfab,
# Poly Pizza, Blockbench exports...) import like FBX files do.
#
#   add-gltfast.cmd                      finds your Unity projects; pick one or all
#   add-gltfast.cmd "D:\Games\MyGame"    just that project (or drag its folder onto the .cmd)
#
# What it changes: one line in the project's Packages\manifest.json (a copy of the old one is
# kept as manifest.json.before-gltfast). Unity downloads it from its own registry the next time
# the project opens. Needs Unity 6 or newer. Safe to run again: projects that have it are skipped.
# ---------------------------------------------------------------------------
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Projects)
$Projects = @($Projects | ForEach-Object { $_ -split '\s*,\s*' } | Where-Object { $_ })
$Package = 'com.unity.cloud.gltfast'
$Version = '6.21.0'

function Find-Projects {
    $found = @()
    foreach ($f in "$env:APPDATA\UnityHub\projects-v1.json", "$env:APPDATA\UnityHub\projects.json") {
        try { $j = Get-Content $f -Raw -ErrorAction Stop | ConvertFrom-Json; $found += @($j.data.PSObject.Properties.Value | ForEach-Object { $_.path }) } catch { }
    }
    $found += @(Get-ChildItem $HOME -Filter ProjectVersion.txt -Recurse -Depth 6 -ErrorAction SilentlyContinue |
                Where-Object { $_.Directory.Name -eq 'ProjectSettings' } | ForEach-Object { $_.Directory.Parent.FullName })
    return @($found | Where-Object { $_ -and (Test-Path (Join-Path $_ 'ProjectSettings')) } | Sort-Object -Unique)
}

function Add-ToProject([string]$dir) {
    $dir = $dir.Trim('"').TrimEnd('\')
    $ver = Join-Path $dir 'ProjectSettings\ProjectVersion.txt'
    $man = Join-Path $dir 'Packages\manifest.json'
    if (-not (Test-Path $ver) -or -not (Test-Path $man)) { Write-Host "  Not a Unity project (no ProjectSettings / Packages\manifest.json): $dir" -ForegroundColor Yellow; return }
    $v = ((Get-Content $ver -First 1) -replace '^m_EditorVersion:\s*', '').Trim()
    $m = [regex]::Match($v, '^(\d+)')
    if ($m.Success -and [int]$m.Groups[1].Value -lt 6000) {
        Write-Host "  Skipped $dir - it uses Unity $v; this glTFast needs Unity 6. Add an older glTFast from Package Manager instead." -ForegroundColor Yellow; return
    }
    $text = Get-Content $man -Raw
    try { $json = $text | ConvertFrom-Json } catch { Write-Host "  Couldn't read $man (not valid JSON) - left alone." -ForegroundColor Red; return }
    if ($json.dependencies.PSObject.Properties.Name -contains $Package) { Write-Host "  Already has it: $dir (Unity $v)" -ForegroundColor Green; return }
    Copy-Item $man "$man.before-gltfast" -Force
    $line = "    `"$Package`": `"$Version`","
    $new = [regex]::Replace($text, '("dependencies"\s*:\s*\{)(\s*)', { param($x) $x.Groups[1].Value + "`n" + $line + $x.Groups[2].Value }, 1)
    try { $null = $new | ConvertFrom-Json } catch { Write-Host "  Couldn't add it safely to $man - left alone." -ForegroundColor Red; return }
    [IO.File]::WriteAllText($man, $new, (New-Object Text.UTF8Encoding $false))
    Write-Host "  Added to $dir (Unity $v)" -ForegroundColor Green
}

if (-not $Projects) {
    $list = @(Find-Projects)
    if (-not $list) { Write-Host 'No Unity projects found. Make one in Unity Hub, or run this with the project folder.'; if (-not $env:DECK_NO_PAUSE) { Read-Host 'Press Enter to close' }; return }
    Write-Host "`nUnity projects found:"
    for ($i = 0; $i -lt $list.Count; $i++) { Write-Host ("  {0}. {1}" -f ($i + 1), $list[$i]) }
    $a = Read-Host "`nAdd glTFast to which? (number, several like 1,3, or a for all)"
    $Projects = if ($a -match '^\s*a') { $list } else { @($a -split '[,\s]+' | Where-Object { $_ -match '^\d+$' -and [int]$_ -ge 1 -and [int]$_ -le $list.Count } | ForEach-Object { $list[[int]$_ - 1] }) }
}
Write-Host ''
foreach ($p in $Projects) { Add-ToProject $p }
Write-Host @"

Next: open the project in Unity; it downloads glTFast by itself. After that, .glb and .gltf files
dropped into Assets (or copied in by Claude's add_to_project) import as models.
"@
if (-not $env:DECK_NO_PAUSE) { Read-Host 'Press Enter to close' }
