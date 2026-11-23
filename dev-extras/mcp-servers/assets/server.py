# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2"]   # mcp 2.x renamed FastMCP
# ///
"""
Free assets for Claude - search free asset sites, download into one shared library,
copy into Unity / Godot projects, and keep the licence credits written as you go.

Run by Claude (registered in ~/.claude.json and Claude's desktop config) as:
    uv run %USERPROFILE%\\mcp-servers\\assets\\server.py

Licence rule: only CC0 and CC-BY (plus permissive code licences like MIT for Godot
plugins, and Mixamo for animations) are allowed. NC (non-commercial), ND (no changes),
SA (share-alike) and GPL are refused, so nothing stops a game from being sold later.

Library: C:\\Assets (or the ASSET_LIBRARY variable). Each asset gets its own folder with
_asset.json (where it came from, author, licence) and LICENCE.txt.

Keys (Windows user variables, saved with set-asset-keys.ps1; read fresh each call so no
restart is needed): FREESOUND_API_KEY, POLYPIZZA_API_KEY, SKETCHFAB_API_TOKEN.
Poly Haven, ambientCG, Kenney, Godot Asset Library and Sketchfab search need no key.
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("assets")
LIB = Path(os.environ.get("ASSET_LIBRARY") or r"C:\Assets")
UA = "asset-fetcher/1.0 (personal game dev library)"
SOURCES = ["polyhaven", "ambientcg", "kenney", "quaternius", "sketchfab", "polypizza", "freesound", "godot"]
KINDS = ["any", "model", "texture", "hdri", "sound", "pack", "godot_plugin"]
_cache: dict = {}          # search results remembered by (source, id), so download needs no re-search


# ---------------------------------------------------------------- helpers
def key(name: str) -> str:
    """A key from the environment, or straight from the Windows user variables (no restart needed)."""
    v = os.environ.get(name, "")
    if not v and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                v = winreg.QueryValueEx(k, name)[0]
        except OSError:
            v = ""
    return v.strip()


def http(url: str, headers: dict | None = None, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def get_json(url: str, headers: dict | None = None):
    with http(url, headers) as r:
        return json.loads(r.read().decode("utf-8"))


def get_text(url: str, headers: dict | None = None) -> str:
    with http(url, headers) as r:
        return r.read().decode("utf-8", "replace")


def fetch(url: str, dest: Path, headers: dict | None = None) -> Path:
    """Download to dest (via a .part file, so a broken download never looks finished)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with http(url, headers, timeout=300) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)
    part.replace(dest)
    return dest


def safe(name: str) -> str:
    s = re.sub(r"[^\w\- .]+", "", name).strip().replace(" ", "_")
    return (s or "asset")[:80]


def licence(text: str) -> tuple[bool, str]:
    """(allowed, short name). CC0 / CC-BY / permissive code licences / Mixamo pass; NC, ND, SA, GPL don't."""
    t = (text or "").lower()
    if re.search(r"\bnc\b|non-?commercial", t):
        return False, "NC (non-commercial)"
    if re.search(r"\bnd\b|no-?deriv", t):
        return False, "ND (no changes allowed)"
    if re.search(r"\bsa\b|share-?alike", t):
        return False, "SA (share-alike)"
    if re.search(r"\b(a|l)?gpl", t):
        return False, "GPL"
    if re.search(r"cc0|publicdomain/zero|public domain", t):
        return True, "CC0"
    if re.search(r"attribution|/licenses/by/|cc[- ]?by\b|^by$", t):
        return True, "CC-BY"
    for code in ("mit", "bsd", "apache", "zlib", "unlicense", "boost", "isc"):
        if re.search(rf"\b{code}", t):
            return True, text.strip()
    if "mixamo" in t:
        return True, "Mixamo (free for games; don't share the raw files)"
    return False, text.strip() or "unknown"


def hit(source, id, name, kind, author, lic, page, preview="", **extra) -> dict:
    ok, short = licence(lic)
    d = {"source": source, "id": str(id), "name": name, "kind": kind, "author": author,
         "licence": short, "page": page, "preview": preview, **{k: v for k, v in extra.items() if v not in (None, "", [])}}
    if ok:
        _cache[(source, str(id))] = d
    return d if ok else {}


