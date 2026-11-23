"""The one-go PC installer (install-pc.cmd -> k12-windows/setup-pc.ps1): it parses, -Check changes
nothing, the steps it runs exist, and its helpers (progress file, .env editing, player names) work.
Never installs anything: the helpers are loaded with -Stage lib, which stops before any step."""
import os, subprocess, sys, tempfile, json, shutil
here = os.path.dirname(os.path.abspath(__file__))
root = os.path.normpath(os.path.join(here, ".."))
ps1 = os.path.join(root, "k12-windows", "setup-pc.ps1")
fails = 0
def check(n, c):
    global fails; print(("PASS " if c else "FAIL ") + n); fails += not c

if not shutil.which("powershell"):
    print("PASS skipped (no PowerShell here)"); sys.exit(0)

def ps(script, home):
    env = dict(os.environ, DECK_SETUP_HOME=home)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
    return r.returncode, r.stdout + r.stderr

src = open(ps1, encoding="utf-8").read()
cmd = open(os.path.join(root, "install-pc.cmd"), encoding="utf-8").read()
check("plain ASCII (Windows PowerShell 5.1 reads files without a BOM as ANSI)", src.isascii() and cmd.isascii())
code, out = ps(f"$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('{ps1}',[ref]$null,[ref]$e); $e.Count", root)
check("setup-pc.ps1 has no syntax errors", out.strip() == "0")
check("install-pc.cmd runs setup-pc.ps1 and passes its options on", r"k12-windows\setup-pc.ps1" in cmd and "%*" in cmd)
for f in ["setup-k12-network.ps1", "install-apps.ps1", "setup-curseforge.ps1", "setup-k12-agent.ps1"]:
    check(f"runs {f}, which exists", f in src and os.path.exists(os.path.join(root, "k12-windows", f)))
check("the agent step uses the signed-in user", "'-DeskUser', \"`\"$env:USERNAME`\"\"" in src)
check("admin parts ask for admin (RunAs), the apps part doesn't",
      src.count("-Verb RunAs") == 4 and "& (Join-Path $PSScriptRoot 'install-apps.ps1')" in src)
check("refuses to run as administrator (Spotify)", "if (Test-Admin) {" in src)
check("carries on after the restart (RunOnce, removed again)", "CurrentVersion\\RunOnce" in src and "Remove-ItemProperty" in src)
check("downloads the server images the packs use", "itzg/minecraft-server:java17" in src and "itzg/minecraft-server:latest" in src)
check("EULA defaults to no", "do you agree to it?' $false" in src)
apps = open(os.path.join(root, "k12-windows", "install-apps.ps1"), encoding="utf-8").read()
agent = open(os.path.join(root, "k12-windows", "setup-k12-agent.ps1"), encoding="utf-8").read()
installs = [l for l in (apps + agent).splitlines() if "winget install" in l and not l.strip().startswith("#")]
check(f"every winget install uses the winget source, not the Store ({len(installs)} installs)",
      len(installs) == 3 and all("--source winget" in l for l in installs))
check("no winget options that older winget doesn't know", "--disable-interactivity" not in apps + agent + src)
check("app installs: list refreshed first, retried once, logged with the error code",
      "winget source update" in apps and "foreach ($try in 1, 2)" in apps and "setup-log.txt" in apps and "0x{2:X8}" in apps)
check("winget is checked by finding an app, and repaired (re-register, then reset as admin)",
      "winget search --id Git.Git" in src and "source reset --force" in src and "RegisterByFamilyName" in src)
check("the internet is checked again right before the apps (after the network step)",
      src.index("The internet stopped working after the network step") < src.index("& (Join-Path $PSScriptRoot 'install-apps.ps1')")
      and src.index("-Stage', 'admin'") < src.index("The internet stopped working after the network step"))
check("a wrong clock stops setup with how to fix it", "The clock is wrong" in src and "TrustFailure" in src)

home = tempfile.mkdtemp()
code, out = ps(f"& '{ps1}' -Check", os.path.join(home, "deck"))
check("-Check reports the PC and the steps", code == 0 and "Windows" in out and "Clock" in out and "Link to the Pi" in out and "Nothing was changed" in out)
check("-Check changes nothing (no deck folder made)", not os.path.exists(os.path.join(home, "deck")))

# ---- helpers, loaded without running any step
envf = os.path.join(home, "test.env")
open(envf, "w").write("# comment\nMC_EULA=FALSE\nMC_WHITELIST_PLAYERS=\nMC_OPS=\nMC_EULA_NOTE=x\n")
lib = f". '{ps1}' -Stage lib; "
code, out = ps(lib + f"""
Set-Done 'apps'; Set-Done 'apps'; Set-Done 'docker'; Set-Done 'bogus'
'DONE=' + ((Get-Progress).done -join ',')
'TEST=' + (Test-Done 'docker') + ',' + (Test-Done 'agent')
Set-EnvValue '{envf}' 'MC_EULA' 'TRUE'
Set-EnvValue '{envf}' 'MC_WHITELIST_PLAYERS' 'Steve,alex_2'
Set-EnvValue '{envf}' 'MC_NEW' 'y'
'EULA=' + (Get-EnvValue '{envf}' 'MC_EULA')
try {{ Set-EnvValue '{envf}' 'MC_OPS' "a`nMC_EULA=FALSE"; 'NL=allowed' }} catch {{ 'NL=refused' }}
'N1=' + ((Split-McNames 'Steve, alex_2  Notch') -join '|')
'N2=' + ($null -eq (Split-McNames 'Steve, bad name!'))
'N3=' + ($null -eq (Split-McNames 'ab'))
$e = Split-McNames ''; 'N4=' + $e.Count + ($null -eq $e)
'N5=' + ($null -eq (Split-McNames 'Steve;rm'))
""", os.path.join(home, "deck2"))
lines = dict(l.split("=", 1) for l in out.splitlines() if "=" in l and l.split("=", 1)[0] in
             ("DONE", "TEST", "EULA", "NL", "N1", "N2", "N3", "N4", "N5"))
check(f"progress: each step once, unknown steps dropped ({lines.get('DONE')})", lines.get("DONE") == "apps,docker")
check("progress: done / not done", lines.get("TEST") == "True,False")
check("progress file is plain JSON", json.load(open(os.path.join(home, "deck2", "setup-progress.json")))["done"] == ["apps", "docker"])
envtxt = open(envf).read()
check(".env: values changed in place, comments and similar names kept",
      "MC_EULA=TRUE\n" in envtxt and "MC_EULA_NOTE=x" in envtxt and "# comment" in envtxt
      and "MC_WHITELIST_PLAYERS=Steve,alex_2" in envtxt and envtxt.count("MC_EULA=") == 1)
check(".env: a new setting is added", "MC_NEW=y" in envtxt and lines.get("EULA") == "TRUE")
check(".env: a value with a line break is refused", lines.get("NL") == "refused" and "MC_OPS=\n" in envtxt)
check("player names: commas or spaces", lines.get("N1") == "Steve|alex_2|Notch")
check("player names: bad ones refused (spaces, symbols, too short)", lines.get("N2") == lines.get("N3") == lines.get("N5") == "True")
check("player names: empty = do it later", lines.get("N4") == "0False")
shutil.rmtree(home, ignore_errors=True)
print(f"\n{fails} failures"); sys.exit(1 if fails else 0)
