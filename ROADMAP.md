# BlendLuxCore roadmap

The open work on the addon, from the modernization review below. Known bugs are
in [BUGS.md](BUGS.md). The work that needs core changes first is also tracked
in LuxCore's own `ROADMAP.md`.

Reviewed: 2026-10-14, branch `hip-backend` at commit `ca163c68` (v2.11.1).
Scope: the full addon source (241 Python files): render engine lifecycle, exporter
and caches, viewport and final draw code, handlers, node system, properties, UI
and the pyluxcore loader. The online library (LOL) was reviewed at the
architecture level only.

File paths are relative to the repository root; line numbers refer to the commit
above.

## Status (2026-10-22)

Measured with the headless tests in `tests/` and in a 1000x700 Blender 5.2
window (PATH CPU).

| Item | Status |
| --- | --- |
| 1.1 Config export on every redraw | Done, `5386391d`: the check runs when the view state changes, and every 0.5 s otherwise. A paused redraw takes 0.17 ms instead of 2.76 ms |
| 1.2 GPU texture on every draw | Done, `5386391d`: the texture is built on the first draw after a film update |
| 1.3 Blocking calls in `view_draw()` | Not needed: `WaitNewFrame()` only blocks on the first frame after a reset (RTPATHCPU) or just updates counters (RTPATHOCL). A draw during rendering costs about 38 ms, split between `GetOutputFloat()` (image pipeline, about 15 ms) and the 16-bit texture upload (about 9 ms; `RGBA32F` is faster to upload but the RGB to RGBA copy costs as much) |
| 1.4 Viewport denoiser on the UI thread | Done, `5bd9f1dc`: OIDN runs as an asynchronous image pipeline. Blender stayed frozen for 957 ms before; now the longest draw is 70 ms. A view change while OIDN runs still waits for it (about 0.9 s), because the core cannot cancel it |
| 1.5 Session restart on resize | Open (core issue #577) |
| 1.6 O(scene) object updates | Open |
| 1.7 Compatibility converters per session | Not worth it for now: 31 ms per session start with 1000 node trees |
| 1.8 Handlers scanning all materials | Not worth it for now: 0.9 ms per depsgraph update with 1000 materials. Filtering on `depsgraph.updates` would miss renames of unused materials, which produce no update entry |
| 1.9 One vertex per loop | Done, `3508f3cb`: loops with equal data share a vertex, and each submesh gets only its own vertices. 3.7x fewer vertices on a smooth subdivided mesh; export plus session start take the same time |
| 1.10 `split_faces()` normals | Done, `3508f3cb`: `Mesh.corner_normals`. Hair: `obj` is already the evaluated object there, and `Mesh.vertex_colors` is still what `mcol_on_emitter()` indexes, so no change |
| 1.11 Cycles node reader per object and slot | Done, `afcbf901`: read once per material and export, dropped when the material changes; `test_cycles_reader_cache` (20 objects, one read). Also fixed: Math nodes with an unlinked constant input read the constant as 0 (`_convert_to_float()`) |
| 1.12 Viewport responsiveness | Started 2026-10-28: steps 0 (the viewport film on the CPU) and 1 (Device Auto) done |
| 2.1 `bpy.app.online_access` | Done, `8b2e2d04` (PyPI download and every online library request) |
| 2.2 Loader internals | Open |
| 2.3 Legacy node add menu | Open |
| 2.4 Viewport color management | Not needed in Blender 5.2: the viewport follows the view transform, look and exposure (measured: Standard, AgX, Filmic and +1 exposure all give different pixels) |
| 2.5 Property-write hacks | Open |
| 2.6 Pointer cache keys | Done, `0058e2ef`: `make_key()` uses `ID.session_uid`; not yet tested in Blender |
| 2.7 Version drift, dead code | Done, `6af5ab61` and `bfa74776` |
| 2.8 Node editor header copy | Done, `4f993d90`: only the LuxCore material tree branch and the pin, parent and snap tail are left; not yet tested in Blender |
| 2.9 Online library on the Asset Browser | Open (separate project) |
| 2.10 Cycles and LuxCore node trees not interchangeable | Done, `dab4b215`: "Convert Cycles Nodes to LuxCore" renders like the reader within 2% in the mean (`test_cycles_convert`, OpenPBR and Disney paths); "Keep EEVEE Nodes in Sync" generates and updates EEVEE nodes from LuxCore trees, keeps hand-made ones (`test_eevee_sync`). Tried in a Blender 5.2 window with simulated input: panel and header buttons, dialogs, a slider drag during a LuxCore viewport render, Material Preview and EEVEE render of the generated nodes, the hand-edit protection, undo and redo of a conversion |
| 3.1 Swallowed registration errors | Done, `6af5ab61` |
| 3.2 AOV table in four places | Done, `8fc2fcb6`: the table in `properties/aovs.py` generates the properties, both pass lists and the film converters; the film output loop in `export/aovs.py` still matches outputs to properties by name. Not yet tested in Blender |
| 3.3 `bpy.context` on render paths | Left as is: `bpy.context.preferences` is the only way to read the addon preferences, and these are static settings |
| 3.4 Logging, types, threading | Thread done, `178855f1`; logging done, `d6437097` (not yet tested in Blender); `Change` as `IntFlag` and the dataclasses open |
| 3.5 Tests | Done, `a88c7242`: `tests/run.sh` (smoke, mesh export, Cycles lights, Cycles Mapping); not run in CI yet, which needs a pyluxcore wheel there |

Also found and fixed on the way, in the "Use Cycles settings" lights and the
Cycles node reader:

- Point, spot and sun lights were dropped from the export: `light.cycles.cast_shadow` no longer exists in Blender 4 and later (`99f0a383`).
- The light factors were wrong: spot lights were 12% too dark and area lights 19% too dark. All light types are now within 1.6% of Cycles (`f8cebc2f`).
- The Blackbody color node on lights is now supported (`99f0a383`).
- The Gamma node no longer crashes when it isn't fed by an image texture (`e40d079e`).
- The Mapping node in front of image textures was ignored; it is now used (`c4b6b031`).
- The light exporter no longer reads the `use_nodes` flag that Blender 5 ignores (`7c47965d`).

Added since the review:

- Viewport: a third Filter for Pixel Size > 1, Edge-Adaptive (sharp), the EASU pass of AMD FidelityFX Super Resolution 1 as a GPU shader (`draw/upscale.py`; LuxCore ROADMAP E4b). The default since 2026-10-26 (the user's decision); files that never set the Filter change from Nearest to it.

Found in the core (not changed here):

- LuxCore's image is one pixel row off Cycles'. The cameras flip the continuous film coordinate with `filmHeight - filmY - 1`, the integer formula. The path engines put pixel `j`'s center at `filmY = j + 0.5`, so the continuous flip is `filmHeight - filmY`. On the OpenCL side, the environment camera uses `filmHeight - filmY` while the CPU one uses `filmHeight - filmY - 1`.
- `pyluxcore.Version()` can report an old version: `LUXCORE_VERSION` is a CMake cache variable, so an existing build directory keeps it after a version bump.

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

### 1.11 Cycles node reader walks the tree per object and slot

Added 2026-10-22. `export/cycles_node_reader.py` has no node cache: a material
with "Use Cycles Nodes" was read again for every object and material slot using
it (`export/caches/object_cache.py:212`), with its helper textures and image
path checks. The reader result is now cached per material in the exporter
(`Exporter.cycles_material_cache`) and dropped when the material changes
(`MaterialCache.update()`).

### 1.12 Viewport responsiveness

Added 2026-10-28, from an exploration of the addon and the core (the core
side is LuxCore ROADMAP E6; the user's viewport runs on the GPU). The viewport
feels sluggish next to Cycles and next to the final render. The addon's part:

- `scene.luxcore.viewport.device` defaults to `CPU` (`properties/viewport.py:15`):
  RTPATHCPU on every hardware thread, which starves Blender's main thread, while
  a final render set to GPU uses PATHOCL.
- While rendering, `view_draw()` calls `tag_redraw()` every time and each draw
  costs ~38 ms on the main thread (1.3), even when no new frame arrived:
  `WaitNewFrame()` is a no-op for RTPATHOCL and `stats.renderengine.pass` moves
  once per 4 frames with `rtpath.resolutionreduction = 2`. `get_pretty_stats()`
  (`utils/render.py:152, 213`) calls `config.GetProperties()` twice per draw.
- Every view-state change runs the whole config export plus the camera export
  (`export/__init__.py:382-400`); `CameraCache.diff()` always re-exports.
- `Exporter.update()` (`export/__init__.py:457-484`) edits a paused session and
  resumes after, so the core's fast camera path runs on a parked thread (up to
  100 ms of polling plus one stale frame until E6 (a)).
- Any OBJECT update re-exports every light: `obj.type != "LIGHT"` in
  `ObjectCache2.update()` (`export/caches/object_cache.py:872`) sends every light
  through `convert_light()`, which deletes and redefines it (`export/light.py:24-27`).
- A resize or any setting change restarts the session and drops the `Exporter`,
  so every mesh and material is exported again (1.5, issue #577).

Measured 2026-10-28 (LuxCore ROADMAP E6 has the numbers): with a GPU
backend the addon exports `film.opencl.enable = 1` for the viewport too
(`export/config.py:88-92`), and a draw's `GetOutputFloat()` then waits for
the frame in flight: 43 ms of DanishMood's 53 ms `view_draw()` at 1600x1000,
111 ms against 14 ms with the CPU pipeline at 1920x1080.

Plan, in order:

0. *Done 2026-10-28.* The viewport film on the CPU: `film.opencl.enable = 0` in the viewport
   config only (the final render keeps the film device), until the core's
   GPU-resident viewport film (E6 Tier 3).
1. *Done 2026-10-28.* Device `AUTO` (new enum item, index 2, the default),
   resolved in `convert_viewport_engine()` by `LuxCoreViewportSettings.get_device()`;
   the UI shows the resolved device. Changed from the plan: Auto follows the
   final render's device (`config.device`) instead of any GPU in the device
   list, since the default OpenCL backend compiles its kernels for 15-30
   minutes and crashes in AMD's compiler on Windows (LuxCore BUGS.md): a user
   whose final render runs on the GPU has a working backend. Older files keep
   their stored CPU (0) or GPU (1); `tests/test_viewport_device.py`.
2. Draw only on a new frame: after `UpdateStats()` compare
   `stats.renderengine.total.samplecount` (then `stats.renderengine.film.generation`
   when E6 (c) lands) and call `FrameBuffer.update()` only when it changed, at
   most every ~30 ms; reuse `FrameBuffer._data` and its `gpu.types.Buffer`
   (`draw/viewport.py:239-253`); cache the engine and sampler strings of the
   stats once per session.
3. Camera-only change check: on a view-state change with the same region and
   pixel size run `camera_cache.diff()` alone; the config export only on a size
   change, a Scene depsgraph update or the 0.5 s periodic check;
   `CameraCache.diff()` compares the view-state tuple before `camera.convert()`.
4. `session.Resume()` before `BeginSceneEdit()` when paused.
5. Lights like meshes in `ObjectCache2.update()`: keep the exported transform
   and re-export a light only when its object is in `depsgraph.updates`.
6. Keep the `Exporter` and its `luxcore_scene` across a CONFIG restart: a new
   `RenderConfig(props, luxcore_scene)` and `RenderSession` only
   (`_update_config()` re-enabled), once E6 (f) shows the memory stays flat.
7. With E6 (c) and (d): drive redraws from a thread on `WaitNewFrame(50)` +
   `tag_redraw()`, so Blender draws exactly once per frame; the log handler must
   be thread-safe.

Measured before and after each step with `tests/benchmark/viewport_timing.py`
in the LuxCore repo (GUI Blender, from `lc-work/e4b/guitest.py`): per-draw
time, draws/s, time to the first frame, camera-move and resize latency, CPU and
GPU device, pixel size 1 and 2, Cycles on the Cornell pair for the A/B.

Related, LuxCore ROADMAP E7 (a DLSS-like viewport on AMD): FSR 1's RCAS pass
after the EASU upscale (`draw/upscale.py`), a camera-only temporal reprojection
of the denoised frame in a Blender compute shader (depth plus the previous and
current view matrices), and the per-frame GPU OIDN when the core has it (O1).

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

Planned as K6 in the LuxCore ROADMAP (2026-10-27): the platform zip becomes
the only install path and the loader's download, manifest rewrite and fake
wheels are deleted; the `blc_settings.json` override goes with them, replaced
by a developer command that bundles a locally built wheel.

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

### 2.10 Cycles and LuxCore node trees are not interchangeable

Added 2026-10-22. With Cycles one node tree serves Cycles and EEVEE. With
LuxCore, a material either renders its Cycles nodes through the reader (slower
to export, fewer features) or has a LuxCore tree that EEVEE and Material Preview
cannot show (gray). Two parts:

- "Convert Cycles Nodes to LuxCore" (material panel, node editor headers, and
  "Convert All Cycles Materials" in the render settings) builds a LuxCore tree
  that renders like the reader (`operators/cycles_converter.py`).
- "Keep EEVEE Nodes in Sync" generates an approximate Cycles/EEVEE tree from the
  LuxCore tree and updates it while the LuxCore tree is edited, never
  overwriting hand-made Cycles nodes (`utils/eevee_nodes.py`, `utils/eevee_sync.py`).

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
| 1.12 Viewport responsiveness | LuxCore E6: `stats.renderengine.film.generation` and `WaitNewFrame(timeoutMs)` (c), the GIL released in pyluxcore and the render-thread logs delivered (d), the #577 fix (f); E7: GPU OIDN (O1) and later a motion-vector AOV. |
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
