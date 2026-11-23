import sys, types, os, tempfile, json, asyncio
fc=types.ModuleType("fcntl"); fc.LOCK_EX=2; fc.LOCK_NB=4; fc.LOCK_UN=8; fc.flock=lambda f,o: None; sys.modules["fcntl"]=fc
os.environ["DECK_STATE"]=tempfile.mkdtemp(); os.environ["DECK_CONFIG"]=os.path.join(os.environ["DECK_STATE"],"c.json")
json.dump({"k12_user":"x","k12_mac":"aa:bb:cc:dd:ee:ff","discord_owner_ids":["111111111111111111"]},open(os.environ["DECK_CONFIG"],"w"))
os.environ["TS_API_KEY"]="tskey-api-test"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi5"))
import deckbot as b, decklib as d, discord
fails=0
def check(n,c):
    global fails; print(("PASS " if c else "FAIL ")+n); fails+= not c

class Resp:
    def __init__(s): s.sent=[]; s.deferred=False
    async def send_message(s, content=None, **kw): s.sent.append(("msg",content,kw))
    async def defer(s, **kw): s.deferred=True
    async def edit_message(s, **kw): s.sent.append(("edit",kw.get("content"),kw))
class Follow:
    def __init__(s): s.sent=[]
    async def send(s, content=None, **kw): s.sent.append((content,kw))
class Role:
    def __init__(s,n): s.name=n
class User:
    def __init__(s, uid, roles=(), dm_ok=True, name="Tester"):
        s.id=uid; s.roles=[Role(r) for r in roles]; s.display_name=name; s.dm_ok=dm_ok; s.dms=[]
    async def send(s, **kw):
        if not s.dm_ok:
            class R: status=403; reason="Forbidden"
            raise discord.Forbidden(R(), "Cannot send messages to this user")
        s.dms.append(kw)
class Inter:
    def __init__(s, user): s.user=user; s.response=Resp(); s.followup=Follow()
OWNER=User(111111111111111111, name="Owner"); ADMIN=User(222222222222222222, roles=["deck-admin"]); RANDO=User(333333333333333333)

# fakes: K12 up, agent OK, mojang known, tailscale API
d.k12_up=lambda c: True
d.k12_agent_ready=lambda c: True
d.agent=lambda c,*a,timeout_s=60: {"ok":True,"names":["Steve"]}
d.mojang_lookup=lambda n,timeout_s=6: None if n=="Nobody_x" else {"name":n,"uuid":"069a79f4-44e9-4726-a5be-fca90e38aaf5"}
d.write_json("k12_ids.json",{"tailscale_ip":"100.101.102.103","host":"K12"})
ts_calls=[]
def fake_invite(key, ip, host=""):
    ts_calls.append((key,ip,host)); return "https://login.tailscale.com/admin/invite/abc123"
d.tailscale_invite=fake_invite
spawned=[]
real_ct=asyncio.create_task
def run(coro): return asyncio.run(coro)
async def call(cmd, inter, *a, **kw):
    await cmd.callback(inter, *a, **kw)

