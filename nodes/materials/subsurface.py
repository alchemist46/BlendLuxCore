import bpy
from ..base import LuxCoreNodeMaterial


class LuxCoreNodeMatSubsurface(LuxCoreNodeMaterial, bpy.types.Node):
    """Random-walk subsurface scattering (pyluxcore 3.0), the walk of Cycles'
    Subsurface Scattering node under a glossy dielectric reflection.
    Color is the multiple-scattering albedo, Radius times Scale the mean free
    path per color channel in scene units. IOR 1 turns the reflection off.
    Only the path engines walk the medium (PATH, TILEPATH, RTPATH on the CPU
    and the GPU); the other engines render a diffuse base of the same color"""
    bl_label = "Subsurface Material"
    bl_width_default = 180

    def init(self, context):
        # inputs[0] gives the viewport color (utils.node.update_opengl_materials)
        self.add_input("LuxCoreSocketColor", "Color", (0.8, 0.8, 0.8))
        self.add_input("LuxCoreSocketFloatPositive", "Scale", 0.05)
        self.add_input("LuxCoreSocketVector", "Radius", (1, 0.2, 0.1))
        self.add_input("LuxCoreSocketIOR", "IOR", 1.4)
        self.add_input("LuxCoreSocketFloat0to1", "Roughness", 0.3)
        self.add_input("LuxCoreSocketFloatUnbounded", "Anisotropy", 0)
        self.add_common_inputs()

        self.outputs.new("LuxCoreSocketMaterial", "Material")

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        definitions = {
            "type": "subsurface",
            "color": self.inputs["Color"].export(exporter, depsgraph, props),
            "scale": self.inputs["Scale"].export(exporter, depsgraph, props),
            "radius": self.inputs["Radius"].export(exporter, depsgraph, props),
            "ior": self.inputs["IOR"].export(exporter, depsgraph, props),
            "roughness": self.inputs["Roughness"].export(exporter, depsgraph, props),
            "anisotropy": self.inputs["Anisotropy"].export(exporter, depsgraph, props),
        }

        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, luxcore_name)
