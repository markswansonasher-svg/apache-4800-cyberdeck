import sys, types, os, json, socket, threading, tempfile, importlib.machinery, importlib.util
# fcntl is Linux-only; stub it so decklib imports on Windows
fc = types.ModuleType("fcntl"); fc.LOCK_EX=2; fc.LOCK_NB=4; fc.LOCK_UN=8
_held=set()
def flock(f, op):
    if op & 8: _held.discard(f.name); return
    if f.name in _held: raise BlockingIOError()
    _held.add(f.name)
fc.flock=flock; sys.modules["fcntl"]=fc
os.environ["DECK_STATE"]=tempfile.mkdtemp(); os.environ["DECK_CONFIG"]=os.path.join(os.environ["DECK_STATE"],"nope.json")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi5"))
import decklib as d
fails=0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ")+name); fails += (not cond)

# ---- 1. Minecraft status ping against a fake server
def fake_server(sock, resp):
    conn,_=sock.accept()
    with conn:
        f=conn.makefile("rb")
        def rv():
            r=0
            for i in range(5):
                b=f.read(1)[0]; r|=(b&0x7f)<<(7*i)
                if not b&0x80: return r
        ln=rv(); hs=f.read(ln); assert hs[0]==0
        ln=rv(); f.read(ln)
        js=json.dumps(resp).encode()
        body=d._varint(0)+d._varint(len(js))+js
        conn.sendall(d._varint(len(body))+body)
srv=socket.socket(); srv.bind(("127.0.0.1",0)); srv.listen(1); port=srv.getsockname()[1]
resp={"version":{"name":"1.21.4","protocol":769},"players":{"max":10,"online":2,"sample":[{"name":"Steve","id":"a"},{"name":"Alex","id":"b"}]},
      "description":{"text":"Apache ","extra":[{"text":"4800 deck"}]}}
threading.Thread(target=fake_server,args=(srv,resp),daemon=True).start()
st=d.mc_status("127.0.0.1",port)
check("mc_status online + players", st["online"] and st["players"]==2 and st["names"]==["Alex","Steve"] and st["version"]=="1.21.4" and st["motd"]=="Apache 4800 deck")
check("mc_status closed port = offline", d.mc_status("127.0.0.1",1,0.5)["online"] is False)
check("varint 300", d._varint(300)==b"\xac\x02" and d._varint(-1)==b"\xff\xff\xff\xff\x0f")

# ---- 2. LibreHardwareMonitor parsing
lhm={"Text":"Sensor","Children":[{"Text":"K12","Children":[
  {"Text":"AMD Ryzen 7 8845HS","ImageURL":"images_icon/cpu.png","Children":[{"Text":"Temperatures","ImageURL":"images_icon/temperature.png","Children":[
     {"Text":"Core (Tctl/Tdie)","Value":"71,5 \u00b0C","Type":"Temperature","SensorId":"/amdcpu/0/temperature/2"},
     {"Text":"CCD1 (Tdie)","Value":"80.0 \u00b0C","Type":"Temperature"}]},
     {"Text":"Clocks","Children":[{"Text":"Core #1","Value":"4900 MHz","Type":"Clock"}]}]},
  {"Text":"AMD Radeon 780M","ImageURL":"images_icon/ati.png","Children":[{"Text":"Temperatures","Children":[{"Text":"GPU Core","Value":"66.0 \u00b0C","Type":"Temperature"}]}]},
  {"Text":"Samsung 990","ImageURL":"images_icon/hdd.png","Children":[{"Text":"Temperatures","Children":[{"Text":"Temperature","Value":"48.0 \u00b0C","Type":"Temperature"},{"Text":"Temperature 2","Value":"55.0 \u00b0C","Type":"Temperature"}]}]}]}]}
t=d.parse_lhm(lhm)
check(f"parse_lhm picks Tctl, comma decimal, gpu, drive max  {t}", t=={"cpu":71.5,"gpu":66.0,"drive":55.0})
check("parse_lhm empty", d.parse_lhm({"Text":"Sensor","Children":[]})=={})

# ---- 3. events + action lock
e1=d.add_event("hello"); e2=d.add_event("world","bad")
evs=d.read_events(e1["id"])
check("events after id", len(evs)==1 and evs[0]["text"]=="world")
with d._ActionLock("start Minecraft","tester"):
    try:
        with d._ActionLock("stop","other"): busy=False
    except d.Busy as e: busy="tester" in str(e)
check("second action is refused while one runs", busy)
check("action cleared after", d.current_action() is None)
for i in range(2100): d.add_event(f"e{i}")
d.trim_events(); check("trim events", len(d.read_events(0, limit=10**6))==500)

