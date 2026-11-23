# Game-dev extras for Claude (optional)

Nothing here is needed for the deck. It sets up the deck's mini PC as a game-dev machine that
[Claude](https://claude.ai) can work with through **MCP servers** (small programs that let Claude use a tool).

Run **`Install.cmd` → 5**, or `powershell -ExecutionPolicy Bypass -File dev-extras\install-dev-extras.ps1`.
Every part asks first and is safe to run again.

| Server | What Claude can do | Needs |
|---|---|---|
| `assets` | Search free asset sites, download into a shared library (`C:\Assets`), copy into Unity/Godot projects and write `ASSET_CREDITS.md` | uv. Optional free keys for Freesound, Poly Pizza, Sketchfab downloads |
| `gemini` | Ask Google Gemini for a second opinion, read files/videos, make images | uv + a free key from [AI Studio](https://aistudio.google.com) (images need billing) |
| `godot` | Run Godot projects and read their errors ([Coding-Solo/godot-mcp](https://github.com/Coding-Solo/godot-mcp)) | Git, Node.js, Godot 4 |
| `blender` | Drive Blender ([BlenderMCP](https://github.com/ahujasid/blender-mcp)) | uv + the BlenderMCP add-on installed in Blender |
| `UnityMCP` | Work inside the Unity editor ([Coplay MCP for Unity](https://github.com/CoplayDev/unity-mcp)) | the package in each project (below), Unity 2021.3+ |

The servers go in `%USERPROFILE%\mcp-servers`, and are registered in Claude Code's `~/.claude.json`
(and the Claude app's config when it's installed; the app can't use the HTTP Unity server).
A copy of each settings file is kept as `*.before-deck-extras` the first time.

## The asset library

* **Licence rule:** only CC0 and CC-BY (plus MIT-style code licences for Godot plugins, and Mixamo for
  animations). Non-commercial, no-changes, share-alike and GPL are refused, so nothing stops a game from being sold.
* **Sources Claude downloads from directly:** Kenney, Poly Haven, ambientCG, Godot Asset Library, Quaternius
  (its Google Drive often hits a download limit; Poly Pizza has many of the same models), Sketchfab*,
  Poly Pizza*, Freesound* (*free key: `mcp-servers\assets\set-asset-keys.ps1`).
* **Download in a browser, then ask Claude to file it** (`import_downloaded`): Mixamo, itch.io packs, OpenGameArt.
* **Unity Asset Store:** "Add to My Assets", then in Unity: Window › Package Manager › My Assets › Import.
* Unity needs the **glTFast** package for `.glb`/`.gltf` models; FBX works as-is.
* Freesound gives its high-quality preview (ogg/mp3), not the original upload.

## Unity: every project

Claude's side is set up once. Each Unity project needs Coplay's package:

1. Double-click `%USERPROFILE%\mcp-servers\unity\add-unity-mcp.cmd` and pick the project
   (or drag the project folder onto it). It adds one line to `Packages\manifest.json` and keeps a backup.
2. Open the project in Unity; it downloads the package.
3. **Window › MCP for Unity:** leave it on HTTP and press **Start**.

## Keys

Keys are saved as Windows user variables with hidden input, never in files or chat:
`set-gemini-key.ps1` (GEMINI_API_KEY) and `set-asset-keys.ps1` (FREESOUND_API_KEY, POLYPIZZA_API_KEY, SKETCHFAB_API_TOKEN).

## PC speed-ups

`C:\deck\tools\speed-up-pc.ps1` (asks for admin) makes Windows Defender skip folders tools rebuild themselves:
Unity `Library/Temp/Logs/obj`, Godot `.godot`, the Minecraft server data and Docker's disk. Your own files are
still scanned. Run it again after making new projects. `-MemoryIntegrity off|on` also switches Memory
integrity (a security trade-off: a few % faster games and editors, weaker protection against driver attacks).

Other things worth doing on a mini PC with integrated graphics: Windows power mode **Best performance** when
plugged in, and raising the graphics memory (**UMA frame buffer**) to 4 GB in the BIOS.
