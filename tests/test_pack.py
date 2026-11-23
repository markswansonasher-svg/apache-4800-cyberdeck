import sys, types, os, tempfile, json, io, urllib.error
fc=types.ModuleType("fcntl"); fc.LOCK_EX=2; fc.LOCK_NB=4; fc.LOCK_UN=8; fc.flock=lambda f,o: None; sys.modules["fcntl"]=fc
os.environ["DECK_STATE"]=tempfile.mkdtemp(); os.environ["DECK_CONFIG"]=os.path.join(os.environ["DECK_STATE"],"c.json")
json.dump({"k12_user":"x","k12_mac":"aa:bb:cc:dd:ee:ff","pack_warn_s":0,"pack_cool_max_s":0},open(os.environ["DECK_CONFIG"],"w"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi5"))
import decklib as d
d.time.sleep=lambda s: None
fails=0
def check(n,c):
    global fails; print(("PASS " if c else "FAIL ")+n); fails+= not c
cfg=d.load_config()
st={"up":True,"ready":True}
d.k12_up=lambda c: st["up"]; d.k12_agent_ready=lambda c: st["ready"]
d.mc_status=lambda h,p=25565,t=3: {"online":False,"players":0}
d.k12_temps=lambda c: {"cpu":50}
d.net=lambda *a,**k: {"ok":True}
def status(p): return {s["key"]:s["status"] for s in p["steps"]}

# 1. K12 on but agent not linked -> k12off FAIL (don't close the case on it)
st.update(up=True, ready=False)
p=d.pack_up(cfg,"t"); check(f"K12 on but unreachable -> 'shut down by hand' fail {status(p)}", status(p)["k12off"]=="fail" and not p["ok"] and p["done"])
# 2. no world yet -> backup skipped, pack OK
st.update(up=True, ready=True)
def agent(c,*a,timeout_s=60):
    if a[0]=="backup": return {"ok":False,"code":"noworld","error":"No world yet"}
    if a[0]=="off": st["up"]=False
    return {"ok":True}
d.agent=agent
p=d.pack_up(cfg,"t"); check(f"no world yet -> backup skipped, pack ok {status(p)}", status(p)["backup"]=="skip" and p["ok"])
# 3. crash mid-way -> step marked fail, pack done (panel not stuck)
st.update(up=True, ready=True)
def boom(c,*a,timeout_s=60):
    if a[0]=="stop": raise RuntimeError("ssh exploded")
    return {"ok":True}
d.agent=boom
p=d.pack_up(cfg,"t"); s=d.read_json("pack.json")
check(f"unexpected error -> pack.json done, step failed, rest not run {status(s)}", s["done"] and not s["running"] and status(s)["stop"]=="fail" and status(s)["k12off"]=="skip" and not s["ok"])
check("lock released after crash", d.current_action() is None)
# 4. K12 too hot to close -> cool step fails but K12 still shut down
st.update(up=True, ready=True); d.agent=agent; d.k12_temps=lambda c: {"cpu":80}
p=d.pack_up(cfg,"t"); check(f"still hot -> cool fail, K12 still shut down {status(p)}", status(p)["cool"]=="fail" and status(p)["k12off"]=="ok")
# 5. packed from the K12's own screen with "shut the Pi down too"
netcalls=[]
d.net=lambda *a,**k: (netcalls.append(a), {"ok":True})[1]
d.k12_temps=lambda c: {"cpu":80}; st.update(up=True, ready=True)
p=d.pack_up(cfg,"t",pi_off=True)
check("pi_off but pack had problems -> Pi stays on", not p["ok"] and ("poweroff",) not in netcalls
      and "Pi stays on" in d.read_events(0,limit=5)[-1]["text"])
d.k12_temps=lambda c: {"cpu":50}; st.update(up=True, ready=True)
p=d.pack_up(cfg,"t",pi_off=True)
check("pi_off and all good -> Pi shut down after the lock is released",
      p["ok"] and netcalls[-1]==("poweroff",) and d.current_action() is None and p["pi_off"])
netcalls.clear(); st.update(up=True, ready=True)
p=d.pack_up(cfg,"t")
check("normal pack (phone) never shuts the Pi down itself", p["ok"] and ("poweroff",) not in netcalls)

# ---- Tailscale API parsing
class FakeResp(io.BytesIO):
    def __enter__(s): return s
    def __exit__(s,*a): pass
calls=[]
def urlopen(req, timeout=15):
    calls.append((req.get_method(), req.full_url, req.data))
    if req.full_url.endswith("/devices"):
        return FakeResp(json.dumps({"devices":[{"id":"1","nodeId":"nPi","hostname":"deckpi","addresses":["100.64.0.2"]},
                                              {"id":"2","nodeId":"nK12","hostname":"K12-ABC","addresses":["100.101.102.103","fd7a::1"]}]}).encode())
    return FakeResp(json.dumps([{"id":"x","inviteUrl":"https://login.tailscale.com/admin/invite/XYZ","multiUse":False}]).encode())
d.urllib.request.urlopen=urlopen
url=d.tailscale_invite("k","100.101.102.103")
check("invite: finds K12 by IP, never the Pi", url.endswith("XYZ") and "/device/nK12/device-invites" in calls[-1][1] and calls[-1][0]=="POST")
check("invite body: single use, no exit node", json.loads(calls[-1][2])==[{"multiUse":False,"allowExitNode":False}])
calls.clear(); d.tailscale_invite("k",None,"k12-abc"); check("invite: falls back to hostname match", "nK12" in calls[-1][1])
try: d.tailscale_invite("k","100.9.9.9"); ok=False
except RuntimeError as e: ok="Couldn't find the PC" in str(e)
check("invite: unknown PC -> clear error", ok)
def u401(req, timeout=15): raise urllib.error.HTTPError(req.full_url,401,"unauth",{},None)
d.urllib.request.urlopen=u401
try: d.tailscale_invite("k","100.101.102.103"); ok=False
except RuntimeError as e: ok="expired" in str(e) and "deck-tailscale-setup" in str(e)
check("invite: expired key -> tells you how to renew", ok)
print(f"\n{fails} failures"); sys.exit(1 if fails else 0)
