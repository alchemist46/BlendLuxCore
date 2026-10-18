"""Subsurface: Cycles' Subsurface Scattering node and the LuxCore Subsurface node.

A sphere of random-walk subsurface material lit by an area light, seen by an
orthographic camera. The Cycles node read by the Cycles node reader must look
like Cycles (the same walk: Chiang 2016 with the van de Hulst mapping and
Dwivedi guiding), and the LuxCore node must export and render.

blender -b --factory-startup --python tests/test_subsurface.py -- <site-packages>
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

checks = harness.Checks("test_subsurface")
OUT = tempfile.mkdtemp(prefix="blc-subsurface-")
R = 64
SAMPLES = 64

scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)

bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=64, ring_count=32)
sphere = bpy.context.object
bpy.ops.object.shade_smooth()

light_data = bpy.data.lights.new("key", "AREA")
light_data.size = 2
light_data.energy = 400
light_data.luxcore.use_cycles_settings = True
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


def cycles_material(color, radius, scale):
    material = bpy.data.materials.new("sss")
    if bpy.app.version < (5, 0, 0):
        material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    sss = tree.nodes.new("ShaderNodeSubsurfaceScattering")
    sss.falloff = "RANDOM_WALK"
    sss.inputs["Color"].default_value = (*color, 1)
    sss.inputs["Radius"].default_value = radius
    sss.inputs["Scale"].default_value = scale
    tree.links.new(sss.outputs[0], output.inputs["Surface"])
    material.luxcore.use_cycles_nodes = True
    return material


def sphere_mean(pixels):
    """Mean color of the pixels well inside the sphere's disk"""
    y, x = np.mgrid[0:R, 0:R]
    r = np.hypot((x + 0.5) / R - 0.5, (y + 0.5) / R - 0.5) * 2.4
    mask = r < 0.9
    return pixels[..., :3][mask].mean(axis=0)


def diffuse_material(color):
    material = bpy.data.materials.new("diffuse")
    if bpy.app.version < (5, 0, 0):
        material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    diffuse = tree.nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.inputs["Color"].default_value = (*color, 1)
    tree.links.new(diffuse.outputs[0], output.inputs["Surface"])
    material.luxcore.use_cycles_nodes = True
    return material


# Each engine's subsurface sphere is compared to its own diffuse sphere of
# the same color, so that the check is about the subsurface material only
sphere.data.materials.append(diffuse_material((0.8, 0.8, 0.8)))
cycles_diffuse = sphere_mean(harness.render(scene, "CYCLES", os.path.join(OUT, "diffuse_cycles.exr"), SAMPLES))
luxcore_diffuse = sphere_mean(harness.render(scene, "LUXCORE", os.path.join(OUT, "diffuse_luxcore.exr"), SAMPLES))
print("diffuse means: Cycles", np.round(cycles_diffuse, 4), "LuxCore", np.round(luxcore_diffuse, 4))

# Cycles' node, through the Cycles node reader, against Cycles
for name, color, radius, scale in (
        ("skin", (0.8, 0.5, 0.4), (1.0, 0.2, 0.1), 0.2),
        ("milk", (0.9, 0.9, 0.9), (1.0, 1.0, 1.0), 0.05)):
    sphere.data.materials.clear()
    sphere.data.materials.append(cycles_material(color, radius, scale))
    LuxCoreErrorLog.clear()
    cycles = sphere_mean(harness.render(scene, "CYCLES", os.path.join(OUT, name + "_cycles.exr"), SAMPLES))
    luxcore = sphere_mean(harness.render(scene, "LUXCORE", os.path.join(OUT, name + "_luxcore.exr"), SAMPLES))
    ratio = (luxcore / luxcore_diffuse) / np.maximum(cycles / cycles_diffuse, 1e-6)
    checks.check(not LuxCoreErrorLog.errors, f"{name}: no export errors")
    checks.check(bool(np.all(np.abs(ratio - 1) < 0.08)),
                 f"{name}: LuxCore/Cycles relative to diffuse {np.round(ratio, 3)} (Cycles {np.round(cycles / cycles_diffuse, 3)})")

# The LuxCore node
material = bpy.data.materials.new("luxcore_sss")
tree = bpy.data.node_groups.new("luxcore_sss", "luxcore_material_nodes")
material.luxcore.node_tree = tree
output = tree.nodes.new("LuxCoreNodeMatOutput")
node = tree.nodes.new("LuxCoreNodeMatSubsurface")
node.inputs["Color"].default_value = (0.8, 0.5, 0.4)
node.inputs["Scale"].default_value = 0.2
tree.links.new(node.outputs[0], output.inputs[0])
sphere.data.materials.clear()
sphere.data.materials.append(material)
LuxCoreErrorLog.clear()
pixels = harness.render(scene, "LUXCORE", os.path.join(OUT, "node_luxcore.exr"), 16)
mean = sphere_mean(pixels)
checks.check(not LuxCoreErrorLog.errors, "Subsurface node: no export errors")
checks.check(bool(mean[0] > 0.02 and mean[0] > mean[2]), f"Subsurface node renders (mean {np.round(mean, 3)})")

print("images in", OUT)
checks.finish()
