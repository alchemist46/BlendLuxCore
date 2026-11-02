"""MaterialX import: the open_pbr_surface materials of a .mtlx file as LuxCore
OpenPBR nodes (the core's ROADMAP OP).

Blender bundles the MaterialX Python module (1.39.4 in Blender 5.2). The
constant inputs are read, and of the node graphs the image, tiledimage,
normalmap and constant nodes; an input fed by another node keeps its value,
with a warning.
"""
import os
import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from .utils import make_nodetree_name, show_nodetree
from ..nodes.materials.openpbr import COAT_BUMP_NAME, FILM_THICKNESS_NAME, INPUTS
from ..utils import eevee_sync

# The open_pbr_surface inputs: the OpenPBR node socket and the factor from the
# MaterialX value to the socket value
SOCKETS = {
    "base_weight": ("Base Weight", 1),
    "base_color": ("Base Color", 1),
    "base_diffuse_roughness": ("Diffuse Roughness", 1),
    "base_metalness": ("Metalness", 1),
    "specular_weight": ("Specular Weight", 1),
    "specular_color": ("Specular Color", 1),
    "specular_roughness": ("Specular Roughness", 1),
    "specular_ior": ("Specular IOR", 1),
    "specular_roughness_anisotropy": ("Specular Anisotropy", 1),
    "transmission_weight": ("Transmission Weight", 1),
    "transmission_color": ("Transmission Color", 1),
    "transmission_depth": ("Transmission Depth", 1),
    "transmission_scatter": ("Transmission Scatter", 1),
    "transmission_scatter_anisotropy": ("Scatter Anisotropy", 1),
    "transmission_dispersion_scale": ("Dispersion Scale", 1),
    "transmission_dispersion_abbe_number": ("Abbe Number", 1),
    "subsurface_weight": ("Subsurface Weight", 1),
    "subsurface_color": ("Subsurface Color", 1),
    "subsurface_radius": ("Subsurface Radius", 1),
    "subsurface_radius_scale": ("Radius Scale", 1),
    "subsurface_scatter_anisotropy": ("Subsurface Anisotropy", 1),
    "fuzz_weight": ("Fuzz Weight", 1),
    "fuzz_color": ("Fuzz Color", 1),
    "fuzz_roughness": ("Fuzz Roughness", 1),
    "coat_weight": ("Coat Weight", 1),
    "coat_color": ("Coat Color", 1),
    "coat_roughness": ("Coat Roughness", 1),
    "coat_roughness_anisotropy": ("Coat Anisotropy", 1),
    "coat_ior": ("Coat IOR", 1),
    "coat_darkening": ("Coat Darkening", 1),
    "thin_film_weight": ("Thin Film Weight", 1),
    # Micrometres in MaterialX, nanometres on the node
    "thin_film_thickness": (FILM_THICKNESS_NAME, 1000),
    "thin_film_ior": ("Thin Film IOR", 1),
    "geometry_opacity": ("Opacity", 1),
    "geometry_normal": ("Bump", 1),
    "geometry_coat_normal": (COAT_BUMP_NAME, 1),
}

# The group toggle of a socket (see INPUTS) and the weight that switches it on
SOCKET_GROUPS = {name: group for name, _, _, _, group, _ in INPUTS}
SOCKET_GROUPS[COAT_BUMP_NAME] = "use_coat"
GROUP_WEIGHTS = {
    "transmission_weight": "use_transmission",
    "subsurface_weight": "use_subsurface",
    "coat_weight": "use_coat",
    "fuzz_weight": "use_fuzz",
    "thin_film_weight": "use_thin_film",
}

# The colour spaces read as data or linear colours (gamma 1); the others as
# sRGB (gamma 2.2)
LINEAR_COLORSPACES = {"", "raw", "lin_rec709", "lin_srgb", "linear"}

TEXTURE_X = -350
TEXTURE_STEP_Y = -260


def _socket(node, name):
    # Indexing node.inputs by name doesn't find the disabled sockets
    return node.inputs[node.inputs.find(name)]


def _value(mx_input):
    """The value of a MaterialX input as a float, a tuple or a bool"""
    value = mx_input.getValue()
    if isinstance(value, (bool, int, float)):
        return value
    # Color3, Vector3 and the like
    return tuple(value.asTuple())


def _upstream(mx_input):
    """The node that feeds a MaterialX input, through a node graph output"""
    output = mx_input.getConnectedOutput()
    if output is not None:
        return output.getConnectedNode()
    return mx_input.getConnectedNode()


