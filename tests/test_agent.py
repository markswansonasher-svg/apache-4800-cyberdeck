"""The PC agent (deck-agent.ps1) for real, in a throwaway folder: modpack + backup commands.
Needs Windows PowerShell; skipped elsewhere. Docker isn't needed (it counts as 'not running')."""
import json, os, re, shutil, subprocess, sys, tempfile, time

fails = 0
def check(n, c):
    global fails; print(("PASS " if c else "FAIL ") + n); fails += not c

ps = shutil.which("powershell") or shutil.which("powershell.exe")
if os.name != "nt" or not ps:
    print("PASS skipped (needs Windows PowerShell)"); sys.exit(0)

here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(here, "..", "k12-windows", "agent", "deck-agent.ps1")
root = tempfile.mkdtemp(prefix="deck-agent-test-")
game, backups, agentdir = (os.path.join(root, x) for x in ("game-servers", "backups", "agent"))
for p in (game, backups, agentdir):
    os.makedirs(p)
secret = os.path.join(root, "secrets", "cf_api_key.secret")
text = open(src, encoding="utf-8").read()
# point every C:\deck path at the throwaway folder
for a, b in [(r"'C:\deck\game-servers'", f"'{game}'"), (r"'C:\deck\agent\travel-state.json'", f"'{root}\\travel.json'"),
             (r"'C:\deck\backups'", f"'{backups}'"), (r"'C:\deck\secrets\cf_api_key.secret'", f"'{secret}'"),
             (r"'C:\deck\agent\agent.log'", f"'{root}\\agent.log'")]:
    check(f"test can redirect {a}", a in text)
    text = text.replace(a, b)
agent = os.path.join(agentdir, "deck-agent.ps1")
open(agent, "w", encoding="utf-8-sig").write(text)
open(os.path.join(game, ".env"), "w").write("MC_EULA=TRUE\nMC_WHITELIST_PLAYERS=Steve\n")

def run(cmd, raw=False):
    env = dict(os.environ, SSH_ORIGINAL_COMMAND=cmd, PATH=os.environ.get("SystemRoot", r"C:\Windows") + r"\System32")
    r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", agent],
                       capture_output=True, text=not raw, env=env, timeout=60)
    if raw:
        return r
    line = next((l for l in reversed(r.stdout.splitlines()) if l.startswith("{")), "")
    try:
        return json.loads(line)
    except ValueError:
        return {"ok": None, "raw": r.stdout + r.stderr}

# checks that only read (refusals) start their PowerShells side by side: each start takes ~2 s
from concurrent.futures import ThreadPoolExecutor
def run_many(fn, items):
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(fn, items))

# --- input checks: nothing odd gets through
BAD = ["modpack apply ../evil latest java21 8 10", "modpack apply Evil latest java21 8 10",
       "modpack apply ok-pack 12ab java21 8 10", "modpack apply ok-pack latest java99 8 10",
       "modpack apply ok-pack latest java21 30 32", "modpack apply ok-pack latest java21 8 8",
       "modpack apply ok-pack latest java21 8", "modpack apply ok;pack latest java21 8 10",
       "modpack delete ../data", "backup ..\\data"]
for bad, r in zip(BAD, run_many(run, BAD)):
    check(f"refused: {bad}", r.get("ok") is False and "error" in r)
check("nothing was written by refused commands", not os.path.exists(os.path.join(game, "packs")))

# --- apply a pack
r = run("modpack apply cozy-pack 4812345 java17 8 10")
check(f"apply works ({r.get('msg') or r})", r.get("ok") and r.get("active") == "cozy-pack")
envf = open(os.path.join(game, "packs", "cozy-pack.env")).read()
check("pack env has the CurseForge + memory + folder settings",
      all(s in envf for s in ["MC_TYPE=AUTO_CURSEFORGE", "CF_SLUG=cozy-pack", "CF_FILE_ID=4812345", "MC_IMAGE_TAG=java17",
                              "MC_MEMORY=8G", "MC_MEM_LIMIT=10g", "MC_DATA_DIR=./data/packs/cozy-pack",
                              "CF_API_KEY_FILE=/run/secrets/cf_api_key"]))
