# ---------------------------------------------------------------------------
# deck-agent - the ONLY thing the Pi can run on the PC.
#
# The Pi logs in over SSH with its key; the key's entry in authorized_keys has
#   restrict,from="10.48.0.2",command="...deck-agent.ps1"
# so whatever the Pi asks for, Windows runs this script instead, and the
# request arrives in $env:SSH_ORIGINAL_COMMAND. Anything not listed below is
# refused. Every answer is one line of JSON.
#
#   ping                  -> agent alive?
#   info                  -> Docker / Minecraft state, Tailscale IP
#   start                 -> make sure Docker is running, then start Minecraft
#   stop                  -> save the world and stop Minecraft
#   off [now]             -> stop Minecraft, then shut Windows down in 60 s (now: 5 s)
#   cancel-off            -> cancel that shutdown
#   say <text>            -> chat message to everyone in the game
#   travel on|off         -> route internet through the Pi (travel mode)
#   whitelist list|add|remove <name> [uuid]
#                         -> who may join (server running: live; stopped: whitelist.json)
#   backup [pack]         -> zip the active world (or that pack's) into C:\deck\backups
#                            (pauses saving while copying)
#   backup-get <file>     -> send that zip to the Pi as raw bytes (the only non-JSON answer)
#   modpack list          -> installed packs + which one is on
#   modpack apply <pack> <fileId|latest|zip> <java tag> <heap GB> <limit GB>
#                         -> write packs\<pack>.env and make it the active one (server stopped)
#                            zip: install from imports\<pack>.zip, a CurseForge "Export profile" zip
#   modpack vanilla       -> back to plain Minecraft (server stopped)
#   modpack delete <pack> -> remove a pack and its world (needs a fresh backup of it first)
#   modpack mods <pack>   -> each mod jar: file name, mod IDs inside, mixin configs (to name crash culprits)
#   modpack diagnose [since] -> the last 400 log lines + the newest crash report of the active pack
#   (logs [since] and diagnose [since]: only lines after that unix time, which "start" returns)
#   modpack exclude|include <pack> <projectId>
#                         -> leave a mod out of the pack (or put it back); next start re-syncs the mods
#   modpack synced <pack> -> the re-sync happened; normal starts again
#   modpack upload <pack> <name, base64url> <bytes>
#                         -> receive a mod you downloaded by hand (raw bytes on stdin, .jar only)
#   modpack preset <pack> <bytes>|clear
#                         -> the pack's keybind preset (JSON on stdin) -> config\keypresets\server.json
#   settings get          -> the server settings the Deck app may change (.env + the world's server.properties)
#   settings set env|prop <key> <value, base64url>
#                         -> change one of them (checked against $EnvRules / $PropRules); used at the next start
#   perf                  -> ticks per second / time per tick + the server container's CPU and memory
#   logs                  -> the last 80 lines of the Minecraft server log
#   cmd <command>         -> run one server console command (not stop/whitelist: the
#                            deck has buttons for those that also back up / keep .env in step)
# ---------------------------------------------------------------------------
# 'Continue': docker prints progress on stderr, which PowerShell 5.1 would
# otherwise turn into errors. Cmdlets that must not fail use -ErrorAction Stop.
$ErrorActionPreference = 'Continue'
$GameDir   = 'C:\deck\game-servers'
$StateFile = 'C:\deck\agent\travel-state.json'
$PiIP      = '10.48.0.2'
$CaseIP    = '10.48.0.10'

$EnvFile   = Join-Path $GameDir '.env'
$PacksDir  = Join-Path $GameDir 'packs'
$ImportsDir = Join-Path $GameDir 'imports'     # CurseForge export zips the Deck app copied here
$ActiveFile = Join-Path $GameDir 'active-pack.txt'
# the agent's own copy, in its admin-only folder (setup-k12-agent copies it there)
$Compose   = Join-Path $PSScriptRoot 'deck-compose.ps1'
$SecretFile = 'C:\deck\secrets\cf_api_key.secret'
$BackupDir = 'C:\deck\backups'
$NameRe    = '^[A-Za-z0-9_]{3,16}$'
$UuidRe    = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
# modpack names = CurseForge slugs; keep in step with decklib.SLUG_RE
$SlugRe    = '^[a-z0-9][a-z0-9-]{0,79}$'
$FileIdRe  = '^\d{1,10}$'
$TagRe     = '^(java8|java17|java21|java25|latest)$'
$ProjectRe = '^\d{1,9}$'
# a mod you downloaded by hand: CurseForge file names (letters, digits, space . _ + - ( ) [ ]), .jar only
$JarRe     = '^[A-Za-z0-9][A-Za-z0-9 ._+()\[\]-]{0,150}\.jar$'
$MaxUpload = 100MB
# vanilla: minecraft-<date>.zip   pack: pack-<slug>-<date>.zip   (keep in step with decklib.BACKUP_RE)
$BackupRe  = '^(minecraft|pack-[a-z0-9][a-z0-9-]{0,79})-\d{8}-\d{4,6}\.zip$'

