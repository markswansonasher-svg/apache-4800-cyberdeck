"""
Adds (or updates) MCP servers in Claude's settings, keeping everything else as it was.

    python register_mcp.py servers.json

servers.json: {"name": {"command": ..., "args": [...], "env": {...}} or {"type": "http", "url": ...}}

Claude Code:  %USERPROFILE%\\.claude.json            (stdio and http servers)
Claude app:   %APPDATA%\\Claude\\claude_desktop_config.json  (stdio servers only; only if Claude is installed)

A copy of each settings file is kept next to it as *.before-deck-extras the first time.
"""
import json
import os
import shutil
import sys
from pathlib import Path


def update(path: Path, servers: dict, stdio_only: bool) -> list:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")   # -sig: files saved by Windows PowerShell start with a BOM
        backup = path.with_name(path.name + ".before-deck-extras")
        if not backup.exists():
            shutil.copy2(path, backup)
    else:
        data = {}
    mcp = data.setdefault("mcpServers", {})
    done = []
    for name, spec in servers.items():
        if stdio_only and spec.get("type") == "http":
            continue
        entry = dict(spec)
        if "command" in entry and not stdio_only:
            entry.setdefault("type", "stdio")
        mcp[name] = entry
        done.append(name)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)          # never leaves a half-written settings file
    return done


def main():
    servers = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    code = Path(os.path.expanduser("~")) / ".claude.json"
    print("Claude Code:", ", ".join(update(code, servers, False)) or "nothing")
    app_dir = Path(os.environ.get("APPDATA", "")) / "Claude"
    if app_dir.is_dir():
        print("Claude app :", ", ".join(update(app_dir / "claude_desktop_config.json", servers, True)) or "nothing")
    else:
        print("Claude app : not installed - skipped")


if __name__ == "__main__":
    main()
