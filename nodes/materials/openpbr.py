import bpy
from bpy.props import BoolProperty, FloatProperty
from ..base import LuxCoreNodeMaterial
from ..sockets import LuxCoreSocketFloat, FLOAT_UI_PRECISION
from ... import icons
from ... import utils
from ...utils import node as utils_node

# The inputs: socket name, socket type, default, OpenPBR property, the group
# toggle that enables them (None: always on), the factor from the socket
# value to the property value. The defaults are the ones of the OpenPBR
# specification (v1.1): an input is exported only when it is linked or
# differs from its default, which pyluxcore uses for the inputs not set.
FILM_THICKNESS_NAME = "Thin Film Thickness (nm)"
INPUTS = (
    # inputs[0] gives the viewport color (utils.node.update_opengl_materials)
    ("Base Color", "LuxCoreSocketColor", (0.8, 0.8, 0.8), "base.color", None, 1),
    ("Base Weight", "LuxCoreSocketFloat0to1", 1, "base.weight", None, 1),
    ("Metalness", "LuxCoreSocketFloat0to1", 0, "base.metalness", None, 1),
    ("Diffuse Roughness", "LuxCoreSocketFloat0to1", 0, "base.diffuseroughness", None, 1),
    ("Specular Weight", "LuxCoreSocketOpenPBRSpecularWeight", 1, "specular.weight", None, 1),
    ("Specular Color", "LuxCoreSocketColor", (1, 1, 1), "specular.color", None, 1),
    ("Specular Roughness", "LuxCoreSocketFloat0to1", 0.3, "specular.roughness", None, 1),
    ("Specular IOR", "LuxCoreSocketIOR", 1.5, "specular.ior", None, 1),
    ("Specular Anisotropy", "LuxCoreSocketFloat0to1", 0, "specular.anisotropy", None, 1),
    ("Tangent Rotation", "LuxCoreSocketOpenPBRRotation", 0, "tangent.rotation", None, 1),
    ("Transmission Weight", "LuxCoreSocketFloat0to1", 0, "transmission.weight", "use_transmission", 1),
    ("Transmission Color", "LuxCoreSocketColor", (1, 1, 1), "transmission.color", "use_transmission", 1),
    ("Transmission Depth", "LuxCoreSocketOpenPBRDistance", 0, "transmission.depth", "use_transmission", 1),
    ("Transmission Scatter", "LuxCoreSocketColor", (0, 0, 0), "transmission.scatter", "use_transmission", 1),
    ("Scatter Anisotropy", "LuxCoreSocketOpenPBRAnisotropy", 0, "transmission.scatteranisotropy",
     "use_transmission", 1),
    ("Subsurface Weight", "LuxCoreSocketFloat0to1", 0, "subsurface.weight", "use_subsurface", 1),
    ("Subsurface Color", "LuxCoreSocketColor", (0.8, 0.8, 0.8), "subsurface.color", "use_subsurface", 1),
    ("Subsurface Radius", "LuxCoreSocketOpenPBRDistance", 1, "subsurface.radius", "use_subsurface", 1),
    ("Radius Scale", "LuxCoreSocketColor", (1, 0.5, 0.25), "subsurface.radiusscale", "use_subsurface", 1),
    ("Subsurface Anisotropy", "LuxCoreSocketOpenPBRAnisotropy", 0, "subsurface.anisotropy", "use_subsurface", 1),
    ("Coat Weight", "LuxCoreSocketFloat0to1", 0, "coat.weight", "use_coat", 1),
    ("Coat Color", "LuxCoreSocketColor", (1, 1, 1), "coat.color", "use_coat", 1),
    ("Coat Roughness", "LuxCoreSocketFloat0to1", 0, "coat.roughness", "use_coat", 1),
    ("Coat Anisotropy", "LuxCoreSocketFloat0to1", 0, "coat.anisotropy", "use_coat", 1),
    ("Coat IOR", "LuxCoreSocketIOR", 1.6, "coat.ior", "use_coat", 1),
    ("Coat Darkening", "LuxCoreSocketFloat0to1", 1, "coat.darkening", "use_coat", 1),
    ("Fuzz Weight", "LuxCoreSocketFloat0to1", 0, "fuzz.weight", "use_fuzz", 1),
    ("Fuzz Color", "LuxCoreSocketColor", (1, 1, 1), "fuzz.color", "use_fuzz", 1),
    ("Fuzz Roughness", "LuxCoreSocketFloat0to1", 0.5, "fuzz.roughness", "use_fuzz", 1),
    ("Thin Film Weight", "LuxCoreSocketFloat0to1", 0, "thinfilm.weight", "use_thin_film", 1),
    # pyluxcore takes micrometres
    (FILM_THICKNESS_NAME, "LuxCoreSocketFilmThickness", 500, "thinfilm.thickness", "use_thin_film", 0.001),
    ("Thin Film IOR", "LuxCoreSocketFilmIOR", 1.4, "thinfilm.ior", "use_thin_film", 1),
)

