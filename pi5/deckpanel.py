#!/usr/bin/env python3
"""
Deck Panel - phone-friendly control page for the Apache 4800 cyberdeck.

Runs on the Raspberry Pi 5. Open it over Tailscale or from the laptop:
    http://deckpi:8080      or      http://10.48.0.2:8080

  * Minecraft: start (wakes the PC if needed), stop, stop + shut down
  * Who's playing, join address, temperatures with a 24 h graph
  * Recent events (same ones the Discord bot posts)
  * Wi-Fi + travel mode (share the Pi's Wi-Fi with the case network)

Readings come from deckwatch (state.json); this page only displays them and
runs actions. Standard library only.

Command line:
    deckpanel.py --wake      send the wake-up packet and exit
    deckpanel.py --status    print the latest state as JSON
    deckpanel.py --serve     run the web page (what the service does)
"""
import ipaddress
import json
import os
import shutil
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import decklib as d

HERE = os.path.dirname(os.path.abspath(__file__))
PUBKEY = d.SSH_KEY + ".pub"
_net_cache = {"t": 0, "data": None}
_net_lock = threading.Lock()
_check_running = threading.Event()
_wl_cache = {"t": 0, "data": None}
_wl_lock = threading.Lock()
_con_cache = {"t": 0, "data": None}
_con_lock = threading.Lock()
_mod_cache = {"t": 0, "data": None}
_mod_lock = threading.Lock()
_perf_cache = {"t": 0, "data": None}
_perf_lock = threading.Lock()
_previews = {}            # link -> (time, preview): Install reuses what you just saw
PREVIEW_KEEP_S = 600
# files the phone / PC app fetch to show the deck as an app with its own icon
STATIC = {"/manifest.json": ("manifest.json", "application/manifest+json"),
          "/icon.svg": ("icon.svg", "image/svg+xml"),
          "/icon-180.png": ("icon-180.png", "image/png"),
          "/icon-512.png": ("icon-512.png", "image/png")}


def net_status(max_age=15):
    with _net_lock:
        if time.time() - _net_cache["t"] > max_age or _net_cache["data"] is None:
            _net_cache["data"] = d.net("status", timeout_s=20)
            _net_cache["t"] = time.time()
        return _net_cache["data"]


def where(addr, cfg):
    """Name the device a button was pressed on, for Events: 'the PC', 'the laptop', or its address."""
    ip = addr.split("%")[0].replace("::ffff:", "")
    names = {cfg.get("k12_ip"): "the PC", "10.48.0.20": "the laptop",
             "127.0.0.1": "the Pi", "::1": "the Pi", "10.48.0.2": "the Pi"}
    return names.get(ip) or ip


def preview(link):
    """CurseForge preview, remembered for 10 min so Install uses exactly what was shown."""
    key = d.parse_modpack_link(link)          # ValueError for anything that isn't a pack link
    hit = _previews.get(key)
    if hit and time.time() - hit[0] < PREVIEW_KEEP_S:
        return hit[1]
    p = d.modpack_preview(link)
    if len(_previews) > 50:
        _previews.clear()
    _previews[key] = (time.time(), p)
    return p


