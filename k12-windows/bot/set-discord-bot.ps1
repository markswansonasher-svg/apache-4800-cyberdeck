# ---------------------------------------------------------------------------
# Connects the deck's Discord bot to your Discord server (the PC runs the bot).
#
# Before this: https://discord.com/developers/applications > New Application (e.g. "Deck")
#   > Bot > Reset Token > copy it.
#
# This script:
#   1. asks for the token (hidden), checks it with Discord, saves it as the Windows user
#      variable DISCORD_TOKEN (never in a file, never shown)
#   2. opens the "add the bot to your server" page
#   3. lists the servers the bot is in and their text channels - you pick by number
#   4. makes the application's owner (you) the deck's owner
#   5. writes C:\deck\bot\config.json and restarts the bot
# Run again to change the channel, or type  remove  instead of a token to delete it.
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Api = 'https://discord.com/api/v10'
$Config = 'C:\deck\bot\config.json'
$Perms = 2048 + 16384 + 1024     # Send Messages + Embed Links + View Channel

$UA = 'DiscordBot (https://github.com/markswansonasher-svg/apache-4800-cyberdeck, 1.0)'   # Discord wants a bot-style User-Agent
function Get-Discord([string]$path) {
    # one retry: Discord sometimes answers a first call with a passing "internal network error"
    for ($i = 0; ; $i++) {
        # assigned first, then written out: Windows PowerShell hands a JSON list back as ONE object, and this
        # unrolls it into its items (otherwise "pick a channel" sees a single item holding every channel)
        try { $r = Invoke-RestMethod -TimeoutSec 20 -UserAgent $UA -Headers $H "$Api$path"; return $r | ForEach-Object { $_ } }
        catch { if ($i -ge 1 -or $_.Exception.Response.StatusCode.value__ -in 401, 403, 404) { throw }; Start-Sleep -Seconds 2 }
    }
}
function Say($m)  { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Good($m) { Write-Host "OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "!!  $m" -ForegroundColor Yellow }
function Pick([string]$what, $items, [scriptblock]$label) {
    if ($items.Count -eq 1) { Good "$what`: $(& $label $items[0])"; return $items[0] }
    for ($i = 0; $i -lt $items.Count; $i++) { Write-Host ("  {0}. {1}" -f ($i + 1), (& $label $items[$i])) }
    while ($true) {
        $a = (Read-Host "Which $what? (number)").Trim()
        if ($a -match '^\d+$' -and [int]$a -ge 1 -and [int]$a -le $items.Count) { return $items[[int]$a - 1] }
    }
}

# --- 1. token ---------------------------------------------------------------------------
$tok = [Environment]::GetEnvironmentVariable('DISCORD_TOKEN', 'User')
$change = $true
if ($tok) {
    Write-Host 'A bot token is already saved.'
    $change = (Read-Host 'Replace it? [y/N]').Trim() -match '^y'
}
if ($change) {
    Write-Host 'Get it at https://discord.com/developers/applications > your app > Bot > Reset Token.'
    $sec = Read-Host 'Paste the bot token (hidden)' -AsSecureString
    $t = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)).Trim()
    $sec = $null
    if ($t -eq 'remove') { [Environment]::SetEnvironmentVariable('DISCORD_TOKEN', $null, 'User'); Warn 'Token removed. The bot stops after its next restart.'; return }
    if ($t -notmatch '^[\w\-.]{50,100}$') { Warn "That doesn't look like a bot token (copy it again from the Bot page)."; return }
    $tok = $t; $t = $null
}
$H = @{ Authorization = "Bot $tok" }
Say 'Checking the token with Discord'
try { $me = Get-Discord '/users/@me' }
catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 401) { Warn 'Discord refused that token. Reset it on the Bot page and paste the new one.' } else { Warn "Couldn't reach Discord: $($_.Exception.Message)" }
    return
}
Good "Token works: the bot is $($me.username)"
if ($change) { [Environment]::SetEnvironmentVariable('DISCORD_TOKEN', $tok, 'User'); Good 'Saved (Windows user variable DISCORD_TOKEN).' }
# the app's owner = you. If Discord won't say, the server's owner is used instead (below).
$appId = $me.id; $ownerId = $null      # a bot's user ID is also its application ID
try {
    $app = Get-Discord '/oauth2/applications/@me'
    $appId = $app.id
    $ownerId = if ($app.team) { $app.team.owner_user_id } else { $app.owner.id }
} catch { }

