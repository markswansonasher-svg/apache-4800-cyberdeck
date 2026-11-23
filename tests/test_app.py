"""The Deck app (k12-windows/app/DeckApp.cs): it builds with Windows' own C# compiler, and every
Pi address it calls is one the Deck Panel answers. Building needs Windows; skipped elsewhere."""
import os, re, shutil, subprocess, sys, tempfile
here = os.path.dirname(os.path.abspath(__file__))
root = os.path.normpath(os.path.join(here, ".."))
app = os.path.join(root, "k12-windows", "app")
fails = 0
def check(n, c):
    global fails; print(("PASS " if c else "FAIL ") + n); fails += not c

src = open(os.path.join(app, "DeckApp.cs"), encoding="utf-8").read()
panel = open(os.path.join(root, "pi5", "deckpanel.py"), encoding="utf-8").read()

# every "/api/..." the app asks for must be handled by deckpanel.py
paths = sorted(set(re.findall(r'"(/api/[a-z/-]+)', src)))
check(f"the app calls {len(paths)} Pi addresses", len(paths) >= 20)
for p in paths:
    base = p.rstrip("/")
    ok = (f'"{base}"' in panel or f'"{base}/' in panel or base.startswith("/api/backups")
          or (base.startswith("/api/modpack/") and base.rsplit("/", 1)[-1] in ("preview", "install", "import", "import-check", "switch", "delete", "clear"))
          or (base.startswith("/api/mc/") and base.rsplit("/", 1)[-1] in ("start", "stop", "off", "restart")))
    check(f"the Pi answers {p}", ok)
check("button presses carry the X-Deck header the panel requires", 'req.Headers["X-Deck"] = "1"' in src)
check("no proxy look-up (it can add seconds to every request)", "req.Proxy = null" in src)
check("stops refreshing while minimised", "FormWindowState.Minimized" in src)
check("full-panel links jump to cards that exist",
      all(f'id="{a}"' in open(os.path.join(root, "pi5", "panel.html"), encoding="utf-8").read()
          for a in re.findall(r'OpenPanel\("#([a-z-]+)"\)', src)))
check("C# 5 only (the compiler that comes with Windows): no $\"...\" strings or ?. operators",
      not re.search(r'[\s(,=]\$"', src) and not re.search(r'\w\?\.\w', src))

local = open(os.path.join(app, "DeckLocal.cs"), encoding="utf-8").read()
check("standalone mode (DeckLocal.cs) is C# 5 too", not re.search(r'[\s(,=]\$"', local) and not re.search(r'\w\?\.\w', local))
build = open(os.path.join(app, "build-app.ps1"), encoding="utf-8").read()
agent_setup = open(os.path.join(root, "k12-windows", "setup-k12-agent.ps1"), encoding="utf-8").read()
check("the build compiles the standalone mode, the PC setup copies it", "*.cs" in build and "app\\*.cs" in agent_setup and "devtools.default.json" in agent_setup)
check("standalone uses the same command rules as the Pi and the agent",
      r'^[A-Za-z0-9 _.,:~@!?#/+=()*\[\]-]{1,200}$' in local and "Use the Stop button" in local and "Use Players allowed" in local)
check("standalone runs the installed agent (admin-only folder), never a copy a user could change",
      r'C:\deck\agent\deck-agent.ps1' in local)
check("the app goes standalone only on the deck's own PC, never in the demo",
      "LocalDeck.Available && !demo" in src)

# the Dev tools page's default list
import json as _json
dt = _json.load(open(os.path.join(app, "devtools.default.json"), encoding="utf-8"))
known = {"name", "exe", "glob", "appid", "cli", "process", "mcp", "envkey", "note", "actions"}
items = [it for g in dt.get("groups", []) for it in g.get("items", [])]
check(f"Dev tools list: {len(dt.get('groups', []))} groups, {len(items)} tools, every one named",
      len(items) >= 10 and all(it.get("name") for it in items) and all(g.get("name") for g in dt["groups"]))
check("Dev tools list uses only keys the page knows (a typo would be ignored silently)",
      all(set(it) <= known for it in items) and all(set(a) <= {"label", "run", "args", "ask_folder"} for it in items for a in it.get("actions", [])))
check("every button has a label and something to run", all(a.get("label") and a.get("run") for it in items for a in it.get("actions", [])))
check("the Unity MCP setup is on the Dev tools page", any("add-unity-mcp.cmd" in a.get("run", "") for it in items for a in it.get("actions", [])))
dts = open(os.path.join(app, "DevTools.cs"), encoding="utf-8").read()
check("the page never overwrites your own devtools.json", "if (!File.Exists(ListPath) && File.Exists(DefaultPath)) File.Copy(DefaultPath, ListPath)" in dts)
check("keys are shown as saved/missing, never their value", "EnvironmentVariableTarget.User" in dts and ": saved" in dts)