# permissions
i=Inter(RANDO); run(call(b.wl_add,i,"Lumi")); check("random user can't whitelist", "owner" in i.response.sent[0][1])
i=Inter(ADMIN); run(call(b.wl_add,i,"Lumi")); check("admin role can't whitelist (owner only)", "owner" in i.response.sent[0][1])
i=Inter(ADMIN); run(call(b.wl_list,i)); check("admin can list", i.followup.sent and "Allowed to join" in i.followup.sent[0][0])
i=Inter(RANDO); run(call(b.mc_start,i)); check("random user can't start", "deck-admin" in i.response.sent[0][1])
# owner whitelist
i=Inter(OWNER); run(call(b.wl_add,i,"Lumi")); check(f"owner add -> ✅ ({i.followup.sent[0][0]})", i.followup.sent[0][0].startswith("✅") and i.followup.sent[0][1].get("ephemeral"))
i=Inter(OWNER); run(call(b.wl_add,i,"Nobody_x")); check("typo -> ❌ with hint", i.followup.sent[0][0].startswith("❌") and "spelling" in i.followup.sent[0][0])
# invite
friend=User(444444444444444444, name="Friend")
i=Inter(OWNER); run(call(b.mc_invite,i,friend,"Lumi"))
dm=friend.dms[0]["embed"].description if friend.dms else ""
check("invite: friend got DM with link + join address", "abc123" in dm and "100.101.102.103" in dm and "Lumi" in dm)
check("invite: Tailscale asked with K12's IP", ts_calls and ts_calls[-1][1]=="100.101.102.103")
check("invite: owner report is private and lists 3 ✅", i.followup.sent[0][1].get("ephemeral") and i.followup.sent[0][0].count("✅")==3)
friend2=User(555555555555555555, dm_ok=False, name="Closed")
i=Inter(OWNER); run(call(b.mc_invite,i,friend2,"Alex"))
check("invite: closed DMs -> owner gets the message to forward", "closed" in i.followup.sent[0][0] and "embed" in i.followup.sent[0][1])
b.TS_API_KEY=""
friend3=User(666666666666666666, name="NoKey"); i=Inter(OWNER); run(call(b.mc_invite,i,friend3,"Alex"))
dm=friend3.dms[0]["embed"].description
check("invite without API key: still whitelists + DM says owner sends link", "will send you a Tailscale share link" in dm and "⚠️" in i.followup.sent[0][0])
i=Inter(RANDO); run(call(b.mc_invite,i,friend,"Lumi")); check("random user can't invite", "owner" in i.response.sent[0][1])
# pack
i=Inter(ADMIN); run(call(b.deck_pack,i)); check("admin can't pack (owner only)", "owner" in i.response.sent[0][1])
i=Inter(OWNER); run(call(b.deck_pack,i)); check("owner pack -> private confirm with buttons", isinstance(i.response.sent[0][2].get("view"), discord.ui.View) and i.response.sent[0][2].get("ephemeral"))
# check embed
d.self_test=lambda c, full=False: {"ok":False,"full":full,"t":0,"results":[{"name":"Internet","ok":False,"detail":"none","fix":"Check the Wi-Fi."}]}
i=Inter(ADMIN); run(call(b.deck_check,i,True)); e=i.followup.sent[0][1]["embed"]; check("deck check embed shows fix", "Check the Wi-Fi" in e.description and "attention" in e.title)
# ---- modpacks
P = {"ok": True, "slug": "cozy-pack", "name": "Cozy Pack", "url": "https://www.curseforge.com/minecraft/modpacks/cozy-pack",
     "summary": "cozy", "mc": "1.20.1", "loader": "Fabric", "file_id": 5000, "file_name": "Cozy Pack 2.1", "pinned": False,
     "java": "java17", "logo": "", "can_install": True, "warning": ""}
d.modpack_preview = lambda link: dict(P) if "cozy" in link else (_ for _ in ()).throw(ValueError("That isn't a CurseForge modpack link."))
ran = []
d.modpack_change = lambda c, slug, w, meta=None: ran.append((slug, w, meta))
for u in (ADMIN, RANDO):
    for cmd, args in [(b.mp_install, ("cozy-pack",)), (b.mp_switch, ("cozy-pack",)), (b.mp_vanilla, ())]:
        i = Inter(u); run(call(cmd, i, *args))
        check(f"{cmd.name}: {'admin' if u is ADMIN else 'random user'} refused (owner only)", "owner" in i.response.sent[0][1])
i = Inter(OWNER); run(call(b.mp_install, i, "https://evil.example/x"))
check("install: bad link -> ❌ with the reason", i.followup.sent[0][0].startswith("❌") and "CurseForge" in i.followup.sent[0][0])
i = Inter(OWNER); run(call(b.mp_install, i, "cozy-pack"))
v = i.followup.sent[0][1].get("view")
check("install: private preview with Install + Cancel buttons", isinstance(v, discord.ui.View) and i.followup.sent[0][1].get("ephemeral")
      and i.followup.sent[0][1]["embed"].title == "Cozy Pack" and v.go.label == "Install + switch")
