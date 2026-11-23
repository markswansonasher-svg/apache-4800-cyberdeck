"""Modpack auto-fix: crash logs -> which mod to take out -> CurseForge project -> leave it out and restart
(at most 3 times). Plus Put back and hand-downloaded mod uploads. Real crash samples in tests/crash-logs/."""
import sys, types, os, tempfile, json, io, zipfile, urllib.error
fc = types.ModuleType("fcntl"); fc.LOCK_EX = 2; fc.LOCK_NB = 4; fc.LOCK_UN = 8; fc.flock = lambda f, o: None; sys.modules["fcntl"] = fc
os.environ["DECK_STATE"] = tempfile.mkdtemp(); os.environ["DECK_CONFIG"] = os.path.join(os.environ["DECK_STATE"], "c.json")
os.environ["DECK_CF_ENV"] = os.path.join(os.environ["DECK_STATE"], "curseforge.env")
open(os.environ["DECK_CF_ENV"], "w").write("CF_API_KEY=$2a$10$testkeytestkeytestkey\n")
json.dump({"k12_user": "x", "k12_mac": "aa:bb:cc:dd:ee:ff", "pack_warn_s": 0}, open(os.environ["DECK_CONFIG"], "w"))
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "pi5"))
import decklib as d
d.time.sleep = lambda s: None
fails = 0
def check(n, c):
    global fails; print(("PASS " if c else "FAIL ") + n); fails += not c
cfg = d.load_config()
def log(name): return open(os.path.join(here, "crash-logs", name), encoding="utf-8").read().splitlines()

# ---- 1. reading real crash logs
CASES = [  # file, removed so far, clues expected (kind, value), "other" message expected
    ("forge-1.19.2-oculus.txt", [], [("modid", "oculus"), ("file", "oculus-mc1.19.2-1.2.8a.jar")], ""),
    ("forge-1.18.2-torohealth.txt", [], [("modid", "torohealth"), ("file", "torohealth-1.18-forge-2.jar")], ""),
    ("forge-1.20.1-skyboxes.txt", [], [("modid", "forgeskyboxes")], ""),
    ("forge-1.20.1-mixin-only.txt", [], [("mixin", "mixin.undergroundjungle.json"), ("mixin", "mixins.squarry.json")], ""),
    ("forge-1.19.2-missing-dep.txt", ["spruceui"], [("modid", "ryoamiclights")], ""),
    ("forge-1.19.2-missing-dep.txt", [], [], ""),
    ("forge-1.20.1-java25.txt", [], [], "Java"),
    ("fabric-1.20.1-random.txt", [], [], ""),
    ("fabric-format-client-class.txt", [], [("modid", "xaerominimap")], ""),
]
for f, removed, want, other in CASES:
    r = d.find_client_culprits(log(f), removed)
    got = [(c["kind"], c["value"]) for c in r["clues"]]
    ok = all(w in got for w in want) and (want or not got) and (other in r["other"] if other else not r["other"])
    check(f"{f}{' (after removing ' + ','.join(removed) + ')' if removed else ''}: {got or r['other'] or 'nothing'}", ok)
check("the Oculus crash names its full mod name too", any(c["name"] == "Oculus" for c in d.find_client_culprits(log("forge-1.19.2-oculus.txt"))["clues"]))
check("crash lines are recognised (so a self-restarting server is noticed)",
      all(any(d.CRASH_MARKERS.search(l) for l in log(f)) for f in ["forge-1.19.2-oculus.txt", "forge-1.20.1-skyboxes.txt",
          "forge-1.20.1-mixin-only.txt", "forge-1.19.2-missing-dep.txt", "fabric-format-client-class.txt"]))
check("normal start-up lines are not crashes", not any(d.CRASH_MARKERS.search(l) for l in
      ["[Server thread/INFO]: Preparing spawn area: 50%", "[Server thread/INFO]: Done (12.3s)! For help, type \"help\"",
       "[mc-image-helper] Downloading mods 30/120", "[main/WARN]: Mod 'Debugify' (debugify) recommends any version of modmenu"]))
check("never names the game or a loader as the culprit",
      d.find_client_culprits(["Attempted to load class net/minecraft/client/X for invalid dist DEDICATED_SERVER",
                              "Failed to create mod instance. ModID: forge, class x"])["clues"] == [])
# a mixin warning alone, with a real culprit named elsewhere: the real culprit wins
mixed = log("forge-1.20.1-mixin-only.txt") + log("forge-1.20.1-skyboxes.txt")
check("a named crash beats weaker mixin clues", [c["kind"] for c in d.find_client_culprits(mixed)["clues"]] == ["modid"])

