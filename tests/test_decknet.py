import importlib.util, importlib.machinery, io, json, sys, types, contextlib, os, tempfile
spec=importlib.util.spec_from_loader("dn", importlib.machinery.SourceFileLoader("dn", os.path.join(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi5"), "deck-net")))
dn=importlib.util.module_from_spec(spec); spec.loader.exec_module(dn)
fails=0
def check(n,c):
    global fails; print(("PASS " if c else "FAIL ")+n); fails+= not c
R=lambda out="",err="",code=0: types.SimpleNamespace(stdout=out,stderr=err,returncode=code)

def call(fn,*a,stdin=""):
    """run a deck-net function that ends in out()/sys.exit; return parsed JSON"""
    buf=io.StringIO(); sys.stdin=io.StringIO(stdin)
    with contextlib.redirect_stdout(buf):
        try: fn(*a)
        except SystemExit: pass
    return json.loads(buf.getvalue().strip().splitlines()[-1])

# ---------- fake nmcli
log=[]
saved=["Home WiFi"]
SCAN="*:Home WiFi:82:WPA2\n :Hotel Guest:74:\n :Cafe\\:Guest:60:WPA1 WPA2\n :Hotel Guest:40:\n :::\n :NETGEAR-5G:38:WPA2\n"
radio=["enabled"]
def fake_run(cmd, check=False, timeout=60, **kw):
    log.append(cmd)
    if cmd==["nmcli","radio","wifi"]: return R(radio[0]+"\n")
    if cmd==["nmcli","radio","wifi","on"]: radio[0]="enabled"; return R()
    if "DEVICE,STATE" in cmd: return R("eth0:connected\nwlan0:"+("disconnected" if radio[0]=="enabled" else "unavailable")+"\n")
    if cmd[:2]==["nmcli","-t"] and "connection" in cmd and "show" in cmd:
        return R("".join(f"{s}:802-11-wireless\n" for s in saved)+"Wired connection 1:802-3-ethernet\ncase-lan:802-3-ethernet\n")
    if "list" in cmd and "IN-USE,SSID,SIGNAL,SECURITY" in cmd: return R(SCAN)
    if "ACTIVE,SSID,SIGNAL" in cmd: return R("yes:Home WiFi:82\nno:Hotel Guest:74\n")
    if cmd[:2]==["ip","-4"]: return R("3: wlan0    inet 192.168.1.40/24 brd 192.168.1.255 scope global dynamic wlan0\n")
    if "wifi" in cmd and "connect" in cmd:
        if "password" in cmd and cmd[cmd.index("password")+1]=="wrongpass1": return R("", "Error: Connection activation failed: Secrets were required, but not provided.",4)
        return R("Device 'wlan0' successfully activated")
    if cmd[:3]==["nmcli","-w","40"] and "up" in cmd: return R("ok")
    if "delete" in cmd: return R("deleted")
    return R()
dn.run=fake_run
dn.internet_check=lambda: {"internet":"online"}
dn.time.sleep=lambda s: None

# fresh Pi: NetworkManager's Wi-Fi radio is off -> status says so, scan switches it on
radio[0]="disabled"
r=call(dn.status); check("status reports the Wi-Fi radio is off", r["wifi_radio"] is False)
log.clear(); r=call(dn.scan)
check("scan switches the radio on first, then finds networks",
      ["nmcli","radio","wifi","on"] in log and radio[0]=="enabled" and len(r["networks"])==4)
log.clear(); r=call(dn.scan); check("radio already on -> not switched again", ["nmcli","radio","wifi","on"] not in log)
radio[0]="disabled"; log.clear(); r=call(dn.connect, stdin=json.dumps({"ssid":"Home WiFi","password":""}))
check("connect switches the radio on too", ["nmcli","radio","wifi","on"] in log and r["ok"])

r=call(dn.scan)
names=[n["ssid"] for n in r["networks"]]
check(f"scan: dedup, escaped colon, hidden skipped, in-use first {names}", names==["Home WiFi","Hotel Guest","Cafe:Guest","NETGEAR-5G"])
hg=[n for n in r["networks"] if n["ssid"]=="Hotel Guest"][0]
check("scan: open network labelled, strongest signal kept", hg["security"]=="open" and hg["signal"]==74)
check("scan: saved flag", r["networks"][0]["saved"] and not hg["saved"])
r=call(dn.status); check("status parses ssid/signal/ip", r["wifi"]=={"ssid":"Home WiFi","signal":82,"ip":"192.168.1.40"})
log.clear(); r=call(dn.connect, stdin=json.dumps({"ssid":"Home WiFi","password":""}))
check("connect saved network w/o password -> 'connection up'", r["ok"] and any("up" in c for c in log))
log.clear(); r=call(dn.connect, stdin=json.dumps({"ssid":"NETGEAR-5G","password":"wrongpass1"}))
check(f"wrong password -> friendly error ({r.get('error','')[:40]}...)", not r["ok"] and "Wrong password" in r["error"])
r=call(dn.connect, stdin=json.dumps({"ssid":"NETGEAR-5G","password":"short"}))
check("too-short password rejected before nmcli", not r["ok"] and "8-63" in r["error"])
r=call(dn.connect, stdin=json.dumps({"ssid":"x"*40,"password":""}))
check("overlong SSID rejected", not r["ok"])
r=call(dn.connect, stdin="not json")
check("garbage input rejected", not r["ok"])
r=call(dn.forget, stdin=json.dumps({"ssid":"Wired connection 1"}))
check("forget refuses non-Wi-Fi connections", not r["ok"])

# ---------- iptables travel rules against a fake rule table
rules={("filter","DOCKER-USER"):["-N DOCKER-USER","-A DOCKER-USER -j RETURN"],("filter","FORWARD"):["-P FORWARD DROP"],("nat","POSTROUTING"):["-P POSTROUTING ACCEPT","-A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE"]}
def fake_ipt(*args, table=None, check=False):
    t=table or "filter"; a=list(args)
    if a[0]=="-S":
        k=(t,a[1]); return R("\n".join(rules[k])+"\n") if k in rules else R("",  "No chain",1)
    rule=lambda rest: " ".join(x if " " not in x else f'"{x}"' for x in rest)
    if a[0]=="-I":
        k=(t,a[1]); rules[k].insert(1 if rules[k][0].startswith(("-N","-P")) else 0, "-A "+a[1]+" "+rule(a[3:])); return R()
    if a[0]=="-A":
        rules[(t,a[1])].append("-A "+a[1]+" "+rule(a[2:])); return R()
    if a[0]=="-D":
        line="-A "+a[1]+" "+rule(a[2:])
        if line in rules[(t,a[1])]: rules[(t,a[1])].remove(line); return R()
        return R("","Bad rule",1)
    return R()
dn.ipt=fake_ipt
tmp=tempfile.mkdtemp(); dn.STATE=os.path.join(tmp,"travel")
import builtins
real_open=builtins.open
def fake_open(p,*a,**k):
    if p=="/proc/sys/net/ipv4/ip_forward": return io.StringIO()
    return real_open(p,*a,**k)
builtins.open=fake_open
r=call(dn.travel,"on"); tagged=[l for k in rules for l in rules[k] if "deck-travel" in l]
check("travel on: 3 tagged rules, accepts in DOCKER-USER (Docker drops FORWARD)", r["ok"] and len(tagged)==3 and sum("DOCKER-USER" in l for l in tagged)==2)
check("travel on twice: still 3 rules (no duplicates)", call(dn.travel,"on")["ok"] and len([l for k in rules for l in rules[k] if "deck-travel" in l])==3)
r=call(dn.travel,"off"); left=[l for k in rules for l in rules[k] if "deck-travel" in l]
check("travel off: all tagged rules removed, Docker's own rules kept", r["ok"] and not left and "-A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE" in rules[("nat","POSTROUTING")] and "-A DOCKER-USER -j RETURN" in rules[("filter","DOCKER-USER")])
check("state file says off", dn.travel_is_on() is False)
r=call(dn.travel,"sideways"); check("bad travel arg rejected", not r["ok"])
builtins.open=real_open

# ---------- deck settings the Deck app may change (config-get / config-set)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi5"))
import types as _t
if "fcntl" not in sys.modules:
    fc=_t.ModuleType("fcntl"); fc.LOCK_EX=2; fc.LOCK_NB=4; fc.LOCK_UN=8; fc.flock=lambda f,o: None; sys.modules["fcntl"]=fc
import decklib
check("every editable setting's default matches decklib.DEFAULT_CONFIG",
      all(f["default"] == decklib.DEFAULT_CONFIG[f["key"]] for f in dn.CONFIG_FIELDS))
cfg={"k12_mac":"aa:bb:cc:dd:ee:ff","discord_owner_ids":["123"],"idle_stop_minutes":20}
v=dn.config_view(cfg)
check("view: values for every field, missing ones as their default", v["values"]["idle_stop_minutes"]==20 and v["values"]["backup_keep"]==10 and len(v["fields"])==len(dn.CONFIG_FIELDS))
new=dn.config_apply(cfg, {"idle_stop_minutes":"30","backup_keep":5,"mc_join_address":"play.example.com:25565"})
check("apply: numbers (also as text) and an address, other settings kept",
      new["idle_stop_minutes"]==30 and new["backup_keep"]==5 and new["mc_join_address"]=="play.example.com:25565"
      and new["k12_mac"]=="aa:bb:cc:dd:ee:ff" and new["discord_owner_ids"]==["123"] and cfg["idle_stop_minutes"]==20)
check("apply: empty join address allowed (= Tailscale address)", dn.config_apply(cfg, {"mc_join_address":""})["mc_join_address"]=="")
BAD=[({"idle_stop_minutes":-1},"below the minimum"), ({"idle_stop_minutes":99999},"above the maximum"),
     ({"idle_stop_minutes":"ten"},"not a number"), ({"idle_stop_minutes":True},"true/false"), ({"idle_stop_minutes":None},"empty"),
     ({"k12_mac":"11:22:33:44:55:66"},"not editable from the app"), ({"discord_owner_ids":["666"]},"Discord owners"),
     ({"k12_cpu_hot":80},"hot below warn"), ({"pi_warn":84,"pi_hot":83},"Pi hot below warn"),
     ({"mc_join_address":"evil; rm -rf /"},"odd address"), ({"mc_join_address":"a b"},"space in address"),
     ({},"nothing"), ("x","not an object")]
for ch, why in BAD:
    try: dn.config_apply(cfg, ch); ok=False
    except ValueError: ok=True
    check(f"apply refused: {why}", ok)
tmp=tempfile.mkdtemp(); dn.CONFIG=os.path.join(tmp,"config.json")
json.dump(cfg, open(dn.CONFIG,"w"))
r=call(dn.config_set, stdin=json.dumps({"changes":{"pack_cool_c":50}}))
check("config-set writes the file, keeps the rest", r["ok"] and json.load(open(dn.CONFIG))=={**cfg,"pack_cool_c":50})
r=call(dn.config_set, stdin=json.dumps({"changes":{"k12_user":"evil"}}))
check("config-set refusal leaves the file alone", not r["ok"] and json.load(open(dn.CONFIG))=={**cfg,"pack_cool_c":50})
r=call(dn.config_set, stdin="not json"); check("config-set: bad request refused", not r["ok"])
r=call(dn.config_get); check("config-get reads the file", r["ok"] and r["values"]["pack_cool_c"]==50)
print(f"\n{fails} failures"); sys.exit(1 if fails else 0)
