// ---------------------------------------------------------------------------
// Dev tools page: every development app and tool on this PC, with Open buttons,
// versions, whether it's running, whether Claude has its MCP server, and buttons
// for setup scripts (like adding MCP for Unity to a project).
//
// The page is made from devtools.json next to Deck.exe (copied from
// devtools.default.json the first time). Add a tool or a script there, press
// Reload - no rebuild needed. The format is explained at the top of that file.
//
// Cheap: nothing runs in the background except a "is it running?" look every
// 10 s (20 s behind other windows) while this page is on screen.
// ---------------------------------------------------------------------------
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Windows.Forms;

namespace Cyberdeck
{
    public sealed class DevToolsPage : Page
    {
        static readonly string AppDir = Path.GetDirectoryName(Application.ExecutablePath);
        static string ListPath { get { return Path.Combine(AppDir, "devtools.json"); } }
        static string DefaultPath { get { return Path.Combine(AppDir, "devtools.default.json"); } }

        FlowLayoutPanel cards;
        Label info;
        // "running" labels to refresh: label -> process names
        readonly List<KeyValuePair<Label, string[]>> running = new List<KeyValuePair<Label, string[]>>();

        public DevToolsPage(MainForm a) : base(a) { }
        public override string Title { get { return "Dev tools"; } }
        public override int PollSeconds { get { return 10; } }

        protected override void Build()
        {
            var top = Ui.Row(
                Ui.B("Reload", delegate { Load_(); App.Say("Dev tools list reloaded.", Ui.Good); }),
                Ui.B("Edit this list", delegate { Open("notepad.exe", "\"" + EnsureList() + "\""); }),
                Ui.B("Open the app folder", delegate { Open(AppDir, null); }));
            top.Dock = DockStyle.Top;
            info = Ui.L("", Ui.Small, Ui.Dim);
            info.Dock = DockStyle.Top;
            info.Margin = new Padding(0, 0, 0, Ui.S(6));
            cards = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, WrapContents = true, BackColor = Ui.Bg };
            // added in reverse: docked-top controls stack from the last one added
            Controls.Add(cards); Controls.Add(info); Controls.Add(top);
            Load_();
        }

        public override void Refresh_()
        {
            foreach (var kv in running)
            {
                bool on = kv.Value.Any(n => Process.GetProcessesByName(n).Length > 0);
                kv.Key.Text = on ? "● running" : "";
            }
        }

        // devtools.json, made from the default the first time
        static string EnsureList()
        {
            try { if (!File.Exists(ListPath) && File.Exists(DefaultPath)) File.Copy(DefaultPath, ListPath); } catch { }
            return File.Exists(ListPath) ? ListPath : DefaultPath;
        }

        void Load_()
        {
            cards.SuspendLayout();
            cards.Controls.Clear();
            running.Clear();
            J list;
            string path = EnsureList();
            try { list = new J(Api.Json.DeserializeObject(File.ReadAllText(path))); }
            catch (Exception e)
            {
                info.Text = "Couldn't read " + path + ": " + e.Message + "  (Edit this list, fix it, then Reload.)";
                info.ForeColor = Ui.Bad;
                cards.ResumeLayout();
                return;
            }
            var mcp = ClaudeMcpServers();
            info.ForeColor = Ui.Dim;
            info.Text = "From " + path + "   ·   Claude's MCP servers: " + (mcp.Count > 0 ? string.Join(", ", mcp) : "none registered");
            foreach (var g in list["groups"].List)
            {
                FlowLayoutPanel b;
                cards.Controls.Add(Ui.Card_(g["name"].Str, out b));
                var grid = new TableLayoutPanel { ColumnCount = 3, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, BackColor = Ui.Card };
                grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
                grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
                grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
                foreach (var it in g["items"].List)
                {
                    if (it.Has("glob"))
                    {
                        var found = Glob(Expand(it["glob"].Str));
                        if (found.Count == 0) AddRow(grid, it, null, it["name"].Str + " (none installed yet)", mcp);
                        foreach (var f in found) AddRow(grid, it, f, it["name"].Str + " " + StarPart(Expand(it["glob"].Str), f), mcp);
                    }
                    else if (it.Has("appid")) AddStoreRow(grid, it, mcp);
                    else AddRow(grid, it, it.Has("exe") ? Expand(it["exe"].Str) : null, it["name"].Str, mcp);
                }
                b.Controls.Add(grid);
            }
            cards.ResumeLayout();
            Refresh_();
        }

        void AddRow(TableLayoutPanel grid, J it, string exe, string name, HashSet<string> mcp)
        {
            // column 1: name + note
            var left = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, WrapContents = false, Margin = new Padding(0, Ui.S(4), Ui.S(14), Ui.S(4)), BackColor = Ui.Card };
            left.Controls.Add(Ui.L(name, Ui.Head, Ui.Text));
            if (it["note"].Str.Length > 0) { var n = Ui.L(Expand(it["note"].Str), Ui.Small, Ui.Dim); n.MaximumSize = new Size(Ui.S(330), 0); left.Controls.Add(n); }

