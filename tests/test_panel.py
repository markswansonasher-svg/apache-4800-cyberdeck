"""Deck Panel web server: requests from a real HTTP client against pretend hardware."""
import sys, types, os, tempfile, json, threading, http.client
from http.server import ThreadingHTTPServer
fc = types.ModuleType("fcntl"); fc.LOCK_EX = 2; fc.LOCK_NB = 4; fc.LOCK_UN = 8; fc.flock = lambda f, o: None; sys.modules["fcntl"] = fc
os.environ["DECK_STATE"] = tempfile.mkdtemp(); os.environ["DECK_CONFIG"] = os.path.join(os.environ["DECK_STATE"], "c.json")
json.dump({"k12_user": "x", "k12_mac": "aa:bb:cc:dd:ee:ff", "allowed_networks": ["127.0.0.0/8"]}, open(os.environ["DECK_CONFIG"], "w"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi5"))
import decklib as d
import deckpanel as p

fails = 0
def check(n, c):
    global fails; print(("PASS " if c else "FAIL ") + n); fails += not c

seen = {}
d.whitelist_change = lambda cfg, action, name, who: (seen.update(action=action, name=name), {"ok": True, "msg": "ok"})[1]
power = {"r": {"ok": False, "error": "sudo: a password is required"}}
d.net = lambda cmd, *a, **k: power["r"] if cmd == "poweroff" else {"ok": True}

srv = ThreadingHTTPServer(("127.0.0.1", 0), p.Handler)
srv.cfg = d.load_config()
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]

def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"X-Deck": "1"} if method == "POST" else {}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse(); data = r.read(); c.close()
    try:
        return r.status, json.loads(data)
    except ValueError:
        return r.status, data.decode("utf-8", "replace")

s, html = req("GET", "/")
check("page loads with the status pill + section links", s == 200 and 'id="health"' in html and 'href="#c-pack"' in html)
check("every nav link has a card to jump to",
      all(f'id="{a}"' in html for a in ["c-mc", "c-hw", "c-pl", "c-net", "c-pack", "c-bk", "c-ev"]))
s, _ = req("POST", "/api/wake", headers={"X-Deck": "0"})
check("POST without the X-Deck header is refused", s == 403)
s, r = req("POST", "/api/whitelist", body=b"{}", headers={"Content-Length": "abc"})
check(f"nonsense Content-Length -> treated as empty, not a crash ({s})", s == 200 and seen.get("action") is None)
s, r = req("POST", "/api/whitelist", body=b'["add","Steve"]')
check(f"JSON list instead of object -> ignored, not a crash ({s})", s == 200 and seen.get("name") is None)
s, r = req("POST", "/api/whitelist", body=b'{"action":"add","name":"Steve"}')
check("normal whitelist add reaches decklib", s == 200 and seen == {"action": "add", "name": "Steve"})

real = d.read_json
d.read_json = lambda *a, **k: (_ for _ in ()).throw(OSError("disk on fire"))
s, r = req("GET", "/api/status")
d.read_json = real
check(f"a broken state file gives a 500 with the reason, not a dropped connection ({s})",
      s == 500 and "disk on fire" in r.get("error", ""))

before = len(d.read_events(0, limit=100))
s, r = req("POST", "/api/pi/poweroff")
check("Pi poweroff that fails -> error shown, no 'Pi shutting down' event",
      r.get("ok") is False and len(d.read_events(0, limit=100)) == before)
power["r"] = {"ok": True}
s, r = req("POST", "/api/pi/poweroff")
ev = d.read_events(0, limit=100)
check("Pi poweroff that works -> event posted", r.get("ok") and len(ev) == before + 1 and "shutting down" in ev[-1]["text"])

s, _ = req("GET", "/api/backups/..%2f..%2fetc%2fpasswd")
check("backup download refuses anything that isn't a backup file name", s == 404)
os.makedirs(d.BACKUP_DIR, exist_ok=True)
with open(os.path.join(d.BACKUP_DIR, "minecraft-20261119-1200.zip"), "wb") as f:
    f.write(b"PK" + b"x" * 5000)
