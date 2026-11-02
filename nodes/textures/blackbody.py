import bpy
from bpy.props import FloatProperty, BoolProperty, EnumProperty
from ..base import LuxCoreNodeTexture
from ...utils import node as utils_node
from ...utils import temperature as utils_temperature


class LuxCoreNodeTexBlackbody(LuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Blackbody"
    bl_width_default = 200

    temperature: FloatProperty(update=utils_node.force_viewport_update, name="Temperature", description="Blackbody Temperature in Kelvin",
                               default=6500, min=0, soft_max=13000, step=100, precision=0)

    normalize: BoolProperty(update=utils_node.force_viewport_update, name="Normalize", default=True,
                            description="Bring output from 0-89159.6 to 0-1 range")
    # Max on the nodes of files saved by older addon versions (utils/temperature.py)
    normalization: EnumProperty(update=utils_node.force_viewport_update, name="Normalization",
                                items=utils_temperature.NORMALIZATION_ITEMS, default="luminance",
                                description="How the blackbody color is normalized")

    def init(self, context):
        self.outputs.new("LuxCoreSocketColor", "Color")
        # Stored, so the node is not taken for one of an older addon version
        utils_temperature.store(self, utils_temperature.NODE_PROP, "luminance")

    def draw_buttons(self, context, layout):
        layout.prop(self, "temperature", slider=True)
        layout.prop(self, "normalize")
        if self.normalize:
            layout.prop(self, "normalization", text="")

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        definitions = {
            "type": "blackbody",
            "temperature": self.temperature,
        }
        if self.normalize:
            utils_temperature.export(definitions, "",
                                     utils_temperature.get(self, utils_temperature.NODE_PROP))
        else:
            definitions["normalize"] = False

        return self.create_props(props, definitions, luxcore_name)
