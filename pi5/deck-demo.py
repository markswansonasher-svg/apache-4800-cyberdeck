#!/usr/bin/env python3
"""
deck-demo - try the Deck Panel without any hardware.

Runs the REAL Deck Panel and the REAL watchdog against a pretend PC,
Minecraft server and Wi-Fi. Every button works: Start wakes the pretend PC,
Minecraft "loads", players join, temperatures move, travel mode switches.

    Windows:  double-click  try-the-panel.cmd  (in the cyberdeck folder)
    Any PC:   python deck-demo.py            then open http://127.0.0.1:8090/demo
              python deck-demo.py --lan      also allow phones on your Wi-Fi

The /demo page has the panel at phone size, a control box to cause trouble
(overheating, players, hotel login page...) and a preview of what the
Discord bot would post. Time runs about 10x faster than real life.

Nothing here touches the real deck: no packets, no SSH, no Wi-Fi changes.
"""
import json
import os
import random
import sys
import tempfile
import threading
import time
import types

# fcntl is Linux-only; on Windows a simple stand-in is enough for one process
if os.name == "nt":
    _held = set()
    fc = types.ModuleType("fcntl")
    fc.LOCK_EX, fc.LOCK_NB, fc.LOCK_UN = 2, 4, 8

    def _flock(f, op):
        if op & fc.LOCK_UN:
            _held.discard(f.name)
        elif f.name in _held:
            raise BlockingIOError()
        else:
            _held.add(f.name)
    fc.flock = _flock
    sys.modules["fcntl"] = fc

PORT = 8090
STATE = tempfile.mkdtemp(prefix="deck-demo-")
os.environ["DECK_STATE"] = STATE
os.environ["DECK_CONFIG"] = os.path.join(STATE, "config.json")
LAN = "--lan" in sys.argv
json.dump({
    "k12_mac": "aa:bb:cc:dd:ee:ff", "k12_user": "demo", "listen_port": PORT,
    "allowed_networks": (["0.0.0.0/0", "::/0"] if LAN else ["127.0.0.0/8", "::1/128"]),
    # sped up ~10x so you don't wait 20 minutes for things to happen
    "idle_stop_minutes": 1, "wake_timeout_s": 25,
    "k12_cpu_warn_s": 12, "k12_cpu_hot_s": 6, "k12_crit_after_s": 15,
    "pack_warn_s": 12, "pack_cool_max_s": 40, "backup_min_free_mb": 1,
}, open(os.environ["DECK_CONFIG"], "w"))
os.environ["DECK_BOOT_CHECK_DELAY"] = "4"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import decklib as d          # noqa: E402
import deckwatch as w        # noqa: E402
import deckpanel as p        # noqa: E402
import importlib.machinery   # noqa: E402
import importlib.util        # noqa: E402
# deck-net (the Pi's root helper) has no .py ending: load it by path, for its settings rules
_dn = importlib.util.spec_from_loader("decknet", importlib.machinery.SourceFileLoader("decknet", os.path.join(HERE, "deck-net")))
DN = importlib.util.module_from_spec(_dn)
_dn.loader.exec_module(DN)

# Kinds the Discord bot posts (same list as deckbot.POST_KINDS)
try:
    from deckbot import POST_KINDS
except Exception:
    POST_KINDS = {"mc", "mc_up", "mc_down", "join", "leave", "idle", "temp", "k12",
                  "whitelist", "backup", "pack", "check"}