s, body = req("GET", "/api/backups/minecraft-20261119-1200.zip")
check("backup download streams the whole file", s == 200 and len(body) == 5002)
check("handler has a socket timeout (dropped phones don't pin threads)", (p.Handler.timeout or 0) > 0)

# ---- app files (phone home screen + K12 app)
for f, ct in [("/manifest.json", "manifest"), ("/icon.svg", "svg"), ("/icon-180.png", "png"), ("/icon-512.png", "png")]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10); c.request("GET", f); r = c.getresponse(); body = r.read(); c.close()
    check(f"{f} served as {ct}", r.status == 200 and ct in r.getheader("Content-Type", "") and len(body) > 100)
check("manifest names the app and its icons", json.loads(open(os.path.join(p.HERE, "manifest.json")).read())["short_name"] == "Deck")

# ---- server console
up = {"k12": False}
sent = []
d.k12_up = lambda cfg: up["k12"]
def fake_agent(cfg, *a, timeout_s=60):
    sent.append(a)
    if a[0] == "logs":
        return {"ok": True, "running": True, "lines": ["[12:00:00] [Server thread/INFO]: Done"]}
    if a[0] == "cmd":
        return {"ok": True, "msg": "Set the time to 1000"}
    return {"ok": False, "error": "?"}
d.agent = fake_agent
s, r = req("GET", "/api/console")
check("console while the K12 is off: no SSH call, says K12 off", r.get("k12") is False and r["lines"] == [] and not sent)
up["k12"] = True; p._con_cache["t"] = 0
s, r = req("GET", "/api/console")
check("console with the K12 on: log lines come back", r.get("running") and len(r["lines"]) == 1)
req("GET", "/api/console")
check("console log is cached (2 page polls -> 1 SSH call)", sum(1 for a in sent if a[0] == "logs") == 1)
s, r = req("POST", "/api/console", body=json.dumps({"cmd": "/time  set   day"}).encode())
check(f"command sent tidy, leading / dropped {sent[-1]}", r.get("ok") and sent[-1] == ("cmd", "time set day"))
ev = d.read_events(0, limit=100)
check(f"console command is logged in Events, naming the device ({ev[-1]['text']})",
      "Console: /time set day (by Deck Panel on the Pi)" in ev[-1]["text"])
c = {"k12_ip": "10.48.0.10"}
check("button presses name the device: PC / laptop / Tailscale phone",
      p.where("10.48.0.10", c) == "the PC" and p.where("::ffff:10.48.0.20", c) == "the laptop"
      and p.where("100.88.1.2", c) == "100.88.1.2")
n = len(sent)
for bad, why in [('say "hi"', "quotes"), ("say hi; shutdown", "semicolon"), ("say $(x)", "dollar"), ("say a | b", "pipe"),
                 ("stop", "stop - use the button"), ("Whitelist add Bob", "whitelist - use Players"), ("", "empty")]:
    s, r = req("POST", "/api/console", body=json.dumps({"cmd": bad}).encode())
    check(f"console refuses {why}", r.get("ok") is False and r.get("error"))
check("refused commands never reach the K12", len(sent) == n)
s, r = req("POST", "/api/console", body=json.dumps({"cmd": "tp @a[distance=..5] 0 64 0"}).encode())
check("target selectors with [ ] = . are allowed", r.get("ok"))

# ---- modpacks
check("Modpacks card + jump link", 'id="c-mod"' in html and 'href="#c-mod"' in html)
started = []
p.background = lambda fn, *a, **k: started.append((fn.__name__, a, k))
cf_calls = []
def fake_preview(link):
    cf_calls.append(link)
    slug, fid = d.parse_modpack_link(link)
    return {"ok": True, "slug": slug, "name": "Cozy Pack", "file_id": fid or 5000, "java": "java17", "mc": "1.20.1",
            "loader": "Quilt" if slug == "quilty" else "Fabric", "can_install": slug != "quilty",
            "warning": "Quilt packs aren't supported" if slug == "quilty" else ""}