# ---- 2. clue -> jar -> CurseForge project
MODS = [{"file": "oculus-mc1.19.2-1.2.8a.jar", "ids": ["oculus"], "mixins": ["oculus.mixins.json"]},
        {"file": "forgeskyboxes-1.0.jar", "ids": ["forgeskyboxes"], "mixins": []},
        {"file": "UndergroundJungle-1.2.jar", "ids": ["undergroundjungle"], "mixins": ["mixin.undergroundjungle.json"]},
        {"file": "jei-1.20.jar", "ids": ["jei"], "mixins": []}]
FMAP = {"oculus-mc1.19.2-1.2.8a.jar": {"id": 581495, "name": "Oculus"}, "forgeskyboxes-1.0.jar": {"id": 600001, "name": "Forge Skyboxes"},
        "UndergroundJungle-1.2.jar": {"id": 600002, "name": "Underground Jungle"}, "jei-1.20.jar": {"id": 238222, "name": "JEI"}}
r = d.resolve_culprits(d.find_client_culprits(log("forge-1.19.2-oculus.txt"))["clues"], MODS, FMAP)
check(f"oculus crash -> CurseForge project 581495, once ({r})", [x["id"] for x in r] == [581495] and r[0]["name"] == "Oculus")
r = d.resolve_culprits([{"kind": "mixin", "value": "mixin.undergroundjungle.json", "name": ""}], MODS, FMAP)
check("mixin config -> its jar -> project", [x["id"] for x in r] == [600002])
check("a mod not in the pack's list can't be taken out", d.resolve_culprits([{"kind": "modid", "value": "ghost", "name": ""}], MODS, FMAP) == [])
check("a jar CurseForge doesn't know can't be taken out", d.resolve_culprits([{"kind": "modid", "value": "oculus", "name": ""}], MODS, {}) == [])

# ---- 3. the pack's file list from CurseForge (pretend API)
class R(io.BytesIO):
    def __enter__(s): return s
    def __exit__(s, *a): pass
zbuf = io.BytesIO()
with zipfile.ZipFile(zbuf, "w") as z:
    z.writestr("manifest.json", json.dumps({"files": [{"projectID": 581495, "fileID": 11}, {"projectID": 238222, "fileID": 12}]}))
calls = []
def urlopen(req, timeout=15):
    u = req.full_url; calls.append((req.get_method(), u, req.data))
    if u.endswith("/v1/mods/777/files/5000"): return R(json.dumps({"data": {"downloadUrl": "https://edge.forgecdn.net/files/pack.zip"}}).encode())
    if u == "https://edge.forgecdn.net/files/pack.zip": return R(zbuf.getvalue())
    if u.endswith("/v1/mods/files"):
        return R(json.dumps({"data": [{"id": 11, "modId": 581495, "fileName": "oculus-mc1.19.2-1.2.8a.jar"},
                                      {"id": 12, "modId": 238222, "fileName": "jei-1.20.jar"}]}).encode())
    if u.endswith("/v1/mods"): return R(json.dumps({"data": [{"id": 581495, "name": "Oculus", "slug": "oculus"},
                                                             {"id": 238222, "name": "JEI", "slug": "jei"}]}).encode())
    raise urllib.error.HTTPError(u, 404, "nf", {}, None)
d.urllib.request.urlopen = urlopen
META = {"slug": "cozy-pack", "name": "Cozy Pack", "id": 777, "file_id": 5000, "url": "https://www.curseforge.com/minecraft/modpacks/cozy-pack",
        "file_name": "Cozy Pack 2.1", "mc": "1.19.2", "loader": "Forge", "java": "java17"}
m = d.pack_file_map(META)
check(f"pack file list: jar name -> project + name ({m.get('oculus-mc1.19.2-1.2.8a.jar')})",
      m["oculus-mc1.19.2-1.2.8a.jar"] == {"id": 581495, "name": "Oculus", "slug": "oculus"} and m["jei-1.20.jar"]["id"] == 238222)
check("file ids sent to CurseForge in one POST", any(c[0] == "POST" and c[1].endswith("/v1/mods/files") and json.loads(c[2])["fileIds"] == [11, 12] for c in calls))
n = len(calls); d.pack_file_map(META)
check("cached: a second crash doesn't download the pack again", len(calls) == n)
try: d.pack_file_map({"slug": "x"}); ok = False
except RuntimeError: ok = True
check("no CurseForge ids -> clear error, no download", ok)

# ---- 4. the whole auto-fix loop (pretend PC)
d.write_json("modpacks.json", {"cozy-pack": dict(META)})
pc = {"running": False, "online": False, "excluded": [], "calls": [], "crash_until": {}, "log": [], "stale": []}
d.k12_up = lambda c: True; d.k12_agent_ready = lambda c: True
d.mc_status = lambda h, p=25565, t=3: {"online": pc["online"], "players": 0, "names": []}
d.agent_download = lambda c, n, dest, timeout_s=1800: zipfile.ZipFile(dest, "w").writestr("world/level.dat", "x")
def crashing():   # which crash this start gives, given what's been taken out
    for pid, lines in pc["crashes"]:
        if pid not in pc["excluded"]: return lines
    return None
