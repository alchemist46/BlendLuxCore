"""A BlendLuxCore node giving the two colors of a metal for the OpenPBR node:
    the Fresnel node's metal presets as F0 and edge tint"""
# <pep8 compliant>
import bpy
from bpy.props import EnumProperty, FloatVectorProperty
from ..base import LuxCoreNodeTexture
from ..sockets import FLOAT_UI_PRECISION
from .fresnel import LuxCoreNodeTexFresnel
from ... import icons
from ...utils import node as utils_node
from ...utils.colorspace import get_working_space
from ...utils.metal_presets import get_preset, nk_to_f82


class LuxCoreNodeTexMetalPreset(LuxCoreNodeTexture, bpy.types.Node):
    """The colors of a metal (F0 and the F82 edge tint), to give the OpenPBR
    node the reflectance of the Metal node with a Fresnel texture"""
    bl_label = "Metal Preset"
    bl_width_default = 220

    def update_label(self, context):
        if self.input_type == "preset":
            names = {item[0]: item[1] for item in LuxCoreNodeTexFresnel.preset_items}
            self.label = "Metal: " + names.get(self.preset, self.preset)
        else:
            self.label = "Metal: Custom n, k"
        utils_node.force_viewport_update(self, context)

    input_type_items = [
        ("preset", "Preset", "A metal preset of the Fresnel node", 0),
        ("custom_nk", "Custom n, k Values", "The complex index of refraction of the metal. "
                                            "There's no check if the entered values are physically plausible", 1),
    ]
    input_type: EnumProperty(name="Type", description="Input Type", items=input_type_items, default="preset",
                             update=update_label)
    preset: EnumProperty(name="Preset", description="NK data presets", items=LuxCoreNodeTexFresnel.preset_items,
                         default="gold", update=update_label)
    n: FloatVectorProperty(name="n", min=0, soft_max=100,
                           default=[1.0, 0.7, 0.5],
                           description="Refractive index. Left to right: RGB",
                           update=utils_node.force_viewport_update,
                           precision=FLOAT_UI_PRECISION)
    k: FloatVectorProperty(name="k", min=0, soft_max=100,
                           default=[10.0, 7.0, 5.0],
                           description="Extinction coefficient. Left to right: RGB",
                           update=utils_node.force_viewport_update,
                           precision=FLOAT_UI_PRECISION)

    def init(self, context):
        # To the OpenPBR inputs of the same names (with Metalness 1)
        self.outputs.new("LuxCoreSocketColor", "Color")
        self.outputs.new("LuxCoreSocketColor", "Specular Color")
        self.update_label(context)

    def get_colors(self):
        """F0 and the F82 edge tint in the working space of the file"""
        if self.input_type == "custom_nk":
            return nk_to_f82(self.n, self.k)
        return get_preset(self.preset, get_working_space())

    def draw_buttons(self, context, layout):
        layout.prop(self, "input_type")

        if self.input_type == "preset":
            layout.prop(self, "preset")
        else:
            layout.label(text="Not checked for physical plausibility!", icon=icons.WARNING)
            layout.prop(self, "n")
            layout.prop(self, "k")

        color, tint = self.get_colors()
        col = layout.column(align=True)
        col.label(text="Color {:.3f} {:.3f} {:.3f}".format(*color))
        col.label(text="Specular Color {:.3f} {:.3f} {:.3f}".format(*tint))

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        color, tint = self.get_colors()
        # The second output was called "Edge Tint" before
        value = color if not output_socket or output_socket.name == "Color" else tint
        definitions = {
            "type": "constfloat3",
            "value": list(value),
        }
        return self.create_props(props, definitions, luxcore_name)
