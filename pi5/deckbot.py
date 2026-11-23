#!/usr/bin/env python3
"""
deckbot - the cyberdeck's Discord bot. Runs on the Pi as a service.

Slash commands
  /mc status            is it up, who's on, join address      (anyone)
  /mc start             wake the PC if needed + start Minecraft   (admin role)
  /mc stop              save the world + stop the server            (admin role)
  /mc off               stop the server + shut the PC down        (admin role)
  /mc backup            back the world up to the Pi now             (admin role)
  /mc whitelist add|remove|list                                     (OWNER only; list: admin)
  /mc invite @friend <minecraft name>  whitelist + Tailscale link + DM    (OWNER only)
  /mc modpack install <link>   look a CurseForge pack up, confirm, install  (OWNER only)
  /mc modpack switch <name> | vanilla   change which world is on          (OWNER only)
  /mc modpack list      installed packs + which one is on              (anyone)
  /deck temps           PC + Pi temperatures                       (anyone)
  /deck check [full]    self-test; full also wakes + checks the PC (admin role)
  /deck pack            pack-up steps (finish on the Deck Panel)    (OWNER only)

"OWNER" = the Discord user IDs in discord_owner_ids (set by deck-discord-setup).

It also posts what the watchdog sees (server up/down, joins/leaves, heat
warnings) to one channel. Only connects OUT to Discord - opens no ports.

Settings: token in /etc/deck/discord.env (DISCORD_TOKEN=...), channel / role /
server ID in /etc/deck/config.json. Run  sudo deck-discord-setup  to fill them in.
Tailscale API key (for /mc invite) in /etc/deck/tailscale.env: sudo deck-tailscale-setup
"""
import asyncio
import os
import sys

import discord
from discord import app_commands

import decklib as d

TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
COLORS = {"good": 0x3DDC84, "info": 0x4CC9F0, "warn": 0xF2C94C, "bad": 0xFF5C5C}
# event kinds worth a channel message (everything else stays on the Deck Panel)
POST_KINDS = {"mc", "mc_up", "mc_down", "join", "leave", "idle", "temp", "k12",
              "whitelist", "backup", "pack", "check", "modpack"}
TS_API_KEY = os.environ.get("TS_API_KEY", "").strip()


def cfg():
    return d.load_config()


def is_owner(inter: discord.Interaction) -> bool:
    owners = {str(x).strip() for x in (cfg().get("discord_owner_ids") or [])}
    return str(inter.user.id) in owners


def is_admin(inter: discord.Interaction) -> bool:
    if is_owner(inter):
        return True
    role = (cfg().get("discord_admin_role") or "deck-admin").lower()
    roles = getattr(inter.user, "roles", []) or []
    return any(r.name.lower() == role for r in roles)


def fmt_temp(v):
    return f"{v:.0f} °C" if isinstance(v, (int, float)) else "?"


def status_embed():
    c = cfg()
    s = d.read_json("state.json", {}) or {}
    k12, mc = s.get("k12") or {}, s.get("mc") or {}
    act = d.current_action()
    if mc.get("online"):
        title, color = "🟢 Minecraft is up", COLORS["good"]
    elif k12.get("up"):
        title, color = "🟡 PC is on, Minecraft is down", COLORS["warn"]
    else:
        title, color = "⚫ Deck is asleep", 0x555555
    e = discord.Embed(title=title, color=color)
    if mc.get("online"):
        names = ", ".join(mc.get("names") or []) or "nobody"
        e.add_field(name=f"Players {mc.get('players', 0)}/{mc.get('max', 0)}", value=names, inline=False)
        if mc.get("version"):
            e.add_field(name="Version", value=mc["version"])
        join = d.join_address(c, s)
        if join:
            e.add_field(name="Join address", value=f"`{join}`")
        if s.get("idle_left_s") is not None:
            e.add_field(name="Empty server stops in", value=f"{s['idle_left_s'] // 60} min")
    t = k12.get("temps") or {}
    e.add_field(name="Temps", value=f"PC CPU {fmt_temp(t.get('cpu'))} · Pi {fmt_temp((s.get('pi') or {}).get('temp'))}",
                inline=False)
    if act:
        e.add_field(name="Working on", value=f"{act['what']} (by {act['by']})", inline=False)
    if not mc.get("online") and not act:
        e.set_footer(text="Use /mc start to wake it up.")
    return e


class DeckBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.last_event = 0
        self.poster = None

    async def setup_hook(self):
        self.tree.add_command(mc_group)
        self.tree.add_command(deck_group)
        gid = str(cfg().get("discord_guild_id") or "").strip()
        if gid.isdigit():
            guild = discord.Object(id=int(gid))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)      # instant in that server
        else:
            await self.tree.sync()                 # global: can take up to an hour the first time
        evs = d.read_events(0, limit=1)
        self.last_event = evs[-1]["id"] if evs else 0   # don't replay old history on restart
        self.poster = asyncio.create_task(self.post_events())

    async def on_ready(self):
        print(f"deckbot logged in as {self.user}", flush=True)

    async def post_events(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                ch_id = str(cfg().get("discord_channel_id") or "").strip()
                events = d.read_events(self.last_event, limit=20)
                if events:
                    self.last_event = events[-1]["id"]
                if ch_id.isdigit() and events:
                    ch = self.get_channel(int(ch_id)) or await self.fetch_channel(int(ch_id))
                    for ev in events:
                        if ev.get("kind") in POST_KINDS and "(asked by" not in ev["text"]:
                            await ch.send(embed=discord.Embed(description=ev["text"],
                                                              color=COLORS.get(ev.get("level"), 0x888888)))
            except Exception as e:
                print(f"event post failed: {e!r}", file=sys.stderr, flush=True)
            await asyncio.sleep(5)


bot = DeckBot()
mc_group = app_commands.Group(name="mc", description="Minecraft server on the cyberdeck")
deck_group = app_commands.Group(name="deck", description="The cyberdeck itself")


async def deny(inter):
    role = cfg().get("discord_admin_role") or "deck-admin"
    await inter.response.send_message(f"You need the **{role}** role for that.", ephemeral=True)


def who(inter):
    return f"{inter.user.display_name} (Discord)"


async def run_action(fn, *args, **kw):
    try:
        return await asyncio.to_thread(fn, *args, **kw)
    except d.Busy as e:
        d.add_event(str(e).capitalize() + ".", "warn", "mc")
    except Exception as e:
        d.add_event(f"Error: {e}", "bad", "mc")


@mc_group.command(name="status", description="Is the server up and who's playing?")
async def mc_status_cmd(inter: discord.Interaction):
    await inter.response.send_message(embed=status_embed())


@mc_group.command(name="start", description="Wake the deck's PC and start Minecraft")
async def mc_start(inter: discord.Interaction):
    if not is_admin(inter):
        return await deny(inter)
    s = d.read_json("state.json", {}) or {}
    if (s.get("mc") or {}).get("online"):
        return await inter.response.send_message(embed=status_embed())
    if d.current_action():
        return await inter.response.send_message("Already working on it - watch this channel.", ephemeral=True)
    await inter.response.send_message("Starting… I'll post here when it's ready (usually 1–4 minutes).")
    asyncio.create_task(run_action(d.start_minecraft, cfg(), who(inter)))


@mc_group.command(name="stop", description="Save the world and stop the server (PC stays on)")
async def mc_stop(inter: discord.Interaction):
    if not is_admin(inter):
        return await deny(inter)
    await inter.response.send_message("Saving and stopping…")
    asyncio.create_task(run_action(d.stop_minecraft, cfg(), who(inter)))


@mc_group.command(name="off", description="Stop the server and shut the deck's PC down")
async def mc_off(inter: discord.Interaction):
    if not is_admin(inter):
        return await deny(inter)
    await inter.response.send_message("Saving, stopping, and shutting the PC down in 1 minute…")
    asyncio.create_task(run_action(d.stop_minecraft, cfg(), who(inter), power_off=True))


@deck_group.command(name="temps", description="Temperatures inside the deck")
async def deck_temps(inter: discord.Interaction):
    s = d.read_json("state.json", {}) or {}
    t = (s.get("k12") or {}).get("temps") or {}
    lines = [f"**PC CPU** {fmt_temp(t.get('cpu'))}",
             f"**PC GPU** {fmt_temp(t.get('gpu'))}",
             f"**PC SSD** {fmt_temp(t.get('drive'))}",
             f"**Pi** {fmt_temp((s.get('pi') or {}).get('temp'))}"]
    lvl = s.get("temp_level", "ok")
    color = {"ok": COLORS["good"], "warn": COLORS["warn"]}.get(lvl, COLORS["bad"])
    if not (s.get("k12") or {}).get("up"):
        lines[0:3] = ["PC is off."]
    await inter.response.send_message(embed=discord.Embed(title="Deck temperatures",
                                                          description="\n".join(lines), color=color))


async def deny_owner(inter):
    await inter.response.send_message("Only the deck's owner can do that.", ephemeral=True)


# ------------------------------------------------------------ whitelist -----
wl_group = app_commands.Group(name="whitelist", description="Who may join the server", parent=mc_group)


@wl_group.command(name="add", description="Let a player join (owner only)")
@app_commands.describe(minecraft_name="Their Minecraft (Java) name")
async def wl_add(inter: discord.Interaction, minecraft_name: str):
    if not is_owner(inter):
        return await deny_owner(inter)
    await inter.response.defer(ephemeral=True, thinking=True)
    r = await asyncio.to_thread(d.whitelist_change, cfg(), "add", minecraft_name, who(inter))
    await inter.followup.send(("✅ " + r["msg"]) if r.get("ok") else ("❌ " + r["error"]), ephemeral=True)


@wl_group.command(name="remove", description="Remove a player (kicks them if online; owner only)")
@app_commands.describe(minecraft_name="Their Minecraft name")
async def wl_remove(inter: discord.Interaction, minecraft_name: str):
    if not is_owner(inter):
        return await deny_owner(inter)
    await inter.response.defer(ephemeral=True, thinking=True)
    r = await asyncio.to_thread(d.whitelist_change, cfg(), "remove", minecraft_name, who(inter))
    await inter.followup.send(("✅ " + r["msg"]) if r.get("ok") else ("❌ " + r["error"]), ephemeral=True)


@wl_group.command(name="list", description="Who is allowed to join")
async def wl_list(inter: discord.Interaction):
    if not is_admin(inter):
        return await deny(inter)
    await inter.response.defer(ephemeral=True, thinking=True)
    r = await asyncio.to_thread(d.whitelist_list, cfg())
    names = ", ".join(r.get("names") or []) or "nobody yet"
    extra = "" if r.get("live") else "\n*(PC is off - this is the last list the Pi saw)*"
    if r.get("pending"):
        extra += "\nWaiting for the PC: " + ", ".join(f"{p['action']} {p['name']}" for p in r["pending"])
    await inter.followup.send(f"**Allowed to join:** {names}{extra}", ephemeral=True)


# ------------------------------------------------------------ invite ---------
@mc_group.command(name="invite", description="Whitelist a friend, make their Tailscale link and DM them (owner only)")
@app_commands.describe(friend="The Discord member to invite", minecraft_name="Their Minecraft (Java) name")
async def mc_invite(inter: discord.Interaction, friend: discord.Member, minecraft_name: str):
    if not is_owner(inter):
        return await deny_owner(inter)
    await inter.response.defer(ephemeral=True, thinking=True)
    c = cfg()
    report = []
    r = await asyncio.to_thread(d.whitelist_change, c, "add", minecraft_name, who(inter))
    if not r.get("ok"):
        return await inter.followup.send("❌ " + r["error"], ephemeral=True)
    name = r.get("name", minecraft_name)
    report.append(("✅ " if not r.get("queued") else "🕒 ") + r["msg"])

    ids = d.read_json("k12_ids.json", {}) or {}
    link, link_err = None, None
    if TS_API_KEY:
        try:
            link = await asyncio.to_thread(d.tailscale_invite, TS_API_KEY, ids.get("tailscale_ip"), ids.get("host", ""))
            report.append("✅ Tailscale share link made (one use, PC only).")
        except Exception as e:
            link_err = str(e)
            report.append(f"⚠️ Tailscale link failed: {e}")
    else:
        report.append("⚠️ No Tailscale API key (sudo deck-tailscale-setup) - share the PC by hand.")

    join = d.join_address(c, {"k12": {"tailscale_ip": ids.get("tailscale_ip")}}) or "the PC's Tailscale address"
    owner = inter.user.display_name
    steps = ("1. Install **Tailscale** (free) - https://tailscale.com/download - and sign in with any account.\n"
             + (f"2. Open this link and accept: {link}\n" if link
                else f"2. {owner} will send you a Tailscale share link - open it and accept.\n")
             + f"3. Minecraft **Java Edition** → Multiplayer → Add Server → address `{join}`\n"
             f"\nYou're on the whitelist as **{name}**. The server may be asleep - "
             "ask in the server channel or someone with access can `/mc start` it.")
    emb = discord.Embed(title=f"You're invited to {owner}'s Minecraft server 🎮", description=steps,
                        color=COLORS["good"])
    try:
        await friend.send(embed=emb)
        report.append(f"✅ Sent {friend.display_name} the steps by DM.")
        await inter.followup.send("\n".join(report), ephemeral=True)
    except discord.HTTPException:
        report.append(f"⚠️ Couldn't DM {friend.display_name} (their DMs are closed). Send them this:")
        await inter.followup.send("\n".join(report), embed=emb, ephemeral=True)
    if link_err is None:
        d.add_event(f"{name} invited (by {who(inter)}).", "good", "whitelist")


# ------------------------------------------------------------ backup / check / pack
@mc_group.command(name="backup", description="Back the world up to the Pi now")
async def mc_backup(inter: discord.Interaction):
    if not is_admin(inter):
        return await deny(inter)
    if d.current_action():
        return await inter.response.send_message("Busy with something else - try again in a minute.", ephemeral=True)
    await inter.response.send_message("Backing up… I'll post here when it's saved on the Pi.")
    asyncio.create_task(run_action(d.backup_now, cfg(), who(inter)))


def check_embed(res):
    icon = {True: "✅", False: "❌", None: "➖"}
    lines = []
    for r in res["results"]:
        line = f"{icon[r['ok']]} **{r['name']}** - {r['detail']}"
        if r["ok"] is False and r.get("fix"):
            line += f"\n   ↳ {r['fix']}"
        lines.append(line)
    return discord.Embed(title="Deck check: " + ("all good" if res["ok"] else "needs attention"),
                         description="\n".join(lines), color=COLORS["good" if res["ok"] else "warn"])


@deck_group.command(name="check", description="Self-test the deck (full: also wakes and checks the PC)")
@app_commands.describe(full="Also wake the PC and check Docker, temperatures and the EULA")
async def deck_check(inter: discord.Interaction, full: bool = False):
    if not is_admin(inter):
        return await deny(inter)
    await inter.response.defer(thinking=True)
    res = await asyncio.to_thread(d.self_test, cfg(), full)
    await inter.followup.send(embed=check_embed(res))


class PackConfirm(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=60)
        self.owner_id = owner_id

    async def interaction_check(self, inter):
        return inter.user.id == self.owner_id

    @discord.ui.button(label="Pack up now", style=discord.ButtonStyle.danger)
    async def go(self, inter: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await inter.response.edit_message(
            content="📦 Packing up: warn players → save + stop → back up → cool down → PC off. "
                    "Progress posts here. Then tick the checklist and shut the Pi down on the Deck Panel.",
            view=None)
        asyncio.create_task(run_action(d.pack_up, cfg(), who(inter)))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, inter: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await inter.response.edit_message(content="Cancelled.", view=None)


@deck_group.command(name="pack", description="Get the deck ready to close and carry (owner only)")
async def deck_pack(inter: discord.Interaction):
    if not is_owner(inter):
        return await deny_owner(inter)
    if d.current_action():
        return await inter.response.send_message("Busy with something else - try again in a minute.", ephemeral=True)
    s = d.read_json("state.json", {}) or {}
    mc = s.get("mc") or {}
    warn = f"\n⚠️ {mc.get('players')} player(s) online - they get a warning first." if mc.get("players") else ""
    await inter.response.send_message("This stops Minecraft, backs up the world and shuts the PC down." + warn,
                                      view=PackConfirm(inter.user.id), ephemeral=True)


# ------------------------------------------------------------ modpacks -------
mp_group = app_commands.Group(name="modpack", description="CurseForge modpacks on the server", parent=mc_group)


class Confirm(discord.ui.View):
    """Yes / Cancel buttons only the person who asked can press."""
    def __init__(self, owner_id, label, on_yes, done_text):
        super().__init__(timeout=120)
        self.owner_id, self.on_yes, self.done_text = owner_id, on_yes, done_text
        self.go.label = label

    async def interaction_check(self, inter):
        return inter.user.id == self.owner_id

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger)
    async def go(self, inter: discord.Interaction, button: discord.ui.Button):
        self.stop()
        if d.current_action():
            return await inter.response.edit_message(content="Busy with something else - try again in a minute.", view=None, embed=None)
        await inter.response.edit_message(content=self.done_text, view=None)
        asyncio.create_task(run_action(self.on_yes, who(inter)))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, inter: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await inter.response.edit_message(content="Cancelled.", view=None, embed=None)


def players_warning():
    mc = (d.read_json("state.json", {}) or {}).get("mc") or {}
    return f"\n⚠️ {mc.get('players')} player(s) online - they get a 1-minute warning." if mc.get("players") else ""


def preview_embed(p):
    e = discord.Embed(title=p["name"], url=p["url"], description=p.get("summary") or None, color=COLORS["info"])
    e.add_field(name="Minecraft", value=p.get("mc") or "?")
    e.add_field(name="Mod loader", value=p.get("loader") or "?")
    e.add_field(name="Version", value=(p.get("file_name") or "?") + (" (from your link)" if p.get("pinned") else " (newest)"),
                inline=False)
    if p.get("logo"):
        e.set_thumbnail(url=p["logo"])
    if p.get("warning"):
        e.add_field(name="⚠️", value=p["warning"], inline=False)
    return e


@mp_group.command(name="install", description="Install a CurseForge modpack and switch the server to it (owner only)")
@app_commands.describe(link="The pack's CurseForge page link (or its short name)")
async def mp_install(inter: discord.Interaction, link: str):
    if not is_owner(inter):
        return await deny_owner(inter)
    await inter.response.defer(ephemeral=True, thinking=True)
    try:
        p = await asyncio.to_thread(d.modpack_preview, link)
    except (ValueError, RuntimeError) as e:
        return await inter.followup.send("❌ " + str(e), ephemeral=True)
    if not p.get("can_install"):
        return await inter.followup.send("❌ This pack can't be installed.", embed=preview_embed(p), ephemeral=True)
    c = cfg()
    view = Confirm(inter.user.id, "Install + switch",
                   lambda w: d.modpack_change(c, p["slug"], w, meta=p),
                   f"📦 Installing **{p['name']}**: save + back up the current world → switch → start → download mods. "
                   "Progress posts in the deck channel (the first start can take 10+ minutes).")
    await inter.followup.send("Install this and switch the server to it? The current world is saved and backed up first."
                              + players_warning(), embed=preview_embed(p), view=view, ephemeral=True)


def _known_packs():
    lst = d.read_json("modpack_list.json", {}) or {}
    meta = d.read_json("modpacks.json", {}) or {}
    return [(s, (meta.get(s) or {}).get("name") or s) for s in
            [p.get("slug", "") for p in lst.get("packs") or []] if d.SLUG_RE.match(s)]


async def pack_names(inter: discord.Interaction, current: str):
    cur = current.lower()
    return [app_commands.Choice(name=n[:100], value=s) for s, n in _known_packs()
            if cur in s or cur in n.lower()][:25]


@mp_group.command(name="switch", description="Switch the server to an installed modpack (owner only)")
@app_commands.describe(name="The pack (start typing)")
@app_commands.autocomplete(name=pack_names)
async def mp_switch(inter: discord.Interaction, name: str):
    if not is_owner(inter):
        return await deny_owner(inter)
    slug = name.strip().lower()
    known = dict(_known_packs())
    if not d.SLUG_RE.match(slug) or slug not in known:
        return await inter.response.send_message("❌ No installed pack by that name. See `/mc modpack list`.", ephemeral=True)
    c = cfg()
    await inter.response.send_message(f"Switch the server to **{known[slug]}**? The current world is saved and backed up first."
                                      + players_warning(),
                                      view=Confirm(inter.user.id, "Switch", lambda w: d.modpack_change(c, slug, w),
                                                   f"🔁 Switching to **{known[slug]}**… progress posts in the deck channel."),
                                      ephemeral=True)


@mp_group.command(name="vanilla", description="Switch the server back to plain Minecraft (owner only)")
async def mp_vanilla(inter: discord.Interaction):
    if not is_owner(inter):
        return await deny_owner(inter)
    c = cfg()
    await inter.response.send_message("Switch back to vanilla Minecraft? The modpack's world is saved and kept." + players_warning(),
                                      view=Confirm(inter.user.id, "Switch to vanilla", lambda w: d.modpack_change(c, "", w),
                                                   "🔁 Switching to vanilla… progress posts in the deck channel."),
                                      ephemeral=True)


@mp_group.command(name="list", description="Installed modpacks and which one is on")
async def mp_list(inter: discord.Interaction):
    await inter.response.defer(thinking=True)
    r = await asyncio.to_thread(d.modpack_list, cfg())
    if not r.get("ok"):
        return await inter.followup.send("❌ " + r.get("error", "couldn't read the list"))
    lines = [("🟢 " if not r["active"] else "⚪ ") + "**Vanilla** - plain Minecraft"]
    for p in r["packs"]:
        sub = " · ".join(x for x in [p["mc"] and f"Minecraft {p['mc']}", p["loader"], p["file_name"]] if x)
        lines.append(("🟢 " if p["slug"] == r["active"] else "⚪ ") + f"**{p['name']}**" + (f" - {sub}" if sub else ""))
        if p.get("removed"):
            lines.append("   ↳ left out on the server (players keep them): " + ", ".join(x["name"] for x in p["removed"]))
        pr = d.preset_get(p["slug"])
        if pr:
            lines.append(f"   ⌨️ keybind preset: {pr['name']} ({len(pr['keys'])} keys)")
    if not r.get("live"):
        lines.append("*(PC is off - this is the last list the Pi saw)*")
    e = discord.Embed(title="Modpacks", description="\n".join(lines), color=COLORS["info"])
    if r["active"]:
        meta = (d.read_json("modpacks.json", {}) or {}).get(r["active"])
        if meta:
            e.add_field(name="How to join", value=d.how_to_join(cfg(), meta)[:1024], inline=False)
    await inter.followup.send(embed=e)


if __name__ == "__main__":
    if not TOKEN:
        print("No DISCORD_TOKEN. Run: sudo deck-discord-setup", file=sys.stderr)
        sys.exit(1)
    bot.run(TOKEN)