async def press(view, user):
    it = Inter(user); await view.go.callback(it); await asyncio.sleep(0.05); return it
it = run(press(v, OWNER))
check(f"install: pressing Install runs the switch with the preview ({ran[-1][:2] if ran else None})",
      ran and ran[-1][0] == "cozy-pack" and ran[-1][2]["file_id"] == 5000 and "Installing" in it.response.sent[0][1])
P["can_install"] = False; P["warning"] = "Quilt"
i = Inter(OWNER); run(call(b.mp_install, i, "cozy-pack"))
check("install: a pack that can't be installed has no Install button", "can't be installed" in i.followup.sent[0][0] and "view" not in i.followup.sent[0][1])
d.write_json("modpack_list.json", {"active": "", "packs": [{"slug": "cozy-pack"}]})
d.write_json("modpacks.json", {"cozy-pack": {"name": "Cozy Pack", "url": P["url"], "file_name": "Cozy Pack 2.1", "mc": "1.20.1"}})
i = Inter(OWNER); run(call(b.mp_switch, i, "nope"))
check("switch: unknown pack -> ❌", i.response.sent[0][1].startswith("❌"))
i = Inter(OWNER); run(call(b.mp_switch, i, "../etc"))
check("switch: bad name -> ❌", i.response.sent[0][1].startswith("❌"))
i = Inter(OWNER); run(call(b.mp_switch, i, "cozy-pack")); v = i.response.sent[0][2]["view"]
n = len(ran); run(press(v, OWNER))
check("switch: confirm -> switches to that pack", len(ran) == n + 1 and ran[-1][0] == "cozy-pack" and ran[-1][2] is None)
i = Inter(OWNER); run(call(b.mp_vanilla, i)); run(press(i.response.sent[0][2]["view"], OWNER))
check("vanilla: confirm -> switches to vanilla", ran[-1][0] == "")
ch = run(b.pack_names(Inter(RANDO), "coz"))
check("switch name autocompletes from the installed packs", [c.value for c in ch] == ["cozy-pack"] and ch[0].name == "Cozy Pack")
d.modpack_list = lambda c: {"ok": True, "live": True, "active": "cozy-pack", "active_name": "Cozy Pack", "pc_key": True, "pi_key": True,
                            "packs": [{"slug": "cozy-pack", "name": "Cozy Pack", "mc": "1.20.1", "loader": "Fabric", "file_name": "Cozy Pack 2.1",
                                       "removed": [{"id": 581495, "name": "Oculus", "why": "x"}]}]}
d.join_address = lambda c, s=None: "100.101.102.103"
d.preset_get = lambda slug: {"name": "Server keys", "keys": [1, 2, 3]} if slug == "cozy-pack" else None
i = Inter(RANDO); run(call(b.mp_list, i)); e = i.followup.sent[0][1]["embed"]
check("list: shows the pack's keybind preset", "keybind preset: Server keys (3 keys)" in e.description)
check("list: anyone can see it, active pack marked, How to join included",
      "🟢 **Cozy Pack**" in e.description and "Cozy Pack 2.1" in e.fields[0].value and "100.101.102.103" in e.fields[0].value)
check("list: mods the auto-fix took out are shown", "left out on the server (players keep them): Oculus" in e.description)
check("bot posts modpack events to the channel", "modpack" in b.POST_KINDS)
d.add_event("Cozy Pack crashed - the deck took out Oculus (only works on players' computers) and is restarting it (fix 1 of 3).", "warn", "modpack")
ev = d.read_events(0, limit=1)[-1]
check("auto-fix report is a kind the bot posts (no '(asked by' filter)", ev["kind"] in b.POST_KINDS and "(asked by" not in ev["text"])
print(f"\n{fails} failures"); sys.exit(1 if fails else 0)
