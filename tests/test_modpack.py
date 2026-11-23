"""Modpacks on the Pi side: link checks, Java/memory choice, CurseForge preview, the switch steps,
log scanning, delete, and backup names. Pretend PC + pretend CurseForge."""
import sys, types, os, tempfile, json, io, re, urllib.error
fc = types.ModuleType("fcntl"); fc.LOCK_EX = 2; fc.LOCK_NB = 4; fc.LOCK_UN = 8; fc.flock = lambda f, o: None; sys.modules["fcntl"] = fc
os.environ["DECK_STATE"] = tempfile.mkdtemp(); os.environ["DECK_CONFIG"] = os.path.join(os.environ["DECK_STATE"], "c.json")
os.environ["DECK_CF_ENV"] = os.path.join(os.environ["DECK_STATE"], "curseforge.env")
json.dump({"k12_user": "x", "k12_mac": "aa:bb:cc:dd:ee:ff", "pack_warn_s": 0, "mc_join_address": "deck.example:25565"},
          open(os.environ["DECK_CONFIG"], "w"))
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "pi5"))
import decklib as d
d.time.sleep = lambda s: None
fails = 0
def check(n, c):
    global fails; print(("PASS " if c else "FAIL ") + n); fails += not c
cfg = d.load_config()

# ---- link parsing
good = {"https://www.curseforge.com/minecraft/modpacks/cozy-pack": ("cozy-pack", None),
        "curseforge.com/minecraft/modpacks/cozy-pack/files/4812345": ("cozy-pack", 4812345),
        "https://www.curseforge.com/minecraft/modpacks/cozy-pack/download/4812345": ("cozy-pack", 4812345),
        "https://www.curseforge.com/minecraft/modpacks/Cozy-Pack/files": ("cozy-pack", None),
        "https://www.curseforge.com/minecraft/modpacks/cozy-pack?page=2#top": ("cozy-pack", None),
        "  cozy-pack  ": ("cozy-pack", None), "atm9": ("atm9", None)}
for t, want in good.items():
    try: got = d.parse_modpack_link(t)
    except ValueError as e: got = str(e)
    check(f"link ok: {t.strip()} -> {got}", got == want)
bad = ["", "https://evil.example/minecraft/modpacks/x", "https://www.curseforge.com/minecraft/mc-mods/jei",
       "cozy pack", "../etc", "https://www.curseforge.com/minecraft/modpacks/cozy;rm", "-starts-with-dash",
       "https://www.curseforge.com/minecraft/modpacks/cozy-pack/files/12ab", "x" * 400,
       "https://www.curseforge.com.evil.example/minecraft/modpacks/cozy"]
for t in bad:
    try: d.parse_modpack_link(t); ok = False; msg = ""
    except ValueError as e: ok = True; msg = str(e)
    check(f"link refused: {t[:50]!r} ({msg[:40]})", ok and msg)
try: d.parse_modpack_link("https://www.curseforge.com/minecraft/mc-mods/jei")
except ValueError as e: check("a mod (not modpack) link gets a helpful hint", "not to a modpack" in str(e))

# ---- Java + memory
for v, tag in [("1.12.2", "java8"), ("1.16.5", "java8"), ("1.17.1", "java17"), ("1.20.1", "java17"), ("1.20.4", "java17"),
               ("1.20.5", "java21"), ("1.21.1", "java21"), ("1.21.10", "java21"), ("26.1", "latest"), ("", "latest"), ("x.y", "latest")]:
    check(f"java for {v or 'unknown'} -> {tag}", d.java_tag(v) == tag)
check("memory: 10 GB -> Java 8 GB in a 10 GB box", d.modpack_memory(cfg) == (8, 10))
check("memory never above what Docker may use", d.modpack_memory(dict(cfg, modpack_mem_gb=64)) == (16, 18))
check("memory never silly small", d.modpack_memory(dict(cfg, modpack_mem_gb=1)) == (2, 4))
check("agent's memory rule accepts what the Pi sends", all(2 <= h <= 24 and h < l <= 28 for h, l in
      [d.modpack_memory(dict(cfg, modpack_mem_gb=g, docker_mem_gb=dm)) for g in range(1, 40) for dm in (8, 20, 30)]))