d.modpack_preview = fake_preview
s, _ = req("POST", "/api/modpack/install", body=b'{"link":"cozy-pack"}', headers={"X-Deck": "0"})
check("modpack POST without X-Deck refused", s == 403 and not started)
s, r = req("POST", "/api/modpack/preview", body=json.dumps({"link": "https://evil.example/x"}).encode())
check(f"preview of a non-CurseForge link -> message, no CurseForge call ({r.get('error', '')[:40]})", r.get("ok") is False and not cf_calls)
s, r = req("POST", "/api/modpack/preview", body=json.dumps({"link": "https://www.curseforge.com/minecraft/modpacks/cozy-pack"}).encode())
check("preview works", r.get("ok") and r["name"] == "Cozy Pack")
s, r = req("POST", "/api/modpack/install", body=json.dumps({"link": "https://www.curseforge.com/minecraft/modpacks/cozy-pack"}).encode())
check("install reuses the preview just shown (one CurseForge lookup)", r.get("ok") and len(cf_calls) == 1)
check("install starts the switch with the pack's details from the Pi, not the browser",
      started and started[-1][0] == "modpack_change" and started[-1][1][1] == "cozy-pack" and started[-1][2]["meta"]["java"] == "java17")
n = len(started)
s, r = req("POST", "/api/modpack/install", body=json.dumps({"link": "quilty"}).encode())
check("a pack that can't be installed is refused with the reason", r.get("ok") is False and "Quilt" in r["error"] and len(started) == n)
for bad in ['{"slug":"../x"}', '{"slug":"Cozy"}', '{"slug":"a b"}']:
    s, r = req("POST", "/api/modpack/switch", body=bad.encode())
    check(f"switch refuses {bad}", r.get("ok") is False and len(started) == n)
s, r = req("POST", "/api/modpack/switch", body=b'{"slug":""}')
check("switch to vanilla accepted", r.get("ok") and started[-1][1][1] == "")
s, r = req("POST", "/api/modpack/delete", body=b'{"slug":""}')
check("vanilla can't be deleted", r.get("ok") is False)
d.write_json("action.json", {"what": "pack up", "by": "x", "since": __import__("time").time()})
s, r = req("POST", "/api/modpack/switch", body=b'{"slug":"cozy-pack"}')
check("busy deck -> switch refused (409)", s == 409)
d.remove("action.json")
s, r = req("POST", "/api/modpack/whatever", body=b'{}')
check("unknown modpack action -> 404", s == 404)
# uploads of hand-downloaded mods
got = []
d.modpack_upload = lambda cfg, slug, name, src, who: (got.append((slug, name, open(src, "rb").read())), {"ok": True, "msg": "saved"})[1]
body = b"PK\x03\x04" + os.urandom(3 * 2**20)        # bigger than one read
s, r = req("POST", "/api/modpack/upload?slug=cozy-pack&name=Fancy%20Furniture%20%5B3.2%5D.jar", body=body)
check("upload: the whole file reaches decklib with the expected name", r.get("ok") and got[-1][:2] == ("cozy-pack", "Fancy Furniture [3.2].jar") and got[-1][2] == body)
check("upload: the temporary copy on the Pi is removed", os.listdir(os.path.join(d.STATE_DIR, "uploads")) == [])
n = len(got)
for q, why in [("slug=../x&name=a.jar", "bad pack"), ("slug=cozy-pack&name=a.exe", "not a jar"), ("slug=cozy-pack&name=..%2Fa.jar", "path"),
               ("slug=cozy-pack", "no name")]:
    s, r = req("POST", "/api/modpack/upload?" + q, body=b"PK123")
    check(f"upload refused: {why}", r.get("ok") is False)
s, r = req("POST", "/api/modpack/upload?slug=cozy-pack&name=a.jar", body=b"PK123", headers={"X-Deck": "0"})
check("upload without X-Deck refused", s == 403 and len(got) == n)
c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
c.putrequest("POST", "/api/modpack/upload?slug=cozy-pack&name=a.jar"); c.putheader("X-Deck", "1"); c.putheader("Content-Length", str(200 * 2**20)); c.endheaders()
rr = c.getresponse(); big = json.loads(rr.read()); c.close()
check("upload over 100 MB refused before reading it", big.get("ok") is False and "100 MB" in big["error"] and len(got) == n)
# put back
pb = []
d.modpack_putback = lambda cfg, slug, pid, who: (pb.append((slug, pid)), {"ok": True, "msg": "back"})[1]
s, r = req("POST", "/api/modpack/putback", body=b'{"slug":"cozy-pack","id":581495}')
check("put back reaches decklib", r.get("ok") and pb[-1] == ("cozy-pack", 581495))
for bad in [b'{"slug":"cozy-pack","id":"1;2"}', b'{"slug":"../x","id":1}', b'{"slug":"cozy-pack"}']:
    s, r = req("POST", "/api/modpack/putback", body=bad)
    check(f"put back refuses {bad.decode()}", r.get("ok") is False and len(pb) == 1)