check("active-pack.txt names it", open(os.path.join(game, "active-pack.txt")).read().strip() == "cozy-pack")
r = run("info")
check("info reports the active pack", r.get("pack") == "cozy-pack")
r = run("start")
check(f"start without a CurseForge key -> clear message ({r.get('error')})", r.get("ok") is False and "setup-curseforge" in (r.get("error") or ""))

# exclusions survive a re-apply (e.g. a version change)
with open(os.path.join(game, "packs", "cozy-pack.env"), "a") as f:
    f.write("")
envf = envf.replace("CF_EXCLUDE_MODS=", "CF_EXCLUDE_MODS=iris,sodium-extra")
open(os.path.join(game, "packs", "cozy-pack.env"), "w").write(envf)
r = run("modpack apply cozy-pack latest java17 8 10")
envf = open(os.path.join(game, "packs", "cozy-pack.env")).read()
check("re-apply keeps the pack's excluded mods, 'latest' leaves CF_FILE_ID empty",
      "CF_EXCLUDE_MODS=iris,sodium-extra" in envf and "CF_FILE_ID=\n" in envf)

# --- whitelist follows the active world
vworld = os.path.join(game, "data", "minecraft")
os.makedirs(os.path.join(vworld, "world"))
r = run("modpack vanilla")
check("vanilla works", r.get("ok") and not os.path.exists(os.path.join(game, "active-pack.txt")))
r = run("whitelist add Alex 069a79f4-44e9-4726-a5be-fca90e38aaf5")
check("whitelist (server stopped) writes vanilla's whitelist.json", "Alex" in open(os.path.join(vworld, "whitelist.json")).read())
run("modpack apply cozy-pack latest java17 8 10")
pwl = os.path.join(game, "data", "packs", "cozy-pack", "whitelist.json")
check("switching to the pack carries the player list over", os.path.exists(pwl) and "Alex" in open(pwl).read())

# --- backups are named per world
os.makedirs(os.path.join(game, "data", "packs", "cozy-pack", "world", "region"))
open(os.path.join(game, "data", "packs", "cozy-pack", "world", "level.dat"), "w").write("x")
os.makedirs(os.path.join(game, "data", "packs", "cozy-pack", "mods"))
open(os.path.join(game, "data", "packs", "cozy-pack", "mods", "big.jar"), "w").write("x" * 1000)
r = run("backup")
check(f"backup of the active pack is named pack-cozy-pack-... ({r.get('file')})",
      r.get("ok") and re.match(r"^pack-cozy-pack-\d{8}-\d{6}\.zip$", r.get("file", "")))
import zipfile
names = zipfile.ZipFile(os.path.join(backups, r["file"])).namelist()
check("pack backup has the world but not the mods folder (re-downloads)", "world/level.dat" in names and not any(n.startswith("mods/") for n in names))
open(os.path.join(vworld, "world", "level.dat"), "w").write("v")
r = run("backup minecraft-not-a-pack")
check("backup of an unknown pack is refused", r.get("ok") is False)
run("modpack vanilla")
r = run("backup")
check(f"vanilla backup keeps the old name minecraft-... ({r.get('file')})", r.get("ok") and r.get("file", "").startswith("minecraft-"))
rb = run("backup-get " + r["file"], raw=True)
check("backup-get sends the zip bytes", rb.returncode == 0 and rb.stdout[:2] == b"PK")
check("backup-get refuses a path", run("backup-get ..\\game-servers\\.env", raw=True).returncode != 0)

# --- list
r = run("modpack list")
p = (r.get("packs") or [{}])[0]
check(f"list shows the pack, its world and that vanilla is on ({r})",
      r.get("ok") and r.get("active") == "" and p.get("slug") == "cozy-pack" and p.get("has_world") and p.get("java") == "java17" and r.get("key") is False)

# --- delete: never the active one, never without a fresh backup
run("modpack apply cozy-pack latest java17 8 10")
r = run("modpack delete cozy-pack")
check("can't delete the pack that's on", r.get("ok") is False and "switch" in r.get("error", ""))
run("modpack vanilla")
for f in os.listdir(backups):
    if f.startswith("pack-"):
        old = time.time() - 3600
        os.utime(os.path.join(backups, f), (old, old))