# The weight that switching a group on sets to 1 when it is 0
GROUP_WEIGHTS = {
    "use_transmission": "Transmission Weight",
    "use_subsurface": "Subsurface Weight",
    "use_coat": "Coat Weight",
    "use_fuzz": "Fuzz Weight",
    "use_thin_film": "Thin Film Weight",
}

THIN_WALLED_DESCRIPTION = (
    "The surface is an infinitely thin shell with nothing inside (leaves, paper, "
    "soap bubbles): transmission and subsurface scattering pass through it without "
    "refraction, and the interior volume is ignored"
)


class LuxCoreSocketOpenPBRSpecularWeight(bpy.types.NodeSocket, LuxCoreSocketFloat):
    default_value: FloatProperty(
        min=0,
        soft_max=1,
        description="Multiplier of the dielectric Fresnel reflectance at normal incidence (F0); "
                    "values above 1 are allowed",
        update=utils_node.force_viewport_update,
        precision=FLOAT_UI_PRECISION,
    )
    slider = True


class LuxCoreSocketOpenPBRRotation(bpy.types.NodeSocket, LuxCoreSocketFloat):
    default_value: FloatProperty(
        soft_min=0,
        soft_max=1,
        description="Rotation of the anisotropy direction around the normal, in turns (1 = 360°)",
        update=utils_node.force_viewport_update,
        precision=FLOAT_UI_PRECISION,
    )
    slider = True


class LuxCoreSocketOpenPBRAnisotropy(bpy.types.NodeSocket, LuxCoreSocketFloat):
    default_value: FloatProperty(
        min=-1,
        max=1,
        description="Phase function anisotropy: negative scatters backward, positive forward, "
                    "0 in all directions alike",
        update=utils_node.force_viewport_update,
        precision=FLOAT_UI_PRECISION,
    )
    slider = True


class LuxCoreSocketOpenPBRDistance(bpy.types.NodeSocket, LuxCoreSocketFloat):
    default_value: FloatProperty(
        min=0,
        subtype="DISTANCE",
        description="Distance in scene units",
        update=utils_node.force_viewport_update,
        precision=FLOAT_UI_PRECISION,
    )


def _input(node, name):
    # Indexing node.inputs by name doesn't find the disabled sockets
    return node.inputs[node.inputs.find(name)]


def _differs(value, default):
    if isinstance(value, str):
        # A texture
        return True
    if isinstance(default, tuple):
        return any(abs(v - d) > 1e-6 for v, d in zip(value, default))
    return abs(value - default) > 1e-6


