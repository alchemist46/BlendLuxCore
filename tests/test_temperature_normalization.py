"""Blackbody normalization of the LuxCore Blackbody node and the Temperature color mode.

New nodes, lights and worlds use Luminance (Cycles' normalization) and export
".normalization = luminance" next to ".normalize" (older pyluxcore builds
ignore the first one and fall back to Max). Max (Legacy) exports what the
older addon versions exported. Files saved by an older addon version (no
value stored) get Max on load and on append, and linked data from them
exports Max; files saved now keep their values. The converted Cycles Blackbody
node renders like Cycles, and a Temperature light like Blender's light color.

blender -b --factory-startup --python tests/test_temperature_normalization.py -- <site-packages>
"""

import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
import numpy as np
import pyluxcore
from blendluxcore.export import light as light_export
from blendluxcore.handlers import load_post, save_pre
from blendluxcore.operators.cycles_convert import assign_converted_tree
from blendluxcore.operators.cycles_converter import convert_material

checks = harness.Checks("test_temperature_normalization")
OUT = tempfile.mkdtemp(prefix="blc-temperature-")
OLD = os.path.join(OUT, "old.blend")
NEW = os.path.join(OUT, "new.blend")


def blackbody_props(node):
    props = pyluxcore.Properties()
    node.sub_export(SimpleNamespace(node_cache={}), None, props, "bb")
    return props, "scene.textures.bb."


EXPORTER = SimpleNamespace(lightgroup_cache=set())


