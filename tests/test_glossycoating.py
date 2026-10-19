"""Glossy Coating: the Darkening input.

A sphere with a glossy coating over a red matte base lit by an area light,
seen by an orthographic camera. Darkening is exported only when it is on (older
pyluxcore builds keep working), an old node gets the input switched off, and
the darkening darkens a colored base.

blender -b --factory-startup --python tests/test_glossycoating.py -- <site-packages>
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
import numpy as np
from blendluxcore.utils.errorlog import LuxCoreErrorLog

checks = harness.Checks("test_glossycoating")
OUT = tempfile.mkdtemp(prefix="blc-glossycoating-")
R = 64

scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)

bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=64, ring_count=32)
sphere = bpy.context.object
bpy.ops.object.shade_smooth()

light_data = bpy.data.lights.new("key", "AREA")
light_data.size = 2
light_data.energy = 400
light = bpy.data.objects.new("key", light_data)
scene.collection.objects.link(light)
light.location = (2, -2, 3)
light.rotation_euler = (0.785, 0, 0.785)

camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
camera.data.type = "ORTHO"
camera.data.ortho_scale = 2.4
scene.collection.objects.link(camera)
camera.location = (0, -5, 0)
camera.rotation_euler = (1.5708, 0, 0)
scene.camera = camera

scene.render.resolution_x = scene.render.resolution_y = R
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = "Standard"

material = bpy.data.materials.new("coated")
tree = bpy.data.node_groups.new("coated", "luxcore_material_nodes")
material.luxcore.node_tree = tree
output = tree.nodes.new("LuxCoreNodeMatOutput")
coating = tree.nodes.new("LuxCoreNodeMatGlossyCoating")
base = tree.nodes.new("LuxCoreNodeMatMatte")
base.inputs["Diffuse Color"].default_value = (0.8, 0.1, 0.05)
tree.links.new(base.outputs[0], coating.inputs["Base Material"])
tree.links.new(coating.outputs[0], output.inputs[0])
sphere.data.materials.append(material)


def exported(node):
    import types
    import pyluxcore
    props = pyluxcore.Properties()
    # The base material is exported through the exporter's node cache
    exporter = types.SimpleNamespace(node_cache={})
    name = node.sub_export(exporter, None, props, "test")
    return props, "scene.materials." + name + "."


# Darkening: off by default and not exported, exported when on
darkening = coating.inputs["Darkening"]
checks.check(darkening.default_value == 0, "Darkening is off in a new node")
props, prefix = exported(coating)
checks.check(not props.IsDefined(prefix + "darkening"), "Darkening 0 is not exported")
darkening.default_value = 0.5
props, prefix = exported(coating)
checks.check(props.IsDefined(prefix + "darkening")
             and abs(props.Get(prefix + "darkening").GetFloat() - 0.5) < 1e-6, "Darkening 0.5 is exported")

# An old node (no Darkening input) gets it switched off, after Absorption Depth
from blendluxcore.utils import compatibility
old_tree = bpy.data.node_groups.new("old_coating", "luxcore_material_nodes")
old = old_tree.nodes.new("LuxCoreNodeMatGlossyCoating")
old.inputs.remove(old.inputs["Darkening"])
compatibility.update_glossycoating_add_darkening_input(old_tree)
names = [s.name for s in old.inputs]
depth = names.index("Absorption Depth (nm)")
checks.check(names[depth + 1] == "Darkening" and old.inputs["Darkening"].default_value == 0,
             f"old node converted: {names[:depth + 2]}")
bpy.data.node_groups.remove(old_tree)


def sphere_mean(pixels):
    y, x = np.mgrid[0:R, 0:R]
    disk = (x + 0.5 - R / 2) ** 2 + (y + 0.5 - R / 2) ** 2 < (0.35 * R) ** 2
    return pixels[disk][:, :3].mean(axis=0)


means = {}
for value in (0, 1):
    darkening.default_value = value
    LuxCoreErrorLog.clear()
    means[value] = sphere_mean(harness.render(scene, "LUXCORE", os.path.join(OUT, f"darkening{value}.exr"), 32))
    checks.check(not LuxCoreErrorLog.errors, f"Darkening {value}: no export errors")
ratio = means[1] / means[0]
# The red channel (albedo 0.8) is darkened less than the others: more saturated
checks.check(bool(ratio[0] < 0.97 and ratio[1] < ratio[0]),
             f"Darkening 1 darkens the colored base, the dim channels more (ratio {np.round(ratio, 3)})")

print("images in", OUT)
checks.finish()