# ------------------------------------------------------------ the pretend world
class World:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        with getattr(self, "lock", threading.RLock()):
            self.k12 = False          # powered on?
            self.mc = "off"           # off | loading | up
            self.players = []
            self.heat = 0.0           # extra degrees from the "make it hot" buttons
            self.pi_heat = 0.0
            self.lhm = True           # LibreHardwareMonitor answering?
            self.wakes = True         # does Wake-on-LAN work?
            self.eula = True
            self.wifi = {"ssid": "Home WiFi", "signal": 82, "ip": "192.168.1.40"}
            self.saved = {"Home WiFi"}
            self.portal = False
            self.travel = False
            self.k12_travel = False
            self.whitelist = ["Steve", "Alex"]
            self.zips = {}            # backups "on the PC": name -> bytes
            self.cable = True         # Pi's case cable plugged in?
            self.log = []             # pretend Minecraft server log
            self.time = "day"
            self.packs = {}           # installed modpacks on the pretend PC: slug -> settings
            self.active = ""          # "" = vanilla
            self.manual_done = False  # demo button: the hand-downloaded mods are in place
            self.cf_key = True
            self.env = {"MC_DIFFICULTY": "normal", "MC_MOTD": "Apache 4800 deck", "MC_MAX_PLAYERS": "10",
                        "MC_MEMORY": "6G", "MC_VERSION": "LATEST", "MC_TYPE": "VANILLA"}
            self.props = {"": {"gamemode": "survival", "view-distance": "10", "simulation-distance": "10", "pvp": "true"}}

    def later(self, sec, fn):
        t = threading.Timer(sec, fn)
        t.daemon = True
        t.start()


W = World()


def mclog(text, level="INFO"):
    W.log.append(f"[{time.strftime('%H:%M:%S')}] [Server thread/{level}]: {text}")
    W.log = W.log[-200:]


def mc_up():
    if W.mc == "loading":
        W.mc = "up"
        mclog('Done (7.412s)! For help, type "help"')


NAMES =["Steve", "Alex", "Notch_fan", "CreeperBane", "Lumi", "Ozzy"]


def fake_k12_up(cfg):
    return W.k12


def fake_temps(cfg, timeout_s=4):
    if not (W.k12 and W.lhm):
        return None
    load = 14 if W.mc == "up" else 22 if W.mc == "loading" else 0
    cpu = 52 + load + 2 * len(W.players) + W.heat + random.uniform(-1.5, 1.5)
    return {"cpu": round(cpu, 1), "gpu": round(cpu - 9, 1), "drive": round(40 + load / 3, 1)}


def fake_pi_temp():
    return round(51 + W.pi_heat + random.uniform(-0.8, 0.8), 1)


def fake_mc_status(host, port=25565, timeout_s=3):
    if W.k12 and W.mc == "up":
        ver = (FAKE_PACKS.get(W.active) or {}).get("mc", "1.21.4") if W.active else "1.21.4"
        return {"online": True, "players": len(W.players), "max": 10, "names": sorted(W.players),
                "version": ver, "motd": "Apache 4800 deck"}
    return {"online": False, "players": 0, "max": 0, "names": [], "version": "", "motd": ""}


def fake_wake(mac, broadcast, repeats=3):
    if W.wakes and not W.k12:
        W.later(6, lambda: setattr(W, "k12", True))