def agent(c, *a, timeout_s=60):
    pc["calls"].append(a)
    if a[0] == "stop": pc["running"] = pc["online"] = False; return {"ok": True}
    if a[0] == "backup": return {"ok": False, "code": "noworld"}
    if a[0] == "start":
        pc["running"] = True; cr = crashing(); pc["log"] = cr or ["Done (9.1s)!"]
        if not cr: pc["online"] = True
        pc["t"] = pc.get("t", 1763700000) + 100
        return {"ok": True, "since": pc["t"]}
    # like Docker: earlier runs' lines stay in the log unless you ask "--since" this start
    def lines_for(arg): return (pc["log"] if arg == str(pc.get("t")) else pc["stale"] + pc["log"])
    if a[0] == "logs":
        return {"ok": True, "running": pc["running"], "lines": lines_for(a[1] if len(a) > 1 else "") if pc["running"] else pc["stale"]}
    if a[:2] == ("modpack", "apply"): return {"ok": True}
    if a[:2] == ("modpack", "mods"): return {"ok": True, "mods": MODS}
    if a[:2] == ("modpack", "diagnose"): return {"ok": True, "lines": lines_for(a[2] if len(a) > 2 else ""), "crash": []}
    if a[:2] == ("modpack", "exclude"): pc["excluded"].append(int(a[3])); return {"ok": True}
    if a[:2] == ("modpack", "include"): pc["excluded"] = [x for x in pc["excluded"] if x != int(a[3])]; return {"ok": True}
    if a[:2] == ("modpack", "synced"): pc["synced"] = True; return {"ok": True}
    return {"ok": True}
d.agent = agent
def steps(st): return {s["key"]: s["status"] for s in st["steps"]}

pc["crashes"] = [(581495, log("forge-1.19.2-oculus.txt"))]
st = d.modpack_change(cfg, "cozy-pack", "test")
check(f"client mod crash -> taken out, restarted, running ({steps(st)['watch']}: {st['steps'][-1]['detail']})",
      st["ok"] and pc["excluded"] == [581495] and st["rounds"] == 1 and "Oculus" in st["steps"][-1]["detail"])
check("the pack's mods were re-synced, then normal starts again", pc.get("synced"))
ev = [e["text"] for e in d.read_events(0, limit=30)]
check("Discord/Events say what was taken out and why", any("took out Oculus (only works on players' computers)" in t for t in ev))
check("removed mod remembered for the panel (Put back)", d.read_json("modpacks.json")["cozy-pack"]["removed"][0]["id"] == 581495)
pc["calls"].clear(); d.write_json("modpack_list.json", {"active": "cozy-pack", "packs": [{"slug": "cozy-pack"}]})
lst = d.modpack_list(dict(cfg, k12_user=""))
check("list shows the taken-out mod", lst["packs"][0]["removed"][0]["name"] == "Oculus")

# two client mods, one after the other (second shows up once the first is gone)
pc["excluded"] = []; pc["synced"] = False
pc["crashes"] = [(581495, log("forge-1.19.2-oculus.txt")), (600001, log("forge-1.20.1-skyboxes.txt"))]
d.write_json("modpacks.json", {"cozy-pack": dict(META)})
FMAP2 = dict(FMAP); d.pack_file_map = lambda meta: FMAP2
st = d.modpack_change(cfg, "cozy-pack", "test")
check(f"two client mods -> two fixes, then running ({[r['name'] for r in st['removed']]})", st["ok"] and pc["excluded"] == [581495, 600001] and st["rounds"] == 2)

# never more than 3 rounds
pc["excluded"] = []; d.write_json("modpacks.json", {"cozy-pack": dict(META)})
crash4 = [(581495, log("forge-1.19.2-oculus.txt")), (600001, log("forge-1.20.1-skyboxes.txt")),
          (600002, ["Attempted to load class net/minecraft/client/A for invalid dist DEDICATED_SERVER", "Failed to create mod instance. ModID: undergroundjungle, class x", "Mod Loading has failed"]),
          (238222, ["Attempted to load class net/minecraft/client/B for invalid dist DEDICATED_SERVER", "Failed to create mod instance. ModID: jei, class y", "Mod Loading has failed"])]
pc["crashes"] = crash4
st = d.modpack_change(cfg, "cozy-pack", "test")
check(f"stops after 3 fixes and says so ({st['steps'][-1]['detail'][:60]})", not st["ok"] and len(pc["excluded"]) == 3 and "3 tries" in st["steps"][-1]["detail"])
check("server left stopped after giving up (no restart loop)", not pc["running"])