            // column 2: version / installed, running, MCP, key
            var mid = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, WrapContents = false, Margin = new Padding(0, Ui.S(4), Ui.S(14), Ui.S(4)), BackColor = Ui.Card };
            bool have = exe != null && File.Exists(exe);
            if (exe != null)
                mid.Controls.Add(have ? Ui.L(Version(exe), Ui.Small, Ui.Text) : Ui.L("not found", Ui.Small, Ui.Warn));
            if (have && !it["cli"].Bool)
            {
                var run = Ui.L("", Ui.Small, Ui.Good);
                string proc = it["process"].Str.Length > 0 ? it["process"].Str : Path.GetFileNameWithoutExtension(exe);
                running.Add(new KeyValuePair<Label, string[]>(run, new[] { proc }));
                mid.Controls.Add(run);
            }
            if (it["mcp"].Str.Length > 0)
            {
                bool reg = mcp.Contains(it["mcp"].Str);
                mid.Controls.Add(Ui.L("MCP " + it["mcp"].Str + (reg ? ": Claude has it" : ": not set up"), Ui.Small, reg ? Ui.Accent : Ui.Warn));
            }
            // one or more keys (separated by ;) - shows saved / missing, never the value
            foreach (var k in it["envkey"].Str.Split(';').Select(x => x.Trim()).Where(x => x.Length > 0))
            {
                bool set = (Environment.GetEnvironmentVariable(k, EnvironmentVariableTarget.User) ?? "").Length > 0;
                mid.Controls.Add(Ui.L(k + (set ? ": saved" : ": missing"), Ui.Small, set ? Ui.Good : Ui.Warn));
            }

            // column 3: buttons
            var right = new FlowLayoutPanel { FlowDirection = FlowDirection.LeftToRight, AutoSize = true, WrapContents = true, MaximumSize = new Size(Ui.S(520), 0), Margin = new Padding(0, Ui.S(2), 0, Ui.S(2)), BackColor = Ui.Card };
            if (have && !it["cli"].Bool) { string e = exe; right.Controls.Add(Ui.B("Open", delegate { Open(e, null); }, Ui.Accent)); }
            if (have) { string d = Path.GetDirectoryName(exe); right.Controls.Add(Ui.B("Folder", delegate { Open(d, null); })); }
            foreach (var a in it["actions"].List)
            {
                J act = a;
                right.Controls.Add(Ui.B(act["label"].Str, delegate { Run(act); }));
            }
            grid.Controls.Add(left); grid.Controls.Add(mid); grid.Controls.Add(right);
        }

        // a Microsoft Store app: its folder changes with every update, so it's opened by its app ID
        // (find one with PowerShell:  Get-StartApps | Where-Object Name -like '*name*')
        void AddStoreRow(TableLayoutPanel grid, J it, HashSet<string> mcp)
        {
            string id = it["appid"].Str;
            var left = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, WrapContents = false, Margin = new Padding(0, Ui.S(4), Ui.S(14), Ui.S(4)), BackColor = Ui.Card };
            left.Controls.Add(Ui.L(it["name"].Str, Ui.Head, Ui.Text));
            if (it["note"].Str.Length > 0) { var n = Ui.L(Expand(it["note"].Str), Ui.Small, Ui.Dim); n.MaximumSize = new Size(Ui.S(330), 0); left.Controls.Add(n); }
            var mid = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, WrapContents = false, Margin = new Padding(0, Ui.S(4), Ui.S(14), Ui.S(4)), BackColor = Ui.Card };
            string pkg = id.Split('!')[0];
            bool have = Directory.Exists(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Packages", pkg));
            mid.Controls.Add(Ui.L(have ? "Microsoft Store app" : "not installed", Ui.Small, have ? Ui.Text : Ui.Warn));
            if (it["process"].Str.Length > 0)
            {
                var run = Ui.L("", Ui.Small, Ui.Good);
                running.Add(new KeyValuePair<Label, string[]>(run, new[] { it["process"].Str }));
                mid.Controls.Add(run);
            }
            var right = new FlowLayoutPanel { FlowDirection = FlowDirection.LeftToRight, AutoSize = true, WrapContents = true, MaximumSize = new Size(Ui.S(520), 0), Margin = new Padding(0, Ui.S(2), 0, Ui.S(2)), BackColor = Ui.Card };
            if (have) right.Controls.Add(Ui.B("Open", delegate { Open("explorer.exe", "shell:AppsFolder\\" + id); }, Ui.Accent));
            foreach (var a in it["actions"].List) { J act = a; right.Controls.Add(Ui.B(act["label"].Str, delegate { Run(act); })); }
            grid.Controls.Add(left); grid.Controls.Add(mid); grid.Controls.Add(right);
        }

        void Run(J act)
        {
            string args = Expand(act["args"].Str);
            if (act["ask_folder"].Bool)
            {
                using (var dlg = new FolderBrowserDialog { Description = act["label"].Str.TrimEnd('.') })
                {
                    if (dlg.ShowDialog(this) != DialogResult.OK) return;
                    args = (args + " \"" + dlg.SelectedPath + "\"").Trim();
                }
            }
            Open(Expand(act["run"].Str), args.Length > 0 ? args : null);
        }

        void Open(string what, string args)
        {
            try
            {
                var psi = new ProcessStartInfo(what) { UseShellExecute = true };
                if (args != null) psi.Arguments = args;
                if (File.Exists(what)) psi.WorkingDirectory = Path.GetDirectoryName(what);
                Process.Start(psi);
                App.Say("Opened " + (what.Length > 80 ? "..." + what.Substring(what.Length - 77) : what), Ui.Good);
            }
            catch (Exception e) { App.Say("Couldn't open " + what + ": " + e.Message, Ui.Bad); }
        }

        // ------------------------------------------------------------ helpers
        static string Expand(string s) { return Environment.ExpandEnvironmentVariables(s ?? ""); }

        static string Version(string exe)
        {
            try
            {
                var v = FileVersionInfo.GetVersionInfo(exe);
                string s = (v.ProductVersion ?? "").Trim();
                if (s.Length == 0 || s == "0.0.0.0") s = (v.FileVersion ?? "").Trim();
                if (s.Length == 0 || s == "0.0.0.0") return "installed";
                s = s.Split(' ')[0];
                return "version " + (s.Length > 24 ? s.Substring(0, 24) : s);
            }
            catch { return "installed"; }
        }

        // "C:\...\Editor\*\Editor\Unity.exe" -> every existing match (one * folder level)
        // several places can be given, separated by ;
        static List<string> Glob(string patterns)
        {
            var all = new List<string>();
            foreach (var p in patterns.Split(';').Select(x => x.Trim()).Where(x => x.Length > 0))
                foreach (var f in GlobOne(p)) if (!all.Contains(f, StringComparer.OrdinalIgnoreCase)) all.Add(f);
            return all;
        }

        static List<string> GlobOne(string pattern)
        {
            var found = new List<string>();
            int star = pattern.IndexOf('*');
            if (star < 0) { if (File.Exists(pattern)) found.Add(pattern); return found; }
            int cut = pattern.LastIndexOf('\\', star);
            int next = pattern.IndexOf('\\', star);
            string baseDir = pattern.Substring(0, cut), part = pattern.Substring(cut + 1, (next < 0 ? pattern.Length : next) - cut - 1);
            string rest = next < 0 ? "" : pattern.Substring(next);
            try
            {
                if (Directory.Exists(baseDir))
                    foreach (var d in Directory.GetDirectories(baseDir, part).OrderBy(x => x))
                        if (File.Exists(d + rest)) found.Add(d + rest);
            }
            catch { }
            return found;
        }

        // the folder that the * matched (a Unity editor's version, "app-26.22.0" -> "26.22.0", "ffmpeg-9.1-full_build" -> "9.1")
        static string StarPart(string patterns, string path)
        {
            foreach (var p in patterns.Split(';').Select(x => x.Trim()))
            {
                var pp = p.Split('\\'); var fp = path.Split('\\');
                int i = Array.FindIndex(pp, s => s.Contains("*"));
                if (i < 0 || pp.Length != fp.Length) continue;
                string pre = pp[i].Substring(0, pp[i].IndexOf('*')), post = pp[i].Substring(pp[i].IndexOf('*') + 1);
                string part = fp[i];
                if (!part.StartsWith(pre, StringComparison.OrdinalIgnoreCase) || !part.EndsWith(post, StringComparison.OrdinalIgnoreCase)) continue;
                part = part.Substring(pre.Length, part.Length - pre.Length - post.Length);
                var v = System.Text.RegularExpressions.Regex.Match(part, @"\d+(\.\d+)+[a-z0-9]*");
                return v.Success ? v.Value : part;
            }
            return "";
        }

        // MCP servers Claude Code knows (user settings, ~/.claude.json)
        static HashSet<string> ClaudeMcpServers()
        {
            var set = new HashSet<string>();
            try
            {
                string p = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".claude.json");
                var j = new J(Api.Json.DeserializeObject(File.ReadAllText(p)));
                foreach (var k in j["mcpServers"].Keys) set.Add(k);
            }
            catch { }
            return set;
        }
    }
}
