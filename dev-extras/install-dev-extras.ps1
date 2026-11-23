# ---------------------------------------------------------------------------
# Optional: game-dev extras for the deck PC, so Claude can work with your tools.
# Run from Install.cmd (option 4) or on its own. Normal user, not admin.
# Every part asks first and is safe to run again.
#
#   1. Tools Claude uses: Git, Python, Node.js, uv (winget)
#   2. MCP servers in %USERPROFILE%\mcp-servers:
#        gemini  - second opinions + images from Google Gemini (free key)
#        assets  - free CC0 / CC-BY models, textures, sounds, Godot plugins, with credits
#        godot   - run Godot projects and read their errors (Coding-Solo/godot-mcp)
#        blender - drive Blender (BlenderMCP add-on in Blender)
#        UnityMCP - Coplay's MCP for Unity (add it to each project with add-unity-mcp.cmd)
#   3. Registers them with Claude Code (and the Claude app, when it's installed)
#   4. API keys (hidden input, saved as Windows user variables - never in files)
#   5. PC speed-ups for game dev (Defender skips build folders; asks for admin)
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Continue'
$Here = $PSScriptRoot
$Dest = Join-Path $env:USERPROFILE 'mcp-servers'
$GodotMcpCommit = '1209744'      # Coding-Solo/godot-mcp, includes the April 2026 security fix