# ---- the same name rules on both sides (decklib <-> deck-agent.ps1)
agent_src = open(os.path.join(here, "..", "k12-windows", "agent", "deck-agent.ps1"), encoding="utf-8").read()
ps = {k: re.search(rf"\${k}\s*=\s*'([^']+)'", agent_src).group(1) for k in ("SlugRe", "BackupRe", "FileIdRe", "TagRe")}
for s in ["cozy-pack", "a", "atm9", "x" * 80, "x" * 81, "Cozy", "-a", "a_b", "a.b", "a b"]:
    check(f"slug rule agrees on {s[:20]!r}", bool(d.SLUG_RE.match(s)) == bool(re.match(ps["SlugRe"], s)))
for n in ["minecraft-20261121-1830.zip", "minecraft-20261121-183015.zip", "pack-cozy-pack-20261121-183015.zip",
          "pack-a-20261121-1830.zip", "pack--20261121-1830.zip", "pack-Cozy-20261121-1830.zip", "minecraft-2026.zip",
          "pack-../x-20261121-1830.zip", "world-20261121-1830.zip"]:
    check(f"backup name rule agrees on {n}", bool(d.BACKUP_RE.match(n)) == bool(re.match(ps["BackupRe"], n)))
check("old backup names still valid", d.BACKUP_RE.match("minecraft-20261119-1200.zip") and d.backup_world("minecraft-20261119-1200.zip") == "minecraft")
check("pack backups name their world", d.backup_world("pack-cozy-pack-20261121-183015.zip") == "pack-cozy-pack")
for t in ["java8", "java17", "java21", "latest"]:
    check(f"agent accepts java tag {t}", re.match(ps["TagRe"], t))

# ---- CurseForge preview (pretend API)
class FakeResp(io.BytesIO):
    def __enter__(s): return s
    def __exit__(s, *a): pass
seen = []
MOD = {"id": 777, "slug": "cozy-pack", "name": "Cozy Pack", "summary": "Light and cozy", "downloadCount": 12345,
       "mainFileId": 5000, "logo": {"thumbnailUrl": "https://media.forgecdn.net/avatars/thumbnails/1/2/64/64/x.png"},
       "latestFilesIndexes": [{"fileId": 5000, "gameVersion": "1.20.1", "modLoader": 4}]}
FILES = {5000: {"id": 5000, "displayName": "Cozy Pack 2.1", "gameVersions": ["1.20.1"]},
         4000: {"id": 4000, "displayName": "Cozy Pack 1.0", "gameVersions": ["1.19.2", "Forge"]},
         4100: {"id": 4100, "displayName": "Cozy Server", "gameVersions": ["1.20.1"], "isServerPack": True}}
def urlopen(req, timeout=15):
    seen.append((req.full_url, req.get_header("X-api-key")))
    u = req.full_url
    if "/v1/categories" in u: return FakeResp(json.dumps({"data": [{"id": 6, "slug": "mc-mods"}, {"id": 4471, "slug": "modpacks"}]}).encode())
    if "/v1/mods/search" in u:
        return FakeResp(json.dumps({"data": [MOD] if "slug=cozy-pack" in u and "classId=4471" in u else []}).encode())
    m = re.search(r"/v1/mods/777/files/(\d+)$", u)
    if m and int(m.group(1)) in FILES: return FakeResp(json.dumps({"data": FILES[int(m.group(1))]}).encode())
    raise urllib.error.HTTPError(u, 404, "nf", {}, None)
d.urllib.request.urlopen = urlopen
try: d.modpack_preview("cozy-pack"); ok = False
except RuntimeError as e: ok = "deck-curseforge-setup" in str(e)
check("no key on the Pi -> says how to add it", ok)
open(os.environ["DECK_CF_ENV"], "w").write("CF_API_KEY=$2a$10$abcdefghijklmnopqrstuv\n")
p = d.modpack_preview("https://www.curseforge.com/minecraft/modpacks/cozy-pack")
check(f"preview: name, version, loader, java, logo ({p['name']}, {p['mc']}, {p['loader']}, {p['java']})",
      p["name"] == "Cozy Pack" and p["mc"] == "1.20.1" and p["loader"] == "Fabric" and p["java"] == "java17"
      and p["file_id"] == 5000 and p["can_install"] and p["logo"].startswith("https://media.forgecdn.net/"))
