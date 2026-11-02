from bl_ui.space_node import NODE_HT_header, NODE_MT_editor_menus
from .material import lux_mat_template_ID
from ..utils.node import uses_nodes

original_draw = None


# Based on NODE_HT_header.draw() in bl_ui/space_node.py (Blender 2.92.0),
# reduced to the parts used by LuxCore material node trees
def lux_node_header_draw(panel, context):
    layout = panel.layout

    snode = context.space_data
    id_from = snode.id_from
    tool_settings = context.tool_settings
    types_that_support_material = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META',
                                   'GPENCIL', 'VOLUME', 'HAIR', 'POINTCLOUD'}

    layout.template_header()

    NODE_MT_editor_menus.draw_collapsible(context, layout)

    ob = context.object

    if ob:
        layout.separator_spacer()
        ob_type = ob.type

        has_material_slots = not snode.pin and ob_type in types_that_support_material

        row = layout.row()
        row.enabled = has_material_slots
        row.ui_units_x = 4
        row.popover(panel="NODE_PT_material_slots")

        row = layout.row()
        row.enabled = has_material_slots

        # id_from is the material the node tree is attached to
        mat = id_from if id_from else ob.active_material
        lux_mat_template_ID(row, mat)

        if mat and not mat.library:
            if mat.luxcore.use_cycles_nodes:
                layout.operator("luxcore.material_convert_cycles", icon="NODETREE", text="Convert Cycles Nodes")
            elif not mat.luxcore.node_tree:
                layout.operator("luxcore.mat_nodetree_new", icon="NODETREE", text="Use LuxCore Material Nodes")

    # Put pin next to ID block
    layout.prop(snode, "pin", text="", emboss=False)

    layout.separator_spacer()

    layout.operator("node.tree_path_parent", text="", icon='FILE_PARENT')

    # Snap
    row = layout.row(align=True)
    row.prop(tool_settings, "use_snap_node", text="")


def lux_draw_switch(panel, context):
    if context.scene.render.engine == "LUXCORE" and context.space_data.tree_type == "luxcore_material_nodes":
        lux_node_header_draw(panel, context)
    else:
        original_draw(panel, context)


def lux_shader_header_convert(panel, context):
    """A conversion button in the header of Blender's shader editor, showing the Cycles nodes"""
    space = context.space_data
    if (context.scene.render.engine != "LUXCORE" or space.tree_type != "ShaderNodeTree"
            or getattr(space, "shader_type", "OBJECT") != "OBJECT"):
        return
    mat = getattr(context, "material", None)
    if mat is None or mat.library or not uses_nodes(mat):
        return
    if mat.luxcore.node_tree and not mat.luxcore.use_cycles_nodes:
        # The LuxCore nodes are the source; these Cycles nodes may be generated from them
        return
    panel.layout.operator("luxcore.material_convert_cycles", icon="NODETREE", text="Convert to LuxCore Nodes")


def register():
    global original_draw
    original_draw = NODE_HT_header.draw
    NODE_HT_header.draw = lux_draw_switch
    NODE_HT_header.append(lux_shader_header_convert)


def unregister():
    NODE_HT_header.remove(lux_shader_header_convert)
    NODE_HT_header.draw = original_draw
