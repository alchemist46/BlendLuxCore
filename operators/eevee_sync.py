"""Operators that generate the Cycles/EEVEE nodes of LuxCore materials (see utils/eevee_nodes.py)"""

import bpy
from ..utils import eevee_nodes, eevee_sync
from ..utils.ui import tag_region_for_redraw


def _active_material(context):
    material = getattr(context, "material", None)
    if material is None and getattr(context, "object", None) is not None:
        material = context.object.active_material
    return material


def can_generate(material):
    """True if a material renders with a LuxCore node tree that EEVEE nodes can be made from"""
    return (material is not None
            and not material.library
            and material.luxcore.node_tree is not None
            and not material.luxcore.use_cycles_nodes)


class LUXCORE_OT_material_update_eevee_nodes(bpy.types.Operator):
    bl_idname = "luxcore.material_update_eevee_nodes"
    bl_label = "Update EEVEE Nodes"
    bl_description = ("Generate the Cycles/EEVEE nodes of this material from its LuxCore node tree, "
                      "so Material Preview and EEVEE show an approximation of it")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return can_generate(_active_material(context))

    def invoke(self, context, event):
        material = _active_material(context)
        if not eevee_nodes.is_ours(material):
            return context.window_manager.invoke_confirm(
                self, event,
                title="Overwrite EEVEE nodes?",
                message=f'The Cycles nodes of "{material.name}" were made by hand, '
                        "they are replaced by nodes generated from the LuxCore node tree",
                confirm_text="Overwrite",
                icon="WARNING",
            )
        return self.execute(context)

    def execute(self, context):
        material = _active_material(context)
        status = eevee_nodes.generate(material, force=True)
        eevee_sync.discard(material)
        if status.startswith("skipped"):
            self.report({"WARNING"}, f'Material "{material.name}": no EEVEE nodes generated ({status})')
            return {"CANCELLED"}
        self.report({"INFO"}, f'Material "{material.name}": EEVEE nodes {status}')
        return {"FINISHED"}


class LUXCORE_OT_sync_eevee_nodes_everywhere(bpy.types.Operator):
    bl_idname = "luxcore.sync_eevee_nodes_everywhere"
    bl_label = "Sync EEVEE Nodes on All Materials"
    bl_description = ("Generate EEVEE nodes from the LuxCore node tree of every material whose Cycles nodes "
                      "are the default or were generated before, and keep them in sync; materials with "
                      "hand-made Cycles nodes are left alone")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        generated = 0
        skipped = 0
        for material in list(bpy.data.materials):
            if not can_generate(material):
                continue
            status = eevee_sync.enable(material)
            if status.startswith("skipped"):
                skipped += 1
            else:
                generated += 1

        if generated == 0 and skipped == 0:
            self.report({"INFO"}, "No LuxCore materials found")
        else:
            message = f"EEVEE nodes generated for {generated} materials"
            if skipped:
                message += f", {skipped} skipped (hand-made Cycles nodes)"
            self.report({"WARNING"} if skipped else {"INFO"}, message)
        tag_region_for_redraw(context, "PROPERTIES", "WINDOW")
        return {"FINISHED"}
