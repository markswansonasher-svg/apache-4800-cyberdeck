# Exports every printed part to cad\stl, plus the assembly picture and the wall
# template. Run build.cmd after changing params.scad.
$ErrorActionPreference = 'Continue'   # OpenSCAD writes progress to stderr; exit codes are checked instead
$scad = @('C:\Program Files\OpenSCAD\openscad.exe', 'C:\Program Files\OpenSCAD (Nightly)\openscad.exe') |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $scad) { Write-Host 'OpenSCAD not found. Install it:  winget install OpenSCAD.OpenSCAD'; exit 1 }

$here = $PSScriptRoot
$out  = Join-Path $here 'stl'
New-Item -ItemType Directory -Force $out | Out-Null
Get-ChildItem $out -Filter *.stl | Remove-Item   # start clean so old parts don't linger

# name, source file, -D overrides
$jobs = @(
    @('01_keystone_test',      'keystone_test.scad',   @()),
    @('02_side_panels',        'side_panels.scad',     @()),
    @('03_cover_and_combs',    'cover_and_combs.scad', @()),
    @('04_lid_clips',          'lid_clips.scad',       @()),
    @('05_hub_pi_cradle',      'hub_pi_cradle.scad',   @()),
    @('06_power_cradle',       'power_cradle.scad',    @()),
    @('07_k12_plinth',         'k12_plinth.scad',      @())
)
foreach ($c in 0..2) { foreach ($r in 0..1) { $jobs += ,@("08_floor_tile_c${c}_r${r}", 'floor_tile.scad', @("col=$c", "row=$r")) } }

foreach ($j in $jobs) {
    $name, $src, $defs = $j
    $oargs = @('-o', (Join-Path $out "$name.stl"))
    foreach ($d in $defs) { $oargs += @('-D', $d) }
    $oargs += (Join-Path $here $src)
    $t = Get-Date
    Write-Host ("{0,-24}" -f $name) -NoNewline
    & $scad @oargs 2>&1 | Where-Object { $_ -match 'WARNING|ERROR' } | ForEach-Object { Write-Host "`n   $_" -ForegroundColor Yellow -NoNewline }
    if ($LASTEXITCODE -ne 0) { Write-Host "`nOpenSCAD failed on $name" -ForegroundColor Red; exit 1 }
    Write-Host (" ok  ({0:n0} s)" -f ((Get-Date) - $t).TotalSeconds)
}

Write-Host 'wall_cutout_template.svg' -NoNewline
& $scad -o (Join-Path $here 'wall_cutout_template.svg') (Join-Path $here 'wall_cutout_template.scad') 2>$null
Write-Host ' ok'
Write-Host 'assembly_top.png' -NoNewline
& $scad -o (Join-Path $here 'assembly_top.png') --preview --projection=o --imgsize=1800,1300 `
    --camera=222,156,0,0,0,0,1150 --colorscheme=Tomorrow (Join-Path $here 'assembly.scad') 2>$null
& $scad -o (Join-Path $here 'assembly_3d.png') --preview --imgsize=1800,1300 `
    --camera=222,140,20,55,0,25,1100 --colorscheme=Tomorrow (Join-Path $here 'assembly.scad') 2>$null
Write-Host ' ok'

$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) { & python (Join-Path $here 'check_sizes.py') $out }