def words(q: str) -> list[str]:
    return [w for w in re.split(r"\W+", q.lower()) if w]


def unzip(z: Path, into: Path) -> None:
    with zipfile.ZipFile(z) as zf:
        for m in zf.infolist():
            target = (into / m.filename).resolve()
            if not str(target).startswith(str(into.resolve())):   # no ../ tricks
                continue
            zf.extract(m, into)
    z.unlink()


def finish(meta: dict, folder: Path) -> dict:
    """Write _asset.json + LICENCE.txt + library index; return a summary."""
    meta = {**meta, "downloaded": dt.date.today().isoformat(), "path": str(folder)}
    (folder / "_asset.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (folder / "LICENCE.txt").write_text(
        f"{meta['name']}\nby {meta['author']}\nfrom {meta['page']}\nlicence: {meta['licence']}\n"
        + ("\nCC-BY: credit the author in your game (e.g. the credits screen).\n" if "BY" in meta["licence"] else ""),
        encoding="utf-8")
    idx = LIB / "library.json"
    items = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else []
    items = [i for i in items if i.get("path") != meta["path"]] + [meta]
    idx.write_text(json.dumps(items, indent=2), encoding="utf-8")
    files = [p for p in folder.rglob("*") if p.is_file() and p.name not in ("_asset.json", "LICENCE.txt")]
    size = sum(p.stat().st_size for p in files)
    return {"saved_to": str(folder), "files": len(files), "size_mb": round(size / 1e6, 1),
            "licence": meta["licence"], "author": meta["author"],
            "examples": [str(p.relative_to(folder)) for p in files[:12]]}


# ---------------------------------------------------------------- Poly Haven (CC0, no key)
PH_TYPES = {"model": "models", "texture": "textures", "hdri": "hdris"}


def ph_search(q, kind, limit):
    out = []
    for k, t in PH_TYPES.items():
        if kind not in ("any", k):
            continue
        ck = ("ph-list", t)
        if ck not in _cache or time.time() - _cache[ck][0] > 3600:
            _cache[ck] = (time.time(), get_json(f"https://api.polyhaven.com/assets?t={t}"))
        ws = words(q)
        for aid, a in _cache[ck][1].items():
            hay = " ".join([aid, a.get("name", "")] + a.get("tags", []) + a.get("categories", [])).lower()
            if all(w in hay for w in ws):
                out.append(hit("polyhaven", aid, a.get("name", aid), k, ", ".join(a.get("authors", {})), "CC0",
                               f"https://polyhaven.com/a/{aid}", a.get("thumbnail_url", ""),
                               downloads=a.get("download_count")))
    out.sort(key=lambda d: -(d.get("downloads") or 0))
    return out[:limit]


def ph_download(aid, resolution, fmt, folder: Path, meta):
    files = get_json(f"https://api.polyhaven.com/files/{aid}")
    res = resolution.lower()
    kind = {0: "hdri", 1: "texture", 2: "model"}.get(get_json(f"https://api.polyhaven.com/info/{aid}").get("type"), "texture")
    if kind == "texture":                     # textures also list a gltf/blend preview; we want the maps
        files = {k: v for k, v in files.items() if k not in ("gltf", "fbx", "blend", "usd", "mtlx")}
    if "hdri" in files:                                                    # HDRI
        opts = files["hdri"].get(res) or next(iter(files["hdri"].values()))
        f = opts.get(fmt if fmt in opts else "hdr") or next(iter(opts.values()))
        fetch(f["url"], folder / Path(urllib.parse.urlparse(f["url"]).path).name)
    elif fmt in ("gltf", "fbx", "blend") and fmt in files or ("gltf" in files and fmt == "auto"):   # model
        fmt = "gltf" if fmt == "auto" else fmt
        opts = files[fmt].get(res) or next(iter(files[fmt].values()))
        main = opts[fmt]
        fetch(main["url"], folder / Path(urllib.parse.urlparse(main["url"]).path).name)
        for rel, inc in main.get("include", {}).items():
            fetch(inc["url"], folder / rel)
    else:                                                                  # texture maps
        for name, byres in files.items():
            if name in ("blend", "gltf", "mtlx", "usd", "fbx") or not isinstance(byres, dict):
                continue
            opts = byres.get(res) or next(iter(byres.values()))
            f = opts.get("jpg") or opts.get("png") or next(iter(opts.values()))
            if isinstance(f, dict) and "url" in f:
                fetch(f["url"], folder / Path(urllib.parse.urlparse(f["url"]).path).name)


