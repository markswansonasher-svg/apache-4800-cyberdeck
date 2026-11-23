"""
deckhost - runs the deck's Discord bot and watchdog ON THE PC, for decks without a Pi
(or before the Pi is set up). Same code as the Pi (deckbot.py, deckwatch.py, decklib.py),
with the Pi-only parts swapped for local ones:

  * commands go straight to the deck agent on this PC (no SSH)
  * the PC is "always up" (no Wake-on-LAN), Minecraft is checked on 127.0.0.1
  * backups stay in C:\\deck\\backups (made by the agent; nothing is copied anywhere)
  * pack up / travel mode / the Pi's temperature need the Pi: they say so
  * the empty-server idle stop is left to the Deck app (so the two never race)

Installed to C:\\deck\\bot by install-bot.ps1 and started at sign-in (no window).
Settings: C:\\deck\\bot\\config.json (set-discord-bot.ps1). The bot token is the Windows
user variable DISCORD_TOKEN, never in a file. Log: C:\\deck\\bot\\state\\deckhost.log

    pythonw deckhost.py           run (what the sign-in task does)
    python  deckhost.py --check   one watchdog tick + a status summary, no Discord
"""
import json
import msvcrt
import os
import subprocess
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
os.makedirs(STATE, exist_ok=True)
os.environ.setdefault("DECK_STATE", STATE)
os.environ.setdefault("DECK_CONFIG", os.path.join(HERE, "config.json"))
AGENT = os.environ.get("DECK_AGENT", r"C:\deck\agent\deck-agent.ps1")
PC_BACKUPS = os.environ.get("DECK_BACKUPS", r"C:\deck\backups")
CF_SECRET = r"C:\deck\secrets\cf_api_key.secret"
POWERSHELL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), r"System32\WindowsPowerShell\v1.0\powershell.exe")
NO_WINDOW = 0x08000000        # CREATE_NO_WINDOW: no console flashing up for each agent call


# --- fcntl (Linux file locks) -> Windows byte-range locks -------------------------
def _flock(f, op):
    try:
        if op & 8:                                   # LOCK_UN
            f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        if not op & 8:
            raise BlockingIOError("locked")


_fc = types.ModuleType("fcntl")
_fc.LOCK_EX, _fc.LOCK_NB, _fc.LOCK_UN, _fc.flock = 2, 4, 8, _flock
sys.modules["fcntl"] = _fc

sys.path.insert(0, HERE)
import decklib as d  # noqa: E402  (after the fcntl stand-in)


# --- the agent, run locally the same way the Deck app does --------------------------
_pc_travel = [False]


def _agent_env(args):
    env = dict(os.environ)
    env["SSH_ORIGINAL_COMMAND"] = " ".join(args)
    return env


