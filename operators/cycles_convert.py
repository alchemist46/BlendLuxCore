"""Operators that convert the Cycles nodes of materials into LuxCore node trees.

The conversion itself is in cycles_converter.py. The Cycles node tree of a
material is never changed here; with "Keep EEVEE Nodes in Sync" it is replaced
by generated EEVEE nodes only after the LuxCore tree is first edited (see
utils/eevee_sync.py), and "Back Up Cycles Nodes" keeps a hidden copy first.
"""

import traceback

import bpy
from bpy.props import BoolProperty

from .. import icons
from ..utils import ui as utils_ui
from ..utils import eevee_sync
from ..utils.errorlog import LuxCoreErrorLog
from ..utils.node import uses_nodes
from .cycles_converter import convert_material
from .utils import poll_material, show_nodetree

SYNC_EEVEE_DESCRIPTION = (
    "Keep the Cycles/EEVEE nodes of the material in sync with the new LuxCore node tree: "
    "they are replaced by generated nodes when the LuxCore tree is first edited"
)
BACKUP_DESCRIPTION = (
    "Keep a copy of the original Cycles nodes in a hidden material "
    '(".<material>.cycles_original") before they are ever replaced'
)


def assign_converted_tree(material, node_tree):
    """Make node_tree the material's LuxCore tree, stop using the Cycles nodes, drop the old tree"""
    old_tree = material.luxcore.node_tree
    material.luxcore.node_tree = node_tree
    # Without the update callback, which only switches the node editor
    material.luxcore["use_cycles_nodes"] = False
    # MaterialCache.diff() re-exports the material in a running viewport render
    material.update_tag()

    if old_tree is None or old_tree == node_tree or old_tree.library:
        return
    if any(mat.luxcore.node_tree == old_tree for mat in bpy.data.materials):
        # Another material still uses it
        return
    bpy.data.node_groups.remove(old_tree)


def needs_conversion(material, overwrite_existing=False):
    """Materials the bulk operator converts: local ones with Cycles nodes that render
    with them, or have no LuxCore tree, or any when overwrite_existing is set"""
    if (material.library or getattr(material, "is_grease_pencil", False) or not uses_nodes(material)
            or eevee_sync.is_backup(material)):
        return False
    return (material.luxcore.use_cycles_nodes
            or material.luxcore.node_tree is None
            or overwrite_existing)


def candidates(overwrite_existing=False):
    return [mat for mat in bpy.data.materials if needs_conversion(mat, overwrite_existing)]


def convert_and_assign(material, sync_eevee, backup):
    """Convert, assign and report to the error log. Returns the ConversionResult."""
    node_tree, result = convert_material(material)
    assign_converted_tree(material, node_tree)
    if sync_eevee:
        eevee_sync.adopt(material, backup=backup)
    for message in result.warnings:
        LuxCoreErrorLog.add_warning(message, obj_name=material.name)
    return result


class LUXCORE_OT_material_convert_cycles(bpy.types.Operator):
    bl_idname = "luxcore.material_convert_cycles"
    bl_label = "Convert Cycles Nodes to LuxCore"
    bl_description = ("Create a LuxCore node tree from the Cycles nodes of this material and render "
                      "with it; the Cycles nodes are kept")
    bl_options = {"REGISTER", "UNDO"}

    replace_existing: BoolProperty(name="Replace Existing Tree", default=False, options={"SKIP_SAVE"},
                                   description="Replace the LuxCore node tree this material already has")
    sync_eevee: BoolProperty(name="Keep EEVEE Nodes in Sync", default=True, description=SYNC_EEVEE_DESCRIPTION)
    backup_cycles_nodes: BoolProperty(name="Back Up Cycles Nodes", default=True, description=BACKUP_DESCRIPTION)

    @classmethod
    def poll(cls, context):
        return poll_material(context) and uses_nodes(context.material)

    def invoke(self, context, event):
        node_tree = context.material.luxcore.node_tree
        if node_tree and not self.replace_existing:
            self.replace_existing = True
            return context.window_manager.invoke_confirm(
                self, event,
                title="Replace the LuxCore node tree?",
                message=f'"{node_tree.name}" is replaced by a tree converted from the Cycles nodes',
                confirm_text="Replace",
                icon="WARNING",
            )
        return self.execute(context)

    def execute(self, context):
        mat = context.material
        if mat.luxcore.node_tree and not self.replace_existing:
            self.report({"WARNING"}, f'Material "{mat.name}" already has a LuxCore node tree, '
                                     'enable "Replace Existing Tree" to replace it')
            return {"CANCELLED"}

        try:
            result = convert_and_assign(mat, self.sync_eevee, self.backup_cycles_nodes)
        except Exception as error:
            traceback.print_exc()
            self.report({"ERROR"}, f'Material "{mat.name}": {error}')
            return {"CANCELLED"}

        if result.warnings:
            self.report({"WARNING"}, f'Material "{mat.name}" converted with {len(result.warnings)} warnings, '
                                     'see the LuxCore error log and the labelled nodes')
        else:
            self.report({"INFO"}, f'Material "{mat.name}" converted ({result.nodes} nodes)')

        if context.screen is not None:
            show_nodetree(context, mat.luxcore.node_tree)
        return {"FINISHED"}


class LUXCORE_OT_convert_all_cycles_materials(bpy.types.Operator):
    bl_idname = "luxcore.convert_all_cycles_materials"
    bl_label = "Convert All Cycles Materials"
    bl_description = ("Create LuxCore node trees from the Cycles nodes of all materials that render with "
                      "them or have no LuxCore node tree; linked library materials are skipped")
    bl_options = {"REGISTER", "UNDO"}

    overwrite_existing: BoolProperty(name="Replace Existing Trees", default=False,
                                     description="Also convert the materials that already render with a "
                                                 "LuxCore node tree, replacing it")
    sync_eevee: BoolProperty(name="Keep EEVEE Nodes in Sync", default=True, description=SYNC_EEVEE_DESCRIPTION)
    backup_cycles_nodes: BoolProperty(name="Back Up Cycles Nodes", default=True, description=BACKUP_DESCRIPTION)

    @classmethod
    def poll(cls, context):
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout
        count = len(candidates(self.overwrite_existing))
        skipped = len(candidates(True)) - len(candidates(False))
        layout.label(text=f"{count} materials to convert", icon=icons.NODETREE)
        if skipped and not self.overwrite_existing:
            layout.label(text=f"{skipped} skipped: they already render with a LuxCore node tree",
                         icon=icons.INFO)
        layout.prop(self, "overwrite_existing")
        layout.prop(self, "sync_eevee")
        row = layout.row()
        row.active = self.sync_eevee
        row.prop(self, "backup_cycles_nodes")

    def execute(self, context):
        materials = candidates(self.overwrite_existing)
        if not materials:
            self.report({"INFO"}, "No materials to convert")
            return {"FINISHED"}

        converted = 0
        warnings = 0
        failed = 0
        for mat in materials:
            try:
                result = convert_and_assign(mat, self.sync_eevee, self.backup_cycles_nodes)
            except Exception as error:
                traceback.print_exc()
                LuxCoreErrorLog.add_warning(f"Conversion failed: {error}", obj_name=mat.name)
                failed += 1
                continue
            converted += 1
            warnings += len(result.warnings)

        message = f"Converted {converted} materials, {warnings} warnings"
        if failed:
            message += f", {failed} failed"
        self.report({"WARNING"} if warnings or failed else {"INFO"}, message)
        utils_ui.tag_region_for_redraw(context, "PROPERTIES", "WINDOW")
        return {"FINISHED"}
