"""Lights with "Use Cycles settings": same brightness as Cycles, and the Blackbody color node.

A diffuse plane lit by one lamp, direct light only, rendered by both engines;
the center of the image is compared.

blender -b --factory-startup --python tests/test_cycles_lights.py -- <site-packages>
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
import pyluxcore
from mathutils import Vector
from blendluxcore.export import light as light_export

checks = harness.Checks("test_cycles_lights")
TOLERANCE = 0.03
OUT = tempfile.mkdtemp(prefix="blc-lights-")

scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)

bpy.ops.mesh.primitive_plane_add(size=40)
ground = bpy.context.object
material = bpy.data.materials.new("ground")
if bpy.app.version < (5, 0, 0):
    material.use_nodes = True
nodes = material.node_tree.nodes
for node in list(nodes):
    nodes.remove(node)
output = nodes.new("ShaderNodeOutputMaterial")
diffuse = nodes.new("ShaderNodeBsdfDiffuse")
diffuse.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1)
material.node_tree.links.new(diffuse.outputs[0], output.inputs[0])
ground.data.materials.append(material)
material.luxcore.use_cycles_nodes = True

camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
camera.data.lens = 120
scene.collection.objects.link(camera)
camera.location = (8, 0, 8)
camera.rotation_euler = (-camera.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = camera

scene.render.resolution_x = scene.render.resolution_y = 24
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = "Standard"
scene.cycles.max_bounces = 0
scene.luxcore.config.path.depth_total = 1
scene.luxcore.config.path.depth_diffuse = 1
scene.luxcore.config.path.depth_glossy = 1


def lamp(kind, energy, location=(0, 0, 2), **settings):
    for obj in [o for o in bpy.data.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(obj)
    data = bpy.data.lights.new("lamp", kind)
    data.energy = energy
    for key, value in settings.items():
        setattr(data, key, value)
    data.luxcore.use_cycles_settings = True
    obj = bpy.data.objects.new("lamp", data)
    scene.collection.objects.link(obj)
    obj.location = location
    return obj


def center(pixels):
    h, w = pixels.shape[:2]
    return float(pixels[h // 2 - 2:h // 2 + 2, w // 2 - 2:w // 2 + 2, :3].mean())


cases = {
    "point": dict(kind="POINT", energy=1000, shadow_soft_size=0),
    "sphere": dict(kind="POINT", energy=1000, shadow_soft_size=0.25),
    "spot": dict(kind="SPOT", energy=1000, shadow_soft_size=0, spot_size=math.radians(70), spot_blend=0.2),
    "sun": dict(kind="SUN", energy=3, location=(0, 0, 5), angle=math.radians(1.0)),
    "sharp sun": dict(kind="SUN", energy=3, location=(0, 0, 5), angle=math.radians(0.05)),
    "square area": dict(kind="AREA", energy=1000, shape="SQUARE", size=1.0),
    "rectangle area": dict(kind="AREA", energy=1000, shape="RECTANGLE", size=1.0, size_y=2.0),
}
if "normalize" in bpy.types.AreaLight.bl_rna.properties:
    # Normalize off: the power is not divided by the area (4 m^2 here)
    cases["area, normalize off"] = dict(kind="AREA", energy=250, shape="SQUARE", size=2.0, normalize=False)
for name, settings in cases.items():
    lamp(**settings)
    cycles = center(harness.render(scene, "CYCLES", os.path.join(OUT, name + "_cycles.exr"), 128))
    luxcore = center(harness.render(scene, "LUXCORE", os.path.join(OUT, name + "_luxcore.exr"), 128))
    ratio = luxcore / cycles
    checks.check(abs(ratio - 1) < TOLERANCE, f"{name}: LuxCore / Cycles = {ratio:.3f}")

# Blackbody node on the Emission color: the light gets the temperature
for kind in ("POINT", "SPOT", "SUN", "AREA"):
    obj = lamp(kind, 10)
    if bpy.app.version < (5, 0, 0):
        obj.data.use_nodes = True
    tree = obj.data.node_tree
    emission = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeEmission")
    blackbody = tree.nodes.new("ShaderNodeBlackbody")
    blackbody.inputs["Temperature"].default_value = 3200
    tree.links.new(blackbody.outputs[0], emission.inputs["Color"])
    depsgraph = bpy.context.evaluated_depsgraph_get()
    props, _ = light_export.convert_light(None, obj, "lamp", depsgraph, pyluxcore.Scene(), obj.matrix_world.copy(), False)
    text = str(props)
    key = "emission.temperature = 3200" if kind == "AREA" else "scene.lights.lamp.temperature = 3200"
    checks.check(key in text, f"blackbody {kind.lower()} light: temperature exported")

checks.finish()
