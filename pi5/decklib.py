#!/usr/bin/env python3
"""
decklib - shared code for the cyberdeck's Pi services.

Used by deckpanel.py (web page), deckwatch.py (temperature + idle watchdog)
and deckbot.py (Discord). Standard library only.

Files:
  /etc/deck/config.json        settings (see config.example.json)
  /var/lib/deck/state.json     latest readings, written by deckwatch
  /var/lib/deck/history.json   24 h of temperatures, written by deckwatch
  /var/lib/deck/events.jsonl   things that happened; the bot posts these
  /var/lib/deck/.ssh/          key the Pi uses to talk to the PC agent
  /var/lib/deck/backups/       Minecraft world backups copied from the PC
  /var/lib/deck/pack.json      progress of "Pack up"
  /var/lib/deck/selftest.json  last deck check
"""
import base64
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

CONFIG_PATH = os.environ.get("DECK_CONFIG", "/etc/deck/config.json")
STATE_DIR = os.environ.get("DECK_STATE", "/var/lib/deck")
SSH_KEY = os.path.join(STATE_DIR, ".ssh", "id_ed25519")
KNOWN_HOSTS = os.path.join(STATE_DIR, ".ssh", "known_hosts")
DECK_NET = "/usr/local/sbin/deck-net"

DEFAULT_CONFIG = {
    "k12_mac": "",                  # set by deck-set-mac
    "k12_ip": "10.48.0.10",
    "k12_user": "",                 # Windows user the agent runs as; set by deck-link-k12
    "broadcast": "10.48.0.255",     # case-network broadcast -> leaves via eth0
    "listen_port": 8080,
    "allowed_networks": ["100.64.0.0/10", "fd7a:115c:a1e0::/48",
                         "127.0.0.0/8", "::1/128", "10.48.0.0/24"],
    "mc_port": 25565,
    "mc_join_address": "",          # blank = PC's Tailscale IP, found automatically
    "idle_stop_minutes": 20,        # 0 = never stop an empty server
    "wake_timeout_s": 300,
    "lhm_port": 8085,               # LibreHardwareMonitor web server on the PC
    # temperature limits (deg C) - see README "Temperature watchdog"
    "k12_cpu_warn": 85, "k12_cpu_warn_s": 120,
    "k12_cpu_hot": 92,  "k12_cpu_hot_s": 60,
    "k12_crit_after_s": 120,
    "pi_warn": 75, "pi_hot": 82,
    "kuma_push_url": "",            # optional Uptime Kuma "Push" monitor URL
    "discord_channel_id": "",
    "discord_admin_role": "deck-admin",
    "discord_guild_id": "",
    "discord_owner_ids": [],        # Discord user IDs allowed to add/remove players and pack up
    # backups of the Minecraft world, copied to the Pi
    "backup_keep": 10,
    "backup_min_free_mb": 1024,     # never fill the Pi's card past this
    "backup_on_stop_hours": 1,      # back up when the server stops, if the last backup is older
    # pack up
    "pack_warn_s": 60,              # warning to players before the server stops
    "pack_cool_c": 55,              # wait for the PC CPU to drop below this before shutdown
    "pack_cool_max_s": 300,
    "pack_checklist": ["Side-panel cover on", "Cables in the combs",
                       "Monitor clips seated", "Power cord unplugged and stored",
                       "Laptop cable unplugged (and travel-off.cmd run)"],
    # modpacks (README "Modpacks")
    "modpack_mem_gb": 10,           # memory the server may use with a modpack (Java gets 2 GB less)
    "docker_mem_gb": 20,            # what all of Docker may use (wslconfig.example); packs stay under it
    "modpack_start_timeout_s": 1800,  # first start downloads every mod - give it time
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    return cfg


# ------------------------------------------------------------ small files ---

def _path(name):
    return os.path.join(STATE_DIR, name)


def read_json(name, default=None):
    try:
        with open(_path(name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def write_json(name, data):
    tmp = _path(name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _path(name))


def remove(name):
    try:
        os.remove(_path(name))
    except FileNotFoundError:
        pass


_last_id = 0


def add_event(text, level="info", kind="info"):
    """Append one line to events.jsonl. level: info | good | warn | bad."""
    global _last_id
    _last_id = max(time.time_ns(), _last_id + 1)   # strictly increasing, even on a coarse clock
    ev = {"id": _last_id, "t": time.strftime("%Y-%m-%d %H:%M:%S"),
          "level": level, "kind": kind, "text": text}
    line = json.dumps(ev) + "\n"
    with open(_path("events.jsonl"), "a", encoding="utf-8") as f:
        f.write(line)
    return ev


def read_events(after_id=0, limit=50):
    out = []
    try:
        with open(_path("events.jsonl"), "r", encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("id", 0) > after_id:
                    out.append(ev)
    except FileNotFoundError:
        pass
    return out[-limit:]


def trim_events(keep=500, max_lines=2000):
    p = _path("events.jsonl")
    try:
        with open(p, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    if len(lines) > max_lines:
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            f.writelines(lines[-keep:])
        os.replace(p + ".tmp", p)


# --------------------------------------------------------------- network ---

def normalize_mac(mac):
    hexdigits = "".join(c for c in mac if c in "0123456789abcdefABCDEF")
    if len(hexdigits) != 12:
        raise ValueError(f"bad MAC address: {mac!r}")
    return hexdigits.lower()


def send_magic_packet(mac, broadcast, repeats=3):
    """6x 0xFF + 16x MAC to the case network's directed broadcast (so it leaves eth0)."""
    packet = b"\xff" * 6 + bytes.fromhex(normalize_mac(mac)) * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(repeats):
            for port in (9, 7):
                s.sendto(packet, (broadcast, port))
            time.sleep(0.1)


def ping(host, timeout_s=1):
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", str(timeout_s), host],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=timeout_s + 2)
        return r.returncode == 0
    except Exception:
        return False


def tcp_open(host, port, timeout_s=1.5):
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def k12_up(cfg):
    ip = cfg["k12_ip"]
    return ping(ip) or any(tcp_open(ip, p, 0.7) for p in (445, 135, 22))


# ------------------------------------------------- Minecraft status ping ---

def _varint(n):
    n &= 0xFFFFFFFF
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("server closed the connection")
        buf += chunk
    return bytes(buf)


def _read_varint(sock):
    result = 0
    for i in range(5):
        b = _read_exact(sock, 1)[0]
        result |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return result
    raise ValueError("varint too long")


def _varint_from(buf, pos):
    result = 0
    for i in range(5):
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return result, pos
    raise ValueError("varint too long")


def _motd_text(desc):
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict):
        return (desc.get("text", "") + "".join(_motd_text(x) for x in desc.get("extra", []))).strip()
    if isinstance(desc, list):
        return "".join(_motd_text(x) for x in desc)
    return ""


def mc_status(host, port=25565, timeout_s=3):
    """Minecraft 'server list ping'. Works on any Java server; no mods or RCON.

    Returns {"online": bool, "players": n, "max": n, "names": [...], "version": str, "motd": str}
    """
    try:
        with socket.create_connection((host, port), timeout=timeout_s) as s:
            s.settimeout(timeout_s)
            hb = host.encode("utf-8")
            data = (_varint(0) + _varint(767) + _varint(len(hb)) + hb +
                    struct.pack(">H", port) + _varint(1))
            s.sendall(_varint(len(data)) + data + _varint(1) + _varint(0))
            length = _read_varint(s)
            payload = _read_exact(s, length)
        pid, pos = _varint_from(payload, 0)
        if pid != 0:
            raise ValueError("unexpected packet")
        slen, pos = _varint_from(payload, pos)
        info = json.loads(payload[pos:pos + slen].decode("utf-8"))
        players = info.get("players", {}) or {}
        names = sorted(p.get("name", "") for p in players.get("sample", []) or []
                       if p.get("name") and p.get("id") != "00000000-0000-0000-0000-000000000000")
        return {"online": True, "players": int(players.get("online", 0)),
                "max": int(players.get("max", 0)), "names": names,
                "version": (info.get("version") or {}).get("name", ""),
                "motd": _motd_text(info.get("description", ""))[:80]}
    except Exception:
        return {"online": False, "players": 0, "max": 0, "names": [], "version": "", "motd": ""}


# ------------------------------------------------- temperatures --------------

def pi_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


_NUM = re.compile(r"(-?\d+(?:[.,]\d+)?)")


def _celsius(value):
    if not isinstance(value, str) or "C" not in value:
        return None
    m = _NUM.search(value)
    return float(m.group(1).replace(",", ".")) if m else None


def parse_lhm(tree):
    """Pick CPU / GPU / drive temperatures out of LibreHardwareMonitor's data.json."""
    found = {"cpu": [], "gpu": [], "drive": []}
    preferred_cpu = ("core (tctl/tdie)", "cpu package", "package", "tctl/tdie", "core (tctl)", "core max")

    def kind_of(node):
        img = (node.get("ImageURL") or "").lower()
        sid = (node.get("SensorId") or "").lower()
        if "cpu" in img or sid.startswith(("/amdcpu", "/intelcpu")):
            return "cpu"
        if any(k in img for k in ("nvidia", "ati", "amd.png", "gpu", "intel.png")) or "gpu" in sid:
            return "gpu"
        if "hdd" in img or "nvme" in sid or "/hdd" in sid or "ssd" in img:
            return "drive"
        return None

    # drives and RAM also list their limits as "temperatures" (Warning 82 °C, Critical 84 °C): not readings
    limit = re.compile(r"limit|warning|critical|threshold|resolution|\bmax\b|\bmin\b")

    def walk(node, hw):
        hw = kind_of(node) or hw
        t = _celsius(node.get("Value"))
        label = (node.get("Text") or "").lower()
        if t is not None and hw and not limit.search(label) and (node.get("Type") in (None, "Temperature")):
            found[hw].append(((node.get("Text") or "").lower(), t))
        for child in node.get("Children", []) or []:
            walk(child, hw)

    walk(tree, None)
    out = {}
    cpu = found["cpu"]
    if cpu:
        pick = next((t for name, t in cpu if name in preferred_cpu), None)
        out["cpu"] = pick if pick is not None else max(t for _, t in cpu)
    if found["gpu"]:
        out["gpu"] = max(t for _, t in found["gpu"])
    if found["drive"]:
        out["drive"] = max(t for _, t in found["drive"])
    return out


def k12_temps(cfg, timeout_s=4):
    """Returns {"cpu": 61.2, "gpu": .., "drive": ..} or None if LibreHardwareMonitor isn't answering."""
    url = f"http://{cfg['k12_ip']}:{int(cfg['lhm_port'])}/data.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            temps = parse_lhm(json.load(r))
        return temps or None
    except Exception:
        return None


# ------------------------------------------------- PC agent (over SSH) -----

def agent(cfg, *args, timeout_s=60):
    """Run one allow-listed command on the PC's deck-agent. Returns a dict."""
    if not cfg.get("k12_user"):
        return {"ok": False, "error": "PC not linked yet - run: sudo deck-link-k12 <windows-user>"}
    cmd = ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
           "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
           "-o", "LogLevel=ERROR", f"{cfg['k12_user']}@{cfg['k12_ip']}", " ".join(args)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "PC agent timed out"}
    out = r.stdout.strip().splitlines()
    for line in reversed(out):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    err = (r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}")[-300:]
    return {"ok": False, "error": err}


def k12_agent_ready(cfg):
    return agent(cfg, "ping", timeout_s=12).get("ok", False)


# ------------------------------------------------- actions ---------------------

class Busy(Exception):
    pass


class _ActionLock:
    def __init__(self, what, who):
        self.what, self.who = what, who

    def __enter__(self):
        self.f = open(_path("action.lock"), "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.f.close()
            cur = read_json("action.json", {}) or {}
            raise Busy(f"busy: {cur.get('what', 'another action')} (started by {cur.get('by', '?')})")
        write_json("action.json", {"what": self.what, "by": self.who, "since": time.time()})
        return self

    def __exit__(self, *exc):
        remove("action.json")
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def current_action():
    a = read_json("action.json")
    if a and time.time() - a.get("since", 0) > 3600:   # stale (crashed mid-action)
        return None
    return a


def wake_k12(cfg, who="panel"):
    if not cfg.get("k12_mac"):
        raise RuntimeError("PC MAC not set (run: sudo deck-set-mac)")
    was_up = k12_up(cfg)
    send_magic_packet(cfg["k12_mac"], cfg["broadcast"])
    if not was_up:
        write_json("woke", {"by": who, "t": time.time()})
    return was_up


def start_minecraft(cfg, who="panel"):
    """Wake the PC if needed, wait for it, start the Minecraft container.
    Blocking (up to ~10 min). Call it from a thread. Progress goes to events."""
    with _ActionLock("start Minecraft", who):
        if mc_status(cfg["k12_ip"], int(cfg["mc_port"]))["online"]:
            add_event("Minecraft is already running.", "good", "mc")
            return True
        add_event(f"Starting Minecraft (asked by {who}).", "info", "mc")
        if not k12_agent_ready(cfg):
            if not k12_up(cfg):
                add_event("Waking the PC...", "info", "k12")
                wake_k12(cfg, who)
            deadline = time.time() + int(cfg["wake_timeout_s"])
            while time.time() < deadline:
                time.sleep(5)
                if k12_agent_ready(cfg):
                    break
            else:
                add_event("The PC didn't answer. Is it plugged in? Check Wake-on-LAN "
                          "and that Windows signs in automatically (README).", "bad", "k12")
                return False
        r = agent(cfg, "start", timeout_s=420)
        if r.get("ok"):
            add_event("Minecraft server process started; loading the world (1-3 min)...", "info", "mc")
            return True
        add_event(f"Couldn't start Minecraft: {r.get('error', 'unknown error')}", "bad", "mc")
        return False


def stop_minecraft(cfg, who="panel", power_off=False, backup=True):
    """Save + stop Minecraft; back the world up to the Pi if the last backup is old;
    then (power_off) shut the PC down."""
    what = "stop Minecraft + shut down the PC" if power_off else "stop Minecraft"
    with _ActionLock(what, who):
        add_event(f"{what[0].upper() + what[1:]} (asked by {who}).", "info", "mc")
        r = agent(cfg, "stop", timeout_s=180)
        if not r.get("ok"):
            add_event(f"Stop failed: {r.get('error', 'unknown error')}", "bad", "mc")
            return False
        add_event(r.get("msg") or "Minecraft stopped, world saved.", "good", "mc")
        if backup and backup_age_h() >= float(cfg.get("backup_on_stop_hours", 1)):
            _backup(cfg)
        if power_off:
            r = agent(cfg, "off", timeout_s=60)
            if r.get("ok"):
                add_event("PC shuts down in 1 minute.", "info", "k12")
                remove("woke")
            else:
                add_event(f"Couldn't shut the PC down: {r.get('error')}", "bad", "k12")
        return True


def restart_minecraft(cfg, who="panel"):
    """Save + stop, then start again (new server settings take effect). No backup in between."""
    if not stop_minecraft(cfg, who, backup=False):
        return False
    return start_minecraft(cfg, who)


# ------------------------------------------------- server settings + performance ------
# What the Deck app may change on the PC. The PC agent checks every value again
# (deck-agent.ps1 $EnvRules / $PropRules); these lists only say what to show.
#   env:  C:\deck\game-servers\.env - the same for every world (a modpack's own memory wins)
#   prop: server.properties of the world that's on - each modpack keeps its own
SERVER_ENV_KEYS = ["MC_DIFFICULTY", "MC_MOTD", "MC_MAX_PLAYERS", "MC_MEMORY", "MC_VERSION", "MC_TYPE"]
SERVER_PROP_KEYS = ["gamemode", "view-distance", "simulation-distance", "pvp", "hardcore", "allow-flight",
                    "spawn-protection", "enable-command-block", "force-gamemode", "spawn-monsters"]


def server_settings(cfg):
    if not (k12_up(cfg) and cfg.get("k12_user")):
        return {"ok": False, "error": "The PC is off - start it to see the server settings."}
    r = agent(cfg, "settings", "get", timeout_s=30)
    if not r.get("ok") and "unknown command" in str(r.get("error", "")):
        return {"ok": False, "error": "The PC's deck agent is too old - run setup-k12-agent.cmd on the PC again."}
    return r


def server_setting_set(cfg, kind, key, value, who="panel"):
    """kind 'env' or 'prop'. The value travels base64url-encoded (a MOTD has spaces)."""
    if kind not in ("env", "prop"):
        return {"ok": False, "error": "kind must be env or prop"}
    if key not in (SERVER_ENV_KEYS if kind == "env" else SERVER_PROP_KEYS):
        return {"ok": False, "error": f"'{key}' can't be changed from the app"}
    value = str(value if value is not None else "").strip()
    if not value or len(value) > 100:
        return {"ok": False, "error": "Type a value first."}
    if not (k12_up(cfg) and cfg.get("k12_user")):
        return {"ok": False, "error": "The PC is off - start it first."}
    b64 = base64.urlsafe_b64encode(value.encode("utf-8")).decode().rstrip("=")
    r = agent(cfg, "settings", "set", kind, key, b64, timeout_s=30)
    if r.get("ok"):
        add_event(f"Server setting {key} = {value} (by {who}). Takes effect when Minecraft restarts.",
                  "info", "settings")
    return r


def server_perf(cfg):
    """Ticks per second, time per tick, and the server container's CPU + memory."""
    if not (k12_up(cfg) and cfg.get("k12_user")):
        return {"ok": True, "running": False, "k12": False}
    r = agent(cfg, "perf", timeout_s=30)
    r["k12"] = True
    return r


def say_in_game(cfg, text):
    text = re.sub(r"[^A-Za-z0-9 .,!?:'()/+-]", "", text)[:200]
    return agent(cfg, "say", text, timeout_s=20)


# ------------------------------------------------- server console -------------
# Same rule as the PC agent: nothing a shell could misread (no quotes, braces, $, ;, |, &).
CMD_RE = re.compile(r"^[A-Za-z0-9 _.,:~@!?#/+=()*\[\]-]{1,200}$")
CMD_BLOCKED = {"stop": "Use the Stop button - it also backs the world up.",
               "whitelist": "Use Players allowed - it keeps the start-up list in step."}


def console_logs(cfg):
    """Last ~80 lines of the Minecraft server log (empty while the PC is off)."""
    if not k12_up(cfg):
        return {"ok": True, "running": False, "lines": [], "k12": False}
    r = agent(cfg, "logs", timeout_s=20)
    r.setdefault("lines", [])
    r["k12"] = True
    return r


def console_cmd(cfg, text, who="panel"):
    text = " ".join(str(text or "").split()).lstrip("/")
    if not text:
        return {"ok": False, "error": "Type a command, e.g. time set day"}
    if not CMD_RE.match(text):
        return {"ok": False, "error": "That command has characters the deck does not send (quotes, braces, ;, $, | ...)."}
    first = text.split()[0].lower()
    if first in CMD_BLOCKED:
        return {"ok": False, "error": CMD_BLOCKED[first]}
    if not k12_up(cfg):
        return {"ok": False, "error": "The PC is off - start Minecraft first."}
    r = agent(cfg, "cmd", text, timeout_s=30)
    if r.get("ok"):
        add_event(f"Console: /{text} (by {who})", "info", "console")
    return r


# ------------------------------------------------- deck-net (root helper) ---

def net(*args, payload=None, timeout_s=70):
    try:
        r = subprocess.run(["sudo", "-n", DECK_NET, *args], capture_output=True, text=True,
                           input=json.dumps(payload) if payload is not None else None,
                           timeout=timeout_s)
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"ok": False, "error": f"deck-net: {e}"}


def travel_on():
    """Travel mode as last set by deck-net (file is world-readable)."""
    try:
        with open(os.path.join(STATE_DIR, "travel")) as f:
            return f.read().strip() == "on"
    except FileNotFoundError:
        return False


def join_address(cfg, state=None):
    if cfg.get("mc_join_address"):
        return cfg["mc_join_address"]
    state = state if state is not None else (read_json("state.json", {}) or {})
    ts = (state.get("k12") or {}).get("tailscale_ip")
    port = int(cfg["mc_port"])
    if ts:
        return ts if port == 25565 else f"{ts}:{port}"
    return ""


# ================================================= whitelist (who may join) ===

NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def mojang_lookup(name, timeout_s=6):
    """Is this a real Minecraft (Java) account?  -> {"name": "ExactCase", "uuid": "dashed"}
    Returns None if no such account; raises OSError if Mojang can't be reached."""
    url = "https://api.mojang.com/users/profiles/minecraft/" + name
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            if r.status == 204:
                return None
            j = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (204, 404):
            return None
        raise OSError(f"Mojang answered {e.code}")
    u = j["id"]
    return {"name": j["name"], "uuid": f"{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:]}"}


def whitelist_cached():
    return read_json("whitelist.json", {"names": [], "t": 0}) or {"names": [], "t": 0}


def whitelist_list(cfg):
    """Ask the PC (server running: live list; stopped: whitelist.json). Falls back to the
    last list the Pi saw if the PC is off."""
    pending = read_json("whitelist_pending.json", []) or []
    if k12_up(cfg):
        r = agent(cfg, "whitelist", "list", timeout_s=30)
        if r.get("ok"):
            names = sorted(r.get("names") or [], key=str.lower)
            write_json("whitelist.json", {"names": names, "t": time.time()})
            return {"ok": True, "names": names, "live": True, "pending": pending}
    c = whitelist_cached()
    return {"ok": True, "names": c["names"], "live": False, "as_of": c["t"], "pending": pending}


def _cache_change(action, name):
    c = whitelist_cached()
    names = {n.lower(): n for n in c["names"]}
    if action == "add":
        names[name.lower()] = name
    else:
        names.pop(name.lower(), None)
    write_json("whitelist.json", {"names": sorted(names.values(), key=str.lower), "t": time.time()})


def whitelist_change(cfg, action, name, who="panel"):
    """Add or remove one player. Checks the name with Mojang first. If the PC is off,
    the change is queued and applied the next time the PC is up."""
    name = (name or "").strip()
    if action not in ("add", "remove"):
        return {"ok": False, "error": "action must be add or remove"}
    if not NAME_RE.match(name):
        return {"ok": False, "error": "Minecraft names are 3-16 letters, numbers or _"}
    uuid = ""
    if action == "add":
        try:
            prof = mojang_lookup(name)
        except OSError:
            prof = {"name": name, "uuid": ""}     # can't check right now; the server checks again
        if prof is None:
            return {"ok": False, "error": f"No Minecraft account is called '{name}'. Check the spelling."}
        name, uuid = prof["name"], prof["uuid"]
    verb = "can now join" if action == "add" else "removed from the whitelist"
    if k12_up(cfg) and k12_agent_ready(cfg):
        r = agent(cfg, "whitelist", action, name, *([uuid] if uuid else []), timeout_s=40)
        if r.get("ok"):
            _cache_change(action, name)
            add_event(f"{name} {verb} (by {who}).", "good", "whitelist")
            return {"ok": True, "msg": f"{name} {verb}.", "name": name}
        return {"ok": False, "error": r.get("error", "PC agent error")}
    pend = [p for p in (read_json("whitelist_pending.json", []) or []) if p["name"].lower() != name.lower()]
    pend.append({"action": action, "name": name, "uuid": uuid, "by": who, "t": time.time()})
    write_json("whitelist_pending.json", pend)
    when = "added" if action == "add" else "removed"
    add_event(f"PC is off - {name} will be {when} the next time it starts.", "info", "whitelist")
    return {"ok": True, "queued": True, "name": name,
            "msg": f"The PC is off, so {name} will be {when} the next time it starts."}


def whitelist_apply_pending(cfg):
    """Called by the watchdog when the PC agent answers."""
    pend = read_json("whitelist_pending.json", []) or []
    if not pend:
        return
    left = []
    for p in pend:
        r = agent(cfg, "whitelist", p["action"], p["name"], *([p["uuid"]] if p.get("uuid") else []),
                  timeout_s=40)
        verb = "can now join" if p["action"] == "add" else "removed from the whitelist"
        if r.get("ok"):
            _cache_change(p["action"], p["name"])
            add_event(f"{p['name']} {verb} (queued by {p['by']}).", "good", "whitelist")
        else:
            left.append(p)
            add_event(f"Couldn't {p['action']} {p['name']}: {r.get('error')}", "warn", "whitelist")
    write_json("whitelist_pending.json", left)


# ================================================= backups to the Pi ==========

BACKUP_DIR = os.path.join(STATE_DIR, "backups")
# vanilla: minecraft-<date>.zip   modpack: pack-<slug>-<date>.zip   (same rule as $BackupRe in deck-agent.ps1)
BACKUP_RE = re.compile(r"^(minecraft|pack-[a-z0-9][a-z0-9-]{0,79})-\d{8}-\d{4,6}\.zip$")


def backup_world(name):
    """'minecraft' or 'pack-<slug>': which world a backup file belongs to."""
    m = BACKUP_RE.match(name)
    return m.group(1) if m else None


def human_size(n):
    for unit, div in (("GB", 2**30), ("MB", 2**20), ("KB", 2**10)):
        if n >= div:
            return f"{n / div:.1f} {unit}" if unit == "GB" else f"{n / div:.0f} {unit}"
    return f"{n} bytes"


def list_backups():
    try:
        files = [f for f in os.listdir(BACKUP_DIR) if BACKUP_RE.match(f)]
    except FileNotFoundError:
        return []
    out = []
    for f in files:
        st = os.stat(os.path.join(BACKUP_DIR, f))
        out.append({"file": f, "bytes": st.st_size, "t": st.st_mtime, "world": backup_world(f)})
    out.sort(key=lambda b: b["t"], reverse=True)      # newest first, whichever world
    return out


def backup_age_h():
    b = list_backups()
    return (time.time() - b[0]["t"]) / 3600 if b else 1e9


def agent_download(cfg, remote_file, dest, timeout_s=1800):
    """Stream one backup zip from the PC agent into dest (raw bytes over SSH)."""
    cmd = ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
           "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
           "-o", "LogLevel=ERROR", f"{cfg['k12_user']}@{cfg['k12_ip']}", f"backup-get {remote_file}"]
    with open(dest, "wb") as out:
        r = subprocess.run(cmd, stdout=out, stderr=subprocess.PIPE, timeout=timeout_s)
    if r.returncode != 0:
        err = (r.stderr or b"").decode(errors="replace").strip()[-200:]
        raise RuntimeError(err or f"ssh exit {r.returncode}")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _backup(cfg, slug=None):
    """Make a zip on the PC, copy it to the Pi, check it, keep the newest N of that world.
    slug: back up that modpack's world instead of the active one (e.g. before deleting it).
    Caller holds the action lock. Returns {"ok":..., "file":..., "bytes":...}."""
    add_event(f"Backing up {'the ' + slug + ' world' if slug else 'the world'} to the Pi...", "info", "backup")
    r = agent(cfg, *(["backup", slug] if slug else ["backup"]), timeout_s=900)
    if not r.get("ok"):
        if r.get("code") == "noworld":      # nothing to back up yet - not a failure
            add_event("No Minecraft world yet - nothing to back up.", "info", "backup")
            return {"ok": False, "skip": True, "error": "no world yet"}
        add_event(f"Backup failed on the PC: {r.get('error')}", "bad", "backup")
        return {"ok": False, "error": r.get("error")}
    name, size, sha = r.get("file", ""), int(r.get("bytes") or 0), (r.get("sha256") or "").lower()
    if not BACKUP_RE.match(name) or (slug and backup_world(name) != f"pack-{slug}"):
        return {"ok": False, "error": f"odd backup name from the PC: {name!r}"}
    os.makedirs(BACKUP_DIR, exist_ok=True)
    free = shutil.disk_usage(BACKUP_DIR).free
    need = size + int(cfg.get("backup_min_free_mb", 1024)) * 2**20
    if free < need:
        msg = (f"Not enough space on the Pi for the backup ({size // 2**20} MB, "
               f"{free // 2**20} MB free). Lower backup_keep or use a bigger card.")
        add_event(msg, "bad", "backup")
        return {"ok": False, "error": msg}
    tmp = os.path.join(BACKUP_DIR, name + ".part")
    try:
        agent_download(cfg, name, tmp)
        if sha and _sha256(tmp) != sha:
            raise RuntimeError("the copy doesn't match the PC's file (checksum)")
        if not zipfile.is_zipfile(tmp):
            raise RuntimeError("the copy isn't a valid zip")
        os.replace(tmp, os.path.join(BACKUP_DIR, name))
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        add_event(f"Copying the backup to the Pi failed: {e}", "bad", "backup")
        return {"ok": False, "error": str(e)}
    # keep the newest N of THIS world; other worlds' backups are never pushed out
    same = [b for b in list_backups() if b["world"] == backup_world(name)]
    for old in same[int(cfg.get("backup_keep", 10)):]:
        os.remove(os.path.join(BACKUP_DIR, old["file"]))
    add_event(f"Backup saved on the Pi: {name} ({human_size(size)}).", "good", "backup")
    return {"ok": True, "file": name, "bytes": size}


def backup_now(cfg, who="panel"):
    with _ActionLock("back up the world", who):
        if not (k12_up(cfg) and k12_agent_ready(cfg)):
            add_event("Backup skipped: the PC is off.", "warn", "backup")
            return {"ok": False, "error": "PC is off"}
        return _backup(cfg)


# ================================================= pack up ====================

PACK_STEPS = [("players", "Warn players"), ("stop", "Save + stop Minecraft"),
              ("backup", "Back up the world to the Pi"), ("cool", "Let the PC cool down"),
              ("travel", "Travel mode off"), ("k12off", "Shut the PC down")]


def pack_state():
    return read_json("pack.json")


def pack_up(cfg, who="panel", pi_off=False):
    """Get the deck ready to close and carry. Progress goes to pack.json (the panel shows it).
    pi_off: also shut the Pi down at the end, if everything went well (asked from the PC's
    own screen, which goes dark when the PC shuts down)."""
    st = {"running": True, "started": time.time(), "by": who, "done": False, "ok": None, "pi_off": bool(pi_off),
          "steps": [{"key": k, "label": lbl, "status": "pending", "detail": ""} for k, lbl in PACK_STEPS],
          "checklist": list(cfg.get("pack_checklist") or [])}

    def mark(key, status, detail=""):
        for s in st["steps"]:
            if s["key"] == key:
                s["status"], s["detail"] = status, detail
        write_json("pack.json", st)

    with _ActionLock("pack up", who):
        write_json("pack.json", st)
        add_event(f"Packing up (asked by {who}).", "info", "pack")
        try:
            _pack_steps(cfg, mark)
        except Exception as e:
            # never leave the panel stuck on "Working..."
            cur = next((s for s in st["steps"] if s["status"] == "running"), None)
            if cur:
                mark(cur["key"], "fail", f"error: {e}")
            for s in st["steps"]:
                if s["status"] == "pending":
                    mark(s["key"], "skip", "not run (error above)")
            add_event(f"Pack up stopped by an error: {e}", "bad", "pack")
        st["running"], st["done"] = False, True
        st["ok"] = not any(s["status"] == "fail" for s in st["steps"])
        write_json("pack.json", st)
        if st["ok"]:
            stat = {s["key"]: s["status"] for s in st["steps"]}
            done = (["world backed up"] if stat.get("backup") == "ok" else []) + ["PC off"]
            if pi_off:
                add_event("Packed: " + ", ".join(done) + ". The Pi is shutting down too - unplug the deck "
                          "when the Pi's green light stops flashing (about 20 s).", "good", "pack")
            else:
                add_event("Packed: " + ", ".join(done) + ". Tick the checklist and shut the Pi down "
                          "on the Deck Panel.", "good", "pack")
        else:
            add_event("Pack up finished with problems - see the Deck Panel."
                      + (" The Pi stays on so you can look." if pi_off else ""), "warn", "pack")
    # after the lock is released, so nothing is left "busy" if the shutdown is slow
    if pi_off and st["ok"]:
        r = net("poweroff")
        if not r.get("ok"):
            add_event(f"Couldn't shut the Pi down: {r.get('error')}", "warn", "pack")
    return st


def _pack_steps(cfg, mark):
        up = k12_up(cfg)
        k12 = up and k12_agent_ready(cfg)
        if not k12:
            why = ("PC is on but the Pi can't control it (agent not linked?)" if up
                   else "PC is already off")
            for k in ("players", "stop", "backup", "cool"):
                mark(k, "skip", why)
            if up:   # don't let someone close the case on a running PC without noticing
                mark("k12off", "fail", "PC is still on - shut it down by hand before closing the case")
            else:
                mark("k12off", "skip", why)
        else:
            # 1. warn players
            mark("players", "running")
            mc = mc_status(cfg["k12_ip"], int(cfg["mc_port"]))
            if mc["online"] and mc["players"]:
                wait = int(cfg.get("pack_warn_s", 60))
                mark("players", "running", f"{mc['players']} playing - stopping in {wait} s")
                say_in_game(cfg, f"The deck is packing up - saving and stopping in {wait} seconds.")
                time.sleep(max(0, wait - 10))
                say_in_game(cfg, "Stopping in 10 seconds. Thanks for playing!")
                time.sleep(min(10, wait))
                mark("players", "ok", f"warned {mc['players']} player(s)")
            else:
                mark("players", "skip", "nobody playing")
            # 2. save + stop
            mark("stop", "running")
            r = agent(cfg, "stop", timeout_s=180)
            mark("stop", "ok" if r.get("ok") else "fail", r.get("msg") or r.get("error", ""))
            # 3. backup
            mark("backup", "running", "zipping the world and copying it to the Pi...")
            b = _backup(cfg)
            if b.get("ok"):
                mark("backup", "ok", f"{b['file']} ({human_size(b['bytes'])})")
            else:
                mark("backup", "skip" if b.get("skip") else "fail", b.get("error", ""))
            # 4. cool down
            mark("cool", "running")
            limit = float(cfg.get("pack_cool_c", 55))
            deadline = time.time() + int(cfg.get("pack_cool_max_s", 300))
            while True:
                t = (k12_temps(cfg) or {}).get("cpu")
                if t is None:
                    mark("cool", "skip", "no temperature reading")
                    break
                if t <= limit:
                    mark("cool", "ok", f"CPU {t:.0f} °C")
                    break
                if time.time() > deadline:
                    mark("cool", "fail", f"still {t:.0f} °C - leave the lid open a few more minutes")
                    break
                mark("cool", "running", f"CPU {t:.0f} °C, waiting for {limit:.0f} °C...")
                time.sleep(10)
        # 5. travel mode off
        if travel_on():
            mark("travel", "running")
            r = net("travel", "off")
            if k12:
                agent(cfg, "travel", "off", timeout_s=40)
            mark("travel", "ok" if r.get("ok") else "fail",
                 "off - run travel-off.cmd on the laptop" if r.get("ok") else r.get("error", ""))
        else:
            mark("travel", "skip", "wasn't on")
        # 6. PC off
        if k12:
            mark("k12off", "running", "shutting down...")
            r = agent(cfg, "off", "now", timeout_s=60)
            if not r.get("ok"):
                mark("k12off", "fail", r.get("error", ""))
            else:
                deadline = time.time() + 180
                while time.time() < deadline and k12_up(cfg):
                    time.sleep(5)
                if k12_up(cfg):
                    mark("k12off", "fail", "still answering after 3 min - check its screen")
                else:
                    remove("woke")
                    mark("k12off", "ok", "off")


# ================================================= deck check (self-test) =====

def _check(name, ok, detail="", fix=""):
    return {"name": name, "ok": ok, "detail": detail, "fix": fix}


def tailscale_running():
    try:
        r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
        return json.loads(r.stdout).get("BackendState") == "Running"
    except Exception:
        return False


def eth0_linked():
    try:
        with open("/sys/class/net/eth0/carrier") as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def self_test(cfg, full=False):
    """Quick check of the Pi side; full=True also wakes and checks the PC."""
    res = []
    link = eth0_linked()
    res.append(_check("Case cable (Pi to switch)", link, "linked" if link else "no link",
                      "Check the Pi's Ethernet cable and that the switch has power (Pi USB port)."))
    n = net("status", timeout_s=25)
    w = (n.get("wifi") or {}) if n.get("ok") else {}
    res.append(_check("Pi Wi-Fi", bool(w.get("ssid")), w.get("ssid") or "not connected",
                      "Deck Panel > Choose Wi-Fi network."))
    inet = n.get("internet")
    res.append(_check("Internet", inet == "online",
                      {"online": "online", "portal": "login page in the way", "offline": "none"}.get(inet, "?"),
                      "Open the login link on the panel." if inet == "portal" else "Check the Wi-Fi."))
    ts = tailscale_running()
    res.append(_check("Tailscale", ts, "connected" if ts else "not connected", "On the Pi: sudo tailscale up"))
    pt = pi_temp()
    res.append(_check("Pi temperature", pt is None or pt < float(cfg["pi_warn"]),
                      f"{pt:.0f} °C" if pt is not None else "?", "Check the Pi's fan."))
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        free_mb = shutil.disk_usage(BACKUP_DIR).free // 2**20
    except OSError:
        free_mb = 0
    res.append(_check("Pi storage", free_mb > int(cfg.get("backup_min_free_mb", 1024)),
                      f"{free_mb / 1024:.1f} GB free", "Delete old backups or use a bigger card."))
    age = backup_age_h()
    if age > 1e8:
        res.append(_check("World backup", None, "none yet", "Deck Panel > Back up now (PC on)."))
    else:
        res.append(_check("World backup", age < 24 * 7,
                          f"{age:.0f} h old" if age < 48 else f"{age / 24:.0f} days old",
                          "Deck Panel > Back up now (PC on)."))
    linked = bool(cfg.get("k12_mac") and cfg.get("k12_user"))
    res.append(_check("PC set up", linked, "MAC + agent linked" if linked else "not finished",
                      "README steps 2 and 6 (deck-set-mac, deck-link-k12)."))
    if full and linked:
        if not k12_up(cfg):
            wake_k12(cfg, "deck check")
        deadline = time.time() + int(cfg["wake_timeout_s"])
        while time.time() < deadline and not k12_agent_ready(cfg):
            time.sleep(5)
        ready = k12_agent_ready(cfg)
        res.append(_check("PC wakes + agent answers", ready, "yes" if ready else "no answer",
                          "Check Wake-on-LAN (BIOS), auto sign-in, and the sshd service (README)."))
        if ready:
            info = {}
            deadline = time.time() + 150          # Docker takes a while after sign-in
            while time.time() < deadline:
                info = agent(cfg, "info", timeout_s=20)
                if info.get("docker"):
                    break
                time.sleep(10)
            res.append(_check("Docker", bool(info.get("docker")),
                              "running" if info.get("docker") else "not running",
                              "Sign in on the PC once and open Docker Desktop."))
            t = k12_temps(cfg)
            res.append(_check("PC temperatures", t is not None,
                              f"CPU {t['cpu']:.0f} °C" if t and t.get("cpu") is not None else "not readable",
                              "Start LibreHardwareMonitor (web server on, port 8085)."))
            res.append(_check("Minecraft EULA", info.get("eula") is not False,
                              "accepted" if info.get("eula") else "not accepted",
                              r"Set MC_EULA=TRUE in C:\deck\game-servers\.env"))
    out = {"t": time.time(), "full": full, "results": res,
           "ok": all(r["ok"] is not False for r in res)}
    write_json("selftest.json", out)
    bad = [r for r in res if r["ok"] is False]
    if bad:
        add_event("Deck check: " + "; ".join(f"{r['name']} - {r['detail']}" for r in bad) + ".", "warn", "check")
    else:
        add_event("Deck check: all good.", "good", "check")
    return out


# ================================================= Tailscale invites ==========

def tailscale_invite(api_key, k12_tailscale_ip, k12_host=""):
    """Make a one-time link that shares ONLY the PC with a friend's own tailnet.
    Needs a Tailscale API access token (admin console > Settings > Keys)."""
    def call(method, path, body=None):
        req = urllib.request.Request("https://api.tailscale.com/api/v2" + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {api_key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError("Tailscale API key rejected - it may have expired. "
                                   "Make a new one and run: sudo deck-tailscale-setup")
            raise RuntimeError(f"Tailscale API error {e.code}")

    devices = call("GET", "/tailnet/-/devices").get("devices", [])
    dev = next((x for x in devices if k12_tailscale_ip and k12_tailscale_ip in (x.get("addresses") or [])), None)
    if dev is None and k12_host:
        dev = next((x for x in devices if (x.get("hostname") or "").lower() == k12_host.lower()), None)
    if dev is None:
        raise RuntimeError("Couldn't find the PC in your tailnet (is Tailscale signed in on it?)")
    inv = call("POST", f"/device/{dev.get('nodeId') or dev.get('id')}/device-invites",
               [{"multiUse": False, "allowExitNode": False}])
    if isinstance(inv, dict):
        inv = inv.get("invites") or [inv]
    url = (inv[0] if inv else {}).get("inviteUrl")
    if not url:
        raise RuntimeError("Tailscale didn't return an invite link")
    return url


# ================================================= modpacks (CurseForge) =======
# The server image (itzg/minecraft-server, TYPE=AUTO_CURSEFORGE) downloads and installs
# the pack. The deck picks it, keeps one world per pack, switches, and watches the log.

CF_ENV = os.environ.get("DECK_CF_ENV", "/etc/deck/curseforge.env")   # sudo deck-curseforge-setup
CF_API = "https://api.curseforge.com"
CF_GAME_MINECRAFT = 432
CF_MODPACK_CLASS_FALLBACK = 4471      # looked up from /v1/categories when possible
# same rules as $SlugRe / $FileIdRe in deck-agent.ps1
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
FILE_ID_RE = re.compile(r"^\d{1,10}$")
_LINK_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?curseforge\.com/minecraft/modpacks/([a-z0-9][a-z0-9-]{0,79})"
    r"(?:/(?:files|download)/(\d{1,10})|/[a-z]+(?:/[a-z]+)?)?/?(?:[?#]\S*)?$", re.I)
# CurseForge modLoader numbers; the server image installs Forge, NeoForge and Fabric (not Quilt)
CF_LOADERS = {1: "Forge", 4: "Fabric", 5: "Quilt", 6: "NeoForge"}
SUPPORTED_LOADERS = {"Forge", "NeoForge", "Fabric"}

# Minecraft version -> server image (Java) tag. The ONE place this lives.
# Minecraft wiki "Update Java": 1.16.5 and older run on Java 8, 1.17-1.20.4 need 16/17,
# 1.20.5 and newer need 21. Newer than this table -> "latest" (newest Java).
JAVA_TAGS = [((1, 16, 5), "java8"), ((1, 20, 4), "java17"), ((1, 21, 999), "java21")]

# What the server log says when something needs you (data, not code - add lines as we learn more).
# kind "missing": a mod its author won't let tools download; you fetch it by hand.
MODPACK_LOG_RULES = [
    (re.compile(r"The authors of the mod '(?P<mod>.+?)' have disallowed project distribution\. "
                r"Manually download the file '(?P<file>.+?)' from (?P<url>\S+)"), "missing", ""),
    (re.compile(r"Some mod authors disallow automated downloads"), "missing", ""),
    (re.compile(r"forbidden or rate-limit has been exceeded|CurseForge API key is required|"
                r"API key (?:should start|seems to have extra)", re.I), "key",
     "The PC's CurseForge key was refused. On the PC run setup-curseforge.cmd again."),
    (re.compile(r"Could not resolve user from \w+: \S+"), "user",
     "A name on the whitelist or operators list isn't a Minecraft Java account. Fix MC_WHITELIST_PLAYERS / MC_OPS "
     "in C:\\deck\\game-servers\\.env (or remove the name under Players allowed), then try again."),
    (re.compile(r"ModLoader \S+ is not yet supported"), "loader",
     "The server can't install this pack's mod loader (Quilt packs aren't supported)."),
    (re.compile(r"modpack authors have indicated this file is not allowed for project distribution", re.I), "blocked",
     "This pack's author doesn't allow automatic installs. Pick another pack."),
]
# ---- crashes caused by mods that only belong on players' computers (shaders, minimaps, menus...)
# Data, not code: add a line when a new kind of crash turns up. Built from real server logs:
#   Forge 1.19.2/1.18.2 "-- MOD oculus --" + "Mod File:" + "has failed to load correctly"
#     (itzg/docker-minecraft-server issue 1893), Forge 1.20.1 "Failed to create mod instance. ModID:"
#     (issue 3405), mixin "@Mixin target net.minecraft.client... was not found x.json" (issue 3912),
#   missing dependency "Mod ID: 'x', Requested by: 'y'" (issue 2730).
#   Fabric lines follow Fabric Loader's message format; not yet checked against a real crash log.
CLIENT_MARKERS = re.compile(r"for invalid dist DEDICATED_SERVER|in environment type SERVER")
# lines that NAME a mod; looked for within CLIENT_WINDOW lines of a client marker
CLIENT_CLUES = [
    ("modid", re.compile(r"Failed to create mod instance\. ModID: (?P<v>[\w.-]+)")),
    ("modid", re.compile(r"Failure message: (?P<name>.+?) \((?P<v>[\w.-]+)\) has failed to load correctly")),
    ("file", re.compile(r"Mod File: (?:.*[/\\])?(?P<v>[^/\\]+?\.jar)\s*$")),
    ("modid", re.compile(r"^\s*-- MOD (?P<v>[\w.-]+) --")),
    ("modid", re.compile(r"provided by '(?P<v>[\w.-]+)'")),                       # Fabric (format only)
    ("modid", re.compile(r"Mixin apply for mod (?P<v>[\w.-]+) failed")),           # Fabric (format only)
]
CLIENT_WINDOW = 8
# a mixin aimed at a client-only class names its own config file - a weaker clue, used only if nothing better
MIXIN_CLUE = re.compile(r"@Mixin target net\.minecraft\.client\.\S+ was not found (?P<v>[\w.-]+\.json)")
# "Requested by" a mod that needs one we already took out: that one goes too
NEEDS_CLUE = re.compile(r"Mod ID: '(?P<dep>[\w.-]+)', Requested by: '(?P<v>[\w.-]+)'")
# the server crashed (it may restart by itself, so these lines are how we notice)
CRASH_MARKERS = re.compile(r"Mod Loading has failed|Mod loading error has occurred|Crash report saved to|"
                           r"This crash report has been saved|Failed to start the minecraft server|"
                           r"Could not execute entrypoint stage|Incompatible mods found|"
                           r"Missing or unsupported mandatory dependencies|Exception in server tick loop", re.I)
OTHER_CRASHES = [
    (re.compile(r"Unsupported class file major version|has been compiled by a more recent version of the Java"),
     "A mod needs a different Java version than the server has. Tell the deck owner (README > Modpacks)."),
    (re.compile(r"java\.lang\.OutOfMemoryError"),
     "The server ran out of memory. Raise modpack_mem_gb in /etc/deck/config.json."),
]
# never take these out: they're the game and the loaders themselves
PROTECTED_IDS = {"minecraft", "forge", "neoforge", "fml", "javafml", "fabricloader", "fabric", "fabric-api",
                 "quilt_loader", "java", "mixinextras"}
AUTOFIX_ROUNDS = 3
JAR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+()\[\]-]{0,150}\.jar$")      # same as $JarRe in deck-agent.ps1
MAX_UPLOAD = 100 * 2**20


def find_client_culprits(lines, removed_ids=()):
    """Server log / crash report lines -> what to take out.
    -> {"clues": [{"kind": "modid"|"file"|"mixin", "value", "name"}], "other": text or ""}"""
    lines = list(lines or [])
    clues, seen = [], set()

    def add(kind, value, name=""):
        key = (kind, value.lower())
        if key in seen and name:          # named again, this time with its full name
            for c in clues:
                if (c["kind"], c["value"].lower()) == key and not c["name"]:
                    c["name"] = name
        if key not in seen and value.lower() not in PROTECTED_IDS:
            seen.add(key)
            clues.append({"kind": kind, "value": value, "name": name})
    marks = [i for i, l in enumerate(lines) if CLIENT_MARKERS.search(l)]
    for i in marks:
        for l in lines[max(0, i - CLIENT_WINDOW): i + CLIENT_WINDOW + 1]:
            for kind, rx in CLIENT_CLUES:
                m = rx.search(l)
                if m:
                    add(kind, m.group("v"), m.groupdict().get("name") or "")
    for l in lines:
        m = NEEDS_CLUE.search(l)
        if m and m.group("dep").lower() in {x.lower() for x in removed_ids}:
            add("modid", m.group("v"), f"needs {m.group('dep')}, which only works on players' computers")
    if not clues:
        for l in lines:
            m = MIXIN_CLUE.search(l)
            if m:
                add("mixin", m.group("v"))
    other = ""
    if not clues:
        for rx, msg in OTHER_CRASHES:
            if any(rx.search(l) for l in lines):
                other = msg
                break
    return {"clues": clues, "other": other}


def pack_file_map(meta):
    """{jar file name: {"id": CurseForge project id, "name", "slug"}} for one pack version, from CurseForge.
    Cached on the Pi. Raises RuntimeError if CurseForge can't say."""
    cache = f"modpack_files_{meta.get('slug')}.json"
    if meta.get("source") == "zip":
        # an imported export: the mod list came with its manifest.json (meta["files"] = CurseForge file ids)
        ids = [int(x) for x in meta.get("files") or [] if str(x).isdigit()]
        if not ids:
            raise RuntimeError("the Pi doesn't know this pack's mod list")
        key = "zip-" + hashlib.sha1(",".join(map(str, ids)).encode()).hexdigest()[:12]
        hit = read_json(cache) or {}
        if hit.get("file_id") == key and hit.get("files"):
            return hit["files"]
        return _map_pack_files(ids, cache, key)
    pid, fid = meta.get("id"), meta.get("file_id")
    if not (pid and fid):
        raise RuntimeError("the Pi doesn't know this pack's CurseForge ids")
    hit = read_json(cache) or {}
    if hit.get("file_id") == fid and hit.get("files"):
        return hit["files"]
    f = cf_api(f"/v1/mods/{int(pid)}/files/{int(fid)}").get("data") or {}
    url = f.get("downloadUrl") or ""
    if not url.startswith("https://"):
        raise RuntimeError("CurseForge won't hand out this pack's file list")
    tmp = _path(f"packzip-{os.getpid()}.part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cyberdeck/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
            n = 0
            for chunk in iter(lambda: r.read(1 << 20), b""):
                n += len(chunk)
                if n > 300 * 2**20:
                    raise RuntimeError("pack download too big")
                out.write(chunk)
        with zipfile.ZipFile(tmp) as z:
            man = json.loads(z.read("manifest.json"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as e:
        raise RuntimeError(f"couldn't read the pack's mod list ({e})")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    ids = [int(x["fileID"]) for x in man.get("files") or [] if str(x.get("fileID", "")).isdigit()]
    return _map_pack_files(ids, cache, fid)


def _map_pack_files(ids, cache, key):
    """CurseForge file ids -> {jar file name: {"id", "name", "slug"}}, cached under key."""
    files = []
    for i in range(0, len(ids), 500):
        files += cf_api("/v1/mods/files", body={"fileIds": ids[i:i + 500]}).get("data") or []
    mids = sorted({int(x["modId"]) for x in files if x.get("modId")})
    mods = {}
    for i in range(0, len(mids), 500):
        for m in cf_api("/v1/mods", body={"modIds": mids[i:i + 500]}).get("data") or []:
            mods[m.get("id")] = m
    out = {}
    for x in files:
        m = mods.get(x.get("modId")) or {}
        if x.get("fileName"):
            out[x["fileName"]] = {"id": x.get("modId"), "name": m.get("name") or x.get("displayName") or x["fileName"],
                                  "slug": m.get("slug") or ""}
    write_json(cache, {"file_id": key, "files": out})
    return out


# ---- importing a CurseForge "Export profile" zip (the Deck app copies the zip to the PC itself)
# The zip is what the CurseForge app's Export profile makes: manifest.json (Minecraft version,
# mod loader, every mod as a CurseForge project + file id) and overrides/ (configs, scripts...).
# The server image installs it from C:\deck\game-servers\imports\<pack>.zip (CF_MODPACK_ZIP): it
# downloads the mods, leaves out the client-only ones it knows, and copies the overrides.
CF_LOADER_PREFIX = {"forge": "Forge", "neoforge": "NeoForge", "fabric": "Fabric", "quilt": "Quilt"}
MAX_IMPORT_MODS = 3000


def import_slug(name):
    """Pack name -> the name its world and settings are kept under (same rule as SLUG_RE)."""
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:60].strip("-")
    return s if SLUG_RE.match(s or "") else "imported-pack"


def import_meta(manifest):
    """manifest.json of an exported pack (dict or JSON text) -> pack details like modpack_preview's.
    Raises ValueError with a message for people."""
    if isinstance(manifest, (bytes, str)):
        if len(manifest) > 4 * 2**20:
            raise ValueError("That manifest.json is too big.")
        try:
            manifest = json.loads(manifest)
        except ValueError:
            raise ValueError("The zip's manifest.json isn't readable.")
    how = ("Export it from the CurseForge app: open the pack's profile, the ⋯ menu > Export profile, "
           "and pick that zip.")
    if not isinstance(manifest, dict) or manifest.get("manifestType") != "minecraftModpack":
        raise ValueError("That zip isn't a CurseForge modpack export. " + how)
    mc = manifest.get("minecraft") if isinstance(manifest.get("minecraft"), dict) else {}
    mcv = str(mc.get("version") or "")
    if not re.match(r"^\d+\.\d+(\.\d+)?$", mcv):
        raise ValueError("The export doesn't say which Minecraft version it's for. " + how)
    loaders = [x for x in mc.get("modLoaders") or [] if isinstance(x, dict) and x.get("id")]
    main = next((x for x in loaders if x.get("primary")), loaders[0] if loaders else {})
    loader_id = _text(main.get("id"), 60)
    loader = CF_LOADER_PREFIX.get(loader_id.split("-")[0].lower(), "")
    files = []
    for f in manifest.get("files") or []:
        if isinstance(f, dict) and str(f.get("fileID", "")).isdigit() and str(f.get("projectID", "")).isdigit():
            files.append(int(f["fileID"]))
    if len(files) > MAX_IMPORT_MODS:
        raise ValueError(f"That pack lists more than {MAX_IMPORT_MODS} mods.")
    name = _text(manifest.get("name"), 60).strip() or "Imported pack"
    version = _text(manifest.get("version"), 40).strip()
    warn = ""
    if loader == "Quilt":
        warn = "This is a Quilt pack. The server can't install Quilt packs - pick a Forge, NeoForge or Fabric pack."
    elif not loader:
        warn = "The export doesn't say which mod loader it uses, so the server can't install it."
    elif not files:
        warn = "This export has no mods in it - it would just be plain Minecraft " + mcv + "."
    return {"ok": True, "source": "zip", "slug": import_slug(name), "name": name, "version": version,
            "file_id": "zip", "file_name": (name + " " + version).strip(), "mc": mcv, "loader": loader,
            "loader_version": loader_id, "java": java_tag(mcv), "files": files, "mods": len(files),
            "url": "", "logo": "", "summary": "", "can_install": loader in SUPPORTED_LOADERS, "warning": warn}


def resolve_culprits(clues, mods, filemap):
    """Clues from the crash -> mods to take out: [{"id", "name", "file", "why"}] (only ones we can name)."""
    out, seen = [], set()
    for c in clues:
        jar = None
        for m in mods or []:
            if (c["kind"] == "file" and m.get("file") == c["value"]) or \
               (c["kind"] == "modid" and c["value"] in (m.get("ids") or [])) or \
               (c["kind"] == "mixin" and c["value"] in (m.get("mixins") or [])):
                jar = m
                break
        info = (filemap or {}).get(jar["file"]) if jar else None
        if not info or not info.get("id") or info["id"] in seen:
            continue
        seen.add(info["id"])
        out.append({"id": int(info["id"]), "name": info.get("name") or c.get("name") or c["value"],
                    "file": jar["file"], "mod_ids": jar.get("ids") or [],
                    "why": c.get("name") if c.get("name", "").startswith("needs ") else "only works on players' computers"})
    return out


MODPACK_STEPS = [("players", "Warn players"), ("stop", "Save + stop Minecraft"),
                 ("backup", "Back up the world that's on now"), ("apply", "Switch worlds"),
                 ("start", "Start the server"), ("watch", "Download + load (first time: several minutes)")]


def parse_modpack_link(text):
    """CurseForge modpack link (or just its short name) -> (slug, file_id or None).
    Raises ValueError with a message for people."""
    t = (text or "").strip()
    if not t:
        raise ValueError("Paste a CurseForge modpack link first.")
    if len(t) > 300:
        raise ValueError("That's too long to be a CurseForge link.")
    if SLUG_RE.match(t):
        return t, None
    m = _LINK_RE.match(t)
    if not m:
        if "curseforge.com" in t.lower() and "/modpacks/" not in t.lower():
            raise ValueError("That's a CurseForge link, but not to a modpack. "
                             "Open the pack's page (curseforge.com/minecraft/modpacks/...) and copy that.")
        raise ValueError("That isn't a CurseForge modpack link. It should look like "
                         "https://www.curseforge.com/minecraft/modpacks/<name>")
    slug = m.group(1).lower()
    return slug, (int(m.group(2)) if m.group(2) else None)


def _version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return None


def java_tag(mc_version):
    """Server image tag for a Minecraft version (see JAVA_TAGS)."""
    v = _version_tuple(mc_version)
    if not v:
        return "latest"
    for top, tag in JAVA_TAGS:
        if v <= top:
            return tag
    return "latest"


def modpack_memory(cfg):
    """(java heap GB, container limit GB) for a modpack, kept under what Docker may use."""
    cap = min(max(4, int(cfg.get("docker_mem_gb", 20)) - 2), 26)   # agent allows up to 24 GB Java
    limit = min(max(int(cfg.get("modpack_mem_gb", 10)), 4), cap)
    return limit - 2, limit


def cf_api_key():
    k = os.environ.get("CF_API_KEY", "").strip()
    if k:
        return k
    try:
        with open(CF_ENV, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("CF_API_KEY="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def cf_api(path, params=None, timeout_s=15, body=None):
    """GET (or POST with body) the CurseForge API. Raises RuntimeError with a message for people."""
    key = cf_api_key()
    if not key:
        raise RuntimeError("No CurseForge key on the Pi yet. On the Pi run: sudo deck-curseforge-setup")
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    hdr = {"x-api-key": key, "Accept": "application/json", "User-Agent": "cyberdeck/1.0"}
    if body is not None:
        hdr["Content-Type"] = "application/json"
    req = urllib.request.Request(CF_API + path + q, headers=hdr, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("CurseForge refused the Pi's key. Run sudo deck-curseforge-setup again.")
        if e.code == 404:
            raise RuntimeError("CurseForge doesn't know that pack or file.")
        raise RuntimeError(f"CurseForge error {e.code} - try again in a minute.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Can't reach CurseForge - is the Pi online?")


_cf_class = {}


def _modpack_class():
    if "id" not in _cf_class:
        try:
            cats = cf_api("/v1/categories", {"gameId": CF_GAME_MINECRAFT, "classesOnly": "true"}).get("data") or []
            _cf_class["id"] = next((c["id"] for c in cats if c.get("slug") == "modpacks"), CF_MODPACK_CLASS_FALLBACK)
        except RuntimeError:
            return CF_MODPACK_CLASS_FALLBACK
    return _cf_class["id"]


def _mc_version(game_versions):
    vs = [v for v in (game_versions or []) if re.match(r"^\d+\.\d+(\.\d+)?$", str(v))]
    return max(vs, key=_version_tuple) if vs else ""


def modpack_preview(text):
    """Look a pack up on CurseForge. -> dict for the panel / bot (raises ValueError/RuntimeError)."""
    slug, file_id = parse_modpack_link(text)
    found = cf_api("/v1/mods/search", {"gameId": CF_GAME_MINECRAFT, "classId": _modpack_class(), "slug": slug})
    mod = next((m for m in (found.get("data") or []) if m.get("slug") == slug), None)
    if not mod:
        raise RuntimeError(f"No modpack called '{slug}' on CurseForge. Check the link.")
    fid = file_id or mod.get("mainFileId")
    if not fid:
        raise RuntimeError("This pack has no files to install yet.")
    f = cf_api(f"/v1/mods/{int(mod['id'])}/files/{int(fid)}").get("data") or {}
    mc = _mc_version(f.get("gameVersions"))
    loader = ""
    for idx in mod.get("latestFilesIndexes") or []:
        if idx.get("fileId") == fid and idx.get("modLoader") in CF_LOADERS:
            loader = CF_LOADERS[idx["modLoader"]]
    if not loader:
        names = {str(v).lower() for v in f.get("gameVersions") or []}
        loader = next((n for n in ("NeoForge", "Forge", "Fabric", "Quilt") if n.lower() in names), "")
    if not loader:
        loader = next((CF_LOADERS[i["modLoader"]] for i in mod.get("latestFilesIndexes") or []
                       if i.get("gameVersion") == mc and i.get("modLoader") in CF_LOADERS), "")
    logo = ((mod.get("logo") or {}).get("thumbnailUrl") or "")
    warn = ""
    if loader == "Quilt":
        warn = "This is a Quilt pack. The server can't install Quilt packs - pick a Forge, NeoForge or Fabric pack."
    elif not loader:
        warn = "CurseForge doesn't say which mod loader this pack uses. It may still work."
    if f.get("isServerPack"):
        warn = "That link is the pack's server download. Paste the pack's page link (or a normal file) instead."
    return {"ok": True, "slug": slug, "id": mod.get("id"), "name": str(mod.get("name") or slug)[:80],
            "summary": str(mod.get("summary") or "")[:200],
            "logo": logo if logo.startswith("https://media.forgecdn.net/") else "",
            "url": f"https://www.curseforge.com/minecraft/modpacks/{slug}",
            "file_id": int(fid), "file_name": str(f.get("displayName") or f.get("fileName") or "")[:120],
            "pinned": file_id is not None, "mc": mc, "loader": loader, "java": java_tag(mc),
            "downloads": int(mod.get("downloadCount") or 0),
            "can_install": loader in SUPPORTED_LOADERS and not f.get("isServerPack"), "warning": warn}


def scan_modpack_log(lines):
    """Read server log lines. -> {"missing": [{mod,file,url}], "problem": text or ""}"""
    missing, problem = [], ""
    for line in lines or []:
        for rx, kind, msg in MODPACK_LOG_RULES:
            m = rx.search(line)
            if not m:
                continue
            if kind == "missing" and "mod" in m.groupdict():
                url = m.group("url").rstrip(".,")
                item = {"mod": m.group("mod")[:80], "file": m.group("file")[:120],
                        "url": url if url.startswith("https://www.curseforge.com/") else ""}
                if item not in missing:
                    missing.append(item)
            elif kind != "missing" and not problem:
                problem = msg
    return {"missing": missing, "problem": problem}


def how_to_join(cfg, meta, state=None):
    """The message players need: same pack + same version in the CurseForge app, then the address."""
    join = join_address(cfg, state or read_json("state.json", {}) or {}) or "the deck's address (ask the owner)"
    ver = meta.get("file_name") or "the version the server uses"
    # plain text: it shows on the panel as-is and in Discord
    return (f"How to join {meta.get('name', meta.get('slug'))}:\n"
            f"1. Install the CurseForge app (curseforge.com/download/app) and open Minecraft there.\n"
            + (f"2. Get the pack's export zip from the deck's owner (\"{ver}\"), then in the CurseForge app: "
               f"Create Custom Profile > Import, and pick that zip"
               if meta.get("source") == "zip" else
               f"2. Install this pack: {meta.get('url')} - pick version \"{ver}\"")
            + (f" (Minecraft {meta['mc']}, {meta.get('loader') or 'mods'})" if meta.get("mc") else "") + ".\n"
            f"3. Play it, then Multiplayer > Add Server > {join}"
            + ("\n4. This server has a keybind preset. When you join, the keybind mod asks "
               "\"Add and switch to the server preset?\" - say Yes to get the server's keys "
               "(you can switch back to your own any time)." if preset_get(meta.get("slug", "")) else ""))


def _modpack_meta():
    return read_json("modpacks.json", {}) or {}


def modpack_list(cfg):
    """Installed packs (from the PC if it's on, else the last list the Pi saw) + names from the Pi."""
    meta = _modpack_meta()
    live = False
    data = read_json("modpack_list.json", {"active": "", "packs": []}) or {}
    if k12_up(cfg) and cfg.get("k12_user"):
        r = agent(cfg, "modpack", "list", timeout_s=30)
        if r.get("ok"):
            live = True
            data = {"active": r.get("active") or "", "packs": r.get("packs") or [], "key": r.get("key")}
            write_json("modpack_list.json", data)
        elif "unknown command" in str(r.get("error", "")):
            return {"ok": False, "error": "The PC's deck agent is too old for modpacks - "
                                          "run setup-k12-agent.cmd on the PC again."}
    packs = []
    for p in data.get("packs") or []:
        s = p.get("slug", "")
        if not SLUG_RE.match(s):
            continue
        m = meta.get(s, {})
        packs.append({"slug": s, "name": m.get("name") or s, "mc": m.get("mc", ""), "loader": m.get("loader", ""),
                      "file_name": m.get("file_name", ""), "logo": m.get("logo", ""), "url": m.get("url", ""),
                      "has_world": bool(p.get("has_world")), "excluded": p.get("excluded") or "",
                      # mods the auto-fix took out (Put back undoes it)
                      "removed": [{"id": r.get("id"), "name": r.get("name"), "why": r.get("why")}
                                  for r in m.get("removed") or []]})
    packs.sort(key=lambda p: p["name"].lower())
    active = data.get("active") or ""
    return {"ok": True, "live": live, "active": active,
            "active_name": (meta.get(active, {}).get("name") or active) if active else "Vanilla",
            "packs": packs, "pc_key": data.get("key"), "pi_key": bool(cf_api_key())}


def modpack_job():
    return read_json("modpack_job.json")


def modpack_active_name():
    """Name of the world that's on, from what the Pi last saw (no SSH). '' = vanilla."""
    active = (read_json("modpack_list.json", {}) or {}).get("active") or ""
    return (_modpack_meta().get(active, {}).get("name") or active) if active else ""


def _wait_for_pc(cfg, who):
    if k12_agent_ready(cfg):
        return True
    if not k12_up(cfg):
        add_event("Waking the PC...", "info", "k12")
        wake_k12(cfg, who)
    deadline = time.time() + int(cfg["wake_timeout_s"])
    while time.time() < deadline:
        time.sleep(5)
        if k12_agent_ready(cfg):
            return True
    return False


def modpack_change(cfg, target, who="panel", meta=None):
    """Switch the server to modpack `target` (slug) or to vanilla (target "").
    meta: preview dict when installing a new pack. Progress goes to modpack_job.json."""
    target = target or ""
    if target and not SLUG_RE.match(target):
        raise ValueError("bad pack name")
    known = _modpack_meta()
    m = dict(meta or known.get(target) or {"slug": target, "name": target})
    if target and meta:
        if meta.get("source") == "zip":
            if meta.get("file_id") != "zip" or meta.get("slug") != target:
                raise ValueError("bad imported pack")
        elif not FILE_ID_RE.match(str(meta.get("file_id", ""))):
            raise ValueError("bad file id")
        if meta.get("java") not in ("java8", "java17", "java21", "java25", "latest"):
            raise ValueError("bad java tag")
    name = m.get("name") or target or "Vanilla"
    st = {"running": True, "started": time.time(), "by": who, "done": False, "ok": None,
          "target": target, "name": name if target else "Vanilla", "missing": [], "problem": "",
          "removed": [], "rounds": 0,
          "steps": [{"key": k, "label": lbl, "status": "pending", "detail": ""} for k, lbl in MODPACK_STEPS]}

    def mark(key, status, detail=""):
        for s in st["steps"]:
            if s["key"] == key:
                s["status"], s["detail"] = status, detail
        write_json("modpack_job.json", st)

    with _ActionLock(f"switch to {name if target else 'vanilla'}", who):
        write_json("modpack_job.json", st)
        add_event(f"Switching the server to {st['name']} (asked by {who}).", "info", "modpack")
        try:
            _modpack_steps(cfg, st, mark, target, m)
        except Exception as e:
            cur = next((s for s in st["steps"] if s["status"] == "running"), None)
            if cur:
                mark(cur["key"], "fail", f"error: {e}")
            for s in st["steps"]:
                if s["status"] == "pending":
                    mark(s["key"], "skip", "not run (error above)")
            add_event(f"Switching to {st['name']} stopped by an error: {e}", "bad", "modpack")
        st["running"], st["done"], st["ended"] = False, True, time.time()
        st["ok"] = not any(s["status"] == "fail" for s in st["steps"])
        write_json("modpack_job.json", st)
    return st


def _modpack_steps(cfg, st, mark, target, m):
    mark("players", "running", "waking the PC..." if not k12_up(cfg) else "")
    if not _wait_for_pc(cfg, st["by"]):
        mark("players", "fail", "The PC didn't answer - is it plugged in? (README: Wake-on-LAN, auto sign-in)")
        for k in ("stop", "backup", "apply", "start", "watch"):
            mark(k, "skip", "PC not reachable")
        add_event(f"Couldn't switch to {st['name']}: the PC didn't answer.", "bad", "modpack")
        return
    # 1. warn players
    mc = mc_status(cfg["k12_ip"], int(cfg["mc_port"]))
    if mc["online"] and mc["players"]:
        wait = int(cfg.get("pack_warn_s", 60))
        mark("players", "running", f"{mc['players']} playing - restarting in {wait} s")
        say_in_game(cfg, f"The server is switching to {st['name']} - saving and restarting in {wait} seconds.")
        time.sleep(max(0, wait - 10))
        say_in_game(cfg, "Restarting in 10 seconds.")
        time.sleep(min(10, wait))
        mark("players", "ok", f"warned {mc['players']} player(s)")
    else:
        mark("players", "skip", "nobody playing")
    # 2. stop
    mark("stop", "running")
    r = agent(cfg, "stop", timeout_s=180)
    if not r.get("ok"):
        mark("stop", "fail", r.get("error", ""))
        for k in ("backup", "apply", "start", "watch"):
            mark(k, "skip", "server didn't stop")
        return
    mark("stop", "ok", "world saved")
    # 3. back up the world that was on
    mark("backup", "running", "zipping the world and copying it to the Pi...")
    b = _backup(cfg)
    if b.get("ok"):
        mark("backup", "ok", f"{b['file']} ({human_size(b['bytes'])})")
    elif b.get("skip"):
        mark("backup", "skip", "no world yet")
    else:
        # don't switch away from a world we couldn't back up
        mark("backup", "fail", b.get("error", ""))
        for k in ("apply", "start", "watch"):
            mark(k, "skip", "backup failed - nothing changed")
        return
    # 4. switch
    mark("apply", "running")
    if target:
        heap, limit = modpack_memory(cfg)
        fid = str(m.get("file_id") or "latest")
        r = agent(cfg, "modpack", "apply", target, fid, m.get("java") or java_tag(m.get("mc")),
                  str(heap), str(limit), timeout_s=60)
    else:
        r = agent(cfg, "modpack", "vanilla", timeout_s=60)
    if r.get("ok"):
        lst = read_json("modpack_list.json", {}) or {}
        lst["active"] = target
        write_json("modpack_list.json", lst)
    if not r.get("ok"):
        mark("apply", "fail", r.get("error", ""))
        for k in ("start", "watch"):
            mark(k, "skip", "")
        return
    if target:
        known = _modpack_meta()
        keep = {k: m.get(k) for k in ("slug", "name", "logo", "url", "file_id", "file_name", "mc", "loader", "java", "id",
                                      "removed", "keep", "source", "version", "files", "mods")
                if m.get(k) not in (None, "")}
        keep["slug"] = target
        known[target] = dict(known.get(target, {}), **keep)
        write_json("modpacks.json", known)
        extra = ""
        if preset_get(target):              # made while the PC was off? put it on the server now
            pr = preset_push(cfg, target)
            extra = ", keybind preset" if pr.get("ok") else f", keybind preset NOT copied ({pr.get('error')})"
        mark("apply", "ok", f"{st['name']} ({m.get('file_name') or 'newest version'}), {modpack_memory(cfg)[1]} GB{extra}")
    else:
        mark("apply", "ok", "vanilla world")
    # 5. start
    mark("start", "running")
    # Docker keeps the log of earlier runs of the same server: only read lines that come after this start
    old_lines = set(agent(cfg, "logs", timeout_s=30).get("lines") or [])
    r = agent(cfg, "start", timeout_s=900)          # first run pulls a new image
    if not r.get("ok"):
        mark("start", "fail", r.get("error", ""))
        mark("watch", "skip", "")
        add_event(f"Couldn't start {st['name']}: {r.get('error')}", "bad", "modpack")
        return
    mark("start", "ok")
    st["since"] = r.get("since")     # PC's clock at start: ask only for lines after it (older agents: None)
    # 6. watch the log until it's up (or something needs you)
    mark("watch", "running", "starting...")
    deadline = time.time() + int(cfg.get("modpack_start_timeout_s", 1800) if target else 600)
    seen_running = False
    started = time.time()
    while True:
        if mc_status(cfg["k12_ip"], int(cfg["mc_port"]))["online"]:
            mark("watch", "ok", "running")
            break
        if st.get("since"):
            lg = agent(cfg, "logs", str(int(st["since"])), timeout_s=30)
            lines = lg.get("lines") or []
        else:                                   # older agent: drop lines we saw before this start
            lg = agent(cfg, "logs", timeout_s=30)
            lines = [l for l in lg.get("lines") or [] if l not in old_lines]
        found = scan_modpack_log(lines)
        if found["missing"] or found["problem"]:
            st["missing"], st["problem"] = found["missing"], found["problem"]
            agent(cfg, "stop", timeout_s=180)      # don't let Docker restart it over and over
            if found["missing"]:
                n = len(found["missing"])
                mark("watch", "fail", f"{n} mod{'s' if n > 1 else ''} must be downloaded by hand - see below")
                add_event(f"{st['name']} needs {n} mod{'s' if n > 1 else ''} downloaded by hand: "
                          + ", ".join(x["mod"] for x in found["missing"][:5])
                          + ". See the Deck Panel > Modpacks.", "warn", "modpack")
            else:
                mark("watch", "fail", found["problem"])
                add_event(f"{st['name']} didn't start: {found['problem']}", "bad", "modpack")
            return
        # crashed? (Docker may restart it by itself, so crash lines in the log count too)
        crashed = any(CRASH_MARKERS.search(l) for l in lines) or \
            (not lg.get("running") and (seen_running or time.time() - started > 120))
        if lg.get("running"):
            seen_running = True
        if crashed:
            agent(cfg, "stop", timeout_s=180)
            why = _autofix(cfg, st, target, m, mark, old_lines) if target else \
                "the server stopped - see the Server console for why"
            if why == "retry":            # a mod was taken out and the server restarted: watch again
                old_lines |= set(lines)
                seen_running, started = False, time.time()
                deadline = time.time() + int(cfg.get("modpack_start_timeout_s", 1800))
                time.sleep(10)
                continue
            mark("watch", "fail", why)
            add_event(f"{st['name']} didn't start: {why}", "bad", "modpack")
            return
        if time.time() > deadline:
            mark("watch", "fail", "still not up - it may just be slow; watch the Server console")
            add_event(f"{st['name']} is taking very long to start. Check the Server console.", "warn", "modpack")
            return
        last = next((l for l in reversed(lines) if l.strip()), "")
        last = re.sub(r"^\[[^\]]*\] (\[[^\]]*\]: )?", "", last)[:120]
        mark("watch", "running", last or "starting...")
        time.sleep(10)
    if target:
        agent(cfg, "modpack", "synced", target, timeout_s=30)     # mods are in step again: normal starts
        if st["removed"]:
            mark("watch", "ok", "running - the deck took out " + ", ".join(r["name"] for r in st["removed"]))
        add_event(f"{st['name']} is running! " + how_to_join(cfg, _modpack_meta().get(target, m)), "good", "modpack")
    else:
        add_event("Back on vanilla Minecraft - same address as always.", "good", "modpack")


def _autofix(cfg, st, target, m, mark, old_lines):
    """The pack crashed. If a mod that only belongs on players' computers did it, leave it out and restart.
    -> "retry", or the reason we stopped (for people)."""
    if st["rounds"] >= AUTOFIX_ROUNDS:
        return (f"still crashing after taking out {len(st['removed'])} mod(s) in {AUTOFIX_ROUNDS} tries. "
                "The Server console shows the last error.")
    mark("watch", "running", "it crashed - looking for the mod that did it...")
    if st.get("since"):
        dg = agent(cfg, "modpack", "diagnose", str(int(st["since"])), timeout_s=60)
        lines = list(dg.get("crash") or []) + list(dg.get("lines") or [])
    else:
        dg = agent(cfg, "modpack", "diagnose", timeout_s=60)
        lines = list(dg.get("crash") or []) + [l for l in dg.get("lines") or [] if l not in old_lines]
    removed_ids = [i for r in st["removed"] for i in r.get("mod_ids") or []]
    found = find_client_culprits(lines, removed_ids)
    if not found["clues"]:
        return found["other"] or "it crashed - the Server console shows why."
    meta = _modpack_meta().get(target, m)
    keep = set(meta.get("keep") or [])
    mods = agent(cfg, "modpack", "mods", target, timeout_s=120).get("mods") or []
    try:
        filemap = pack_file_map(meta)
    except RuntimeError as e:
        filemap, map_err = {}, str(e)
    else:
        map_err = ""
    done = {r["id"] for r in st["removed"]}
    culprits = [c for c in resolve_culprits(found["clues"], mods, filemap) if c["id"] not in keep and c["id"] not in done]
    if not culprits:
        names = ", ".join(c["value"] for c in found["clues"][:3])
        kept = " You chose to keep it (Put back)." if any(c["id"] in keep for c in resolve_culprits(found["clues"], mods, filemap)) else ""
        return (f"it crashed because of {names}, a mod meant for players' computers, but the deck couldn't "
                f"leave it out automatically{' (' + map_err + ')' if map_err else ''}.{kept} README > Modpacks.")
    for c in culprits:
        r = agent(cfg, "modpack", "exclude", target, str(c["id"]), timeout_s=30)
        if not r.get("ok"):
            return f"couldn't leave {c['name']} out: {r.get('error')}"
    st["rounds"] += 1
    st["removed"] += culprits
    known = _modpack_meta()
    p = known.setdefault(target, dict(m))
    gone = {r["id"]: r for r in p.get("removed") or []}
    for c in culprits:
        gone[c["id"]] = dict(c, t=time.time())
    p["removed"] = list(gone.values())
    write_json("modpacks.json", known)
    names = ", ".join(f"{c['name']} ({c['why']})" for c in culprits)
    add_event(f"{st['name']} crashed - the deck took out {names} and is restarting it "
              f"(fix {st['rounds']} of {AUTOFIX_ROUNDS}). Players keep it on their own computers.", "warn", "modpack")
    mark("watch", "running", f"took out {', '.join(c['name'] for c in culprits)} - restarting (fix {st['rounds']} of {AUTOFIX_ROUNDS})")
    r = agent(cfg, "start", timeout_s=900)
    if not r.get("ok"):
        return f"couldn't restart it: {r.get('error')}"
    st["since"] = r.get("since")
    return "retry"


def modpack_putback(cfg, slug, project_id, who="panel"):
    """Undo an auto-fix: put a mod back in the pack, and never take it out automatically again."""
    if not SLUG_RE.match(slug or "") or not str(project_id).isdigit():
        return {"ok": False, "error": "bad pack or mod"}
    pid = int(project_id)
    with _ActionLock(f"put a mod back in {slug}", who):
        if not (k12_up(cfg) and k12_agent_ready(cfg)):
            return {"ok": False, "error": "The PC is off - turn it on first."}
        r = agent(cfg, "modpack", "include", slug, str(pid), timeout_s=30)
        if not r.get("ok"):
            return r
        known = _modpack_meta()
        p = known.setdefault(slug, {"slug": slug, "name": slug})
        mod = next((x for x in p.get("removed") or [] if x.get("id") == pid), {"name": f"mod {pid}"})
        p["removed"] = [x for x in p.get("removed") or [] if x.get("id") != pid]
        p["keep"] = sorted(set(p.get("keep") or []) | {pid})
        write_json("modpacks.json", known)
        add_event(f"{mod['name']} is back in {p.get('name') or slug} (asked by {who}). "
                  "It's used from the next start of that pack.", "info", "modpack")
        return {"ok": True, "msg": f"{mod['name']} goes back in on the next start."}


def agent_upload(cfg, args, src, timeout_s=600):
    """Send a file to the PC agent on stdin (backup-get in reverse)."""
    cmd = ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
           "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
           "-o", "LogLevel=ERROR", f"{cfg['k12_user']}@{cfg['k12_ip']}", " ".join(args)]
    with open(src, "rb") as f:
        r = subprocess.run(cmd, stdin=f, capture_output=True, timeout=timeout_s)
    for line in reversed(r.stdout.decode(errors="replace").splitlines()):
        if line.strip().startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"ok": False, "error": (r.stderr.decode(errors="replace").strip() or f"exit {r.returncode}")[-300:]}


def modpack_upload(cfg, slug, name, src, who="panel"):
    """A mod you downloaded by hand -> the pack's downloads folder on the PC. src: file on the Pi."""
    if not SLUG_RE.match(slug or ""):
        return {"ok": False, "error": "bad pack name"}
    if not JAR_RE.match(name or ""):
        return {"ok": False, "error": "Only .jar mod files (plain names) can be uploaded."}
    size = os.path.getsize(src)
    if size < 4 or size > MAX_UPLOAD:
        return {"ok": False, "error": "That file is empty or bigger than 100 MB."}
    with open(src, "rb") as f:
        if f.read(2) != b"PK":
            return {"ok": False, "error": "That isn't a .jar mod file."}
    if not (k12_up(cfg) and cfg.get("k12_user")):
        return {"ok": False, "error": "The PC is off - turn it on first."}
    b64 = base64.urlsafe_b64encode(name.encode()).decode().rstrip("=")     # the name may have spaces
    r = agent_upload(cfg, ["modpack", "upload", slug, b64, str(size)], src)
    if r.get("ok"):
        add_event(f"{name} uploaded to the PC for {slug} (by {who}). Press Try again in Modpacks.", "info", "modpack")
    return r


def modpack_delete(cfg, slug, who="panel"):
    """Delete an installed pack and its world - after backing that world up to the Pi."""
    if not SLUG_RE.match(slug or ""):
        return {"ok": False, "error": "bad pack name"}
    with _ActionLock(f"delete {slug}", who):
        if not (k12_up(cfg) and k12_agent_ready(cfg)):
            return {"ok": False, "error": "The PC is off - turn it on first."}
        cur = agent(cfg, "modpack", "list", timeout_s=30)
        if cur.get("active") == slug:
            return {"ok": False, "error": "That pack is on right now - switch to another one (or Vanilla) first."}
        b = _backup(cfg, slug)
        if not b.get("ok") and not b.get("skip"):
            return {"ok": False, "error": f"Backup failed, so nothing was deleted: {b.get('error')}"}
        r = agent(cfg, "modpack", "delete", slug, timeout_s=120)
        if not r.get("ok"):
            return r
        known = _modpack_meta()
        name = (known.pop(slug, {}) or {}).get("name") or slug
        write_json("modpacks.json", known)
        add_event(f"Deleted the {name} modpack (asked by {who}). Its world backups are kept on the Pi.", "info", "modpack")
        return {"ok": True, "msg": f"Deleted {name}. Its backups are kept."}

# ================================================= keybind presets ===============
# One preset per modpack (docs/keybind-preset-format.md). Made in-game with the keybind mod,
# imported and edited on the Deck Panel, kept on the Pi, put on the server for the mod to hand out.
PRESET_FORMAT = "cyberdeck-keys/1"
PRESET_DIR = os.path.join(STATE_DIR, "presets")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,120}$")
KEY_NAME_RE = re.compile(r"^key\.(keyboard|mouse)\.[a-z0-9.]{1,30}$")
MODIFIERS = ("NONE", "SHIFT", "CONTROL", "ALT")
MAX_PRESET_KEYS = 3000
MAX_PRESET_BYTES = 512 * 1024


def _text(v, n):
    """Display text from a file: no control characters, at most n long."""
    return re.sub(r"[\x00-\x1f\x7f]", "", str(v or ""))[:n]


def clean_preset(data, name=None):
    """Check a preset (dict or JSON text) and keep only what the format allows.
    Raises ValueError with a message for people."""
    if isinstance(data, (bytes, str)):
        if len(data) > MAX_PRESET_BYTES:
            raise ValueError("That preset file is over 512 KB.")
        try:
            data = json.loads(data)
        except ValueError:
            raise ValueError("That isn't a keybind preset file (it's not JSON).")
    if not isinstance(data, dict) or data.get("format") != PRESET_FORMAT:
        raise ValueError("That isn't a keybind preset file from the keybind mod (format cyberdeck-keys/1).")
    keys = data.get("keys")
    if not isinstance(keys, list) or not keys:
        raise ValueError("That preset has no keys in it.")
    if len(keys) > MAX_PRESET_KEYS:
        raise ValueError(f"That preset has more than {MAX_PRESET_KEYS} keys.")
    out, seen = [], set()
    for k in keys:
        if not isinstance(k, dict):
            raise ValueError("A key in that preset is broken.")
        kid, key = str(k.get("id") or ""), str(k.get("key") or "")
        if not KEY_ID_RE.match(kid):
            raise ValueError(f"Odd key name in that preset: {_text(kid, 40)!r}")
        if not KEY_NAME_RE.match(key):
            raise ValueError(f"Odd key for {kid}: {_text(key, 40)!r}")
        if kid in seen:
            continue
        seen.add(kid)
        mod = str(k.get("modifier") or "NONE").upper()
        item = {"id": kid, "key": key, "modifier": mod if mod in MODIFIERS else "NONE"}
        if KEY_NAME_RE.match(str(k.get("default") or "")):
            item["default"] = k["default"]
        for f in ("label", "category"):
            if k.get(f):
                item[f] = _text(k[f], 120)
        out.append(item)
    nm = _text(name or data.get("name") or "Server preset", 60).strip() or "Server preset"
    p = {"format": PRESET_FORMAT, "name": nm, "keys": out}
    if re.match(r"^\d+\.\d+(\.\d+)?$", str(data.get("minecraft") or "")):
        p["minecraft"] = data["minecraft"]
    return p


def _preset_path(slug):
    return os.path.join(PRESET_DIR, f"{slug}.json")


def preset_get(slug):
    if not SLUG_RE.match(slug or ""):
        return None
    try:
        with open(_preset_path(slug), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def preset_push(cfg, slug):
    """Put the pack's preset on the PC (server folder). -> agent reply."""
    p = _preset_path(slug)
    if not os.path.exists(p):
        return agent(cfg, "modpack", "preset", slug, "clear", timeout_s=30)
    return agent_upload(cfg, ["modpack", "preset", slug, str(os.path.getsize(p))], p, timeout_s=60)


def preset_save(cfg, slug, data, who="panel"):
    """Save a pack's preset on the Pi, and on the PC if it's on (else at the next switch to that pack)."""
    if not SLUG_RE.match(slug or ""):
        return {"ok": False, "error": "bad pack name"}
    try:
        p = clean_preset(data)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    os.makedirs(PRESET_DIR, exist_ok=True)
    tmp = _preset_path(slug) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _preset_path(slug))
    on_pc = False
    if k12_up(cfg) and cfg.get("k12_user"):
        r = preset_push(cfg, slug)
        on_pc = bool(r.get("ok"))
    add_event(f"Keybind preset \"{p['name']}\" saved for {slug} ({len(p['keys'])} keys, by {who})."
              + ("" if on_pc else " It goes on the server the next time that pack starts."), "info", "modpack")
    return {"ok": True, "msg": f"Saved ({len(p['keys'])} keys)." + ("" if on_pc else " The PC is off - it's sent when that pack next starts."),
            "on_pc": on_pc, "keys": len(p["keys"])}


def preset_delete(cfg, slug, who="panel"):
    if not SLUG_RE.match(slug or ""):
        return {"ok": False, "error": "bad pack name"}
    try:
        os.remove(_preset_path(slug))
    except FileNotFoundError:
        return {"ok": False, "error": "That pack has no keybind preset."}
    if k12_up(cfg) and cfg.get("k12_user"):
        preset_push(cfg, slug)          # clears it on the server
    add_event(f"Keybind preset removed from {slug} (by {who}).", "info", "modpack")
    return {"ok": True, "msg": "Preset removed. Players keep the copy they already have."}