# ---------------------------------------------------------------- ambientCG (CC0, no key)
ACG_TYPES = {"texture": "Material", "hdri": "HDRI", "model": "3DModel"}


def acg_search(q, kind, limit):
    types = ",".join(v for k, v in ACG_TYPES.items() if kind in ("any", k))
    if not types:
        return []
    r = get_json("https://ambientcg.com/api/v2/full_json?" + urllib.parse.urlencode(
        {"q": q, "type": types, "limit": limit, "sort": "Popular", "include": "displayData,imageData"}))
    kmap = {v: k for k, v in ACG_TYPES.items()}
    return [hit("ambientcg", a["assetId"], a.get("displayName") or a["assetId"], kmap.get(a.get("dataType"), "texture"),
                "ambientCG", "CC0", f"https://ambientcg.com/view?id={a['assetId']}",
                (a.get("previewImage") or {}).get("256-PNG", "")) for a in r.get("foundAssets", [])]


def acg_download(aid, resolution, fmt, folder: Path, meta):
    r = get_json("https://ambientcg.com/api/v2/full_json?" + urllib.parse.urlencode({"id": aid, "include": "downloadData"}))
    a = r["foundAssets"][0]
    dls = [d for f in a["downloadFolders"].values() for c in f["downloadFiletypeCategories"].values() for d in c["downloads"]]
    want = f"{resolution.upper()}-{'PNG' if fmt == 'png' else 'JPG'}"
    d = next((d for d in dls if d.get("attribute") == want), None) or next((d for d in dls if resolution.upper() in d.get("attribute", "")), dls[0])
    z = fetch(d["downloadLink"], folder / d["fileName"])
    if z.suffix.lower() == ".zip":
        unzip(z, folder)


# ---------------------------------------------------------------- Kenney (CC0, no key)
def kenney_search(q, kind, limit):
    if kind not in ("any", "pack", "model", "sound", "texture"):
        return []
    h = get_text("https://kenney.nl/assets?" + urllib.parse.urlencode({"search": q}))
    slugs = []
    for s in re.findall(r"kenney\.nl/assets/([a-z0-9-]+)", h):
        if s not in ("category", "tag", "series") and s not in slugs:
            slugs.append(s)
    return [hit("kenney", s, s.replace("-", " ").title(), "pack", "Kenney", "CC0",
                f"https://kenney.nl/assets/{s}") for s in slugs[:limit]]


