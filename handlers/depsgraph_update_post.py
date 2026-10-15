import bpy
from bpy.app.handlers import persistent
from .. import utils

@persistent
def handler(scene):
    # The working colour space may have changed (Blender's Working Space
    # selector): convert the LuxCore colours as Blender converted its own
    utils.colorspace.sync_luxcore_colors()

    # If material name was changed, rename the node tree, too.
    for mat in bpy.data.materials:
        node_tree = mat.luxcore.node_tree

        if node_tree and node_tree.name != mat.name:
            node_tree.name = mat.name