check("key sent as x-api-key, with $ signs intact", seen[-1][1] == "$2a$10$abcdefghijklmnopqrstuv")
p = d.modpack_preview("https://www.curseforge.com/minecraft/modpacks/cozy-pack/files/4000")
check("pinned file: its own version + loader", p["file_id"] == 4000 and p["mc"] == "1.19.2" and p["loader"] == "Forge" and p["pinned"])
p = d.modpack_preview("https://www.curseforge.com/minecraft/modpacks/cozy-pack/files/4100")
check("server-pack file -> can't install, says why", not p["can_install"] and "server download" in p["warning"])
MOD["latestFilesIndexes"][0]["modLoader"] = 5
p = d.modpack_preview("cozy-pack")
check("Quilt pack -> can't install, says why", not p["can_install"] and "Quilt" in p["warning"])
MOD["latestFilesIndexes"][0]["modLoader"] = 4
MOD["logo"]["thumbnailUrl"] = "https://evil.example/x.png"
check("logo only from CurseForge's image server", d.modpack_preview("cozy-pack")["logo"] == "")
try: d.modpack_preview("no-such-pack"); ok = False
except RuntimeError as e: ok = "No modpack called" in str(e)
check("unknown pack -> clear message", ok)
def u403(req, timeout=15): raise urllib.error.HTTPError(req.full_url, 403, "no", {}, None)
d.urllib.request.urlopen = u403
try: d.modpack_preview("cozy-pack"); ok = False
except RuntimeError as e: ok = "refused" in str(e)
check("key refused -> says to run the setup again", ok)
d.urllib.request.urlopen = urlopen

# ---- log scanning (lines as the server image prints them)
LOG = ["[mc-image-helper] 12:00:01.000 WARN  : The authors of the mod 'Cool Mod' have disallowed project distribution. "
       "Manually download the file 'coolmod-1.2.jar' from https://www.curseforge.com/minecraft/mc-mods/cool-mod/download/123 and supply via downloads repo or separately.",
       "[mc-image-helper] 12:00:01.000 WARN  : The authors of the mod 'Other' have disallowed project distribution. "
       "Manually download the file 'other.jar' from https://evil.example/x and supply via downloads repo or separately.",
       "Some mod authors disallow automated downloads."]
s = d.scan_modpack_log(LOG)
check(f"manual downloads found with name, file, link ({s['missing']})",
      len(s["missing"]) == 2 and s["missing"][0] == {"mod": "Cool Mod", "file": "coolmod-1.2.jar",
      "url": "https://www.curseforge.com/minecraft/mc-mods/cool-mod/download/123"} and s["missing"][1]["url"] == "")
s = d.scan_modpack_log(["Access to https://api.curseforge.com is forbidden or rate-limit has been exceeded. Ensure CF_API_KEY ..."])
check("bad key on the PC -> says to run setup-curseforge", "setup-curseforge" in s["problem"])
check("normal lines -> nothing to report", d.scan_modpack_log(["[Server thread/INFO]: Preparing spawn area: 50%"]) == {"missing": [], "problem": ""})