# ---- 4. deck-net helpers
spec=importlib.util.spec_from_loader("decknet", importlib.machinery.SourceFileLoader("decknet", os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi5"), "deck-net")))
dn=importlib.util.module_from_spec(spec); spec.loader.exec_module(dn)
check("split_terse escaped colon", dn.split_terse(r"*:Cafe\:Guest:72:WPA2")==["*","Cafe:Guest","72","WPA2"])
check("valid_ssid", dn.valid_ssid("Hotel WiFi") and not dn.valid_ssid("") and not dn.valid_ssid("a"*33) and not dn.valid_ssid("x\ny"))
r="-A DOCKER-USER -s 10.48.0.0/24 -i eth0 ! -o eth0 -m comment --comment deck-travel -j ACCEPT"
check("rule split", dn.split_rule(r)[:2]==["-A","DOCKER-USER"] and "!" in dn.split_rule(r))

# ---- 5. watchdog: overheat + idle-stop simulation (fake clock)
import deckwatch as w
clock=[1_000_000.0]
w.time.time=lambda: clock[0]; d.time.time=lambda: clock[0]
w.time.sleep=lambda s: None
calls=[]
cpu=[60.0]; players=[1]; online=[True]
d.k12_up=lambda cfg: True
d.k12_temps=lambda cfg: {"cpu":cpu[0]}
d.pi_temp=lambda: 50.0
d.mc_status=lambda h,p=25565,t=3: {"online":online[0],"players":players[0],"max":10,"names":["Steve"] if players[0] else [],"version":"x","motd":""}
def fake_agent(cfg,*a,timeout_s=60):
    calls.append(a[0])
    if a[0]=="info": return {"ok":True,"tailscale_ip":"100.101.102.103","docker":True}
    if a[0] in("off","stop"): online[0]=False; return {"ok":True}
    return {"ok":True}
d.agent=fake_agent
wd=w.Watch()
def run(sec):
    for _ in range(int(sec/30)): clock[0]+=30; wd.tick()
    # the idle stop runs in its own thread (the watchdog keeps reading temps meanwhile):
    # let it finish before checking what it did
    for th in threading.enumerate():
        if th is not threading.current_thread() and th.daemon: th.join(timeout=10)
run(60); check("normal: level ok", wd.level=="ok")
st=d.read_json("state.json"); check(f"join address from Tailscale IP ({st['join']})", st["join"]=="100.101.102.103")
cpu[0]=87; run(90); check("87C for 90s: still ok (needs 120s)", wd.level=="ok")
run(60); check("87C for 150s: warn", wd.level=="warn")
cpu[0]=94; run(90); check("94C for 60s+: hot, server stopped", wd.level=="hot" and "say" in calls and "stop" in calls)
run(150); check("still 94C 2+ min after stop: crit, K12 off", wd.level=="crit" and "off" in calls)
cpu[0]=60; run(60); check("cools down: back to ok", wd.level=="ok")
# idle stop: server running, nobody on, deck woke it -> off
online[0]=True; players[0]=0; calls.clear(); d.write_json("woke",{"by":"t","t":0}); wd.mc_was_online=True
run(19*60); check("empty 19 min: still running", "off" not in calls and "stop" not in calls)
run(90); check("empty 20+ min, deck woke it: stop + shut down", "off" in calls)
online[0]=True; players[0]=0; calls.clear(); d.remove("woke")
run(21*60); check("empty 20+ min, you turned it on: only stop", "stop" in calls and "off" not in calls)
texts=[e["text"] for e in d.read_events(0,limit=100)]
calls.clear(); open(os.path.join(os.environ["DECK_STATE"],"travel"),"w").write("on"); wd.agent_info_at=0; run(30)
check("travel on at Pi -> watchdog switches K12", "travel" in calls)
# too hot while Minecraft isn't running: no "saving the world" claim, nothing to stop
online[0]=False; cpu[0]=95; wd.reset_temp_state(quiet=True); wd.mc_was_online=False; calls.clear(); run(90)
hot=[e["text"] for e in d.read_events(0,limit=100) if "too hot" in e["text"]]
check(f"hot with Minecraft off: honest message, no say/stop ({hot[-1] if hot else None})",
      wd.level=="hot" and hot and "isn't running" in hot[-1] and "say" not in calls and "stop" not in calls)
cpu[0]=60; run(60)
print("\nlast events:"); [print("  ",x) for x in texts[-8:]]
print(f"\n{fails} failures"); sys.exit(1 if fails else 0)