def light_props(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    props, _ = light_export.convert_light(EXPORTER, obj, obj.name, depsgraph, pyluxcore.Scene(),
                                          obj.matrix_world.copy(), False)
    return props


def keys(props, suffix):
    """The values of the keys ending with suffix"""
    return [props.Get(k).GetValuesString() for k in props.GetAllNames() if k.endswith(suffix)]


def exported_as(props, what, luminance):
    normalize = keys(props, "normalize")
    normalization = keys(props, "normalization")
    expected = ["luminance"] if luminance else []
    checks.check(normalize == ["1"] and normalization == expected,
                 f"{what}: normalize {normalize}, normalization {normalization} (expected {expected})")


def new_light(name, kind):
    data = bpy.data.lights.new(name, kind)
    data.luxcore.color_mode = "temperature"
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def blackbody_tree(name):
    tree = bpy.data.node_groups.new(name, "luxcore_material_nodes")
    tree.use_fake_user = True
    return tree, tree.nodes.new("LuxCoreNodeTexBlackbody")


# The startup file (no path): its lights are new ones, load_post leaves them alone
default_lights = list(bpy.data.lights)
load_post.handler(None)
checks.check(all(light.luxcore.temperature_normalization == "luminance" for light in default_lights),
             f"startup file: lights keep Luminance ({len(default_lights)} lights)")

# Export of new data and of Max
tree, node = blackbody_tree("bb")
checks.check(node.normalization == "luminance" and node.is_property_set("normalization"),
             "new Blackbody node: Luminance, stored")
exported_as(blackbody_props(node)[0], "Blackbody node Luminance", True)
node.normalization = "max"
exported_as(blackbody_props(node)[0], "Blackbody node Max", False)
node.normalize = False
props, prefix = blackbody_props(node)
checks.check(not props.Get(prefix + "normalize").GetBool() and not props.IsDefined(prefix + "normalization"),
             "Blackbody node without Normalize: normalize 0, no normalization")
bpy.data.node_groups.remove(tree)

for kind in ("POINT", "SPOT", "SUN", "AREA"):
    obj = new_light("lamp_" + kind.lower(), kind)
    checks.check(obj.data.luxcore.temperature_normalization == "luminance", f"new {kind.lower()} light: Luminance")
    exported_as(light_props(obj), f"{kind.lower()} light Luminance", True)
    obj.data.luxcore.temperature_normalization = "max"
    exported_as(light_props(obj), f"{kind.lower()} light Max", False)
    bpy.data.objects.remove(obj)

scene = bpy.context.scene
world = bpy.data.worlds.new("temperature world")
world.luxcore.light = "sky2"
world.luxcore.color_mode = "temperature"
scene.world = world
exported_as(light_export.convert_world(EXPORTER, world, scene, False), "world Luminance", True)
world.luxcore.temperature_normalization = "max"
exported_as(light_export.convert_world(EXPORTER, world, scene, False), "world Max", False)

# A file of an older addon version: nothing stored
tree, node = blackbody_tree("old material")
material = bpy.data.materials.new("old material")
material.use_fake_user = True
material.luxcore.node_tree = tree
node.property_unset("normalization")
old_light = new_light("old light", "POINT")
old_rgb_light = new_light("old rgb light", "AREA")
old_rgb_light.data.luxcore.color_mode = "rgb"
for data in (old_light.data, old_rgb_light.data, world):
    data.luxcore.property_unset("temperature_normalization")
bpy.app.handlers.save_pre.remove(save_pre.handler)
bpy.ops.wm.save_as_mainfile(filepath=OLD)
bpy.app.handlers.save_pre.append(save_pre.handler)


def loaded_values():
    return (bpy.data.node_groups["old material"].nodes[0].normalization,
            bpy.data.lights["old light"].luxcore.temperature_normalization,
            bpy.data.lights["old rgb light"].luxcore.temperature_normalization,
            bpy.data.worlds["temperature world"].luxcore.temperature_normalization)


bpy.ops.wm.open_mainfile(filepath=OLD)
checks.check(loaded_values() == ("max",) * 4, f"old file loaded: Max on the node, lights and world {loaded_values()}")
exported_as(blackbody_props(bpy.data.node_groups["old material"].nodes[0])[0], "old Blackbody node", False)
exported_as(light_props(bpy.data.objects["old light"]), "old light", False)

# Saved now: the old data keeps Max, a light added since keeps Luminance
added = new_light("added light", "POINT")
checks.check(not added.data.luxcore.is_property_set("temperature_normalization"), "added light: nothing stored yet")
bpy.ops.wm.save_as_mainfile(filepath=NEW)
checks.check(added.data.luxcore.is_property_set("temperature_normalization"), "added light: stored on save")
bpy.ops.wm.open_mainfile(filepath=NEW)
checks.check(loaded_values() == ("max",) * 4
             and bpy.data.lights["added light"].luxcore.temperature_normalization == "luminance",
             f"new file loaded: old data Max {loaded_values()}, added light "
             f"{bpy.data.lights['added light'].luxcore.temperature_normalization}")

# Appended from the old file (with the operator and with libraries.load), and linked
bpy.ops.wm.append(directory=OLD + "/Light/", filename="old light")
bpy.ops.wm.append(directory=OLD + "/Material/", filename="old material")
appended_light = bpy.data.lights["old light.001"]
appended_tree = bpy.data.materials["old material.001"].luxcore.node_tree
checks.check(appended_light.luxcore.temperature_normalization == "max", "appended light: Max")
checks.check(appended_tree is not None and appended_tree.nodes[0].normalization == "max",
             f"appended material: its Blackbody node Max ({appended_tree and appended_tree.name})")
with bpy.data.libraries.load(OLD, link=False) as (data_from, data_to):
    data_to.lights = ["old rgb light"]
checks.check(data_to.lights[0].luxcore.temperature_normalization == "max",
             f"libraries.load() append: Max ({data_to.lights[0].name})")
with bpy.data.libraries.load(OLD, link=True) as (data_from, data_to):
    data_to.node_groups = ["old material"]
linked = data_to.node_groups[0]
exported_as(blackbody_props(linked.nodes[0])[0], f"linked Blackbody node ({linked.library is not None})", False)

# Brightness: Cycles' Emission with a Blackbody color, converted to LuxCore nodes, against Cycles;
# the Temperature mode of a LuxCore light against Blender's light color
scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)
bpy.ops.mesh.primitive_plane_add(size=40)
ground = bpy.context.object
diffuse_material = bpy.data.materials.new("ground")
if bpy.app.version < (5, 0, 0):
    diffuse_material.use_nodes = True