# ---- the switch, with a pretend PC
pc = {"up": True, "active": "", "running": False, "online": False, "log": [], "calls": [], "packs": {}}
d.k12_up = lambda c: pc["up"]
d.k12_agent_ready = lambda c: pc["up"]
d.mc_status = lambda h, p=25565, t=3: {"online": pc["online"], "players": 0, "names": []}
def agent(c, *a, timeout_s=60):
    pc["calls"].append(a)
    if a[0] == "stop": pc["running"] = pc["online"] = False; return {"ok": True}
    if a[0] == "backup":
        slug = a[1] if len(a) > 1 else pc["active"]
        name = (f"pack-{slug}" if slug else "minecraft") + "-20261121-120000.zip"
        pc["zip"] = name
        return {"ok": True, "file": name, "bytes": 22, "sha256": ""}
    if a[:2] == ("modpack", "apply"): pc["active"] = a[2]; pc["packs"][a[2]] = a; return {"ok": True}
    if a[:2] == ("modpack", "vanilla"): pc["active"] = ""; return {"ok": True}
    if a[:2] == ("modpack", "list"):
        return {"ok": True, "active": pc["active"], "key": True,
                "packs": [{"slug": s, "has_world": True, "excluded": ""} for s in pc["packs"]]}
    if a[:2] == ("modpack", "delete"): pc["packs"].pop(a[2], None); return {"ok": True}
    if a[0] == "start": pc["running"] = True; pc["starts"] = pc.get("starts", 0) + 1; return {"ok": True}
    if a[0] == "logs":
        if pc.get("ok_after") and pc["running"]: pc["online"] = True
        # like Docker: lines from earlier runs stay; this run's lines appear once it's running
        return {"ok": True, "running": pc["running"], "lines": pc.get("stale", []) + (pc["log"] if pc["running"] else [])}
    return {"ok": True}
d.agent = agent
import zipfile
def fake_dl(c, name, dest, timeout_s=1800):
    with zipfile.ZipFile(dest, "w") as z: z.writestr("world/level.dat", "x")
d.agent_download = fake_dl
def step(st): return {s["key"]: s["status"] for s in st["steps"]}

preview = d.modpack_preview("cozy-pack")
pc["ok_after"] = True
st = d.modpack_change(cfg, "cozy-pack", "test", meta=preview)
check(f"install: every step ok {step(st)}", st["ok"] and all(v in ("ok", "skip") for v in step(st).values()) and step(st)["watch"] == "ok")
ap = pc["packs"]["cozy-pack"]
check(f"install: PC told pack, file, java, memory ({ap})", ap == ("modpack", "apply", "cozy-pack", "5000", "java17", "8", "10"))
order = [c[0] if c[0] != "modpack" else c[1] for c in pc["calls"]]
check(f"install order: stop -> backup -> apply -> start ({order})", order.index("stop") < order.index("backup") < order.index("apply") < order.index("start"))
ev = [e["text"] for e in d.read_events(0, limit=50)]
check("install: 'How to join' posted with link, exact version and address",
      any("How to join" in t and "cozy-pack" in t and "Cozy Pack 2.1" in t and "deck.example:25565" in t for t in ev))
check("install: vanilla world backed up first under its own name", any("minecraft-20261121-120000.zip" in t for t in ev))
lst = d.modpack_list(cfg)
check(f"list: names from the Pi, active pack ({lst['active_name']})", lst["active"] == "cozy-pack" and lst["active_name"] == "Cozy Pack"
      and lst["packs"][0]["mc"] == "1.20.1" and lst["live"])

# back to vanilla
pc["calls"].clear()
st = d.modpack_change(cfg, "", "test")
check(f"vanilla: ok, pack world backed up as pack-cozy-pack ({step(st)})", st["ok"] and pc["active"] == ""
      and ("backup",) in pc["calls"] and any("pack-cozy-pack-" in e["text"] for e in d.read_events(0, limit=10)))

# manual downloads needed -> stops the server, lists the mods, says so
pc["ok_after"] = False; pc["log"] = LOG[:1]
st = d.modpack_change(cfg, "cozy-pack", "test")
check(f"needs a manual download -> watch fails, mod listed, server stopped ({step(st)['watch']})",
      not st["ok"] and step(st)["watch"] == "fail" and st["missing"][0]["mod"] == "Cool Mod" and not pc["running"])
check("switch to an installed pack reuses its saved version", pc["packs"]["cozy-pack"][3] == "5000")
check("Events say a mod must be downloaded by hand", "downloaded by hand" in d.read_events(0, limit=5)[-1]["text"])
# Try again after putting the file in place: the OLD warning is still in Docker's log, but must not count
pc["ok_after"] = True; pc["stale"] = LOG[:1]; pc["log"] = []
st = d.modpack_change(cfg, "cozy-pack", "test")
check(f"try again: old log lines from the last run are ignored ({step(st)['watch']})", st["ok"] and step(st)["watch"] == "ok")
pc["stale"] = []