def kenney_download(slug, resolution, fmt, folder: Path, meta):
    import http.cookiejar
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", UA)]
    page = f"https://kenney.nl/assets/{slug}"
    h = op.open(page, timeout=60).read().decode("utf-8", "replace")
    m = re.search(r"data-download-id='([0-9a-f]+)'", h)
    if not m:
        raise RuntimeError("Kenney's page has no download button for this pack.")
    link = json.loads(op.open(urllib.request.Request("https://kenney.nl/download-link/" + m.group(1),
                                                      headers={"Referer": page}), timeout=60).read())["url"]
    z = folder / f"kenney_{slug}.zip"
    z.parent.mkdir(parents=True, exist_ok=True)
    with op.open(urllib.request.Request(link, headers={"Referer": page}), timeout=300) as r, open(z, "wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)
    unzip(z, folder)


# ---------------------------------------------------------------- Quaternius (CC0 packs on public Google Drive folders)
def _drive_ls(folder_id):
    import html as _h
    h = get_text("https://drive.google.com/embeddedfolderview?id=" + folder_id, {"User-Agent": "Mozilla/5.0"})
    out = []
    for m in re.finditer(r'<a href="([^"]+)"[^>]*>.*?flip-entry-title">([^<]+)<', h, re.S):
        url, name = m.group(1), _h.unescape(m.group(2))
        f = re.search(r"/folders/([\w-]+)", url)
        d = re.search(r"/file/d/([\w-]+)", url)
        out.append((name, "folder" if f else "file", (f or d).group(1) if (f or d) else ""))
    return out


def q_search(q, kind, limit):
    if kind not in ("any", "pack", "model"):
        return []
    ck = ("q-list",)
    if ck not in _cache or time.time() - _cache[ck][0] > 86400:
        _cache[ck] = (time.time(), sorted(set(re.findall(r"packs/([a-z0-9_-]+)\.html", get_text("https://quaternius.com/")))))
    ws = words(q)
    hits = [s for s in _cache[ck][1] if all(w in s for w in ws)] or [s for s in _cache[ck][1] if any(w in s for w in ws)]
    return [hit("quaternius", s, s, "pack", "Quaternius", "CC0", f"https://quaternius.com/packs/{s}.html",
                f"https://quaternius.com/assets/images/fullres/{s}.jpg") for s in hits[:limit]]


def q_download(slug, resolution, fmt, folder: Path, meta):
    page = get_text(f"https://quaternius.com/packs/{slug}.html")
    m = re.search(r"drive\.google\.com/drive/folders/([\w-]+)", page)
    if not m:
        raise RuntimeError("No free download folder on this pack's page (it may be Patreon/itch only).")
    want = {"auto": "fbx", "glb": "gltf"}.get(fmt, fmt)        # FBX by default: works in Unity, Godot and Blender

    def walk(fid, rel: Path, depth):
        for name, typ, id_ in _drive_ls(fid):
            if typ == "folder":
                # format folders (FBX / glTF / OBJ / Blends): only the one asked for, unless format=all
                is_fmt = re.fullmatch(r"(?i)fbx|gltf|glb|obj|blends?|blender|unity|godot|unreal", name.strip())
                if depth < 5 and (want == "all" or not is_fmt or want in name.lower()):
                    walk(id_, rel / safe(name), depth + 1)
            elif id_:
                dest = folder / rel / name
                with http(f"https://drive.usercontent.google.com/download?id={id_}&export=download&confirm=t",
                          {"User-Agent": "Mozilla/5.0"}, timeout=300) as r:
                    if "text/html" in (r.headers.get("Content-Type") or "") and not name.lower().endswith((".html", ".htm")):
                        body = r.read(4000).decode("utf-8", "replace")
                        if "Quota exceeded" in body or "quota" in body.lower():
                            raise RuntimeError("Google Drive's download limit for Quaternius's files is used up right now. "
                                               "Try later, find the same models on Poly Pizza (search with sources=['polypizza']), "
                                               f"or download in the browser ({meta.get('page')}) and use import_downloaded.")
                        raise RuntimeError(f"Drive sent a web page instead of {name}.")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with open(dest, "wb") as f:
                        shutil.copyfileobj(r, f, 1024 * 1024)
    walk(m.group(1), Path("."), 0)
    for z in list(folder.rglob("*.zip")):
        unzip(z, z.parent)


# ---------------------------------------------------------------- Sketchfab (search free; download needs token)
def sf_search(q, kind, limit):
    if kind not in ("any", "model"):
        return []
    out = []
    for lic in ("cc0", "by"):
        r = get_json("https://api.sketchfab.com/v3/search?" + urllib.parse.urlencode(
            {"type": "models", "q": q, "downloadable": "true", "license": lic, "count": limit}))
        for m in r.get("results", []):
            thumbs = sorted((m.get("thumbnails") or {}).get("images", []), key=lambda i: i.get("width", 0))
            out.append(hit("sketchfab", m["uid"], m.get("name", ""), "model", (m.get("user") or {}).get("username", ""),
                           (m.get("license") or {}).get("label", lic), m.get("viewerUrl", ""),
                           next((i["url"] for i in thumbs if i.get("width", 0) >= 200), ""),
                           animated=bool(m.get("animationCount")), faces=m.get("faceCount"), likes=m.get("likeCount")))
    # interleave CC0 and CC-BY results, keeping Sketchfab's relevance order
    cc0 = [o for o in out if o and o["licence"] == "CC0"]
    by = [o for o in out if o and o["licence"] != "CC0"]
    mixed = [x for pair in zip(by, cc0) for x in pair] + by[len(cc0):] + cc0[len(by):]
    return mixed[:limit]


def sf_download(uid, resolution, fmt, folder: Path, meta):
    tok = key("SKETCHFAB_API_TOKEN")
    if not tok:
        raise RuntimeError("Sketchfab downloads need your free API token - run set-asset-keys.ps1 (Dev tools page).")
    info = get_json(f"https://api.sketchfab.com/v3/models/{uid}")
    ok, short = licence((info.get("license") or {}).get("label", ""))
    if not ok:
        raise RuntimeError(f"Refused: this model's licence is {short}.")
    links = get_json(f"https://api.sketchfab.com/v3/models/{uid}/download", {"Authorization": f"Token {tok}"})
    pick = links.get("glb") if fmt in ("auto", "glb") and links.get("glb") else links.get("gltf") or links.get("glb")
    if not pick:
        raise RuntimeError(f"No glTF/GLB download offered (got: {', '.join(links)}).")
    url = pick["url"]
    name = Path(urllib.parse.urlparse(url).path).name or f"{uid}.zip"
    f = fetch(url, folder / name)
    if f.suffix.lower() == ".zip":
        unzip(f, folder)


# ---------------------------------------------------------------- Poly Pizza (key)
def pp_search(q, kind, limit):
    tok = key("POLYPIZZA_API_KEY")
    if kind not in ("any", "model") or not tok:
        return []
    r = get_json(f"https://api.poly.pizza/v1.1/search/{urllib.parse.quote(q)}?Limit={min(limit * 2, 32)}", {"x-auth-token": tok})
    out = []
    for m in r.get("results", []):
        creator = m.get("Creator") or {}
        out.append(hit("polypizza", m.get("ID") or m.get("Id"), m.get("Title", ""), "model",
                       creator.get("Username", "") if isinstance(creator, dict) else str(creator),
                       m.get("Licence") or m.get("License") or "", f"https://poly.pizza/m/{m.get('ID') or m.get('Id')}",
                       m.get("Thumbnail", ""), animated=m.get("Animated"), download=m.get("Download")))
    return [o for o in out if o][:limit]


def pp_download(mid, resolution, fmt, folder: Path, meta):
    url = meta.get("download")
    if not url:
        tok = key("POLYPIZZA_API_KEY")
        m = get_json(f"https://api.poly.pizza/v1.1/model/{mid}", {"x-auth-token": tok})
        ok, short = licence(m.get("Licence") or m.get("License") or "")
        if not ok:
            raise RuntimeError(f"Refused: licence is {short}.")
        url = m.get("Download")
    fetch(url, folder / (safe(meta.get("name") or mid) + ".glb"))


# ---------------------------------------------------------------- Freesound (key)
def fs_search(q, kind, limit):
    tok = key("FREESOUND_API_KEY")
    if kind not in ("any", "sound") or not tok:
        return []
    r = get_json("https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode(
        {"query": q, "page_size": min(limit * 3, 150), "sort": "score",
         "fields": "id,name,username,license,previews,url,duration,avg_rating"}), {"Authorization": f"Token {tok}"})
    out = [hit("freesound", s["id"], s["name"], "sound", s.get("username", ""), s.get("license", ""), s.get("url", ""),
               (s.get("previews") or {}).get("preview-hq-mp3", ""), seconds=round(s.get("duration") or 0, 1),
               previews=s.get("previews")) for s in r.get("results", [])]
    return [o for o in out if o][:limit]


