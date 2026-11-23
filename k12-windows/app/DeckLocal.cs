// ---------------------------------------------------------------------------
// Standalone mode: the deck without its Pi.
//
// When the Pi can't be reached (it's not in the case, or not plugged in), the Deck
// app answers its own questions here, on this PC, with the same answers the Pi would
// give - so every page keeps working the same way:
//
//   Minecraft   start / stop / restart / shut down, status, players  (deck-agent.ps1 + a status ping)
//   Console     the server log and commands                          (docker logs / rcon-cli)
//   Players     who's on, kick / ban / op, who may join              (Mojang check + deck-agent)
//   Server      performance and server settings                      (docker stats / deck-agent)
//   Modpacks    switch, delete, import a CurseForge export zip       (deck-agent)
//   Backups     back up now, download (kept on this PC: C:\deck\backups)
//   Temperatures from LibreHardwareMonitor on this PC; Minecraft is saved + stopped if the CPU
//   stays too hot, and an empty server stops after 20 minutes - like the Pi's watchdog.
//
// Only what needs the Pi waits for it: deck settings, Wi-Fi + travel mode, pack up, the
// phone panel, the Discord bot, backups copied to the Pi, installing from a CurseForge link.
// The app keeps looking for the Pi and switches back by itself when it's there.
//
// The frequent checks are cheap on purpose (a socket ping, one local web request, the docker
// command); deck-agent.ps1 (PowerShell, ~1 s to start) runs only when you press something.
// ---------------------------------------------------------------------------
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace Cyberdeck
{
    public sealed class LocalDeck
    {
        public const string AgentPath = @"C:\deck\agent\deck-agent.ps1";
        public const string GameDir = @"C:\deck\game-servers";
        public const string BackupDir = @"C:\deck\backups";
        public static bool Available { get { return File.Exists(AgentPath); } }

        const string NoPi = "That needs the Pi, and it isn't connected. Everything else works from this PC.";
        // same rules as decklib.py / deck-agent.ps1
        static readonly Regex CmdRe = new Regex(@"^[A-Za-z0-9 _.,:~@!?#/+=()*\[\]-]{1,200}$");
        static readonly Regex NameRe = new Regex(@"^[A-Za-z0-9_]{3,16}$");
        static readonly Regex SlugRe = new Regex(@"^[a-z0-9][a-z0-9-]{0,79}$");
        static readonly Regex BackupRe = new Regex(@"^(minecraft|pack-[a-z0-9][a-z0-9-]{0,79})-\d{8}-\d{4,6}\.zip$");
        static readonly Regex Ansi = new Regex(@"\x1b\[[0-9;?]*[A-Za-z]|\u00a7.");
        static readonly Regex CrashRe = new Regex(@"Mod Loading has failed|Mod loading error has occurred|Crash report saved to|Failed to start the minecraft server|Could not execute entrypoint stage|Incompatible mods found|Missing or unsupported mandatory dependencies|Exception in server tick loop", RegexOptions.IgnoreCase);
        static readonly Regex ManualRe = new Regex(@"The authors of the mod '(.+?)' have disallowed project distribution\. Manually download the file '(.+?)' from (\S+)");

        readonly object gate = new object();
        readonly List<Dictionary<string, object>> events = new List<Dictionary<string, object>>();
        long lastId;
        Dictionary<string, object> action, job;
        // cheap caches
        object temps; DateTime tempsAt = DateTime.MinValue;
        string tsIp = ""; DateTime tsAt = DateTime.MinValue;
        object perf; DateTime perfAt = DateTime.MinValue;
        // watchdog
        DateTime? warnSince, hotSince, idleSince; string level = "ok";
        HashSet<string> lastNames; bool? wasOnline;
        DateTime watchedAt = DateTime.MinValue;
        Dictionary<string, object> lastMc;

        // ------------------------------------------------------------ the API, answered here
        public J Handle(string method, string fullPath, object body)
        {
            string path = fullPath.Split('?')[0];
            var b = new J(body);
            try
            {
                if (method == "GET")
                {
                    switch (path)
                    {
                        case "/api/status": return Status();
                        case "/api/console": return Console_();
                        case "/api/whitelist":
                            {
                                var r = Agent("whitelist list", 30000);
                                return Obj("ok", r["ok"].Bool, "names", r["names"].V, "live", r["live"].Bool, "pending", new object[0], "error", r["error"].V);
                            }
                        case "/api/server/settings": return Agent("settings get", 30000);
                        case "/api/perf": return Perf();
                        case "/api/modpack": return Modpacks();
                        case "/api/backups": return Backups();
                        case "/api/check": return Obj("result", null, "running", false);
                        case "/api/pack": return Obj("pack", null, "checklist", new object[0]);
                        default: return Fail(NoPi);
                    }
                }
                switch (path)
                {
                    case "/api/mc/start": return Run("start Minecraft", delegate { Start("Deck app"); });
                    case "/api/mc/stop": return Run("stop Minecraft", delegate { Stop("Deck app", false); });
                    case "/api/mc/off": return Run("stop Minecraft + shut down the PC", delegate { Stop("Deck app", true); });
                    case "/api/mc/restart": return Run("restart Minecraft", delegate { if (Stop("Deck app", false, false)) Start("Deck app"); });
                    case "/api/backup": return Run("back up the world", delegate { Backup(null); });
                    case "/api/console": return Command(b["cmd"].Str);
                    case "/api/whitelist": return Whitelist(b["action"].Str, b["name"].Str.Trim());
                    case "/api/server/settings":
                        {
                            string v = b["value"].Str.Trim();
                            if (v.Length == 0 || v.Length > 100) return Fail("Type a value first.");
                            var r = Agent("settings set " + b["kind"].Str + " " + b["key"].Str + " " + B64(v), 30000);
                            if (r["ok"].Bool) Event("Server setting " + b["key"].Str + " = " + v + ". Takes effect when Minecraft restarts.", "info", "settings");
                            return r;
                        }
                    case "/api/modpack/import-check":
                        {
                            var m = ImportMeta(b["manifest"]);
                            return m;
                        }
                    case "/api/modpack/import":
                        {
                            var m = ImportMeta(b["manifest"]);
                            if (!m["ok"].Bool) return m;
                            if (!m["can_install"].Bool) return Fail(m["warning"].Str);
                            var meta = (Dictionary<string, object>)m.V;
                            return Run("import " + m["name"].Str, delegate { Switch(meta["slug"] as string, meta); });
                        }
                    case "/api/modpack/switch":
                        {
                            string slug = b["slug"].Str;
                            if (slug.Length > 0 && !SlugRe.IsMatch(slug)) return Fail("bad pack name");
                            return Run("switch to " + (slug.Length > 0 ? slug : "vanilla"), delegate { Switch(slug, null); });
                        }
                    case "/api/modpack/delete": return Delete(b["slug"].Str);
                    case "/api/modpack/clear": lock (gate) job = null; return Obj("ok", true);
                    case "/api/modpack/preview":
                    case "/api/modpack/install":
                        return Fail("Installing from a CurseForge link needs the Pi (it looks the pack up). Without it: in the CurseForge app install the pack, " +
                                    "then its profile > ⋯ > Export profile, and use Import a CurseForge export below.");
                    default: return Fail(NoPi);
                }
            }
            catch (Exception e) { return Fail(e.Message); }
        }

        // ------------------------------------------------------------ status + watchdog
        J Status()
        {
            var mc = McPing("127.0.0.1", 25565);
            var t = Temps();
            Watch(mc, t);
            if ((DateTime.Now - tsAt).TotalMinutes > 5)
            {
                tsAt = DateTime.Now;
                string ip = Exe(@"C:\Program Files\Tailscale\tailscale.exe", "ip -4", 5000).Trim().Split('\n')[0].Trim();
                tsIp = Regex.IsMatch(ip, @"^\d+\.\d+\.\d+\.\d+$") ? ip : "";
            }
            double? idleLeft = idleSince.HasValue && (bool)mc["online"] ? (double?)Math.Max(0, 20 * 60 - (DateTime.Now - idleSince.Value).TotalSeconds) : null;
            List<object> evs;
            Dictionary<string, object> act;
            lock (gate) { evs = events.AsEnumerable().Reverse().Take(15).Cast<object>().ToList(); act = action; }
            var state = Obj(
                "time", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"),
                "k12", Obj("up", true, "temps", t, "tailscale_ip", tsIp, "docker", File.Exists(@"\\.\pipe\docker_engine"), "woke_by_deck", false).V,
                "pi", Obj("temp", null).V, "mc", mc, "join", tsIp, "idle_left_s", idleLeft,
                "temp_level", level, "pi_level", "ok").V;
            return Obj("state", state, "stale", false, "action", act, "events", evs, "mac_configured", true, "linked", true,
                       "modpack", ActiveName(), "standalone", true);
        }

        // The Pi's watchdog, light: joins/leaves, empty-server stop, heat stop (default limits)
        void Watch(Dictionary<string, object> mc, object t)
        {
            if ((DateTime.Now - watchedAt).TotalSeconds < 3) return;      // two pages asking at once count once
            watchedAt = DateTime.Now;
            bool online = (bool)mc["online"];
            if (wasOnline.HasValue && wasOnline.Value != online)
                Event(online ? "Minecraft is up" + (tsIp.Length > 0 ? " - join at " + tsIp : "") + "." : "Minecraft is down.", online ? "good" : "warn", online ? "mc_up" : "mc_down");
            wasOnline = online;
            var names = new HashSet<string>(((IEnumerable<object>)mc["names"]).Select(x => (string)x));
            if (online && lastNames != null)
            {
                foreach (var n in names.Except(lastNames)) Event(n + " joined (" + mc["players"] + "/" + mc["max"] + ").", "info", "join");
                foreach (var n in lastNames.Except(names)) Event(n + " left (" + mc["players"] + "/" + mc["max"] + ").", "info", "leave");
            }
            lastNames = online ? names : null;
            lastMc = mc;
            bool busy; lock (gate) busy = action != null;
            // empty server: stop it after 20 minutes (the PC stays on - you're using it)
            if (online && (int)mc["players"] == 0 && !busy)
            {
                if (!idleSince.HasValue) idleSince = DateTime.Now;
                else if ((DateTime.Now - idleSince.Value).TotalMinutes >= 20)
                {
                    idleSince = null;
                    Event("Nobody has played for 20 min - stopping the server.", "info", "idle");
                    Run("stop Minecraft", delegate { Stop("idle timer", false); });
                }
            }
            else idleSince = null;
            // heat: 85 °C for 2 min = warning, 92 °C for 1 min = save + stop Minecraft
            var td = t as Dictionary<string, object>;
            double cpu = td != null && td.ContainsKey("cpu") ? Convert.ToDouble(td["cpu"], CultureInfo.InvariantCulture) : double.NaN;
            if (double.IsNaN(cpu)) return;
            warnSince = cpu >= 85 ? (warnSince ?? DateTime.Now) : (DateTime?)null;
            hotSince = cpu >= 92 ? (hotSince ?? DateTime.Now) : (DateTime?)null;
            if (level != "ok" && cpu < 80) { level = "ok"; Event("PC temperature back to normal.", "good", "temp"); }
            if (hotSince.HasValue && (DateTime.Now - hotSince.Value).TotalSeconds >= 60 && level != "hot")
            {
                level = "hot";
                Event("PC is too hot (" + Math.Round(cpu) + " C)" + (online ? " - saving the world and stopping Minecraft." : "."), "bad", "temp");
                if (online && !busy) Run("stop Minecraft (too hot)", delegate
                {
                    Agent("say The server is overheating - saving and stopping in 20 seconds.", 20000);
                    Thread.Sleep(20000);
                    Stop("temperature watchdog", false, false);   // too hot: no zipping on top (the world is saved)
                });
            }
            else if (warnSince.HasValue && (DateTime.Now - warnSince.Value).TotalSeconds >= 120 && level == "ok")
            {
                level = "warn";
                Event("PC is running warm (" + Math.Round(cpu) + " C). Give the case some air.", "warn", "temp");
            }
        }

        // ------------------------------------------------------------ Minecraft
        bool Start(string who)
        {
            Event("Starting Minecraft (asked by " + who + ").", "info", "mc");
            var r = Agent("start", 420000);
            if (!r["ok"].Bool) { Event("Couldn't start Minecraft: " + r["error"].Str, "bad", "mc"); return false; }
            Event("Minecraft server process started; loading the world (1-3 min)...", "info", "mc");
            // watch it come up: a server that can't start would otherwise restart over and over, silently
            string since = r["since"].Str;
            var until = DateTime.Now.AddMinutes(6);
            int restarts0 = Restarts();
            while (DateTime.Now < until)
            {
                Thread.Sleep(8000);
                if ((bool)McPing("127.0.0.1", 25565)["online"]) return true;
                string log = Ansi.Replace(Docker("logs --tail 60" + (since.Length > 0 ? " --since " + since : "") + " minecraft", 15000), "");
                string why = StartProblem(log);
                if (why == null && restarts0 >= 0 && Restarts() >= restarts0 + 2)
                    why = "it keeps restarting without coming up - the Console page shows the error.";
                if (why != null)
                {
                    Agent("stop", 180000);
                    Event("Minecraft couldn't start, so it was stopped: " + why, "bad", "mc");
                    return false;
                }
            }
            return true;
        }

        // how often Docker has restarted the server since it was created (a crash loop counts up)
        static int Restarts()
        {
            int n; return int.TryParse(Docker("inspect -f {{.RestartCount}} minecraft", 10000).Trim(), out n) ? n : -1;
        }

        // why a starting server gave up (from its log), in words for people; null = nothing found
        public static string StartProblem(string log)
        {
            var m = Regex.Match(log ?? "", @"Could not resolve user from \w+: ([A-Za-z0-9_]+)");
            if (m.Success)
                return "there's no Minecraft Java account called '" + m.Groups[1].Value + "' (on the whitelist or the operators list). " +
                       "Fix the name in C:\\deck\\game-servers\\.env (MC_WHITELIST_PLAYERS and MC_OPS), or remove it on the Players page, then start again.";
            var dep = Regex.Match(log ?? "", @"Mod ID: '([\w.-]+)', Requested by: '([\w.-]+)'.*?Actual version: '\[MISSING\]'");
            if (dep.Success)
                return dep.Groups[2].Value + " needs the mod '" + dep.Groups[1].Value + "', which isn't in the pack. Add it to the pack in the CurseForge app, " +
                       "export again and import the new zip.";
            if (Regex.IsMatch(log ?? "", @"EULA", RegexOptions.IgnoreCase) && Regex.IsMatch(log ?? "", @"(must|need to) (accept|agree)", RegexOptions.IgnoreCase))
                return "the Minecraft EULA isn't accepted (MC_EULA=TRUE in C:\\deck\\game-servers\\.env).";
            if (CrashRe.IsMatch(log ?? "")) return "the server crashed while starting - the Console page shows why.";
            return null;
        }

        // Stopping backs the world up, like the Pi does (a restart doesn't: the world is back in a minute).
        // For "shut down the PC" the order is stop -> back up -> shut down, so the backup is never cut off.
        bool Stop(string who, bool powerOff, bool backup = true)
        {
            Event((powerOff ? "Stop Minecraft + shut down the PC" : "Stop Minecraft") + " (asked by " + who + ").", "info", "mc");
            var r = Agent("stop", 180000);
            if (!r["ok"].Bool) { Event("Stop failed: " + r["error"].Str, "bad", "mc"); return false; }
            Event(r["msg"].Str.Length > 0 ? r["msg"].Str : "Minecraft stopped, world saved.", "good", "mc");
            if (backup) Backup(null);
            if (powerOff)
            {
                r = Agent("off", 60000);
                if (!r["ok"].Bool) { Event("Shutting down failed: " + r["error"].Str, "bad", "mc"); return false; }
                Event(r["msg"].Str, "good", "mc");
            }
            return true;
        }

        J Command(string text)
        {
            text = Regex.Replace(text ?? "", @"\s+", " ").Trim().TrimStart('/');
            if (text.Length == 0) return Fail("Type a command, e.g. time set day");
            if (!CmdRe.IsMatch(text)) return Fail("That command has characters the deck does not send (quotes, braces, ;, $, | ...).");
            string first = text.Split(' ')[0].ToLowerInvariant();
            if (first == "stop") return Fail("Use the Stop button - it also backs the world up.");
            if (first == "whitelist") return Fail("Use Players allowed - it keeps the start-up list in step.");
            if (!(bool)McPing("127.0.0.1", 25565)["online"]) return Fail("Minecraft isn't running.");
            // rcon-cli directly (the same as the agent does, without starting PowerShell)
            var words = text.Split(' ');
            string o = Ansi.Replace(Docker("exec minecraft rcon-cli " + string.Join(" ", words), 30000), "").Trim();
            Event("Console: /" + text + " (by Deck app)", "info", "console");
            return Obj("ok", true, "msg", o.Length > 0 ? o : "done");
        }

        J Console_()
        {
            bool online = (bool)McPing("127.0.0.1", 25565)["online"];
            string o = Docker("logs --tail 80 minecraft", 15000);
            var lines = o.Split('\n').Select(l => Ansi.Replace(l, "").TrimEnd('\r')).Where(l => l.Trim().Length > 0 && !l.StartsWith("Error"))
                         .Select(l => l.Length > 300 ? l.Substring(0, 300) : l).Cast<object>().ToList();
            return Obj("ok", true, "running", online, "lines", lines, "k12", true);
        }

        J Whitelist(string act, string name)
        {
            if (act != "add" && act != "remove") return Fail("action must be add or remove");
            if (!NameRe.IsMatch(name)) return Fail("Minecraft names are 3-16 letters, numbers or _");
            string uuid = "";
            if (act == "add")
            {
                try
                {
                    var p = Mojang(name);
                    if (p == null) return Fail("No Minecraft account is called '" + name + "'. Check the spelling.");
                    name = p[0]; uuid = p[1];
                }
                catch (Exception) { }          // Mojang unreachable: the server checks the name itself
            }
            var r = Agent("whitelist " + act + " " + name + (uuid.Length > 0 ? " " + uuid : ""), 40000);
            if (!r["ok"].Bool) return r;
            string verb = act == "add" ? "can now join" : "removed from the whitelist";
            Event(name + " " + verb + " (by Deck app).", "good", "whitelist");
            return Obj("ok", true, "msg", name + " " + verb + ".", "name", name);
        }

        // [exact name, dashed uuid], null = no such account; throws when Mojang can't be reached
        static string[] Mojang(string name)
        {
            ServicePointManager.SecurityProtocol |= (SecurityProtocolType)3072;     // TLS 1.2
            var req = (HttpWebRequest)WebRequest.Create("https://api.mojang.com/users/profiles/minecraft/" + name);
            req.Timeout = 6000; req.Proxy = null;
            try
            {
                using (var resp = (HttpWebResponse)req.GetResponse())
                {
                    if (resp.StatusCode == HttpStatusCode.NoContent) return null;
                    var j = new J(Api.Json.DeserializeObject(new StreamReader(resp.GetResponseStream()).ReadToEnd()));
                    string u = j["id"].Str;
                    if (u.Length != 32) return null;
                    return new[] { j["name"].Str, u.Substring(0, 8) + "-" + u.Substring(8, 4) + "-" + u.Substring(12, 4) + "-" + u.Substring(16, 4) + "-" + u.Substring(20) };
                }
            }
            catch (WebException e)
            {
                var r = e.Response as HttpWebResponse;
                if (r != null && (r.StatusCode == HttpStatusCode.NotFound || r.StatusCode == HttpStatusCode.NoContent)) return null;
                throw;
            }
        }

        // ticks per second + the server container's CPU and memory, from docker (no PowerShell)
        J Perf()
        {
            lock (gate) { if (perf != null && (DateTime.Now - perfAt).TotalSeconds < 8) return new J(perf); }
            Dictionary<string, object> r;
            if (!(bool)McPing("127.0.0.1", 25565)["online"]) r = (Dictionary<string, object>)Obj("ok", true, "running", false, "k12", true).V;
            else
            {
                r = (Dictionary<string, object>)Obj("ok", true, "running", true, "k12", true).V;
                var p = Docker("stats --no-stream --format \"{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}\" minecraft", 20000).Trim().Split('|');
                if (p.Length == 3)
                {
                    r["cpu"] = Num(p[0]); var mem = p[1].Split('/');
                    r["mem_used"] = mem[0].Trim(); r["mem_limit"] = mem.Length > 1 ? mem[1].Trim() : ""; r["mem_pct"] = Num(p[2]);
                }
                foreach (var c in new[] { "tick query", "neoforge tps", "forge tps", "tps" })
                {
                    string o = Ansi.Replace(Docker("exec minecraft rcon-cli " + c, 15000), "");
                    Match m;
                    double? mspt = null, tps = null;
                    if ((m = Regex.Match(o, @"Average time per tick:\s*([\d.]+)\s*ms")).Success) mspt = Num(m.Groups[1].Value);
                    else if ((m = Regex.Match(o, @"Overall.*?Mean tick time:\s*([\d.]+)\s*ms.*?Mean TPS:\s*([\d.]+)", RegexOptions.Singleline)).Success) { mspt = Num(m.Groups[1].Value); tps = Num(m.Groups[2].Value); }
                    else if ((m = Regex.Match(o, @"TPS from last 1m, 5m, 15m:\s*\*?([\d.]+)")).Success) tps = Num(m.Groups[1].Value);
                    else continue;
                    if (!tps.HasValue) tps = Math.Round(Math.Min(20, 1000 / Math.Max(mspt.Value, 50)), 1);
                    r["tps"] = tps.Value; if (mspt.HasValue) r["mspt"] = mspt.Value;
                    break;
                }
                if (!r.ContainsKey("tps")) r["note"] = "This Minecraft version doesn't report tick speed (vanilla needs 1.20.3 or newer).";
            }
            lock (gate) { perf = r; perfAt = DateTime.Now; }
            return new J(r);
        }

        // ------------------------------------------------------------ backups (on this PC)
        J Backups()
        {
            var list = new List<object>();
            if (Directory.Exists(BackupDir))
                foreach (var f in new DirectoryInfo(BackupDir).GetFiles("*.zip").Where(x => BackupRe.IsMatch(x.Name)).OrderByDescending(x => x.LastWriteTimeUtc))
                    list.Add(Obj("file", f.Name, "bytes", (double)f.Length, "t", (f.LastWriteTimeUtc - new DateTime(1970, 1, 1)).TotalSeconds).V);
            return Obj("backups", list, "standalone", true);
        }

        bool Backup(string slug)
        {
            Event("Backing up " + (slug != null ? "the " + slug + " world" : "the world") + " (kept on this PC until the Pi is back)...", "info", "backup");
            var r = Agent(slug != null ? "backup " + slug : "backup", 900000);
            if (r["ok"].Bool) { Event("Backup saved on this PC: " + r["file"].Str + " (" + Ui.Size(r["bytes"].Num) + ").", "good", "backup"); return true; }
            if (r["code"].Str == "noworld") { Event("No world yet - nothing to back up.", "info", "backup"); return true; }
            Event("Backup failed: " + r["error"].Str, "bad", "backup");
            return false;
        }

        public static string BackupPath(string name) { return BackupRe.IsMatch(name) ? Path.Combine(BackupDir, name) : null; }

        // ------------------------------------------------------------ modpacks
        J Modpacks()
        {
            var r = Agent("modpack list", 30000);
            var packs = new List<object>();
            foreach (var p in r["packs"].List)
            {
                string slug = p["slug"].Str;
                packs.Add(Obj("slug", slug, "name", PackName(slug), "mc", "", "loader", "", "has_world", p["has_world"].Bool,
                              "removed", new object[0], "file_name", "").V);
            }
            object j; lock (gate) j = job;
            string active = r["active"].Str;
            return Obj("ok", r["ok"].Bool, "error", r["error"].V, "live", true, "active", active,
                       "active_name", active.Length > 0 ? PackName(active) : "Vanilla", "packs", packs, "pc_key", r["key"].Bool, "pi_key", true, "job", j);
        }

        // an imported pack's name is in its zip; one from a CurseForge link is known by its short name
        static string PackName(string slug)
        {
            string zip = Path.Combine(GameDir, "imports", slug + ".zip");
            try
            {
                if (File.Exists(zip))
                    using (var z = System.IO.Compression.ZipFile.OpenRead(zip))
                    {
                        var e = z.GetEntry("manifest.json");
                        if (e != null && e.Length < 4 * 1024 * 1024)
                            using (var s = new StreamReader(e.Open())) { var n = new J(Api.Json.DeserializeObject(s.ReadToEnd()))["name"].Str; if (n.Length > 0) return n; }
                    }
            }
            catch { }
            return slug;
        }

        static string ActiveName()
        {
            try
            {
                string a = File.ReadAllText(Path.Combine(GameDir, "active-pack.txt")).Trim();
                return SlugRe.IsMatch(a) && File.Exists(Path.Combine(GameDir, "packs", a + ".env")) ? PackName(a) : "";
            }
            catch { return ""; }
        }

        static readonly string[][] JobSteps = {
            new[] { "players", "Warn players" }, new[] { "stop", "Save + stop Minecraft" }, new[] { "backup", "Back up the world that's on now" },
            new[] { "apply", "Switch worlds" }, new[] { "start", "Start the server" }, new[] { "watch", "Download + load (first time: several minutes)" } };

        // switch to an installed pack (meta null), to vanilla (slug ""), or to an imported export zip (meta from ImportMeta)
        void Switch(string slug, Dictionary<string, object> meta)
        {
            string name = slug.Length == 0 ? "Vanilla" : meta != null ? (string)meta["name"] : PackName(slug);
            var steps = JobSteps.Select(s => (object)new Dictionary<string, object> { { "key", s[0] }, { "label", s[1] }, { "status", "pending" }, { "detail", "" } }).ToList();
            var st = new Dictionary<string, object> { { "running", true }, { "done", false }, { "ok", null }, { "name", name }, { "target", slug },
                                                      { "missing", new List<object>() }, { "problem", "" }, { "steps", steps } };
            lock (gate) job = st;
            Action<string, string, string> mark = delegate (string key, string status, string detail)
            {
                lock (gate) foreach (Dictionary<string, object> s in steps) if ((string)s["key"] == key) { s["status"] = status; s["detail"] = detail ?? ""; }
            };
            Event("Switching the server to " + name + ".", "info", "modpack");
            bool ok = false;
            try { ok = SwitchSteps(slug, meta, name, mark, st); }
            catch (Exception e) { Event("Switching stopped by an error: " + e.Message, "bad", "modpack"); }
            lock (gate)
            {
                foreach (Dictionary<string, object> s in steps) if ((string)s["status"] == "pending" || (string)s["status"] == "running") s["status"] = ok ? "skip" : "fail";
                st["running"] = false; st["done"] = true; st["ok"] = ok;
            }
        }

        bool SwitchSteps(string slug, Dictionary<string, object> meta, string name, Action<string, string, string> mark, Dictionary<string, object> st)
        {
            var mc = McPing("127.0.0.1", 25565);
            int playing = (int)mc["players"];
            if ((bool)mc["online"] && playing > 0)
            {
                mark("players", "running", playing + " playing - restarting in 60 s");
                Agent("say The server is switching to " + Regex.Replace(name, @"[^A-Za-z0-9 .,!?:()/+-]", "") + " - saving and restarting in 60 seconds.", 20000);
                Thread.Sleep(50000);
                Agent("say Restarting in 10 seconds.", 20000);
                Thread.Sleep(10000);
                mark("players", "ok", "warned " + playing + " player(s)");
            }
            else mark("players", "skip", "nobody playing");
            mark("stop", "running", "");
            var r = Agent("stop", 180000);
            if (!r["ok"].Bool) { mark("stop", "fail", r["error"].Str); return false; }
            mark("stop", "ok", "world saved");
            mark("backup", "running", "zipping the world (kept on this PC)...");
            var b = Agent("backup", 900000);
            if (b["ok"].Bool) mark("backup", "ok", b["file"].Str + " (" + Ui.Size(b["bytes"].Num) + ", on this PC)");
            else if (b["code"].Str == "noworld") mark("backup", "skip", "no world yet");
            else { mark("backup", "fail", b["error"].Str + " - nothing changed"); return false; }
            mark("apply", "running", "");
            if (slug.Length == 0) r = Agent("modpack vanilla", 60000);
            else
            {
                string fid, java; int heap = 8;
                if (meta != null) { fid = "zip"; java = (string)meta["java"]; }
                else
                {
                    // an installed pack: use what it was set up with (packs\<pack>.env)
                    var list = Agent("modpack list", 30000);
                    var p = list["packs"].List.FirstOrDefault(x => x["slug"].Str == slug);
                    if (p == null) { mark("apply", "fail", "no installed pack called " + slug); return false; }
                    fid = p["source"].Str == "zip" ? "zip" : p["file_id"].Str.Length > 0 ? p["file_id"].Str : "latest";
                    java = p["java"].Str.Length > 0 ? p["java"].Str : "latest";
                    int.TryParse(p["memory"].Str.TrimEnd('G', 'g'), out heap);
                    if (heap < 2 || heap > 24) heap = 8;
                }
                r = Agent("modpack apply " + slug + " " + fid + " " + java + " " + heap + " " + (heap + 2), 60000);
            }
            if (!r["ok"].Bool) { mark("apply", "fail", r["error"].Str); return false; }
            mark("apply", "ok", slug.Length == 0 ? "vanilla world" : name);
            mark("start", "running", "");
            r = Agent("start", 900000);
            if (!r["ok"].Bool) { mark("start", "fail", r["error"].Str); Event("Couldn't start " + name + ": " + r["error"].Str, "bad", "modpack"); return false; }
            mark("start", "ok", "");
            string since = r["since"].Str;
            mark("watch", "running", "starting...");
            var deadline = DateTime.Now.AddMinutes(slug.Length > 0 ? 30 : 10);
            var started = DateTime.Now;
            int restarts0 = Restarts();
            while (true)
            {
                Thread.Sleep(10000);
                if ((bool)McPing("127.0.0.1", 25565)["online"]) break;
                var lines = Docker("logs --tail 200" + (since.Length > 0 ? " --since " + since : "") + " minecraft", 20000).Split('\n').Select(l => Ansi.Replace(l, "").TrimEnd('\r')).ToList();
                var missing = lines.Select(l => ManualRe.Match(l)).Where(m => m.Success)
                                   .Select(m => (object)new Dictionary<string, object> { { "mod", m.Groups[1].Value }, { "file", m.Groups[2].Value }, { "url", m.Groups[3].Value } }).ToList();
                if (missing.Count > 0)
                {
                    Agent("stop", 180000);
                    lock (gate) st["missing"] = missing;
                    mark("watch", "fail", missing.Count + " mod(s) must be downloaded by hand into C:\\deck\\game-servers\\downloads\\" + slug + ", then switch again");
                    Event(name + " needs " + missing.Count + " mod(s) downloaded by hand - see Modpacks.", "warn", "modpack");
                    return false;
                }
                string startProblem = StartProblem(string.Join("\n", lines));
                if (startProblem == null && restarts0 >= 0 && Restarts() >= restarts0 + 2)
                    startProblem = "it keeps restarting without coming up - the Console page shows the error.";
                // a specific reason (missing mod, unknown player, crash loop) beats "it crashed"
                if (startProblem != null && !startProblem.StartsWith("the server crashed"))
                {
                    Agent("stop", 180000);
                    mark("watch", "fail", startProblem);
                    Event(name + " couldn't start: " + startProblem, "bad", "modpack");
                    return false;
                }
                if (lines.Any(l => CrashRe.IsMatch(l)) || (Docker("ps --filter name=^minecraft$ --format {{.Status}}", 10000).Trim().Length == 0 && (DateTime.Now - started).TotalMinutes > 2))
                {
                    Agent("stop", 180000);
                    mark("watch", "fail", "the server stopped - the Console page shows why (the Pi's auto-fix for client-only mods needs the Pi)");
                    Event(name + " didn't start - see the Console page.", "bad", "modpack");
                    return false;
                }
                if (DateTime.Now > deadline) { mark("watch", "fail", "still not up - it may just be slow; watch the Console"); return false; }
                string last = lines.LastOrDefault(l => l.Trim().Length > 0) ?? "";
                mark("watch", "running", last.Length > 120 ? last.Substring(0, 120) : last);
            }
            if (slug.Length > 0) Agent("modpack synced " + slug, 30000);
            mark("watch", "ok", "running");
            Event(name + " is running!" + (meta != null ? " Players need the same pack: send them the export zip (CurseForge app > Create Custom Profile > Import)." : ""), "good", "modpack");
            return true;
        }

        J Delete(string slug)
        {
            if (!SlugRe.IsMatch(slug ?? "")) return Fail("bad pack name");
            return Run("delete " + slug, delegate
            {
                if (!Backup(slug)) { Event("Couldn't back " + slug + " up, so nothing was deleted.", "warn", "modpack"); return; }
                var r = Agent("modpack delete " + slug, 120000);
                Event(r["ok"].Bool ? "Deleted " + slug + ". Its backups are kept." : "Couldn't delete " + slug + ": " + r["error"].Str, r["ok"].Bool ? "info" : "warn", "modpack");
            });
        }

        // manifest.json of a CurseForge export -> the same details the Pi's import_meta gives
        static J ImportMeta(J man)
        {
            const string how = "Export it from the CurseForge app: open the pack's profile, the ⋯ menu > Export profile, and pick that zip.";
            if (man["manifestType"].Str != "minecraftModpack") return Fail("That zip isn't a CurseForge modpack export. " + how);
            string mcv = man["minecraft"]["version"].Str;
            if (!Regex.IsMatch(mcv, @"^\d+\.\d+(\.\d+)?$")) return Fail("The export doesn't say which Minecraft version it's for. " + how);
            var loaders = man["minecraft"]["modLoaders"].List.Where(x => x["id"].Str.Length > 0).ToList();
            var main = loaders.FirstOrDefault(x => x["primary"].Bool) ?? loaders.FirstOrDefault();
            string lid = main == null ? "" : main["id"].Str;
            string pre = lid.Split('-')[0].ToLowerInvariant();
            string loader = pre == "forge" ? "Forge" : pre == "neoforge" ? "NeoForge" : pre == "fabric" ? "Fabric" : pre == "quilt" ? "Quilt" : "";
            var files = man["files"].List.Where(f => Regex.IsMatch(f["fileID"].Str, @"^\d+$") && Regex.IsMatch(f["projectID"].Str, @"^\d+$")).Count();
            if (files > 3000) return Fail("That pack lists more than 3000 mods.");
            string name = Regex.Replace(man["name"].Str, @"[\x00-\x1f\x7f]", "").Trim(); if (name.Length > 60) name = name.Substring(0, 60); if (name.Length == 0) name = "Imported pack";
            string version = Regex.Replace(man["version"].Str, @"[\x00-\x1f\x7f]", "").Trim(); if (version.Length > 40) version = version.Substring(0, 40);
            string slug = Regex.Replace(name.ToLowerInvariant(), "[^a-z0-9]+", "-").Trim('-'); if (slug.Length > 60) slug = slug.Substring(0, 60).Trim('-');
            if (!SlugRe.IsMatch(slug)) slug = "imported-pack";
            bool can = loader == "Forge" || loader == "NeoForge" || loader == "Fabric";
            string warn = loader == "Quilt" ? "This is a Quilt pack. The server can't install Quilt packs - pick a Forge, NeoForge or Fabric pack."
                        : loader.Length == 0 ? "The export doesn't say which mod loader it uses, so the server can't install it."
                        : files == 0 ? "This export has no mods in it - it would just be plain Minecraft " + mcv + "." : "";
            return Obj("ok", true, "source", "zip", "slug", slug, "name", name, "version", version, "file_id", "zip",
                       "file_name", (name + " " + version).Trim(), "mc", mcv, "loader", loader, "loader_version", lid,
                       "java", JavaTag(mcv), "mods", files, "can_install", can, "warning", warn);
        }

        // Minecraft version -> server image (Java) tag; same table as decklib.JAVA_TAGS
        static string JavaTag(string v)
        {
            var p = v.Split('.').Select(x => { int n; return int.TryParse(x, out n) ? n : -1; }).ToList();
            while (p.Count < 3) p.Add(0);
            if (p.Any(x => x < 0)) return "latest";
            long k = p[0] * 1000000L + p[1] * 1000L + p[2];
            if (k <= 1016005) return "java8";
            if (k <= 1020004) return "java17";
            if (k <= 1021999) return "java21";
            return "latest";
        }

        // ------------------------------------------------------------ actions, one at a time
        J Run(string what, Action work)
        {
            lock (gate)
            {
                if (action != null) return Fail("Already busy: " + action["what"] + " - see Events.");
                action = new Dictionary<string, object> { { "what", what }, { "by", "Deck app" } };
            }
            new Thread(delegate ()
            {
                try { work(); }
                catch (Exception e) { Event("Error: " + e.Message, "bad", "mc"); }
                finally { lock (gate) action = null; }
            }) { IsBackground = true }.Start();
            return Obj("ok", true);
        }

        void Event(string text, string level, string kind)
        {
            lock (gate)
            {
                lastId = Math.Max(DateTime.UtcNow.Ticks, lastId + 1);
                events.Add(new Dictionary<string, object> { { "id", lastId }, { "t", DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") }, { "level", level }, { "kind", kind }, { "text", text } });
                if (events.Count > 200) events.RemoveRange(0, events.Count - 200);
            }
        }

        // ------------------------------------------------------------ cheap probes
        // Minecraft "server list ping": is it up, who's on. A few hundred bytes over a local socket.
        public static Dictionary<string, object> McPing(string host, int port)
        {
            var off = new Dictionary<string, object> { { "online", false }, { "players", 0 }, { "max", 0 }, { "names", new List<object>() }, { "version", "" }, { "motd", "" } };
            try
            {
                using (var c = new TcpClient())
                {
                    var ar = c.BeginConnect(host, port, null, null);
                    if (!ar.AsyncWaitHandle.WaitOne(1500) || !c.Connected) return off;
                    c.EndConnect(ar);
                    var s = c.GetStream(); s.ReadTimeout = 3000; s.WriteTimeout = 3000;
                    var hs = new List<byte>();
                    hs.AddRange(VarInt(0)); hs.AddRange(VarInt(767));
                    var hb = Encoding.UTF8.GetBytes(host); hs.AddRange(VarInt(hb.Length)); hs.AddRange(hb);
                    hs.Add((byte)(port >> 8)); hs.Add((byte)port); hs.AddRange(VarInt(1));
                    var pkt = new List<byte>(VarInt(hs.Count)); pkt.AddRange(hs); pkt.AddRange(VarInt(1)); pkt.AddRange(VarInt(0));
                    s.Write(pkt.ToArray(), 0, pkt.Count);
                    int len = ReadVarInt(s);
                    if (len <= 0 || len > 1 << 21) return off;
                    var buf = new byte[len]; int got = 0;
                    while (got < len) { int n = s.Read(buf, got, len - got); if (n <= 0) return off; got += n; }
                    int pos = 0;
                    if (VarIntAt(buf, ref pos) != 0) return off;
                    int sl = VarIntAt(buf, ref pos);
                    var info = new J(Api.Json.DeserializeObject(Encoding.UTF8.GetString(buf, pos, sl)));
                    var names = info["players"]["sample"].List.Select(x => x["name"].Str)
                        .Where(n => n.Length > 0 && NameRe.IsMatch(n)).OrderBy(n => n, StringComparer.OrdinalIgnoreCase).Cast<object>().ToList();
                    off["online"] = true; off["players"] = (int)Num0(info["players"]["online"]); off["max"] = (int)Num0(info["players"]["max"]);
                    off["names"] = names; off["version"] = info["version"]["name"].Str;
                    return off;
                }
            }
            catch { return off; }
        }
        static byte[] VarInt(int v)
        {
            var o = new List<byte>(); uint u = (uint)v;
            do { byte b = (byte)(u & 0x7F); u >>= 7; if (u != 0) b |= 0x80; o.Add(b); } while (u != 0);
            return o.ToArray();
        }
        static int ReadVarInt(Stream s)
        {
            int r = 0;
            for (int i = 0; i < 5; i++) { int b = s.ReadByte(); if (b < 0) return -1; r |= (b & 0x7F) << (7 * i); if ((b & 0x80) == 0) return r; }
            return -1;
        }
        static int VarIntAt(byte[] buf, ref int pos)
        {
            int r = 0;
            for (int i = 0; i < 5 && pos < buf.Length; i++) { int b = buf[pos++]; r |= (b & 0x7F) << (7 * i); if ((b & 0x80) == 0) return r; }
            return -1;
        }

        // PC temperatures from LibreHardwareMonitor's own web page on this PC (read every 8 s at most)
        object Temps()
        {
            lock (gate) { if ((DateTime.Now - tempsAt).TotalSeconds < 8) return temps; }
            object result = null;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8085/data.json");
                req.Proxy = null; req.Timeout = 3000;
                using (var resp = req.GetResponse())
                using (var rd = new StreamReader(resp.GetResponseStream()))
                {
                    var found = new Dictionary<string, List<KeyValuePair<string, double>>> { { "cpu", new List<KeyValuePair<string, double>>() }, { "gpu", new List<KeyValuePair<string, double>>() }, { "drive", new List<KeyValuePair<string, double>>() } };
                    WalkLhm(new J(Api.Json.DeserializeObject(rd.ReadToEnd())), null, found);
                    var t = new Dictionary<string, object>();
                    string[] preferred = { "core (tctl/tdie)", "cpu package", "package", "tctl/tdie", "core (tctl)", "core max" };
                    if (found["cpu"].Count > 0)
                    {
                        var pick = found["cpu"].Where(x => preferred.Contains(x.Key)).Select(x => (double?)x.Value).FirstOrDefault();
                        t["cpu"] = pick ?? found["cpu"].Max(x => x.Value);
                    }
                    if (found["gpu"].Count > 0) t["gpu"] = found["gpu"].Max(x => x.Value);
                    if (found["drive"].Count > 0) t["drive"] = found["drive"].Max(x => x.Value);
                    result = t.Count > 0 ? t : null;
                }
            }
            catch { result = null; }
            lock (gate) { temps = result; tempsAt = DateTime.Now; }
            return result;
        }
        // same picking rules as decklib.parse_lhm
        static void WalkLhm(J node, string hw, Dictionary<string, List<KeyValuePair<string, double>>> found)
        {
            string img = node["ImageURL"].Str.ToLowerInvariant(), sid = node["SensorId"].Str.ToLowerInvariant();
            string kind = img.Contains("cpu") || sid.StartsWith("/amdcpu") || sid.StartsWith("/intelcpu") ? "cpu"
                : new[] { "nvidia", "ati", "amd.png", "gpu", "intel.png" }.Any(k => img.Contains(k)) || sid.Contains("gpu") ? "gpu"
                : img.Contains("hdd") || sid.Contains("nvme") || sid.Contains("/hdd") || img.Contains("ssd") ? "drive" : null;
            hw = kind ?? hw;
            string v = node["Value"].Str;
            string label = node["Text"].Str.ToLowerInvariant();
            // drives and RAM also list their limits as "temperatures" (Warning 82 °C, Critical 84 °C) - those aren't readings
            bool isLimit = Regex.IsMatch(label, @"limit|warning|critical|threshold|resolution|\bmax\b|\bmin\b");
            if (hw != null && !isLimit && v.Contains("C") && (node["Type"].IsNull || node["Type"].Str == "Temperature"))
            {
                var m = Regex.Match(v, @"(-?\d+(?:[.,]\d+)?)");
                if (m.Success) found[hw].Add(new KeyValuePair<string, double>(node["Text"].Str.ToLowerInvariant(), Num(m.Groups[1].Value.Replace(',', '.'))));
            }
            foreach (var c in node["Children"].List) WalkLhm(c, hw, found);
        }

        // ------------------------------------------------------------ running things
        // deck-agent.ps1, the same script the Pi uses over SSH (it reads the command from SSH_ORIGINAL_COMMAND)
        static J Agent(string cmd, int timeoutMs)
        {
            string o = Run(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe"),
                           "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + AgentPath + "\"", timeoutMs, cmd);
            var line = o.Split('\n').Select(l => l.Trim()).LastOrDefault(l => l.StartsWith("{"));
            if (line == null) return Fail("The deck agent didn't answer" + (o.Trim().Length > 0 ? ": " + Last(o.Trim(), 200) : "."));
            try { return new J(Api.Json.DeserializeObject(line)); } catch { return Fail("odd answer from the deck agent"); }
        }

        static string dockerExe;
        static string Docker(string args, int timeoutMs)
        {
            if (dockerExe == null)
            {
                const string dd = @"C:\Program Files\Docker\Docker\resources\bin\docker.exe";
                dockerExe = File.Exists(dd) ? dd : "docker.exe";
            }
            return Run(dockerExe, args, timeoutMs, null);
        }

        static string Exe(string file, string args, int timeoutMs) { return File.Exists(file) ? Run(file, args, timeoutMs, null) : ""; }

        // stdout + stderr of a hidden process (stderr too: docker logs prints the server's log there)
        static string Run(string file, string args, int timeoutMs, string sshCommand)
        {
            try
            {
                var psi = new ProcessStartInfo(file, args)
                {
                    UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8
                };
                if (sshCommand != null) psi.EnvironmentVariables["SSH_ORIGINAL_COMMAND"] = sshCommand;
                using (var p = Process.Start(psi))
                {
                    var err = new StringBuilder();
                    p.ErrorDataReceived += delegate (object s, DataReceivedEventArgs e) { if (e.Data != null) lock (err) err.AppendLine(e.Data); };
                    p.BeginErrorReadLine();
                    var outTask = p.StandardOutput.ReadToEndAsync();
                    if (!p.WaitForExit(timeoutMs)) { try { p.Kill(); } catch { } return ""; }
                    p.WaitForExit();
                    string e2; lock (err) e2 = err.ToString();
                    return outTask.Result + e2;
                }
            }
            catch { return ""; }
        }

        // ------------------------------------------------------------ small helpers
        static J Obj(params object[] kv)
        {
            var d = new Dictionary<string, object>();
            for (int i = 0; i + 1 < kv.Length; i += 2) d[(string)kv[i]] = kv[i + 1] is J ? ((J)kv[i + 1]).V : kv[i + 1];
            return new J(d);
        }
        static J Fail(string msg) { return Obj("ok", false, "error", msg); }
        static string B64(string s) { return Convert.ToBase64String(Encoding.UTF8.GetBytes(s)).TrimEnd('=').Replace('+', '-').Replace('/', '_'); }
        static double Num(string s) { double d; return double.TryParse(Regex.Replace(s ?? "", @"[^\d.\-]", ""), NumberStyles.Float, CultureInfo.InvariantCulture, out d) ? d : 0; }
        static double Num0(J v) { return v.IsNum ? v.Num : 0; }
        static string Last(string s, int n) { return s.Length > n ? s.Substring(s.Length - n) : s; }
    }
}
