# BlendLuxCore modernization review

Reviewed: 2026-10-14, branch `hip-backend` at commit `ca163c68` (v2.11.1).
Scope: the full addon source (241 Python files): render engine lifecycle, exporter
and caches, viewport and final draw code, handlers, node system, properties, UI
and the pyluxcore loader. The online library (LOL) was reviewed at the
architecture level only.

File paths are relative to the repository root; line numbers refer to the commit
above.

## Summary

The addon is structurally sound (strict import hierarchy, incremental caches,
numpy-based mesh and film transfer). The main problems are that the viewport
does far more work per redraw than it needs to, and that a few Blender
integration gaps have accumulated since the move to the extensions platform.

The three largest wins, in order:

1. **Stop re-exporting the config and camera on every viewport redraw**, and
   stop rebuilding the GPU texture on every draw. Pure addon changes.
2. **Fix the LuxCore-side leak that forces a full session restart** on viewport
   resize or any render-setting change, then restore incremental config updates
   in the addon.
3. **Honor `bpy.app.online_access` and stop depending on Blender's private
   wheel manager** in the loader. Both are requirements for staying on the
   extensions platform.

## 1. Viewport and export performance

### 1.1 Full config export on every redraw

`Exporter.get_viewport_changes()` (`export/__init__.py:371-385`) re-runs the
whole config export, including AOV definitions, plus the camera export, and
compares the resulting property strings. It is called from `view_draw()`
(`engine/viewport.py:208`) on every frame, even while the session is paused.

Fix: detect camera changes by comparing `region_data.view_matrix`,
`view_perspective`, `view_camera_zoom/offset` and the region size; only re-run
the config export when `depsgraph.updates` contains the Scene, or when the
viewport settings or addon preferences changed.

### 1.2 GPU texture rebuilt on every draw

`FrameBuffer.draw()` (`draw/viewport.py:228-237`) creates a new `GPUTexture`
every call, and `FrameBuffer.update()` (`draw/viewport.py:214-226`) allocates a
fresh numpy array and a fresh `gpu.types.Buffer` each time.

Fix: keep the numpy array and the texture as members; recreate the texture only
after a real film update. A paused viewport should then cost nothing per redraw.

### 1.3 Blocking calls on the UI thread

`engine.session.WaitNewFrame()` and `UpdateStats()` run inside `view_draw()`
(`engine/viewport.py:259-266` and `304-311`). With a GPU engine these stall the
interface. Cycles never blocks in draw: it shows the latest film and calls
`tag_redraw()`.

### 1.4 The viewport denoiser does not run in a thread

`FrameBuffer.start_denoiser()` (`draw/viewport.py:190-194`) calls
`Thread.run()` instead of `Thread.start()`, so OIDN runs synchronously and
blocks Blender. It cannot be a real thread anyway because `run_denoiser()`
draws, which needs the GPU context.

Fix: use `film.AsyncExecuteImagePipeline()` and poll
`HasDoneAsyncExecuteImagePipeline()` from `view_draw()`, exactly as
`draw/final.py:262-278` already does for the final render.

### 1.5 Session restart on resize or any setting change

`force_session_restart()` (`engine/viewport.py:50-62`) stops and recreates the
session, triggering a full scene re-export, whenever `Change.CONFIG` is set.
This includes every viewport resize. The reason is the LuxCore memory leak
tracked in BlendLuxCore issue #577; `Exporter._update_config()`
(`export/__init__.py:462-471`) is the incremental path and is disabled.

This is the single largest viewport responsiveness win and lives mostly on the
core side (see section 4).

### 1.6 O(scene) work per viewport update

`ObjectCache2.update()` (`export/caches/object_cache.py:797`) and
`VisibilityCache._get_visible_objects()` (`export/caches/__init__.py:84-105`)
walk all `depsgraph.object_instances` on every change. Moving one object in a
scene with many instances costs the whole instance list.

Fix: index exported objects by their parent object pointer, so a transform
update only touches instances whose object appears in `depsgraph.updates`.
Visibility changes do need a full pass, but they can be limited to updates
where `dg_update.is_updated_transform` or a visibility flag changed.

### 1.7 Compatibility converters run on every session

`Exporter.create_session()` (`export/__init__.py:116`) calls
`utils/compatibility.run()`, which makes over a dozen passes over every LuxCore
node tree in the file. This runs on every viewport start and every final render.

Fix: stamp converted trees with a version `IntProperty` and skip them; run the
converters in `load_post` and after link/append only.

### 1.8 Handlers that scan all materials

- `handlers/depsgraph_update_post.py` loops over every material on every
  depsgraph update, just to rename node trees. Filter on `depsgraph.updates`
  (`isinstance(update.id, bpy.types.Material)`) instead.
- `LuxCoreMaterialNodeTree.update()` (`nodes/materials/tree.py:40-56`) loops
  over every material to find the owner of the tree. `bpy.data.user_map(
  subset=[self])` returns it directly.

