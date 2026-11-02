"""'Only write LuxCore scene' (file saver) with render(write_still=True).

The scene files are written, and Blender gets an empty result to write
instead of crashing in BKE_image_render_write_exr.

blender -b --factory-startup --python tests/test_filesaver.py -- <site-packages>
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy

checks = harness.Checks("test_filesaver")
OUT = tempfile.mkdtemp(prefix="blc-filesaver-")

scene = bpy.context.scene
scene.render.engine = "LUXCORE"
scene.render.resolution_x = scene.render.resolution_y = 32
scene.render.resolution_percentage = 100
scene.luxcore.config.engine = "PATH"
scene.luxcore.config.device = "CPU"
scene.luxcore.config.use_filesaver = True
scene.luxcore.config.filesaver_format = "TXT"
os.makedirs(os.path.join(OUT, "scene"))
scene.luxcore.config.filesaver_path = os.path.join(OUT, "scene")

for file_format, extension in (("OPEN_EXR", ".exr"), ("PNG", ".png")):
    scene.render.filepath = os.path.join(OUT, "image" + extension)
    scene.render.image_settings.file_format = file_format
    bpy.ops.render.render(write_still=True)
    checks.check(os.path.isfile(scene.render.filepath), f"{file_format}: the image is written")

written = [name for _, _, names in os.walk(os.path.join(OUT, "scene")) for name in names]
checks.check(any(name.endswith(".cfg") for name in written) and any(name.endswith(".scn") for name in written),
             f"the scene files are written: {sorted(written)}")

checks.finish()
