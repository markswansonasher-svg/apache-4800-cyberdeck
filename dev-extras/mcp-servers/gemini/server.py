# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "google-genai>=1.0"]   # mcp 2.x renamed FastMCP
# ///
"""
Gemini for Claude - a small MCP server so Claude can ask Google's Gemini for a second
opinion, have it read files or YouTube videos, and make or edit images.

Run by Claude (registered in ~/.claude.json and Claude's desktop config) as:
    uv run %USERPROFILE%\\mcp-servers\\gemini\\server.py

The API key is read from the GEMINI_API_KEY environment variable (your Windows user
variables - save it with set-gemini-key.ps1). It is never printed or sent anywhere
except to Google's Gemini API.

Models: GEMINI_MODEL / GEMINI_IMAGE_MODEL pick them; otherwise the newest suitable
model your key can use is chosen automatically (gemini_models lists them).
"""
import mimetypes
import os
import re
import time
from pathlib import Path

from google import genai
from google.genai import types
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gemini")
MAX_FILE = 20 * 1024 * 1024                     # inline files: up to 20 MB each
IMAGE_DIR = Path(os.environ.get("GEMINI_IMAGE_DIR", Path.home() / "Pictures" / "Gemini"))
_client = None
_models = {"t": 0, "list": []}


def client():
    global _client
    if _client is None:
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("No GEMINI_API_KEY yet. Run set-gemini-key.ps1 (in the same folder as this server), "
                               "then restart Claude so it sees the key.")
        # 90 s per request: an overloaded model then fails over to the next one instead of hanging
        _client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=90_000))
    return _client


def models():
    """Models this key can use, cached for 10 minutes: [(name, actions)]."""
    if time.time() - _models["t"] > 600 or not _models["list"]:
        out = []
        for m in client().models.list():
            name = (m.name or "").removeprefix("models/")
            out.append((name, list(getattr(m, "supported_actions", None) or [])))
        _models.update(t=time.time(), list=out)
    return _models["list"]


def _version(name):
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
    return float(m.group(1)) if m else 0.0


def pick(kind):
    """The newest stable Gemini model for 'text' or 'image' (unless set by environment variable)."""
    return candidates(kind)[0]


def candidates(kind):
    """Models to try for 'text' or 'image', best first (GEMINI_MODEL / GEMINI_IMAGE_MODEL go first)."""
    env = os.environ.get("GEMINI_IMAGE_MODEL" if kind == "image" else "GEMINI_MODEL", "").strip()
    names = [n for n, acts in models() if n.startswith("gemini-") and "generateContent" in acts
             and not re.search(r"preview|exp|tts|live|audio|embedding|thinking|lite", n)]
    if kind == "image":
        cands = [n for n in names if "image" in n]
        if not cands:     # image models are often previews: allow those
            cands = [n for n, acts in models() if n.startswith("gemini-") and "image" in n and "generateContent" in acts]
    else:
        cands = [n for n in names if "image" not in n]
        flash = [n for n in cands if "flash" in n]
        cands = flash or cands      # flash: fast and the most free use
    if not cands and not env:
        raise RuntimeError(f"Your key has no {kind} model available - see gemini_models.")
    ordered = sorted(cands, key=lambda n: (_version(n), -len(n)), reverse=True)
    return ([env] if env else []) + [n for n in ordered if n != env]


# Google says "try another one" with these: overloaded, retired for new keys, timed out
RETRY = re.compile(r"503|UNAVAILABLE|high demand|404|NOT_FOUND|no longer available|timed? ?out|deadline", re.I)


def generate(kind, model, contents, config=None, tries=4):
    """generate_content on the given model, or on the best models in turn while they're busy / retired."""
    order = [model] if model else candidates(kind)[:tries]
    last = None
    for m in order:
        try:
            return m, client().models.generate_content(model=m, contents=contents, config=config)
        except Exception as e:
            last = e
            if model or not RETRY.search(str(e)):
                raise
    raise last