nodes = diffuse_material.node_tree.nodes
for n in list(nodes):
    nodes.remove(n)
output = nodes.new("ShaderNodeOutputMaterial")
diffuse = nodes.new("ShaderNodeBsdfDiffuse")
diffuse.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1)
diffuse_material.node_tree.links.new(diffuse.outputs[0], output.inputs[0])
ground.data.materials.append(diffuse_material)
diffuse_material.luxcore.use_cycles_nodes = True

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


def center(pixels):
    h, w = pixels.shape[:2]
    return pixels[h // 2 - 2:h // 2 + 2, w // 2 - 2:w // 2 + 2, :3].reshape(-1, 3).mean(axis=0)


def ratio_text(r):
    return ", ".join(f"{c:.3f}" for c in r)


# A small emitting plane above the ground, hidden from the camera
bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 2), rotation=(3.14159, 0, 0))
emitter = bpy.context.object
emitter.visible_camera = False
emission = bpy.data.materials.new("blackbody emission")
if bpy.app.version < (5, 0, 0):
    emission.use_nodes = True
tree = emission.node_tree
for n in list(tree.nodes):
    tree.nodes.remove(n)
out = tree.nodes.new("ShaderNodeOutputMaterial")
em = tree.nodes.new("ShaderNodeEmission")
em.inputs["Strength"].default_value = 50
bb = tree.nodes.new("ShaderNodeBlackbody")
bb.inputs["Temperature"].default_value = 6500
tree.links.new(bb.outputs[0], em.inputs["Color"])
tree.links.new(em.outputs[0], out.inputs[0])
emitter.data.materials.append(emission)

cycles = center(harness.render(scene, "CYCLES", os.path.join(OUT, "bb_cycles.exr"), 128))
converted, _ = convert_material(emission)
assign_converted_tree(emission, converted)
lux_node = next(n for n in converted.nodes if n.bl_idname == "LuxCoreNodeTexBlackbody")
checks.check(lux_node.normalization == "luminance", "converted Blackbody node: Luminance")
luminance = center(harness.render(scene, "LUXCORE", os.path.join(OUT, "bb_luminance.exr"), 128))
lux_node.normalization = "max"
legacy = center(harness.render(scene, "LUXCORE", os.path.join(OUT, "bb_max.exr"), 128))
if np.allclose(luminance, legacy, rtol=0.005):
    print(f"SKIP renders: pyluxcore {pyluxcore.Version()} has no .normalization (before 2026-10-23)")
else:
    r = luminance / cycles
    checks.check(np.all(abs(r - 1) < 0.03), f"converted 6500 K Blackbody, Luminance: LuxCore / Cycles = {ratio_text(r)}")
    r = legacy / cycles
    checks.check(np.all(abs(r - 0.72) < 0.03), f"converted 6500 K Blackbody, Max: LuxCore / Cycles = {ratio_text(r)} (0.72 expected)")

    # A LuxCore point light (artistic units): Temperature against a white light gives Blender's light color
    bpy.data.objects.remove(emitter)
    lamp = new_light("lamp", "POINT")
    lamp.location = (0, 0, 2)
    lamp.data.luxcore.light_unit = "artistic"
    lamp.data.luxcore.gain = 20
    for t in (6500, 3000):
        lamp.data.luxcore.color_mode = "rgb"
        white = center(harness.render(scene, "LUXCORE", os.path.join(OUT, f"white.exr"), 64))
        lamp.data.luxcore.color_mode = "temperature"
        lamp.data.luxcore.temperature = t
        lamp.data.luxcore.temperature_normalization = "luminance"
        colored = center(harness.render(scene, "LUXCORE", os.path.join(OUT, f"t{t}.exr"), 64))
        lamp.data.use_temperature = True
        lamp.data.temperature = t
        expected = np.array(lamp.data.temperature_color)
        print("Blender's light color", t, expected)
        r = colored / white / expected
        checks.check(np.all(abs(r - 1) < 0.01),
                     f"{t} K Temperature light / white light / Blender's color = {ratio_text(r)}")

checks.finish()
