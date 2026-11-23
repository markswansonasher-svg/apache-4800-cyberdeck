"""Run every cyberdeck test suite.  python tests/run_tests.py   (or run-tests.cmd)
The suites run at the same time (each in its own process and temp folder), so the whole
run takes about as long as the slowest one (test_agent, which starts PowerShell many times).
test_bot needs discord.py (pip install "discord.py>=2.5,<3"); it is skipped without it."""
import os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
here = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ, PYTHONIOENCODING="utf-8")
SUITES = ["test_deck", "test_new", "test_decknet", "test_pack", "test_panel", "test_modpack",
          "test_autofix", "test_presets", "test_installer", "test_agent", "test_app", "test_bot"]


def run(t):
    if t == "test_bot":
        try:
            __import__("discord")
        except ImportError:
            return t, None, ""
    start = time.time()
    r = subprocess.run([sys.executable, os.path.join(here, t + ".py")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return t, r, time.time() - start


start = time.time()
total_fail = 0
with ThreadPoolExecutor(max_workers=len(SUITES)) as pool:
    for t, r, secs in pool.map(run, SUITES):          # results print in the usual order
        if r is None:
            print(f"{t:14} skipped (pip install discord.py to run it)")
            continue
        out = r.stdout + r.stderr
        p, f = out.count("\nPASS ") + out.startswith("PASS "), out.count("FAIL ")
        total_fail += f + (1 if r.returncode and not f else 0)
        print(f"{t:14} {p} passed, {f} failed   ({secs:.0f} s)" + ("" if r.returncode == 0 else "   <-- see below"))
        if r.returncode:
            print("\n".join(l for l in out.splitlines() if l.startswith("FAIL") or "Error" in l)[-2000:])
print(("\nALL GOOD" if total_fail == 0 else f"\n{total_fail} problem(s)") + f"  ({time.time() - start:.0f} s)")
sys.exit(1 if total_fail else 0)
