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

### Converted Blackbody nodes and the LuxCore Temperature are 27% dark against Cycles
- Found: 2026-10-23
- Area: nodes, export
- Problem: the Cycles-to-LuxCore node converter (`operators/cycles_converter.py`) turns a Cycles Blackbody node into the LuxCore Blackbody node, which exports `normalize` (the blackbody divided by the largest RGB value of any blackbody up to 13000 K): the converted material is 27% darker than Cycles at 6500 K, 57% at 3000 K. The LuxCore Temperature color mode of the lights and the world has the same normalization.
- Reproduce: Blender 5.2, an Emission material with a Blackbody node at 6500 K, convert it with the LuxCore converter, compare with Cycles.
- Notes: the core has `normalization = "luminance"` (Cycles' normalization) since LuxCore ROADMAP F9; the "Use Cycles settings" lights and the Cycles node reader use it. Changing the LuxCore node and color modes is a look change of existing files: needs a decision (a Normalization option in their UI, luminance only for new nodes, or both).

### Blender's Camera ray visibility is ignored for meshes
- Found: 2026-10-23
- Area: export
- Problem: a mesh with Object > Visibility > Ray Visibility > Camera off (`Object.visible_camera`, what Cycles uses) is still seen by the camera in LuxCore: the addon reads only its own `obj.luxcore.visible_to_camera` (`utils.visible_to_camera()`). An emitter plane hidden from the camera in Cycles shows as a bright rectangle in LuxCore.
- Reproduce: Blender 5.2, the D1 Cornell box with its area light replaced by an emitting plane with Camera visibility off (LuxCore `lc-work/f9/mesh3000.blend` of ROADMAP F9), PATHOCL against Cycles.
- Notes: probably wanted (the LuxCore property predates Blender's), but a file made for Cycles renders differently; a fix could combine both flags.
