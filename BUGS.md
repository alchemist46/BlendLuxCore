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

### Persistent cache overwrite never deletes the old file for `//` paths
- Found: 2026-10-22
- Area: export
- Problem: `get_persistent_cache_file_path` (`utils/__init__.py:708`) tests and removes `file_path` (the relative `//name.pgi` the load handler fills in) instead of `file_path_abs`, so with the default paths "Compute and overwrite" leaves the old file and LuxCore loads the stale cache instead of recomputing it.
- Reproduce: Blender 5.2, PhotonGI with "Compute and overwrite" on and the default file path; render, change a light, render again: the first cache is used.
- Notes: one-line fix (LuxCore ROADMAP CA, the addon step).

### Cycles light temperature: ignored, and the Blackbody node 27% too dark
- Found: 2026-10-23
- Area: export
- Problem: with "Use Cycles settings", a light's Temperature (`Light.use_temperature`, `Light.temperature`) is not exported: the light is plain white. Exporting it as `temperature` with `temperature.normalize` (as the Blackbody node path does since `99f0a383`) fixes the tint but makes the light 27% too dark at 6500 K: Cycles normalizes the blackbody color to luminance 1, LuxCore's `temperature.normalize` divides by the largest RGB value of any blackbody from 0 to 13000 K (`TemperatureToWhitePoint()`, color.cpp), so its scale depends on the temperature. The Blackbody node path has the same intensity error.
- Reproduce: Blender 5.2, the user's Cornell box (`Cornell Box Cycles.blend`, area light 100 W, temperature 6500 K, Rec.2020 file) prepared with LuxCore's `tests/benchmark/g0_prepare.py`, direct light only (`g0_render.py --bounces 0`), PATHOCL against Cycles: LuxCore/Cycles on every wall (0.978, 1.012, 0.970), which is (1, 1, 1) against the 6500 K blackbody in Rec.2020; with the temperature exported, (0.720, 0.726, 0.730).
- Notes: the fix needs the luminance-normalized blackbody: either the addon computes it (gain / Y of LuxCore's normalized blackbody, or the color itself in the working space), or the core gets a luminance normalization mode for `temperature`. Found by LuxCore ROADMAP D1.

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