if os.name != "nt" or not shutil.which("powershell"):
    print("PASS build skipped (needs Windows)"); sys.exit(1 if fails else 0)
out = os.path.join(tempfile.mkdtemp(), "Deck.exe")
r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                    os.path.join(app, "build-app.ps1"), "-Out", out], capture_output=True, text=True, timeout=180)
warnings = [l for l in r.stdout.splitlines() if "warning" in l or "error" in l]
check(f"Deck.exe builds without errors or warnings {warnings[:3]}", r.returncode == 0 and os.path.exists(out) and not warnings)
check(f"Deck.exe is small ({os.path.getsize(out) // 1024 if os.path.exists(out) else 0} KB)",
      os.path.exists(out) and os.path.getsize(out) < 400 * 1024)

# standalone mode reads an export's manifest.json exactly like the Pi does (decklib.import_meta)
import json, types
fc = types.ModuleType("fcntl"); fc.LOCK_EX = 2; fc.LOCK_NB = 4; fc.LOCK_UN = 8; fc.flock = lambda f, o: None; sys.modules.setdefault("fcntl", fc)
os.environ.setdefault("DECK_STATE", tempfile.mkdtemp())
sys.path.insert(0, os.path.join(root, "pi5"))
import decklib
cases = [("Mark's Cozy Mix!", "1.20.1", "forge-47.2.0"), ("  -- All the Mods 9 --  ", "1.20.1", "neoforge-20.4.1"),
         ("!!!", "1.21.4", "fabric-0.16.9"), ("A" * 90, "1.16.5", "forge-36.2.39"), ("Quilty", "1.20.1", "quilt-0.20"),
         ("Neo", "1.21.1", "neoforge-21.1.77")]
mans = [{"manifestType": "minecraftModpack", "name": n, "version": "1.0", "files": [{"projectID": 1, "fileID": 2}],
         "minecraft": {"version": v, "modLoaders": [{"id": l, "primary": True}]}} for n, v, l in cases]
mf = os.path.join(tempfile.mkdtemp(), "m.json"); json.dump(mans, open(mf, "w"))
ps1 = os.path.join(tempfile.mkdtemp(), "local.ps1")
open(ps1, "w").write(f"""Add-Type -AssemblyName System.Web.Extensions, System.IO.Compression.FileSystem
$asm = [Reflection.Assembly]::LoadFile('{out}'); $t = $asm.GetType('Cyberdeck.LocalDeck'); $ld = [Activator]::CreateInstance($t)
$ser = New-Object Web.Script.Serialization.JavaScriptSerializer
$handle = $t.GetMethod('Handle')
function Ask([string]$path, [string]$key, $value) {{
  $d = New-Object 'System.Collections.Generic.Dictionary[string,object]'
  if ($key) {{ $d.Add($key, $value) }}
  $argv = New-Object object[] 3; $argv.SetValue('POST', 0); $argv.SetValue($path, 1); $argv.SetValue($d, 2)   # SetValue: the real object, not PowerShell's wrapper
  $ser.Serialize($handle.Invoke($ld, $argv).V) }}
foreach ($m in $ser.DeserializeObject([IO.File]::ReadAllText('{mf}'))) {{ Ask '/api/modpack/import-check' 'manifest' $m }}
Ask '/api/console' 'cmd' 'say "hi"'
Ask '/api/console' 'cmd' 'stop'
Ask '/api/pi/reboot' '' $null
Ask '/api/modpack/install' 'link' 'x'
""")
r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps1],
                   capture_output=True, text=True, timeout=120)
got = [json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")]
check(f"standalone answered every check ({len(got)} of {len(cases) + 4}) {r.stderr[-300:]}", len(got) == len(cases) + 4)
for m, g in zip(mans, got):
    want = decklib.import_meta(m)
    same = all(g.get(k) == want[k] for k in ("slug", "name", "mc", "loader", "java", "can_install", "file_name"))
    check(f"standalone import of '{m['name'][:20]}' = the Pi's ({g.get('slug')} / {g.get('loader')} / {g.get('java')})", same)
if len(got) == len(cases) + 4:
    q, s, pi, link = got[len(cases):]
    check("standalone console refuses quotes", q.get("ok") is False and "characters" in q.get("error", ""))
    check("standalone console refuses stop (the Stop button backs up)", s.get("ok") is False and "Stop button" in s.get("error", ""))
    check("standalone: Pi-only things say they need the Pi", pi.get("ok") is False and "Pi" in pi.get("error", ""))
    check("standalone: a CurseForge link says to import an export instead", link.get("ok") is False and "Export profile" in link.get("error", ""))
print(f"\n{fails} failures"); sys.exit(1 if fails else 0)
