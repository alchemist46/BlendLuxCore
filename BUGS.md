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

### Online library thumbnails load while their file is still open
- Found: 2026-10-22
- Area: online library
- Problem: the thumbnail download thread fails on some images with "Image '<name>.jpg' failed to load image buffer" (`IMB_load_image_from_file_descriptor: couldn't get mapping`), and that thread stops downloading the rest of its queue.
- Reproduce: Blender 5.2.2 on Windows, empty library folder; Update ToC from server, then open the Asset Bar. Happened in every run.
- Notes: in `bg_download_thumbnails()` (utils/lol/utils.py), `bpy.data.images.load()` and `img.scale()` run inside the `with open(tpath, "wb")` block, so the file is still open and maybe not flushed. The thread also calls the bpy API outside the main thread, which is not thread-safe; a suspect for the GUI hang below.

### Update ToC fails until the library folder exists
- Found: 2026-10-22
- Area: online library
- Problem: "Update ToC from server" raises FileNotFoundError writing `assets_model_blendermarket.json` when the library folder (addon preferences, global_dir) does not exist yet.
- Reproduce: change the library folder in the addon preferences to a new path, then click Update ToC from server without loading a file.
- Notes: the folders are only created in `_init_LuxCoreOnlineLibrary()` (handlers/load_post.py), which runs on file load.

### Blender hung with the online library asset bar open (suspected)
- Found: 2026-10-22
- Area: online library
- Problem: Blender stopped responding (one core busy) shortly after right-clicking a thumbnail in the Asset Bar, while a thumbnail download thread was still running.
- Reproduce: not reliable. Blender 5.2.2 on Windows with simulated input (`--enable-event-simulate`): Update ToC (after a failed first try, see above), Asset Bar, hover and right-click a thumbnail. Six scripted replays of the same steps, on the code before and after the cleanup commits, did not hang.
- Notes: py-spy showed the main thread in native Blender code with no Python frame, and Thread-4 (`bg_download_thumbnails`) in an SSL read. Main suspect: the bpy calls from that thread (see "Online library thumbnails load while their file is still open").

### Empty material tree dropdown never offers "New Node Tree"
- Found: 2026-10-22
- Area: ui
- Problem: when the file has no LuxCore material node tree, the material node tree dropdown (`LUXCORE_MATERIAL_MT_node_tree`) shows "No material node trees available" without the "New Node Tree" button and the presets submenu.
- Reproduce: Blender 5.2, factory scene, LuxCore engine; open the material node tree dropdown before creating any LuxCore tree.
- Notes: `LUXCORE_MT_node_tree.custom_draw()` (operators/utils.py) compares `tree_type` against `"luxcore_material_nodetree"`, but the tree type is `"luxcore_material_nodes"` (utils/node.py), so the branch is never taken. Found while adding the Cycles node conversion.
