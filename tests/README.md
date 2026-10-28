# Headless tests

Each test runs in a fresh background Blender and loads the addon from this
repository, with the pyluxcore wheel loader stubbed out: nothing is
downloaded or installed, and the installed extension is not touched.

```bash
# All tests, with pyluxcore from a venv
tests/run.sh ~/venvs/pyluxcore/lib/python3.13/site-packages
# One test
tests/run.sh <site-packages> test_mesh_export
# Directly
blender -b --factory-startup --python tests/test_smoke.py -- <site-packages>
```

`BLENDER` selects the Blender executable (default: `blender` in `PATH`). The
renders are tiny (64x64 or smaller, a few to 128 samples, CPU): the whole
suite takes about a minute.

| Test | Checks |
| --- | --- |
| `test_smoke` | The addon enables, exports the default scene and renders it, with an empty error log |
| `test_mesh_export` | Every exported triangle corner has Blender's position, corner normal, UVs and colors; corners with the same data share one vertex (flat, smooth, smooth by angle, custom normals, several materials, UV and color layers, UV seams) |
| `test_cycles_lights` | Lights with "Use Cycles settings" have Cycles' brightness within 3% (point, sphere, spot, sun, sharp sun, square and rectangle area), and a Blackbody color node becomes the light temperature |
| `test_cycles_mapping` | An image texture behind a Mapping node matches Cycles (identity, scale, rotation, translation, Vector type) |
| `test_subsurface` | Cycles' Subsurface Scattering node (Random Walk), read by the Cycles node reader, matches Cycles relative to a diffuse sphere within 8% per channel (white medium 0.2%, coloured 1-4%); the LuxCore Subsurface node exports Scatter Color * Radius as the mean free path (one Radius, per channel, or a texture), its collapsed Radius scales the channels together, an old node (Scale and a Radius vector) is converted with the same mean free path, and it renders |
| `test_glossycoating` | The Glossy Coating node's Darkening input is off in a new node and exported only when on (0.5 is exported, 0 is not), an old node gets it switched off after Absorption Depth, and darkening 1 darkens a red matte base (the dim channels more) with no export errors |
| `test_viewport_device` | The viewport Device defaults to Auto, which renders RTPATHOCL when the final render uses the GPU (and pyluxcore has GPU support) and RTPATHCPU otherwise; CPU and GPU still force their device; the stored values of CPU (0) and GPU (1) in older files are unchanged |
| `test_openpbr_principled` | The Principled BSDF, read as an OpenPBR material (pyluxcore 3.0, skipped before), matches Cycles once LuxCore's one-row image offset is aligned on a diffuse sphere: dielectric, metal, sheen and glass within 0.6% in the mean, smooth glass (delta lobes) 0.5%, the coat over a white base (no darkening) without base specular within 0.5%, anisotropic 3%, subsurface up to 10% (the exit interface differs); thin film is not compared (another interference model). The OpenPBR node exports only the non-default inputs of its enabled groups, takes the dispersion's Abbe number from the IOR Preset node, and renders |
| `test_cycles_reader_cache` | A material read from its Cycles nodes is read once per export, not once per object (20 cubes, one read); a changed material is read again during a viewport update |
| `test_cycles_convert` | "Convert Cycles Nodes to LuxCore": every case renders like the Cycles node reader within 2-3% in the mean (both LuxCore, same seed), with the same material properties where both define them: Principled dielectric, metal, glass, coat and sheen, an image behind a Mapping node with a Math roughness, emission, a Mix Shader of Diffuse and Glossy driven by an image through Separate Color and Color Ramp, a Color Mix node, the Emission and Transparent shaders, a node group and a normal map. An unsupported node becomes a labelled placeholder and an error log warning; converting again needs Replace Existing Tree and removes the old tree; the bulk operator skips hand-made LuxCore trees, library and grease pencil materials, and the hidden backups of Cycles nodes |
| `test_cycles_convert_disney` | `test_cycles_convert` with the Principled BSDF read as before pyluxcore 3.0 (Disney, Glass or a mix of both), as with the pyluxcore the addon installs |
| `test_camera_visibility` | Blender's Camera ray visibility hides a mesh from the camera like the LuxCore "Visible to Camera" option (hidden when either is off); an emitting plane hidden from the camera is not seen and lights the ground like in Cycles (within 0.3%); a collection instance is hidden when the object or its instancer has Camera off, as in Cycles; an area light with LuxCore settings follows the Camera flag |
| `test_temperature_normalization` | The LuxCore Blackbody node and the Temperature mode of lights and the world: new ones use Luminance and export `normalization = luminance` next to `normalize`, Max (Legacy) exports only `normalize`, as before; a file of an older addon version (nothing stored) gets Max on load, on append (operator and `libraries.load()`) and when linked, the startup file's lights keep Luminance, a light added since keeps Luminance once saved; the converted Cycles Blackbody node (6500 K emitter) matches Cycles within 3% (Max: 0.72x), a 3000 K and 6500 K Temperature light against a white one gives Blender's light color within 1% (the renders are skipped with a pyluxcore before 2026-10-23) |
| `test_eevee_sync` | "Keep EEVEE Nodes in Sync": LuxCore trees (OpenPBR, Disney, Glass, Matte with Emission, Mix, image with 2D mapping, Math and Color Math, Metal with Fresnel presets) give the expected Cycles nodes; Cycles renders the generated nodes like LuxCore renders the tree (diffuse 0.2%, others within 15%, images placed alike with and without a 2D mapping); a socket value change updates the Cycles nodes in place, a link change rebuilds them; hand-edited Cycles nodes are kept and the sync switches off with a warning; a converted material keeps its original Cycles nodes until the first LuxCore edit, with a hidden backup |

`harness.py` has the shared setup (`setup()`, `Checks`, `render()`). A test
prints one `OK` or `FAIL` line per check and exits with status 1 when a check
fails.

The tests are excluded from the extension package (`paths_exclude_pattern` in
`blender_manifest.toml`).
