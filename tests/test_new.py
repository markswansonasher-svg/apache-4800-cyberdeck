import sys, types, os, tempfile, json, hashlib, io, zipfile
fc=types.ModuleType("fcntl"); fc.LOCK_EX=2; fc.LOCK_NB=4; fc.LOCK_UN=8; fc.flock=lambda f,o: None; sys.modules["fcntl"]=fc
os.environ["DECK_STATE"]=tempfile.mkdtemp(); os.environ["DECK_CONFIG"]=os.path.join(os.environ["DECK_STATE"],"c.json")
json.dump({"k12_user":"x","k12_mac":"aa:bb:cc:dd:ee:ff","backup_keep":3,"backup_min_free_mb":1},open(os.environ["DECK_CONFIG"],"w"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi5"))
import decklib as d
fails=0
def check(n,c):
    global fails; print(("PASS " if c else "FAIL ")+n); fails+= not c
cfg=d.load_config()
k12=[False]; calls=[]; zips={}; corrupt=[False]
def agent(cfg,*a,timeout_s=60):
    calls.append(a)
    if not k12[0]: return {"ok":False,"error":"off"}
    if a[0]=="ping": return {"ok":True}
    if a[0]=="whitelist": return {"ok":True,"names":["Steve"]} if a[1]=="list" else {"ok":True}
    if a[0]=="backup":
        b=io.BytesIO(); z=zipfile.ZipFile(b,"w"); z.writestr("level.dat","x"); z.close(); data=b.getvalue()
        name=f"minecraft-20261118-12{len(zips):04d}.zip"; zips[name]=data
        return {"ok":True,"file":name,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()}
    return {"ok":True}
def dl(cfg,remote,dest,timeout_s=0):
    open(dest,"wb").write(zips[remote][:-3] if corrupt[0] else zips[remote])
d.agent=agent; d.agent_download=dl; d.k12_up=lambda c: k12[0]
d.mojang_lookup=lambda n,timeout_s=6: None if n=="Nobody_x" else {"name":n.capitalize(),"uuid":"069a79f4-44e9-4726-a5be-fca90e38aaf5"}
# whitelist
check("bad name rejected", not d.whitelist_change(cfg,"add","a b")["ok"])
check("unknown account rejected", "No Minecraft account" in d.whitelist_change(cfg,"add","Nobody_x")["error"])
r=d.whitelist_change(cfg,"add","lumi"); check("K12 off -> queued with exact name", r.get("queued") and r["name"]=="Lumi")
d.whitelist_change(cfg,"add","lumi"); check("queue has no duplicates", len(d.read_json("whitelist_pending.json"))==1)
k12[0]=True; calls.clear(); d.whitelist_apply_pending(cfg)
check("queue applied when K12 up (with uuid)", ("whitelist","add","Lumi","069a79f4-44e9-4726-a5be-fca90e38aaf5") in calls and d.read_json("whitelist_pending.json")==[])
check("cache updated", "Lumi" in d.whitelist_cached()["names"])
r=d.whitelist_change(cfg,"remove","Lumi"); check("remove live", r["ok"] and "Lumi" not in d.whitelist_cached()["names"])
# backups
for i in range(5): d.backup_now(cfg,"t")
check("rotation keeps backup_keep=3", len(d.list_backups())==3)
corrupt[0]=True; r=d.backup_now(cfg,"t")
check("truncated copy rejected (checksum) and not kept", not r["ok"] and "checksum" in r["error"] and len(d.list_backups())==3 and not any(f.endswith(".part") for f in os.listdir(d.BACKUP_DIR)))
corrupt[0]=False
k12[0]=False; check("backup with PC off -> clear error", d.backup_now(cfg,"t")["error"]=="PC is off")
check("human_size", d.human_size(1568)=="2 KB" and d.human_size(300*2**20)=="300 MB" and d.human_size(3*2**30)=="3.0 GB")
# temperatures: drives list their limits as "temperatures" too - those must not be read as the drive's temperature
def sensor(text,v): return {"Text":text,"Value":f"{v} °C","Type":"Temperature","Children":[]}
lhm={"Children":[{"Text":"CT500P310SSD8","ImageURL":"images_icon/hdd.png","SensorId":"/nvme/0","Children":[{"Text":"Temperatures","Children":[
        sensor("Composite Temperature",34.0),sensor("Temperature #1",33.9),sensor("Warning Temperature",82.0),sensor("Critical Temperature",84.0)]}]},
    {"Text":"Ryzen","ImageURL":"images_icon/cpu.png","SensorId":"/amdcpu/0","Children":[sensor("Core (Tctl/Tdie)",55.9)]}]}
t=d.parse_lhm(lhm)
check(f"drive temp ignores Warning/Critical limits ({t.get('drive')})", t.get("drive")==34.0 and t.get("cpu")==55.9)
print(f"\n{fails} failures"); sys.exit(1 if fails else 0)
