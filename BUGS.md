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

### Blender's Diffuse, Glossy, Transmission, Volume Scatter and Shadow ray visibility are ignored
- Found: 2026-10-24
- Area: export
- Problem: of Blender's Object > Visibility > Ray Visibility (what Cycles uses), the addon follows only Camera (meshes, and area lights with either settings). With Diffuse, Glossy, Transmission, Volume Scatter or Shadow off, Cycles leaves the object or light out of those rays; LuxCore still shows it to them. The Object panel says which flags are ignored.
- Reproduce: Blender 5.2, any of these flags off on a mesh or a light, Cycles against LuxCore.
- Notes: needs the core. A light's `visibility.indirect.diffuse/glossy/specular.enable` is not the counterpart: it drops only a path that hits the light after such a bounce, and direct light sampling still lights the surface (`PathTracer::CheckDirectHitVisibilityFlags()`), while Cycles' Diffuse off removes the light from diffuse lobes altogether. Point, spot and distant lights have no flags at all. For meshes there is no per-object flag besides `camerainvisible`; the material's `transparency.shadow` would give Shadow off, but per material (copies for objects with different flags). The Cycles area light's Camera flag was fixed on 2026-10-29.

### Two cases where a new light or world gets Max (Legacy) temperature normalization
- Found: 2026-10-29
- Area: handlers
- Problem: a light or world whose value is not stored is taken for one of an older addon version and set to Max on load. Two new ones are not stored: (1) a world that no scene uses, created after the last save and recovered from an autosave (a world is in the depsgraph only when a scene uses it, and the value is stored by the first depsgraph update or on save); (2) a light or world created while the addon was disabled, when the addon is enabled with the file open or the file is loaded again.
- Reproduce: (1) add a world with a fake user and no scene, wait for an autosave, recover it (not tried).
- Notes: `utils/temperature.py`, `handlers/depsgraph_update_post.py`. Both only change the brightness of a Temperature color (Max is 0.72x at 6500 K).
