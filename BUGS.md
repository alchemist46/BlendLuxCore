# Known bugs

Addon bugs and issues found during a task that were not fixed right away. Bugs in the LuxCore core go in LuxCore's own `BUGS.md`. Remove an entry when it is fixed, and name it in the fix commit message. The open work items are in [ROADMAP.md](ROADMAP.md).

## Entry format

```
### <short title>
- Found: YYYY-MM-DD
- Area: viewport | export | nodes | properties | ui | loader | online library | build | tests | ...
- Problem: what happens, and what was expected
- Reproduce: Blender version, scene, engine, steps
- Notes: cause, suspects, what was ruled out
```

## Open

### A Pixel Size change does not reach a running viewport (suspected)
- Found: 2026-10-26
- Area: viewport
- Problem: setting `scene.luxcore.viewport.pixel_size` while the viewport renders leaves the viewport at the old pixel size: `view_draw()` reads it from `depsgraph.scene_eval`, and the evaluated scene keeps the old value until some other scene property changes.
- Reproduce: Blender 5.2.2, `scenes/cornell/Cornell Box Luxcore.blend` (LuxCore repo), viewport in Rendered mode; set the Pixel Size from Python (`scene.luxcore.viewport.pixel_size = "2"`) or with `wm.context_set_enum`: `bpy.context.evaluated_depsgraph_get().scene_eval.luxcore.viewport.pixel_size` stays "1", the viewport neither restarts nor changes its film size, even after 20 s of redraws. Setting an RNA property of the scene afterwards (`render.resolution_percentage`) brings the new value over, as does `scene.update_tag()`. The same holds for `halt_time`.
- Notes: a click on the Pixel Size field in the panel was not simulated, so it is not known whether the UI path has the same problem (both go through `RNA_property_update()`). The Filter setting has it too and is read from `context.scene` for that reason (`72bf7ada`). Fix: read the display settings (pixel size, halt time) from `context.scene` in `view_draw()`, `FrameBuffer` and `utils.calc_filmsize()`, or tag the scene from an update callback of these properties. Found during LuxCore ROADMAP E4b.

### The viewport main thread still stalls 30-45 ms once per frame while RTPATHOCL renders LynxSpider
- Found: 2026-10-29
- Area: viewport
- Problem: with the film read in a thread (ROADMAP 1.12 step 8), the main thread of a LynxSpider viewport render still has a 30-45 ms gap about every 118 ms (the RT frame time): 9 gaps over 30 ms, 0.30 s in all, in 2.8 s of rendering (1.82 s before step 8). `view_draw()` itself stays under 15 ms (1.4 ms on average); the gaps fall between two short draws.
- Reproduce: Blender 5.2.2, `scenes/LynxSpider/LynxSpider/LynxSpider.blend` (LuxCore repo), GPU (HIP) viewport, pixel size 1, 1600x1000 region: LuxCore `tests/benchmark/viewport_timing.py --device OCL --pixel-size 1 --halt 4` ("rendering" window, `main_thread`; the gaps come from `probes.npz` `ticks`).
- Notes: not measured further. Suspects: Blender's own redraw waiting for the GPU (the texture upload or the buffer swap queued behind the RTPATHOCL frame on the same GPU), or the read thread holding the GIL (only between its pyluxcore and numpy calls, which release it). Cycles on the Cornell box stays under 9 ms.

### A view change waits for the viewport denoiser
- Found: 2026-10-17
- Area: viewport
- Problem: when the view changes while OIDN runs in the viewport, Blender waits for the denoiser to finish (about 0.9 s) before it restarts the render.
- Reproduce: Blender 5.2, 1000x700 window, PATH CPU, viewport denoiser on; orbit the view while a denoise pass is running.
- Notes: the denoiser runs as an asynchronous image pipeline since `5bd9f1dc`, so the plain draw no longer blocks (longest draw 70 ms). The wait is in the session stop: the core cannot cancel a running image pipeline, so the fix needs a cancel call in the core first. See ROADMAP.md item 1.4.

### Blender hung with the online library asset bar open (suspected)
- Found: 2026-10-22
- Area: online library
- Problem: Blender stopped responding (one core busy) shortly after right-clicking a thumbnail in the Asset Bar, while a thumbnail download thread was still running.
- Reproduce: not reliable. Blender 5.2.2 on Windows with simulated input (`--enable-event-simulate`): Update ToC (after a first try that failed on the missing library folder), Asset Bar, hover and right-click a thumbnail. Six scripted replays of the same steps, on the code before and after the cleanup commits, did not hang.
- Notes: py-spy showed the main thread in native Blender code with no Python frame, and Thread-4 (`bg_download_thumbnails`) in an SSL read. Main suspect: that thread loaded and scaled the images with the bpy API. Since the thumbnail fix of 2026-10-22 it only downloads the files, and the images are loaded in the main thread; three replays since then did not hang. Remove this entry if it does not happen again.

