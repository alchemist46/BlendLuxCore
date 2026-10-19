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
suite takes about 15 seconds.

| Test | Checks |
| --- | --- |
| `test_smoke` | The addon enables, exports the default scene and renders it, with an empty error log |
| `test_mesh_export` | Every exported triangle corner has Blender's position, corner normal, UVs and colors; corners with the same data share one vertex (flat, smooth, smooth by angle, custom normals, several materials, UV and color layers, UV seams) |
| `test_cycles_lights` | Lights with "Use Cycles settings" have Cycles' brightness within 3% (point, sphere, spot, sun, sharp sun, square and rectangle area), and a Blackbody color node becomes the light temperature |
| `test_cycles_mapping` | An image texture behind a Mapping node matches Cycles (identity, scale, rotation, translation, Vector type) |
| `test_subsurface` | Cycles' Subsurface Scattering node (Random Walk), read by the Cycles node reader, matches Cycles relative to a diffuse sphere within 8% per channel (white medium 0.2%, coloured 1-4%); the LuxCore Subsurface node exports and renders |
| `test_openpbr_principled` | The Principled BSDF, read as an OpenPBR material (pyluxcore 3.0, skipped before), matches Cycles once LuxCore's one-row image offset is aligned on a diffuse sphere: dielectric, metal, sheen and glass within 0.6% in the mean, the coat (without base specular) within 0.3%, anisotropic 3%, subsurface up to 10% (the exit interface differs); thin film is not compared (another interference model). The OpenPBR node exports only the non-default inputs of its enabled groups, and renders |

`harness.py` has the shared setup (`setup()`, `Checks`, `render()`). A test
prints one `OK` or `FAIL` line per check and exits with status 1 when a check
fails.

The tests are excluded from the extension package (`paths_exclude_pattern` in
`blender_manifest.toml`).