# Server settings the Deck app may change. Anything not listed here is refused.
#   .env (every world): no quotes, $ or # - docker compose would read them as quoting,
#   variables or a comment. A modpack's own MC_MEMORY (packs\<pack>.env) wins over this one.
$EnvRules = [ordered]@{
    MC_DIFFICULTY  = '^(peaceful|easy|normal|hard)$'
    MC_MOTD        = '^[A-Za-z0-9][A-Za-z0-9 .,!?:()/+&@*_-]{0,58}$'
    MC_MAX_PLAYERS = 'int:1:100'
    MC_MEMORY      = '^[2-7]G$'                       # vanilla's container limit is 8 GB
    MC_VERSION     = '^(LATEST|1\.\d{1,2}(\.\d{1,2})?)$'
    MC_TYPE        = '^(VANILLA|PAPER|FABRIC|NEOFORGE)$'
}
#   server.properties of the world that's on (each modpack keeps its own)
$PropRules = [ordered]@{
    'gamemode'             = '^(survival|creative|adventure|spectator)$'
    'view-distance'        = 'int:3:32'
    'simulation-distance'  = 'int:3:32'
    'pvp'                  = '^(true|false)$'
    'hardcore'             = '^(true|false)$'
    'allow-flight'         = '^(true|false)$'
    'spawn-protection'     = 'int:0:64'
    'enable-command-block' = '^(true|false)$'
    'force-gamemode'       = '^(true|false)$'
    'spawn-monsters'       = '^(true|false)$'
}
function Test-Rule([string]$rule, [string]$value) {
    if ($rule -like 'int:*') {
        $p = $rule.Split(':'); $n = 0
        return ($value -cmatch '^\d{1,4}$') -and [int]::TryParse($value, [ref]$n) -and $n -ge [int]$p[1] -and $n -le [int]$p[2]
    }
    return $value -cmatch $rule
}
# KEY=value files (.env, server.properties): every KEY=value line as a hashtable
function Read-KeyValues([string]$path) {
    $h = [ordered]@{}
    if (Test-Path $path) {
        foreach ($l in Get-Content $path) {
            if ($l -match '^\s*([A-Za-z0-9_.-]+)\s*=(.*)$') { $h[$Matches[1]] = $Matches[2].Trim() }
        }
    }
    return $h
}
# Change one KEY=value line (adds it if missing); no BOM, so Java and compose both read it
function Set-KeyValue([string]$path, [string]$key, [string]$value) {
    $lines = @(); if (Test-Path $path) { $lines = @(Get-Content $path) }
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match ('^\s*' + [regex]::Escape($key) + '\s*=')) { $lines[$i] = "$key=$value"; $found = $true }
    }
    if (-not $found) { $lines += "$key=$value" }
    New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null
    [IO.File]::WriteAllLines($path, [string[]]$lines, (New-Object Text.UTF8Encoding $false))
}
function Strip-Codes([string]$s) { $s -replace '\x1b\[[0-9;?]*[A-Za-z]', '' -replace '\xA7.', '' }

function Pack-Env([string]$slug) { Join-Path $PacksDir "$slug.env" }
function Active-Pack {
    if (-not (Test-Path $ActiveFile)) { return '' }
    $s = "$(Get-Content $ActiveFile -Raw -ErrorAction SilentlyContinue)".Trim()
    if ($s -cmatch $SlugRe -and (Test-Path (Pack-Env $s))) { return $s }
    return ''
}
function World-Dir([string]$slug) {
    if ($slug) { return (Join-Path $GameDir "data\packs\$slug") }
    return (Join-Path $GameDir 'data\minecraft')
}
$Active    = Active-Pack
$WorldDir  = World-Dir $Active
$WlFile    = Join-Path $WorldDir 'whitelist.json'
# console commands: no quotes, braces, $, ;, |, & or backticks - nothing a shell could misread
$CmdRe     = '^[A-Za-z0-9 _.,:~@!?#/+=()*\[\]-]{1,200}$'

function Reply($obj) { $obj | ConvertTo-Json -Compress -Depth 4 | Write-Output; exit 0 }
function Fail($msg)  { Reply @{ ok = $false; error = $msg } }
function Log($msg) {
    try { Add-Content -ErrorAction SilentlyContinue -Path 'C:\deck\agent\agent.log' -Value ("{0}  {1}" -f (Get-Date -Format 's'), $msg) } catch { }
}

function Docker-Ready {
    try { docker info --format '{{.ServerVersion}}' 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}

function Mc-State {
    try {
        $s = docker inspect -f '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}' minecraft 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $s) { return @{ state = 'none'; health = '' } }
        $p = "$s".Trim().Split('|')
        return @{ state = $p[0]; health = $(if ($p.Count -gt 1) { $p[1] } else { '' }) }
    } catch { return @{ state = 'unknown'; health = '' } }
}

function Ensure-Docker {
    if (Docker-Ready) { return $true }
    # Docker Desktop is a desktop app: it must run in the signed-in user's session,
    # so we ask a scheduled task (made by setup-k12-agent) to start it there.
    if (-not (Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue)) {
        Log 'starting Docker Desktop via task DeckStartDocker'
        schtasks /run /tn 'DeckStartDocker' 2>$null | Out-Null
    }
    $deadline = (Get-Date).AddSeconds(240)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        if (Docker-Ready) { return $true }
    }
    return $false
}

function Mc-Running { (Docker-Ready) -and ((Mc-State).state -eq 'running') }

function Rcon([string[]]$words) {
    $o = docker exec minecraft rcon-cli @words 2>&1 | Out-String
    return @{ code = $LASTEXITCODE; out = $o.Trim() }
}

function Eula-Accepted {
    [bool](Select-String -Path $EnvFile -Pattern '^\s*MC_EULA\s*=\s*TRUE\s*$' -Quiet -ErrorAction SilentlyContinue)
}

