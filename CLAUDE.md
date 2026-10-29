# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BlendLuxCore is a Blender extension/addon that integrates the LuxCoreRender engine into Blender. It is a pure-Python addon (the `pyluxcore` render engine itself is a separate compiled project, consumed here as a Python wheel). Requires Blender 4.5 or newer (the minimum in `blender_manifest.toml`).

## Build & Packaging

The extension zips are built by `scripts/package_bundle.py`, which runs Blender's own `extension build` command on a clean copy of the committed files (`git archive HEAD`, so commit first; `--worktree` packages the working tree, to test). It needs `blender` in the PATH, `--blender <path>`, or `BLENDER_ROOT` (the folder of the executable). Nothing in the repository is modified by a build.

```bash
# The platform zips, with pyluxcore inside (what the users install)
python scripts/package_bundle.py --wheels <folder with pyluxcore wheels> --platform windows-x64 --out dist
# The slim zip, without pyluxcore (downloaded at the first start)
python scripts/package_bundle.py --slim --out dist
```

`--tag Latest` names the zip `BlendLuxCore-Latest.zip` (the nightly builds). A zip is installed with `blender --command extension install-file -r user_default --enable <zip>`.

CI (`.github/workflows/build_bundle.yml`) builds the slim zip on Linux with a downloaded Blender; the platform zips are built by the LuxCore repository's `addon-bundle.yml` from its wheels. The headless tests are in `tests/` (see its README).

### Local development loop

For iterating without rebuilding the extension zip each time, use `debug-helpers/blendev/blendev` (Linux only):
- Copy `debug-helpers/blendev/blendev-config.default` to `blendev-config.cfg` and fill in local paths (git-ignored).
- `blendev` launches Blender with this repo loaded directly (via `BLC_DEV_PATH`) and optionally a locally-built `pyluxcore` wheel (via `-p=/path/to/wheel.whl`).
- Blender's "Reload Scripts" command (Ctrl+Alt+R... or via the menu) then reloads BlendLuxCore in-place — see "Reloadability" below, this only works if import rules are respected.

### pyluxcore loading

`pyluxcore` is NOT vendored; it's installed at runtime as a pip wheel by `luxloader/__init__.py` (`ensure_pyluxcore()`), called from the top-level `__init__.py` before any other BlendLuxCore submodule is imported. The target PyPI version is pinned in `luxloader/__init__.py` as `PYLUXCORE_VERSION` — bump this only after the corresponding `pyluxcore` release is actually on PyPI. Wheel source (PyPI vs local file/folder) is controlled by a JSON settings file typically written by the external "BlendLuxHelper" tool.

## Roadmap and bug list

[ROADMAP.md](ROADMAP.md) lists the open work on the addon; update its status table when an item is done. [BUGS.md](BUGS.md) lists the known addon bugs that are not fixed yet. Add an entry as soon as a problem is found, before moving on, even when it is unrelated to the current task or only suspected; do not add a problem that is fixed in the same task. When an entry is fixed, remove it and name it in the fix commit message. Bugs in the LuxCore core go in the core's own `BUGS.md`.

## Architecture

### Import hierarchy (critical — read `doc/mandatory.md`)

To support Blender's "Reload Scripts" (reloadability) and avoid circular imports, submodules form a strict dependency hierarchy. A module may only import from modules at a **lower** rank:

```
0. icons
1. utils
2. pyluxcore   (the external render engine module)
3. properties
4. export
5. nodes
6. operators
7. handlers
8. engine
9. ui
```

`utils` must not import any other BlendLuxCore submodule — if a submodule needs its own helpers, give it a local `<submodule>/utils.py` instead. `utils` is also imported before `pyluxcore` exists, so it must not call into `pyluxcore` either (pyluxcore-dependent helpers live in `utils/luxutils.py`).

Each top-level package (`properties`, `export`, `nodes`, `operators`, `handlers`, `engine`, `ui`) follows the same registration pattern in its `__init__.py`:
- Imports its child submodules, with a `_needs_reload` / `importlib.reload()` block for reloadability (this block must list child modules in dependency order).
- Defines a `classes` tuple (and optionally `submodules`) in registration order — order matters for `PropertyGroup`/`PointerProperty` dependencies.
- Exposes `register()` / `unregister()` calling `utils.register_module(...)` / `utils.unregister_module(...)`.

The root `__init__.py` registers top-level packages in the order: `properties, nodes, operators, handlers, engine, ui`.