def local_agent(cfg, *args, timeout_s=60):
    try:
        r = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", AGENT],
                           env=_agent_env(args), capture_output=True, text=True, timeout=timeout_s,
                           creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "deck agent timed out"}
    for line in reversed(r.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                res = json.loads(line)
            except ValueError:
                break
            if args and args[0] == "info" and res.get("ok"):
                _pc_travel[0] = bool(res.get("travel"))
            return res
    return {"ok": False, "error": (r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}")[-300:]}


def local_agent_upload(cfg, args, src, timeout_s=600):
    with open(src, "rb") as f:
        r = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", AGENT],
                           env=_agent_env(args), stdin=f, capture_output=True, timeout=timeout_s, creationflags=NO_WINDOW)
    out = r.stdout.decode(errors="replace").strip().splitlines()
    for line in reversed(out):
        if line.strip().startswith("{"):
            try:
                return json.loads(line.strip())
            except ValueError:
                break
    return {"ok": False, "error": (r.stderr.decode(errors="replace").strip() or f"exit {r.returncode}")[-300:]}


def pc_backup(cfg, slug=None):
    """The agent zips the world into C:\\deck\\backups (keeping the newest 10) - that's the backup."""
    d.add_event(f"Backing up {'the ' + slug + ' world' if slug else 'the world'} on this PC...", "info", "backup")
    r = local_agent(cfg, *(["backup", slug] if slug else ["backup"]), timeout_s=900)
    if r.get("ok"):
        d.add_event(f"Backup saved on this PC: {r.get('file')} ({d.human_size(int(r.get('bytes') or 0))}).", "good", "backup")
        return {"ok": True, "file": r.get("file"), "bytes": r.get("bytes")}
    if r.get("code") == "noworld":
        d.add_event("No Minecraft world yet - nothing to back up.", "info", "backup")
        return {"ok": False, "skip": True, "error": "no world yet"}
    d.add_event(f"Backup failed: {r.get('error')}", "bad", "backup")
    return {"ok": False, "error": r.get("error")}


def needs_pi(what):
    def f(*a, **k):
        raise RuntimeError(f"{what} needs the Pi, which isn't set up. Use the Deck app on the PC instead.")
    return f


def pc_cf_key():
    k = os.environ.get("CF_API_KEY", "").strip()
    if k:
        return k
    try:
        with open(CF_SECRET, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def pc_self_test(cfg, full=False):
    res = []
    r = local_agent(cfg, "info", timeout_s=30)
    res.append(d._check("Deck agent", r.get("ok"), "answers" if r.get("ok") else r.get("error", "no answer"),
                        "Run install-pc.cmd again (step: link)."))
    res.append(d._check("Docker", bool(r.get("docker")), "running" if r.get("docker") else "not running",
                        "Start Docker Desktop."))
    t = d.k12_temps(cfg)
    res.append(d._check("Temperatures", t is not None,
                        f"CPU {t.get('cpu', 0):.0f} C" if t and t.get("cpu") is not None else "LibreHardwareMonitor isn't answering",
                        "Start C:\\deck\\lhm\\LibreHardwareMonitor.exe (web server on, port 8085)."))
    mc = d.mc_status(cfg["k12_ip"], int(cfg["mc_port"]))
    res.append(d._check("Minecraft", None, f"up, {mc['players']}/{mc['max']} playing" if mc["online"] else "not running"))
    res.append(d._check("Tailscale", bool(r.get("tailscale_ip")), r.get("tailscale_ip") or "no address",
                        "Open Tailscale and sign in."))
    res.append(d._check("Pi", None, "not set up - the PC runs the bot and watchdog itself"))
    return {"ok": all(x["ok"] is not False for x in res), "results": res, "t": time.time()}


# swap the Pi-only parts
d.agent = local_agent
d.agent_upload = local_agent_upload
d._backup = pc_backup
d.BACKUP_DIR = PC_BACKUPS                       # "last backup" age = the PC's own backups
d.k12_up = lambda cfg: True                     # this IS the PC
d.wake_k12 = lambda cfg, who="panel": True
d.pi_temp = lambda: None
d.travel_on = lambda: _pc_travel[0]             # never "corrects" the PC's travel mode
d.net = needs_pi("Wi-Fi and travel mode")
d.pack_up = needs_pi("Pack up")
d.cf_api_key = pc_cf_key
d.self_test = pc_self_test

# settings for running on the PC (the file's values win, except where the PC must differ)
_load = d.load_config


def pc_config():
    c = _load()
    c["k12_ip"] = "127.0.0.1"
    c.setdefault("k12_user", os.environ.get("USERNAME", "deck"))
    c["k12_user"] = c["k12_user"] or os.environ.get("USERNAME", "deck")
    c["idle_stop_minutes"] = 0                  # the Deck app does the idle stop
    return c


d.load_config = pc_config

import deckwatch  # noqa: E402  (uses the patched decklib)


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    try:
        p = os.path.join(STATE, "deckhost.log")
        if os.path.exists(p) and os.path.getsize(p) > 2 * 2**20:
            os.replace(p, p + ".old")
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    if sys.stdout:
        try:
            print(line, end="", flush=True)
        except Exception:
            pass


def single_instance():
    f = open(os.path.join(STATE, "deckhost.lock"), "w")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        log("Another deckhost is already running - exiting.")
        sys.exit(0)
    return f


def user_env(name):
    """A Windows user variable, fresh from the registry (set-*.ps1 save them there; a process
    started before that wouldn't see them in its own environment)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except OSError:
        return os.environ.get(name, "").strip()


def token():
    return user_env("DISCORD_TOKEN")


def main():
    if "--check" in sys.argv:
        w = deckwatch.Watch()
        w.tick()
        s = d.read_json("state.json", {})
        print(json.dumps({"mc": s.get("mc"), "temps": s["k12"]["temps"], "join": s.get("join"),
                          "tailscale_ip": s["k12"]["tailscale_ip"]}, indent=2))
        print(json.dumps(pc_self_test(d.load_config()), indent=2))
        return
    if sys.stdout is None:                       # pythonw: no console - log to the file only
        sys.stdout = sys.stderr = open(os.path.join(STATE, "deckhost.out"), "a", encoding="utf-8", buffering=1)
    lock = single_instance()  # noqa: F841 (held for the life of the process)
    log("deckhost starting (PC mode: bot + watchdog, no Pi)")
    threading.Thread(target=deckwatch.Watch().run, daemon=True, name="deckwatch").start()
    tok = token()
    if not tok:
        log("No DISCORD_TOKEN - only the watchdog runs. Set the bot up with set-discord-bot.ps1.")
        while True:
            time.sleep(3600)
    os.environ["DISCORD_TOKEN"] = tok
    ts = user_env("TS_API_KEY")                  # /mc invite's Tailscale link (set-tailscale-key.ps1); optional
    if ts:
        os.environ["TS_API_KEY"] = ts
    else:
        os.environ.pop("TS_API_KEY", None)
    import deckbot  # noqa: E402  (reads DISCORD_TOKEN and TS_API_KEY when imported)
    deckbot.TOKEN, deckbot.TS_API_KEY = tok, ts
    while True:
        try:
            deckbot.bot.run(tok, log_handler=None)
            break
        except Exception as e:                   # bad token / no internet: log and retry later
            log(f"Discord connection failed: {e!r} - retrying in 60 s")
            time.sleep(60)
            import importlib
            deckbot = importlib.reload(deckbot)


if __name__ == "__main__":
    main()