### 1.9 Mesh export expands every loop into a vertex

`export/mesh_converter.py:68-73` builds `loop_points = vertex_points[
loop_vertices]`, so every mesh is sent to LuxCore with loop-count vertices and
no index sharing. That is roughly four to six times more vertex memory than
needed for a typical mesh.

Fix: deduplicate (vertex index, normal, uv, color) tuples with `np.unique` on
a structured array, or share vertices directly when the mesh has at most one UV
layer with no seams.

### 1.10 Normals via `split_faces()`

`mesh.split_faces()` (`export/mesh_converter.py:205`) followed by
`mesh.vertices.normal` only approximates custom normals. Since Blender 4.1,
`mesh.corner_normals` gives exact per-loop normals, including custom and
sharp-edge normals, without modifying the temporary mesh.

Related: `export/hair.py:241-243` calls `obj.to_mesh(depsgraph=...)` on the
original object instead of `obj.evaluated_get(depsgraph).to_mesh()`, and reads
the deprecated `Mesh.vertex_colors` instead of `color_attributes`.

## 2. Blender integration

### 2.1 No `bpy.app.online_access` check

`blender_manifest.toml` declares the `network` permission, but nothing in the
addon reads `bpy.app.online_access`. The loader runs `pip download` at startup
and the online library downloads freely. The extensions platform requires
honoring this flag; a user who disabled online access will see unexpected
network traffic and the extension can be rejected on review.

Fix: check the flag in `luxloader.ensure_pyluxcore()` before any download,
fall back to the cached wheel, and surface a clear message in preferences. Gate
all LOL network calls the same way.

### 2.2 Loader depends on Blender internals and writes into itself

`luxloader/__init__.py:22` imports `_bpy_internal.extensions.wheel_manager`,
a private module with no stability guarantee. `_install_wheels()` and
`_update_manifest()` (`luxloader/__init__.py:300-370`) rewrite
`blender_manifest.toml` at runtime and create empty "fake" wheel files inside
the extension directory. This breaks on read-only installs (system packages,
shared network installs) and on any Blender refactor of the extension code.

The standard route is `blender --command extension build --split-platforms`
with the pyluxcore wheel bundled per platform. Keep the local-wheel override
from `blc_settings.json`, which is what locally built pyluxcore wheels (HIP
work included) need, but store its state under
`bpy.utils.extension_path_user()` rather than in the extension folder.

### 2.3 Legacy node add menu

`nodes/materials/__init__.py:88` (and the texture and volume trees) register
categories through `nodeitems_utils`. Blender's own node editor treats that
path as the deprecated fallback. Defining `NODE_MT_add` submenus with the
`bl_ui.node_add_menu` helpers gives the same menu plus integration with the
add-menu search, and removes the undocumented `NodeItemCustom` hacks in
`nodes/nodeitems.py`.

### 2.4 Viewport bypasses Blender's color management

`FrameBuffer._init_opengl()` (`draw/viewport.py:89`) draws with the built-in
`IMAGE` shader, so the scene's view transform and look (AgX, Filmic, exposure,
gamma) are never applied in the viewport, while the final Render Result does go
through them. Viewport and final render therefore differ unless the view
transform is Standard.

Fix: wrap the draw in `engine.bind_display_space_shader(scene)` /
`engine.unbind_display_space_shader()`, as Cycles does. If the LuxCore
tonemapper is meant to replace Blender's view transform entirely, that should
be documented and the final render should be made consistent instead.

### 2.5 Property-write hacks to force updates

Several places write a property back to itself to trigger a depsgraph update:
`mat.diffuse_color = mat.diffuse_color` in `nodes/materials/tree.py:53` and
`handlers/frame_change_pre.py:38`, and the `refresh` property in
`nodes/base.py:50-58`. These mark the file dirty, can confuse undo and run on
every node edit.

Fix: in `MaterialCache.diff()` (`export/caches/__init__.py:58-64`), also react
to `depsgraph.id_type_updated("NODETREE")` and map the changed node trees to
their materials with `bpy.data.user_map`. The same approach replaces the
image-sequence scan in `frame_change_pre`.

### 2.6 Pointer-based cache keys

`utils.make_key()` (`utils/__init__.py:59-69`) uses `as_pointer()`. Addresses
can be reused after undo or datablock deletion, which `export/image.py` already
works around. `ID.session_uid` is stable for the session, survives renames and
is never reused.

### 2.7 Version drift and dead code

- `blender_manifest.toml` requires 4.5, `__init__.py:25` and `CMakeLists.txt:25`
  check for 4.2, and `CLAUDE.md` says 4.2/4.3. Pick one.
- Checks for Blender 2.8x are dead: `export/smoke.py:46`,
  `nodes/volumes/heterogeneous.py:101`, `draw/lol/viewport.py:365`,
  `utils/lol/utils.py:604,666,692`.