def fake_agent(cfg, *args, timeout_s=60):
    time.sleep(0.4)                         # feels like a network call
    if not W.k12:
        return {"ok": False, "error": "ssh: connect to host 10.48.0.10 port 22: No route to host"}
    cmd = args[0]
    if cmd == "ping":
        return {"ok": True, "msg": "pong"}
    if cmd == "info":
        return {"ok": True, "tailscale_ip": "100.101.102.103", "docker": True, "host": "PC-DEMO",
                "mc": "running" if W.mc == "up" else "exited", "travel": W.k12_travel, "eula": W.eula,
                "pack": W.active}
    if cmd == "modpack":
        return fake_modpack(args[1:])
    if cmd == "whitelist":
        sub = args[1]
        if sub == "list":
            return {"ok": True, "live": W.mc == "up", "names": list(W.whitelist)}
        name = args[2]
        W.whitelist = [n for n in W.whitelist if n.lower() != name.lower()]
        if sub == "add":
            W.whitelist.append(name)
        elif name in W.players:
            W.players.remove(name)          # removed players get kicked
        return {"ok": True, "msg": "done"}
    if cmd == "backup":
        import hashlib
        import io
        import zipfile
        slug = args[1] if len(args) > 1 else W.active
        if slug and slug not in W.packs:
            return {"ok": False, "error": f"no modpack called '{slug}'"}
        if slug and not W.packs[slug].get("world"):
            return {"ok": False, "code": "noworld", "error": "No world yet"}
        time.sleep(2)                       # zipping
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("world/level.dat", "pretend world " + time.ctime())
            z.writestr("world/region/r.0.0.mca", os.urandom(4096))
        data = buf.getvalue()
        name = (f"pack-{slug}-" if slug else "minecraft-") + time.strftime("%Y%m%d-%H%M%S.zip")
        W.zips[name] = data
        return {"ok": True, "file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if cmd == "start":
        if not W.eula:
            return {"ok": False, "error": r"Minecraft EULA not accepted yet: set MC_EULA=TRUE in C:\deck\game-servers\.env"}
        if W.active and not W.cf_key:
            return {"ok": False, "error": "No CurseForge key on the PC yet - run setup-curseforge.cmd there (README > Modpacks)."}
        if W.mc == "off" and W.active:
            start_pack(W.active)
        elif W.mc == "off":
            W.mc = "loading"
            mclog("Starting minecraft server version 1.21.4")
            mclog("Preparing level \"world\"")
            mclog("Preparing spawn area: 0%", "INFO")
            mclog("Can't keep up! Is the server overloaded? Running 2041ms behind", "WARN")
            W.later(8, mc_up)
        return {"ok": True, "msg": "Minecraft container started", "pack": W.active}
    if cmd in ("stop", "off"):
        time.sleep(1.5)                     # saving the world
        if W.mc != "off":
            mclog("Stopping the server")
            mclog("Saving worlds")
        W.mc, W.players = "off", []
        if cmd == "off":
            W.later(4 if args[1:] == ("now",) else 6, lambda: setattr(W, "k12", False))
            return {"ok": True, "msg": "Minecraft stopped, world saved. PC shuts down in 1 minute."}
        return {"ok": True, "msg": "Minecraft stopped, world saved."}
    if cmd == "say":
        mclog(f"[Server] [Deck] {' '.join(args[1:])}")
        return {"ok": True, "msg": "sent"}
    if cmd == "logs":
        return {"ok": True, "running": W.mc != "off", "lines": W.log[-80:]}
    if cmd == "cmd":
        # what the real agent allows, with a few answers that look like Minecraft's
        text = " ".join(args[1:])
        if not d.CMD_RE.match(text) or text.split()[0].lower() in d.CMD_BLOCKED:
            return {"ok": False, "error": "refused by the agent"}
        if W.mc != "up":
            return {"ok": False, "error": "Minecraft isn't running."}
        w0 = text.split()[0].lower()
        if w0 == "list":
            out = f"There are {len(W.players)} of a max of 10 players online: {', '.join(sorted(W.players))}"
        elif w0 == "time":
            W.time = text.split()[-1]
            out = "Set the time to " + ("1000" if W.time == "day" else "13000" if W.time == "night" else W.time)
        elif w0 == "weather":
            out = "Set the weather to " + text.split()[-1]
        elif w0 == "save-all":
            out = "Saved the game"
            mclog("Saved the game")
        elif w0 == "say":
            out = ""
            mclog(f"[Rcon] {text[4:]}")
        else:
            out = f"Unknown or incomplete command, see below for error\n{text}<--[HERE]" if w0 == "fly" else "done"
        return {"ok": True, "msg": out or "done"}
    if cmd == "travel":
        W.k12_travel = args[1] == "on"
        return {"ok": True}
    if cmd == "settings":
        props = W.props.setdefault(W.active, {})
        if args[1] == "get":
            own = ["MC_MEMORY"] if W.active else []
            return {"ok": True, "env": dict(W.env), "props": dict(props), "pack": W.active, "pack_sets": own,
                    "props_exist": bool(props), "running": W.mc == "up"}
        import base64
        kind, key, b64 = args[2], args[3], args[4]
        value = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)).decode()
        if any(c in value for c in "$\"'`#") or key not in (d.SERVER_ENV_KEYS if kind == "env" else d.SERVER_PROP_KEYS):
            return {"ok": False, "error": f"That value isn't allowed for {key}."}
        (W.env if kind == "env" else props)[key] = value
        return {"ok": True, "msg": f"{key} = {value}. It takes effect when Minecraft restarts.", "key": key, "value": value}
    if cmd == "perf":
        if W.mc != "up":
            return {"ok": True, "running": False}
        load = len(W.players) * 2.5 + W.heat / 4
        mspt = round(4 + load + random.uniform(-0.8, 0.8), 1)
        return {"ok": True, "running": True, "pack": W.active, "cpu": round(35 + 12 * len(W.players) + random.uniform(-4, 4), 1),
                "mem_used": f"{2.1 + 0.2 * len(W.players):.2f}GiB", "mem_limit": "8GiB", "mem_pct": 30.0,
                "mspt": mspt, "tps": round(min(20, 1000 / max(mspt, 50)), 1), "method": "tick query"}
    return {"ok": False, "error": f"unknown command '{cmd}'"}


