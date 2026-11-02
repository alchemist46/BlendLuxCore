""" BlendLuxCore operator classes to support the LuxCoreNodeIORPreset
    class in module BlendLuxCore/nodes/textures/iorpreset"""
# <pep8 compliant>
import bpy
from bpy.props import EnumProperty, StringProperty, IntProperty
from ..utils.ior_presets import LuxCoreIORPresetValues


# It seems that Blender *Property classes do not support inheritance if
# there are methods in the same class. The following classes
# (LuxCoreIORPresetCommonProperties, LuxCoreIORPresetBase,
#  LUXCORE_OT_ior_preset_names  and LUXCORE_OT_ior_preset_values) are an
# attempt at minimizing code duplication within the limits imposed by Blender.
class LuxCoreIORPresetCommonProperties():
    """ A property-only class to work around Blender's inheritance limits. """
    bl_idname = "luxcore.ior_preset_common_properties"
    bl_label = ""
    node_name: StringProperty()
    node_tree_index: IntProperty()


class LuxCoreIORPresetBase(LuxCoreIORPresetCommonProperties):
    """ A base class for LUXCORE_OT_ior_preset_* common methods. Do not call"""

    bl_idname = "luxcore.ior_preset_base"

    def cb_ior_preset(self, context):
        return []

    ior_preset: EnumProperty(name="IOR Preset",
                              description="Index of Refraction Preset Values",
                              items=cb_ior_preset)

    def execute(self, context):
        ior_name, ior_value, abbe = LuxCoreIORPresetValues.get_item(self.ior_preset)
        node = bpy.data.node_groups[self.node_tree_index].nodes[self.node_name]
        node.ior_name_text = ior_name
        node.ior_value_text = str(ior_value)
        # Set before ior_value_float, whose update callback writes the label
        node.abbe_number = abbe or 0
        node.ior_value_float = ior_value
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'FINISHED'}


class LUXCORE_OT_ior_preset_names(bpy.types.Operator,
                                  LuxCoreIORPresetBase,
                                  LuxCoreIORPresetCommonProperties):
    """ A custom operator to return a list of IOR presets sorted by name """

    bl_idname = "luxcore.ior_preset_names"
    bl_description = "Index of Refraction presets sorted by name"
    bl_property = "ior_preset"

    callback_strings = []

    def cb_ior_preset(self, context):
        items = LuxCoreIORPresetValues.get_sorted_list("name")
        # There is a known bug with using a callback,
        # Python must keep a reference to the strings
        # returned or Blender will misbehave or even crash.
        LUXCORE_OT_ior_preset_names.callback_strings = items
        return items

    ior_preset: EnumProperty(name="IOR Preset",
                              description="Index of Refraction Preset Values",
                              items=cb_ior_preset)


class LUXCORE_OT_ior_preset_values(bpy.types.Operator,
                                   LuxCoreIORPresetBase,
                                   LuxCoreIORPresetCommonProperties):
    """ A custom operator to return a list of IOR presets sorted by value """

    bl_idname = "luxcore.ior_preset_values"
    bl_description = "Index of Refraction presets sorted by value"
    bl_property = "ior_preset"

    callback_strings = []

    def cb_ior_preset(self, context):
        items = LuxCoreIORPresetValues.get_sorted_list("value")
        # There is a known bug with using a callback,
        # Python must keep a reference to the strings
        # returned or Blender will misbehave or even crash.
        LUXCORE_OT_ior_preset_values.callback_strings = items
        return items

    ior_preset: EnumProperty(name="IOR Preset",
                              description="Index of Refraction Preset Values",
                              items=cb_ior_preset)
