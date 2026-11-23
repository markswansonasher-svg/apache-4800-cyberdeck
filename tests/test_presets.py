"""Keybind presets on the Pi: checking a file, saving, sending it to the PC, How to join, and the
same format rules as docs/keybind-preset-format.md."""
import sys, types, os, tempfile, json
fc = types.ModuleType("fcntl"); fc.LOCK_EX = 2; fc.LOCK_NB = 4; fc.LOCK_UN = 8; fc.flock = lambda f, o: None; sys.modules["fcntl"] = fc
os.environ["DECK_STATE"] = tempfile.mkdtemp(); os.environ["DECK_CONFIG"] = os.path.join(os.environ["DECK_STATE"], "c.json")
json.dump({"k12_user": "x", "k12_mac": "aa:bb:cc:dd:ee:ff", "mc_join_address": "deck.example"}, open(os.environ["DECK_CONFIG"], "w"))
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(here, "..", "pi5"))
import decklib as d
d.time.sleep = lambda s: None
fails = 0
def check(n, c):
    global fails; print(("PASS " if c else "FAIL ") + n); fails += not c
cfg = d.load_config()
sample = open(os.path.join(here, "presets", "sample-export.json"), encoding="utf-8").read()

# ---- checking files
p = d.clean_preset(sample)
check(f"sample export accepted ({len(p['keys'])} keys)", p["format"] == "cyberdeck-keys/1" and len(p["keys"]) == 24 and p["minecraft"] == "1.20.1")
jei = next(k for k in p["keys"] if k["id"] == "key.jei.showRecipe")
check("modifier, default, label, category kept", jei == {"id": "key.jei.showRecipe", "key": "key.keyboard.r", "modifier": "SHIFT",
      "default": "key.keyboard.r", "label": "Show Recipe", "category": "jei.key.category"})
check("unknown fields dropped (e.g. _note)", "_note" not in p)
check("missing modifier -> NONE", next(k for k in p["keys"] if k["id"] == "key.jump")["modifier"] == "NONE")
check("unbound key allowed", next(k for k in p["keys"] if k["id"] == "key.minimap.waypoint")["key"] == "key.keyboard.unknown")
check("name can be changed on save", d.clean_preset(sample, name="Server keys")["name"] == "Server keys")
odd = json.loads(sample); odd["keys"][0]["modifier"] = "HYPER"; odd["keys"][1]["label"] = "Use\x00\x1b[31m" + "x" * 300
q = d.clean_preset(odd)
check("odd modifier -> NONE, control characters + long labels trimmed", q["keys"][0]["modifier"] == "NONE"
      and "\x00" not in q["keys"][1]["label"] and "\x1b" not in q["keys"][1]["label"] and len(q["keys"][1]["label"]) <= 120)
dup = json.loads(sample); dup["keys"].append(dict(dup["keys"][0], key="key.keyboard.z"))
check("a key listed twice: the first one counts", len(d.clean_preset(dup)["keys"]) == 24 and d.clean_preset(dup)["keys"][0]["key"] == "key.mouse.left")
def refused(data, why):
    try: d.clean_preset(data); ok = False; msg = ""
    except ValueError as e: ok = True; msg = str(e)
    check(f"refused: {why} ({msg[:50]})", ok and msg)
refused("not json", "not JSON")
refused(json.dumps({"format": "other/1", "keys": [{"id": "key.jump", "key": "key.keyboard.space"}]}), "wrong format")
refused(json.dumps({"format": "cyberdeck-keys/1", "keys": []}), "no keys")
refused(json.dumps({"format": "cyberdeck-keys/1", "keys": [{"id": "key jump", "key": "key.keyboard.space"}]}), "space in key name")
refused(json.dumps({"format": "cyberdeck-keys/1", "keys": [{"id": "key.jump", "key": "key.keyboard.SPACE;rm"}]}), "odd key")
refused(json.dumps({"format": "cyberdeck-keys/1", "keys": [{"id": "key.jump", "key": "space"}]}), "key without key.keyboard.")
refused(json.dumps({"format": "cyberdeck-keys/1", "keys": [{"id": "k%d" % i, "key": "key.keyboard.a"} for i in range(3001)]}), "too many keys")
refused("x" * (513 * 1024), "over 512 KB")
refused(json.dumps([1, 2]), "a list, not a preset")