# server dies while loading
pc["log"] = ["[Server thread/ERROR]: oops"]; pc["ok_after"] = False
real_agent = d.agent
polls = {"n": 0}
def dies(c, *a, timeout_s=60):
    r = real_agent(c, *a, timeout_s=timeout_s)
    if a[0] == "logs" and pc["running"]:
        polls["n"] += 1
        if polls["n"] > 1: pc["running"] = False; r["running"] = False   # up for one look, then gone
    return r
d.agent = dies
st = d.modpack_change(cfg, "", "test")
d.agent = real_agent
check(f"server stops while loading -> clear fail ({step(st)['watch']})", not st["ok"] and "stopped" in st["steps"][-1]["detail"])

# backup fails -> nothing switched
def nobackup(c, *a, timeout_s=60):
    if a[0] == "backup": return {"ok": False, "error": "disk full"}
    return real_agent(c, *a, timeout_s=timeout_s)
d.agent = nobackup; before = pc["active"]
st = d.modpack_change(cfg, "cozy-pack", "test")
d.agent = real_agent
check(f"backup fails -> world NOT switched ({step(st)})", not st["ok"] and step(st)["apply"] == "skip" and pc["active"] == before)

# PC off and won't wake
pc["up"] = False; d.wake_k12 = lambda c, w="": None; cfg2 = dict(cfg, wake_timeout_s=0)
st = d.modpack_change(cfg2, "", "test")
check("PC won't wake -> clear fail, nothing else run", not st["ok"] and "didn't answer" in st["steps"][0]["detail"])
pc["up"] = True
check("action lock released after every run", d.current_action() is None)

# bad input never reaches the PC
for t, meta in [("../x", None), ("Cozy", None), ("cozy-pack", dict(preview, file_id="12;rm")), ("cozy-pack", dict(preview, java="java99"))]:
    n = len(pc["calls"])
    try: d.modpack_change(cfg, t, "test", meta=meta); ok = False
    except ValueError: ok = True
    check(f"refused before the PC sees it: {t} {'' if meta is None else meta.get('file_id'), meta.get('java') if meta else ''}", ok and len(pc["calls"]) == n)

# ---- delete
pc["active"] = "cozy-pack"
r = d.modpack_delete(cfg, "cozy-pack", "test")
check("can't delete the pack that's on", not r["ok"] and "switch" in r["error"])
pc["active"] = ""; pc["calls"].clear()
r = d.modpack_delete(cfg, "cozy-pack", "test")
check(f"delete: backs that world up first, then deletes ({pc['calls']})", r["ok"] and ("backup", "cozy-pack") in pc["calls"]
      and pc["calls"].index(("backup", "cozy-pack")) < pc["calls"].index(("modpack", "delete", "cozy-pack")))
check("delete keeps the backups on the Pi", any(b["world"] == "pack-cozy-pack" for b in d.list_backups()))
check("bad name refused", d.modpack_delete(cfg, "../x", "t")["ok"] is False)

# ---- backups: one world's backups never push out another's
import time as _t
os.makedirs(d.BACKUP_DIR, exist_ok=True)
for f in os.listdir(d.BACKUP_DIR): os.remove(os.path.join(d.BACKUP_DIR, f))
for i in range(3):
    p_ = os.path.join(d.BACKUP_DIR, f"pack-old-pack-2026010{i}-1200.zip"); open(p_, "wb").write(b"PK")
    os.utime(p_, (_t.time() - 99999 + i, _t.time() - 99999 + i))
cfg_keep = dict(cfg, backup_keep=2, backup_min_free_mb=0)
for i in range(4):
    def ag(c, *a, timeout_s=60, i=i):
        return {"ok": True, "file": f"minecraft-2026112{i}-1200.zip", "bytes": 1, "sha256": ""}
    d.agent = ag; d._backup(cfg_keep)