def fs_download(sid, resolution, fmt, folder: Path, meta):
    pv = meta.get("previews")
    if not pv:
        s = get_json(f"https://freesound.org/apiv2/sounds/{sid}/?" + urllib.parse.urlencode(
            {"fields": "name,username,license,previews,url"}), {"Authorization": f"Token {key('FREESOUND_API_KEY')}"})
        ok, short = licence(s.get("license", ""))
        if not ok:
            raise RuntimeError(f"Refused: licence is {short}.")
        pv = s["previews"]
    url = pv.get("preview-hq-mp3") if fmt == "mp3" else pv.get("preview-hq-ogg") or pv.get("preview-hq-mp3")
    fetch(url, folder / (safe(meta.get("name") or str(sid)) + Path(urllib.parse.urlparse(url).path).suffix))


# ---------------------------------------------------------------- Godot Asset Library (no key)
def gd_search(q, kind, limit):
    if kind not in ("any", "godot_plugin"):
        return []
    r = get_json("https://godotengine.org/asset-library/api/asset?" + urllib.parse.urlencode(
        {"filter": q, "max_results": limit * 2, "sort": "rating",
         "godot_version": os.environ.get("GODOT_VERSION", "4.7")}))   # the library returns nothing without a version
    return [o for o in (hit("godot", a["asset_id"], a["title"], "godot_plugin", a.get("author", ""), a.get("cost", ""),
                            f"https://godotengine.org/asset-library/asset/{a['asset_id']}", a.get("icon_url", ""),
                            godot_version=a.get("godot_version"), category=a.get("category"))
                        for a in r.get("result", [])) if o][:limit]