# keybind presets
saved = []
d.k12_up = lambda c: False
sample = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "presets", "sample-export.json"), encoding="utf-8"))
s, r = req("GET", "/api/preset?slug=cozy-pack")
check("no preset yet -> null", r.get("ok") and r["preset"] is None)
s, r = req("POST", "/api/preset/save", body=json.dumps({"slug": "cozy-pack", "preset": sample, "name": "Server keys"}).encode())
check(f"save a 24-key preset bigger than the normal 4 KB limit ({r.get('msg') or r.get('error')})", r.get("ok") and r["keys"] == 24)
s, r = req("GET", "/api/preset?slug=cozy-pack")
check("load it back, with the new name", r["preset"]["name"] == "Server keys" and len(r["preset"]["keys"]) == 24)
c = http.client.HTTPConnection("127.0.0.1", port, timeout=10); c.request("GET", "/api/preset/file?slug=cozy-pack"); rr = c.getresponse(); body = rr.read(); c.close()
check("download the file", rr.status == 200 and "attachment" in rr.getheader("Content-Disposition", "") and json.loads(body)["format"] == "cyberdeck-keys/1")
s, r = req("POST", "/api/preset/save", body=json.dumps({"slug": "cozy-pack", "preset": {"format": "nope"}}).encode())
check("a non-preset is refused with a reason", r.get("ok") is False and "keybind preset" in r["error"])
s, r = req("POST", "/api/preset/save", body=json.dumps({"slug": "../x", "preset": sample}).encode())
check("bad pack refused", r.get("ok") is False)
s, r = req("POST", "/api/preset/save", body=json.dumps({"slug": "cozy-pack", "preset": sample}).encode(), headers={"X-Deck": "0"})
check("preset save without X-Deck refused", s == 403)
s, r = req("POST", "/api/preset/delete", body=b'{"slug":"cozy-pack"}')
check("delete", r.get("ok") and d.preset_get("cozy-pack") is None)
c = http.client.HTTPConnection("127.0.0.1", port, timeout=10); c.request("GET", "/api/preset/file?slug=../etc"); rr = c.getresponse(); rr.read(); c.close()
check("download of a bad name -> 404", rr.status == 404)
check("editor is on the page", 'id="kpslug"' in html and "cyberdeck-keys/1" in html)

d.write_json("modpack_list.json", {"active": "cozy-pack", "packs": []})
d.write_json("modpacks.json", {"cozy-pack": {"name": "Cozy Pack"}})
s, r = req("GET", "/api/status")
check("status names the world that's on", r.get("modpack") == "Cozy Pack")

# ---- what the Deck app (Deck.exe) uses on top of the page
calls = []
def fake_net(cmd, *a, payload=None, timeout_s=70):
    calls.append((cmd, payload))
    if cmd == "config-set":
        return {"ok": False, "error": "nope"} if "bad" in json.dumps(payload) else {"ok": True, "msg": "Saved."}
    return {"ok": True, "values": {"idle_stop_minutes": 20}, "fields": []} if cmd == "config-get" else {"ok": True}
d.net = fake_net
s, r = req("GET", "/api/config")
check("GET /api/config asks deck-net (root) for the settings", r.get("ok") and calls[-1][0] == "config-get")
before = len(d.read_events(0, limit=100))
s, r = req("POST", "/api/config", body=b'{"changes":{"idle_stop_minutes":30}}')
check("POST /api/config hands the changes to deck-net and logs them",
      r.get("ok") and calls[-1] == ("config-set", {"changes": {"idle_stop_minutes": 30}})
      and "idle_stop_minutes = 30" in d.read_events(0, limit=100)[-1]["text"])