r = run("modpack delete cozy-pack")
check("can't delete without a fresh backup", r.get("ok") is False and "backup" in r.get("error", ""))
run("backup cozy-pack")
r = run("modpack delete cozy-pack")
check(f"delete after a fresh backup works ({r.get('msg') or r.get('error')})", r.get("ok"))
check("pack world + settings gone, its backups and vanilla's world kept",
      not os.path.exists(os.path.join(game, "data", "packs", "cozy-pack")) and not os.path.exists(os.path.join(game, "packs", "cozy-pack.env"))
      and any(f.startswith("pack-cozy-pack-") for f in os.listdir(backups)) and os.path.exists(os.path.join(vworld, "world", "level.dat")))

# ---- phase 3: mods inside jars, leave out / put back, uploads
import base64, io
run("modpack apply cozy-pack 5000 java17 8 10")
mods = os.path.join(game, "data", "packs", "cozy-pack", "mods")
os.makedirs(mods, exist_ok=True)
def jar(name, files):
    with zipfile.ZipFile(os.path.join(mods, name), "w") as z:
        for k, v in files.items(): z.writestr(k, v)
jar("oculus-mc1.19.2-1.2.8a.jar", {"META-INF/mods.toml": 'modLoader="javafml"\n[[mods]]\nmodId="oculus"\n', "oculus.mixins.json": "{}"})
jar("sodium-fabric-0.5.jar", {"fabric.mod.json": '{"schemaVersion":1,"id":"sodium","name":"Sodium"}', "sodium.mixins.json": "{}"})
jar("mixin.undergroundjungle-1.0.jar", {"META-INF/neoforge.mods.toml": 'modId = "undergroundjungle"', "mixin.undergroundjungle.json": "{}"})
open(os.path.join(mods, "broken.jar"), "w").write("not a zip")
r = run("modpack mods cozy-pack")
by = {m["file"]: m for m in r.get("mods", [])}
check(f"mods: reads mod IDs from Forge/NeoForge/Fabric jars ({ {k: v['ids'] for k, v in by.items()} })",
      by["oculus-mc1.19.2-1.2.8a.jar"]["ids"] == ["oculus"] and by["sodium-fabric-0.5.jar"]["ids"] == ["sodium"]
      and by["mixin.undergroundjungle-1.0.jar"]["ids"] == ["undergroundjungle"])
check("mods: lists each jar's mixin configs", "oculus.mixins.json" in by["oculus-mc1.19.2-1.2.8a.jar"]["mixins"]
      and "mixin.undergroundjungle.json" in by["mixin.undergroundjungle-1.0.jar"]["mixins"])
check("mods: a broken jar doesn't break the list", by.get("broken.jar", {}).get("ids") == [])
envp = os.path.join(game, "packs", "cozy-pack.env")
open(envp, "a").write("")
txt = re.sub(r"(?m)^CF_EXCLUDE_MODS=.*$", "CF_EXCLUDE_MODS=my-hand-slug", open(envp).read())
open(envp, "w").write(txt)
r = run("modpack exclude cozy-pack 581495")
e = open(envp).read()
check(f"exclude: adds the project, keeps hand-typed slugs, turns on re-sync ({r.get('excluded')})",
      "CF_EXCLUDE_MODS=my-hand-slug,581495" in e and "CF_FORCE_SYNCHRONIZE=true" in e)
run("modpack exclude cozy-pack 581495")
check("exclude twice -> listed once", open(envp).read().count("581495") == 1)
r = run("modpack include cozy-pack 581495")
e = open(envp).read()
check("include: out of the exclude list, into force-include", "CF_EXCLUDE_MODS=my-hand-slug\n" in e and "CF_FORCE_INCLUDE_MODS=581495" in e)
run("modpack synced cozy-pack")
check("synced: re-sync off again", "CF_FORCE_SYNCHRONIZE=\n" in open(envp).read())
BAD = ["modpack exclude cozy-pack abc", "modpack exclude cozy-pack 1;2", "modpack exclude nope 123", "modpack include cozy-pack"]
for bad, r in zip(BAD, run_many(run, BAD)):
    check(f"refused: {bad}", r.get("ok") is False)

