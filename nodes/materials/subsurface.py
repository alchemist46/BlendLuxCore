import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty
from ..base import LuxCoreNodeMaterial
from ..sockets import LuxCoreNodeSocket, Color, FLOAT_UI_PRECISION
from ... import icons
from ... import utils
from ...utils import node as utils_node


def _get_radius(self):
    return max(self.default_value)


def _set_radius(self, value):
    # Scale the channels together, keeping their ratios
    current = max(self.default_value)
    if current > 0:
        self.default_value = [c * value / current for c in self.default_value]
    else:
        self.default_value = (value, value, value)


class LuxCoreSocketSubsurfaceRadius(bpy.types.NodeSocket, LuxCoreNodeSocket):
    """One distance, or expanded a distance per color channel"""
    color = Color.float_texture
    default_value: FloatVectorProperty(
        min=0,
        subtype="XYZ",
        unit="LENGTH",
        description="Mean free path per color channel in scene units, multiplied by the Scatter Color",
        update=utils_node.force_viewport_update,
        precision=FLOAT_UI_PRECISION,
    )
    radius: FloatProperty(
        name="Radius",
        min=0,
        subtype="DISTANCE",
        description="Mean free path in scene units, how far light travels in the medium before "
                    "it scatters, multiplied by the Scatter Color. Expand for a radius per color channel",
        get=_get_radius,
        set=_set_radius,
        update=utils_node.force_viewport_update,
        precision=FLOAT_UI_PRECISION,
    )
    expand: BoolProperty(default=False)

    def draw_prop(self, context, layout, node, text):
        row = layout.row(align=True)
        icon = icons.EXPANDABLE_OPENED if self.expand else icons.EXPANDABLE_CLOSED
        row.prop(self, "expand", text="", icon=icon, emboss=False)

        if self.expand:
            col = row.column(align=True)
            for i, channel in enumerate("RGB"):
                col.prop(self, "default_value", index=i, text=text + " " + channel)
        else:
            row.prop(self, "radius", text=text)

    def export_default(self):
        return list(self.default_value)


class LuxCoreNodeMatSubsurface(LuxCoreNodeMaterial, bpy.types.Node):
    """Random-walk subsurface scattering (pyluxcore 3.0), the walk of Cycles'
    Subsurface Scattering node under a glossy dielectric reflection.
    Color is the color seen at the surface (the multiple-scattering albedo).
    Scatter Color times Radius is the mean free path per color channel in
    scene units: the channels of the Scatter Color go deeper, so thin parts
    take its color. IOR 1 turns the reflection off. Only the path engines walk
    the medium (PATH, TILEPATH, RTPATH on the CPU and the GPU); the other
    engines render a diffuse base of the same color"""
    bl_label = "Subsurface Material"
    bl_width_default = 180

    def init(self, context):
        # inputs[0] gives the viewport color (utils.node.update_opengl_materials)
        self.add_input("LuxCoreSocketColor", "Color", (0.8, 0.8, 0.8))
        self.add_input("LuxCoreSocketColor", "Scatter Color", (1, 0.5, 0.25))
        self.add_input("LuxCoreSocketSubsurfaceRadius", "Radius", (0.05, 0.05, 0.05))
        self.add_input("LuxCoreSocketIOR", "IOR", 1.4)
        self.add_input("LuxCoreSocketFloat0to1", "Roughness", 0.3)
        self.add_input("LuxCoreSocketFloatUnbounded", "Anisotropy", 0)
        self.add_common_inputs()

        self.outputs.new("LuxCoreSocketMaterial", "Material")

    def export_mean_free_path(self, exporter, depsgraph, props, definitions):
        # The mean free path of pyluxcore is radius (per channel) * scale
        scatter_socket = self.inputs["Scatter Color"]
        radius_socket = self.inputs["Radius"]
        scatter = scatter_socket.export(exporter, depsgraph, props)
        radius = radius_socket.export(exporter, depsgraph, props)

        if not radius_socket.is_linked and max(radius) - min(radius) < 1e-6:
            definitions["radius"] = scatter
            definitions["scale"] = radius[0]
        elif not radius_socket.is_linked and not scatter_socket.is_linked:
            definitions["radius"] = [s * r for s, r in zip(scatter, radius)]
            definitions["scale"] = 1
        else:
            tex_name = self.make_name() + "_mfp"
            props.Set(utils.luxutils.create_props("scene.textures." + tex_name + ".", {
                "type": "scale",
                "texture1": scatter,
                "texture2": radius,
            }))
            definitions["radius"] = tex_name
            definitions["scale"] = 1

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        definitions = {
            "type": "subsurface",
            "color": self.inputs["Color"].export(exporter, depsgraph, props),
            "ior": self.inputs["IOR"].export(exporter, depsgraph, props),
            "roughness": self.inputs["Roughness"].export(exporter, depsgraph, props),
            "anisotropy": self.inputs["Anisotropy"].export(exporter, depsgraph, props),
        }
        self.export_mean_free_path(exporter, depsgraph, props, definitions)

        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, luxcore_name)