- `bl_use_save_buffers` (`engine/base.py:37`) no longer exists on
  `RenderEngine`; it is a harmless but misleading attribute.
- `handlers/exit.py` reloads `pyluxcore` in its reload block; reloading a
  compiled extension module is a no-op.

### 2.8 Node editor header copy

`ui/node_editor.py` replaces `NODE_HT_header.draw` with a copy of Blender
2.92's header. Only the `luxcore_material_nodes` branch and the tail (pin,
parent, snap) are reachable because `lux_draw_switch()` delegates everything
else to the original. Trim the copy to those parts to shrink the drift surface
each Blender release.

### 2.9 Online library UI

`operators/lol/assetbar.py` (1537 lines) is a modal operator that draws its own
asset bar with the `gpu` module and raycasts for drop placement. It predates
the Asset Browser. Shipping the library as a Blender asset library with
catalogs (`.blend` files plus `blender_assets.cats.txt`) would remove most of
this code and give native previews, drag-and-drop and search. This is a larger
project and should be scoped separately.

## 3. Maintainability

### 3.1 Registration errors are swallowed

`utils.register_module()` (`utils/__init__.py:793-796`) catches every exception
from `bpy.utils.register_class()` and turns it into a warning in the LuxCore
error log. A broken class therefore leaves the addon half-registered with no
traceback in the console. Re-raise, or at least print the traceback.

### 3.2 AOV definitions live in four places

`LuxCoreRenderEngine.update_render_passes()` (`engine/base.py:125`),
`_add_passes()` (`engine/final.py:250`), the `AOVS` table in
`draw/final.py:19` and the properties in `properties/aovs.py` all describe the
same set of passes and must be kept in sync by hand. One declarative table
(name, channel count, channel ids, pass type, dtype, normalize) can drive all
four.

### 3.3 `bpy.context` on render paths

`export/config.py:41`, `export/__init__.py:293` and `engine/base.py:94` read
addon preferences through the global `bpy.context` during final render, which
the Blender API forbids inside `RenderEngine.render()`. Resolve the preferences
once at the start of a render and thread them through the exporter.

### 3.4 Logging, types and threading

- There are over 150 bare `print()` calls. A `logging` logger named
  `blendluxcore`, with its level tied to the existing "display LuxCore logs"
  preference, lets users silence or capture output.
- `Change` (`export/__init__.py`) is a natural `enum.IntFlag`;
  `ExportedObject`, `ExportedPart` and `Duplis` are dataclasses in waiting.
- `engine/viewport.py:114-116` uses `_thread.start_new_thread`; a daemon
  `threading.Thread` is the supported API and makes the session-start thread
  visible to debuggers.

### 3.5 No tests or lint

CI (`.github/workflows/build_bundle.yml`) only builds the zip. Suggested
additions:

- `ruff` with a small rule set (pyflakes, isort) to catch unused imports and
  undefined names.
- A headless smoke test, `blender -b --factory-startup --python
  tests/smoke.py`, that enables the extension, exports the default scene with
  a few materials, renders for a handful of samples and asserts that the
  Combined pass is non-black and that the error log is empty.

## 4. Items that depend on LuxCore core changes

| Addon item | What the core needs to provide |
| --- | --- |
| 1.5 Incremental config updates in the viewport | Fix the leak when a `RenderConfig` is re-parsed and a new `RenderSession` is created on the same scene (issue #577), so the addon can call `renderconfig.Parse()` and restart the session without re-exporting the scene. |
| 1.3 Non-blocking viewport draw | A non-blocking way to ask whether a new frame is ready (or a short timeout on `WaitNewFrame`). |
| 1.9 Indexed mesh export | Nothing new; `DefineMeshExt` already takes indexed triangles. Worth confirming it accepts shared vertices with per-loop UVs split only where needed. |
| 1.4 Async OIDN in the viewport | Nothing new; `AsyncExecuteImagePipeline` already exists. |
| 2.2 Bundled wheels | Per-platform pyluxcore wheels on PyPI for every supported Blender Python version (currently 3.11). |

## 5. Suggested order of work

1. Viewport redraw cost: 1.1, 1.2 and 1.4. Small, isolated changes with
   immediately visible results.
2. Extensions-platform compliance: 2.1 (online access), then 2.2 (loader).
3. Core fix for issue #577, then re-enable `_update_config()` (1.5).
4. Depsgraph-driven material and node tree detection, removing the property
   write hacks (2.5), together with the handler scans (1.8) and the
   compatibility stamping (1.7).
5. Mesh export: `corner_normals` and vertex deduplication (1.9, 1.10).
6. Color-managed viewport draw (2.4) and the `NODE_MT_add` menus (2.3).
7. Cleanup: AOV table (3.2), registration errors (3.1), logging (3.4), dead
   version checks (2.7), CI tests (3.5).
8. Asset Browser migration for the online library (2.9), scoped as its own
   project.