w = {}
for b in d.list_backups(): w[b["world"]] = w.get(b["world"], 0) + 1
check(f"keep newest 2 vanilla backups, old pack's 3 untouched ({w})", w == {"minecraft": 2, "pack-old-pack": 3})

# ---- importing a CurseForge "Export profile" zip (its manifest.json)
MAN = {"minecraft": {"version": "1.20.1", "modLoaders": [{"id": "forge-47.2.0", "primary": True}]},
       "manifestType": "minecraftModpack", "manifestVersion": 1, "name": "Mark's Cozy Mix!", "version": "1.3",
       "author": "mark", "files": [{"projectID": 238222, "fileID": 4712866, "required": True},
                                   {"projectID": 581495, "fileID": 4600000, "required": True},
                                   {"projectID": "x", "fileID": 1}], "overrides": "overrides"}
m = d.import_meta(MAN)
check(f"import: name, version, Minecraft, loader, Java, mods ({m['slug']}, {m['loader']}, {m['java']}, {m['mods']})",
      m["slug"] == "mark-s-cozy-mix" and m["name"] == "Mark's Cozy Mix!" and m["file_name"] == "Mark's Cozy Mix! 1.3"
      and m["mc"] == "1.20.1" and m["loader"] == "Forge" and m["java"] == "java17" and m["files"] == [4712866, 4600000]
      and m["can_install"] and m["source"] == "zip" and m["file_id"] == "zip")
check("import: JSON text works too", d.import_meta(json.dumps(MAN))["slug"] == "mark-s-cozy-mix")
for lid, want in [("neoforge-21.1.77", "NeoForge"), ("fabric-0.16.9", "Fabric")]:
    check(f"import: loader {lid} -> {want}", d.import_meta(dict(MAN, minecraft={"version": "1.21.1", "modLoaders": [{"id": lid, "primary": True}]}))["loader"] == want)
q = d.import_meta(dict(MAN, minecraft={"version": "1.20.1", "modLoaders": [{"id": "quilt-0.20", "primary": True}]}))
check("import: a Quilt pack is shown but can't be installed", not q["can_install"] and "Quilt" in q["warning"])
for bad, why in [({"name": "x"}, "not a modpack manifest"), ("not json", "unreadable"), ([1, 2], "a list"),
                 (dict(MAN, minecraft={"version": "banana"}), "no Minecraft version"),
                 (dict(MAN, files=[{"projectID": 1, "fileID": i} for i in range(d.MAX_IMPORT_MODS + 1)]), "too many mods")]:
    try: d.import_meta(bad); ok = False
    except ValueError: ok = True
    check(f"import refused: {why}", ok)
check("import: slugs are always safe names",
      d.import_slug("../../etc") == "etc" and d.import_slug("!!!") == "imported-pack" and d.import_slug("A" * 200) == "a" * 60
      and d.SLUG_RE.match(d.import_slug("  -- Ünïcode Pack --  ")))
asked = []
d.cf_api = lambda path, params=None, timeout_s=15, body=None: (asked.append((path, body)), {"data":
    [{"id": 4712866, "modId": 238222, "fileName": "jei-1.20.1.jar"}, {"id": 4600000, "modId": 581495, "fileName": "oculus.jar"}]
    if path == "/v1/mods/files" else [{"id": 238222, "name": "JEI", "slug": "jei"}, {"id": 581495, "name": "Oculus", "slug": "oculus"}]})[1]
fm = d.pack_file_map(dict(m, slug="mark-s-cozy-mix"))
check(f"import: the auto-fix's mod map comes from the manifest's file ids ({sorted(fm)})",
      fm.get("oculus.jar", {}).get("id") == 581495 and asked[0] == ("/v1/mods/files", {"fileIds": [4712866, 4600000]}))
n = len(asked); d.pack_file_map(dict(m, slug="mark-s-cozy-mix"))
check("import: mod map cached (no second CurseForge call)", len(asked) == n)
htj = d.how_to_join(cfg, m)
check("import: How to join tells players to import the same export zip", "Create Custom Profile > Import" in htj and "None" not in htj)

print(f"\n{fails} failures"); sys.exit(1 if fails else 0)