def background(fn, *args, **kw):
    def run():
        try:
            fn(*args, **kw)
        except d.Busy as e:
            d.add_event(str(e).capitalize() + ".", "warn", "mc")
        except Exception as e:
            d.add_event(f"Error: {e}", "bad", "panel")
    threading.Thread(target=run, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = "DeckPanel/2.0"
    timeout = 60   # a phone that drops off mid-request mustn't hold a thread forever

    def _allowed(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0].split("%")[0])
            if ip.version == 6 and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
        except ValueError:
            return False
        for net in self.server.cfg["allowed_networks"]:
            try:
                if ip in ipaddress.ip_network(net, strict=False):
                    return True
            except ValueError:
                continue
        return False

    def _send(self, code, body, ctype="application/json"):
        if not isinstance(body, (str, bytes)):
            body = json.dumps(body)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "SAMEORIGIN")   # other sites can't embed it
        self.end_headers()
        self.wfile.write(data)

    def _send_backup(self, name):
        """Stream a backup zip (could be hundreds of MB) without loading it into memory."""
        if not d.BACKUP_RE.match(name):
            return self._send(404, {"error": "no such backup"})
        p = os.path.join(d.BACKUP_DIR, name)
        try:
            size = os.path.getsize(p)
            f = open(p, "rb")
        except OSError:
            return self._send(404, {"error": "no such backup"})
        with f:
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.end_headers()
            try:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass   # download cancelled on the phone

    def _body(self, limit=4096):
        try:
            n = max(0, min(int(self.headers.get("Content-Length") or 0), limit))
            b = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    def do_GET(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        try:
            return self._get()
        except ConnectionError:      # the phone/browser went away mid-answer (any kind)
            pass
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)})

    def _get(self):
        cfg = self.server.cfg = d.load_config()
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "panel.html"), "rb") as f:
                return self._send(200, f.read(), "text/html")
        if path in STATIC:
            name, ctype = STATIC[path]
            try:
                with open(os.path.join(HERE, name), "rb") as f:
                    return self._send(200, f.read(), ctype)
            except FileNotFoundError:
                return self._send(404, {"error": "not found"})
        if path == "/api/console":
            # cached 3 s: the console card polls, and each miss is an SSH call to the PC
            with _con_lock:
                if time.time() - _con_cache["t"] > 3 or _con_cache["data"] is None:
                    _con_cache["data"], _con_cache["t"] = d.console_logs(cfg), time.time()
                data = _con_cache["data"]
            return self._send(200, data)
        if path == "/api/status":
            st = d.read_json("state.json", {}) or {}
            return self._send(200, {
                "state": st, "stale": time.time() - st.get("t", 0) > 120,
                "action": d.current_action(),
                "events": list(reversed(d.read_events(0, limit=15))),
                "mac_configured": bool(cfg.get("k12_mac")), "linked": bool(cfg.get("k12_user")),
                "idle_stop_minutes": cfg.get("idle_stop_minutes"),
                "modpack": d.modpack_active_name(),
            })
        if path == "/api/config":
            return self._send(200, d.net("config-get", timeout_s=20))
        if path == "/api/server/settings":
            return self._send(200, d.server_settings(cfg))
        if path == "/api/perf":
            # cached 5 s: each miss runs docker stats + a console command on the PC
            with _perf_lock:
                if time.time() - _perf_cache["t"] > 5 or _perf_cache["data"] is None:
                    _perf_cache["data"], _perf_cache["t"] = d.server_perf(cfg), time.time()
                data = _perf_cache["data"]
            return self._send(200, data)
        if path == "/api/history":
            return self._send(200, d.read_json("history.json", []) or [])
        if path == "/api/net":
            # ?fresh=1: the page is waiting for a hotel login to finish - check sooner
            return self._send(200, net_status(3 if "fresh=1" in self.path else 15))
        if path == "/api/wifi/scan":
            return self._send(200, d.net("scan", timeout_s=45))
        if path == "/api/whitelist":
            # cached 20 s: each open panel polls this, and every miss is an SSH call to the PC
            with _wl_lock:
                if time.time() - _wl_cache["t"] > 20 or "fresh=1" in self.path:
                    _wl_cache["data"], _wl_cache["t"] = d.whitelist_list(cfg), time.time()
                data = _wl_cache["data"]
            return self._send(200, data)
        if path == "/api/backups":
            return self._send(200, {"backups": d.list_backups(), "age_h": d.backup_age_h()})
        if path.startswith("/api/backups/"):
            return self._send_backup(path.rsplit("/", 1)[-1])
        if path == "/api/pack":
            return self._send(200, {"pack": d.pack_state(), "checklist": cfg.get("pack_checklist") or []})
        if path == "/api/check":
            return self._send(200, {"result": d.read_json("selftest.json"), "running": _check_running.is_set()})
        if path in ("/api/preset", "/api/preset/file"):
            from urllib.parse import parse_qs, urlsplit
            slug = (parse_qs(urlsplit(self.path).query).get("slug") or [""])[0]
            p = d.preset_get(slug)
            if path == "/api/preset":
                return self._send(200, {"ok": True, "slug": slug, "preset": p})
            if not p:
                return self._send(404, {"error": "no preset"})
            data = json.dumps(p, ensure_ascii=False, indent=1).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{slug}-keys.json"')
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/modpack":
            # cached 20 s: each miss is an SSH call to the PC
            job = d.modpack_job()
            with _mod_lock:
                if (time.time() - _mod_cache["t"] > 20 or "fresh=1" in self.path or _mod_cache["data"] is None
                        or (job or {}).get("ended", 0) > _mod_cache["t"]):     # a switch just finished
                    _mod_cache["data"], _mod_cache["t"] = d.modpack_list(cfg), time.time()
                data = dict(_mod_cache["data"])
            data["job"] = job
            return self._send(200, data)
        if path == "/api/pubkey":
            try:
                with open(PUBKEY, "r") as f:
                    return self._send(200, f.read(), "text/plain")
            except FileNotFoundError:
                return self._send(404, {"error": "no key yet - run setup-pi.sh"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"error": "forbidden"})
        # A custom header can't be sent cross-site without CORS, which we never allow,
        # so other web pages open in your browser can't press these buttons.
        if self.headers.get("X-Deck") != "1":
            return self._send(403, {"error": "missing X-Deck header"})
        cfg = self.server.cfg = d.load_config()
        path = self.path
        who = "Deck Panel on " + where(self.client_address[0], cfg)
        try:
            if path == "/api/wake":
                d.wake_k12(cfg, who)
                d.add_event("Wake-up sent to the PC.", "info", "k12")
                return self._send(200, {"ok": True})
            if path in ("/api/mc/start", "/api/mc/stop", "/api/mc/off", "/api/mc/restart"):
                if d.current_action():
                    return self._send(409, {"ok": False, "error": "Already busy - see Events."})
                if path.endswith("start") and not path.endswith("restart"):
                    background(d.start_minecraft, cfg, who)
                elif path.endswith("restart"):
                    background(d.restart_minecraft, cfg, who)
                else:
                    background(d.stop_minecraft, cfg, who, power_off=path.endswith("off"))
                return self._send(200, {"ok": True})
            if path == "/api/whitelist":
                b = self._body()
                r = d.whitelist_change(cfg, b.get("action"), b.get("name"), who)
                _wl_cache["t"] = 0
                return self._send(200, r)
            if path == "/api/console":
                r = d.console_cmd(cfg, self._body().get("cmd"), who)
                _con_cache["t"] = 0
                return self._send(200, r)
            if path in ("/api/backup", "/api/pack"):
                if d.current_action():
                    return self._send(409, {"ok": False, "error": "Already busy - see Events."})
                if path == "/api/backup":
                    background(d.backup_now, cfg, who)
                else:
                    background(d.pack_up, cfg, who, pi_off=bool(self._body().get("pi_off")))
                return self._send(200, {"ok": True})
            if path.split("?")[0] == "/api/modpack/upload":
                return self._upload(cfg, who)
            if path in ("/api/preset/save", "/api/preset/delete"):
                b = self._body(d.MAX_PRESET_BYTES + 4096)
                slug = str(b.get("slug") or "")
                if path.endswith("delete"):
                    return self._send(200, d.preset_delete(cfg, slug, who))
                p = b.get("preset")
                if not isinstance(p, dict):
                    return self._send(200, {"ok": False, "error": "No preset in the request."})
                return self._send(200, d.preset_save(cfg, slug, dict(p, name=b.get("name") or p.get("name")), who))
            if path.startswith("/api/modpack/"):
                # an import carries the pack's whole manifest.json (one line per mod)
                return self._modpack(cfg, path.rsplit("/", 1)[-1], self._body(4 * 2**20), who)
            if path == "/api/pack/clear":
                d.remove("pack.json")
                return self._send(200, {"ok": True})
            if path == "/api/check":
                if _check_running.is_set():
                    return self._send(409, {"ok": False, "error": "A check is already running."})
                full = bool(self._body().get("full"))

                def run_check():
                    _check_running.set()
                    try:
                        d.self_test(cfg, full)
                    finally:
                        _check_running.clear()
                threading.Thread(target=run_check, daemon=True).start()
                return self._send(200, {"ok": True})
            if path == "/api/pi/poweroff":
                r = d.net("poweroff")
                if r.get("ok"):
                    d.add_event(f"Pi shutting down (asked by {who}). Safe to unplug when its green light "
                                "stops flashing.", "info", "pack")
                return self._send(200, r)
            if path == "/api/pi/reboot":
                r = d.net("reboot")
                if r.get("ok"):
                    d.add_event(f"Pi restarting (asked by {who}). The panel is back in about a minute.", "info", "pack")
                return self._send(200, r)
            if path == "/api/config":
                changes = self._body().get("changes")
                r = d.net("config-set", payload={"changes": changes}, timeout_s=20)
                if r.get("ok"):
                    d.add_event("Deck settings changed (by " + who + "): "
                                + ", ".join(f"{k} = {v}" for k, v in (changes or {}).items()) + ".", "info", "settings")
                return self._send(200, r)
            if path == "/api/server/settings":
                b = self._body()
                r = d.server_setting_set(cfg, b.get("kind"), b.get("key"), b.get("value"), who)
                return self._send(200, r)
            if path == "/api/wifi/connect":
                b = self._body()
                r = d.net("connect", payload={"ssid": b.get("ssid"), "password": b.get("password", "")})
                _net_cache["t"] = 0
                return self._send(200, r)
            if path == "/api/wifi/forget":
                r = d.net("forget", payload={"ssid": self._body().get("ssid")})
                _net_cache["t"] = 0
                return self._send(200, r)
            if path == "/api/travel":
                on = bool(self._body().get("on"))
                r = d.net("travel", "on" if on else "off")
                if r.get("ok"):
                    if d.k12_up(cfg) and cfg.get("k12_user"):
                        k = d.agent(cfg, "travel", "on" if on else "off", timeout_s=40)
                    else:
                        k = {"ok": True, "msg": "PC is off - it switches when it next starts"}
                    r["k12"] = k
                    d.add_event(f"Travel mode {'ON' if on else 'OFF'}"
                                + ("" if k.get("ok") else f" (PC: {k.get('error')})") + ".", "info", "travel")
                _net_cache["t"] = 0
                return self._send(200, r)
        except Exception as e:
            return self._send(500, {"ok": False, "error": str(e)})
        return self._send(404, {"error": "not found"})

    def _modpack(self, cfg, what, b, who):
        """POST /api/modpack/preview|install|import-check|import|switch|delete|putback|clear"""
        if what in ("import-check", "import"):
            # a CurseForge "Export profile" zip: the Deck app sends its manifest.json and (for import)
            # has already copied the zip to the PC's game-servers\imports\<slug>.zip
            try:
                meta = d.import_meta(b.get("manifest"))
            except ValueError as e:
                return self._send(200, {"ok": False, "error": str(e)})
            if what == "import-check" or not meta.get("can_install"):
                if what == "import":
                    return self._send(200, {"ok": False, "error": meta.get("warning") or "This pack can't be installed."})
                return self._send(200, dict(meta, files=None))
            if d.current_action():
                return self._send(409, {"ok": False, "error": "Already busy - see Events."})
            _mod_cache["t"] = 0
            background(d.modpack_change, cfg, meta["slug"], who, meta=meta)
            return self._send(200, {"ok": True, "slug": meta["slug"], "msg": f"Importing {meta['name']}..."})
        if what == "preview":
            try:
                return self._send(200, preview(b.get("link")))
            except (ValueError, RuntimeError) as e:
                return self._send(200, {"ok": False, "error": str(e)})
        if what == "clear":
            d.remove("modpack_job.json")
            return self._send(200, {"ok": True})
        if what == "putback":
            slug, pid = str(b.get("slug") or ""), str(b.get("id") or "")
            if not d.SLUG_RE.match(slug) or not pid.isdigit():
                return self._send(200, {"ok": False, "error": "bad pack or mod"})
            if d.current_action():
                return self._send(409, {"ok": False, "error": "Already busy - see Events."})
            _mod_cache["t"] = 0
            return self._send(200, d.modpack_putback(cfg, slug, int(pid), who))
        if what not in ("install", "switch", "delete"):
            return self._send(404, {"error": "not found"})
        if d.current_action():
            return self._send(409, {"ok": False, "error": "Already busy - see Events."})
        _mod_cache["t"] = 0
        if what == "install":
            try:
                p = preview(b.get("link"))
            except (ValueError, RuntimeError) as e:
                return self._send(200, {"ok": False, "error": str(e)})
            if not p.get("can_install"):
                return self._send(200, {"ok": False, "error": p.get("warning") or "This pack can't be installed."})
            background(d.modpack_change, cfg, p["slug"], who, meta=p)
            return self._send(200, {"ok": True, "msg": f"Installing {p['name']}..."})
        slug = str(b.get("slug") or "")
        if slug and not d.SLUG_RE.match(slug):
            return self._send(200, {"ok": False, "error": "bad pack name"})
        if what == "switch":
            background(d.modpack_change, cfg, slug, who)
            return self._send(200, {"ok": True})
        if not slug:
            return self._send(200, {"ok": False, "error": "Vanilla can't be deleted."})
        def delete():
            r = d.modpack_delete(cfg, slug, who)     # success posts its own event
            if not r.get("ok"):
                d.add_event(f"Couldn't delete {slug}: {r.get('error')}", "warn", "modpack")
            _mod_cache["t"] = 0
        background(delete)
        return self._send(200, {"ok": True, "msg": f"Backing up {slug}, then deleting it..."})

    def _upload(self, cfg, who):
        """POST /api/modpack/upload?slug=<pack>&name=<file.jar>  body: the .jar itself (max 100 MB)."""
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(self.path).query)
        slug, name = (q.get("slug") or [""])[0], (q.get("name") or [""])[0]
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if not d.SLUG_RE.match(slug) or not d.JAR_RE.match(name):
            return self._send(200, {"ok": False, "error": "Only .jar mod files (plain names) can be uploaded."})
        if n < 4 or n > d.MAX_UPLOAD:
            return self._send(200, {"ok": False, "error": "That file is empty or bigger than 100 MB."})
        up = os.path.join(d.STATE_DIR, "uploads")
        os.makedirs(up, exist_ok=True)
        tmp = os.path.join(up, f"up-{threading.get_ident()}.jar")
        try:
            with open(tmp, "wb") as f:
                left = n
                while left:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        return self._send(400, {"ok": False, "error": "Upload cut off - try again."})
                    f.write(chunk)
                    left -= len(chunk)
            return self._send(200, d.modpack_upload(cfg, slug, name, tmp, who))
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def log_message(self, fmt, *args):
        if args and any(p in str(args[0]) for p in ("GET /api/status", "GET /api/console", "GET /api/pack",
                                                       "GET /api/check", "GET /api/backups ", "GET /api/modpack",
                                                       "GET /api/perf", "GET /api/whitelist")):
            return   # the page polls these every few seconds
        sys.stderr.write("%s %s\n" % (self.client_address[0], fmt % args))