# ---- saving + sending to the PC
up = {"pc": True}
d.k12_up = lambda c: up["pc"]
pushed = []
d.agent_upload = lambda c, args, src, timeout_s=600: (pushed.append((args, open(src, "rb").read())), {"ok": True})[1]
calls = []
d.agent = lambda c, *a, timeout_s=60: (calls.append(a), {"ok": True})[1]
r = d.preset_save(cfg, "cozy-pack", sample, "test")
check(f"save with the PC on -> on the server too ({r['msg']})", r["ok"] and r["on_pc"] and pushed[-1][0][:3] == ["modpack", "preset", "cozy-pack"])
sent = json.loads(pushed[-1][1])
check("what's sent is the checked copy, with its byte count", sent == d.preset_get("cozy-pack") and pushed[-1][0][3] == str(len(pushed[-1][1])))
up["pc"] = False; n = len(pushed)
r = d.preset_save(cfg, "cozy-pack", sample, "test")
check("save with the PC off -> kept on the Pi, says when it's sent", r["ok"] and not r["on_pc"] and len(pushed) == n and "next starts" in r["msg"])
check("bad pack name refused", d.preset_save(cfg, "../x", sample)["ok"] is False and d.preset_get("../x") is None)
check("bad file refused, old preset kept", d.preset_save(cfg, "cozy-pack", "junk")["ok"] is False and d.preset_get("cozy-pack")["name"] == "Cozy Pack test keys")

# switching to the pack puts the preset on the server (made while the PC was off)
pc = {"running": False}
up["pc"] = True; d.k12_agent_ready = lambda c: True
d.mc_status = lambda h, p=25565, t=3: {"online": pc["running"], "players": 0}
def agent(c, *a, timeout_s=60):
    calls.append(a)
    if a[0] == "backup": return {"ok": False, "code": "noworld"}
    if a[0] == "start": pc["running"] = True; return {"ok": True, "since": 1763700000}
    if a[0] == "logs": return {"ok": True, "running": True, "lines": []}
    return {"ok": True}
d.agent = agent
d.write_json("modpacks.json", {"cozy-pack": {"slug": "cozy-pack", "name": "Cozy Pack", "url": "https://www.curseforge.com/minecraft/modpacks/cozy-pack",
                                             "file_name": "Cozy Pack 2.1", "mc": "1.20.1", "loader": "Fabric", "java": "java17", "file_id": 5000}})
n = len(pushed)
st = d.modpack_change(cfg, "cozy-pack", "test")
check(f"switch puts the preset on the server ({st['steps'][3]['detail']})", st["ok"] and len(pushed) == n + 1 and "keybind preset" in st["steps"][3]["detail"])
order = [a[0] if a[0] != "modpack" else a[1] for a in calls]
ev = [e["text"] for e in d.read_events(0, limit=20)]
check("How to join tells players about the server preset prompt",
      any("How to join" in t and "Add and switch to the server preset?" in t for t in ev))
check("How to join for a pack without a preset doesn't mention it", "server preset" not in d.how_to_join(cfg, {"slug": "other", "name": "Other", "url": "u"}))

# delete
r = d.preset_delete(cfg, "cozy-pack", "test")
check("delete: gone from the Pi, cleared on the server", r["ok"] and d.preset_get("cozy-pack") is None and calls[-1] == ("modpack", "preset", "cozy-pack", "clear"))
check("delete again -> says there's none", d.preset_delete(cfg, "cozy-pack")["ok"] is False)

# ---- the format doc and the code agree
doc = open(os.path.join(here, "..", "docs", "keybind-preset-format.md"), encoding="utf-8").read()
check("format name in the doc matches the code", d.PRESET_FORMAT in doc and "config/keypresets/server.json" in doc)
check("all modifiers in the doc", all(f"`{m}`" in doc for m in d.MODIFIERS))
agent_src = open(os.path.join(here, "..", "k12-windows", "agent", "deck-agent.ps1"), encoding="utf-8").read()
check("the PC agent checks the same format name and folder", f"'{d.PRESET_FORMAT}'" in agent_src and "config\\keypresets" in agent_src)
# ---- the Keybind Presets mod (Java) uses the same rules
import re
jp = os.path.join(here, "..", "keybind-mod", "common", "src", "main", "java", "cyberdeck", "keybindpresets", "preset", "PresetFormat.java")
if os.path.exists(jp):
    js = open(jp, encoding="utf-8").read()
    jre = lambda name: re.search(name + r'\s*=\s*Pattern\.compile\("(.+?)"\);', js).group(1).replace("\\\\", "\\")
    check("mod: same format name", f'FORMAT = "{d.PRESET_FORMAT}"' in js)
    check("mod: same key-name rule", jre("KEY_ID") == d.KEY_ID_RE.pattern)
    check("mod: same key rule", jre("KEY_NAME") == d.KEY_NAME_RE.pattern)
    check("mod: same limits", f"MAX_KEYS = {d.MAX_PRESET_KEYS};" in js and "MAX_BYTES = 512 * 1024;" in js)
    check("mod: same modifiers", all(f'"{m}"' in js for m in d.MODIFIERS))
    sp = open(os.path.join(os.path.dirname(jp), "..", "server", "ServerPreset.java"), encoding="utf-8").read()
    check("mod reads the preset where the deck puts it", 'Path.of("config", "keypresets", "server.json")' in sp)
print(f"\n{fails} failures"); sys.exit(1 if fails else 0)
