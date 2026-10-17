"""Cycles node reader: an image texture behind a Mapping node looks the same as in Cycles.

An emission plane with a small color grid, seen from above by an orthographic
camera, rendered by both engines for several Mapping settings.

blender -b --factory-startup --python tests/test_cycles_mapping.py -- <site-packages>
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
import numpy as np
from blendluxcore.utils.errorlog import LuxCoreErrorLog

checks = harness.Checks("test_cycles_mapping")
OUT = tempfile.mkdtemp(prefix="blc-mapping-")
R = 64
# Pixels may differ along the edges of the grid cells (filtering, sampling)
MAX_DIFFERING_PIXELS = 0.08

scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)

# An asymmetric test image, saved so that both engines read the same file
grid = bpy.data.images.new("grid", 16, 16)
grid.generated_type = "COLOR_GRID"
grid.filepath_raw = os.path.join(OUT, "grid.png")
grid.file_format = "PNG"
grid.save()
grid = bpy.data.images.load(os.path.join(OUT, "grid.png"))

bpy.ops.mesh.primitive_plane_add(size=2)
plane = bpy.context.object
material = bpy.data.materials.new("mapped")
if bpy.app.version < (5, 0, 0):
    material.use_nodes = True
tree = material.node_tree
for node in list(tree.nodes):
    tree.nodes.remove(node)
output = tree.nodes.new("ShaderNodeOutputMaterial")
emission = tree.nodes.new("ShaderNodeEmission")
texture = tree.nodes.new("ShaderNodeTexImage")
texture.image = grid
mapping = tree.nodes.new("ShaderNodeMapping")
coordinates = tree.nodes.new("ShaderNodeTexCoord")
tree.links.new(coordinates.outputs["UV"], mapping.inputs["Vector"])
tree.links.new(mapping.outputs[0], texture.inputs["Vector"])
tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
tree.links.new(emission.outputs[0], output.inputs[0])
plane.data.materials.append(material)
material.luxcore.use_cycles_nodes = True

camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
camera.data.type = "ORTHO"
camera.data.ortho_scale = 2
scene.collection.objects.link(camera)
camera.location = (0, 0, 5)
scene.camera = camera

scene.render.resolution_x = scene.render.resolution_y = R
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = "Standard"
scene.render.filter_size = 0.01
scene.cycles.pixel_filter_type = "BOX"
scene.luxcore.config.filter = "BOX"
scene.luxcore.config.filter_width = 0.5


def differing_pixels(a, b):
    """Fraction of differing pixels at the best alignment within 2 pixels.

    LuxCore's image is one pixel row off Cycles' (a camera convention of the
    engine), which is not what this test is about.
    """
    best = 1.0
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            sa = a[max(dy, 0):R + min(dy, 0), max(dx, 0):R + min(dx, 0)]
            sb = b[max(-dy, 0):R + min(-dy, 0), max(-dx, 0):R + min(-dx, 0)]
            best = min(best, float((np.abs(sa - sb).max(axis=2) > 0.05).mean()))
    return best


cases = [
    ("identity", "POINT", (0, 0, 0), 0, (1, 1, 1)),
    ("scale", "POINT", (0, 0, 0), 0, (2, 3, 1)),
    ("rotation", "POINT", (0, 0, 0), 30, (1, 1, 1)),
    ("translation", "POINT", (0.25, 0.1, 0), 0, (1, 1, 1)),
    ("all three", "POINT", (0.3, -0.2, 0), -50, (1.5, 2.5, 1)),
    ("vector type", "VECTOR", None, 20, (2, 1, 1)),
]
for name, vector_type, location, rotation_z, scale in cases:
    mapping.vector_type = vector_type
    if location is not None:
        mapping.inputs["Location"].default_value = location
    mapping.inputs["Rotation"].default_value = (0, 0, math.radians(rotation_z))
    mapping.inputs["Scale"].default_value = scale
    LuxCoreErrorLog.clear()
    cycles = harness.render(scene, "CYCLES", os.path.join(OUT, name + "_cycles.exr"), 16)[..., :3]
    luxcore = harness.render(scene, "LUXCORE", os.path.join(OUT, name + "_luxcore.exr"), 16)[..., :3]
    differing = differing_pixels(cycles, luxcore)
    warnings = [w.message for w in LuxCoreErrorLog.warnings]
    checks.check(differing < MAX_DIFFERING_PIXELS and not warnings,
                 f"{name}: {differing * 100:.1f}% of the pixels differ from Cycles, warnings {warnings}")

# Unsupported mappings keep the UVs and say so
mapping.vector_type = "TEXTURE"
LuxCoreErrorLog.clear()
harness.render(scene, "LUXCORE", os.path.join(OUT, "texture_luxcore.exr"), 1)
checks.check(any("Mapping type" in w.message for w in LuxCoreErrorLog.warnings), "Texture type mapping: warning")

checks.finish()