def file_part(path):
    p = Path(path).expanduser()
    if not p.is_file():
        raise RuntimeError(f"No such file: {p}")
    if p.stat().st_size > MAX_FILE:
        raise RuntimeError(f"{p.name} is over 20 MB.")
    mime = mimetypes.guess_type(p.name)[0] or "text/plain"
    if mime.startswith("text/") or p.suffix.lower() in (".py", ".cs", ".ps1", ".json", ".md", ".scad", ".yml", ".yaml",
                                                         ".toml", ".java", ".js", ".ts", ".sh", ".cmd", ".gradle", ".txt"):
        return types.Part.from_text(text=f"--- file: {p.name} ---\n" + p.read_text(encoding="utf-8", errors="replace"))
    return types.Part.from_bytes(data=p.read_bytes(), mime_type=mime)


def friendly(e):
    s = str(e)
    if "API_KEY_INVALID" in s or "API key not valid" in s:
        return "Google refused the API key - make a new one at aistudio.google.com and run set-gemini-key.ps1 again."
    if ("RESOURCE_EXHAUSTED" in s or "429" in s) and "billing" in s.lower():
        return ("This isn't included in Gemini's free tier for your key (Google says: check your plan and billing). "
                "Image generation needs billing turned on for your project at aistudio.google.com - it's pay per image.")
    if "RESOURCE_EXHAUSTED" in s or "429" in s:
        return "Gemini's free limit is used up for now (it resets daily) - try again later or with a smaller model."
    if RETRY.search(s):
        return "Gemini's models are busy right now (Google: high demand) - try again in a few minutes."
    if "PERMISSION_DENIED" in s or "not available" in s.lower():
        return "This model isn't available on your key or plan: " + s[:300]
    return s[:500]


@mcp.tool()
def ask_gemini(prompt: str, files: list[str] | None = None, youtube_url: str | None = None,
               model: str | None = None) -> str:
    """Ask Google Gemini - for a second opinion, or to read things Claude can't (a YouTube video, a very long file).
    files: local paths (code, text, images, PDFs; up to 20 MB each) sent along with the prompt.
    youtube_url: a public YouTube video for Gemini to watch.
    model: a model name from gemini_models (optional; the newest Flash model is used otherwise).
    Returns Gemini's answer and which model gave it."""
    try:
        parts = [file_part(f) for f in files or []]
        if youtube_url:
            if not re.match(r"^https://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w-]{6,}", youtube_url):
                return "That doesn't look like a YouTube video link."
            parts.append(types.Part(file_data=types.FileData(file_uri=youtube_url)))
        parts.append(types.Part.from_text(text=prompt))
        m, r = generate("text", model, parts)
        return f"[{m}]\n{r.text or '(no text in the answer)'}"
    except Exception as e:
        return "Gemini error: " + friendly(e)


@mcp.tool()
def gemini_image(prompt: str, save_as: str | None = None, edit_images: list[str] | None = None,
                 model: str | None = None) -> str:
    """Make an image with Gemini (or edit existing ones: pass their paths in edit_images) and save it as a PNG.
    save_as: where to save (a .png path); default is your Pictures\\Gemini folder.
    Returns the saved file path(s) and any text Gemini added."""
    try:
        parts = [file_part(f) for f in edit_images or []] + [types.Part.from_text(text=prompt)]
        m, r = generate("image", model, parts, types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]))
        saved, text = [], []
        for c in r.candidates or []:
            for part in (c.content.parts if c.content else []) or []:
                if part.inline_data and part.inline_data.data:
                    if save_as and not saved:
                        out = Path(save_as).expanduser()
                    else:
                        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:40].strip("-") or "image"
                        out = IMAGE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}-{len(saved) + 1}.png"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(part.inline_data.data)
                    saved.append(str(out))
                elif part.text:
                    text.append(part.text)
        if not saved:
            return f"[{m}] Gemini didn't return an image." + (" It said: " + " ".join(text)[:500] if text else "")
        return f"[{m}] Saved: " + ", ".join(saved) + (("\n" + " ".join(text)[:500]) if text else "")
    except Exception as e:
        return "Gemini error: " + friendly(e)


@mcp.tool()
def gemini_models() -> str:
    """List the Gemini models this API key can use, and which ones the tools pick by default."""
    try:
        names = sorted(n for n, acts in models() if n.startswith("gemini-") and "generateContent" in acts)
        try:
            dt = pick("text")
        except Exception:
            dt = "(none)"
        try:
            di = pick("image")
        except Exception:
            di = "(none)"
        return f"Default for questions: {dt}\nDefault for images: {di}\nAvailable:\n" + "\n".join(names)
    except Exception as e:
        return "Gemini error: " + friendly(e)


if __name__ == "__main__":
    mcp.run()