def serve(cfg):
    class DualStackServer(ThreadingHTTPServer):
        address_family = socket.AF_INET6
        daemon_threads = True

        def server_bind(self):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    def boot_check():
        # quick deck check once after start-up, so Discord/panel show "all good" or
        # what's wrong when you unpack. Runs here because this service may use deck-net.
        time.sleep(int(os.environ.get("DECK_BOOT_CHECK_DELAY", "25")))
        _check_running.set()
        try:
            d.self_test(d.load_config(), full=False)
        except Exception as e:
            print(f"boot check failed: {e!r}", file=sys.stderr, flush=True)
        finally:
            _check_running.clear()
    threading.Thread(target=boot_check, daemon=True).start()

    httpd = DualStackServer(("::", int(cfg["listen_port"])), Handler)
    httpd.cfg = cfg
    print(f"Deck Panel on port {cfg['listen_port']}", flush=True)
    httpd.serve_forever()


def main(argv):
    cfg = d.load_config()
    if "--wake" in argv:
        if not cfg.get("k12_mac"):
            print("PC MAC not set. Run: sudo deck-set-mac", file=sys.stderr)
            return 1
        d.send_magic_packet(cfg["k12_mac"], cfg["broadcast"])
        print(f"Wake-up sent to {cfg['k12_mac']} via {cfg['broadcast']}")
        return 0
    if "--status" in argv:
        st = d.read_json("state.json")
        if st is None:
            st = {"k12_up": d.k12_up(cfg), "note": "deckwatch hasn't written state yet"}
        print(json.dumps(st, indent=2))
        return 0
    serve(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