# ------------------------------------------------------------ pretend CurseForge + modpacks
FAKE_PACKS = {
    "cozy-pack": {"name": "Cozy Pack", "mc": "1.20.1", "loader": "Fabric", "mods": 84, "summary": "A light, cozy Fabric pack."},
    "tech-town": {"name": "Tech Town", "mc": "1.21.1", "loader": "NeoForge", "mods": 162, "summary": "Machines, trains and factories."},
    "needs-manual-mods": {"name": "Manual Mods Demo", "mc": "1.20.1", "loader": "Forge", "mods": 120,
                          "summary": "One of its mods can't be downloaded automatically.", "manual": True},
    "quilt-garden": {"name": "Quilt Garden", "mc": "1.20.1", "loader": "Quilt", "mods": 60, "summary": "A Quilt pack."},
    # ships two mods that only work on players' computers: the auto-fix takes them out one by one
    "crashy-pack": {"name": "Crashy Pack", "mc": "1.19.2", "loader": "Forge", "mods": 140,
                    "summary": "Has client-only mods that crash a server.",
                    "client_mods": [(581495, "Oculus", "oculus", "oculus-mc1.19.2-1.2.8a.jar"),
                                    (263420, "Xaero's Minimap", "xaerominimap", "Xaeros_Minimap_23.6.2_Forge_1.19.2.jar")]},
}


def fake_preview(link):
    time.sleep(0.8)
    slug, fid = d.parse_modpack_link(link)
    p = FAKE_PACKS.get(slug) or {"name": slug.replace("-", " ").title(), "mc": "1.20.1", "loader": "Fabric",
                                 "mods": 100, "summary": "(demo) Any pack link works here."}
    fid = fid or 5000 + sum(map(ord, slug))
    warn = "This is a Quilt pack. The server can't install Quilt packs - pick a Forge, NeoForge or Fabric pack." \
        if p["loader"] == "Quilt" else ""
    return {"ok": True, "slug": slug, "id": 1, "name": p["name"], "summary": p["summary"], "logo": "",
            "url": f"https://www.curseforge.com/minecraft/modpacks/{slug}", "file_id": fid,
            "file_name": f"{p['name']} 2.{fid % 10}", "pinned": "/files/" in link, "mc": p["mc"], "loader": p["loader"],
            "java": d.java_tag(p["mc"]), "downloads": 1234567, "can_install": p["loader"] != "Quilt", "warning": warn}


