"""Which viewport updates the object and world caches see as changes.

Blender's depsgraph reports id_type_updated("OBJECT") and ("WORLD") after a
scene property with an update callback is set, and WORLD after any edit: the
caches look for an updated object, or the world or one of its node trees.
A material whose node tree cannot take its name (another tree has it) no
longer gets its tree tagged in every depsgraph update.

blender -b --factory-startup --python tests/test_viewport_diff.py -- <site-packages>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
from blendluxcore.export import caches

checks = harness.Checks("test_viewport_diff")
scene = bpy.context.scene
scene.render.engine = "LUXCORE"
world = scene.world
cube = scene.objects["Cube"]
light = scene.objects["Light"]

volume = bpy.data.node_groups.new("world volume", "luxcore_volume_nodes")
clear = volume.nodes.new("LuxCoreNodeVolClear")
volume_output = volume.nodes.new("LuxCoreNodeVolOutput")
world.luxcore.volume = volume

# A material named like another node tree: its tree keeps the name "plastic.001"
bpy.data.node_groups.new("plastic", "luxcore_material_nodes")
material = bpy.data.materials.new("plastic")
material.luxcore.node_tree = bpy.data.node_groups.new("plastic", "luxcore_material_nodes")
cube.active_material = material

object_cache = caches.ObjectCache2()
world_cache = caches.WorldCache()
seen = []


def handler(_, depsgraph):
    seen.append((object_cache.diff(depsgraph), world_cache.diff(depsgraph),
                 [(type(u.id).__name__, u.id.name) for u in depsgraph.updates]))


# The first update stores the world's name in the cache
bpy.context.view_layer.update()
world_cache.diff(bpy.context.evaluated_depsgraph_get())
bpy.app.handlers.depsgraph_update_post.append(handler)


def case(what, edit, expected_object, expected_world):
    seen.clear()
    edit()
    bpy.context.view_layer.update()
    got_object = any(o for o, _, _ in seen)
    got_world = any(w for _, w, _ in seen)
    checks.check(seen and expected_object in (None, got_object) and got_world == expected_world,
                 f"{what}: object {got_object}, world {got_world}, updates {[u for _, _, u in seen]}")


def halt_time_and_tree():
    # Lists the material's node tree, and no object (as the rename above did)
    scene.luxcore.viewport.halt_time = 40
    material.luxcore.node_tree.name = "plastic tree"


case("viewport Halt Time", lambda: setattr(scene.luxcore.viewport, "halt_time", 30), False, False)
checks.check(seen and seen[-1][2] == [("Scene", "Scene")] and material.luxcore.node_tree.name == "plastic.001",
             "a material named like another node tree: its tree keeps its name and is not updated")
case("viewport Pixel Size", lambda: setattr(scene.luxcore.viewport, "pixel_size", "2"), False, False)
case("viewport Halt Time and a node tree rename", halt_time_and_tree, False, False)
case("object moved", lambda: setattr(cube, "location", (1, 0, 0)), True, False)
case("light power", lambda: setattr(light.data, "energy", 123), True, False)
case("world color", lambda: setattr(world, "color", (0.2, 0.3, 0.4)), False, True)
background = next(node for node in world.node_tree.nodes if node.type == "BACKGROUND")
case("world node", lambda: setattr(background.inputs["Strength"], "default_value", 0.7), False, True)
case("world volume node", lambda: setattr(clear, "color_depth", 2.0), None, True)
case("world volume link", lambda: volume.links.new(clear.outputs[0], volume_output.inputs[0]), None, True)
# Blender lists every object too
case("another world", lambda: setattr(scene, "world", bpy.data.worlds.new("other")), None, True)

checks.finish()
