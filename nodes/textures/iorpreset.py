import bpy
"""A BlendLuxCore node to provide index of refraction preset values for
    LuxCoreRender in Blender"""
# <pep8 compliant>
import bpy
from bpy.props import FloatProperty, StringProperty
from ..base import LuxCoreNodeTexture
from ...utils import node as utils_node
from ...utils.ior_presets import LuxCoreIORPresetValues, abbe_to_cauchy_b


class LuxCoreNodeTexIORPreset(LuxCoreNodeTexture, bpy.types.Node):
    """ Index of Refraction Preset node """
    bl_label = "IOR Preset"
    bl_width_default = 180

    ior_name_text: StringProperty(update=utils_node.force_viewport_update, name="IOR Name", description="The name of"
                                   " the selected Index of Refraction preset")
    ior_value_text: StringProperty(update=utils_node.force_viewport_update, name="IOR Value", description="The value "
                                    "of the selected Index of Refraction"
                                    " preset")

    def update_ior_value_float(self, context):
        # Change the node label to indicate the selected IOR preset
        if self.abbe_number > 0:
            self.label = "IOR: {} ({}, V {:.1f})".format(self.ior_name_text,
                                                       self.ior_value_text,
                                                       self.abbe_number)
        else:
            self.label = "IOR: {} ({})".format(self.ior_name_text,
                                               self.ior_value_text)
        utils_node.force_viewport_update(self, context)
        return None

    ior_value_float: FloatProperty(name="IOR Float",
                                    update=update_ior_value_float)

    abbe_number: FloatProperty(name="Abbe Number", default=0, min=0,
                                description="Abbe number of the selected preset, "
                                            "0 if there is no dispersion data")

    def init(self, context):
        self.label = "IOR Preset"
        self.outputs.new("LuxCoreSocketIOR", "IOR")
        self.outputs.new("LuxCoreSocketFloatPositive", "Dispersion")
        if not (self.ior_name_text and self.ior_value_text):
            item = LuxCoreIORPresetValues.get_item()
            self.ior_name_text = item[0]
            self.ior_value_text = str(item[1])
            self.abbe_number = item[2] or 0
            self.ior_value_float = item[1]

    def get_dispersion(self):
        """Cauchy B coefficient for the glass Dispersion input"""
        return abbe_to_cauchy_b(self.ior_value_float, self.abbe_number)

    def draw_buttons(self, context, layout):
        layout.alignment = "LEFT"

        # Alpha-sorting operator
        row = layout.row(align=False)
        row.scale_x = 1.1
        row.scale_y = 1.1
        row.prop(self, "ior_name_text", text="", emboss=False)
        op_alpha = row.operator("luxcore.ior_preset_names", icon="SORTALPHA")

        # Numeric-sorting operator
        row = layout.row(align=False)
        row.scale_x = 1.1
        row.scale_y = 1.1
        row.prop(self, "ior_value_text", text="Value", emboss=False)
        op_num = row.operator("luxcore.ior_preset_values", icon="SORTSIZE")

        row = layout.row()
        if self.abbe_number > 0:
            row.label(text="Abbe {:.2f}, dispersion {:.5f}".format(
                self.abbe_number, self.get_dispersion()))
        else:
            row.label(text="No dispersion data")

        # Get the index of the node tree in bpy.data.node_groups
        tree_index = None
        for index, tree in enumerate(bpy.data.node_groups):
            if tree == self.id_data:
                tree_index = index
                break

        for operator in [op_alpha, op_num]:
            operator.node_name = self.name
            operator.node_tree_index = tree_index

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        if output_socket and output_socket.name == "Dispersion":
            value = self.get_dispersion()
        else:
            value = self.ior_value_float
        definitions = {
            "type": "constfloat1",
            "value": value,
        }
        return self.create_props(props, definitions, luxcore_name)