def gd_download(aid, resolution, fmt, folder: Path, meta):
    a = get_json(f"https://godotengine.org/asset-library/api/asset/{aid}")
    ok, short = licence(a.get("cost", ""))
    if not ok:
        raise RuntimeError(f"Refused: licence is {short}.")
    z = fetch(a["download_url"], folder / "download.zip")
    unzip(z, folder)


SEARCH = {"polyhaven": ph_search, "ambientcg": acg_search, "kenney": kenney_search, "quaternius": q_search, "sketchfab": sf_search,
          "polypizza": pp_search, "freesound": fs_search, "godot": gd_search}
DOWNLOAD = {"polyhaven": ph_download, "ambientcg": acg_download, "kenney": kenney_download, "quaternius": q_download, "sketchfab": sf_download,
            "polypizza": pp_download, "freesound": fs_download, "godot": gd_download}
NEEDS_KEY = {"polypizza": "POLYPIZZA_API_KEY", "freesound": "FREESOUND_API_KEY"}


# ---------------------------------------------------------------- tools
@mcp.tool()
def search_assets(query: str, kind: str = "any", sources: list[str] | None = None, limit: int = 6) -> dict:
    """Search free asset sites at once. Only CC0 / CC-BY (and permissive code licences) are returned.

    kind: any, model, texture, hdri, sound, pack (Kenney packs), godot_plugin.
    sources: any of polyhaven, ambientcg, kenney, quaternius, sketchfab, polypizza, freesound, godot (default: all).
    Quaternius models (many animated) are also on polypizza.
    Each result has source + id for download_asset, plus author, licence, page and a preview image/sound.
    """
    if kind not in KINDS:
        return {"error": f"kind must be one of {KINDS}"}
    srcs = [s for s in (sources or SOURCES) if s in SEARCH]
    skipped = {s: f"needs {NEEDS_KEY[s]} (run set-asset-keys.ps1)" for s in srcs if s in NEEDS_KEY and not key(NEEDS_KEY[s])}
    results, errors = {}, {}
    with cf.ThreadPoolExecutor(len(srcs) or 1) as ex:
        futs = {ex.submit(SEARCH[s], query, kind, max(1, min(limit, 25))): s for s in srcs if s not in skipped}
        for f in cf.as_completed(futs):
            s = futs[f]
            try:
                r = [x for x in f.result() if x]
                if r:
                    results[s] = r
            except Exception as e:      # one site down shouldn't sink the search
                errors[s] = f"{type(e).__name__}: {e}"[:200]
    for r in results.values():
        for x in r:
            x.pop("previews", None); x.pop("download", None)
    return {"results": results, "skipped": skipped, "errors": errors,
            "licence_rule": "CC0 and CC-BY only (NC/ND/SA/GPL filtered out)"}


