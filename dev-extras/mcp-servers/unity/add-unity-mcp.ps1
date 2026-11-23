# ---------------------------------------------------------------------------
# Adds Coplay's "MCP for Unity" package to Unity projects, so Claude can work in them.
#
#   add-unity-mcp.cmd                      finds your Unity projects; pick one or all
#   add-unity-mcp.cmd "D:\Games\MyGame"    just that project (or drag its folder onto the .cmd)
#
# What it changes: one line in the project's Packages\manifest.json (a copy of the old one
# is kept as manifest.json.before-unity-mcp). Unity downloads the package the next time
# the project opens. Claude's side is already set up once for the whole PC ("UnityMCP").
# Safe to run again: projects that already have it are skipped.
# ---------------------------------------------------------------------------
# every folder given counts (several dragged onto the .cmd arrive as separate arguments)
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Projects)
$Projects = @($Projects | ForEach-Object { $_ -split '\s*,\s*' } | Where-Object { $_ })
$Package = 'com.coplaydev.unity-mcp'
$Source  = 'https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main'

function Find-Projects {
    $found = @()
    # projects Unity Hub knows about
    foreach ($f in "$env:APPDATA\UnityHub\projects-v1.json", "$env:APPDATA\UnityHub\projects.json") {
        try { $j = Get-Content $f -Raw -ErrorAction Stop | ConvertFrom-Json; $found += @($j.data.PSObject.Properties.Value | ForEach-Object { $_.path }) } catch { }
    }
    # and any project folders under your user folder (Desktop, Documents, ...)
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
    $m = [regex]::Match($v, '^(\d+)\.(\d+)')
    if ($m.Success -and ([int]$m.Groups[1].Value -lt 2021 -or ([int]$m.Groups[1].Value -eq 2021 -and [int]$m.Groups[2].Value -lt 3))) {
        Write-Host "  Skipped $dir - it uses Unity $v; MCP for Unity needs 2021.3 or newer." -ForegroundColor Yellow; return
    }
    $text = Get-Content $man -Raw
    try { $json = $text | ConvertFrom-Json } catch { Write-Host "  Couldn't read $man (not valid JSON) - left alone." -ForegroundColor Red; return }
    if ($json.dependencies.PSObject.Properties.Name -contains $Package) { Write-Host "  Already has it: $dir (Unity $v)" -ForegroundColor Green; return }
    Copy-Item $man "$man.before-unity-mcp" -Force
    # one new line after the opening of "dependencies", so the rest of the file stays exactly as it was
    $line = "    `"$Package`": `"$Source`","
    $new = [regex]::Replace($text, '("dependencies"\s*:\s*\{)(\s*)', { param($x) $x.Groups[1].Value + "`n" + $line + $x.Groups[2].Value }, 1)
    try { $null = $new | ConvertFrom-Json } catch { Write-Host "  Couldn't add it safely to $man - left alone." -ForegroundColor Red; return }
    [IO.File]::WriteAllText($man, $new, (New-Object Text.UTF8Encoding $false))
    Write-Host "  Added to $dir (Unity $v)" -ForegroundColor Green
}

if (-not $Projects) {
    # @( ): with exactly one project PowerShell would hand back plain text instead of a list
    $list = @(Find-Projects)
    if (-not $list) { Write-Host 'No Unity projects found. Make one in Unity Hub, or run this with the project folder.'; Read-Host 'Press Enter to close'; return }
    Write-Host "`nUnity projects found:"
    for ($i = 0; $i -lt $list.Count; $i++) { Write-Host ("  {0}. {1}" -f ($i + 1), $list[$i]) }
    $a = Read-Host "`nAdd MCP for Unity to which? (number, several like 1,3, or a for all)"
    $Projects = if ($a -match '^\s*a') { $list } else { @($a -split '[,\s]+' | Where-Object { $_ -match '^\d+$' -and [int]$_ -ge 1 -and [int]$_ -le $list.Count } | ForEach-Object { $list[[int]$_ - 1] }) }
}
Write-Host ''
foreach ($p in $Projects) { Add-ToProject $p }
Write-Host @"

Next, in each project: open it in Unity. It downloads the package (needs Git, already installed),
then opens the MCP for Unity setup. Window > MCP for Unity: leave it on HTTP (the default) and press Start -
Unity runs the server itself on http://localhost:8080 and Claude Code is already pointed there. Then ask Claude to work in the project.
"@
if (-not $env:DECK_NO_PAUSE) { Read-Host 'Press Enter to close' }
