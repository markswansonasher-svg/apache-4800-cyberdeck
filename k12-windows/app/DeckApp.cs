// ---------------------------------------------------------------------------
// Deck - the cyberdeck's control app on the PC's own screen.
//
// A small native Windows app (about 20-40 MB of memory, no browser) that talks to
// the Deck Panel's API on the Pi (http://10.48.0.2:8080). Same data as the phone
// page, so both always agree.
//
//   Overview   Minecraft start/stop/restart, players, join address, temperatures, events
//   Console    the server's live log and a command box
//   Players    who's online (kick, ban, op, game mode), who may join, bans
//   Server     tick speed, CPU + memory, and the server settings (.env + server.properties)
//   Modpacks   install from a CurseForge link, switch, delete, progress of a switch
//   Backups    world backups on the Pi: back up now, download
//   Pi & deck  Wi-Fi + travel mode, deck check, pack up, deck settings, restart / shut down the Pi
//
// Cheap on purpose: it only asks the Pi for what the page on screen shows, and stops
// asking completely while minimised. Built by build-app.ps1 with the C# compiler that
// comes with Windows (.NET Framework 4), so nothing extra is installed.
//
//   Deck.exe [--pi 10.48.0.2] [--port 8080] [--at-sign-in] [--page console] [export.zip]
// ---------------------------------------------------------------------------
using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace Cyberdeck
{
    // ------------------------------------------------------------------ JSON helper
    // Wraps what JavaScriptSerializer gives back (dictionaries, object[] and numbers) so
    // the pages can read it without casts everywhere. Missing values read as empty.
    public sealed class J
    {
        public readonly object V;
        public J(object v) { V = v; }
        public static readonly J Null = new J(null);

        public J this[string key]
        {
            get
            {
                var d = V as IDictionary<string, object>;
                object o;
                return d != null && d.TryGetValue(key, out o) ? new J(o) : Null;
            }
        }
        public bool IsNull { get { return V == null; } }
        public bool Has(string key) { var d = V as IDictionary<string, object>; return d != null && d.ContainsKey(key) && d[key] != null; }
        public string Str { get { return V == null ? "" : Convert.ToString(V, CultureInfo.InvariantCulture); } }
        public bool Bool { get { return V is bool ? (bool)V : (V != null && !(V is bool) && Str.Length > 0 && Str != "0"); } }
        public double Num { get { double d; return V != null && !(V is bool) && double.TryParse(Str, NumberStyles.Float, CultureInfo.InvariantCulture, out d) ? d : double.NaN; } }
        public bool IsNum { get { return !double.IsNaN(Num); } }
        public List<J> List
        {
            get
            {
                var l = new List<J>();
                var a = V as IEnumerable;
                if (a != null && !(V is string) && !(V is IDictionary<string, object>)) foreach (var o in a) l.Add(new J(o));
                return l;
            }
        }
        public IEnumerable<string> Keys
        {
            get { var d = V as IDictionary<string, object>; return d == null ? new string[0] : d.Keys.ToArray(); }
        }
    }

    // ------------------------------------------------------------------ the Pi's API
    public sealed class Api
    {
        public string Base;
        public static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 32 * 1024 * 1024 };
        readonly Control ui;

        public Api(string host, int port, Control uiThread)
        {
            Base = "http://" + host + ":" + port;
            ui = uiThread;
            ServicePointManager.DefaultConnectionLimit = 6;
            ServicePointManager.Expect100Continue = false;
        }

        // Runs on a pool thread; done() runs on the UI thread with the answer (or {ok:false,error}).
        public void Call(string method, string path, object body, Action<J> done, int timeoutMs = 15000)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                J r = Send(method, path, body, timeoutMs);
                if (done == null) return;
                try { if (!ui.IsDisposed) ui.BeginInvoke(done, r); } catch (InvalidOperationException) { }
            });
        }
        // A read that's still on its way isn't asked again (a slow Pi never gets a pile-up of requests)
        readonly HashSet<string> reading = new HashSet<string>();
        public void Get(string path, Action<J> done)
        {
            lock (reading) { if (!reading.Add(path)) return; }
            Call("GET", path, null, delegate (J r) { lock (reading) reading.Remove(path); done(r); });
        }
        public void Post(string path, object body, Action<J> done) { Call("POST", path, body ?? new Dictionary<string, object>(), done); }

        // Standalone: no Pi, so this PC answers (DeckLocal.cs). Null when this isn't the deck's PC.
        public LocalDeck Local;
        public volatile bool Standalone;

        // Is the Pi there? (asked directly, even in standalone mode) done(true/false) on the UI thread
        public void ProbePi(Action<bool> done)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                // a quick knock first (1.5 s): a Pi that isn't there costs no long web timeout
                bool ok = PortOpen(1500) && Fetch("GET", "/api/status", null, 4000).Has("state");
                try { if (!ui.IsDisposed) ui.BeginInvoke(done, ok); } catch (InvalidOperationException) { }
            });
        }

        bool PortOpen(int ms)
        {
            try
            {
                var u = new Uri(Base);
                using (var c = new System.Net.Sockets.TcpClient())
                {
                    var ar = c.BeginConnect(u.Host, u.Port, null, null);
                    bool ok = ar.AsyncWaitHandle.WaitOne(ms) && c.Connected;
                    if (ok) c.EndConnect(ar);
                    return ok;
                }
            }
            catch { return false; }
        }

        J Send(string method, string path, object body, int timeoutMs)
        {
            if (Standalone && Local != null) return Local.Handle(method, path, body);
            return Fetch(method, path, body, timeoutMs);
        }

        J Fetch(string method, string path, object body, int timeoutMs)
        {
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(Base + path);
                req.Method = method;
                req.Proxy = null;                 // no proxy look-up: the Pi is on the case cable
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.KeepAlive = true;
                req.Headers["X-Deck"] = "1";      // the panel only accepts button presses with this
                if (body != null)
                {
                    byte[] b = Encoding.UTF8.GetBytes(Json.Serialize(body));
                    req.ContentType = "application/json";
                    req.ContentLength = b.Length;
                    using (var s = req.GetRequestStream()) s.Write(b, 0, b.Length);
                }
                using (var resp = (HttpWebResponse)req.GetResponse())
                    return Parse(resp);
            }
            catch (WebException e)
            {
                var resp = e.Response as HttpWebResponse;
                if (resp != null) { using (resp) { var j = Parse(resp); if (j.Has("error")) return j; return Fail("HTTP " + (int)resp.StatusCode); } }
                return Fail("offline: " + e.Message);
            }
            catch (Exception e) { return Fail(e.Message); }
        }

        J Parse(HttpWebResponse resp)
        {
            using (var r = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
            {
                string text = r.ReadToEnd();
                try { return new J(Json.DeserializeObject(text)); }
                catch { return Fail("odd answer from the Pi"); }
            }
        }

        public static J Fail(string msg)
        {
            return new J(new Dictionary<string, object> { { "ok", false }, { "error", msg } });
        }

        // Downloads a file (a backup zip) to path; done(error or null) on the UI thread.
        public void Download(string path, string dest, Action<string> done)
        {
            ThreadPool.QueueUserWorkItem(delegate
            {
                string err = null;
                try
                {
                    if (Standalone && Local != null)
                    {
                        // standalone: the backups are on this PC already
                        string src = LocalDeck.BackupPath(Path.GetFileName(path));
                        if (src == null || !File.Exists(src)) throw new FileNotFoundException("no such backup");
                        File.Copy(src, dest, true);
                        goto copied;
                    }
                    var req = (HttpWebRequest)WebRequest.Create(Base + path);
                    req.Proxy = null; req.Timeout = 30000; req.ReadWriteTimeout = 120000;
                    using (var resp = req.GetResponse())
                    using (var s = resp.GetResponseStream())
                    using (var f = File.Create(dest)) s.CopyTo(f, 1 << 20);
                }
                catch (Exception e) { err = e.Message; try { File.Delete(dest); } catch { } }
            copied:
                try { if (!ui.IsDisposed) ui.BeginInvoke(done, err); } catch (InvalidOperationException) { }
            });
        }
    }

    // A label that ignores "set it to what it already says": the pages re-read the Pi every
    // few seconds, and an unchanged label then costs nothing (no re-layout, no repaint).
    // Shows & as & (a plain Label would hide it as a keyboard-shortcut marker).
    public class QuietLabel : Label
    {
        public QuietLabel() { UseMnemonic = false; }
        public override string Text { get { return base.Text; } set { if (base.Text != value) base.Text = value; } }
        public override Color ForeColor { get { return base.ForeColor; } set { if (base.ForeColor != value) base.ForeColor = value; } }
    }

    // ------------------------------------------------------------------ look + building blocks
    public static class Ui
    {
        public static readonly Color Bg = Color.FromArgb(17, 19, 22);
        public static readonly Color Card = Color.FromArgb(27, 30, 35);
        public static readonly Color Field = Color.FromArgb(36, 40, 47);
        public static readonly Color Line = Color.FromArgb(50, 55, 64);
        public static readonly Color Text = Color.FromArgb(230, 232, 235);
        public static readonly Color Dim = Color.FromArgb(150, 156, 166);
        public static readonly Color Accent = Color.FromArgb(76, 201, 240);
        public static readonly Color Good = Color.FromArgb(61, 220, 132);
        public static readonly Color Warn = Color.FromArgb(242, 201, 76);
        public static readonly Color Bad = Color.FromArgb(255, 92, 92);
        public static float Scale = 1f;
        public static Font Body, Small, Big, Head, Mono;

        public static void Init(float dpi)
        {
            Scale = dpi / 96f;
            Body = new Font("Segoe UI", 10f);
            Small = new Font("Segoe UI", 8.5f);
            Big = new Font("Segoe UI Semibold", 16f);
            Head = new Font("Segoe UI Semibold", 9f);
            Mono = new Font("Consolas", 9.5f);
        }
        public static int S(int px) { return (int)Math.Round(px * Scale); }

        public static Color LevelColor(string level)
        {
            switch (level) { case "good": return Good; case "warn": return Warn; case "bad": return Bad; default: return Text; }
        }

        // A card: dark box with a small grey heading, content flows top to bottom
        public static FlowLayoutPanel Card_(string title, out FlowLayoutPanel body)
        {
            var card = new FlowLayoutPanel
            {
                FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoSize = true,
                AutoSizeMode = AutoSizeMode.GrowAndShrink, BackColor = Card,
                Padding = new Padding(S(14), S(10), S(14), S(12)), Margin = new Padding(0, 0, S(12), S(12))
            };
            card.Controls.Add(new QuietLabel { Text = title.ToUpperInvariant(), Font = Head, ForeColor = Dim, AutoSize = true, Margin = new Padding(0, 0, 0, S(6)) });
            body = card;
            return card;
        }

        public static Label L(string text, Font f = null, Color? c = null)
        {
            return new QuietLabel { Text = text, AutoSize = true, Font = f ?? Body, ForeColor = c ?? Text, Margin = new Padding(0, S(2), S(6), S(2)) };
        }

        public static Button B(string text, EventHandler click, Color? back = null, int width = 0)
        {
            var b = new Button
            {
                Text = text, FlatStyle = FlatStyle.Flat, BackColor = back ?? Field, ForeColor = back.HasValue ? Color.FromArgb(17, 19, 22) : Text,
                Font = Body, AutoSize = width == 0, Height = S(32), Padding = new Padding(S(8), 0, S(8), 0),
                Margin = new Padding(0, S(3), S(8), S(3)), Cursor = Cursors.Hand, UseVisualStyleBackColor = false, UseMnemonic = false
            };
            if (width > 0) b.Width = S(width);
            // flat buttons keep their colour when disabled: dim them so "can't press" is visible
            Color back0 = b.BackColor, fore0 = b.ForeColor;
            b.EnabledChanged += delegate { b.BackColor = b.Enabled ? back0 : Card; b.ForeColor = b.Enabled ? fore0 : Line; };
            b.FlatAppearance.BorderColor = Line;
            b.FlatAppearance.MouseOverBackColor = back.HasValue ? ControlPaint.Light(back.Value) : Line;
            if (click != null) b.Click += click;
            return b;
        }

        public static TextBox T(int width, string cue = null)
        {
            var t = new TextBox { Width = S(width), BackColor = Field, ForeColor = Text, BorderStyle = BorderStyle.FixedSingle, Font = Body, Margin = new Padding(0, S(4), S(8), S(4)) };
            if (cue != null) SetCue(t, cue);
            return t;
        }

        public static FlowLayoutPanel Row(params Control[] cs)
        {
            var p = new FlowLayoutPanel { FlowDirection = FlowDirection.LeftToRight, WrapContents = true, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Margin = new Padding(0), BackColor = Color.Transparent };
            p.Controls.AddRange(cs);
            return p;
        }

        public static ListBox List(int width, int height)
        {
            return new ListBox { Width = S(width), Height = S(height), BackColor = Field, ForeColor = Text, BorderStyle = BorderStyle.None, Font = Body, IntegralHeight = false, Margin = new Padding(0, S(4), 0, S(4)) };
        }

        public static ComboBox Combo(int width, params string[] items)
        {
            var c = new ComboBox { Width = S(width), DropDownStyle = ComboBoxStyle.DropDownList, BackColor = Field, ForeColor = Text, FlatStyle = FlatStyle.Flat, Font = Body, Margin = new Padding(0, S(4), S(8), S(4)) };
            c.Items.AddRange(items);
            if (items.Length > 0) c.SelectedIndex = 0;
            return c;
        }

        public static string Temp(J v) { return v.IsNum ? Math.Round(v.Num).ToString(CultureInfo.InvariantCulture) + " °C" : "–"; }

        public static string Size(double bytes)
        {
            if (bytes >= 1 << 30) return (bytes / (1 << 30)).ToString("0.0", CultureInfo.InvariantCulture) + " GB";
            if (bytes >= 1 << 20) return Math.Round(bytes / (1 << 20)) + " MB";
            if (bytes >= 1 << 10) return Math.Round(bytes / (1 << 10)) + " KB";
            return bytes + " bytes";
        }

        public static string Ago(double unix)
        {
            var s = (DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddSeconds(unix)).TotalSeconds;
            if (s < 90) return "just now";
            if (s < 5400) return Math.Round(s / 60) + " min ago";
            if (s < 172800) return Math.Round(s / 3600) + " h ago";
            return Math.Round(s / 86400) + " days ago";
        }

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, string l);
        public static void SetCue(TextBox t, string cue)
        {
            t.HandleCreated += delegate { SendMessage(t.Handle, 0x1501, (IntPtr)1, cue); };   // EM_SETCUEBANNER
        }

        public static bool Confirm(IWin32Window owner, string text)
        {
            return MessageBox.Show(owner, text, "Deck", MessageBoxButtons.OKCancel, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2) == DialogResult.OK;
        }
    }

    // ------------------------------------------------------------------ pages
    public abstract class Page : Panel
    {
        protected readonly MainForm App;
        protected Api Api { get { return App.Api; } }
        public abstract string Title { get; }
        public virtual int PollSeconds { get { return 0; } }     // 0 = only when shown
        bool built;

        protected Page(MainForm app)
        {
            App = app;
            Dock = DockStyle.Fill; AutoScroll = true; BackColor = Ui.Bg; DoubleBuffered = true;
            Padding = new Padding(Ui.S(16), Ui.S(14), Ui.S(4), Ui.S(8));
        }

        public void Shown_()
        {
            if (!built) { SuspendLayout(); Build(); ResumeLayout(); built = true; }
            Refresh_();
        }
        protected abstract void Build();
        public abstract void Refresh_();

        // the cards sit in columns that wrap on a narrow screen
        protected FlowLayoutPanel Columns()
        {
            var f = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, WrapContents = true, BackColor = Ui.Bg };
            Controls.Add(f);
            return f;
        }
        protected FlowLayoutPanel Column(FlowLayoutPanel parent)
        {
            var c = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Margin = new Padding(0), BackColor = Ui.Bg };
            parent.Controls.Add(c);
            return c;
        }

        // shows an action's answer in the status line at the bottom
        protected Action<J> Report(string doing, Action after = null)
        {
            App.Say(doing + "...", Ui.Dim);
            return delegate (J r)
            {
                if (r["ok"].Bool || (!r.Has("ok") && !r.Has("error"))) App.Say(r["msg"].Str.Length > 0 ? r["msg"].Str : doing + ": done.", Ui.Good);
                else App.Say(r["error"].Str.Length > 0 ? r["error"].Str : doing + " failed.", Ui.Bad);
                if (after != null) after();
            };
        }
    }

    // ---- Overview ---------------------------------------------------------------
    public sealed class OverviewPage : Page
    {
        Label state, players, names, join, idle, version, pack, busy, cpu, gpu, ssd, pi, k12;
        Button start, stop, restart, off, copy;
        ListBox events;
        static readonly SolidBrush FieldBrush = new SolidBrush(Ui.Field);
        public OverviewPage(MainForm a) : base(a) { }
        public override string Title { get { return "Overview"; } }

        protected override void Build()
        {
            var cols = Columns();
            var left = Column(cols);
            FlowLayoutPanel b;
            left.Controls.Add(Ui.Card_("Minecraft", out b));
            state = Ui.L("…", Ui.Big);
            players = Ui.L("", Ui.Body, Ui.Dim);
            b.Controls.Add(Ui.Row(state, players));
            names = Ui.L("", Ui.Body, Ui.Dim); names.MaximumSize = new Size(Ui.S(460), 0);
            b.Controls.Add(names);
            join = Ui.L("");
            copy = Ui.B("Copy", delegate { if (join.Tag != null) { Clipboard.SetText((string)join.Tag); App.Say("Join address copied.", Ui.Good); } });
            b.Controls.Add(Ui.Row(Ui.L("Join at", Ui.Body, Ui.Dim), join, copy));
            version = Ui.L("", Ui.Small, Ui.Dim); pack = Ui.L("", Ui.Small, Ui.Dim); idle = Ui.L("", Ui.Small, Ui.Warn);
            b.Controls.Add(Ui.Row(version, pack));
            b.Controls.Add(idle);
            start = Ui.B("Start Minecraft", delegate { Api.Post("/api/mc/start", null, Report("Starting Minecraft", App.RefreshStatus)); }, Ui.Accent, 200);
            restart = Ui.B("Restart", delegate
            {
                if (Ui.Confirm(this, "Restart Minecraft? Players are disconnected for a minute or two.\nNew server settings take effect."))
                    Api.Post("/api/mc/restart", null, Report("Restarting Minecraft", App.RefreshStatus));
            });
            stop = Ui.B("Stop server", delegate { Api.Post("/api/mc/stop", null, Report("Stopping Minecraft", App.RefreshStatus)); });
            off = Ui.B("Stop + shut down this PC", delegate
            {
                if (Ui.Confirm(this, "Save the world, stop Minecraft and shut THIS PC down?\nThis screen goes dark in about a minute."))
                    Api.Post("/api/mc/off", null, Report("Stopping and shutting down", App.RefreshStatus));
            }, Ui.Bad);
            b.Controls.Add(Ui.Row(start, restart, stop, off));
            busy = Ui.L("", Ui.Small, Ui.Accent);
            b.Controls.Add(busy);

            left.Controls.Add(Ui.Card_("Temperatures", out b));
            k12 = Ui.L("", Ui.Small, Ui.Dim);
            b.Controls.Add(k12);
            cpu = Tile(b, "PC CPU"); gpu = Tile(b, "PC GPU"); ssd = Tile(b, "PC SSD"); pi = Tile(b, "Pi");
            b.Controls.Add(Ui.Row((Control)cpu.Parent, gpu.Parent, ssd.Parent, pi.Parent));

            var right = Column(cols);
            right.Controls.Add(Ui.Card_("Events", out b));
            events = Ui.List(520, 420);
            events.DrawMode = DrawMode.OwnerDrawFixed;
            events.ItemHeight = Ui.S(22);
            events.DrawItem += DrawEvent;
            b.Controls.Add(events);
        }

        Label Tile(FlowLayoutPanel parent, string name)
        {
            var t = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, BackColor = Ui.Field, Padding = new Padding(Ui.S(10), Ui.S(6), Ui.S(10), Ui.S(6)), Margin = new Padding(0, Ui.S(4), Ui.S(8), Ui.S(4)), MinimumSize = new Size(Ui.S(96), 0) };
            var v = Ui.L("–", Ui.Big);
            t.Controls.Add(v);
            t.Controls.Add(Ui.L(name, Ui.Small, Ui.Dim));
            return v;
        }

        void DrawEvent(object s, DrawItemEventArgs e)
        {
            if (e.Index < 0) return;
            var ev = (J)events.Items[e.Index];
            e.Graphics.FillRectangle(FieldBrush, e.Bounds);
            string t = ev["t"].Str; if (t.Length >= 16) t = t.Substring(11, 5);
            TextRenderer.DrawText(e.Graphics, t, Ui.Small, new Point(e.Bounds.X + 4, e.Bounds.Y + 3), Ui.Dim);
            TextRenderer.DrawText(e.Graphics, ev["text"].Str, Ui.Body, new Rectangle(e.Bounds.X + Ui.S(46), e.Bounds.Y + 1, e.Bounds.Width - Ui.S(48), e.Bounds.Height),
                Ui.LevelColor(ev["level"].Str), TextFormatFlags.EndEllipsis | TextFormatFlags.SingleLine);
        }

        public override void Refresh_() { Show_(App.Status); }

        public void Show_(J st)
        {
            if (state == null || st.IsNull) return;
            var s = st["state"]; var mc = s["mc"]; var pc = s["k12"];
            bool online = mc["online"].Bool, up = pc["up"].Bool;
            state.Text = online ? "● Online" : up ? "● PC on, Minecraft off" : "● PC asleep";
            state.ForeColor = online ? Ui.Good : up ? Ui.Warn : Ui.Dim;
            players.Text = online ? mc["players"].Str + "/" + mc["max"].Str + " playing" : "";
            var nm = mc["names"].List.Select(x => x.Str).ToArray();
            names.Text = online ? (nm.Length > 0 ? string.Join(", ", nm) : "nobody on right now") : "";
            string j = s["join"].Str;
            join.Text = j.Length > 0 ? j : "(appears once the PC is on)"; join.Tag = j.Length > 0 ? j : null; copy.Enabled = j.Length > 0;
            version.Text = online && mc["version"].Str.Length > 0 ? "Version " + mc["version"].Str : "";
            pack.Text = st["modpack"].Str.Length > 0 ? "· Modpack: " + st["modpack"].Str : "· Vanilla";
            idle.Text = s["idle_left_s"].IsNum ? "Nobody on - the server stops in " + Math.Ceiling(s["idle_left_s"].Num / 60) + " min" : "";
            var act = st["action"];
            busy.Text = act.IsNull ? "" : "Working on: " + act["what"].Str + " (by " + act["by"].Str + ")";
            start.Enabled = !online && act.IsNull; stop.Enabled = up && act.IsNull; restart.Enabled = online && act.IsNull; off.Enabled = up && act.IsNull;
            var t = pc["temps"];
            cpu.Text = up ? Ui.Temp(t["cpu"]) : "–"; gpu.Text = up ? Ui.Temp(t["gpu"]) : "–"; ssd.Text = up ? Ui.Temp(t["drive"]) : "–";
            pi.Text = Ui.Temp(s["pi"]["temp"]);
            cpu.ForeColor = s["temp_level"].Str == "ok" || !up ? Ui.Text : s["temp_level"].Str == "warn" ? Ui.Warn : Ui.Bad;
            pi.ForeColor = s["pi_level"].Str == "ok" ? Ui.Text : s["pi_level"].Str == "warn" ? Ui.Warn : Ui.Bad;
            k12.Text = up ? "PC on" + (pc["woke_by_deck"].Bool ? " (woken by the deck)" : "") : "PC off";
            var evs = st["events"].List;
            string key = string.Join("|", evs.Select(x => x["id"].Str));
            if ((string)events.Tag != key)
            {
                events.Tag = key;
                events.BeginUpdate(); events.Items.Clear();
                foreach (var e in evs) events.Items.Add(e);
                events.EndUpdate();
            }
        }
    }

    // ---- Console ----------------------------------------------------------------
    public sealed class ConsolePage : Page
    {
        TextBox log, cmd;
        Label note;
        string lastKey = "";
        public ConsolePage(MainForm a) : base(a) { }
        public override string Title { get { return "Console"; } }
        public override int PollSeconds { get { return 3; } }

        protected override void Build()
        {
            var cols = Columns();
            FlowLayoutPanel b;
            cols.Controls.Add(Ui.Card_("Server console", out b));
            log = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, WordWrap = false, BackColor = Color.FromArgb(11, 12, 14), ForeColor = Ui.Text, BorderStyle = BorderStyle.None, Font = Ui.Mono, Width = Ui.S(900), Height = Ui.S(460) };
            b.Controls.Add(log);
            note = Ui.L("", Ui.Small, Ui.Dim);
            b.Controls.Add(note);
            b.Controls.Add(Ui.Row(
                Ui.B("Who's on", delegate { Run("list"); }), Ui.B("Make it day", delegate { Run("time set day"); }),
                Ui.B("Clear weather", delegate { Run("weather clear"); }), Ui.B("Save now", delegate { Run("save-all"); })));
            cmd = Ui.T(700, "Command, e.g. say hello  (no / needed)");
            cmd.KeyDown += delegate (object s, KeyEventArgs e) { if (e.KeyCode == Keys.Enter) { e.SuppressKeyPress = true; Send(); } };
            b.Controls.Add(Ui.Row(cmd, Ui.B("Send", delegate { Send(); }, Ui.Accent)));
            b.Controls.Add(Ui.L("Stop and whitelist aren't sent from here: use Stop (it backs up) and Players. Quotes, braces and ; $ | & are refused.", Ui.Small, Ui.Dim));
        }

        void Send() { if (cmd.Text.Trim().Length > 0) { Run(cmd.Text.Trim()); cmd.Clear(); } }
        void Run(string c)
        {
            Api.Post("/api/console", new Dictionary<string, object> { { "cmd", c } }, delegate (J r)
            {
                if (r["ok"].Bool) App.Say("/" + c + ": " + r["msg"].Str, Ui.Good); else App.Say(r["error"].Str, Ui.Bad);
                Refresh_();
            });
        }

        public override void Refresh_()
        {
            Api.Get("/api/console", delegate (J r)
            {
                var lines = r["lines"].List.Select(x => x.Str).ToList();
                note.Text = !r["k12"].Bool && r.Has("k12") ? "The PC is off." : r["running"].Bool ? "" : "Minecraft isn't running - the log shows the last run.";
                string key = lines.Count + "|" + (lines.Count > 0 ? lines[lines.Count - 1] : "");
                if (key == lastKey) return;          // nothing new: don't touch the box (no flicker, no work)
                lastKey = key;
                log.Text = string.Join("\r\n", lines);
                log.SelectionStart = log.TextLength; log.ScrollToCaret();
            });
        }
    }

    // ---- Players ------------------------------------------------------------------
    public sealed class PlayersPage : Page
    {
        ListBox online, allowed;
        TextBox addName, banName;
        ComboBox mode;
        Label onlineNote, allowedNote, bans;
        public PlayersPage(MainForm a) : base(a) { }
        public override string Title { get { return "Players"; } }
        public override int PollSeconds { get { return 5; } }
        DateTime allowedAt = DateTime.MinValue;

        protected override void Build()
        {
            var cols = Columns();
            FlowLayoutPanel b;
            var c1 = Column(cols);
            c1.Controls.Add(Ui.Card_("Online now", out b));
            onlineNote = Ui.L("", Ui.Small, Ui.Dim); b.Controls.Add(onlineNote);
            online = Ui.List(300, 220); b.Controls.Add(online);
            b.Controls.Add(Ui.Row(Ui.B("Kick", delegate { OnSel("kick {0}", null); }),
                                  Ui.B("Ban", delegate { OnSel("ban {0}", "Ban {0}? They're kicked and can't join until you unban them."); }, Ui.Bad)));
            b.Controls.Add(Ui.Row(Ui.B("Make operator", delegate { OnSel("op {0}", "Make {0} an operator (admin commands)?"); }),
                                  Ui.B("Remove operator", delegate { OnSel("deop {0}", null); })));
            mode = Ui.Combo(140, "survival", "creative", "adventure", "spectator");
            b.Controls.Add(Ui.Row(Ui.L("Game mode"), mode, Ui.B("Set", delegate { OnSel("gamemode " + mode.Text + " {0}", null); })));

            var c2 = Column(cols);
            c2.Controls.Add(Ui.Card_("Who may join", out b));
            allowedNote = Ui.L("", Ui.Small, Ui.Dim); allowedNote.MaximumSize = new Size(Ui.S(320), 0); b.Controls.Add(allowedNote);
            allowed = Ui.List(300, 220); b.Controls.Add(allowed);
            addName = Ui.T(180, "Minecraft name");
            addName.KeyDown += delegate (object s, KeyEventArgs e) { if (e.KeyCode == Keys.Enter) { e.SuppressKeyPress = true; Add(); } };
            b.Controls.Add(Ui.Row(addName, Ui.B("Add", delegate { Add(); }, Ui.Accent)));
            b.Controls.Add(Ui.B("Remove selected", delegate
            {
                var n = allowed.SelectedItem as string;
                if (n == null) { App.Say("Pick a player in the list first.", Ui.Warn); return; }
                n = n.Split(' ')[0];
                if (!Ui.Confirm(this, "Remove " + n + "? They're kicked if they're playing.")) return;
                Wl("remove", n);
            }));
            b.Controls.Add(Ui.L("Names are checked with Mojang. With the PC off,\nchanges wait and happen when it starts.", Ui.Small, Ui.Dim));

            var c3 = Column(cols);
            c3.Controls.Add(Ui.Card_("Bans", out b));
            bans = Ui.L("Press Show bans (the server must be running).", Ui.Body, Ui.Dim); bans.MaximumSize = new Size(Ui.S(320), 0);
            b.Controls.Add(bans);
            b.Controls.Add(Ui.B("Show bans", delegate { Cmd("banlist players", delegate (J r) { bans.Text = r["ok"].Bool ? r["msg"].Str : r["error"].Str; }); }));
            banName = Ui.T(180, "Name to unban");
            b.Controls.Add(Ui.Row(banName, Ui.B("Unban", delegate
            {
                if (!Valid(banName.Text.Trim())) return;
                Cmd("pardon " + banName.Text.Trim(), null); banName.Clear();
            })));
        }

        static bool Valid(string n)
        {
            if (System.Text.RegularExpressions.Regex.IsMatch(n, "^[A-Za-z0-9_]{3,16}$")) return true;
            MessageBox.Show("Minecraft names are 3-16 letters, numbers or _.", "Deck");
            return false;
        }

        void OnSel(string fmt, string ask)
        {
            var n = online.SelectedItem as string;
            if (n == null) { App.Say("Pick a player in Online now first.", Ui.Warn); return; }
            if (ask != null && !Ui.Confirm(this, string.Format(ask, n))) return;
            Cmd(string.Format(fmt, n), null);
        }

        void Cmd(string c, Action<J> then)
        {
            Api.Post("/api/console", new Dictionary<string, object> { { "cmd", c } }, delegate (J r)
            {
                if (r["ok"].Bool) App.Say("/" + c + ": " + r["msg"].Str, Ui.Good); else App.Say(r["error"].Str, Ui.Bad);
                if (then != null) then(r);
            });
        }

        void Add()
        {
            var n = addName.Text.Trim();
            if (!Valid(n)) return;
            Wl("add", n); addName.Clear();
        }

        void Wl(string action, string name)
        {
            App.Say((action == "add" ? "Adding " : "Removing ") + name + "...", Ui.Dim);
            Api.Post("/api/whitelist", new Dictionary<string, object> { { "action", action }, { "name", name } }, delegate (J r)
            {
                if (r["ok"].Bool) App.Say(r["msg"].Str, Ui.Good); else App.Say(r["error"].Str, Ui.Bad);
                LoadAllowed(true);
            });
        }

        void LoadAllowed(bool fresh)
        {
            if (allowed.Items.Count == 0) allowedNote.Text = "Loading...";
            Api.Get("/api/whitelist" + (fresh ? "?fresh=1" : ""), delegate (J r)
            {
                if (!r["ok"].Bool) { allowedNote.Text = r["error"].Str; return; }
                var items = r["names"].List.Select(x => x.Str).ToList();
                foreach (var p in r["pending"].List) items.Add(p["name"].Str + "  (" + p["action"].Str + " - waits for the PC)");
                allowedNote.Text = r["live"].Bool ? ""
                    : Api.Standalone ? "Minecraft is off - this is the saved list (it's used when it starts)."
                    : "PC is off - the last list the Pi saw.";
                Fill(allowed, items);
            });
        }

        static void Fill(ListBox l, List<string> items)
        {
            string key = string.Join("|", items);
            if ((string)l.Tag == key) return;
            l.Tag = key;
            var sel = l.SelectedItem;
            l.BeginUpdate(); l.Items.Clear(); foreach (var i in items) l.Items.Add(i); l.EndUpdate();
            if (sel != null && l.Items.Contains(sel)) l.SelectedItem = sel;
        }

        public override void Refresh_()
        {
            var mc = App.Status["state"]["mc"];
            onlineNote.Text = mc["online"].Bool ? mc["players"].Str + " of " + mc["max"].Str + " (the list shows up to 12 names)" : "Minecraft isn't running.";
            Fill(online, mc["names"].List.Select(x => x.Str).ToList());
            // who's online comes with the status the app reads anyway; the join list costs the PC an SSH call
            if ((DateTime.Now - allowedAt).TotalSeconds >= 20) { allowedAt = DateTime.Now; LoadAllowed(false); }
        }
    }

    // ---- Server: performance + settings --------------------------------------------
    public sealed class ServerPage : Page
    {
        // what each setting is called and which values it takes (the PC agent checks again)
        // Def = what the server uses when the setting isn't written down yet (Minecraft's / the deck's default)
        sealed class Field { public string Kind, Key, Label, Help, Def; public string[] Choices; public int Min, Max; public Control Edit; public string Current; }
        static Field F(string kind, string key, string label, string help, string def, int min, int max, params string[] choices)
        {
            return new Field { Kind = kind, Key = key, Label = label, Help = help, Def = def, Min = min, Max = max, Choices = choices };
        }
        readonly List<Field> fields = new List<Field>
        {
            F("env", "MC_DIFFICULTY", "Difficulty", null, "normal", 0, 0, "peaceful", "easy", "normal", "hard"),
            F("env", "MC_MOTD", "Server name (MOTD)", "Letters, numbers, spaces and . , ! ? : ( ) / + & @ * _ -", "Apache 4800 deck", 0, 0),
            F("env", "MC_MAX_PLAYERS", "Max players", null, "10", 1, 100),
            F("env", "MC_MEMORY", "Memory for vanilla", "A modpack uses its own (Pi & deck > Modpack memory)", "6G", 0, 0, "2G", "3G", "4G", "5G", "6G", "7G"),
            F("env", "MC_VERSION", "Minecraft version", "LATEST or e.g. 1.21.4. Never go back to an older version on a world", "LATEST", 0, 0),
            F("env", "MC_TYPE", "Server type (vanilla)", "PAPER is faster; FABRIC / NEOFORGE are for mods you add yourself", "VANILLA", 0, 0, "VANILLA", "PAPER", "FABRIC", "NEOFORGE"),
            F("prop", "gamemode", "Game mode for new players", null, "survival", 0, 0, "survival", "creative", "adventure", "spectator"),
            F("prop", "force-gamemode", "Everyone gets that game mode on join", null, "false", 0, 0, "false", "true"),
            F("prop", "view-distance", "View distance (chunks)", "Lower = less work for the PC", "10", 3, 32),
            F("prop", "simulation-distance", "Simulation distance (chunks)", "Lower = less work for the PC", "10", 3, 32),
            F("prop", "pvp", "Players can hurt each other", null, "true", 0, 0, "true", "false"),
            F("prop", "spawn-monsters", "Monsters", null, "true", 0, 0, "true", "false"),
            F("prop", "allow-flight", "Allow flying (modpacks often need it)", null, "false", 0, 0, "false", "true"),
            F("prop", "spawn-protection", "Spawn protection (blocks)", "0 = off", "16", 0, 64),
            F("prop", "enable-command-block", "Command blocks", null, "false", 0, 0, "false", "true"),
            F("prop", "hardcore", "Hardcore", "Only for a new world", "false", 0, 0, "false", "true"),
        };
        Label tps, mspt, cpu, mem, perfNote, setNote;
        TableLayoutPanel grid;
        Button save;
        public ServerPage(MainForm a) : base(a) { }
        public override string Title { get { return "Server"; } }
        public override int PollSeconds { get { return 10; } }

        protected override void Build()
        {
            var cols = Columns();
            FlowLayoutPanel b;
            var c1 = Column(cols);
            c1.Controls.Add(Ui.Card_("Performance", out b));
            tps = Stat(b, "ticks per second (20 = smooth)"); mspt = Stat(b, "ms per tick (under 50 = keeping up)");
            cpu = Stat(b, "CPU (100% = one core)"); mem = Stat(b, "memory used");
            perfNote = Ui.L("", Ui.Small, Ui.Dim); perfNote.MaximumSize = new Size(Ui.S(300), 0);
            b.Controls.Add(perfNote);

            var c2 = Column(cols);
            c2.Controls.Add(Ui.Card_("Server settings", out b));
            setNote = Ui.L("", Ui.Small, Ui.Dim); setNote.MaximumSize = new Size(Ui.S(640), 0);
            b.Controls.Add(setNote);
            grid = new TableLayoutPanel { ColumnCount = 2, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, BackColor = Ui.Card };
            grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize)); grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            string group = null;
            foreach (var f in fields)
            {
                if (f.Kind != group)
                {
                    group = f.Kind;
                    var h = Ui.L(group == "env" ? "Every world" : "This world (each modpack keeps its own)", Ui.Head, Ui.Accent);
                    h.Margin = new Padding(0, Ui.S(10), 0, Ui.S(2));
                    grid.Controls.Add(h); grid.SetColumnSpan(h, 2);
                }
                var lbl = Ui.L(f.Label);
                if (f.Help != null) new ToolTip().SetToolTip(lbl, f.Help);
                if (f.Choices.Length > 0) f.Edit = Ui.Combo(170, f.Choices);
                else if (f.Max > 0) f.Edit = new NumericUpDown { Minimum = f.Min, Maximum = f.Max, Width = Ui.S(90), BackColor = Ui.Field, ForeColor = Ui.Text, BorderStyle = BorderStyle.FixedSingle, Font = Ui.Body, Margin = new Padding(0, Ui.S(4), 0, Ui.S(4)) };
                else f.Edit = Ui.T(f.Key == "MC_MOTD" ? 300 : 170);
                var edit = f.Edit;
                Control cell = edit;
                if (f.Help != null) { var p = Ui.Row(edit, Ui.L(f.Help, Ui.Small, Ui.Dim)); cell = p; }
                grid.Controls.Add(lbl); grid.Controls.Add(cell);
            }
            b.Controls.Add(grid);
            save = Ui.B("Save changes", delegate { Save(); }, Ui.Accent);
            b.Controls.Add(Ui.Row(save, Ui.B("Restart Minecraft now", delegate
            {
                if (Ui.Confirm(this, "Restart Minecraft so the new settings are used?\nPlayers are disconnected for a minute or two."))
                    Api.Post("/api/mc/restart", null, Report("Restarting Minecraft", App.RefreshStatus));
            }), Ui.B("Undo my edits", delegate { LoadSettings(); })));
        }

        Label Stat(FlowLayoutPanel b, string what)
        {
            var v = Ui.L("–", Ui.Big);
            b.Controls.Add(Ui.Row(v, Ui.L(what, Ui.Small, Ui.Dim)));
            return v;
        }

        static string Val(Field f)
        {
            var n = f.Edit as NumericUpDown;
            if (n != null) return ((int)n.Value).ToString(CultureInfo.InvariantCulture);
            return f.Edit.Text.Trim();
        }

        void Save()
        {
            var todo = fields.Where(f => f.Edit.Enabled && f.Current != null && Val(f) != f.Current).ToList();
            if (todo.Count == 0) { App.Say("Nothing changed.", Ui.Dim); return; }
            save.Enabled = false;
            int left = todo.Count, failed = 0;
            foreach (var f in todo)
            {
                var field = f;
                Api.Post("/api/server/settings", new Dictionary<string, object> { { "kind", f.Kind }, { "key", f.Key }, { "value", Val(f) } }, delegate (J r)
                {
                    if (!r["ok"].Bool) { failed++; App.Say(field.Label + ": " + r["error"].Str, Ui.Bad); }
                    if (--left == 0)
                    {
                        save.Enabled = true;
                        if (failed == 0) App.Say("Saved " + todo.Count + " setting(s). They're used when Minecraft restarts.", Ui.Good);
                        LoadSettings();
                    }
                });
            }
        }

        void LoadSettings()
        {
            Api.Get("/api/server/settings", delegate (J r)
            {
                bool ok = r["ok"].Bool;
                setNote.ForeColor = ok ? Ui.Dim : Ui.Warn;
                if (!ok) { setNote.Text = r["error"].Str; foreach (var f in fields) { f.Edit.Enabled = false; f.Current = null; } save.Enabled = false; return; }
                var own = new HashSet<string>(r["pack_sets"].List.Select(x => x.Str));
                setNote.Text = (r["pack"].Str.Length > 0 ? "World on now: modpack " + r["pack"].Str + ". " : "World on now: vanilla. ")
                    + (r["props_exist"].Bool ? "" : "This world hasn't started yet, so blank settings use Minecraft's defaults. ")
                    + "Changes are used when Minecraft restarts.";
                foreach (var f in fields)
                {
                    var src = f.Kind == "env" ? r["env"] : r["props"];
                    string v = src[f.Key].Str;
                    if (v.Length == 0) v = f.Def;                  // not written down yet: what the server uses
                    var n = f.Edit as NumericUpDown;
                    var c = f.Edit as ComboBox;
                    if (n != null) { int x; if (!int.TryParse(v, out x)) int.TryParse(f.Def, out x); n.Value = Math.Max(f.Min, Math.Min(f.Max, x)); }
                    else if (c != null) { int i = c.Items.IndexOf(v); if (i < 0) i = Math.Max(0, c.Items.IndexOf(f.Def)); c.SelectedIndex = i; }
                    else f.Edit.Text = v;
                    f.Current = Val(f);                            // only what you change afterwards is sent
                    f.Edit.Enabled = !own.Contains(f.Key);         // the modpack sets this one itself
                }
                save.Enabled = true;
            });
        }

        public override void Refresh_()
        {
            if (grid.Tag == null) { grid.Tag = 1; LoadSettings(); }
            Api.Get("/api/perf", delegate (J r)
            {
                if (!r["running"].Bool)
                {
                    tps.Text = mspt.Text = cpu.Text = mem.Text = "–";
                    perfNote.Text = r.Has("error") ? r["error"].Str : r["k12"].Bool ? "Minecraft isn't running." : "The PC is off.";
                    return;
                }
                tps.Text = r["tps"].IsNum ? r["tps"].Num.ToString("0.0", CultureInfo.InvariantCulture) : "–";
                tps.ForeColor = !r["tps"].IsNum ? Ui.Text : r["tps"].Num >= 19 ? Ui.Good : r["tps"].Num >= 15 ? Ui.Warn : Ui.Bad;
                mspt.Text = r["mspt"].IsNum ? r["mspt"].Num.ToString("0.0", CultureInfo.InvariantCulture) : "–";
                cpu.Text = r["cpu"].IsNum ? Math.Round(r["cpu"].Num) + "%" : "–";
                mem.Text = r["mem_used"].Str.Length > 0 ? r["mem_used"].Str + " / " + r["mem_limit"].Str : "–";
                perfNote.Text = r["note"].Str;
            });
        }
    }

    // ---- Modpacks -------------------------------------------------------------------
    public sealed class ModpacksPage : Page
    {
        ListBox packs;
        TextBox link;
        Label active, preview, job, keyNote, importInfo;
        Button install, lookUp, pickZip, importGo;
        J lastPreview = J.Null, importPreview = J.Null;
        string zipPath;            // the export zip you picked
        object manifest;           // its manifest.json, as sent to the Pi

        // where the server reads imported zips (game-servers\imports, mounted as /imports)
        // (the demo's pretend Pi runs on this PC: its imports go to a temp folder, never the real server)
        string ImportsDir()
        {
            const string game = @"C:\deck\game-servers";
            bool demo = App.Host == "127.0.0.1" || App.Host == "localhost";
            return Directory.Exists(game) && !demo ? Path.Combine(game, "imports") : Path.Combine(Path.GetTempPath(), "deck-imports");
        }

        void PickZip()
        {
            var dlg = new OpenFileDialog { Filter = "CurseForge export (*.zip)|*.zip", Title = "Pick the zip from CurseForge's Export profile" };
            if (dlg.ShowDialog(this) != DialogResult.OK) return;
            LoadZip(dlg.FileName);
        }

        // also used when a zip is dropped on the window or opened with Deck.exe
        public void LoadZip(string path)
        {
            if (importInfo == null) Shown_();
            importGo.Visible = false; importPreview = J.Null; manifest = null;
            importInfo.ForeColor = Ui.Dim; importInfo.Text = "Reading " + Path.GetFileName(path) + "...";
            ThreadPool.QueueUserWorkItem(delegate
            {
                // only manifest.json is read here; the rest of the zip (the overrides) goes to the server as is
                string text = null, err = null; int overrides = 0;
                try
                {
                    using (var z = System.IO.Compression.ZipFile.OpenRead(path))
                    {
                        var e = z.GetEntry("manifest.json");
                        if (e == null) err = "There's no manifest.json in that zip, so it isn't a CurseForge export. A server pack or a world zip won't work: use the CurseForge app's Export profile.";
                        else if (e.Length > 4 * 1024 * 1024) err = "That zip's manifest.json is too big.";
                        else using (var r = new StreamReader(e.Open(), Encoding.UTF8)) text = r.ReadToEnd();
                        overrides = z.Entries.Count(x => x.FullName.StartsWith("overrides/") && x.Length > 0);
                    }
                }
                catch (Exception ex) { err = "Couldn't read that zip: " + ex.Message; }
                BeginInvoke((Action)delegate
                {
                    if (err == null) { try { manifest = Api.Json.DeserializeObject(text); } catch { err = "The zip's manifest.json isn't readable."; } }
                    if (err != null) { importInfo.ForeColor = Ui.Bad; importInfo.Text = err; return; }
                    zipPath = path;
                    Api.Post("/api/modpack/import-check", new Dictionary<string, object> { { "manifest", manifest } }, delegate (J p)
                    {
                        if (!p["ok"].Bool) { importInfo.ForeColor = Ui.Bad; importInfo.Text = p["error"].Str; return; }
                        importPreview = p;
                        importInfo.ForeColor = Ui.Text;
                        importInfo.Text = p["file_name"].Str + "\nMinecraft " + p["mc"].Str + " · " + (p["loader"].Str.Length > 0 ? p["loader"].Str : "no mod loader")
                            + " · " + p["mods"].Str + " mods · " + overrides + " config/override files"
                            + "\nKept on the deck as: " + p["slug"].Str
                            + (p["warning"].Str.Length > 0 ? "\n⚠ " + p["warning"].Str : "");
                        importGo.Visible = p["can_install"].Bool;
                    });
                });
            });
        }

        void DoImport()
        {
            if (importPreview.IsNull || zipPath == null) return;
            string slug = importPreview["slug"].Str, name = importPreview["name"].Str;
            if (!Ui.Confirm(this, "Import " + importPreview["file_name"].Str + " and switch the server to it?\n\n" +
                "The current world is saved and backed up first. The first start downloads every mod (10+ minutes).\n" +
                "Already have a pack called " + slug + "? This replaces its mods and configs with this export; its world is kept.")) return;
            importGo.Enabled = pickZip.Enabled = false;
            App.Say("Copying " + Path.GetFileName(zipPath) + " to the server...", Ui.Dim);
            string src = zipPath;
            ThreadPool.QueueUserWorkItem(delegate
            {
                string err = null;
                try
                {
                    string dir = ImportsDir(), dest = Path.Combine(dir, slug + ".zip"), tmp = dest + ".part";
                    Directory.CreateDirectory(dir);
                    File.Copy(src, tmp, true);
                    if (File.Exists(dest)) File.Delete(dest);
                    File.Move(tmp, dest);
                }
                catch (Exception ex) { err = ex.Message; }
                BeginInvoke((Action)delegate
                {
                    importGo.Enabled = pickZip.Enabled = true;
                    if (err != null) { App.Say("Couldn't copy the zip to the server folder: " + err, Ui.Bad); return; }
                    Api.Post("/api/modpack/import", new Dictionary<string, object> { { "manifest", manifest } },
                        Report("Importing " + name + " (progress under Last switch)", delegate { importGo.Visible = false; Refresh_(); }));
                });
            });
        }
        public ModpacksPage(MainForm a) : base(a) { }
        public override string Title { get { return "Modpacks"; } }
        public override int PollSeconds { get { return 4; } }

        protected override void Build()
        {
            var cols = Columns();
            FlowLayoutPanel b;
            var c1 = Column(cols);
            c1.Controls.Add(Ui.Card_("Installed", out b));
            active = Ui.L("", Ui.Body, Ui.Good); b.Controls.Add(active);
            packs = Ui.List(420, 200); b.Controls.Add(packs);
            b.Controls.Add(Ui.Row(
                Ui.B("Switch to selected", delegate
                {
                    var p = Sel(); if (p == null) return;
                    if (Ui.Confirm(this, "Switch the server to " + p["name"].Str + "?\nThe world that's on now is saved and backed up first; players get a warning."))
                        Api.Post("/api/modpack/switch", new Dictionary<string, object> { { "slug", p["slug"].Str } }, Report("Switching"));
                }, Ui.Accent),
                Ui.B("Switch to vanilla", delegate
                {
                    if (Ui.Confirm(this, "Switch back to plain Minecraft? The modpack's world is saved and kept."))
                        Api.Post("/api/modpack/switch", new Dictionary<string, object> { { "slug", "" } }, Report("Switching to vanilla"));
                }),
                Ui.B("Delete selected", delegate
                {
                    var p = Sel(); if (p == null) return;
                    if (Ui.Confirm(this, "Delete " + p["name"].Str + " and its world from the PC?\nIts world is backed up to the Pi first, and backups are never deleted."))
                        Api.Post("/api/modpack/delete", new Dictionary<string, object> { { "slug", p["slug"].Str } }, Report("Deleting"));
                }, Ui.Bad)));
            keyNote = Ui.L("", Ui.Small, Ui.Warn); keyNote.MaximumSize = new Size(Ui.S(440), 0); b.Controls.Add(keyNote);

            c1.Controls.Add(Ui.Card_("Install from CurseForge", out b));
            link = Ui.T(330, "https://www.curseforge.com/minecraft/modpacks/...");
            lookUp = Ui.B("Look up", delegate { LookUp(); });
            b.Controls.Add(Ui.Row(link, lookUp));
            preview = Ui.L("", Ui.Body); preview.MaximumSize = new Size(Ui.S(440), 0); b.Controls.Add(preview);
            install = Ui.B("Install + switch to it", delegate
            {
                if (lastPreview.IsNull) return;
                if (Ui.Confirm(this, "Install " + lastPreview["name"].Str + " and switch the server to it?\nThe current world is saved and backed up first. The first start can take 10+ minutes."))
                    Api.Post("/api/modpack/install", new Dictionary<string, object> { { "link", link.Text.Trim() } }, Report("Installing"));
            }, Ui.Accent);
            install.Visible = false;
            b.Controls.Add(install);
            b.Controls.Add(Ui.B("Keybind presets, mod uploads… (opens the full panel)", delegate { App.OpenPanel("#c-mod"); }));

            c1.Controls.Add(Ui.Card_("Import a CurseForge export", out b));
            b.Controls.Add(Ui.L("For a pack you made or changed yourself: in the CurseForge app open the\n" +
                                "pack's profile, then ⋯ > Export profile, and pick that zip here. The server\n" +
                                "downloads every mod, leaves out the ones that only work on players' computers\n" +
                                "and copies the pack's configs.", Ui.Small, Ui.Dim));
            pickZip = Ui.B("Choose export zip…", delegate { PickZip(); });
            b.Controls.Add(pickZip);
            importInfo = Ui.L("", Ui.Body); importInfo.MaximumSize = new Size(Ui.S(440), 0); b.Controls.Add(importInfo);
            importGo = Ui.B("Import + switch to it", delegate { DoImport(); }, Ui.Accent);
            importGo.Visible = false;
            b.Controls.Add(importGo);

            var c2 = Column(cols);
            c2.Controls.Add(Ui.Card_("Last switch", out b));
            job = Ui.L("No switch yet.", Ui.Mono, Ui.Text); job.MaximumSize = new Size(Ui.S(560), 0); b.Controls.Add(job);
            b.Controls.Add(Ui.B("Clear", delegate { Api.Post("/api/modpack/clear", null, delegate { Refresh_(); }); }));
        }

        J Sel()
        {
            int i = packs.SelectedIndex;
            var list = packs.Tag as List<J>;
            if (i < 0 || list == null || i >= list.Count) { App.Say("Pick a pack in the list first.", Ui.Warn); return null; }
            return list[i];
        }

        void LookUp()
        {
            if (link.Text.Trim().Length == 0) return;
            lookUp.Enabled = false; preview.Text = "Looking it up..."; preview.ForeColor = Ui.Dim; install.Visible = false;
            Api.Post("/api/modpack/preview", new Dictionary<string, object> { { "link", link.Text.Trim() } }, delegate (J p)
            {
                lookUp.Enabled = true;
                if (!p["ok"].Bool) { preview.Text = p["error"].Str; preview.ForeColor = Ui.Bad; lastPreview = J.Null; return; }
                lastPreview = p;
                preview.ForeColor = Ui.Text;
                preview.Text = p["name"].Str + "\n" + p["summary"].Str + "\nMinecraft " + p["mc"].Str + " · " + p["loader"].Str + " · " + p["file_name"].Str
                    + (p["pinned"].Bool ? " (from your link)" : " (newest)") + (p["warning"].Str.Length > 0 ? "\n⚠ " + p["warning"].Str : "");
                install.Visible = p["can_install"].Bool;
            });
        }

        public override void Refresh_()
        {
            Api.Get("/api/modpack", delegate (J r)
            {
                if (!r["ok"].Bool && r.Has("error")) { active.Text = r["error"].Str; active.ForeColor = Ui.Warn; }
                else { active.Text = "On now: " + r["active_name"].Str + (r["live"].Bool ? "" : "   (PC off - last list the Pi saw)"); active.ForeColor = Ui.Good; }
                var list = r["packs"].List;
                string key = string.Join("|", list.Select(p => p["slug"].Str + p["has_world"].Str)) + r["active"].Str;
                if ((string)packs.AccessibleDescription != key)
                {
                    packs.AccessibleDescription = key; packs.Tag = list;
                    int sel = packs.SelectedIndex;
                    packs.BeginUpdate(); packs.Items.Clear();
                    foreach (var p in list)
                    {
                        string s = (p["slug"].Str == r["active"].Str ? "● " : "   ") + p["name"].Str;
                        string sub = string.Join(" · ", new[] { p["mc"].Str, p["loader"].Str }.Where(x => x.Length > 0));
                        if (sub.Length > 0) s += "  (" + sub + ")";
                        if (p["removed"].List.Count > 0) s += "  - " + p["removed"].List.Count + " mod(s) left out";
                        packs.Items.Add(s);
                    }
                    packs.EndUpdate();
                    if (sel >= 0 && sel < packs.Items.Count) packs.SelectedIndex = sel;
                }
                var notes = new List<string>();
                if (r.Has("pc_key") && !r["pc_key"].Bool) notes.Add("No CurseForge key on the PC: run setup-curseforge.cmd.");
                if (r.Has("pi_key") && !r["pi_key"].Bool) notes.Add("No CurseForge key on the Pi: sudo deck-curseforge-setup.");
                keyNote.Text = string.Join("\n", notes);
                ShowJob(r["job"]);
            });
        }

        void ShowJob(J j)
        {
            if (j.IsNull) { job.Text = "No switch yet."; job.ForeColor = Ui.Dim; return; }
            var sb = new StringBuilder();
            sb.AppendLine((j["running"].Bool ? "Working: " : j["ok"].Bool ? "Done: " : "Stopped: ") + j["name"].Str);
            foreach (var s in j["steps"].List)
            {
                string st = s["status"].Str;
                string icon = st == "ok" ? "✓" : st == "fail" ? "✕" : st == "running" ? "…" : st == "skip" ? "–" : " ";
                sb.AppendLine(icon + " " + s["label"].Str + (s["detail"].Str.Length > 0 ? ": " + s["detail"].Str : ""));
            }
            foreach (var m in j["missing"].List) sb.AppendLine("  needs by hand: " + m["mod"].Str + " (" + m["file"].Str + ")");
            if (j["missing"].List.Count > 0) sb.AppendLine("Download them, then upload in the full panel (button on the left).");
            if (j["problem"].Str.Length > 0) sb.AppendLine(j["problem"].Str);
            job.Text = sb.ToString().TrimEnd();
            job.ForeColor = j["running"].Bool ? Ui.Accent : j["ok"].Bool ? Ui.Good : Ui.Warn;
        }
    }

    // ---- Backups ---------------------------------------------------------------------
    public sealed class BackupsPage : Page
    {
        ListBox list;
        Label age, where;
        public BackupsPage(MainForm a) : base(a) { }
        public override string Title { get { return "Backups"; } }
        public override int PollSeconds { get { return 15; } }

        protected override void Build()
        {
            var cols = Columns();
            FlowLayoutPanel b;
            cols.Controls.Add(Ui.Card_("World backups", out b));
            age = Ui.L("", Ui.Body, Ui.Dim); b.Controls.Add(age);
            where = Ui.L("", Ui.Small, Ui.Dim); b.Controls.Add(where);
            list = Ui.List(620, 360); b.Controls.Add(list);
            b.Controls.Add(Ui.Row(
                Ui.B("Back up now", delegate { Api.Post("/api/backup", null, Report("Backing up (progress in Events)")); }, Ui.Accent),
                Ui.B("Download selected…", delegate { Download(); })));
            b.Controls.Add(Ui.L("Restore: stop Minecraft, rename the world folder on the PC to world-old,\nunzip the backup in its place, start again (README > World backups).", Ui.Small, Ui.Dim));
        }

        void Download()
        {
            var files = list.Tag as List<string>;
            int i = list.SelectedIndex;
            if (files == null || i < 0 || i >= files.Count) { App.Say("Pick a backup first.", Ui.Warn); return; }
            var dlg = new SaveFileDialog { FileName = files[i], Filter = "Zip|*.zip" };
            if (dlg.ShowDialog(this) != DialogResult.OK) return;
            App.Say("Downloading " + files[i] + "...", Ui.Dim);
            Api.Download("/api/backups/" + files[i], dlg.FileName, delegate (string err)
            {
                if (err == null) App.Say("Saved " + dlg.FileName, Ui.Good); else App.Say("Download failed: " + err, Ui.Bad);
            });
        }

        public override void Refresh_()
        {
            where.Text = Api.Standalone ? "Kept on this PC: " + LocalDeck.BackupDir + "  (they go to the Pi again once it's connected)" : "Kept on the Pi.";
            Api.Get("/api/backups", delegate (J r)
            {
                var bs = r["backups"].List;
                age.Text = bs.Count == 0 ? "No backups yet." : "Newest: " + Ui.Ago(bs[0]["t"].Num) + " · " + bs.Count + " kept";
                var files = bs.Select(x => x["file"].Str).ToList();
                string key = string.Join("|", files);
                if ((string)list.AccessibleDescription == key) return;
                list.AccessibleDescription = key; list.Tag = files;
                list.BeginUpdate(); list.Items.Clear();
                foreach (var x in bs) list.Items.Add(x["file"].Str + "    " + Ui.Size(x["bytes"].Num) + " · " + Ui.Ago(x["t"].Num));
                list.EndUpdate();
            });
        }
    }

    // ---- Pi & deck ----------------------------------------------------------------------
    public sealed class PiPage : Page
    {
        Label wifi, inet, checkRes, packRes, cfgNote, piNote;
        CheckBox travel, full, piOff;
        Button checkBtn, packBtn, cfgSave, piRestart, piShut;
        TableLayoutPanel cfgGrid;
        readonly Dictionary<string, Control> cfgEdits = new Dictionary<string, Control>();
        readonly Dictionary<string, string> cfgNow = new Dictionary<string, string>();
        bool settingTravel;
        public PiPage(MainForm a) : base(a) { }
        public override string Title { get { return "Pi & deck"; } }
        public override int PollSeconds { get { return 5; } }

        protected override void Build()
        {
            var cols = Columns();
            FlowLayoutPanel b;
            var c1 = Column(cols);
            c1.Controls.Add(Ui.Card_("Wi-Fi & travel", out b));
            wifi = Ui.L(""); inet = Ui.L("", Ui.Body, Ui.Dim);
            b.Controls.Add(wifi); b.Controls.Add(inet);
            travel = new CheckBox { Text = "Travel mode: share the Pi's Wi-Fi with this PC + the laptop", AutoSize = true, ForeColor = Ui.Text, Font = Ui.Body };
            travel.CheckedChanged += delegate
            {
                if (settingTravel) return;
                bool on = travel.Checked;
                Api.Post("/api/travel", new Dictionary<string, object> { { "on", on } }, Report("Travel mode " + (on ? "on" : "off"), delegate { LoadNet(); }));
            };
            b.Controls.Add(travel);
            b.Controls.Add(Ui.B("Choose Wi-Fi network… (opens the full panel)", delegate { App.OpenPanel("#c-net"); }));

            c1.Controls.Add(Ui.Card_("Pi power", out b));
            piNote = Ui.L("", Ui.Small, Ui.Warn); b.Controls.Add(piNote);
            b.Controls.Add(Ui.Row(
                piRestart = Ui.B("Restart the Pi", delegate
                {
                    if (Ui.Confirm(this, "Restart the Pi? The panel, Discord bot and watchdog are gone for about a minute.\nMinecraft on this PC keeps running."))
                        Api.Post("/api/pi/reboot", null, Report("Restarting the Pi"));
                }),
                piShut = Ui.B("Shut the Pi down", delegate
                {
                    if (Ui.Confirm(this, "Shut the Pi down? Nothing can wake this PC, back up, or watch temperatures until the Pi is switched on again (unplug and replug its power)."))
                        Api.Post("/api/pi/poweroff", null, Report("Shutting the Pi down"));
                }, Ui.Bad)));

            c1.Controls.Add(Ui.Card_("Check the deck", out b));
            full = new CheckBox { Text = "Also wake + test the PC (Docker, temps, EULA)", AutoSize = true, ForeColor = Ui.Text, Font = Ui.Body };
            checkBtn = Ui.B("Check the deck", delegate { Api.Post("/api/check", new Dictionary<string, object> { { "full", full.Checked } }, Report("Checking the deck", delegate { LoadCheck(); })); });
            b.Controls.Add(Ui.Row(checkBtn, full));
            checkRes = Ui.L("", Ui.Body); checkRes.MaximumSize = new Size(Ui.S(460), 0); b.Controls.Add(checkRes);

            c1.Controls.Add(Ui.Card_("Pack up", out b));
            b.Controls.Add(Ui.L("Warns players, saves + stops Minecraft, backs up to the Pi,\nwaits for the PC to cool, shuts this PC down. The screen goes dark:\ntick the by-hand checklist on your phone's Deck Panel.", Ui.Small, Ui.Dim));
            piOff = new CheckBox { Text = "Shut the Pi down too at the end", Checked = true, AutoSize = true, ForeColor = Ui.Text, Font = Ui.Body };
            packBtn = Ui.B("Pack up", delegate
            {
                if (Ui.Confirm(this, "Pack up now? This PC shuts down at the end" + (piOff.Checked ? ", and the Pi too." : ".")))
                    Api.Post("/api/pack", new Dictionary<string, object> { { "pi_off", piOff.Checked } }, Report("Packing up", delegate { LoadPack(); }));
            }, Ui.Accent);
            b.Controls.Add(Ui.Row(packBtn, piOff));
            packRes = Ui.L("", Ui.Mono); packRes.MaximumSize = new Size(Ui.S(460), 0); b.Controls.Add(packRes);

            var c2 = Column(cols);
            c2.Controls.Add(Ui.Card_("Deck settings (on the Pi)", out b));
            cfgNote = Ui.L("", Ui.Small, Ui.Dim); cfgNote.MaximumSize = new Size(Ui.S(560), 0); b.Controls.Add(cfgNote);
            cfgGrid = new TableLayoutPanel { ColumnCount = 2, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, BackColor = Ui.Card };
            b.Controls.Add(cfgGrid);
            cfgSave = Ui.B("Save deck settings", delegate { SaveCfg(); }, Ui.Accent);
            b.Controls.Add(Ui.Row(cfgSave, Ui.B("Undo my edits", delegate { cfgGrid.Tag = null; LoadCfg(); })));
        }

        public override void Refresh_()
        {
            // without the Pi these can't work: grey them out rather than let them fail
            bool pi = !Api.Standalone;
            piRestart.Enabled = piShut.Enabled = travel.Enabled = pi;
            piNote.Text = pi ? "" : "The Pi isn't connected.";
            LoadNet(); LoadCheck(); LoadPack();
            if (cfgGrid.Tag == null) LoadCfg();
        }

        void LoadNet()
        {
            Api.Get("/api/net", delegate (J r)
            {
                if (!r["ok"].Bool) { wifi.Text = r["error"].Str; return; }
                var w = r["wifi"];
                wifi.Text = w["ssid"].Str.Length > 0 ? "Pi Wi-Fi: " + w["ssid"].Str + " (" + w["signal"].Str + "%)" : "Pi Wi-Fi: not connected";
                string i = r["internet"].Str;
                inet.Text = "Internet: " + (i == "portal" ? "login page in the way - open it on the laptop: " + r["portal_url"].Str : i);
                inet.ForeColor = i == "online" ? Ui.Good : Ui.Warn;
                settingTravel = true; travel.Checked = r["travel"].Bool; settingTravel = false;
            });
        }

        void LoadCheck()
        {
            Api.Get("/api/check", delegate (J r)
            {
                checkBtn.Enabled = !r["running"].Bool && !Api.Standalone;
                var res = r["result"];
                if (r["running"].Bool) { checkRes.Text = "Checking..."; checkRes.ForeColor = Ui.Dim; return; }
                if (res.IsNull) { checkRes.Text = Api.Standalone ? "The deck check runs on the Pi. Without it, the Overview page shows this PC's state." : ""; checkRes.ForeColor = Ui.Dim; return; }
                var sb = new StringBuilder();
                sb.AppendLine((res["ok"].Bool ? "✓ All good" : "Needs attention") + " · " + Ui.Ago(res["t"].Num));
                foreach (var x in res["results"].List)
                {
                    string icon = x["ok"].IsNull ? "–" : x["ok"].Bool ? "✓" : "✕";
                    sb.AppendLine(icon + " " + x["name"].Str + ": " + x["detail"].Str + (x["ok"].V is bool && !x["ok"].Bool && x["fix"].Str.Length > 0 ? "\n     → " + x["fix"].Str : ""));
                }
                checkRes.Text = sb.ToString().TrimEnd();
                checkRes.ForeColor = res["ok"].Bool ? Ui.Text : Ui.Warn;
            });
        }

        void LoadPack()
        {
            Api.Get("/api/pack", delegate (J r)
            {
                var p = r["pack"];
                packBtn.Enabled = (p.IsNull || !p["running"].Bool) && !Api.Standalone;
                piOff.Enabled = !Api.Standalone;
                if (p.IsNull) { packRes.Text = Api.Standalone ? "Pack up needs the Pi. Without it: Overview > Stop + shut down this PC (it saves the world first)." : ""; return; }
                var sb = new StringBuilder();
                foreach (var s in p["steps"].List)
                {
                    string st = s["status"].Str;
                    string icon = st == "ok" ? "✓" : st == "fail" ? "✕" : st == "running" ? "…" : st == "skip" ? "–" : " ";
                    sb.AppendLine(icon + " " + s["label"].Str + (s["detail"].Str.Length > 0 ? ": " + s["detail"].Str : ""));
                }
                packRes.Text = sb.ToString().TrimEnd();
            });
        }

        void LoadCfg()
        {
            Api.Get("/api/config", delegate (J r)
            {
                if (!r["ok"].Bool) { cfgNote.Text = r["error"].Str; cfgNote.ForeColor = Ui.Warn; cfgSave.Enabled = false; return; }
                cfgGrid.Tag = 1; cfgNote.ForeColor = Ui.Dim;
                cfgNote.Text = "The Pi uses these right away. Hover a name for help.";
                cfgGrid.SuspendLayout(); cfgGrid.Controls.Clear(); cfgEdits.Clear(); cfgNow.Clear();
                string group = null;
                foreach (var f in r["fields"].List)
                {
                    if (f["group"].Str != group)
                    {
                        group = f["group"].Str;
                        var h = Ui.L(group, Ui.Head, Ui.Accent); h.Margin = new Padding(0, Ui.S(10), 0, Ui.S(2));
                        cfgGrid.Controls.Add(h); cfgGrid.SetColumnSpan(h, 2);
                    }
                    string key = f["key"].Str;
                    var v = r["values"][key];
                    var lbl = Ui.L(f["label"].Str);
                    if (f["help"].Str.Length > 0) new ToolTip().SetToolTip(lbl, f["help"].Str);
                    Control edit;
                    if (f["type"].Str == "int")
                    {
                        var n = new NumericUpDown { Minimum = (decimal)f["min"].Num, Maximum = (decimal)f["max"].Num, Width = Ui.S(90), BackColor = Ui.Field, ForeColor = Ui.Text, BorderStyle = BorderStyle.FixedSingle, Font = Ui.Body, Margin = new Padding(0, Ui.S(4), 0, Ui.S(4)) };
                        if (v.IsNum) n.Value = (decimal)Math.Max(f["min"].Num, Math.Min(f["max"].Num, v.Num));
                        edit = n;
                        cfgNow[key] = ((int)n.Value).ToString(CultureInfo.InvariantCulture);
                    }
                    else { var t = Ui.T(220); t.Text = v.Str; edit = t; cfgNow[key] = v.Str; }
                    cfgEdits[key] = edit;
                    cfgGrid.Controls.Add(lbl);
                    cfgGrid.Controls.Add(f["help"].Str.Length > 0 ? (Control)Ui.Row(edit, Ui.L(f["help"].Str, Ui.Small, Ui.Dim)) : edit);
                }
                cfgGrid.ResumeLayout();
                cfgSave.Enabled = true;
            });
        }

        void SaveCfg()
        {
            var changes = new Dictionary<string, object>();
            foreach (var kv in cfgEdits)
            {
                var n = kv.Value as NumericUpDown;
                string v = n != null ? ((int)n.Value).ToString(CultureInfo.InvariantCulture) : kv.Value.Text.Trim();
                if (v != cfgNow[kv.Key]) changes[kv.Key] = n != null ? (object)(int)n.Value : v;
            }
            if (changes.Count == 0) { App.Say("Nothing changed.", Ui.Dim); return; }
            cfgSave.Enabled = false;
            Api.Post("/api/config", new Dictionary<string, object> { { "changes", changes } }, delegate (J r)
            {
                cfgSave.Enabled = true;
                if (r["ok"].Bool) { App.Say(r["msg"].Str, Ui.Good); cfgGrid.Tag = null; LoadCfg(); }
                else App.Say(r["error"].Str, Ui.Bad);
            });
        }
    }

    // ------------------------------------------------------------------ the window
    public sealed class MainForm : Form
    {
        public Api Api;
        public J Status = J.Null;
        readonly string host; readonly int port; readonly bool atSignIn; public string StartPage = "";
        public string Host { get { return host; } }
        readonly List<Page> pages = new List<Page>();
        readonly List<Button> nav = new List<Button>();
        Page current;
        Label pill, sub, statusLine;
        Panel content;
        System.Windows.Forms.Timer timer;
        int tick, pageAge, statusAge = 999, failures;
        bool statusBusy;
        DateTime startedAt = DateTime.Now;

        public MainForm(string host, int port, bool atSignIn)
        {
            this.host = host; this.port = port; this.atSignIn = atSignIn;
            Text = "Deck";
            BackColor = Ui.Bg; ForeColor = Ui.Text; Font = Ui.Body;
            StartPosition = FormStartPosition.CenterScreen;
            Size = new Size(Ui.S(1400), Ui.S(900));
            MinimumSize = new Size(Ui.S(760), Ui.S(520));
            WindowState = FormWindowState.Maximized;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            Api = new Api(host, port, this);
            // on the deck's own PC the app can run the deck without the Pi (standalone mode)
            bool demo = host == "127.0.0.1" || host == "localhost";
            if (LocalDeck.Available && !demo) Api.Local = new LocalDeck();

            var top = new Panel { Dock = DockStyle.Top, Height = Ui.S(58), BackColor = Ui.Bg, Padding = new Padding(Ui.S(16), Ui.S(8), Ui.S(16), 0) };
            var title = Ui.L("APACHE 4800 // DECK", new Font("Segoe UI Semibold", 15f));
            title.Location = new Point(Ui.S(16), Ui.S(8));
            sub = Ui.L("Connecting to the Pi (" + host + ")...", Ui.Small, Ui.Dim);
            sub.Location = new Point(Ui.S(18), Ui.S(36));
            pill = new QuietLabel { AutoSize = true, Font = Ui.Head, Padding = new Padding(Ui.S(10), Ui.S(5), Ui.S(10), Ui.S(5)), BackColor = Ui.Field, ForeColor = Ui.Dim, Text = "…", Margin = new Padding(0, Ui.S(7), Ui.S(12), 0) };
            var browser = Ui.B("Full panel ↗", delegate { OpenPanel(""); });
            // right-hand side: lays itself out, so the pill never covers the button whatever its text
            var right = new FlowLayoutPanel { Dock = DockStyle.Right, FlowDirection = FlowDirection.RightToLeft, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, WrapContents = false, BackColor = Ui.Bg };
            right.Controls.Add(browser); right.Controls.Add(pill);
            top.Controls.AddRange(new Control[] { right, title, sub });

            var side = new FlowLayoutPanel { Dock = DockStyle.Left, Width = Ui.S(170), FlowDirection = FlowDirection.TopDown, WrapContents = false, BackColor = Ui.Bg, Padding = new Padding(Ui.S(10), Ui.S(8), 0, 0) };
            content = new Panel { Dock = DockStyle.Fill, BackColor = Ui.Bg };
            statusLine = new QuietLabel { Dock = DockStyle.Bottom, Height = Ui.S(28), Padding = new Padding(Ui.S(16), Ui.S(5), 0, 0), BackColor = Ui.Card, ForeColor = Ui.Dim, Font = Ui.Body, AutoEllipsis = true };

            pages.Add(new OverviewPage(this)); pages.Add(new ConsolePage(this)); pages.Add(new PlayersPage(this));
            pages.Add(new ServerPage(this)); pages.Add(new ModpacksPage(this)); pages.Add(new BackupsPage(this)); pages.Add(new PiPage(this)); pages.Add(new DevToolsPage(this));
            foreach (var p in pages)
            {
                var page = p;
                var b = new Button
                {
                    Text = "  " + p.Title, TextAlign = ContentAlignment.MiddleLeft, FlatStyle = FlatStyle.Flat, Width = Ui.S(150), Height = Ui.S(38),
                    BackColor = Ui.Bg, ForeColor = Ui.Text, Font = Ui.Body, Margin = new Padding(0, 0, 0, Ui.S(4)), Cursor = Cursors.Hand, UseMnemonic = false
                };
                b.FlatAppearance.BorderSize = 0; b.FlatAppearance.MouseOverBackColor = Ui.Card;
                b.Click += delegate { ShowPage(page); };
                nav.Add(b); side.Controls.Add(b);
            }
            Controls.Add(content); Controls.Add(side); Controls.Add(statusLine); Controls.Add(top);

            // one 1-second heartbeat drives every refresh; it stops while minimised
            timer = new System.Windows.Forms.Timer { Interval = 1000 };
            timer.Tick += delegate { Beat(); };
            // (only once the window is up: maximising it while it opens must not start the refreshes early)
            Resize += delegate { if (!started) return; timer.Enabled = WindowState != FormWindowState.Minimized; if (timer.Enabled) { statusAge = 999; pageAge = 999; } };
            // drop a CurseForge export zip anywhere on the window to import it
            AllowDrop = true;
            DragEnter += delegate (object s, DragEventArgs e)
            {
                var f = e.Data.GetData(DataFormats.FileDrop) as string[];
                e.Effect = f != null && f.Length == 1 && f[0].EndsWith(".zip", StringComparison.OrdinalIgnoreCase) ? DragDropEffects.Copy : DragDropEffects.None;
            };
            DragDrop += delegate (object s, DragEventArgs e)
            {
                var f = e.Data.GetData(DataFormats.FileDrop) as string[];
                if (f != null && f.Length == 1) OpenZip(f[0]);
            };
            Load += delegate
            {
                Action go = delegate
                {
                    if (StartZip.Length > 0) OpenZip(StartZip);      // opened with a zip: straight to its import
                    else if (current == null) ShowPage(FindPage(StartPage) ?? pages[0]);
                    started = true; timer.Start(); Beat();
                };
                if (Api.Local == null) { go(); return; }
                // on the deck's PC: knock on the Pi first, so without it every page starts in standalone mode at once
                Api.ProbePi(delegate (bool found)
                {
                    if (!found)
                    {
                        Api.Standalone = true;
                        Say("No Pi - running standalone on this PC. Minecraft, console, players, server settings, backups and modpack switches all work; " +
                            "the app goes back to the Pi by itself when it's connected.", Ui.Accent);
                    }
                    go();
                });
            };
        }

        public string StartZip = "";
        Page FindPage(string key)
        {
            return pages.FirstOrDefault(x => x.Title.Replace("&", "").Replace(" ", "").ToLowerInvariant() == key);
        }

        // Deck.exe started again while this window is open: it hands its --page / zip over to us (Program.Main)
        protected override void WndProc(ref Message m)
        {
            if (m.Msg == Program.WM_COPYDATA)
            {
                var cd = (Program.CopyData)Marshal.PtrToStructure(m.LParam, typeof(Program.CopyData));
                if (cd.Tag == Program.HandOverTag && cd.Ptr != IntPtr.Zero && cd.Size > 0 && cd.Size < 4096)
                {
                    string s = Marshal.PtrToStringUni(cd.Ptr, cd.Size / 2).TrimEnd('\0');
                    BeginInvoke((Action)delegate { HandOver(s); });   // answer the sender at once, act afterwards
                    m.Result = (IntPtr)1; return;
                }
            }
            base.WndProc(ref m);
        }

        void HandOver(string s)
        {
            int bar = s.IndexOf('|'); if (bar < 0) return;
            string kind = s.Substring(0, bar), val = s.Substring(bar + 1);
            bool isZip = kind == "zip" && val.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) && File.Exists(val);
            if (!started) { if (isZip) StartZip = val; else if (kind == "page") StartPage = val; return; }   // still opening: use it when it does
            if (isZip) OpenZip(val);
            else if (kind == "page") { var p = FindPage(val); if (p != null) ShowPage(p); }
        }

        public void OpenZip(string path)
        {
            var mp = pages.OfType<ModpacksPage>().First();
            ShowPage(mp);
            mp.LoadZip(path);
        }

        public void ShowPage(Page p)
        {
            if (current == p) { pageAge = 999; return; }
            current = p;
            for (int i = 0; i < pages.Count; i++) { nav[i].BackColor = pages[i] == p ? Ui.Card : Ui.Bg; nav[i].ForeColor = pages[i] == p ? Ui.Accent : Ui.Text; }
            content.SuspendLayout();
            content.Controls.Clear();
            content.Controls.Add(p);
            content.ResumeLayout();
            p.Shown_();
            pageAge = 0;
        }

        void Beat()
        {
            tick++;
            // status (top bar + Overview): every 4 s while you're using the app, every 8 s while it just
            // sits on the screen behind another window, every 10 s while the Pi is unreachable
            bool active = Form.ActiveForm == this;
            if (++statusAge >= (failures > 0 ? 10 : active ? 4 : 8) && !statusBusy) { statusAge = 0; RefreshStatus(); }
            if (current != null && current.PollSeconds > 0 && failures == 0 && ++pageAge >= current.PollSeconds * (active ? 1 : 2))
            {
                pageAge = 0;
                current.Refresh_();
            }
            // standalone: keep looking for the Pi (every 15 s for the first 3 minutes, then every 30 s)
            if (Api.Standalone && !probing && ++probeAge >= ((DateTime.Now - startedAt).TotalMinutes < 3 ? 15 : 30))
            {
                probeAge = 0; probing = true;
                Api.ProbePi(delegate (bool found)
                {
                    probing = false;
                    if (!found || !Api.Standalone) return;
                    Api.Standalone = false;
                    Say("The Pi is back - everything goes through it again.", Ui.Good);
                    statusAge = 999; pageAge = 999;
                });
            }
        }
        int probeAge; bool probing, started;

        public void RefreshStatus()
        {
            statusBusy = true;
            Api.Call("GET", "/api/status", null, delegate (J r)
            {
                statusBusy = false;
                if (r.Has("state"))
                {
                    bool wasDown = failures > 0;
                    failures = 0; Status = r;
                    var s = r["state"];
                    sub.Text = r["standalone"].Bool
                        ? "Standalone - no Pi (" + host + "), this PC runs the deck itself · updated " + s["time"].Str
                        : "Pi " + host + " · updated " + s["time"].Str + (r["stale"].Bool ? " (watchdog not reporting!)" : "");
                    SetPill(r);
                    var ov = current as OverviewPage;
                    if (ov != null) ov.Show_(r);
                    if (wasDown && current != null) current.Refresh_();
                }
                else if (Api.Standalone)
                {
                    statusAge = 999;      // an answer to a question asked the Pi just before going standalone: ask again, locally
                }
                else if (Api.Local != null)
                {
                    // no Pi: run the deck from this PC, and keep looking for the Pi (Beat)
                    Api.Standalone = true; probeAge = 0;
                    Say("No Pi - running standalone on this PC. Minecraft, console, players, server settings, backups and modpack switches all work; " +
                        "the app goes back to the Pi by itself when it's connected.", Ui.Accent);
                    statusAge = 999;
                }
                else
                {
                    failures++;
                    bool patient = atSignIn && (DateTime.Now - startedAt).TotalMinutes < 3;
                    pill.Text = patient ? "Waiting for the Pi" : "Pi offline"; pill.ForeColor = patient ? Ui.Dim : Ui.Bad;
                    sub.Text = patient ? "The Pi is still starting (it boots slower than this PC)..."
                        : "Can't reach the Pi (" + host + "). Is it on, and its cable in the case switch? Retrying every 10 s.";
                }
            }, 6000);
        }

        void SetPill(J r)
        {
            var s = r["state"];
            string text = r["standalone"].Bool ? "Standalone (no Pi)" : "All good"; Color c = r["standalone"].Bool ? Ui.Accent : Ui.Good;
            if (r["stale"].Bool) { text = "Watchdog stopped"; c = Ui.Warn; }
            else if (!r["linked"].Bool) { text = "PC not linked"; c = Ui.Warn; }
            else if (s["temp_level"].Str == "hot" || s["temp_level"].Str == "crit") { text = "PC too hot"; c = Ui.Bad; }
            else if (s["temp_level"].Str == "warn") { text = "PC warm"; c = Ui.Warn; }
            else if (s["pi_level"].Str != "ok" && s["pi_level"].Str.Length > 0) { text = "Pi warm"; c = Ui.Warn; }
            else if (!r["action"].IsNull) { text = "Working…"; c = Ui.Accent; }
            pill.Text = text; pill.ForeColor = c;
        }

        public void Say(string text, Color c)
        {
            statusLine.Text = DateTime.Now.ToString("HH:mm") + "   " + text.Replace("\n", "  ");
            statusLine.ForeColor = c;
        }

        public void OpenPanel(string anchor)
        {
            if (Api.Standalone) { Say("The full panel is on the Pi, and it isn't connected.", Ui.Warn); return; }
            try { System.Diagnostics.Process.Start(Api.Base + "/?k12=1" + anchor); }
            catch (Exception e) { Say("Couldn't open the browser: " + e.Message, Ui.Bad); }
        }
    }

    static class Program
    {
        [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
        [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
        [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int cmd);
        [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
        [DllImport("user32.dll")] static extern IntPtr SendMessageTimeout(IntPtr h, int msg, IntPtr w, ref CopyData d, int flags, int ms, out IntPtr result);

        public const int WM_COPYDATA = 0x4A;
        public static readonly IntPtr HandOverTag = (IntPtr)0x4445434B;   // "DECK": only our own hand-overs are read
        [StructLayout(LayoutKind.Sequential)]
        public struct CopyData { public IntPtr Tag; public int Size; public IntPtr Ptr; }

        static void HandOver(IntPtr window, string msg)
        {
            var cd = new CopyData { Tag = HandOverTag, Size = (msg.Length + 1) * 2, Ptr = Marshal.StringToHGlobalUni(msg) };
            try { IntPtr r; SendMessageTimeout(window, WM_COPYDATA, IntPtr.Zero, ref cd, 2 /* abort if hung */, 3000, out r); }
            finally { Marshal.FreeHGlobal(cd.Ptr); }
        }

        [STAThread]
        static void Main(string[] args)
        {
            string host = "10.48.0.2"; int port = 8080; bool atSignIn = false; string page = "", zip = "";
            for (int i = 0; i < args.Length; i++)
            {
                string a = args[i].ToLowerInvariant();
                if ((a == "--pi" || a == "-pi") && i + 1 < args.Length) host = args[++i];
                else if ((a == "--port" || a == "-port") && i + 1 < args.Length) int.TryParse(args[++i], out port);
                else if (a == "--at-sign-in" || a == "-atsignin") atSignIn = true;
                else if (a == "--page" && i + 1 < args.Length) page = args[++i].ToLowerInvariant().Replace("&", "").Replace(" ", "");
                else if (args[i].EndsWith(".zip", StringComparison.OrdinalIgnoreCase) && File.Exists(args[i])) zip = Path.GetFullPath(args[i]);   // "Open with" Deck
            }
            bool fresh;
            using (var one = new Mutex(true, "Local\\CyberdeckDeckApp", out fresh))
            {
                if (!fresh)
                {
                    // already open: bring that window to the front instead of opening a second one,
                    // and pass it what this launch asked for (a page, or a zip opened with Deck)
                    string ask = zip.Length > 0 ? "zip|" + zip : page.Length > 0 ? "page|" + page : "";
                    foreach (var p in System.Diagnostics.Process.GetProcessesByName(System.Diagnostics.Process.GetCurrentProcess().ProcessName))
                        if (p.MainWindowHandle != IntPtr.Zero && p.Id != System.Diagnostics.Process.GetCurrentProcess().Id)
                        {
                            if (IsIconic(p.MainWindowHandle)) ShowWindow(p.MainWindowHandle, 9);   // restore only if minimised (keeps it maximised)
                            SetForegroundWindow(p.MainWindowHandle);
                            if (ask.Length > 0) HandOver(p.MainWindowHandle, ask);
                        }
                    return;
                }
                try { SetProcessDPIAware(); } catch { }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                float dpi;
                using (var g = Graphics.FromHwnd(IntPtr.Zero)) dpi = g.DpiX;
                Ui.Init(dpi);
                Application.Run(new MainForm(host, port, atSignIn) { StartPage = page, StartZip = zip });
            }
        }
    }
}
