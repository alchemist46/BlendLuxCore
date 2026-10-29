"""Node properties set by an update callback without running the other callback.

In Blender 5, node["name"] = value creates a custom property and leaves the
bpy.props value unchanged; utils.node.set_silently() sets the value itself.

blender -b --factory-startup --python tests/test_node_silent_props.py -- <site-packages>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
from blendluxcore.nodes.textures.constfloat3 import linear_to_srgb

checks = harness.Checks("test_node_silent_props")
bpy.context.scene.render.engine = "LUXCORE"
blender_5 = bpy.app.version >= (5, 0, 0)

tree = bpy.data.node_groups.new("silent", "luxcore_material_nodes")


def close(a, b):
    return all(abs(x - y) < 1e-5 for x, y in zip(a, b))


def no_custom_prop(owner, *names):
    # Blender 4.5 keeps the bpy.props values with the custom properties
    return not blender_5 or not any(name in owner.keys() for name in names)


# 2D Mapping: Uniform Scale sets U and V
mapping = tree.nodes.new("LuxCoreNodeTexMapping2D")
mapping.uniform_scale = 3
checks.check(mapping.uscale == 3 and mapping.vscale == 3,
             f"2D Mapping: uniform scale 3 gives U {mapping.uscale}, V {mapping.vscale}")
checks.check(no_custom_prop(mapping, "uscale", "vscale"), "2D Mapping: no custom property")

# Constant Color: the HSV values follow the color, and the other way round
color = tree.nodes.new("LuxCoreNodeTexConstfloat3")
color.value = (0.2, 0.5, 0.8)
checks.check(close(color.value_hsv, linear_to_srgb(color.value).hsv),
             f"Constant Color: HSV {tuple(color.value_hsv)} follows the color")
color.value_hsv = (0.0, 1.0, 1.0)
checks.check(close(color.value, (1, 0, 0)), f"Constant Color: HSV red gives {tuple(color.value)}")
checks.check(no_custom_prop(color, "value", "value_hsv"), "Constant Color: no custom property")

# ColorRamp: the add and remove buttons switch back off
band = tree.nodes.new("LuxCoreNodeTexBand")
count = len(band.ramp_items)
band.add_item = True
checks.check(len(band.ramp_items) == count + 1 and not band.add_item,
             f"ColorRamp: Add gives {len(band.ramp_items)} items (from {count}), button {band.add_item}")
band.remove_item = True
checks.check(len(band.ramp_items) == count and not band.remove_item,
             f"ColorRamp: Remove gives {len(band.ramp_items)} items, button {band.remove_item}")
checks.check(no_custom_prop(band, "add_item", "remove_item"), "ColorRamp: no custom property")

# Node tree: refresh switches back off
tree.refresh = True
checks.check(not tree.refresh, f"node tree: refresh {tree.refresh} after a refresh")
checks.check(no_custom_prop(tree, "refresh"), "node tree: no custom property")

checks.finish()