# --- 2. add to server -----------------------------------------------------------------------
$guilds = @(Get-Discord '/users/@me/guilds')
$invite = "https://discord.com/oauth2/authorize?client_id=$appId&scope=bot+applications.commands&permissions=$Perms"
if (-not $guilds.Count -or (Read-Host "`nAdd the bot to another server? [y/N]").Trim() -match '^y') {
    Say 'Adding the bot to your server'
    Write-Host 'Your browser opens Discord: pick your server, then Authorize.'
    Write-Host "(Link, if it doesn't open: $invite)"
    Start-Process $invite
    Read-Host 'Press Enter here once the bot is in your server'
    $guilds = @(Get-Discord '/users/@me/guilds')
    if (-not $guilds.Count) { Warn 'The bot isn''t in any server yet. Run this again after adding it.'; return }
}

# --- 3. server + channel ----------------------------------------------------------------------
Say 'Your server'
$g = Pick 'server' $guilds { param($x) $x.name }
$chans = @(Get-Discord "/guilds/$($g.id)/channels" | Where-Object { $_.type -eq 0 } | Sort-Object position)
if (-not $chans.Count) { Warn 'That server has no text channels the bot can see.'; return }
Say 'Which channel should the deck post in? (server up/down, joins, backups, heat warnings)'
$ch = Pick 'channel' $chans { param($x) "#$($x.name)" }

# --- 4. owner + role --------------------------------------------------------------------------
$how = "the app's owner"
if (-not $ownerId) { $ownerId = (Get-Discord "/guilds/$($g.id)").owner_id; $how = "the server's owner" }
$owner = Get-Discord "/users/$ownerId"
Good "Owner (can let people in, switch modpacks): $($owner.username) - $how"
$roles = @(Get-Discord "/guilds/$($g.id)/roles" | ForEach-Object { $_.name })
$role = 'deck-admin'
if ($roles -notcontains $role) {
    Warn "There's no '$role' role yet. Make one in Server Settings > Roles and give it to people who may start/stop the server (you can always do it, as the owner)."
}

# --- 5. save + restart ------------------------------------------------------------------------
New-Item -ItemType Directory -Force (Split-Path $Config) | Out-Null
$cfg = @{}
if (Test-Path $Config) { try { (Get-Content $Config -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $cfg[$_.Name] = $_.Value } } catch { } }
$cfg.discord_guild_id = "$($g.id)"
$cfg.discord_channel_id = "$($ch.id)"
$cfg.discord_owner_ids = @("$ownerId")
$cfg.discord_admin_role = $role
[IO.File]::WriteAllText($Config, ($cfg | ConvertTo-Json -Depth 4), (New-Object Text.UTF8Encoding $false))
Good "Saved $Config"

Say 'Restarting the bot'
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match 'deckhost\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
$pyw = 'C:\deck\bot\venv\Scripts\pythonw.exe'
if (Test-Path $pyw) {
    $env:DISCORD_TOKEN = $tok
    Start-Process $pyw -ArgumentList '"C:\deck\bot\deckhost.py"' -WorkingDirectory 'C:\deck\bot'
    $tok = $null
    Good "Started. In #$($ch.name), type /mc status (commands can take a minute to show up the first time)."
} else { Warn 'The bot isn''t installed yet: run install-bot.cmd.' }