class MaterialXReader:
    def __init__(self, filepath):
        self.base_dir = os.path.dirname(filepath)
        self.warnings = []

    def warn(self, text):
        self.warnings.append(text)

    def material(self, material_node, shader):
        name = material_node.getName()
        mat = bpy.data.materials.new(name=name)
        tree = bpy.data.node_groups.new(name=make_nodetree_name(mat.name), type="luxcore_material_nodes")
        # User counting does not work reliably with Python PointerProperty
        tree.use_fake_user = True
        self.tree = tree
        self.texture_y = 300

        output = tree.nodes.new("LuxCoreNodeMatOutput")
        output.location = 300, 200
        output.select = False
        pbr = tree.nodes.new("LuxCoreNodeMatOpenPBR")
        pbr.location = 0, 200
        tree.links.new(pbr.outputs["Material"], output.inputs[0])

        inputs = {i.getName(): i for i in shader.getInputs()}

        # Switch the groups on first: switching on sets a weight of 0 to 1
        for weight, group in GROUP_WEIGHTS.items():
            mx_input = inputs.get(weight)
            if mx_input is not None and (_upstream(mx_input) is not None or _value(mx_input) > 0):
                setattr(pbr, group, True)
        if "geometry_coat_normal" in inputs and _upstream(inputs["geometry_coat_normal"]) is not None:
            pbr.use_coat = True

        for input_name, mx_input in inputs.items():
            if input_name in SOCKETS:
                socket_name, factor = SOCKETS[input_name]
                self.set_input(pbr, _socket(pbr, socket_name), mx_input, factor, name)
            elif input_name == "geometry_thin_walled":
                pbr.thin_walled = bool(_value(mx_input))
            elif input_name in {"emission_luminance", "emission_color"}:
                pass
            elif input_name in {"geometry_tangent", "geometry_coat_tangent"}:
                if _upstream(mx_input) is not None:
                    self.warn(f'{name}: "{input_name}" is not supported yet, the UV tangent is used')
            else:
                self.warn(f'{name}: the input "{input_name}" is unknown and ignored')

        self.emission(pbr, inputs, name)

        mat.luxcore.node_tree = tree
        # EEVEE and Material Preview show an approximation of the LuxCore material
        eevee_sync.enable(mat)
        return mat

    def set_input(self, pbr, socket, mx_input, factor, mat_name):
        upstream = _upstream(mx_input)
        if upstream is None:
            if not mx_input.hasValueString():
                return
            value = _value(mx_input)
            if not hasattr(socket, "default_value"):
                return
            if isinstance(value, tuple):
                socket.default_value = tuple(v * factor for v in value[:len(socket.default_value)])
            else:
                socket.default_value = value * factor
            return

        bump = socket.bl_idname == "LuxCoreSocketBump"
        out = self.texture(upstream, bump, mat_name)
        if out is None:
            return
        if not isinstance(out, bpy.types.NodeSocket):
            # A constant node
            if hasattr(socket, "default_value"):
                socket.default_value = tuple(v * factor for v in out) if isinstance(out, tuple) else out * factor
            return
        if factor != 1:
            self.warn(f'{mat_name}: the texture of "{mx_input.getName()}" is not scaled '
                      f'by {factor}')
        self.tree.links.new(out, socket)

    def texture(self, mx_node, bump, mat_name):
        """The output socket of the LuxCore nodes for a MaterialX node, a
        constant value, or None (with a warning)"""
        category = mx_node.getCategory()
        if category == "constant":
            value_input = mx_node.getInput("value")
            return _value(value_input) if value_input is not None else None
        if category == "normalmap":
            image_input = mx_node.getInput("in")
            image = _upstream(image_input) if image_input is not None else None
            if image is None or image.getCategory() not in {"image", "tiledimage"}:
                self.warn(f'{mat_name}: the normalmap node "{mx_node.getName()}" needs an image')
                return None
            lux = self.image(image, mat_name)
            if lux is None:
                return None
            lux.is_normal_map = True
            lux.normal_map_orientation = "opengl"
            scale = mx_node.getInput("scale")
            if scale is not None and scale.hasValueString():
                value = _value(scale)
                lux.normal_map_scale = value if isinstance(value, float) else value[0]
            return lux.outputs["Bump"]
        if category in {"image", "tiledimage"}:
            if bump:
                self.warn(f'{mat_name}: an image as a normal without a normalmap node is read as a '
                          'tangent space normal map')
            lux = self.image(mx_node, mat_name)
            if lux is None:
                return None
            if bump:
                lux.is_normal_map = True
                lux.normal_map_orientation = "opengl"
                return lux.outputs["Bump"]
            return lux.outputs["Color"]

        self.warn(f'{mat_name}: the MaterialX node "{mx_node.getName()}" ({category}) is not supported, '
                  'the input keeps its value')
        return None

    def image(self, mx_node, mat_name):
        file_input = mx_node.getInput("file")
        if file_input is None:
            self.warn(f'{mat_name}: the image node "{mx_node.getName()}" has no file')
            return None
        path = file_input.getResolvedValueString()
        if not os.path.isabs(path):
            path = os.path.join(self.base_dir, path)
        path = os.path.normpath(path)
        try:
            image = bpy.data.images.load(path, check_existing=True)
        except RuntimeError:
            self.warn(f'{mat_name}: the image "{path}" could not be loaded')
            return None

        lux = self.tree.nodes.new("LuxCoreNodeTexImagemap")
        lux.location = TEXTURE_X, self.texture_y
        self.texture_y += TEXTURE_STEP_Y
        lux.image = image
        colorspace = file_input.getActiveColorSpace()
        if colorspace in LINEAR_COLORSPACES:
            lux.gamma = 1.0
            image.colorspace_settings.is_data = (colorspace in {"", "raw"})
        else:
            lux.gamma = 2.2
            if colorspace != "srgb_texture":
                self.warn(f'{mat_name}: the colour space {colorspace} of "{os.path.basename(path)}" '
                          'is read as sRGB')

        if mx_node.getCategory() == "tiledimage":
            tiling = mx_node.getInput("uvtiling")
            offset = mx_node.getInput("uvoffset")
            tiling = _value(tiling) if tiling is not None and tiling.hasValueString() else (1, 1)
            offset = _value(offset) if offset is not None and offset.hasValueString() else (0, 0)
            if tuple(tiling) != (1, 1) or tuple(offset) != (0, 0):
                mapping = self.tree.nodes.new("LuxCoreNodeTexMapping2D")
                mapping.location = TEXTURE_X - 220, lux.location.y
                mapping.use_uniform_scale = False
                mapping.uscale, mapping.vscale = tiling[0], tiling[1]
                mapping.udelta, mapping.vdelta = offset[0], offset[1]
                self.tree.links.new(mapping.outputs["2D Mapping"], lux.inputs["2D Mapping"])
        return lux

    def emission(self, pbr, inputs, mat_name):
        luminance = inputs.get("emission_luminance")
        if luminance is None:
            return
        if _upstream(luminance) is not None:
            self.warn(f'{mat_name}: a textured "emission_luminance" is not supported, it is ignored')
            return
        nits = _value(luminance)
        if nits <= 0:
            return

        emission = self.tree.nodes.new("LuxCoreNodeMatEmission")
        emission.location = TEXTURE_X, -400
        # OpenPBR: the emitted luminance in nits times emission_color
        emission.emission_unit = "candela"
        emission.per_square_meter = True
        emission.candela = nits
        emission.normalizebycolor = False
        color = inputs.get("emission_color")
        if color is not None:
            self.set_input(pbr, emission.inputs["Color"], color, 1, mat_name)
        self.tree.links.new(emission.outputs["Emission"], _socket(pbr, "Emission"))