s, r = req("POST", "/api/config", body=b'{"changes":{"bad":1}}')
check("a refused settings change shows the error, no event", r.get("ok") is False and len(d.read_events(0, limit=100)) == before + 1)
s, r = req("POST", "/api/pi/reboot")
check("Pi restart goes through deck-net", r.get("ok") and calls[-1][0] == "reboot" and "restarting" in d.read_events(0, limit=100)[-1]["text"])

seen_set = {}
real_setting_set = d.server_setting_set
d.server_setting_set = lambda cfg, kind, key, value, who: (seen_set.update(kind=kind, key=key, value=value), {"ok": True})[1]
d.server_settings = lambda cfg: {"ok": True, "env": {"MC_DIFFICULTY": "hard"}}
s, r = req("POST", "/api/server/settings", body=b'{"kind":"prop","key":"view-distance","value":"8"}')
check("server setting reaches decklib", r.get("ok") and seen_set == {"kind": "prop", "key": "view-distance", "value": "8"})
s, r = req("GET", "/api/server/settings")
check("server settings are read through decklib", r.get("env", {}).get("MC_DIFFICULTY") == "hard")
n = {"perf": 0}
d.server_perf = lambda cfg: (n.update(perf=n["perf"] + 1), {"ok": True, "running": True, "tps": 20})[1]
req("GET", "/api/perf"); s, r = req("GET", "/api/perf")
check("perf is cached (two reads = one call to the PC)", r.get("tps") == 20 and n["perf"] == 1)
n0 = len(started)          # p.background records what it would run (set up further up)
s, r = req("POST", "/api/mc/restart")
check("restart runs restart_minecraft in the background",
      r.get("ok") and len(started) == n0 + 1 and started[-1][0] == "restart_minecraft")

# decklib itself: only the listed settings, values travel base64url-encoded
d.k12_up = lambda cfg: True
sent = []
d.agent = lambda cfg, *a, timeout_s=60: (sent.append(a), {"ok": True})[1]
r = real_setting_set(d.load_config(), "env", "MC_EULA", "FALSE", "t")
check("decklib refuses settings the app may not change (EULA)", r.get("ok") is False and not sent)
r = real_setting_set(d.load_config(), "env", "MC_MOTD", "Friday night: build party!", "t")
import base64
check("MOTD with spaces arrives as one base64url word",
      r.get("ok") and sent[-1][:4] == ("settings", "set", "env", "MC_MOTD")
      and base64.urlsafe_b64decode(sent[-1][4] + "=" * (-len(sent[-1][4]) % 4)).decode() == "Friday night: build party!")

# ---- importing a CurseForge export: the app sends the zip's manifest.json
d.current_action = lambda: None
MAN = {"manifestType": "minecraftModpack", "name": "My Mix", "version": "2", "files": [{"projectID": 1, "fileID": 5}],
       "minecraft": {"version": "1.20.1", "modLoaders": [{"id": "forge-47.2.0", "primary": True}]}}
n0 = len(started)
s, r = req("POST", "/api/modpack/import-check", body=json.dumps({"manifest": MAN}).encode())
check(f"import-check shows what would be installed, starts nothing ({r.get('slug')})",
      r.get("ok") and r.get("slug") == "my-mix" and r.get("mods") == 1 and len(started) == n0)
s, r = req("POST", "/api/modpack/import", body=json.dumps({"manifest": MAN}).encode())
check("import starts the usual switch with the zip's details",
      r.get("ok") and len(started) == n0 + 1 and started[-1][0] == "modpack_change" and started[-1][1][1] == "my-mix"
      and started[-1][2]["meta"]["source"] == "zip")
s, r = req("POST", "/api/modpack/import", body=json.dumps({"manifest": {"name": "x"}}).encode())
check("a zip that isn't an export is refused with how to make one", r.get("ok") is False and "Export profile" in r.get("error", ""))
big = dict(MAN, files=[{"projectID": i, "fileID": i} for i in range(1, 2001)])
s, r = req("POST", "/api/modpack/import-check", body=json.dumps({"manifest": big}).encode())
check(f"a big pack's manifest (2000 mods) fits in the request ({len(json.dumps(big)) // 1024} KB)", r.get("ok") and r.get("mods") == 2000)
srv.shutdown()
sys.exit(1 if fails else 0)