### "lol" (LuxCore Online Library) feature

Several packages (`properties`, `operators`, `handlers`, `ui`, `utils`, `draw`) have a `lol/` sub-package implementing the in-Blender asset library browser ("LuxCore Online Library"). These are self-contained feature slices that plug into the same registration pattern as their parent package.

### Engine / RenderEngine lifecycle (`engine/`)

`engine/base.py` defines `LuxCoreRenderEngine(bpy.types.RenderEngine)`. Instances are **not persistent**:
- Viewport render: created when shading switches to RENDERED, destroyed on shading-mode change or frame change.
- Final render / material preview: created at render start, destroyed at render end (check `self.is_preview` to distinguish preview).

Session cleanup happens in `__del__()` (stops/deletes the LuxCore session).

### Export (`export/`)

`export/__init__.py` defines the `Exporter` class, which owns a set of caches (`export/caches/`) for incremental scene/session updates during viewport render, plus a `Change` bitmask describing what changed between updates (CONFIG, CAMERA, OBJECT, MATERIAL, VISIBILITY, WORLD, IMAGEPIPELINE, HALT) and grouping flags (`REQUIRES_SCENE_EDIT`, `REQUIRES_VIEW_UPDATE`, `REQUIRES_SESSION_PARSE`).

**Context convention**: throughout `export/` and related code, `context is not None` means viewport render, `context is None` means final render or material preview. Accessing `bpy.context` during final render is forbidden by the Blender API, so functions must thread `context` through explicitly rather than reaching for the global.

Node export is *not* centralized here — material/texture/volume node trees export themselves via methods on their node classes (in `nodes/`).

### Nodes (`nodes/`)

- `nodes/materials/`, `nodes/textures/`, `nodes/volumes/`, `nodes/shapes/` — node tree definitions, categories, and individual node types for each LuxCore node tree type.
- `nodes/base.py` — shared base classes (`LuxCoreNodeTree`, `LuxCoreNode`) and shared constants (noise items, etc.).
- `nodes/sockets.py` — custom socket types.
- Texture node trees can also be embedded inline inside material/volume trees, or referenced as standalone reusable trees via the Pointer node.

### Properties (`properties/`)

All custom (LuxCore-specific) Blender properties live here, grouped under a `luxcore` `PropertyGroup` attached to relevant datablock types, e.g. `bpy.types.Material.luxcore`, `bpy.types.World.luxcore`, `bpy.types.Camera.luxcore`, `bpy.types.Scene.luxcore`.

### UI (`ui/`)

Drawing code for custom panels, reading from `properties/`. `ui/__init__.py` lists which built-in Blender panels BlendLuxCore extends/replaces.

## Key Conventions

- **PEP 8**, double-quoted strings, CamelCase classes (except where Blender mandates specific operator class name formats), `snake_case` functions/methods, leading-underscore for "private" members, property descriptions never end with a period.
- **EnumProperty items must always include an explicit numeric index** (4th tuple element), so identifiers can be reordered/renamed later without breaking old `.blend` files.
- UI text follows Blender's own UI message and panel-naming conventions (see `doc/code_style.md` for links).
- **Property setting via pyluxcore**: setting a `pyluxcore.Property` for a key replaces ALL previously-set properties under that key (e.g. setting `scene.materials.test.kd` after `scene.materials.test.type` was set will drop the `type`). Always re-set every property of an object you're modifying, not just the changed one (see `doc/luxcore_api_tips.md`).
- **Editing a property without firing its `update` callback**: `utils.node.set_silently(self, "prop_name", value)` (bypasses the registered `update=` function). On a node, node tree or other ID in Blender 5, `self["prop_name"] = value` only creates a custom property and leaves the value unchanged; it still works on a `PropertyGroup` — see `doc/useful.md`.
- **UI feedback over hard failures**: if a setting will cause a render-time warning/error, surface it directly in the UI (e.g. a warning label) rather than only failing at render time.
- **Export robustness**: exporting should fail only on the gravest errors. Missing resources (images, HDRIs, etc.) should be replaced with placeholders/warning colors and logged via the error log ("N errors during export"), not abort the whole export. Fail fast only for critical, cheaply-detectable errors, before the expensive part of export runs.
- **Long export loops** should periodically call `engine.test_break()` (to support cancel) and `engine.update_stats()` / `update_progress()`.
- Hidden datablocks: a name starting with `.` hides a datablock from UI dropdowns by default (still searchable by typing `.`).