# Keep MC_WHITELIST_PLAYERS in .env in step, so the server's start-up settings
# never re-add someone you removed (or forget someone you added).
function Set-EnvWhitelist([string]$name, [bool]$add) {
    if (-not (Test-Path $EnvFile)) { return }
    $lines = @(Get-Content $EnvFile)
    $i = -1
    for ($k = 0; $k -lt $lines.Count; $k++) { if ($lines[$k] -match '^\s*MC_WHITELIST_PLAYERS\s*=') { $i = $k } }
    $cur = @()
    if ($i -ge 0) { $cur = @(($lines[$i] -split '=', 2)[1] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
    $cur = @($cur | Where-Object { $_ -ne $name })     # -ne is case-insensitive
    if ($add) { $cur += $name }
    $line = 'MC_WHITELIST_PLAYERS=' + ($cur -join ',')
    if ($i -ge 0) { $lines[$i] = $line } else { $lines += $line }
    Set-Content -Path $EnvFile -Value $lines -Encoding ASCII
}

function Read-Whitelist {
    if (-not (Test-Path $WlFile)) { return }
    try { $parsed = Get-Content $WlFile -Raw | ConvertFrom-Json } catch { return }
    # foreach unrolls the array (PowerShell 5.1 hands ConvertFrom-Json arrays over as one object)
    foreach ($e in $parsed) { if ($e.name) { [pscustomobject]@{ uuid = "$($e.uuid)"; name = "$($e.name)" } } }
}

function Write-Whitelist($list) {
    New-Item -ItemType Directory -Force $WorldDir | Out-Null
    # Built by hand: PowerShell 5.1's ConvertTo-Json can wrap arrays in {"value":..,"Count":..}
    $esc = { param($s) ("$s" -replace '\\', '\\' -replace '"', '\"') }
    $items = foreach ($e in @($list)) { if ($e -and $e.name) { '  {"uuid": "' + (& $esc $e.uuid) + '", "name": "' + (& $esc $e.name) + '"}' } }
    $json = if ($items) { "[`n" + (@($items) -join ",`n") + "`n]" } else { '[]' }
    [IO.File]::WriteAllText($WlFile, $json, (New-Object Text.UTF8Encoding $false))   # Java dislikes a BOM
}

# docker compose with .env + the active pack's settings (see deck-compose.ps1)
function Compose([string[]]$a) {
    if (-not (Test-Path $Compose)) { Fail "deck-compose.ps1 missing in $PSScriptRoot - run setup-k12-agent.cmd again" }
    $o = & $Compose @a 2>&1 | ForEach-Object { "$_" } | Out-String
    return @{ code = $LASTEXITCODE; out = $o.Trim() }
}

# Settings in one pack's file, as a hashtable
function Read-PackEnv([string]$slug) {
    $h = @{}
    $p = Pack-Env $slug
    if (Test-Path $p) { foreach ($l in Get-Content $p) { if ($l -match '^\s*([A-Z_][A-Z0-9_]*)\s*=(.*)$') { $h[$Matches[1]] = $Matches[2].Trim() } } }
    return $h
}

# The player list follows you: the new world gets the current world's whitelist.json
function Carry-Whitelist([string]$toSlug) {
    $to = World-Dir $toSlug
    if ($to -eq $WorldDir -or -not (Test-Path $WlFile)) { return }
    New-Item -ItemType Directory -Force $to | Out-Null
    Copy-Item $WlFile (Join-Path $to 'whitelist.json') -Force
}

# Change one KEY=value line in a pack's settings file (adds it if missing)
function Set-PackValue([string]$slug, [string]$key, [string]$value) {
    $p = Pack-Env $slug
    $lines = @(Get-Content $p)
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$key\s*=") { $lines[$i] = "$key=$value"; $found = $true }
    }
    if (-not $found) { $lines += "$key=$value" }
    Set-Content -Path $p -Value $lines -Encoding ASCII
}
function Id-List([string]$s) { @("$s" -split '[,\s]+' | Where-Object { $_ -cmatch $ProjectRe }) }

# What's inside one mod jar: its mod IDs and mixin config names
function Jar-Info([string]$path) {
    $ids = @(); $mixins = @()
    try {
        $z = [IO.Compression.ZipFile]::OpenRead($path)
        try {
            foreach ($e in $z.Entries) {
                $n = $e.FullName
                if ($n -notmatch '/' -and $n -like '*mixin*.json') { $mixins += $n; continue }
                if ($n -in 'fabric.mod.json', 'quilt.mod.json', 'META-INF/mods.toml', 'META-INF/neoforge.mods.toml') {
                    if ($e.Length -gt 1MB) { continue }
                    $r = New-Object IO.StreamReader($e.Open())
                    try { $t = $r.ReadToEnd() } finally { $r.Dispose() }
                    if ($n -like '*.toml') {
                        foreach ($m in [regex]::Matches($t, '(?m)^\s*modId\s*=\s*"([^"]+)"')) { $ids += $m.Groups[1].Value }
                    } else {
                        $m = [regex]::Match($t, '"id"\s*:\s*"([^"]+)"')
                        if ($m.Success) { $ids += $m.Groups[1].Value }
                    }
                }
            }
        } finally { $z.Dispose() }
    } catch { }
    return @{ file = (Split-Path $path -Leaf); ids = @($ids | Select-Object -Unique); mixins = @($mixins) }
}

# Copy exactly $size bytes from stdin (what the Pi sends) into $path. Returns how many arrived.
function Read-Stdin([string]$path, [long]$size) {
    $in = [Console]::OpenStandardInput()
    $out = [IO.File]::Open($path, [IO.FileMode]::Create)
    $got = 0L
    try {
        $buf = New-Object byte[] 65536
        while ($got -lt $size) {
            $n = $in.Read($buf, 0, [int][Math]::Min($buf.Length, $size - $got))
            if ($n -le 0) { break }
            $out.Write($buf, 0, $n); $got += $n
        }
    } finally { $out.Dispose() }
    return $got
}

function Backup-Re([string]$prefix) { '^' + [regex]::Escape($prefix) + '-\d{8}-\d{4,6}\.zip$' }

function Case-Adapter {
    $a = Get-NetIPAddress -IPAddress $CaseIP -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $a) { throw "No adapter has $CaseIP - run setup-k12-network.cmd" }
    return $a.InterfaceIndex
}

function Is-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------------------
$raw = "$env:SSH_ORIGINAL_COMMAND".Trim()
if (-not $raw) { Fail 'no command (this key only runs deck-agent commands)' }
$verb, $rest = $raw -split '\s+', 2
$verb = $verb.ToLowerInvariant()
Log "cmd: $verb"