### Diffuse BSDF roughness is ignored
- Found: 2026-10-23
- Area: export
- Problem: the Cycles node reader exports `ShaderNodeBsdfDiffuse` as `matte` whatever its Roughness (a TODO in `export/cycles_node_reader.py`), while Cycles renders roughness > 0 as Oren-Nayar. The user's Cornell box (walls at roughness 0.5) renders 4-13% brighter in LuxCore than in Cycles at depth 8 (LuxCore/Cycles RGB 1.108, 1.133, 1.040); with the walls set to roughness 0 in Cycles the ratio is 0.968, 0.998, 0.943 (the rest is the light temperature above and the other materials).
- Reproduce: as above, full path depth (`g0_prepare.py --depth 8`), 540x540, 64 and 256 spp.
- Notes: LuxCore has `roughmatte` (Oren-Nayar with sigma); Cycles' Diffuse BSDF roughness is the energy-conserving Oren-Nayar of Blender 4.x, so the mapping needs a check against Cycles (furnace or a flat-lit plane), not only a sigma conversion.

### Area light "Normalize" off is not exported (suspected)
- Found: 2026-10-23
- Area: export
- Problem: with "Use Cycles settings", an area light's power is always divided by its area (`_convert_cycles_light`), while Cycles divides only when `Light.normalize` is on (the default). Not tested.
- Reproduce: Blender 5.2, an area light of size 2 with Normalize off, Cycles against LuxCore: LuxCore should be 4x too dark.
- Notes: read from the code during LuxCore ROADMAP D1.

### Blender's other ray visibility flags are ignored
- Found: 2026-10-24
- Area: export
- Problem: of Blender's Object > Visibility > Ray Visibility (what Cycles uses), the addon reads only Camera on meshes (and drops an object with every flag off). Meshes: Diffuse, Glossy, Transmission, Volume Scatter and Shadow off still show the object to those rays. Lights: Diffuse, Glossy and Transmission are ignored, and Camera too except on an area light with LuxCore settings; an area light with "Use Cycles settings" is always hidden from the camera (`visible_to_camera = False` in `_convert_cycles_light()`), while Cycles shows it with Camera on (the default): a 4x4 area light seen from below reads 2.04 in Cycles with Camera on, 0.05 with it off (16x16, CPU).
- Reproduce: Blender 5.2, any of these flags off, Cycles against LuxCore; for the area light, a camera looking at it.
- Notes: LuxCore counterparts exist only in part. A light's `visibility.indirect.diffuse/glossy/specular.enable` could take the light's Diffuse, Glossy and Transmission flags. For meshes there is no per-object flag besides `camerainvisible`: the emission's `visibility.indirect.*` and `transparency.shadow` are per material (a material shared by objects with different flags needs copies), and they hide only the emitted light or the shadow, not the surface. Showing the Cycles area light to the camera changes the G0 and D1 Cornell comparisons (the light is in view there).

### Item assignment does not set a node's property in Blender 5
- Found: 2026-10-24
- Area: nodes
- Problem: `node["name"] = value`, the way the addon sets a property without its update callback (CLAUDE.md, `doc/useful.md`), creates a user custom property on a node or node tree in Blender 5 and leaves the `bpy.props` value unchanged. Checked in Blender 5.2: the 2D Mapping node's UV Scale does not reach U and V (`update_uniform_scale()`; the export uses UV Scale while Uniform Scale is on, so only switching it off shows the old U and V); the Constant Color node's HSV values do not follow the color, and editing them does not change it (`constfloat3.py`); a node tree's `refresh` stays on (`acknowledge_connection()`, `nodes/base.py`). `band.py` does the same for its add and remove buttons. Property groups on an ID (`material.luxcore["use_cycles_nodes"]`) are not affected.
- Reproduce: Blender 5.2, background: `n = tree.nodes.new("LuxCoreNodeTexMapping2D"); n.uniform_scale = 3`; `n.uscale` stays 1.
- Notes: Blender 5 keeps the `bpy.props` values of nodes and IDs apart from the user properties; `node.bl_system_properties_get(do_create=True)["name"] = value` sets the value without the callback (`utils/temperature.py` `store()` does this). Blender 4.5 still shares the storage.

### Limits of the Max (Legacy) temperature normalization of old files
- Found: 2026-10-24
- Area: handlers
- Problem: the lights, worlds and Blackbody nodes of a file from an older addon version get Max (Legacy) when the file is loaded or appended from; three cases are missed. (1) A file that was already open when the addon was enabled keeps Luminance (load_post did not run for it). (2) A light or world created after the last save and recovered from an autosave gets Max (the values are stored on save, and autosave runs no save_pre handler). (3) The EEVEE node sync (`utils/eevee_nodes.py`) makes a Cycles Blackbody node, always at luminance 1: a Max node shows 1.38x brighter in EEVEE than in LuxCore at 6500 K (a node with Normalize off already differed).
- Reproduce: (1) open an old file with the addon disabled, enable it; (2) add a light, wait for an autosave, recover it (not tried).
- Notes: `utils/temperature.py`. Case 1 could run the conversion on register, but Reload Scripts would then convert the lights created since the last save.

### "Only write LuxCore scene" crashes Blender when the render is written
- Found: 2026-10-24
- Area: export
- Problem: `bpy.ops.render.render(write_still=True)` with `config.use_filesaver` on writes the scene files, then Blender crashes in `BKE_image_render_write_exr` (null read at 0x48: no render result). An animation render with the file saver probably crashes the same way (not tried).
- Reproduce: Blender 5.2 background, any scene, PATHOCL, `scene.luxcore.config.use_filesaver = True`, a file saver path, `render(write_still=True)`; same with the addon of 5569b5df.
- Notes: the engine leaves no render result in file saver mode; giving Blender an empty result would avoid it.
