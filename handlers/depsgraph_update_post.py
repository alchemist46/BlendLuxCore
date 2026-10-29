import bpy
from bpy.app.handlers import persistent
from .. import utils

@persistent
def handler(scene, depsgraph=None):
    # The working colour space may have changed (Blender's Working Space
    # selector): convert the LuxCore colours as Blender converted its own
    utils.colorspace.sync_luxcore_colors()

    # If material name was changed, rename the node tree, too. Not when another
    # tree has the name: the rename would keep the old name, but tag the tree
    # as updated, in every depsgraph update
    for mat in bpy.data.materials:
        node_tree = mat.luxcore.node_tree

        if node_tree and node_tree.name != mat.name and mat.name not in bpy.data.node_groups:
            node_tree.name = mat.name

    # Safety net for the EEVEE node sync: LuxCore node tree changes that the
    # node and socket update callbacks did not report. Material updates are
    # ignored, so writing the generated Cycles nodes does not come back here.
    if depsgraph is not None and depsgraph.id_type_updated("NODETREE"):
        for update in depsgraph.updates:
            node_tree = update.id
            if (isinstance(node_tree, bpy.types.NodeTree)
                    and node_tree.bl_idname in utils.eevee_sync.LUXCORE_TREE_TYPES):
                utils.eevee_sync.mark_tree(getattr(node_tree, "original", node_tree))
