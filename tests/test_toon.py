"""Toon material (pyluxcore 3.0) and the Outline plugin of the image pipeline.

A sphere lit by a far point light (no world), seen by an orthographic camera.
The LuxCore Toon node exports its ramp (sorted) and renders the steps; Cycles'
Toon BSDF, read by the Cycles node reader and converted to a Toon node, matches
Cycles in the mean (diffuse within 3%, the glossy highlight, an approximation
on the half vector, within 15%); "Keep EEVEE Nodes in Sync" gives a Diffuse
BSDF; the Outline draws its lines at the silhouette. Skipped before pyluxcore
has the toon material.

blender -b --factory-startup --python tests/test_toon.py -- <site-packages>
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
import pyluxcore
from blendluxcore.export import cycles_node_reader
from blendluxcore.utils import eevee_nodes
from blendluxcore.utils.errorlog import LuxCoreErrorLog
from blendluxcore.utils.luxutils import has_material_type

checks = harness.Checks("test_toon")
if not has_material_type("toon"):
    print("SKIP: pyluxcore has no toon material")
    checks.finish()
    raise SystemExit(0)

OUT = tempfile.mkdtemp(prefix="blc-toon-")
R = 64

scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)

bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=128, ring_count=64)
sphere = bpy.context.object
bpy.ops.object.shade_smooth()

light_data = bpy.data.lights.new("key", "POINT")
light_data.energy = 400000
light_data.shadow_soft_size = 0
light_data.luxcore.use_cycles_settings = True
light = bpy.data.objects.new("key", light_data)
scene.collection.objects.link(light)
# From the front left, above
light.location = (-20, -30, 20)

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

y, x = np.mgrid[0:R, 0:R]
DISK = (x + 0.5 - R / 2) ** 2 + (y + 0.5 - R / 2) ** 2 < (0.4 * R) ** 2


def lum(pixels):
    return pixels[..., :3] @ np.array([0.2126, 0.7152, 0.0722])


def toon_material(name):
    material = bpy.data.materials.new(name)
    tree = bpy.data.node_groups.new(name, "luxcore_material_nodes")
    material.luxcore.node_tree = tree
    output = tree.nodes.new("LuxCoreNodeMatOutput")
    node = tree.nodes.new("LuxCoreNodeMatToon")
    tree.links.new(node.outputs[0], output.inputs["Material"])
    return material, node


def assign(material):
    sphere.data.materials.clear()
    sphere.data.materials.append(material)


def exported(node):
    import types
    props = pyluxcore.Properties()
    name = node.sub_export(types.SimpleNamespace(node_cache={}), None, props, "test")
    return props, "scene.materials." + name + "."


# The node's export: the keys of the ramp in ascending positions
material, toon = toon_material("toon")
toon.set_ramp([(0.6, (1, 1, 1)), (0.0, (0.25, 0.25, 0.25)), (0.2, (0.5, 0.5, 0.5))])
props, prefix = exported(toon)
positions = list(props.Get(prefix + "ramp.positions").GetFloats())
colors = list(props.Get(prefix + "ramp.colors").GetFloats())
checks.check(props.Get(prefix + "type").GetString() == "toon"
             and np.allclose(positions, (0, 0.2, 0.6)) and np.allclose(colors[0::3], (0.25, 0.5, 1))
             and props.Get(prefix + "ramp.interpolation").GetString() == "constant"
             and props.Get(prefix + "lightcenter").GetBool(),
             f"Toon node exported, ramp sorted: {np.round(positions, 3)} {np.round(colors[0::3], 3)}")

# The highlight: its size is the Roughness socket
toon.inputs["Roughness"].default_value = 0.35
props, prefix = exported(toon)
checks.check(abs(props.Get(prefix + "specular.roughness").GetFloat() - 0.35) < 1e-6
             and abs(props.Get(prefix + "kd").GetFloats()[0] - 1) < 1e-6,
             "Toon node: Roughness exported, Tint white by default")

# The steps: with one light and no world, the lit part of the sphere takes the
# levels of the ramp (0.25, 0.5 and 1 of the top one)
assign(material)
toon.inputs["Tint"].default_value = (0.8, 0.8, 0.8)
LuxCoreErrorLog.clear()
pixels = harness.render(scene, "LUXCORE", os.path.join(OUT, "toon.exr"), 16)
checks.check(not LuxCoreErrorLog.errors, "Toon node: no export errors")
values = lum(pixels)[DISK]
lit = values[values > 1e-4]
top = np.percentile(lit, 99)
levels = np.array([0.25, 0.5, 1.0]) * top
near = np.min(np.abs(lit[:, None] - levels[None, :]) / levels[None, :], axis=1) < 0.08
checks.check(lit.size > 0.3 * DISK.sum() and near.mean() > 0.9,
             f"Toon node: {near.mean():.1%} of the lit pixels on the ramp levels {np.round(levels, 3)}")

# Light Center: a large area light gives hard band edges with it (the
# direction of its center), blurred ones without it. The edge strength is the
# largest jump between neighbor pixels inside the disk, over the top level.
INNER = (x + 0.5 - R / 2) ** 2 + (y + 0.5 - R / 2) ** 2 < (0.33 * R) ** 2


def edge_strength(pixels):
    values = lum(pixels)
    pairs = INNER[:, 1:] & INNER[:, :-1]
    jumps = np.concatenate([np.abs(np.diff(values, axis=1))[pairs],
                            np.abs(np.diff(values, axis=0))[INNER[1:, :] & INNER[:-1, :]]])
    return np.percentile(jumps, 99.5) / np.percentile(values[INNER], 99)


light.hide_render = True
area_data = bpy.data.lights.new("area", "AREA")
area_data.size = 4
area_data.energy = 3000
area = bpy.data.objects.new("area", area_data)
scene.collection.objects.link(area)
area.location = (-2.5, -3, 2.5)
area.rotation_euler = (-area.location.normalized()).to_track_quat("-Z", "Y").to_euler()
strength = {}
for center in (True, False):
    toon.light_center = center
    strength[center] = edge_strength(harness.render(scene, "LUXCORE", os.path.join(OUT, f"area_{center}.exr"), 64))
checks.check(strength[True] > 0.15 and strength[False] < 0.5 * strength[True],
             f"Light Center: area light band edges {strength[True]:.3f} with it, {strength[False]:.3f} without")
toon.light_center = True
bpy.data.objects.remove(area)
light.hide_render = False

# The highlight alone (black ramp, white Specular Color): its area grows with
# the Roughness
toon.set_ramp([(0, (0, 0, 0))])
toon.inputs["Specular Color"].default_value = (1, 1, 1)
toon.specular_smooth = 0
area = {}
for roughness in (0.4, 0.7):
    toon.inputs["Roughness"].default_value = roughness
    values = lum(harness.render(scene, "LUXCORE", os.path.join(OUT, f"highlight_{roughness}.exr"), 16))[DISK]
    area[roughness] = int((values > 0.5 * values.max()).sum()) if values.max() > 0 else 0
checks.check(0 < area[0.4] and 3 * area[0.4] < area[0.7],
             f"Toon highlight: {area[0.4]} pixels at roughness 0.4, {area[0.7]} at 0.7")
toon.set_ramp([(0, (0.25, 0.25, 0.25)), (0.2, (0.5, 0.5, 0.5)), (0.6, (1, 1, 1))])
toon.inputs["Specular Color"].default_value = (0, 0, 0)


def cycles_toon(name, component, size, smooth, color=(0.8, 0.8, 0.8)):
    material = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    node = tree.nodes.new("ShaderNodeBsdfToon")
    node.component = component
    node.inputs["Color"].default_value = (*color, 1)
    node.inputs["Size"].default_value = size
    node.inputs["Smooth"].default_value = smooth
    tree.links.new(node.outputs[0], output.inputs["Surface"])
    material.luxcore.use_cycles_nodes = True
    return material


# The Cycles node reader
props = pyluxcore.Properties()
diffuse = cycles_toon("cycles_diffuse", "DIFFUSE", 0.5, 0.0)
name = cycles_node_reader.convert(diffuse, props, "test_obj")[0]
prefix = f"scene.materials.{name}."
factor = 1 / (2 * (1 - math.cos(math.pi / 4)))
checks.check(props.Get(prefix + "type").GetString() == "toon"
             and abs(props.Get(prefix + "ramp.thresholds").GetFloats()[0] - math.cos(math.pi / 4)) < 1e-5
             and abs(props.Get(prefix + "ramp.factors").GetFloats()[0] - factor) < 1e-4,
             "Cycles Toon BSDF (diffuse, size 0.5) read as a toon with one step at 45 degrees")

means = {}
for component, size, smooth, tolerance in (("DIFFUSE", 0.5, 0.0, 0.03), ("DIFFUSE", 0.3, 0.2, 0.03),
                                            ("GLOSSY", 0.2, 0.1, 0.15)):
    material = cycles_toon(f"{component}_{size}", component, size, smooth)
    assign(material)
    tag = f"{component.lower()} size {size} smooth {smooth}"
    lux = lum(harness.render(scene, "LUXCORE", os.path.join(OUT, f"{component}_{size}_lux.exr"), 64))[DISK].mean()
    cyc = lum(harness.render(scene, "CYCLES", os.path.join(OUT, f"{component}_{size}_cycles.exr"), 64))[DISK].mean()
    checks.check(cyc > 0 and abs(lux / cyc - 1) < tolerance,
                 f"Cycles Toon BSDF ({tag}) read: LuxCore/Cycles {lux / cyc:.3f}")
    means[(component, size)] = lux

    # The converter: a Toon node, rendering like the reader
    with bpy.context.temp_override(material=material):
        bpy.ops.luxcore.material_convert_cycles(sync_eevee=False)
    tree = material.luxcore.node_tree
    nodes = [n for n in tree.nodes if n.bl_idname == "LuxCoreNodeMatToon"] if tree else []
    checks.check(len(nodes) == 1, f"Cycles Toon BSDF ({tag}) converted to a Toon node")
    material.luxcore.use_cycles_nodes = False
    converted = lum(harness.render(scene, "LUXCORE", os.path.join(OUT, f"{component}_{size}_conv.exr"), 64))[DISK].mean()
    checks.check(abs(converted / lux - 1) < 0.02,
                 f"Cycles Toon BSDF ({tag}) converted renders like the reader: {converted / lux:.3f}")

# The EEVEE nodes of a Toon node: a Diffuse BSDF of its Tint
material, toon = toon_material("eevee_toon")
toon.inputs["Tint"].default_value = (0.2, 0.4, 0.6)
eevee_nodes.generate(material)
diffuse_nodes = [n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfDiffuse"]
color = tuple(diffuse_nodes[0].inputs["Color"].default_value)[:3] if len(diffuse_nodes) == 1 else None
checks.check(color is not None and np.allclose(color, (0.2, 0.4, 0.6)),
             f"EEVEE sync: the Toon node is a Diffuse BSDF of its color ({color})")

# The Outline: black lines on the lit silhouette
material, toon = toon_material("outlined")
toon.set_ramp([(0, (1, 1, 1))])
assign(material)
plain = lum(harness.render(scene, "LUXCORE", os.path.join(OUT, "outline_off.exr"), 16))
outline = camera.data.luxcore.imagepipeline.outline
outline.enabled = True
outline.width = 1
LuxCoreErrorLog.clear()
lined = lum(harness.render(scene, "LUXCORE", os.path.join(OUT, "outline_on.exr"), 16))
checks.check(not LuxCoreErrorLog.errors, "Outline: no export errors")
r = np.sqrt((x + 0.5 - R / 2) ** 2 + (y + 0.5 - R / 2) ** 2) / (R / 2 / 1.2)
darkened = (plain > 0.05) & (lined < 0.2 * plain)
ring = darkened & (np.abs(r - 1) < 0.08)
checks.check(darkened.sum() > 20 and ring.sum() > 0.9 * darkened.sum(),
             f"Outline: {darkened.sum()} lit pixels turned black, {ring.sum()} of them on the silhouette")

print("images in", OUT)
checks.finish()