def b64(s): return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
def upload(name, data, size=None):
    env = dict(os.environ, SSH_ORIGINAL_COMMAND=f"modpack upload cozy-pack {b64(name)} {len(data) if size is None else size}")
    r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", agent],
                       input=data, capture_output=True, env=env, timeout=60)
    line = next((l for l in reversed(r.stdout.decode(errors="replace").splitlines()) if l.startswith("{")), "{}")
    return json.loads(line)
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z: z.writestr("fabric.mod.json", '{"id":"fancy"}')
good = buf.getvalue() + os.urandom(200000)       # bigger than one read
dl = os.path.join(game, "downloads", "cozy-pack")
r = upload("Fancy Furniture 1.20.1 [3.2].jar", good)
saved = os.path.join(dl, "Fancy Furniture 1.20.1 [3.2].jar")
check(f"upload: a .jar with spaces/brackets arrives byte-for-byte ({r.get('msg') or r.get('error')})",
      r.get("ok") and os.path.exists(saved) and open(saved, "rb").read() == good)
CASES = [("evil.exe", good, None, "not a .jar"), ("..\\..\\x.jar", good, None, "path in the name"),
         ("a/b.jar", good, None, "slash in the name"), ("notzip.jar", b"MZ" + b"x" * 100, None, "not a zip"),
         ("short.jar", good[:1000], 5000, "fewer bytes than promised"), ("huge.jar", b"PK", 200 * 2**20, "over 100 MB")]
for (name, data, size, why), r in zip(CASES, run_many(lambda c: upload(c[0], c[1], c[2]), CASES)):
    check(f"upload refused: {why}", r.get("ok") is False)
check("refused uploads leave nothing behind", sorted(os.listdir(dl)) == ["Fancy Furniture 1.20.1 [3.2].jar"])

# ---- phase 4: keybind preset onto the server
def send(cmd, data):
    env = dict(os.environ, SSH_ORIGINAL_COMMAND=cmd)
    r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", agent],
                       input=data, capture_output=True, env=env, timeout=60)
    line = next((l for l in reversed(r.stdout.decode(errors="replace").splitlines()) if l.startswith("{")), "{}")
    return json.loads(line)
preset = json.dumps({"format": "cyberdeck-keys/1", "name": "Cozy Pack", "keys": [{"id": "key.jump", "key": "key.keyboard.space"}]}).encode()
r = send(f"modpack preset cozy-pack {len(preset)}", preset)
pf = os.path.join(game, "data", "packs", "cozy-pack", "config", "keypresets", "server.json")
check(f"preset lands where the server's keybind mod reads it ({r.get('msg') or r.get('error')})", r.get("ok") and open(pf, "rb").read() == preset)
CASES = [(f"modpack preset cozy-pack {len(b)}", b, why) for b, why in
         [(b'{"format":"other/1","keys":[]}', "wrong format"), (b"not json", "not JSON"),
          (json.dumps({"format": "cyberdeck-keys/1"}).encode(), "no keys")]]
CASES += [(f"modpack preset cozy-pack {600 * 1024}", b"{}", "over 512 KB"),
          (f"modpack preset nope {len(preset)}", preset, "unknown pack")]
for (cmd, data, why), r in zip(CASES, run_many(lambda c: send(c[0], c[1]), CASES)):
    check(f"preset refused: {why}", r.get("ok") is False)
check("refused presets leave the old one alone", open(pf, "rb").read() == preset)
r = run("modpack preset cozy-pack clear")
check("clear removes it", r.get("ok") and not os.path.exists(pf))

# ---- server settings (the Deck app's Server page)
run("modpack vanilla")
r = run("settings get")
check(f"settings get: .env values, no server.properties yet ({r.get('env')})",
      r.get("ok") and r.get("env", {}).get("MC_EULA") is None and r.get("props_exist") is False)