def fake_modpack(a):
    sub = a[0] if a else ""
    if sub == "list":
        return {"ok": True, "active": W.active, "key": W.cf_key,
                "packs": [{"slug": s, "has_world": bool(v.get("world")), "excluded": "", "java": v["java"]}
                          for s, v in W.packs.items()]}
    if sub in ("apply", "vanilla", "delete") and W.mc != "off":
        return {"ok": False, "error": "Stop Minecraft first."}
    if sub == "apply":
        slug, fid, java = a[1], a[2], a[3]
        W.packs[slug] = dict(W.packs.get(slug, {}), java=java, file_id=fid)
        W.active = slug
        return {"ok": True, "msg": f"Modpack {slug} is now the active world.", "active": slug}
    if sub == "vanilla":
        W.active = ""
        return {"ok": True, "msg": "Back to vanilla Minecraft.", "active": ""}
    if sub == "delete":
        slug = a[1]
        if slug == W.active:
            return {"ok": False, "error": "That pack is on right now - switch to another one (or vanilla) first."}
        W.packs.pop(slug, None)
        return {"ok": True, "msg": f"Deleted {slug} (its backups are kept)."}
    if sub == "mods":
        cm = (FAKE_PACKS.get(a[1]) or {}).get("client_mods") or []
        return {"ok": True, "mods": [{"file": f, "ids": [mid], "mixins": [mid + ".mixins.json"]} for _, _, mid, f in cm]
                + [{"file": "jei-1.19.2-forge-11.6.jar", "ids": ["jei"], "mixins": []}]}
    if sub == "diagnose":
        return {"ok": True, "lines": W.log[-400:], "crash": [], "running": W.mc != "off"}
    if sub in ("exclude", "include"):
        ex = W.packs.setdefault(a[1], {}).setdefault("excluded", set())
        (ex.add if sub == "exclude" else ex.discard)(int(a[2]))
        return {"ok": True}
    if sub == "synced":
        return {"ok": True}
    if sub == "upload":
        W.manual_done = True
        return {"ok": True, "msg": "Saved (demo)."}
    if sub == "preset":
        W.packs.setdefault(a[1], {})["preset"] = a[2] != "clear"
        return {"ok": True, "msg": "Keybind preset saved (demo)."}
    return {"ok": False, "error": "use: modpack list|apply|vanilla|delete|..."}


def fake_file_map(meta):
    cm = (FAKE_PACKS.get(meta.get("slug")) or {}).get("client_mods") or []
    return {f: {"id": pid, "name": name, "slug": mid} for pid, name, mid, f in cm}