function Say($m)  { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Good($m) { Write-Host "OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Ask([string]$q, [bool]$def = $true) {
    $hint = if ($def) { '[Y/n]' } else { '[y/N]' }
    while ($true) {
        $a = (Read-Host "$q $hint").Trim()
        if (-not $a) { return $def }
        if ($a -match '^(y|yes)$') { return $true }
        if ($a -match '^(n|no)$') { return $false }
    }
}
function Update-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Find-Exe([string]$name) { Update-Path; $c = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1; if ($c) { $c.Source } else { $null } }

Write-Host @"

  Game-dev extras for Claude
  --------------------------
  Sets up the MCP servers that let Claude use Gemini, free asset sites, Godot, Blender
  and Unity on this PC. Nothing here is needed for the deck itself.
"@

# --- 1. tools ---------------------------------------------------------------------
Say '1. Tools Claude uses'
$tools = [ordered]@{ git = 'Git.Git'; python = 'Python.Python.3.12'; node = 'OpenJS.NodeJS.LTS'; uv = 'astral-sh.uv' }
$missing = @($tools.Keys | Where-Object { -not (Find-Exe $_) })
if (-not $missing) { Good 'Git, Python, Node.js and uv are installed.' }
elseif (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Warn "winget is missing - install these yourself: $($missing -join ', ')" }
elseif (Ask "Install $($missing -join ', ') with winget?") {
    foreach ($t in $missing) {
        Write-Host "  installing $t..."
        winget install --id $tools[$t] -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    }
    $still = @($tools.Keys | Where-Object { -not (Find-Exe $_) })
    if ($still) { Warn "Still not found: $($still -join ', '). Open a new window after installing, then run this again." } else { Good 'Tools installed.' }
}
$uv = Find-Exe 'uv'; $uvx = Find-Exe 'uvx'; $node = Find-Exe 'node'; $npm = Find-Exe 'npm'; $git = Find-Exe 'git'; $py = Find-Exe 'python'

# --- 2. servers ---------------------------------------------------------------------
Say '2. MCP servers'
$servers = [ordered]@{}
if (Ask "Copy the Gemini, asset-library and Unity setup files to $Dest?") {
    foreach ($d in 'gemini', 'assets', 'unity') {
        New-Item -ItemType Directory -Force (Join-Path $Dest $d) | Out-Null
        Copy-Item (Join-Path $Here "mcp-servers\$d\*") (Join-Path $Dest $d) -Force
    }
    Copy-Item (Join-Path $Here 'README.md') (Join-Path $Dest 'README.md') -Force
    Good "Copied to $Dest"
}
if ($uv) {
    foreach ($d in 'gemini', 'assets') {
        $s = Join-Path $Dest "$d\server.py"
        if (Test-Path $s) { $servers[$d] = @{ command = $uv; args = @('run', '--quiet', $s) } }
    }
} else { Warn 'uv is missing, so the Gemini and asset servers are not registered yet.' }

if ($uvx -and (Ask 'Register the Blender MCP? (you also install the BlenderMCP add-on in Blender - see README)')) {
    $servers['blender'] = @{ command = $uvx; args = @('blender-mcp'); env = @{ DISABLE_TELEMETRY = 'true' } }
}

if (Ask 'Set up the Godot MCP (downloads and builds it - needs Git and Node.js)?') {
    $gd = Join-Path $Dest 'godot-mcp'
    if (-not ($git -and $npm -and $node)) { Warn 'Git, Node.js or npm is missing - skipped.' }
    else {
        if (-not (Test-Path (Join-Path $gd '.git'))) { & $git clone --quiet https://github.com/Coding-Solo/godot-mcp.git $gd }
        Push-Location $gd
        & $git fetch --quiet origin; & $git checkout --quiet $GodotMcpCommit
        & $npm ci --silent 2>&1 | Out-Null; & $npm run build --silent 2>&1 | Out-Null
        Pop-Location
        $index = Join-Path $gd 'build\index.js'
        if (Test-Path $index) {
            # the console build of Godot prints errors where the MCP can read them
            $godot = @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Godot", "$env:ProgramFiles\Godot", "$env:USERPROFILE\Godot", "$env:USERPROFILE\Desktop" `
                         -Filter 'Godot*_console.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1)
            $env_ = @{}
            if ($godot) { $env_.GODOT_PATH = $godot[0].FullName; Good "Godot found: $($godot[0].Name)" }
            else { Warn 'Godot (console exe) not found - the Godot MCP will look for it itself. Put Godot in %LOCALAPPDATA%\Programs\Godot and run this again to pin it.' }
            $servers['godot'] = @{ command = $node; args = @($index); env = $env_ }
            Good 'Godot MCP built.'
        } else { Warn 'The Godot MCP build failed - run this again, or see README.' }
    }
}

if (Ask "Register Unity's MCP (Coplay's MCP for Unity; Unity starts its server when you press Start)?") {
    $servers['UnityMCP'] = @{ type = 'http'; url = 'http://localhost:8080/mcp' }
    Write-Host '    Add it to each Unity project with: %USERPROFILE%\mcp-servers\unity\add-unity-mcp.cmd'
}

# --- 3. register ----------------------------------------------------------------------
if ($servers.Count) {
    Say '3. Registering with Claude'
    if (-not $py) { Warn 'Python is missing, so Claude was not told about the servers. Install Python and run this again.' }
    else {
        $tmp = Join-Path $env:TEMP 'deck-mcp-servers.json'
        $servers | ConvertTo-Json -Depth 5 | Set-Content $tmp -Encoding UTF8
        & $py -I (Join-Path $Here 'register_mcp.py') $tmp
        Remove-Item $tmp -ErrorAction SilentlyContinue
        Good 'Done. Restart Claude so it loads them.'
    }
}

# --- 4. keys --------------------------------------------------------------------------
Say '4. API keys (optional, free)'
$gk = Join-Path $Dest 'gemini\set-gemini-key.ps1'
if ((Test-Path $gk) -and -not [Environment]::GetEnvironmentVariable('GEMINI_API_KEY', 'User') -and (Ask 'Add a Gemini key now? (from aistudio.google.com)' $false)) { & $gk }
$ak = Join-Path $Dest 'assets\set-asset-keys.ps1'
if ((Test-Path $ak) -and (Ask 'Add Freesound / Poly Pizza / Sketchfab keys now?' $false)) { & $ak }

# --- 5. speed-ups ---------------------------------------------------------------------
Say '5. PC speed-ups for game dev'
New-Item -ItemType Directory -Force 'C:\deck\tools' | Out-Null
Copy-Item (Join-Path $Here 'tools\speed-up-pc.ps1') 'C:\deck\tools\' -Force
Write-Host '    Defender then skips folders tools rebuild themselves (Unity Library/Temp, Godot .godot,'
Write-Host '    the Minecraft data, Docker''s disk). Your own files are still scanned.'
if (Ask 'Run it now? (asks for admin)' $false) { & 'C:\deck\tools\speed-up-pc.ps1' }
Write-Host '    Run it again after making new projects: Deck app > Dev tools > PC speed-ups.'

Write-Host "`nAll done. The Deck app's Dev tools page lists all of this." -ForegroundColor Green