r = run(f"settings set env MC_MOTD {b64('Friday night: build party!')}")
check("MOTD with spaces and : ! saved", r.get("ok") and "MC_MOTD=Friday night: build party!" in open(os.path.join(game, ".env")).read())
r = run(f"settings set prop view-distance {b64('8')}")
props = os.path.join(game, "data", "minecraft", "server.properties")
check("view-distance lands in the active world's server.properties", r.get("ok") and "view-distance=8" in open(props).read())
open(props, "a").write("level-seed=keep-me\n")
run(f"settings set prop view-distance {b64('12')}")
t = open(props).read()
check("changing it again replaces the line, other lines kept", t.count("view-distance=") == 1 and "view-distance=12" in t and "level-seed=keep-me" in t)
check("no byte-order mark (Java and compose dislike it)", not open(props, "rb").read().startswith(b"\xef\xbb\xbf"))
BAD = [f"settings set env MC_MOTD {b64('costs $5')}", f"settings set env MC_MOTD {b64(chr(34) + 'hi' + chr(34))}",
       f"settings set env MC_MOTD {b64('a #comment')}", f"settings set env MC_EULA {b64('FALSE')}",
       f"settings set env MC_MEMORY {b64('8G')}", f"settings set prop view-distance {b64('64')}",
       f"settings set prop gamemode {b64('god')}", f"settings set prop level-seed {b64('1')}",
       f"settings set file MC_MOTD {b64('x')}", "settings set env MC_MOTD !!!", "settings set env MC_MOTD"]
for bad, r in zip(BAD, run_many(run, BAD)):
    check(f"refused: {bad[:40]}", r.get("ok") is False)
check("refused settings changed nothing", "MC_EULA=TRUE" in open(os.path.join(game, ".env")).read()
      and "level-seed=keep-me" in open(props).read())
r = run("modpack apply cozy-pack latest java17 8 10")
r = run("settings get")
check(f"with a pack on: its own memory is reported as set by the pack ({r.get('pack_sets')})",
      r.get("pack") == "cozy-pack" and "MC_MEMORY" in (r.get("pack_sets") or []))
run(f"settings set prop pvp {b64('false')}")
check("a pack's world gets its own server.properties",
      "pvp=false" in open(os.path.join(game, "data", "packs", "cozy-pack", "server.properties")).read() and "pvp" not in open(props).read())
r = run("perf")
check("perf with Minecraft stopped -> running false, no error", r.get("ok") and r.get("running") is False)

# ---- an imported CurseForge export zip (the Deck app copies it to imports\<pack>.zip)
r = run("modpack apply my-mix zip java17 8 10")
check("zip pack without its zip on the PC -> clear message", r.get("ok") is False and "imports" in r.get("error", ""))
imports = os.path.join(game, "imports"); os.makedirs(imports, exist_ok=True)
open(os.path.join(imports, "my-mix.zip"), "wb").write(b"PK\x03\x04 not really a zip")
r = run("modpack apply my-mix zip java17 8 10")
check("a broken zip is refused", r.get("ok") is False)
with zipfile.ZipFile(os.path.join(imports, "my-mix.zip"), "w") as z:
    z.writestr("overrides/config/x.toml", "a=1")
r = run("modpack apply my-mix zip java17 8 10")
check("a zip without manifest.json (not an export) is refused", r.get("ok") is False and "manifest" in r.get("error", ""))
with zipfile.ZipFile(os.path.join(imports, "my-mix.zip"), "w") as z:
    z.writestr("manifest.json", '{"manifestType":"minecraftModpack"}'); z.writestr("overrides/config/x.toml", "a=1")
r = run("modpack apply my-mix zip java17 8 10")
e = open(os.path.join(game, "packs", "my-mix.env")).read()
check(f"export zip applied: the server installs from /imports/my-mix.zip ({r.get('msg') or r.get('error')})",
      r.get("ok") and "CF_MODPACK_ZIP=/imports/my-mix.zip" in e and "CF_SLUG=my-mix\n" in e and "CF_FILE_ID=\n" in e
      and "CF_FORCE_SYNCHRONIZE=true" in e and "MC_TYPE=AUTO_CURSEFORGE" in e)
r = run("modpack list")
check("list marks it as an imported pack", any(p.get("slug") == "my-mix" and p.get("source") == "zip" for p in r.get("packs", [])))
run("modpack apply cozy-pack latest java17 8 10")
check("a normal pack has no zip setting", "CF_MODPACK_ZIP=\n" in open(os.path.join(game, "packs", "cozy-pack.env")).read())
run("backup my-mix")
r = run("modpack delete my-mix")
check("deleting the pack removes its zip too", r.get("ok") and not os.path.exists(os.path.join(imports, "my-mix.zip")))

shutil.rmtree(root, ignore_errors=True)
print(f"\n{fails} failures"); sys.exit(1 if fails else 0)