@mcp.tool()
def download_asset(source: str, id: str, resolution: str = "1k", format: str = "auto") -> dict:
    """Download one asset into the shared library (C:\\Assets\\<source>\\<name>).

    resolution: for Poly Haven / ambientCG textures, models and HDRIs (1k, 2k, 4k...).
    format: auto, or gltf / fbx / blend (Poly Haven models), glb / gltf (Sketchfab),
            fbx (default) / gltf / obj / blend / all (Quaternius packs),
            jpg / png (textures), hdr / exr (HDRIs), ogg / mp3 (Freesound).
    Zips are unpacked. Licence is checked again before downloading.
    Freesound gives its high-quality preview (ogg/mp3); original files need a separate sign-in.
    """
    if source not in DOWNLOAD:
        return {"error": f"source must be one of {SOURCES}"}
    meta = dict(_cache.get((source, str(id))) or {})
    if not meta:     # not from a search in this session: fill in what we can
        try:
            r = SEARCH[source](str(id), "any", 25)
            meta = next((x for x in r if x and x["id"] == str(id)), {})
        except Exception:
            meta = {}
    meta.setdefault("source", source); meta.setdefault("id", str(id)); meta.setdefault("name", str(id))
    if source in ("polyhaven", "ambientcg", "kenney", "quaternius"):
        meta.setdefault("licence", "CC0")
        meta.setdefault("author", {"ambientcg": "ambientCG", "kenney": "Kenney", "quaternius": "Quaternius"}.get(source, ""))
        meta.setdefault("page", {"polyhaven": f"https://polyhaven.com/a/{id}", "ambientcg": f"https://ambientcg.com/view?id={id}",
                                 "kenney": f"https://kenney.nl/assets/{id}",
                                 "quaternius": f"https://quaternius.com/packs/{id}.html"}[source])
    if source in NEEDS_KEY and not key(NEEDS_KEY[source]):
        return {"error": f"{source} needs {NEEDS_KEY[source]} - run set-asset-keys.ps1 (Dev tools page)."}
    folder = LIB / source / safe(meta["name"] if meta["name"] != str(id) else str(id))
    if folder.exists() and (folder / "_asset.json").exists():
        return {"already_in_library": str(folder)}
    folder.mkdir(parents=True, exist_ok=True)
    try:
        DOWNLOAD[source](str(id), resolution, format.lower(), folder, meta)
    except Exception as e:
        shutil.rmtree(folder, ignore_errors=True)      # no half-downloaded assets left behind
        return {"error": f"{type(e).__name__}: {e}"[:400]}
    meta.setdefault("author", ""); meta.setdefault("page", ""); meta.setdefault("licence", "")
    keep = {k: meta[k] for k in ("source", "id", "name", "kind", "author", "licence", "page") if k in meta}
    return finish(keep, folder)


@mcp.tool()
def import_downloaded(file_path: str, source: str, name: str, page_url: str, author: str, licence_name: str) -> dict:
    """Add something downloaded by hand (browser) to the library with its licence:
    Quaternius / itch.io packs, Mixamo animations, OpenGameArt files, etc.
    The licence must be CC0, CC-BY, a permissive code licence or Mixamo. Zips are unpacked."""
    ok, short = licence(licence_name)
    if not ok:
        return {"error": f"Refused: licence is {short}. Only CC0 / CC-BY (or MIT-style code, or Mixamo) are allowed."}
    src = Path(file_path)
    if not src.exists():
        return {"error": f"Not found: {file_path}"}
    folder = LIB / safe(source.lower()) / safe(name)
    folder.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, folder, dirs_exist_ok=True)
    elif src.suffix.lower() == ".zip":
        unzip(shutil.copy2(src, folder / src.name) and folder / src.name, folder)
    else:
        shutil.copy2(src, folder / src.name)
    return finish({"source": source.lower(), "id": safe(name), "name": name, "kind": "manual", "author": author,
                   "licence": short, "page": page_url}, folder)


@mcp.tool()
def library(query: str = "") -> dict:
    """What's in the shared library (optionally filtered by words in name/source/author/kind)."""
    idx = LIB / "library.json"
    items = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else []
    ws = words(query)
    items = [i for i in items if all(w in json.dumps(i).lower() for w in ws) and Path(i["path"]).exists()]
    return {"library": str(LIB), "count": len(items),
            "items": [{k: i.get(k) for k in ("name", "source", "kind", "author", "licence", "path")} for i in items]}


CREDITS_HEAD = """# Asset credits

Third-party assets in this project, added by Claude's asset library.
CC0 needs no credit (but it's nice). **CC-BY items must be credited in the game** (credits screen or store page).

| Asset | Author | Licence | Source | In project |
|---|---|---|---|---|
"""