try {
switch ($verb) {
    'ping' { Reply @{ ok = $true; msg = 'pong'; host = $env:COMPUTERNAME } }

    'info' {
        $ts = $null
        try {
            $tsExe = Get-Command tailscale -ErrorAction SilentlyContinue
            if (-not $tsExe -and (Test-Path 'C:\Program Files\Tailscale\tailscale.exe')) { $tsExe = 'C:\Program Files\Tailscale\tailscale.exe' }
            if ($tsExe) { $ts = (& $tsExe ip -4 2>$null | Select-Object -First 1) }
        } catch { }
        $docker = Docker-Ready
        $mc = if ($docker) { Mc-State } else { @{ state = 'docker-off'; health = '' } }
        $boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
        Reply @{ ok = $true; host = $env:COMPUTERNAME; docker = $docker; mc = $mc.state; health = $mc.health
                 tailscale_ip = $(if ($ts) { "$ts".Trim() } else { $null })
                 uptime_min = [int]((Get-Date) - $boot).TotalMinutes
                 travel = (Test-Path $StateFile); eula = (Eula-Accepted); pack = $Active }
    }

    'start' {
        if (-not (Eula-Accepted)) {
            Fail "Minecraft EULA not accepted yet: set MC_EULA=TRUE in $EnvFile"
        }
        if ($Active -and -not ((Test-Path $SecretFile) -and (Get-Item $SecretFile).Length -gt 0)) {
            Fail 'No CurseForge key on the PC yet - run setup-curseforge.cmd there (README > Modpacks).'
        }
        if (-not (Ensure-Docker)) { Fail 'Docker Desktop did not start. Is Windows signed in (auto sign-in, see README)?' }
        # "since": the Pi asks for log lines after this moment only (Docker keeps earlier runs' logs)
        $since = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - 1
        $r = Compose @('up', '-d')
        if ($r.code -ne 0) { Fail ("docker compose failed: " + $r.out) }
        Reply @{ ok = $true; msg = 'Minecraft container started'; pack = $Active; since = $since }
    }

    'stop' {
        if (-not (Docker-Ready)) { Reply @{ ok = $true; msg = 'Docker is not running, so Minecraft is already stopped.' } }
        $r = Compose @('stop')
        if ($r.code -ne 0) { Fail ("docker compose stop failed: " + $r.out) }
        Reply @{ ok = $true; msg = 'Minecraft stopped, world saved.' }
    }

    'off' {
        if (Docker-Ready) { Compose @('stop') | Out-Null }
        $secs = if ("$rest".Trim() -eq 'now') { 5 } else { 60 }
        shutdown.exe /s /t $secs /c "Cyberdeck: shutting down in $secs seconds (asked by the deck). Cancel: shutdown /a" | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "shutdown refused (exit $LASTEXITCODE)" }
        Reply @{ ok = $true; msg = "Minecraft stopped, world saved. PC shuts down in $secs seconds." }
    }

    'whitelist' {
        $w = @("$rest".Trim() -split '\s+')
        $sub = $w[0].ToLowerInvariant()
        $name = if ($w.Count -gt 1) { $w[1] } else { '' }
        $uuid = if ($w.Count -gt 2) { $w[2].ToLowerInvariant() } else { '' }
        $live = Mc-Running
        if ($sub -eq 'list') {
            if ($live) {
                $r = Rcon @('whitelist', 'list')
                $names = @()
                if ($r.out -match ':\s*(.+)$') { $names = @($Matches[1] -split ',\s*' | Where-Object { $_ }) }
                Reply @{ ok = $true; live = $true; names = $names }
            }
            Reply @{ ok = $true; live = $false; names = @(Read-Whitelist | ForEach-Object { $_.name }) }
        }
        if ($name -notmatch $NameRe) { Fail 'bad player name' }
        if ($uuid -and $uuid -notmatch $UuidRe) { Fail 'bad uuid' }
        if ($sub -eq 'add') {
            Set-EnvWhitelist $name $true
            if ($live) {
                $r = Rcon @('whitelist', 'add', $name)
                if ($r.out -match 'does not exist') { Set-EnvWhitelist $name $false; Fail "Minecraft says there is no player called $name" }
                Reply @{ ok = $true; msg = $r.out }
            }
            $list = @(Read-Whitelist | Where-Object { $_.name -ne $name })
            if ($uuid) { $list += [pscustomobject]@{ uuid = $uuid; name = $name } }
            Write-Whitelist $list
            Reply @{ ok = $true; msg = "added (applies when the server starts)" }
        }
        if ($sub -eq 'remove') {
            Set-EnvWhitelist $name $false
            if ($live) {
                $r = Rcon @('whitelist', 'remove', $name)
                Rcon @('kick', $name, 'Removed from the whitelist') | Out-Null
                Reply @{ ok = $true; msg = $r.out }
            }
            Write-Whitelist @(Read-Whitelist | Where-Object { $_.name -ne $name })
            Reply @{ ok = $true; msg = 'removed' }
        }
        Fail 'use: whitelist list|add|remove <name> [uuid]'
    }

    'backup' {
        # no argument: the world that's on now. "backup <pack>": that pack's world (e.g. before deleting it)
        $slug = "$rest".Trim()
        if ($slug) {
            if ($slug -cnotmatch $SlugRe -or -not (Test-Path (Pack-Env $slug))) { Fail "no modpack called '$slug'" }
        } else { $slug = $Active }
        $bdir = World-Dir $slug
        $prefix = if ($slug) { "pack-$slug" } else { 'minecraft' }
        if (-not (Test-Path (Join-Path $bdir 'world'))) {
            Reply @{ ok = $false; code = 'noworld'; error = "No world yet at $bdir - start Minecraft once first." }
        }
        New-Item -ItemType Directory -Force $BackupDir | Out-Null
        Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
        $name = '{0}-{1}.zip' -f $prefix, (Get-Date -Format 'yyyyMMdd-HHmmss')
        $zipPath = Join-Path $BackupDir $name
        $live = ($slug -eq $Active) -and (Mc-Running)
        if ($live) { Rcon @('save-off') | Out-Null; Rcon @('save-all', 'flush') | Out-Null; Start-Sleep -Seconds 3 }
        # mods/config/libraries re-download with the pack, so a pack backup is just the world + settings
        $skipTop = @('logs', 'cache', '.cache', 'libraries', 'versions', 'crash-reports', 'mods', '.fabric', '.mohist')
        $root = (Resolve-Path $bdir).Path.TrimEnd('\') + '\'
        try {
            $fs = [IO.File]::Open($zipPath, [IO.FileMode]::CreateNew)
            $zip = New-Object IO.Compression.ZipArchive($fs, [IO.Compression.ZipArchiveMode]::Create)
            try {
                foreach ($f in Get-ChildItem $bdir -Recurse -File -Force) {
                    $rel = $f.FullName.Substring($root.Length)
                    $top = ($rel -split '\\')[0]
                    if ($skipTop -contains $top) { continue }
                    if ($rel -notmatch '\\' -and $f.Extension -eq '.jar') { continue }   # server jar re-downloads
                    if ($f.Name -eq 'session.lock') { continue }
                    $entry = $zip.CreateEntry(($rel -replace '\\', '/'), [IO.Compression.CompressionLevel]::Optimal)
                    $es = $entry.Open()
                    $in = [IO.File]::Open($f.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
                    try { $in.CopyTo($es) } finally { $in.Dispose(); $es.Dispose() }
                }
            } finally { $zip.Dispose(); $fs.Dispose() }
        } catch {
            Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
            Fail "zip failed: $($_.Exception.Message)"
        } finally {
            if ($live) { Rcon @('save-on') | Out-Null }
        }
        # keep the newest 10 of this world on the PC too (other worlds keep theirs)
        $re = Backup-Re $prefix
        Get-ChildItem $BackupDir -Filter '*.zip' | Where-Object { $_.Name -match $re } | Sort-Object LastWriteTime -Descending |
            Select-Object -Skip 10 | Remove-Item -Force -ErrorAction SilentlyContinue
        $item = Get-Item $zipPath
        Reply @{ ok = $true; file = $name; bytes = $item.Length
                 sha256 = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLowerInvariant() }
    }

    'backup-get' {
        $name = "$rest".Trim()
        $path = Join-Path $BackupDir $name
        if ($name -cnotmatch $BackupRe -or -not (Test-Path $path)) {
            [Console]::Error.WriteLine("no such backup: $name"); exit 1
        }
        # raw bytes straight to stdout (PowerShell's pipeline would mangle binary)
        $in = [IO.File]::OpenRead($path)
        $out = [Console]::OpenStandardOutput()
        try { $in.CopyTo($out); $out.Flush() } finally { $in.Dispose() }
        exit 0
    }

    'modpack' {
        $w = @("$rest".Trim() -split '\s+')
        $sub = $w[0].ToLowerInvariant()
        if ($sub -eq 'list') {
            $packs = @()
            if (Test-Path $PacksDir) {
                foreach ($f in Get-ChildItem $PacksDir -Filter '*.env') {
                    $slug = $f.BaseName
                    if ($slug -cnotmatch $SlugRe) { continue }
                    $e = Read-PackEnv $slug
                    $packs += @{ slug = $slug; file_id = "$($e['CF_FILE_ID'])"; java = "$($e['MC_IMAGE_TAG'])"
                                 source = $(if ($e['CF_MODPACK_ZIP']) { 'zip' } else { 'curseforge' })
                                 memory = "$($e['MC_MEMORY'])"; excluded = "$($e['CF_EXCLUDE_MODS'])"
                                 has_world = (Test-Path (Join-Path (World-Dir $slug) 'world')) }
                }
            }
            Reply @{ ok = $true; active = $Active; packs = $packs; key = ((Test-Path $SecretFile) -and (Get-Item $SecretFile).Length -gt 0) }
        }
        if ($sub -in 'apply', 'vanilla', 'delete' -and (Mc-Running)) { Fail 'Stop Minecraft first.' }
        if ($sub -eq 'apply') {
            if ($w.Count -ne 6) { Fail 'use: modpack apply <pack> <fileId|latest> <java tag> <heap GB> <limit GB>' }
            $slug, $fid, $tag = $w[1], $w[2], $w[3]
            if ($slug -cnotmatch $SlugRe) { Fail 'bad pack name' }
            if ($fid -ne 'latest' -and $fid -ne 'zip' -and $fid -cnotmatch $FileIdRe) { Fail 'bad file id' }
            if ($tag -cnotmatch $TagRe) { Fail 'bad java tag' }
            # 'zip': a CurseForge "Export profile" zip the Deck app copied to imports\<pack>.zip
            $zipFile = Join-Path $ImportsDir "$slug.zip"
            if ($fid -eq 'zip') {
                if (-not (Test-Path $zipFile)) { Fail "The pack's zip isn't on the PC (imports\$slug.zip) - import it again from the Deck app on this PC." }
                $okZip = $false
                Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
                try { $z = [IO.Compression.ZipFile]::OpenRead($zipFile); try { $okZip = [bool]$z.GetEntry('manifest.json') } finally { $z.Dispose() } } catch { }
                if (-not $okZip) { Fail "imports\$slug.zip isn't a CurseForge modpack export (no manifest.json inside)." }
            }
            $heap = 0; $limit = 0
            if (-not ([int]::TryParse($w[4], [ref]$heap) -and [int]::TryParse($w[5], [ref]$limit)) -or
                $heap -lt 2 -or $heap -gt 24 -or $limit -le $heap -or $limit -gt 28) { Fail 'bad memory numbers' }
            $old = Read-PackEnv $slug      # keep mods the deck had to leave out of this pack
            New-Item -ItemType Directory -Force $PacksDir | Out-Null
            $lines = @(
                '# Written by the deck (Deck Panel > Modpacks). Safe to edit by hand.',
                'MC_TYPE=AUTO_CURSEFORGE',
                # the server image needs a pack name even when it installs from a zip (it names the install after it)
                "CF_SLUG=$slug",
                "CF_FILE_ID=$(if ($fid -eq 'latest' -or $fid -eq 'zip') { '' } else { $fid })",
                "CF_MODPACK_ZIP=$(if ($fid -eq 'zip') { "/imports/$slug.zip" } else { '' })",
                'CF_API_KEY_FILE=/run/secrets/cf_api_key',
                "MC_IMAGE_TAG=$tag",
                "MC_MEMORY=${heap}G",
                "MC_MEM_LIMIT=${limit}g",
                "MC_DATA_DIR=./data/packs/$slug",
                "CF_DOWNLOADS_DIR=./downloads/$slug",
                "CF_EXCLUDE_MODS=$($old['CF_EXCLUDE_MODS'])",
                "CF_FORCE_INCLUDE_MODS=$($old['CF_FORCE_INCLUDE_MODS'])",
                # an imported zip may be a newer export of the same pack: check every mod on the next start
                "CF_FORCE_SYNCHRONIZE=$(if ($fid -eq 'zip') { 'true' } else { $old['CF_FORCE_SYNCHRONIZE'] })")
            Set-Content -Path (Pack-Env $slug) -Value $lines -Encoding ASCII
            Carry-Whitelist $slug
            Set-Content -Path $ActiveFile -Value $slug -Encoding ASCII -NoNewline
            Reply @{ ok = $true; msg = "Modpack $slug is now the active world."; active = $slug }
        }
        if ($sub -eq 'vanilla') {
            Carry-Whitelist ''
            Remove-Item $ActiveFile -Force -ErrorAction SilentlyContinue
            Reply @{ ok = $true; msg = 'Back to vanilla Minecraft.'; active = '' }
        }
        if ($sub -eq 'delete') {
            $slug = if ($w.Count -gt 1) { $w[1] } else { '' }
            if ($slug -cnotmatch $SlugRe -or -not (Test-Path (Pack-Env $slug))) { Fail "no modpack called '$slug'" }
            if ($slug -eq $Active) { Fail 'That pack is on right now - switch to another one (or vanilla) first.' }
            $data = World-Dir $slug
            if (Test-Path (Join-Path $data 'world')) {
                # never delete a world that wasn't just backed up
                $re = Backup-Re "pack-$slug"
                $fresh = Get-ChildItem $BackupDir -Filter '*.zip' -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match $re -and $_.LastWriteTime -gt (Get-Date).AddMinutes(-30) }
                if (-not $fresh) { Fail "No fresh backup of $slug - back it up first." }
            }
            if (Test-Path $data) { Remove-Item $data -Recurse -Force -ErrorAction Stop }
            Remove-Item (Join-Path $GameDir "downloads\$slug") -Recurse -Force -ErrorAction SilentlyContinue
            $zp = Join-Path $ImportsDir "$slug.zip"; if ([IO.File]::Exists($zp)) { [IO.File]::Delete($zp) }
            Remove-Item (Pack-Env $slug) -Force
            Reply @{ ok = $true; msg = "Deleted $slug (its backups are kept)." }
        }
        if ($sub -in 'mods', 'exclude', 'include', 'synced', 'upload', 'preset') {
            $slug = if ($w.Count -gt 1) { $w[1] } else { '' }
            if ($slug -cnotmatch $SlugRe -or -not (Test-Path (Pack-Env $slug))) { Fail "no modpack called '$slug'" }
        }
        if ($sub -eq 'mods') {
            Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
            $dir = Join-Path (World-Dir $slug) 'mods'
            $mods = @()
            if (Test-Path $dir) { foreach ($j in Get-ChildItem $dir -Filter '*.jar' -File) { $mods += Jar-Info $j.FullName } }
            Reply @{ ok = $true; mods = $mods }
        }
        if ($sub -in 'exclude', 'include') {
            $id = if ($w.Count -gt 2) { $w[2] } else { '' }
            if ($id -cnotmatch $ProjectRe) { Fail 'bad project id' }
            $e = Read-PackEnv $slug
            $ex = @(Id-List $e['CF_EXCLUDE_MODS'] | Where-Object { $_ -ne $id })
            $inc = @(Id-List $e['CF_FORCE_INCLUDE_MODS'] | Where-Object { $_ -ne $id })
            # keep any slugs someone typed by hand
            $exOther = @("$($e['CF_EXCLUDE_MODS'])" -split '[,\s]+' | Where-Object { $_ -and $_ -cnotmatch $ProjectRe })
            if ($sub -eq 'exclude') { $ex += $id } else { $inc += $id }
            Set-PackValue $slug 'CF_EXCLUDE_MODS' (($exOther + $ex) -join ',')
            Set-PackValue $slug 'CF_FORCE_INCLUDE_MODS' ($inc -join ',')
            # without this the server keeps an already-installed jar on the next start
            Set-PackValue $slug 'CF_FORCE_SYNCHRONIZE' 'true'
            Reply @{ ok = $true; excluded = (($exOther + $ex) -join ','); included = ($inc -join ',') }
        }
        if ($sub -eq 'synced') {
            Set-PackValue $slug 'CF_FORCE_SYNCHRONIZE' ''
            Reply @{ ok = $true }
        }
        if ($sub -eq 'upload') {
            if ($w.Count -ne 4) { Fail 'use: modpack upload <pack> <name> <bytes>' }
            try {
                $b64 = $w[2].Replace('-', '+').Replace('_', '/')
                $b64 += '=' * ((4 - $b64.Length % 4) % 4)
                $name = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
            } catch { Fail 'bad file name' }
            if ($name -cnotmatch $JarRe) { Fail 'bad file name (only .jar mods)' }
            $size = 0L
            if (-not [long]::TryParse($w[3], [ref]$size) -or $size -lt 4 -or $size -gt $MaxUpload) { Fail 'file too big (100 MB max) or empty' }
            $dir = Join-Path $GameDir "downloads\$slug"
            New-Item -ItemType Directory -Force $dir | Out-Null
            $dest = Join-Path $dir $name
            $tmp = "$dest.part"
            $got = Read-Stdin $tmp $size
            $head = New-Object byte[] 2
            $fs = [IO.File]::OpenRead($tmp); try { [void]$fs.Read($head, 0, 2) } finally { $fs.Dispose() }
            # [IO.File] (not Move-Item): names like "mod [1.2].jar" have [ ] that PowerShell treats as wildcards
            if ($got -ne $size -or $head[0] -ne 0x50 -or $head[1] -ne 0x4B) {
                [IO.File]::Delete($tmp)
                Fail $(if ($got -ne $size) { "only got $got of $size bytes" } else { "that isn't a .jar (zip) file" })
            }
            if ([IO.File]::Exists($dest)) { [IO.File]::Delete($dest) }
            [IO.File]::Move($tmp, $dest)
            Reply @{ ok = $true; msg = "Saved $name for $slug."; file = $name }
        }
        if ($sub -eq 'preset') {
            # the pack's keybind preset (docs\keybind-preset-format.md), where the keybind mod on the server reads it
            $pdir = Join-Path (World-Dir $slug) 'config\keypresets'
            $dest = Join-Path $pdir 'server.json'
            $arg = if ($w.Count -gt 2) { $w[2] } else { '' }
            if ($arg -eq 'clear') {
                if ([IO.File]::Exists($dest)) { [IO.File]::Delete($dest) }
                Reply @{ ok = $true; msg = "Keybind preset removed from $slug." }
            }
            $size = 0L
            if (-not [long]::TryParse($arg, [ref]$size) -or $size -lt 2 -or $size -gt 512KB) { Fail 'preset empty or over 512 KB' }
            New-Item -ItemType Directory -Force $pdir | Out-Null
            $tmp = "$dest.part"
            $got = Read-Stdin $tmp $size
            $ok = $false
            if ($got -eq $size) {
                try { $j = [IO.File]::ReadAllText($tmp) | ConvertFrom-Json; $ok = ($j.format -ceq 'cyberdeck-keys/1') -and ($j.keys -ne $null) } catch { }
            }
            if (-not $ok) { [IO.File]::Delete($tmp); Fail "that isn't a keybind preset (format cyberdeck-keys/1)" }
            if ([IO.File]::Exists($dest)) { [IO.File]::Delete($dest) }
            [IO.File]::Move($tmp, $dest)
            Reply @{ ok = $true; msg = "Keybind preset saved for $slug." }
        }
        if ($sub -eq 'diagnose') {
            # crash details for the Pi's auto-fix: recent log + newest crash report of the active world
            $lines = @()
            $sinceArg = @()
            if ($w.Count -gt 1 -and $w[1] -cmatch '^\d{9,11}$') { $sinceArg = @('--since', $w[1]) }
            if (Docker-Ready) {
                $lines = @(docker logs --tail 400 @sinceArg minecraft 2>&1 | ForEach-Object { "$_" -replace '\x1b\[[0-9;?]*[A-Za-z]', '' } |
                           Where-Object { $_.Trim() } | ForEach-Object { if ($_.Length -gt 400) { $_.Substring(0, 400) } else { $_ } })
            }
            $crash = @()
            $cdir = Join-Path $WorldDir 'crash-reports'
            if (Test-Path $cdir) {
                $c = Get-ChildItem $cdir -Filter '*.txt' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
                if ($c -and $c.LastWriteTime -gt (Get-Date).AddMinutes(-30)) {
                    $crash = @(Get-Content $c.FullName -TotalCount 300 | ForEach-Object { if ($_.Length -gt 400) { $_.Substring(0, 400) } else { $_ } })
                }
            }
            Reply @{ ok = $true; running = (Mc-Running); lines = $lines; crash = $crash; pack = $Active }
        }
        Fail 'use: modpack list|apply|vanilla|delete|mods|diagnose|exclude|include|synced|upload|preset'
    }

    'settings' {
        $w = @("$rest".Trim() -split '\s+')
        $sub = $w[0].ToLowerInvariant()
        $propsFile = Join-Path $WorldDir 'server.properties'
        if ($sub -eq 'get') {
            $envAll = Read-KeyValues $EnvFile
            $propAll = Read-KeyValues $propsFile
            $envOut = [ordered]@{}; foreach ($k in $EnvRules.Keys) { if ($envAll.Contains($k)) { $envOut[$k] = $envAll[$k] } }
            $props = [ordered]@{}; foreach ($k in $PropRules.Keys) { if ($propAll.Contains($k)) { $props[$k] = $propAll[$k] } }
            # settings the active modpack sets itself (its packs\<pack>.env wins over .env)
            $packOwn = @()
            if ($Active) { $pe = Read-PackEnv $Active; $packOwn = @($EnvRules.Keys | Where-Object { $pe.ContainsKey($_) -and $pe[$_] }) }
            Reply @{ ok = $true; env = $envOut; props = $props; pack = $Active; pack_sets = $packOwn
                     props_exist = (Test-Path $propsFile); running = (Mc-Running) }
        }
        if ($sub -ne 'set' -or $w.Count -ne 4) { Fail 'use: settings get | settings set env|prop <key> <value>' }
        $kind, $key = $w[1].ToLowerInvariant(), $w[2]
        $rules = if ($kind -eq 'env') { $EnvRules } elseif ($kind -eq 'prop') { $PropRules } else { Fail 'use: settings set env|prop <key> <value>' }
        if (-not $rules.Contains($key)) { Fail "'$key' can't be changed from the app" }
        try {
            $b64 = $w[3].Replace('-', '+').Replace('_', '/')
            $b64 += '=' * ((4 - $b64.Length % 4) % 4)
            $value = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
        } catch { Fail 'bad value' }
        if (-not (Test-Rule $rules[$key] $value)) { Fail "That value isn't allowed for $key." }
        if ($kind -eq 'env') { Set-KeyValue $EnvFile $key $value } else { Set-KeyValue $propsFile $key $value }
        Log "settings: $kind $key"
        Reply @{ ok = $true; msg = "$key = $value. It takes effect when Minecraft restarts."; key = $key; value = $value }
    }

    'perf' {
        if (-not (Mc-Running)) { Reply @{ ok = $true; running = $false } }
        $r = @{ ok = $true; running = $true; pack = $Active }
        # the container's CPU (100% = one core) and memory
        $s = "$(docker stats --no-stream --format '{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}' minecraft 2>$null)".Trim()
        $p = $s.Split('|')
        if ($p.Count -eq 3) {
            $r.cpu = [double]($p[0] -replace '[^\d.]', '')
            $mem = $p[1].Split('/')
            $r.mem_used = $mem[0].Trim(); $r.mem_limit = $(if ($mem.Count -gt 1) { $mem[1].Trim() } else { '' })
            $r.mem_pct = [double]($p[2] -replace '[^\d.]', '')
        }
        # tick speed: vanilla 1.20.3+ "tick query", Forge/NeoForge "forge tps", Paper "tps".
        # The command that worked last time for this world is tried first (each try is a docker exec).
        $memo = Join-Path $PSScriptRoot 'perf-method.json'
        $order = @('tick query', 'neoforge tps', 'forge tps', 'tps')
        try { $m = Get-Content $memo -Raw -ErrorAction Stop | ConvertFrom-Json; if ($m.pack -eq $Active -and $m.cmd -in $order) { $order = @($m.cmd) + @($order | Where-Object { $_ -ne $m.cmd }) } } catch { }
        foreach ($c in $order) {
            $o = Strip-Codes (Rcon ($c -split ' ')).out
            $mspt = $null; $tps = $null
            if ($o -match 'Average time per tick:\s*([\d.]+)\s*ms') { $mspt = [double]$Matches[1] }
            elseif ($o -match 'Overall.*?Mean tick time:\s*([\d.]+)\s*ms.*?Mean TPS:\s*([\d.]+)') { $mspt = [double]$Matches[1]; $tps = [double]$Matches[2] }
            elseif ($o -match 'TPS from last 1m, 5m, 15m:\s*\*?([\d.]+)') { $tps = [double]$Matches[1] }
            else { continue }
            if ($null -eq $tps) { $tps = [math]::Round([math]::Min(20, 1000 / [math]::Max($mspt, 50)), 1) }
            $r.tps = $tps; if ($null -ne $mspt) { $r.mspt = $mspt }
            $r.method = $c
            try { @{ pack = $Active; cmd = $c } | ConvertTo-Json -Compress | Set-Content $memo -Encoding ASCII } catch { }
            break
        }
        if (-not $r.ContainsKey('tps')) { $r.note = "This Minecraft version doesn't report tick speed (vanilla needs 1.20.3 or newer)." }
        Reply $r
    }

    'logs' {
        if (-not (Docker-Ready)) { Reply @{ ok = $true; running = $false; lines = @() } }
        $running = ((Mc-State).state -eq 'running')
        # "logs <unix time>": only lines after that moment
        $sinceArg = @()
        if ("$rest".Trim() -cmatch '^\d{9,11}$') { $sinceArg = @('--since', "$rest".Trim()) }
        # "$_" turns docker's stderr lines (ErrorRecords in PowerShell 5.1) back into plain text
        $lines = @(docker logs --tail 80 @sinceArg minecraft 2>&1 | ForEach-Object { "$_" -replace '\x1b\[[0-9;?]*[A-Za-z]', '' } |
                   Where-Object { $_.Trim() } | ForEach-Object { if ($_.Length -gt 300) { $_.Substring(0, 300) } else { $_ } })
        if ($LASTEXITCODE -ne 0 -and -not $running) { $lines = @() }
        Reply @{ ok = $true; running = $running; lines = $lines }
    }

    'cmd' {
        $text = "$rest".Trim().TrimStart('/')
        if ($text -notmatch $CmdRe) { Fail 'That command has characters the deck does not send (quotes, braces, ;, $, | ...).' }
        $words = @($text -split '\s+')
        $first = $words[0].ToLowerInvariant()
        if ($first -eq 'stop')      { Fail 'Use the Stop button - it also backs the world up.' }
        if ($first -eq 'whitelist') { Fail 'Use Players allowed - it keeps the start-up list in step.' }
        if (-not (Mc-Running)) { Fail "Minecraft isn't running." }
        $r = Rcon $words
        $out = $r.out -replace '\x1b\[[0-9;?]*[A-Za-z]', '' -replace '\u00a7.', ''   # colour codes
        Reply @{ ok = ($r.code -eq 0); msg = $(if ($out) { $out } else { 'done' }); error = $(if ($r.code -ne 0) { $out } else { $null }) }
    }

    'cancel-off' {
        shutdown.exe /a | Out-Null
        Reply @{ ok = $true; msg = 'Shutdown cancelled.' }
    }

    'say' {
        $text = ("$rest" -replace "[^A-Za-z0-9 .,!?:'()/+-]", '')
        if ($text.Length -gt 200) { $text = $text.Substring(0, 200) }
        if (-not $text) { Fail 'nothing to say' }
        docker exec minecraft rcon-cli say "[Deck] $text" 2>&1 | Out-Null
        Reply @{ ok = ($LASTEXITCODE -eq 0); msg = 'sent' }
    }

    'travel' {
        if (-not (Is-Admin)) { Fail 'agent is not running elevated - the PC user must be an administrator' }
        $idx = Case-Adapter
        $mode = "$rest".Trim().ToLowerInvariant()
        if ($mode -eq 'on') {
            Get-NetRoute -InterfaceIndex $idx -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Remove-NetRoute -Confirm:$false
            New-NetRoute -InterfaceIndex $idx -DestinationPrefix '0.0.0.0/0' -NextHop $PiIP -RouteMetric 0 -ErrorAction Stop | Out-Null
            Set-DnsClientServerAddress -InterfaceIndex $idx -ServerAddresses $PiIP -ErrorAction Stop
            # Turn PC Wi-Fi off so traffic can't go to a Wi-Fi that's stuck at a login page.
            $wifi = @(Get-NetAdapter -Physical | Where-Object { $_.NdisPhysicalMedium -eq 9 -and $_.Status -ne 'Disabled' })
            foreach ($w in $wifi) { Disable-NetAdapter -Name $w.Name -Confirm:$false -ErrorAction Stop }
            @{ wifi = @($wifi | ForEach-Object { $_.Name }) } | ConvertTo-Json | Set-Content $StateFile
            Clear-DnsClientCache
            Reply @{ ok = $true; msg = 'PC now uses the Pi for internet'; wifi_off = @($wifi | ForEach-Object { $_.Name }) }
        } elseif ($mode -eq 'off') {
            Get-NetRoute -InterfaceIndex $idx -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Remove-NetRoute -Confirm:$false
            Set-DnsClientServerAddress -InterfaceIndex $idx -ResetServerAddresses -ErrorAction Stop
            $names = @()
            if (Test-Path $StateFile) {
                try { $names = @((Get-Content $StateFile -Raw | ConvertFrom-Json).wifi) } catch { }
                Remove-Item $StateFile -Force
            }
            foreach ($n in $names) { if ($n) { Enable-NetAdapter -Name $n -Confirm:$false -ErrorAction SilentlyContinue } }
            Clear-DnsClientCache
            Reply @{ ok = $true; msg = 'PC back on its own internet' }
        } else { Fail 'use: travel on|off' }
    }

    default { Fail "unknown command '$verb'" }
}
} catch {
    Log "error: $($_.Exception.Message)"
    Fail $_.Exception.Message
}