# crash it can't fix: wrong Java
pc["excluded"] = []; pc["crashes"] = [(1, log("forge-1.20.1-java25.txt"))]
st = d.modpack_change(cfg, "cozy-pack", "test")
check(f"wrong-Java crash -> no mod removed, says Java ({st['steps'][-1]['detail'][:50]})", not st["ok"] and pc["excluded"] == [] and "Java" in st["steps"][-1]["detail"])
# culprit named, but CurseForge can't say which project it is
pc["crashes"] = [(999, ["Attempted to load class net/minecraft/client/A for invalid dist DEDICATED_SERVER", "Failed to create mod instance. ModID: ghostmod, class x", "Mod Loading has failed"])]
st = d.modpack_change(cfg, "cozy-pack", "test")
check("named but unknown mod -> says which, nothing removed", not st["ok"] and "ghostmod" in st["steps"][-1]["detail"] and pc["excluded"] == [])
# old crash lines from the previous run must not trigger a fix on a good start
pc["crashes"] = []; pc["stale"] = log("forge-1.19.2-oculus.txt"); pc["excluded"] = []
st = d.modpack_change(cfg, "cozy-pack", "test")
check("old crash lines in Docker's log are ignored on a good start", st["ok"] and pc["excluded"] == [])
pc["stale"] = []

# ---- 5. Put back
d.write_json("modpacks.json", {"cozy-pack": dict(META, removed=[{"id": 581495, "name": "Oculus", "why": "x"}])})
pc["excluded"] = [581495]
r = d.modpack_putback(cfg, "cozy-pack", 581495, "test")
mm = d.read_json("modpacks.json")["cozy-pack"]
check("put back: PC includes it again, list updated, kept for good", r["ok"] and pc["excluded"] == [] and mm["removed"] == [] and mm["keep"] == [581495])
pc["crashes"] = [(581495, log("forge-1.19.2-oculus.txt"))]
st = d.modpack_change(cfg, "cozy-pack", "test")
check("a put-back mod is never taken out again automatically (says so)", not st["ok"] and pc["excluded"] == [] and "Put back" in st["steps"][-1]["detail"])
for bad in [("../x", 1), ("cozy-pack", "1;2"), ("cozy-pack", "abc")]:
    check(f"put back refuses {bad}", d.modpack_putback(cfg, *bad)["ok"] is False)

# ---- 6. uploads of hand-downloaded mods
sent = []
d.agent_upload = lambda c, args, src, timeout_s=600: (sent.append((args, open(src, "rb").read())), {"ok": True, "msg": "saved"})[1]
tmp = os.path.join(os.environ["DECK_STATE"], "up.bin")
def put(data):
    open(tmp, "wb").write(data); return tmp
r = d.modpack_upload(cfg, "cozy-pack", "Fancy Furniture 1.20.1 [3.2].jar", put(b"PK\x03\x04" + b"x" * 100))
import base64
check(f"upload: name sent safely encoded, bytes as-is ({sent[-1][0] if sent else None})",
      r["ok"] and sent[-1][0][:3] == ["modpack", "upload", "cozy-pack"]
      and base64.urlsafe_b64decode(sent[-1][0][3] + "==").decode() == "Fancy Furniture 1.20.1 [3.2].jar" and sent[-1][1][:2] == b"PK")
n = len(sent)
for name, data, why in [("evil.exe", b"PK" + b"x" * 9, "not .jar"), ("../x.jar", b"PK" + b"x" * 9, "path"), ("a;b.jar", b"PK" + b"x" * 9, "odd character"),
                        ("ok.jar", b"MZ" + b"x" * 9, "not a zip"), ("ok.jar", b"P", "empty")]:
    r = d.modpack_upload(cfg, "cozy-pack", name, put(data))
    check(f"upload refused on the Pi: {why}", r["ok"] is False)
r = d.modpack_upload(cfg, "../x", "ok.jar", put(b"PK" + b"x" * 9))
check("upload refused: bad pack", r["ok"] is False and len(sent) == n)
agent_src = open(os.path.join(here, "..", "k12-windows", "agent", "deck-agent.ps1"), encoding="utf-8").read()
import re
ps_jar = re.search(r"\$JarRe\s*=\s*'([^']+)'", agent_src).group(1)
for s in ["Fancy Furniture 1.20.1 [3.2].jar", "a.jar", "../x.jar", "x.jar.exe", "a b+c(d)-e_f.jar", "-x.jar", "a;b.jar"]:
    check(f"jar name rule agrees on both sides: {s}", bool(d.JAR_RE.match(s)) == bool(re.match(ps_jar, s)))

print(f"\n{fails} failures"); sys.exit(1 if fails else 0)