class LUXCORE_OT_import_materialx(bpy.types.Operator, ImportHelper):
    bl_idname = "luxcore.import_materialx"
    bl_label = "Import MaterialX"
    bl_description = ("Create LuxCore OpenPBR materials from the OpenPBR Surface materials "
                      "of a MaterialX (.mtlx) file")
    bl_options = {"UNDO"}

    filename_ext = ".mtlx"
    filter_glob: StringProperty(default="*.mtlx", options={"HIDDEN"})
    assign: BoolProperty(name="Assign to Active Object", default=True,
                         description="Put the first imported material in the active material slot "
                                     "of the active object")

    def execute(self, context):
        try:
            import MaterialX as mx
        except ImportError:
            self.report({"ERROR"}, "This Blender has no MaterialX Python module")
            return {"CANCELLED"}

        doc = mx.createDocument()
        try:
            mx.readFromXmlFile(doc, self.filepath)
        except Exception as error:
            self.report({"ERROR"}, f"Could not read {os.path.basename(self.filepath)}: {error}")
            return {"CANCELLED"}

        reader = MaterialXReader(self.filepath)
        imported = []
        for material_node in doc.getMaterialNodes():
            for shader in mx.getShaderNodes(material_node):
                category = shader.getCategory()
                if category == "open_pbr_surface":
                    imported.append(reader.material(material_node, shader))
                else:
                    reader.warn(f'{material_node.getName()}: the shader {category} is not supported '
                                '(only open_pbr_surface)')

        for warning in reader.warnings:
            self.report({"WARNING"}, warning)
        if not imported:
            self.report({"ERROR"}, "No OpenPBR Surface material in the file")
            return {"CANCELLED"}

        obj = context.active_object
        if self.assign and obj and not obj.library and hasattr(obj.data, "materials"):
            if obj.material_slots:
                obj.material_slots[obj.active_material_index].material = imported[0]
            else:
                obj.data.materials.append(imported[0])
            # For viewport render, the luxcore object needs an update
            obj.update_tag()
            show_nodetree(context, imported[0].luxcore.node_tree)

        self.report({"INFO"}, f"Imported {len(imported)} material(s): "
                              + ", ".join(m.name for m in imported))
        return {"FINISHED"}