class LuxCoreNodeMatOpenPBR(LuxCoreNodeMaterial, bpy.types.Node):
    """OpenPBR Surface (Academy Software Foundation, v1.1, pyluxcore 3.0): one
    material for dielectrics, metals, glass, subsurface scattering, coat,
    fuzz (sheen) and thin film, with the parameters of the specification"""
    bl_label = "OpenPBR Material"
    bl_width_default = 220

    def _group_update(prop):
        def update(self, context):
            enabled = getattr(self, prop)
            for name, _, _, _, group, _ in INPUTS:
                if group == prop:
                    _input(self, name).enabled = enabled

            # Switching a group on means using it, which a weight of 0 doesn't
            weight = _input(self, GROUP_WEIGHTS[prop])
            if enabled and not weight.is_linked and weight.default_value == 0:
                weight.default_value = 1

            utils_node.force_viewport_update(self, context)
        return update

    use_transmission: BoolProperty(name="Transmission", default=False, update=_group_update("use_transmission"),
                                   description="Glass-like transmission through the dielectric base")
    use_subsurface: BoolProperty(name="Subsurface", default=False, update=_group_update("use_subsurface"),
                                 description="Random-walk subsurface scattering in the dielectric base")
    use_coat: BoolProperty(name="Coat", default=False, update=_group_update("use_coat"),
                           description="A clear or tinted dielectric layer on top of the base")
    use_fuzz: BoolProperty(name="Fuzz", default=False, update=_group_update("use_fuzz"),
                           description="A layer of microfibers on top of everything (sheen, velvet, dust)")
    use_thin_film: BoolProperty(name="Thin Film", default=False, update=_group_update("use_thin_film"),
                                description="An iridescent film on the base (oil, soap, oxide layers)")
    del _group_update
    thin_walled: BoolProperty(name="Thin-Walled", default=False, description=THIN_WALLED_DESCRIPTION,
                              update=utils_node.force_viewport_update)

    def init(self, context):
        for name, socket_type, default, _, group, _ in INPUTS:
            self.add_input(socket_type, name, default, enabled=group is None)
        self.add_common_inputs()

        self.outputs.new("LuxCoreSocketMaterial", "Material")

    def _interior_volume(self):
        node_tree = self.id_data
        active_output = utils_node.get_active_output(node_tree)
        if active_output:
            return utils_node.get_linked_node(active_output.inputs["Interior Volume"])
        return None

    def draw_buttons(self, context, layout):
        if not utils.luxutils.has_material_type("openpbr"):
            layout.label(text="Needs pyluxcore 3.0 or newer", icon=icons.ERROR)

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(self, "use_transmission", toggle=True)
        row.prop(self, "use_subsurface", toggle=True)
        row = col.row(align=True)
        row.prop(self, "use_coat", toggle=True)
        row.prop(self, "use_fuzz", toggle=True)
        row.prop(self, "use_thin_film", toggle=True)
        layout.prop(self, "thin_walled")

        if self.thin_walled and self._interior_volume():
            layout.label(text="Thin-walled: the interior volume is ignored", icon=icons.WARNING)
        elif self.use_transmission and self._interior_volume():
            depth = _input(self, "Transmission Depth")
            if depth.is_linked or depth.default_value > 0:
                layout.label(text="The interior volume replaces the depth", icon=icons.INFO)

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        definitions = {"type": "openpbr"}

        for name, _, default, key, group, factor in INPUTS:
            if group is not None and not getattr(self, group):
                continue
            socket = _input(self, name)
            value = socket.export(exporter, depsgraph, props)
            if not socket.is_linked and not _differs(value, default):
                continue

            if factor != 1:
                if isinstance(value, str):
                    tex_name = luxcore_name + "_" + key.replace(".", "_")
                    props.Set(utils.luxutils.create_props("scene.textures." + tex_name + ".", {
                        "type": "scale",
                        "texture1": value,
                        "texture2": factor,
                    }))
                    value = tex_name
                else:
                    value *= factor
            definitions[key] = value

        if self.thin_walled:
            definitions["geometry.thinwalled"] = True

        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, luxcore_name)
