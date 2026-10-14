import bpy
from bpy.props import FloatProperty, BoolProperty
from ..base import LuxCoreNodeMaterial
from ..sockets import LuxCoreSocketFloat
from ...utils import node as utils_node

SIGMA_DESCRIPTION = "Surface roughness, 0 for pure Lambertian reflection"

class LuxCoreSocketSigma(bpy.types.NodeSocket, LuxCoreSocketFloat):
    default_value: FloatProperty(min=0, max=45, description=SIGMA_DESCRIPTION,
                                 update=utils_node.force_viewport_update)
    slider = True


class LuxCoreNodeMatMatte(LuxCoreNodeMaterial, bpy.types.Node):
    """(Rough) matte material node"""
    bl_label = "Matte Material"
    bl_width_default = 160

    multiscattering: BoolProperty(name="Energy Compensation", default=False,
                                  description="Use the energy-preserving Oren-Nayar model (EON), which "
                                              "keeps rough surfaces from getting too dark. Only with a "
                                              "Sigma above 0, used as the roughness clamped to 0-1 "
                                              "(needs a pyluxcore build with EON support)",
                                  update=utils_node.force_viewport_update)

    def init(self, context):
        self.add_input("LuxCoreSocketColor", "Diffuse Color", (0.7, 0.7, 0.7))
        self.add_input("LuxCoreSocketSigma", "Sigma", 0)
        self.add_common_inputs()

        self.outputs.new("LuxCoreSocketMaterial", "Material")

    def draw_buttons(self, context, layout):
        layout.prop(self, "multiscattering")

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        sigma = self.inputs["Sigma"].export(exporter, depsgraph, props)
        mat_type = "matte" if sigma == 0 else "roughmatte"
        definitions = {
            "type": mat_type,
            "kd": self.inputs["Diffuse Color"].export(exporter, depsgraph, props),
        }
        if mat_type == "roughmatte":
            definitions["sigma"] = sigma
            # Only exported when enabled, so older pyluxcore builds get the same scene as before
            if self.multiscattering:
                definitions["multiscattering"] = True
        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, luxcore_name)