def start_pack(slug):
    """Pretend first start of a pack: download mods, maybe hit a manual download, then load."""
    p = FAKE_PACKS.get(slug) or {"mods": 100, "mc": "1.20.1", "loader": "Fabric"}
    W.mc = "loading"
    first = not W.packs[slug].get("world")
    mclog(f"[init] Resolving type given AUTO_CURSEFORGE")
    mclog(f"[mc-image-helper] Installing modpack {slug}")
    if p.get("manual") and not W.manual_done:
        def blocked():
            mclog("[mc-image-helper] WARN  : The authors of the mod 'Fancy Furniture' have disallowed project distribution. "
                  "Manually download the file 'fancyfurniture-1.20.1-3.2.jar' from "
                  "https://www.curseforge.com/minecraft/mc-mods/fancy-furniture/download/4567890 "
                  "and supply via downloads repo or separately.", "WARN")
            mclog("Some mod authors disallow automated downloads.", "ERROR")
            W.mc = "off"
        W.later(4, blocked)
        return
    steps = max(3, p["mods"] // 30) if first else 1
    for i in range(steps):
        W.later(1 + i * 2, lambda i=i: mclog(f"[mc-image-helper] Downloading mods {min(p['mods'], (i + 1) * 30)}/{p['mods']}"))
    bad = [c for c in p.get("client_mods") or [] if c[0] not in W.packs[slug].get("excluded", set())]
    if bad:
        pid, name, mid, jar = bad[0]
        def crash():
            mclog(f"Forge mod loading, version 43.2.3, for MC {p['mc']}")
            mclog(f"Attempted to load class net/minecraft/client/gui/screens/Screen for invalid dist DEDICATED_SERVER", "ERROR")
            mclog(f"-- MOD {mid} --", "ERROR")
            mclog(f"Mod File: /data/mods/{jar}", "ERROR")
            mclog(f"Failure message: {name} ({mid}) has failed to load correctly", "ERROR")
            mclog("Mod Loading has failed", "FATAL")
            W.mc = "off"
        W.later(2 + steps * 2 + 2, crash)
        return
    def loaded():
        if W.mc == "loading":
            W.packs[slug]["world"] = True
            mclog(f"Starting minecraft server version {p['mc']}")
            mclog(f"Loading {p['mods']} mods")
            mc_up()
    W.later(2 + steps * 2 + 4, loaded)


NETWORKS = [("Home WiFi", 82, "WPA2"), ("Hotel Guest", 74, "open"), ("Starbucks", 51, "open"),
            ("NETGEAR-5G", 38, "WPA2"), ("<b>evil</b> name", 22, "WPA2")]


def fake_net(*args, payload=None, timeout_s=70):
    time.sleep(0.3)
    cmd = args[0]
    if cmd == "status":
        inet = "offline" if not W.wifi["ssid"] else "portal" if W.portal else "online"
        r = {"ok": True, "wifi": dict(W.wifi), "travel": W.travel, "internet": inet}
        if W.portal:
            r["portal_url"] = "http://login.hotel-wifi.example/?demo=1"
        return r
    if cmd == "scan":
        time.sleep(1.2)
        return {"ok": True, "networks": [
            {"ssid": s, "signal": sig, "security": sec, "in_use": s == W.wifi["ssid"], "saved": s in W.saved}
            for s, sig, sec in NETWORKS]}
    if cmd == "connect":
        ssid, pw = payload.get("ssid"), payload.get("password") or ""
        sec = next((x[2] for x in NETWORKS if x[0] == ssid), "WPA2")
        time.sleep(2)
        if sec != "open" and ssid not in W.saved and pw != "password123":
            return {"ok": False, "error": "Wrong password (demo: the password is password123)"}
        W.wifi = {"ssid": ssid, "signal": next((x[1] for x in NETWORKS if x[0] == ssid), 50), "ip": "172.16.4.22"}
        W.saved.add(ssid)
        W.portal = ssid in ("Hotel Guest", "Starbucks")
        return {"ok": True, "msg": f"Connected to {ssid}"}
    if cmd == "forget":
        W.saved.discard(payload.get("ssid"))
        return {"ok": True, "msg": f"Forgot {payload.get('ssid')}"}
    if cmd == "travel":
        W.travel = args[1] == "on"
        with open(os.path.join(STATE, "travel"), "w") as f:
            f.write(args[1])
        return {"ok": True, "travel": W.travel}
    if cmd == "poweroff":
        return {"ok": True, "msg": "(demo) The real Pi would shut down now."}
    if cmd == "reboot":
        return {"ok": True, "msg": "(demo) The real Pi would restart now."}
    if cmd in ("config-get", "config-set"):
        # the real deck-net's rules, on the demo's own config file
        with open(os.environ["DECK_CONFIG"]) as f:
            cfg = json.load(f)
        if cmd == "config-get":
            return DN.config_view(cfg)
        try:
            new = DN.config_apply(cfg, (payload or {}).get("changes"))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        with open(os.environ["DECK_CONFIG"], "w") as f:
            json.dump(new, f)
        return dict(DN.config_view(new), msg="Saved. The deck uses the new settings right away.")
    return {"ok": False, "error": "unknown"}


def fake_download(cfg, remote_file, dest, timeout_s=1800):
    time.sleep(1)
    if remote_file not in W.zips:
        raise RuntimeError("no such backup")
    with open(dest, "wb") as f:
        f.write(W.zips[remote_file])


def fake_mojang(name, timeout_s=6):
    time.sleep(0.5)
    if name.lower() == "notarealplayer":
        return None
    return {"name": name, "uuid": "00000000-0000-4000-8000-" + format(abs(hash(name)) % 16**12, "012x")}


d.k12_up, d.k12_temps, d.pi_temp, d.mc_status = fake_k12_up, fake_temps, fake_pi_temp, fake_mc_status
d.send_magic_packet, d.agent, d.net = fake_wake, fake_agent, fake_net
d.agent_download, d.mojang_lookup = fake_download, fake_mojang
d.tailscale_running = lambda: True
d.modpack_preview = fake_preview
d.cf_api_key = lambda: "demo-key"
d.pack_file_map = fake_file_map
d.agent_upload = lambda cfg, args, src, timeout_s=600: (time.sleep(1), fake_agent(cfg, *args))[1]
d.eth0_linked = lambda: W.cable

# watchdog: every 3 s instead of 30 s, and its "wait 20 s" pauses are capped at 2 s
w.PERIOD = 3
w.time = types.SimpleNamespace(time=time.time, strftime=time.strftime, sleep=lambda s: time.sleep(min(s, 2)))

# 24 h of made-up history so the graph has something to show
now = time.time()
d.write_json("history.json", [[int(now - 86400 + i * 60),
                               round(55 + 14 * max(0, __import__("math").sin(i / 120)) + random.uniform(-2, 2), 1),
                               round(50 + 3 * __import__("math").sin(i / 300) + random.uniform(-0.5, 0.5), 1)]
                              for i in range(1440)])


# ------------------------------------------------------------ scenario buttons
def act(name):
    if name == "reset":
        W.reset()
        for f in ("woke", "travel", "action.json", "pack.json", "modpack_job.json", "modpack_list.json", "modpacks.json"):
            d.remove(f)
        return "Everything back to normal (PC off, home Wi-Fi)."
    if name == "join":
        if W.mc != "up":
            return "Start Minecraft first."
        free = [n for n in NAMES if n not in W.players]
        if free:
            n = random.choice(free)
            W.players.append(n)
            mclog(f"{n} joined the game")
        return "A player joined."
    if name == "leave":
        if W.players:
            n = W.players.pop(random.randrange(len(W.players)))
            mclog(f"{n} left the game")
        return "A player left."
    if name == "everyone_leaves":
        W.players = []
        return "Everyone left - the empty server stops after 1 demo-minute."
    if name == "warm":
        W.heat = 15     # ~88 C with Minecraft up and a few players: over "warm" (85), under "hot" (92)
        return "PC running warm (~88 C) - warning in ~12 s."
    if name == "hot":
        W.heat = 46     # stays over 92 C even after Minecraft stops, so you see every stage
        return "PC overheating (~110 C, stays hot) - it saves + stops the server, then shuts the PC down."
    if name == "cool":
        W.heat = 0
        return "PC cooled down."
    if name == "pi_hot":
        W.pi_heat = 30
        return "Pi too hot (~81 C)."
    if name == "pi_cool":
        W.pi_heat = 0
        return "Pi cooled down."
    if name == "lhm_off":
        W.lhm = not W.lhm
        return "LibreHardwareMonitor " + ("back on." if W.lhm else "stopped - temperatures disappear.")
    if name == "no_wake":
        W.wakes = not W.wakes
        return "Wake-on-LAN " + ("works again." if W.wakes else "broken - Start will give up after 25 s.")
    if name == "no_eula":
        W.eula = not W.eula
        return "EULA " + ("accepted." if W.eula else "NOT accepted - Start will fail with a message.")
    if name == "power_cut":
        W.k12, W.mc, W.players = False, "off", []
        return "PC lost power."
    if name == "k12_on":
        W.k12 = True
        return "PC switched on by hand (Minecraft not started)."
    if name == "portal_done":
        W.portal = False
        return "Hotel login done - internet online."
    if name == "cable":
        W.cable = not W.cable
        return "Pi's case cable " + ("plugged back in." if W.cable else "unplugged - try Check the deck.")
    if name == "manual_done":
        W.manual_done = True
        return "Pretend: the hand-downloaded mod is in the downloads folder. Press Try again in Modpacks."
    if name == "wifi_lost":
        W.wifi = {"ssid": None, "signal": None, "ip": None}
        W.portal = False
        return "Pi lost Wi-Fi."
    return "unknown scenario"


DEMO_HTML = open(os.path.join(HERE, "deck-demo.html"), "rb").read() if os.path.exists(
    os.path.join(HERE, "deck-demo.html")) else b"deck-demo.html missing"

PHONE_HTML = """<!doctype html><meta charset="utf-8"><title>Deck Panel - phone</title>
<body style="margin:0;background:#111316"><iframe id="f" src="/" style="width:390px;height:844px;border:0;display:block"></iframe>
<script>
// /demo/phone?w=390&h=844&at=c-con : phone-sized panel, scrolled to a card
const q=new URLSearchParams(location.search), f=document.getElementById('f');
f.style.width=(q.get('w')||390)+'px'; f.style.height=(q.get('h')||844)+'px';
f.onload=()=>setTimeout(()=>{const id=q.get('at'); if(id) f.contentWindow.document.getElementById(id).scrollIntoView()},2500);
</script>"""

_get, _post = p.Handler.do_GET, p.Handler.do_POST


def do_GET(self):
    if self.path.startswith("/demo") and self._allowed():
        if self.path in ("/demo", "/demo/"):
            return self._send(200, DEMO_HTML, "text/html")
        if self.path.startswith("/demo/phone"):
            # the panel in a phone-sized frame (for screenshots: browser windows can't be that narrow)
            return self._send(200, PHONE_HTML, "text/html")
        if self.path == "/demo/feed":
            evs = [e for e in d.read_events(0, limit=60) if e.get("kind") in POST_KINDS
                   and "(asked by" not in e.get("text", "")]
            return self._send(200, {"events": evs[-25:], "world": {
                "k12": W.k12, "mc": W.mc, "players": W.players, "heat": W.heat, "lhm": W.lhm,
                "wakes": W.wakes, "eula": W.eula, "travel": W.travel, "k12_travel": W.k12_travel,
                "portal": W.portal, "wifi": W.wifi["ssid"], "whitelist": W.whitelist,
                "cable": W.cable, "backups": len(d.list_backups())}})
    return _get(self)


def do_POST(self):
    if self.path == "/demo/act" and self._allowed() and self.headers.get("X-Deck") == "1":
        return self._send(200, {"ok": True, "msg": act(self._body().get("name", ""))})
    return _post(self)


p.Handler.do_GET, p.Handler.do_POST = do_GET, do_POST

if __name__ == "__main__":
    threading.Thread(target=w.Watch().run, daemon=True).start()
    print("=" * 60)
    print(" Deck Panel DEMO - pretend hardware, nothing real is touched")
    print(f"   Open:  http://127.0.0.1:{PORT}/demo")
    if LAN:
        print(f"   Phone: http://<this PC's IP>:{PORT}/   (same Wi-Fi)")
    print("   Stop:  Ctrl+C or close this window")
    print("=" * 60, flush=True)
    # Windows lets two programs share a port when "reuse" is on - then two demos
    # would answer at random. Turn it off so a second copy fails clearly instead.
    import http.server
    http.server.ThreadingHTTPServer.allow_reuse_address = False
    try:
        p.serve(d.load_config())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"\nPort {PORT} is busy - the demo is probably already running in another window.\n({e})")
        sys.exit(1)
