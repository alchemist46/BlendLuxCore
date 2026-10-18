import bpy
from ..base import LuxCoreNodeMaterial


class LuxCoreNodeMatVelvet(LuxCoreNodeMaterial, bpy.types.Node):
    """Microfiber sheen (Zeltner et al. 2022), the model of Cycles' Sheen BSDF.
    Older nodes keep their Thickness and p1-p3 sockets hidden (see
    utils/compatibility.py); the core ignores those parameters since
    pyluxcore 3.0"""
    bl_label = "Velvet Material"
    bl_width_default = 160

    def init(self, context):
        # inputs[0] gives the viewport color (utils.node.update_opengl_materials)
        self.add_input("LuxCoreSocketColor", "Diffuse Color", (1, 1, 1))
        self.add_input("LuxCoreSocketFloat0to1", "Roughness", 0.5)
        self.add_common_inputs()

        self.outputs.new("LuxCoreSocketMaterial", "Material")

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        definitions = {
            "type": "velvet",
            "kd": self.inputs["Diffuse Color"].export(exporter, depsgraph, props),
            "roughness": self.inputs["Roughness"].export(exporter, depsgraph, props),
        }

        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, luxcore_name)
