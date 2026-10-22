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
