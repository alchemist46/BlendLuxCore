"""Smoke test: the addon enables, exports the default scene and renders it.

blender -b --factory-startup --python tests/test_smoke.py -- <site-packages>
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
import pyluxcore
from blendluxcore.utils.errorlog import LuxCoreErrorLog

checks = harness.Checks("test_smoke")
print("pyluxcore", pyluxcore.Version())

scene = bpy.context.scene
scene.render.resolution_x = 64
scene.render.resolution_y = 48
scene.render.resolution_percentage = 100

# The default material has no LuxCore node tree: export its Cycles nodes
for material in bpy.data.materials:
    material.luxcore.use_cycles_nodes = True

out = tempfile.mkdtemp(prefix="blc-smoke-")
LuxCoreErrorLog.clear()
pixels = harness.render(scene, "LUXCORE", os.path.join(out, "smoke.exr"), samples=4)

checks.check(pixels.shape == (48, 64, 4), f"rendered a 64x48 image (got {pixels.shape})")
checks.check(float(pixels[..., :3].mean()) > 0.01, f"the image is not black (mean {pixels[..., :3].mean():.3f})")
checks.check(not LuxCoreErrorLog.errors, f"no errors in the LuxCore error log {[e.message for e in LuxCoreErrorLog.errors]}")
checks.check(not LuxCoreErrorLog.warnings, f"no warnings in the LuxCore error log {[w.message for w in LuxCoreErrorLog.warnings]}")
checks.finish()