@mcp.tool()
def add_to_project(asset_path: str, project_dir: str, subfolder: str = "") -> dict:
    """Copy a library asset into a Unity or Godot project and record it in ASSET_CREDITS.md.

    Unity: goes to Assets/ThirdParty/<source>/<name>. Godot: assets/third_party/<source>/<name>,
    except Godot plugins, whose addons/ folder is merged into the project's addons/.
    subfolder overrides the destination (relative to the project folder).
    """
    a = Path(asset_path)
    if not (a / "_asset.json").exists():
        hits = [i for i in library(asset_path)["items"]]
        if len(hits) == 1:
            a = Path(hits[0]["path"])
        else:
            return {"error": "Give the library folder of the asset (see library()).", "matches": hits[:10]}
    meta = json.loads((a / "_asset.json").read_text(encoding="utf-8"))
    p = Path(project_dir)
    unity, godot = (p / "ProjectSettings").is_dir(), (p / "project.godot").exists()
    if not (unity or godot) and not subfolder:
        return {"error": "That folder isn't a Unity or Godot project; pass subfolder to copy anyway."}
    notes = []
    if godot and meta.get("kind") == "godot_plugin" and not subfolder:
        addons = next((d for d in a.rglob("addons") if d.is_dir()), None)
        if not addons:
            return {"error": "This plugin has no addons/ folder; pass subfolder to copy it somewhere."}
        dest = p / "addons"
        shutil.copytree(addons, dest, dirs_exist_ok=True)
        notes.append("Enable it in Project > Project Settings > Plugins.")
    else:
        rel = subfolder or (f"Assets/ThirdParty/{meta['source']}/{a.name}" if unity else f"assets/third_party/{meta['source']}/{a.name}")
        dest = p / rel
        shutil.copytree(a, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("_asset.json", "*.part"))
        if unity and any(dest.rglob("*.gl[tb]*")):
            notes.append("glTF/GLB models need Unity's glTFast package (com.unity.cloud.gltfast); FBX files work as-is.")
        if unity and any(dest.rglob("*.blend")):
            notes.append(".blend files need Blender installed for Unity to import them (it is).")
    credits = p / "ASSET_CREDITS.md"
    text = credits.read_text(encoding="utf-8") if credits.exists() else CREDITS_HEAD
    row = (f"| [{meta['name']}]({meta['page']}) | {meta.get('author') or '-'} | {meta['licence']} | {meta['source']} | "
           f"{dest.relative_to(p).as_posix()} |\n")
    if meta["page"] and f"({meta['page']})" in text:
        notes.append("Already in ASSET_CREDITS.md.")
    else:
        credits.write_text(text + row, encoding="utf-8")
    if "BY" in meta["licence"]:
        notes.append(f"CC-BY: credit {meta.get('author') or 'the author'} in the game.")
    return {"copied_to": str(dest), "credits_file": str(credits), "notes": notes}


@mcp.tool()
def asset_sources() -> dict:
    """Which sources are ready (keys set), the licence rule, and the library folder."""
    keys = {"freesound": "FREESOUND_API_KEY", "polypizza": "POLYPIZZA_API_KEY", "sketchfab (downloads)": "SKETCHFAB_API_TOKEN"}
    return {"library": str(LIB),
            "licence_rule": "CC0, CC-BY, permissive code licences (MIT/BSD/Apache/Zlib), Mixamo. Refused: NC, ND, SA, GPL.",
            "no_key_needed": ["polyhaven", "ambientcg", "kenney", "quaternius (packs; Google Drive may hit its limit)", "godot", "sketchfab (search)"],
            "keys": {s: ("set" if key(k) else f"missing - set {k} with set-asset-keys.ps1") for s, k in keys.items()},
            "by_hand_then_import_downloaded": ["Quaternius when Google Drive is over its limit", "itch.io free packs",
                                               "Mixamo (sign in with Adobe)", "OpenGameArt (check each licence)"],
            "unity_asset_store": "Add to My Assets on the store, then Unity: Window > Package Manager > My Assets > Import."}


if __name__ == "__main__":
    LIB.mkdir(parents=True, exist_ok=True)
    mcp.run()
