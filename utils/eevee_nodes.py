"""Generate the Cycles/EEVEE node tree of a material from its LuxCore node tree.

The generated tree is an approximation for EEVEE and Material Preview, not a
copy of the LuxCore material: it is built from the Principled BSDF and a few
other Cycles nodes. Nodes that have no Cycles counterpart leave the target
socket at the value of the LuxCore socket and add a warning.

The generator is two-phase:
- plan_material() walks the LuxCore tree into pure data (a Plan): node specs
  with deterministic names ("lux_<LuxCore node name>", helpers
  "lux_<name>__<role>"), their properties and input values or links.
- apply_plan() writes the plan into the material's node tree. When the tree
  already has the same nodes and links, only the values that differ are
  written (no shader recompile in EEVEE for a slider drag); otherwise the tree
  is rebuilt.

Ownership: material.luxcore.eevee_nodes_signature stores a hash of the tree
the generator wrote (tree_signature()). A tree whose hash differs was edited
by hand and is never overwritten by the automatic sync (see eevee_sync.py).

Conventions inverted from export/cycles_node_reader.py: Cycles roughness r is
LuxCore roughness r * r for glossy, glass and metal (sqrt here), the OpenPBR
specular weight is twice the Specular IOR Level, and the 2D mapping follows
the reader's UV mapping (uvscale [sx, -sy], uvdelta [lx, 1 - ly]).

This module must not import pyluxcore or modules above utils (see
utils/__init__.py): utils.node is imported lazily, inside functions.
"""

import hashlib
import math

import bpy

from . import colorspace
from . import temperature as utils_temperature
from .errorlog import LuxCoreErrorLog
from .log import logger

PREFIX = "lux_"
OUTPUT_NAME = "lux_output"
# Blender node names are limited to 63 bytes
_MAX_NAME_BYTES = 60

LUXCORE_MATERIAL_TREE = "luxcore_material_nodes"
LUXCORE_TEXTURE_TREE = "luxcore_texture_nodes"

# Approximate reflectance at normal incidence (F0, linear) of the metal
# presets of the LuxCore Fresnel node (nodes/textures/fresnel.py)
METAL_PRESET_COLORS = {
    "aluminium": (0.913, 0.922, 0.924),
    "amorphous_carbon": (0.2, 0.2, 0.2),
    "beryllium": (0.5, 0.5, 0.5),
    "brass": (0.91, 0.778, 0.423),
    "red_brass": (0.93, 0.66, 0.49),
    "calcium": (0.6, 0.6, 0.6),
    "chromium": (0.55, 0.556, 0.554),
    "cobalt": (0.662, 0.655, 0.634),
    "copper": (0.955, 0.638, 0.538),
    "electrum": (0.97, 0.88, 0.62),
    "gold": (1.0, 0.766, 0.336),
    "green_gold": (0.98, 0.85, 0.5),
    "purple_gold": (0.8, 0.55, 0.7),
    "iridium": (0.7, 0.7, 0.7),
    "iron": (0.562, 0.565, 0.578),
    "lead": (0.632, 0.626, 0.641),
    "magnesium": (0.85, 0.85, 0.85),
    "mercury": (0.78, 0.78, 0.78),
    "molybdenum": (0.6, 0.6, 0.6),
    "nickel": (0.66, 0.609, 0.526),
    "osmium": (0.6, 0.6, 0.6),
    "palladium": (0.733, 0.697, 0.652),
    "platinum": (0.673, 0.637, 0.585),
    "rhodium": (0.78, 0.78, 0.78),
    "silver": (0.972, 0.96, 0.915),
    "stainless_steel": (0.6, 0.6, 0.6),
    "stainless_steel_ferritic": (0.58, 0.58, 0.58),
    "titanium": (0.542, 0.497, 0.449),
    "tungsten": (0.59, 0.57, 0.55),
    "zinc": (0.664, 0.824, 0.85),
}
DEFAULT_METAL_COLOR = (0.7, 0.7, 0.7)

# Node properties that are part of the content signature (when the node has them)
SIGNED_PROPS = (
    "operation", "use_clamp", "blend_type", "data_type", "clamp_factor", "clamp_result",
    "factor_mode", "interpolation", "extension", "projection", "projection_blend",
    "vector_type", "uv_map", "layer_name", "space", "distribution", "subsurface_method",
    "wave_type", "bands_direction", "rings_direction", "gradient_type", "turbulence_depth",
    "voronoi_dimensions", "feature", "distance", "noise_dimensions", "use_pixel_size",
    "invert", "interpolation_type", "clamp", "mode", "target", "image",
)

_EXTENSIONS = {"repeat": "REPEAT", "clamp": "EXTEND", "black": "CLIP", "white": "CLIP"}
_INTERPOLATIONS = {"linear": "Linear", "nearest": "Closest"}
_RAMP_INTERPOLATIONS = {"linear": "LINEAR", "cubic": "B_SPLINE", "none": "CONSTANT"}
_MATH_OPERATIONS = {
    "scale": "MULTIPLY",
    "divide": "DIVIDE",
    "add": "ADD",
    "subtract": "SUBTRACT",
    "power": "POWER",
    "lessthan": "LESS_THAN",
    "greaterthan": "GREATER_THAN",
    "modulo": "MODULO",
    "sqrt": "SQRT",
    "abs": "ABSOLUTE",
}
_MIX_BLEND_TYPES = {"mix": "MIX", "scale": "MULTIPLY", "add": "ADD", "subtract": "SUBTRACT"}
_VECTOR_OPERATIONS = {"scale": "MULTIPLY", "add": "ADD", "subtract": "SUBTRACT", "abs": "ABSOLUTE"}
_GRADIENT_TYPES = {
    "linear": "LINEAR",
    "quadratic": "QUADRATIC",
    "easing": "EASING",
    "diagonal": "DIAGONAL",
    "spherical": "SPHERICAL",
    "halo": "QUADRATIC_SPHERE",
    "radial": "RADIAL",
}

# Last warnings logged per material, so a flush does not log them again
_last_warnings = {}
# Default input values of a fresh Principled BSDF {identifier: value}
_principled_defaults_cache = None


# ------------------------------------------------------------------------------
# Plan data
# ------------------------------------------------------------------------------

class Ref:
    """A link from an output socket of a planned node"""
    __slots__ = ("node", "socket")

    def __init__(self, node, socket):
        self.node = node
        self.socket = socket

    def __eq__(self, other):
        return isinstance(other, Ref) and self.node == other.node and self.socket == other.socket

    def __hash__(self):
        return hash((self.node, self.socket))

    def __repr__(self):
        return f"Ref({self.node!r}, {self.socket!r})"


class NodeSpec:
    def __init__(self, name, bl_idname, location, label=""):
        self.name = name
        self.bl_idname = bl_idname
        self.location = location
        self.label = label
        # Properties in assignment order (data_type before the sockets it enables)
        self.props = {}
        # Socket key (identifier, or name) -> constant or Ref
        self.inputs = {}
        # Output socket key -> constant (Value and RGB nodes)
        self.outputs = {}


class Plan:
    def __init__(self):
        self.nodes = {}
        self.warnings = []

    def links(self):
        for spec in self.nodes.values():
            for key, value in spec.inputs.items():
                if isinstance(value, Ref):
                    yield value.node, value.socket, spec.name, key


class _Shader:
    """A planned shader: its output and where the common LuxCore inputs go"""

    def __init__(self, ref, principled=None, normals=(), has_emission=False):
        self.ref = ref
        # The Principled BSDF that takes Alpha and Emission directly
        self.principled = principled
        # Node specs with a Normal input that take the bump
        self.normals = list(normals)
        # The shader already is the emission (Matte black + Emission)
        self.has_emission = has_emission


# ------------------------------------------------------------------------------
# Value helpers
# ------------------------------------------------------------------------------

def _is_ref(value):
    return isinstance(value, Ref)


def _is_const(value):
    return value is not None and not isinstance(value, Ref)


def _as_float(value):
    if isinstance(value, (tuple, list)):
        values = list(value[:3])
        return sum(values) / len(values) if values else 0.0
    return float(value)


def _as_color(value):
    if isinstance(value, (tuple, list)):
        values = tuple(float(v) for v in value[:3])
        while len(values) < 3:
            values += (values[-1] if values else 0.0,)
        return values
    v = float(value)
    return (v, v, v)


def _luminance(color):
    r, g, b = _as_color(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _is_black(value):
    return _is_const(value) and max(_as_color(value)) <= 1e-6


def _socket_value(socket):
    """The default value of a LuxCore socket as a float or a 3-tuple, None if it has none"""
    if socket is None or not hasattr(socket, "default_value"):
        return None
    value = socket.default_value
    if value is None:
        return None
    try:
        values = tuple(value)
    except TypeError:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    return tuple(float(v) for v in values[:3])


def _short_name(name):
    if len(name.encode("utf-8")) <= _MAX_NAME_BYTES:
        return name
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=4).hexdigest()
    short = name
    while len((short + "~" + digest).encode("utf-8")) > _MAX_NAME_BYTES:
        short = short[:-1]
    return short + "~" + digest


def _input(node, name):
    """A socket of a LuxCore node by name, also when it is disabled (None if missing)"""
    index = node.inputs.find(name)
    return node.inputs[index] if index != -1 else None


def _get_link(socket):
    if socket is None:
        return None
    from .node import get_link
    return get_link(socket)


def _get_active_output(node_tree):
    from .node import get_active_output
    return get_active_output(node_tree)


def _is_linear_space(name):
    function = getattr(colorspace, "is_linear_scene_space", None) or getattr(colorspace, "_is_linear_scene_space")
    return function(name)


# ------------------------------------------------------------------------------
# Planner
# ------------------------------------------------------------------------------

class _Planner:
    def __init__(self, material):
        self.material = material
        self.plan = Plan()
        self.tex_memo = {}
        self.shader_memo = {}
        self.warned_types = set()
        # Name prefix and location offset of the tree being walked (Pointer nodes)
        self.prefix = ""
        self.offset = (0.0, 0.0)
        self.tree_stack = []

    # Plan building -----------------------------------------------------------

    def warn(self, message):
        if message not in self.plan.warnings:
            self.plan.warnings.append(message)

    def warn_type(self, node, what="is not supported in the EEVEE nodes, its socket values are used"):
        if node.bl_idname not in self.warned_types:
            self.warned_types.add(node.bl_idname)
            label = node.bl_label or node.bl_idname
            self.warn(f'Material "{self.material.name}": LuxCore node "{label}" {what}')

    def node_name(self, node, role=""):
        name = PREFIX + self.prefix + node.name
        if role:
            name += "__" + role
        return _short_name(name)

    def add_raw(self, name, bl_idname, location, label=""):
        spec = self.plan.nodes.get(name)
        if spec is None:
            spec = NodeSpec(name, bl_idname, location, label)
            self.plan.nodes[name] = spec
        return spec

    def add(self, node, role, bl_idname, dx=0.0, dy=0.0, label=""):
        """The spec of the Cycles node made for (a role of) a LuxCore node, the same one when asked again"""
        location = (node.location.x + self.offset[0] + dx, node.location.y + self.offset[1] + dy)
        return self.add_raw(self.node_name(node, role), bl_idname, location, label)

    def black(self):
        spec = self.add_raw(PREFIX + "_black", "ShaderNodeBsdfDiffuse", (0.0, -400.0), "Black")
        spec.inputs["Color"] = (0.0, 0.0, 0.0)
        return Ref(spec.name, "BSDF")

    def position(self):
        spec = self.add_raw(PREFIX + "_position", "ShaderNodeNewGeometry", (-1200.0, 0.0), "Global Coordinates")
        return Ref(spec.name, "Position")

    def uv(self):
        spec = self.add_raw(PREFIX + "_uv", "ShaderNodeTexCoord", (-1200.0, -300.0), "UV")
        return Ref(spec.name, "UV")

    # Math helpers (constants are folded) -------------------------------------

    def math(self, operation, a, b=None, owner=None, role="", c=None):
        if _is_const(a) and (b is None or _is_const(b)) and (c is None or _is_const(c)):
            x = _as_float(a)
            y = _as_float(b) if b is not None else 0.0
            try:
                if operation == "MULTIPLY":
                    return x * y
                if operation == "ADD":
                    return x + y
                if operation == "SUBTRACT":
                    return x - y
                if operation == "DIVIDE":
                    return x / y if y != 0 else 0.0
                if operation == "POWER":
                    return x ** y if x >= 0 else 0.0
                if operation == "SQRT":
                    return math.sqrt(max(x, 0.0))
                if operation == "MULTIPLY_ADD":
                    return x * y + _as_float(c)
            except (ValueError, OverflowError, ZeroDivisionError):
                pass
        spec = self.add(owner, role, "ShaderNodeMath", -200, -160)
        spec.props["operation"] = operation
        spec.inputs["Value"] = a
        if b is not None:
            spec.inputs["Value_001"] = b
        if c is not None:
            spec.inputs["Value_002"] = c
        return Ref(spec.name, "Value")

    def sqrt(self, value, owner, role):
        return self.math("SQRT", value, None, owner, role)

    def multiply_color(self, color, factor, owner, role):
        if _is_const(color) and _is_const(factor):
            f = _as_float(factor)
            return tuple(c * f for c in _as_color(color))
        spec = self.add(owner, role, "ShaderNodeMix", -200, -160)
        spec.props["data_type"] = "RGBA"
        spec.props["blend_type"] = "MULTIPLY"
        spec.inputs["Factor_Float"] = 1.0
        spec.inputs["A_Color"] = color
        spec.inputs["B_Color"] = factor if _is_ref(factor) else _as_color(factor)
        return Ref(spec.name, "Result_Color")

    # Sockets -----------------------------------------------------------------

    def tex(self, socket):
        """The value of a LuxCore texture socket: a constant or a Ref"""
        if socket is None:
            return None
        link = _get_link(socket)
        if link is None:
            return _socket_value(socket)
        result = self.tex_output(link.from_node, link.from_socket)
        if result is None:
            return _socket_value(socket)
        return result

    def tex_in(self, node, name):
        return self.tex(_input(node, name))

    def tex_output(self, node, output_socket):
        key = (self.prefix, node.name, output_socket.identifier)
        if key in self.tex_memo:
            return self.tex_memo[key]
        handler = TEXTURE_HANDLERS.get(node.bl_idname)
        if handler is None:
            self.warn_type(node)
            result = None
        else:
            try:
                result = handler(self, node, output_socket)
            except Exception as error:
                logger.exception(f"EEVEE nodes: node {node.name}")
                self.warn(f'Material "{self.material.name}": node "{node.name}" could not be converted: {error}')
                result = None
        self.tex_memo[key] = result
        return result

    def shader(self, socket):
        """The Cycles shader made for what feeds a LuxCore material socket (a Ref or None)"""
        if socket is None:
            return None
        link = _get_link(socket)
        if link is None:
            return None
        node = link.from_node
        key = (self.prefix, node.name)
        if key in self.shader_memo:
            return self.shader_memo[key]

        if node.bl_idname == "LuxCoreNodeTreePointer":
            result = self.pointer(node, LUXCORE_MATERIAL_TREE, "Material", self.shader)
        else:
            handler = MATERIAL_HANDLERS.get(node.bl_idname)
            if handler is None:
                self.warn_type(node, "is not supported in the EEVEE nodes, it is black there")
                result = None
            else:
                try:
                    shader = handler(self, node)
                    result = self.common(node, shader)
                except Exception as error:
                    logger.exception(f"EEVEE nodes: node {node.name}")
                    self.warn(f'Material "{self.material.name}": node "{node.name}" could not be converted: {error}')
                    result = None
        self.shader_memo[key] = result
        return result

    def pointer(self, node, tree_type, output_input, resolve):
        tree = node.node_tree
        if tree is None or tree.bl_idname != tree_type:
            return None
        if tree == node.id_data or tree.name_full in self.tree_stack:
            self.warn(f'Material "{self.material.name}": Pointer node "{node.name}" creates a recursion')
            return None
        output = _get_active_output(tree)
        if output is None:
            return None
        saved = (self.prefix, self.offset)
        self.tree_stack.append(tree.name_full)
        self.prefix = self.prefix + tree.name + "/"
        self.offset = (node.location.x + self.offset[0] - output.location.x,
                       node.location.y + self.offset[1] - output.location.y)
        try:
            return resolve(_input(output, output_input))
        finally:
            self.tree_stack.pop()
            self.prefix, self.offset = saved

    # Common material inputs (Opacity, Bump, Emission) ------------------------

    def emission(self, node):
        socket = _input(node, "Emission")
        link = _get_link(socket) if socket is not None else None
        if link is None or link.from_node.bl_idname != "LuxCoreNodeMatEmission":
            return None
        emission = link.from_node
        color = self.tex_in(emission, "Color")
        if emission.emission_unit == "artistic":
            strength = emission.gain * 2 ** emission.exposure
        else:
            strength = 1.0
            self.warn(f'Material "{self.material.name}": emission in {emission.emission_unit} needs the area '
                      "of the emitting mesh, the EEVEE nodes use strength 1")
        return color, strength

    def bump(self, socket):
        link = _get_link(socket) if socket is not None else None
        if link is None:
            return None
        source = link.from_node
        key = ("bump", self.prefix, source.name, link.from_socket.identifier)
        if key in self.tex_memo:
            return self.tex_memo[key]

        idname = source.bl_idname
        if idname in {"LuxCoreNodeTexBump", "LuxCoreNodeTexTriplanarBump"}:
            if idname == "LuxCoreNodeTexTriplanarBump":
                self.warn_type(source, "is approximated by a plain bump in the EEVEE nodes")
            height = self.tex_in(source, "Value")
            distance = self.tex_in(source, "Bump Height")
            spec = self.add(source, "bump", "ShaderNodeBump", 200, 0)
            spec.inputs["Strength"] = 1.0
            if _is_ref(distance):
                spec.inputs["Distance"] = 1.0
                spec.inputs["Height"] = self.math("MULTIPLY", height, distance, source, "bump_height")
            else:
                spec.inputs["Distance"] = _as_float(distance) if distance is not None else 0.001
                spec.inputs["Height"] = height if height is not None else 0.0
            result = Ref(spec.name, "Normal")
        elif idname in {"LuxCoreNodeTexNormalmap", "LuxCoreNodeTexTriplanarNormalmap"}:
            if idname == "LuxCoreNodeTexTriplanarNormalmap":
                self.warn_type(source, "is approximated by a plain normal map in the EEVEE nodes")
            spec = self.add(source, "normalmap", "ShaderNodeNormalMap", 200, 0)
            spec.props["space"] = "TANGENT"
            spec.inputs["Color"] = self.tex_in(source, "Color")
            spec.inputs["Strength"] = source.scale
            result = Ref(spec.name, "Normal")
        elif idname == "LuxCoreNodeTexImagemap" and source.is_normal_map:
            color = self.imagemap_color(source, normal_map=True)
            if color is None:
                result = None
            else:
                if source.normal_map_orientation == "directx":
                    color = self.flip_green(source, color)
                spec = self.add(source, "normalmap", "ShaderNodeNormalMap", 200, 0)
                spec.props["space"] = "TANGENT"
                spec.inputs["Color"] = color
                spec.inputs["Strength"] = source.normal_map_scale
                result = Ref(spec.name, "Normal")
        else:
            # A plain texture is the height in scene units
            height = self.tex_output(source, link.from_socket)
            if height is None:
                result = None
            else:
                spec = self.add(source, "bump", "ShaderNodeBump", 200, 0)
                spec.inputs["Strength"] = 1.0
                spec.inputs["Distance"] = 1.0
                spec.inputs["Height"] = height
                result = Ref(spec.name, "Normal")
        self.tex_memo[key] = result
        return result

    def flip_green(self, node, color):
        separate = self.add(node, "dx_separate", "ShaderNodeSeparateColor", 0, -200)
        separate.inputs["Color"] = color
        invert = self.add(node, "dx_invert", "ShaderNodeMath", 100, -200)
        invert.props["operation"] = "SUBTRACT"
        invert.inputs["Value"] = 1.0
        invert.inputs["Value_001"] = Ref(separate.name, "Green")
        combine = self.add(node, "dx_combine", "ShaderNodeCombineColor", 200, -200)
        combine.inputs["Red"] = Ref(separate.name, "Red")
        combine.inputs["Green"] = Ref(invert.name, "Value")
        combine.inputs["Blue"] = Ref(separate.name, "Blue")
        return Ref(combine.name, "Color")

    def common(self, node, shader):
        if shader is None:
            return None
        ref = shader.ref

        bump_socket = _input(node, "Bump")
        if bump_socket is not None and shader.normals:
            normal = self.bump(bump_socket)
            if normal is not None:
                for spec in shader.normals:
                    spec.inputs["Normal"] = normal

        if not shader.has_emission:
            emission = self.emission(node)
            if emission is not None:
                color, strength = emission
                if shader.principled is not None:
                    shader.principled.inputs["Emission Color"] = color
                    shader.principled.inputs["Emission Strength"] = strength
                else:
                    emit = self.add(node, "emission", "ShaderNodeEmission", 0, -250)
                    emit.inputs["Color"] = color
                    emit.inputs["Strength"] = strength
                    add = self.add(node, "add_emission", "ShaderNodeAddShader", 200, -100)
                    add.inputs["Shader"] = ref
                    add.inputs["Shader_001"] = Ref(emit.name, "Emission")
                    ref = Ref(add.name, "Shader")

        opacity_socket = _input(node, "Opacity")
        if opacity_socket is not None:
            opacity = self.tex(opacity_socket)
            if _is_ref(opacity) or (opacity is not None and _as_float(opacity) < 1 - 1e-6):
                if shader.principled is not None:
                    shader.principled.inputs["Alpha"] = opacity
                else:
                    transparent = self.add(node, "transparent", "ShaderNodeBsdfTransparent", 0, -350)
                    mix = self.add(node, "opacity", "ShaderNodeMixShader", 250, -50)
                    mix.inputs["Fac"] = opacity
                    mix.inputs["Shader"] = Ref(transparent.name, "BSDF")
                    mix.inputs["Shader_001"] = ref
                    ref = Ref(mix.name, "Shader")
        return ref

    # Shared material pieces --------------------------------------------------

    def principled(self, node, role=""):
        spec = self.add(node, role, "ShaderNodeBsdfPrincipled", 0, -300 if role else 0)
        # Every input the generator manages, so an in-place update resets what
        # a LuxCore change no longer sets
        spec.inputs.update({
            "Base Color": (0.8, 0.8, 0.8),
            "Metallic": 0.0,
            "Roughness": 0.5,
            "IOR": 1.5,
            "Alpha": 1.0,
            "Diffuse Roughness": 0.0,
            "Specular IOR Level": 0.5,
            "Specular Tint": (1.0, 1.0, 1.0),
            "Anisotropic": 0.0,
            "Anisotropic Rotation": 0.0,
            "Transmission Weight": 0.0,
            "Subsurface Weight": 0.0,
            "Coat Weight": 0.0,
            "Sheen Weight": 0.0,
            "Emission Strength": 0.0,
            "Thin Film Thickness": 0.0,
        })
        return spec

    def roughness(self, node, squared=True, role="roughness"):
        """Cycles roughness and anisotropy from a LuxCore Roughness / U-Roughness pair"""
        socket = _input(node, "Roughness") or _input(node, "U-Roughness")
        u = self.tex(socket)
        if u is None:
            u = 0.05
        anisotropy = 0.0
        if getattr(node, "use_anisotropy", False):
            v = self.tex_in(node, "V-Roughness")
            if _is_const(u) and _is_const(v):
                u_value, v_value = _as_float(u), _as_float(v)
                high, low = max(u_value, v_value), min(u_value, v_value)
                anisotropy = 1 - low / high if high > 0 else 0.0
                u = high
            else:
                self.warn(f'Material "{self.material.name}": textured anisotropic roughness is '
                          "isotropic in the EEVEE nodes")
        if squared:
            u = self.sqrt(u, node, role)
        return u, anisotropy

    def specular_from_color(self, spec, node, ks_name="Specular Color", ior_name="IOR", use_ior=None):
        """Specular IOR Level, IOR and tint of a Principled BSDF from a LuxCore specular color or IOR"""
        if use_ior is None:
            use_ior = getattr(node, "use_ior", False)
        if use_ior:
            spec.inputs["IOR"] = self.tex_in(node, ior_name)
            spec.inputs["Specular IOR Level"] = 0.5
            return
        ks = self.tex_in(node, ks_name)
        if _is_ref(ks):
            spec.inputs["Specular Tint"] = ks
            spec.inputs["Specular IOR Level"] = 0.5
            return
        color = _as_color(ks if ks is not None else 0.04)
        peak = max(color)
        if peak <= 1e-6:
            spec.inputs["Specular IOR Level"] = 0.0
            return
        f0 = min(peak, 0.95)
        root = math.sqrt(f0)
        spec.inputs["IOR"] = (1 + root) / (1 - root)
        spec.inputs["Specular IOR Level"] = 0.5
        spec.inputs["Specular Tint"] = tuple(c / peak for c in color)

    def thin_film(self, spec, node, amount=1.0):
        thickness = self.tex_in(node, "Film Thickness (nm)")
        if amount != 1.0:
            thickness = self.math("MULTIPLY", thickness, amount, node, "film_amount")
        spec.inputs["Thin Film Thickness"] = thickness
        spec.inputs["Thin Film IOR"] = self.tex_in(node, "Film IOR")

    def fresnel_color(self, fresnel):
        if fresnel.input_type == "color":
            return self.tex_in(fresnel, "Reflection Color")
        if fresnel.input_type == "preset":
            return METAL_PRESET_COLORS.get(fresnel.preset, DEFAULT_METAL_COLOR)
        if fresnel.input_type == "custom_nk":
            result = []
            for n, k in zip(fresnel.n, fresnel.k):
                result.append(((n - 1) ** 2 + k ** 2) / ((n + 1) ** 2 + k ** 2))
            return tuple(result)
        self.warn(f'Material "{self.material.name}": the metal color of an nk file is gray in the EEVEE nodes')
        return DEFAULT_METAL_COLOR

    # Textures ----------------------------------------------------------------

    def image_node(self, node):
        spec = self.add(node, "", "ShaderNodeTexImage")
        spec.props["image"] = node.image
        spec.props["interpolation"] = _INTERPOLATIONS.get(node.filter, "Linear")
        spec.props["extension"] = _EXTENSIONS.get(node.wrap, "REPEAT")
        if node.wrap == "white":
            self.warn(f'Material "{self.material.name}": the white image wrap is black in the EEVEE nodes')
        if node.projection == "box":
            spec.props["projection"] = "BOX"
            spec.props["projection_blend"] = 0.2
            coords = self.add(node, "object", "ShaderNodeTexCoord", -400, 0)
            spec.inputs["Vector"] = Ref(coords.name, "Object")
        else:
            spec.props["projection"] = "FLAT"
            vector = self.mapping2d(_input(node, "2D Mapping"))
            if vector is not None:
                spec.inputs["Vector"] = vector
        return spec

    def gamma(self, node, ref, gamma, role="gamma"):
        if abs(gamma - 1) < 1e-3:
            return ref
        spec = self.add(node, role, "ShaderNodeGamma", 200, -150)
        spec.inputs["Color"] = ref
        spec.inputs["Gamma"] = gamma
        return Ref(spec.name, "Color")

    def imagemap_color(self, node, normal_map=False):
        """The color output of an Image Texture node decoded the way the LuxCore image map is"""
        if node.image is None:
            return None
        image = self.image_node(node)
        ref = Ref(image.name, "Color")
        settings = node.image.colorspace_settings
        is_data = settings.is_data
        is_linear = _is_linear_space(settings.name)
        if normal_map:
            # LuxCore reads normal maps without colour conversion
            if not is_data and not is_linear:
                ref = self.gamma(node, ref, 1 / 2.2)
            return ref

        gamma = node.gamma
        if is_data:
            # Cycles reads the raw values
            return self.gamma(node, ref, gamma)
        if abs(gamma - 1) < 1e-6:
            # LuxCore: raw values (encoded image) or a colour space conversion (linear image)
            return ref if is_linear else self.gamma(node, ref, 1 / 2.2)
        if is_linear:
            return self.gamma(node, ref, gamma)
        # Cycles decodes about with gamma 2.2
        return self.gamma(node, ref, gamma / 2.2)

    def mapping2d(self, socket):
        link = _get_link(socket) if socket is not None else None
        if link is None:
            return None
        mapping = link.from_node
        if mapping.bl_idname != "LuxCoreNodeTexMapping2D":
            self.warn_type(mapping, "is not supported as a 2D mapping in the EEVEE nodes")
            return None
        key = ("mapping2d", self.prefix, mapping.name)
        if key in self.tex_memo:
            return self.tex_memo[key]

        uv = self.add(mapping, "uv", "ShaderNodeUVMap", -200, 0)
        uv.props["uv_map"] = mapping.uvmap
        if mapping.mapping_type != "uvmapping2d":
            self.warn(f'Material "{self.material.name}": randomized UV mapping is plain UV in the EEVEE nodes')
            result = Ref(uv.name, "UV")
            self.tex_memo[key] = result
            return result

        if _get_link(_input(mapping, "2D Mapping (optional)")):
            self.warn(f'Material "{self.material.name}": chained 2D mapping nodes use only the last one '
                      "in the EEVEE nodes")
        if mapping.center_rotation:
            self.warn(f'Material "{self.material.name}": "Center Rotation" is ignored in the EEVEE nodes')

        if mapping.use_uniform_scale:
            us = vs = mapping.uniform_scale
        else:
            us, vs = mapping.uscale, mapping.vscale
        # The inverse of the reader's mapping: uvdelta = [lx, 1 - ly]
        # (nodes/textures/mapping2d.py export_uvmapping2d)
        if mapping.center_map:
            lx = mapping.udelta + 0.5 * (1 - us)
            ly = mapping.vdelta + 0.5 * (1 - vs)
        else:
            lx = mapping.udelta
            ly = -mapping.vdelta

        spec = self.add(mapping, "", "ShaderNodeMapping")
        spec.props["vector_type"] = "POINT"
        spec.inputs["Vector"] = Ref(uv.name, "UV")
        spec.inputs["Location"] = (lx, ly, 0.0)
        spec.inputs["Rotation"] = (0.0, 0.0, mapping.rotation)
        spec.inputs["Scale"] = (us, vs, 1.0)
        result = Ref(spec.name, "Vector")
        self.tex_memo[key] = result
        return result

    def mapping3d(self, socket):
        link = _get_link(socket) if socket is not None else None
        if link is None:
            # The LuxCore default is the global mapping
            return self.position()
        mapping = link.from_node
        if mapping.bl_idname != "LuxCoreNodeTexMapping3D":
            self.warn_type(mapping, "is not supported as a 3D mapping in the EEVEE nodes")
            return self.position()
        key = ("mapping3d", self.prefix, mapping.name)
        if key in self.tex_memo:
            return self.tex_memo[key]

        mapping_type = mapping.mapping_type
        if mapping_type == "globalmapping3d":
            coords = self.position()
        elif mapping_type == "uvmapping3d":
            uv = self.add(mapping, "uv", "ShaderNodeUVMap", -200, 0)
            uv.props["uv_map"] = mapping.uvmap
            coords = Ref(uv.name, "UV")
        else:
            if mapping_type == "localrandommapping3d":
                self.warn(f'Material "{self.material.name}": random local mapping is plain local '
                          "mapping in the EEVEE nodes")
            texcoord = self.add(mapping, "object", "ShaderNodeTexCoord", -200, 0)
            coords = Ref(texcoord.name, "Object")
        if _get_link(_input(mapping, "3D Mapping (optional)")):
            self.warn(f'Material "{self.material.name}": chained 3D mapping nodes use only the last one '
                      "in the EEVEE nodes")

        if mapping.use_uniform_scale:
            scale = (mapping.uniform_scale,) * 3
        else:
            scale = tuple(mapping.scale)
        spec = self.add(mapping, "", "ShaderNodeMapping")
        # LuxCore transforms the texture (translate @ rotate @ scale), the
        # inverse of what it does to the coordinates
        spec.props["vector_type"] = "TEXTURE"
        spec.inputs["Vector"] = coords
        spec.inputs["Location"] = tuple(mapping.translate)
        spec.inputs["Rotation"] = tuple(mapping.rotate)
        spec.inputs["Scale"] = scale
        result = Ref(spec.name, "Vector")
        self.tex_memo[key] = result
        return result

    def bright_contrast(self, node, ref):
        """Blender texture brightness and contrast: (v - 0.5) * contrast + bright - 0.5"""
        bright = getattr(node, "bright", 1.0)
        contrast = getattr(node, "contrast", 1.0)
        if abs(bright - 1) < 1e-6 and abs(contrast - 1) < 1e-6:
            return ref
        return self.math("MULTIPLY_ADD", ref, contrast, node, "bright_contrast", c=bright - 0.5 - 0.5 * contrast)

    # Entry -------------------------------------------------------------------

    def plan_output(self, node_tree):
        output = _get_active_output(node_tree)
        location = (output.location.x, output.location.y) if output else (300.0, 0.0)
        spec = self.add_raw(OUTPUT_NAME, "ShaderNodeOutputMaterial", location)
        spec.props["target"] = "ALL"
        if output is None:
            spec.inputs["Surface"] = self.black()
            return
        if output.is_holdout:
            holdout = self.add(output, "holdout", "ShaderNodeHoldout", -200, 0)
            spec.inputs["Surface"] = Ref(holdout.name, "Holdout")
            return
        surface = self.shader(_input(output, "Material"))
        spec.inputs["Surface"] = surface if surface is not None else self.black()


# ------------------------------------------------------------------------------
# Material handlers: (planner, LuxCore node) -> _Shader
# ------------------------------------------------------------------------------

def _mat_openpbr(p, node):
    spec = p.principled(node)
    g = lambda name: p.tex_in(node, name)

    base = g("Base Color")
    weight = g("Base Weight")
    if _is_ref(weight) or (weight is not None and abs(_as_float(weight) - 1) > 1e-6):
        base = p.multiply_color(base, weight, node, "base_weight")
    spec.inputs["Base Color"] = base
    spec.inputs["Metallic"] = g("Metalness")
    spec.inputs["Diffuse Roughness"] = g("Diffuse Roughness")
    level = p.math("MULTIPLY", g("Specular Weight"), 0.5, node, "specular_level")
    spec.inputs["Specular IOR Level"] = min(max(level, 0.0), 1.0) if _is_const(level) else level
    spec.inputs["Specular Tint"] = g("Specular Color")
    spec.inputs["Roughness"] = g("Specular Roughness")
    spec.inputs["IOR"] = g("Specular IOR")
    spec.inputs["Anisotropic"] = g("Specular Anisotropy")
    spec.inputs["Anisotropic Rotation"] = g("Tangent Rotation")

    if getattr(node, "use_transmission", False):
        spec.inputs["Transmission Weight"] = g("Transmission Weight")
        color = g("Transmission Color")
        if _is_ref(color) or (color is not None and min(_as_color(color)) < 1 - 1e-6):
            p.warn(f'Material "{p.material.name}": the OpenPBR transmission color is white in the EEVEE nodes')
    if getattr(node, "use_subsurface", False):
        spec.inputs["Subsurface Weight"] = g("Subsurface Weight")
        spec.inputs["Subsurface Radius"] = g("Radius Scale")
        spec.inputs["Subsurface Scale"] = g("Subsurface Radius")
        spec.inputs["Subsurface Anisotropy"] = g("Subsurface Anisotropy")
    if getattr(node, "use_coat", False):
        spec.inputs["Coat Weight"] = g("Coat Weight")
        spec.inputs["Coat Roughness"] = g("Coat Roughness")
        spec.inputs["Coat IOR"] = g("Coat IOR")
        spec.inputs["Coat Tint"] = g("Coat Color")
    if getattr(node, "use_fuzz", False):
        spec.inputs["Sheen Weight"] = g("Fuzz Weight")
        spec.inputs["Sheen Tint"] = g("Fuzz Color")
        spec.inputs["Sheen Roughness"] = g("Fuzz Roughness")
    if getattr(node, "use_thin_film", False):
        weight = g("Thin Film Weight")
        thickness = g("Thin Film Thickness (nm)")
        if _is_ref(weight) or (weight is not None and _as_float(weight) > 0):
            if _is_ref(weight) or abs(_as_float(weight) - 1) > 1e-6:
                thickness = p.math("MULTIPLY", thickness, weight, node, "thin_film_weight")
            spec.inputs["Thin Film Thickness"] = thickness
            spec.inputs["Thin Film IOR"] = g("Thin Film IOR")
    return _Shader(Ref(spec.name, "BSDF"), spec, [spec])


def _mat_disney(p, node):
    spec = p.principled(node)
    g = lambda name: p.tex_in(node, name)
    base = g("Base Color")
    spec.inputs["Base Color"] = base
    spec.inputs["Metallic"] = g("Metallic")
    spec.inputs["Specular IOR Level"] = g("Specular")
    spec.inputs["Roughness"] = g("Roughness")
    spec.inputs["Anisotropic"] = g("Anisotropic")

    specular_tint = g("Specular Tint")
    if _is_const(specular_tint) and _as_float(specular_tint) > 0:
        if _is_const(base):
            lum = _luminance(base)
            tint = tuple(c / lum for c in _as_color(base)) if lum > 0 else (1.0, 1.0, 1.0)
            t = _as_float(specular_tint)
            spec.inputs["Specular Tint"] = tuple(1 - t + t * c for c in tint)
        else:
            p.warn(f'Material "{p.material.name}": the Disney specular tint of a textured base color is '
                   "white in the EEVEE nodes")
    elif _is_ref(specular_tint):
        p.warn(f'Material "{p.material.name}": a textured Disney specular tint is white in the EEVEE nodes')

    subsurface = g("Subsurface")
    spec.inputs["Subsurface Weight"] = subsurface
    if _is_ref(subsurface) or (subsurface is not None and _as_float(subsurface) > 0):
        spec.inputs["Subsurface Radius"] = (1.0, 0.2, 0.1)
        spec.inputs["Subsurface Scale"] = 0.05

    sheen = g("Sheen")
    spec.inputs["Sheen Weight"] = min(_as_float(sheen), 1.0) if _is_const(sheen) else sheen
    sheen_tint = g("Sheen Tint")
    if _is_const(sheen_tint) and _is_const(base):
        t = _as_float(sheen_tint)
        spec.inputs["Sheen Tint"] = tuple(1 - t + t * c for c in _as_color(base))

    spec.inputs["Coat Weight"] = g("Clearcoat")
    gloss = g("Clearcoat Gloss")
    spec.inputs["Coat Roughness"] = p.math("SUBTRACT", 1.0, gloss, node, "coat_roughness")

    if getattr(node, "use_thinfilmcoating", False):
        amount = g("Film Amount")
        p.thin_film(spec, node, amount if _is_const(amount) else 1.0)
    return _Shader(Ref(spec.name, "BSDF"), spec, [spec])


def _mat_matte(p, node):
    color = p.tex_in(node, "Diffuse Color")
    if _is_black(color) and p.emission(node) is not None:
        emission_color, strength = p.emission(node)
        spec = p.add(node, "", "ShaderNodeEmission")
        spec.inputs["Color"] = emission_color
        spec.inputs["Strength"] = strength
        return _Shader(Ref(spec.name, "Emission"), has_emission=True)

    spec = p.add(node, "", "ShaderNodeBsdfDiffuse")
    spec.inputs["Color"] = color
    # LuxCore's sigma is the roughness of the energy-preserving Oren-Nayar model, as the
    # Diffuse BSDF's (no angle)
    sigma = p.tex_in(node, "Sigma")
    if _is_ref(sigma):
        spec.inputs["Roughness"] = sigma
    else:
        spec.inputs["Roughness"] = min(_as_float(sigma or 0.0), 1.0)
    return _Shader(Ref(spec.name, "BSDF"), normals=[spec])


def _mat_glossy2(p, node):
    spec = p.principled(node)
    spec.inputs["Base Color"] = p.tex_in(node, "Diffuse Color")
    roughness, anisotropy = p.roughness(node)
    spec.inputs["Roughness"] = roughness
    spec.inputs["Anisotropic"] = anisotropy
    p.specular_from_color(spec, node)
    return _Shader(Ref(spec.name, "BSDF"), spec, [spec])


def _mat_metal(p, node):
    spec = p.principled(node)
    spec.inputs["Metallic"] = 1.0
    if node.input_type == "color":
        spec.inputs["Base Color"] = p.tex_in(node, "Color")
    else:
        link = _get_link(_input(node, "Fresnel"))
        if link is not None and link.from_node.bl_idname == "LuxCoreNodeTexFresnel":
            spec.inputs["Base Color"] = p.fresnel_color(link.from_node)
        else:
            spec.inputs["Base Color"] = DEFAULT_METAL_COLOR
    roughness, anisotropy = p.roughness(node)
    spec.inputs["Roughness"] = roughness
    spec.inputs["Anisotropic"] = anisotropy
    return _Shader(Ref(spec.name, "BSDF"), spec, [spec])


def _mat_glass(p, node):
    spec = p.principled(node)
    spec.inputs["Transmission Weight"] = 1.0
    spec.inputs["Base Color"] = p.tex_in(node, "Transmission Color")
    spec.inputs["Specular Tint"] = p.tex_in(node, "Reflection Color")
    spec.inputs["IOR"] = p.tex_in(node, "IOR")
    if node.rough:
        roughness, anisotropy = p.roughness(node)
        spec.inputs["Roughness"] = roughness
        spec.inputs["Anisotropic"] = anisotropy
    else:
        spec.inputs["Roughness"] = 0.0
    dispersion = p.tex_in(node, "Dispersion")
    if _is_ref(dispersion) or (dispersion is not None and _as_float(dispersion) > 0):
        p.warn(f'Material "{p.material.name}": glass dispersion is ignored in the EEVEE nodes')
    if getattr(node, "use_thinfilmcoating", False):
        p.thin_film(spec, node)
    return _Shader(Ref(spec.name, "BSDF"), spec, [spec])


def _mat_mirror(p, node):
    spec = p.principled(node)
    spec.inputs["Metallic"] = 1.0
    spec.inputs["Roughness"] = 0.0
    spec.inputs["Base Color"] = p.tex_in(node, "Reflection Color")
    return _Shader(Ref(spec.name, "BSDF"), spec, [spec])


def _mat_mix(p, node):
    spec = p.add(node, "", "ShaderNodeMixShader")
    spec.inputs["Fac"] = p.tex_in(node, "Mix Factor")
    spec.inputs["Shader"] = p.shader(_input(node, "Material 1")) or p.black()
    spec.inputs["Shader_001"] = p.shader(_input(node, "Material 2")) or p.black()
    return _Shader(Ref(spec.name, "Shader"))


def _mat_null(p, node):
    spec = p.add(node, "", "ShaderNodeBsdfTransparent")
    spec.inputs["Color"] = p.tex_in(node, "Transmission Color")
    return _Shader(Ref(spec.name, "BSDF"))


def _mat_matte_translucent(p, node):
    diffuse = p.add(node, "diffuse", "ShaderNodeBsdfDiffuse", -200, 100)
    diffuse.inputs["Color"] = p.tex_in(node, "Reflection Color")
    translucent = p.add(node, "translucent", "ShaderNodeBsdfTranslucent", -200, -100)
    translucent.inputs["Color"] = p.tex_in(node, "Transmission Color")
    mix = p.add(node, "", "ShaderNodeMixShader")
    mix.inputs["Fac"] = 0.5
    mix.inputs["Shader"] = Ref(diffuse.name, "BSDF")
    mix.inputs["Shader_001"] = Ref(translucent.name, "BSDF")
    return _Shader(Ref(mix.name, "Shader"), normals=[diffuse, translucent])


def _mat_glossy_translucent(p, node):
    glossy = p.principled(node, "glossy")
    glossy.inputs["Base Color"] = p.tex_in(node, "Diffuse Color")
    roughness, anisotropy = p.roughness(node)
    glossy.inputs["Roughness"] = roughness
    glossy.inputs["Anisotropic"] = anisotropy
    p.specular_from_color(glossy, node)
    translucent = p.add(node, "translucent", "ShaderNodeBsdfTranslucent", -200, -400)
    translucent.inputs["Color"] = p.tex_in(node, "Transmission Color")
    mix = p.add(node, "", "ShaderNodeMixShader")
    mix.inputs["Fac"] = 0.5
    mix.inputs["Shader"] = Ref(glossy.name, "BSDF")
    mix.inputs["Shader_001"] = Ref(translucent.name, "BSDF")
    return _Shader(Ref(mix.name, "Shader"), normals=[glossy, translucent])


def _mat_glossy_coating(p, node):
    base = p.shader(_input(node, "Base Material")) or p.black()

    reflection = p.principled(node, "reflection")
    reflection.inputs["Metallic"] = 1.0
    roughness, anisotropy = p.roughness(node)
    reflection.inputs["Roughness"] = roughness
    reflection.inputs["Anisotropic"] = anisotropy

    if node.use_ior:
        ior = p.tex_in(node, "IOR")
        reflection.inputs["Base Color"] = (1.0, 1.0, 1.0)
    else:
        ks = p.tex_in(node, "Specular Color")
        if _is_ref(ks):
            ior = 1.5
            reflection.inputs["Base Color"] = ks
        else:
            color = _as_color(ks if ks is not None else 0.05)
            peak = max(color)
            f0 = min(max(peak, 1e-4), 0.95)
            root = math.sqrt(f0)
            ior = (1 + root) / (1 - root)
            reflection.inputs["Base Color"] = tuple(c / peak for c in color) if peak > 0 else (1.0, 1.0, 1.0)

    fresnel = p.add(node, "fresnel", "ShaderNodeFresnel", -200, 200)
    fresnel.inputs["IOR"] = ior
    mix = p.add(node, "", "ShaderNodeMixShader")
    mix.inputs["Fac"] = Ref(fresnel.name, "Fac")
    mix.inputs["Shader"] = base
    mix.inputs["Shader_001"] = Ref(reflection.name, "BSDF")
    return _Shader(Ref(mix.name, "Shader"), normals=[reflection])


def _mat_subsurface(p, node):
    spec = p.principled(node)
    spec.inputs["Subsurface Weight"] = 1.0
    spec.inputs["Base Color"] = p.tex_in(node, "Color")
    scatter = p.tex_in(node, "Scatter Color")
    radius = p.tex_in(node, "Radius")
    if _is_const(scatter) and _is_const(radius):
        spec.inputs["Subsurface Radius"] = tuple(s * r for s, r in zip(_as_color(scatter), _as_color(radius)))
        spec.inputs["Subsurface Scale"] = 1.0
    else:
        spec.inputs["Subsurface Radius"] = scatter
        if _is_const(radius):
            spec.inputs["Subsurface Scale"] = _as_float(radius)
        else:
            spec.inputs["Subsurface Scale"] = 0.05
            p.warn(f'Material "{p.material.name}": a textured subsurface radius is constant in the EEVEE nodes')
    spec.inputs["IOR"] = p.tex_in(node, "IOR")
    spec.inputs["Roughness"] = p.tex_in(node, "Roughness")
    spec.inputs["Subsurface Anisotropy"] = p.tex_in(node, "Anisotropy")
    return _Shader(Ref(spec.name, "BSDF"), spec, [spec])


def _mat_velvet(p, node):
    spec = p.add(node, "", "ShaderNodeBsdfSheen")
    spec.props["distribution"] = "MICROFIBER"
    spec.inputs["Color"] = p.tex_in(node, "Diffuse Color")
    spec.inputs["Roughness"] = p.tex_in(node, "Roughness")
    return _Shader(Ref(spec.name, "BSDF"), normals=[spec])


def _mat_two_sided(p, node):
    geometry = p.add(node, "geometry", "ShaderNodeNewGeometry", -200, 200)
    spec = p.add(node, "", "ShaderNodeMixShader")
    spec.inputs["Fac"] = Ref(geometry.name, "Backfacing")
    spec.inputs["Shader"] = p.shader(_input(node, "Front Material")) or p.black()
    spec.inputs["Shader_001"] = p.shader(_input(node, "Back Material")) or p.black()
    return _Shader(Ref(spec.name, "Shader"))


def _mat_front_back_opacity(p, node):
    material = p.shader(_input(node, "Material")) or p.black()
    geometry = p.add(node, "geometry", "ShaderNodeNewGeometry", -400, 200)
    opacity = p.add(node, "opacity_mix", "ShaderNodeMix", -200, 200)
    opacity.props["data_type"] = "FLOAT"
    opacity.inputs["Factor_Float"] = Ref(geometry.name, "Backfacing")
    opacity.inputs["A_Float"] = p.tex_in(node, "Front Opacity")
    opacity.inputs["B_Float"] = p.tex_in(node, "Back Opacity")
    transparent = p.add(node, "transparent", "ShaderNodeBsdfTransparent", -200, -100)
    spec = p.add(node, "", "ShaderNodeMixShader")
    spec.inputs["Fac"] = Ref(opacity.name, "Result_Float")
    spec.inputs["Shader"] = Ref(transparent.name, "BSDF")
    spec.inputs["Shader_001"] = material
    return _Shader(Ref(spec.name, "Shader"))


def _mat_carpaint(p, node):
    p.warn_type(node, "is approximated by a coated Principled BSDF in the EEVEE nodes")
    spec = p.principled(node)
    spec.inputs["Base Color"] = p.tex_in(node, "Diffuse Color")
    spec.inputs["Roughness"] = 0.3
    spec.inputs["Coat Weight"] = 1.0
    spec.inputs["Coat Roughness"] = 0.05
    return _Shader(Ref(spec.name, "BSDF"), spec, [spec])


def _mat_cloth(p, node):
    p.warn_type(node, "is approximated by a Principled BSDF with sheen in the EEVEE nodes")
    spec = p.principled(node)
    spec.inputs["Base Color"] = p.tex_in(node, "Wrap Diffuse Color")
    spec.inputs["Roughness"] = 0.8
    spec.inputs["Sheen Weight"] = 1.0
    return _Shader(Ref(spec.name, "BSDF"), spec, [spec])


MATERIAL_HANDLERS = {
    "LuxCoreNodeMatOpenPBR": _mat_openpbr,
    "LuxCoreNodeMatDisney": _mat_disney,
    "LuxCoreNodeMatMatte": _mat_matte,
    "LuxCoreNodeMatGlossy2": _mat_glossy2,
    "LuxCoreNodeMatMetal": _mat_metal,
    "LuxCoreNodeMatGlass": _mat_glass,
    "LuxCoreNodeMatMirror": _mat_mirror,
    "LuxCoreNodeMatMix": _mat_mix,
    "LuxCoreNodeMatNull": _mat_null,
    "LuxCoreNodeMatMatteTranslucent": _mat_matte_translucent,
    "LuxCoreNodeMatGlossyTranslucent": _mat_glossy_translucent,
    "LuxCoreNodeMatGlossyCoating": _mat_glossy_coating,
    "LuxCoreNodeMatSubsurface": _mat_subsurface,
    "LuxCoreNodeMatVelvet": _mat_velvet,
    "LuxCoreNodeMatTwoSided": _mat_two_sided,
    "LuxCoreNodeMatFrontBackOpacity": _mat_front_back_opacity,
    "LuxCoreNodeMatCarpaint": _mat_carpaint,
    "LuxCoreNodeMatCloth": _mat_cloth,
}


# ------------------------------------------------------------------------------
# Texture handlers: (planner, LuxCore node, output socket) -> constant, Ref or None
# ------------------------------------------------------------------------------

def _tex_imagemap(p, node, output_socket):
    if node.image is None:
        return (0.0, 0.0, 0.0)
    if node.is_normal_map:
        # Only meaningful through a Bump socket (Planner.bump)
        return None
    if output_socket.name == "Alpha":
        image = p.image_node(node)
        ref = Ref(image.name, "Alpha")
        return p.math("MULTIPLY", ref, node.brightness, node, "brightness") if node.brightness != 1 else ref

    ref = p.imagemap_color(node)
    channel = node.channel
    if channel in {"red", "green", "blue"}:
        separate = p.add(node, "channel", "ShaderNodeSeparateColor", 200, -200)
        separate.inputs["Color"] = ref
        ref = Ref(separate.name, channel.capitalize())
        if node.brightness != 1:
            ref = p.math("MULTIPLY", ref, node.brightness, node, "brightness")
        return ref
    if channel == "alpha":
        image = p.image_node(node)
        ref = Ref(image.name, "Alpha")
        if node.brightness != 1:
            ref = p.math("MULTIPLY", ref, node.brightness, node, "brightness")
        return ref
    if channel in {"mean", "colored_mean"}:
        bw = p.add(node, "channel", "ShaderNodeRGBToBW", 200, -200)
        bw.inputs["Color"] = ref
        ref = Ref(bw.name, "Val")
        if node.brightness != 1:
            ref = p.math("MULTIPLY", ref, node.brightness, node, "brightness")
        return ref
    if node.brightness != 1:
        ref = p.multiply_color(ref, node.brightness, node, "brightness")
    return ref


def _tex_math(p, node, output_socket):
    a = p.tex(node.inputs[0])
    b = p.tex(node.inputs[1])
    mode = node.mode
    if mode == "mix":
        spec = p.add(node, "", "ShaderNodeMix")
        spec.props["data_type"] = "FLOAT"
        spec.inputs["Factor_Float"] = p.tex(node.inputs[2])
        spec.inputs["A_Float"] = a
        spec.inputs["B_Float"] = b
        ref = Ref(spec.name, "Result_Float")
        if node.clamp_output:
            clamp = p.add(node, "clamp", "ShaderNodeClamp", 200, 0)
            clamp.inputs["Value"] = ref
            clamp.inputs["Min"] = 0.0
            clamp.inputs["Max"] = 1.0
            ref = Ref(clamp.name, "Result")
        return ref
    if mode == "clamp":
        spec = p.add(node, "", "ShaderNodeClamp")
        spec.inputs["Value"] = a
        spec.inputs["Min"] = node.mode_clamp_min
        spec.inputs["Max"] = node.mode_clamp_max
        return Ref(spec.name, "Result")

    spec = p.add(node, "", "ShaderNodeMath")
    spec.inputs["Value"] = a
    if mode == "rounding":
        if _is_const(b) and abs(_as_float(b) - 1) < 1e-6:
            spec.props["operation"] = "ROUND"
        else:
            # Rounding to the nearest increment: snap(v + increment / 2, increment)
            p.warn(f'Material "{p.material.name}": "Round" with an increment is approximated in the EEVEE nodes')
            spec.props["operation"] = "SNAP"
            spec.inputs["Value"] = p.math("MULTIPLY_ADD", b, 0.5, node, "round_offset", c=a)
            spec.inputs["Value_001"] = b
    else:
        spec.props["operation"] = _MATH_OPERATIONS.get(mode, "MULTIPLY")
        if mode not in {"sqrt", "abs"}:
            spec.inputs["Value_001"] = b
    spec.props["use_clamp"] = node.clamp_output
    return Ref(spec.name, "Value")


def _tex_colormix(p, node, output_socket):
    mode = node.mode
    if mode in {"abs", "clamp"}:
        color = p.tex(node.inputs[1])
        if mode == "abs":
            spec = p.add(node, "", "ShaderNodeVectorMath")
            spec.props["operation"] = "ABSOLUTE"
            spec.inputs["Vector"] = color
            return Ref(spec.name, "Vector")
        low = p.add(node, "max", "ShaderNodeVectorMath", -150, 0)
        low.props["operation"] = "MAXIMUM"
        low.inputs["Vector"] = color
        low.inputs["Vector_001"] = (node.mode_clamp_min,) * 3
        spec = p.add(node, "", "ShaderNodeVectorMath")
        spec.props["operation"] = "MINIMUM"
        spec.inputs["Vector"] = Ref(low.name, "Vector")
        spec.inputs["Vector_001"] = (node.mode_clamp_max,) * 3
        return Ref(spec.name, "Vector")

    spec = p.add(node, "", "ShaderNodeMix")
    spec.props["data_type"] = "RGBA"
    spec.props["blend_type"] = _MIX_BLEND_TYPES.get(mode, "MIX")
    spec.props["clamp_result"] = node.clamp_output
    spec.inputs["Factor_Float"] = p.tex(node.inputs[0]) if mode == "mix" else 1.0
    spec.inputs["A_Color"] = p.tex(node.inputs[1])
    spec.inputs["B_Color"] = p.tex(node.inputs[2])
    return Ref(spec.name, "Result_Color")


def _tex_vectormath(p, node, output_socket):
    mode = node.mode
    v1 = p.tex_in(node, "Vector 1")
    v2 = p.tex_in(node, "Vector 2")
    if mode == "mix":
        spec = p.add(node, "", "ShaderNodeMix")
        spec.props["data_type"] = "VECTOR"
        spec.inputs["Factor_Float"] = p.tex_in(node, "Fac")
        spec.inputs["A_Vector"] = v1
        spec.inputs["B_Vector"] = v2
        return Ref(spec.name, "Result_Vector")
    if mode == "clamp":
        low = p.add(node, "max", "ShaderNodeVectorMath", -150, 0)
        low.props["operation"] = "MAXIMUM"
        low.inputs["Vector"] = v1
        low.inputs["Vector_001"] = (node.mode_clamp_min,) * 3
        spec = p.add(node, "", "ShaderNodeVectorMath")
        spec.props["operation"] = "MINIMUM"
        spec.inputs["Vector"] = Ref(low.name, "Vector")
        spec.inputs["Vector_001"] = (node.mode_clamp_max,) * 3
        return Ref(spec.name, "Vector")
    spec = p.add(node, "", "ShaderNodeVectorMath")
    spec.props["operation"] = _VECTOR_OPERATIONS.get(mode, "MULTIPLY")
    spec.inputs["Vector"] = v1
    if mode != "abs":
        spec.inputs["Vector_001"] = v2
    return Ref(spec.name, "Vector")


def _tex_band(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeValToRGB")
    spec.inputs["Fac"] = p.tex_in(node, "Amount")
    items = sorted(((item.offset, tuple(item.value)) for item in node.ramp_items), key=lambda e: e[0])
    if not items:
        items = [(0.0, (0.0, 0.0, 0.0)), (1.0, (1.0, 1.0, 1.0))]
    spec.props["color_ramp_interpolation"] = _RAMP_INTERPOLATIONS.get(node.interpolation, "LINEAR")
    spec.props["color_ramp"] = [(offset, _as_color(value) + (1.0,)) for offset, value in items]
    return Ref(spec.name, "Color")


def _tex_constfloat1(p, node, output_socket):
    return float(node.value)


def _tex_constfloat3(p, node, output_socket):
    return _as_color(tuple(node.value))


def _tex_hsv(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeHueSaturation")
    spec.inputs["Hue"] = p.tex_in(node, "Hue")
    spec.inputs["Saturation"] = p.tex_in(node, "Saturation")
    spec.inputs["Value"] = p.tex_in(node, "Value")
    spec.inputs["Fac"] = 1.0
    spec.inputs["Color"] = p.tex_in(node, "Color")
    return Ref(spec.name, "Color")


def _tex_invert(p, node, output_socket):
    maximum = p.tex_in(node, "Maximum")
    if _is_ref(maximum) or (maximum is not None and abs(_as_float(maximum) - 1) > 1e-6):
        p.warn(f'Material "{p.material.name}": Invert with a maximum other than 1 is approximated '
               "in the EEVEE nodes")
    spec = p.add(node, "", "ShaderNodeInvert")
    spec.inputs["Fac"] = 1.0
    spec.inputs["Color"] = p.tex_in(node, "Color")
    return Ref(spec.name, "Color")


def _tex_brightcontrast(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeBrightContrast")
    spec.inputs["Color"] = p.tex_in(node, "Color")
    spec.inputs["Bright"] = p.tex_in(node, "Brightness")
    spec.inputs["Contrast"] = p.tex_in(node, "Contrast")
    return Ref(spec.name, "Color")


def _tex_remap(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeMapRange")
    spec.props["data_type"] = "FLOAT"
    spec.props["interpolation_type"] = "LINEAR"
    spec.props["clamp"] = True
    spec.inputs["Value"] = p.tex_in(node, "Value")
    spec.inputs["From Min"] = p.tex_in(node, "Source Min")
    spec.inputs["From Max"] = p.tex_in(node, "Source Max")
    spec.inputs["To Min"] = p.tex_in(node, "Target Min")
    spec.inputs["To Max"] = p.tex_in(node, "Target Max")
    return Ref(spec.name, "Result")


def _tex_splitfloat3(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeSeparateColor")
    spec.inputs["Color"] = p.tex_in(node, "Color")
    channel = {"R": "Red", "G": "Green", "B": "Blue"}.get(output_socket.name, "Red")
    return Ref(spec.name, channel)


def _tex_makefloat3(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeCombineColor")
    spec.inputs["Red"] = p.tex_in(node, "R")
    spec.inputs["Green"] = p.tex_in(node, "G")
    spec.inputs["Blue"] = p.tex_in(node, "B")
    return Ref(spec.name, "Color")


def _tex_dotproduct(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeVectorMath")
    spec.props["operation"] = "DOT_PRODUCT"
    spec.inputs["Vector"] = p.tex_in(node, "Vector 1")
    spec.inputs["Vector_001"] = p.tex_in(node, "Vector 2")
    return Ref(spec.name, "Value")


def _tex_checkerboard(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeTexChecker")
    # Swapped like the reader does (export/cycles_node_reader.py, TexChecker)
    spec.inputs["Color1"] = p.tex_in(node, "Color 2")
    spec.inputs["Color2"] = p.tex_in(node, "Color 1")
    spec.inputs["Scale"] = 1.0
    if node.bl_idname == "LuxCoreNodeTexCheckerboard2D":
        vector = p.mapping2d(_input(node, "2D Mapping"))
        spec.inputs["Vector"] = vector if vector is not None else p.uv()
    else:
        spec.inputs["Vector"] = p.mapping3d(_input(node, "3D Mapping"))
    return Ref(spec.name, "Color")


def _tex_blackbody(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeBlackbody")
    spec.inputs["Temperature"] = node.temperature
    color = Ref(spec.name, "Color")
    # Cycles' color has luminance 1, as LuxCore's Luminance normalization: Max (Legacy)
    # and Normalize off are scaled to LuxCore's brightness
    normalization = (utils_temperature.get(node, utils_temperature.NODE_PROP)
                     if node.normalize else None)
    factor = utils_temperature.luminance_factor(node.temperature, normalization)
    if factor != 1.0:
        color = p.multiply_color(color, factor, node, "normalization")
    return color


def _tex_hitpointinfo(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeNewGeometry")
    return Ref(spec.name, "Normal" if output_socket.name == "Shading Normal" else "Position")


def _tex_objectid(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeObjectInfo")
    if node.mode == "objectid":
        return Ref(spec.name, "Object Index")
    if node.mode == "objectidcolor":
        p.warn(f'Material "{p.material.name}": the object ID color is the object random value '
               "in the EEVEE nodes")
    return Ref(spec.name, "Random")


def _tex_hitpoint(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeVertexColor")
    spec.props["layer_name"] = node.vcol
    color = Ref(spec.name, "Color")
    wants_value = output_socket.name == "Value"
    if wants_value and node.channel in {"0", "1", "2"}:
        separate = p.add(node, "channel", "ShaderNodeSeparateColor", 200, 0)
        separate.inputs["Color"] = color
        return Ref(separate.name, ("Red", "Green", "Blue")[int(node.channel)])
    if wants_value or node.mode == "hitpointgrey":
        bw = p.add(node, "grey", "ShaderNodeRGBToBW", 200, -150)
        bw.inputs["Color"] = color
        return Ref(bw.name, "Val")
    return color


def _tex_pointiness(p, node, output_socket):
    p.warn_type(node, "is approximated by the Cycles pointiness in the EEVEE nodes")
    geometry = p.add(node, "", "ShaderNodeNewGeometry")
    multiplier = p.tex_in(node, "Multiplier")
    centered = p.math("SUBTRACT", Ref(geometry.name, "Pointiness"), 0.5, node, "center")
    return p.math("MULTIPLY", centered, multiplier, node, "multiplier")


def _tex_uv(p, node, output_socket):
    vector = p.mapping2d(_input(node, "2D Mapping"))
    return vector if vector is not None else p.uv()


def _tex_wireframe(p, node, output_socket):
    wireframe = p.add(node, "wireframe", "ShaderNodeWireframe", -200, 0)
    wireframe.props["use_pixel_size"] = False
    wireframe.inputs["Size"] = node.width
    spec = p.add(node, "", "ShaderNodeMix")
    spec.props["data_type"] = "RGBA"
    spec.props["blend_type"] = "MIX"
    spec.inputs["Factor_Float"] = Ref(wireframe.name, "Fac")
    spec.inputs["A_Color"] = p.tex_in(node, "Inside")
    spec.inputs["B_Color"] = p.tex_in(node, "Border")
    return Ref(spec.name, "Result_Color")


def _tex_iorpreset(p, node, output_socket):
    if output_socket.name == "IOR":
        return float(node.ior_value_float)
    if output_socket.name == "Abbe Number":
        return float(node.abbe_number)
    return None


def _tex_triplanar(p, node, output_socket):
    p.warn_type(node, "uses only its first input in the EEVEE nodes")
    return p.tex_in(node, "Color")


def _tex_pointer(p, node, output_socket):
    return p.pointer(node, LUXCORE_TEXTURE_TREE, "Color", p.tex)


def _tex_noise(p, node, output_socket):
    p.warn_type(node, "is approximated by a Noise Texture in the EEVEE nodes")
    spec = p.add(node, "", "ShaderNodeTexNoise")
    spec.inputs["Vector"] = p.mapping3d(_input(node, "3D Mapping"))
    noise_size = getattr(node, "noise_size", None)
    spec.inputs["Scale"] = 1 / noise_size if noise_size else 1.0
    if hasattr(node, "octaves"):
        spec.inputs["Detail"] = float(min(max(node.octaves - 1, 0), 15))
    elif hasattr(node, "noise_depth"):
        spec.inputs["Detail"] = float(min(node.noise_depth, 15))
    if hasattr(node, "roughness"):
        spec.inputs["Roughness"] = node.roughness
    return p.bright_contrast(node, Ref(spec.name, "Fac"))


def _tex_voronoi(p, node, output_socket):
    p.warn_type(node, "is approximated by a Voronoi Texture in the EEVEE nodes")
    spec = p.add(node, "", "ShaderNodeTexVoronoi")
    spec.inputs["Vector"] = p.mapping3d(_input(node, "3D Mapping"))
    spec.inputs["Scale"] = 1 / node.noise_size if node.noise_size else 1.0
    return p.bright_contrast(node, Ref(spec.name, "Distance"))


def _tex_wave(p, node, output_socket):
    p.warn_type(node, "is approximated by a Wave Texture in the EEVEE nodes")
    spec = p.add(node, "", "ShaderNodeTexWave")
    rings = getattr(node, "wood_type", "") in {"rings", "ringnoise"}
    spec.props["wave_type"] = "RINGS" if rings else "BANDS"
    spec.inputs["Vector"] = p.mapping3d(_input(node, "3D Mapping"))
    spec.inputs["Scale"] = 1 / node.noise_size if node.noise_size else 1.0
    noisy = node.bl_idname == "LuxCoreNodeTexBlenderMarble" or getattr(node, "wood_type", "") in {"bandnoise", "ringnoise"}
    spec.inputs["Distortion"] = float(node.turbulence) if noisy else 0.0
    if hasattr(node, "noise_depth"):
        spec.inputs["Detail"] = float(min(node.noise_depth, 15))
    return p.bright_contrast(node, Ref(spec.name, "Fac"))


def _tex_magic(p, node, output_socket):
    p.warn_type(node, "is approximated by a Magic Texture in the EEVEE nodes")
    spec = p.add(node, "", "ShaderNodeTexMagic")
    spec.props["turbulence_depth"] = min(node.noise_depth, 10)
    spec.inputs["Vector"] = p.mapping3d(_input(node, "3D Mapping"))
    spec.inputs["Distortion"] = float(node.turbulence)
    return Ref(spec.name, "Color")


def _tex_blend(p, node, output_socket):
    spec = p.add(node, "", "ShaderNodeTexGradient")
    spec.props["gradient_type"] = _GRADIENT_TYPES.get(node.progression_type, "LINEAR")
    spec.inputs["Vector"] = p.mapping3d(_input(node, "3D Mapping"))
    return p.bright_contrast(node, Ref(spec.name, "Fac"))


def _tex_brick(p, node, output_socket):
    p.warn_type(node, "is approximated by a Brick Texture in the EEVEE nodes")
    spec = p.add(node, "", "ShaderNodeTexBrick")
    spec.inputs["Vector"] = p.mapping3d(_input(node, "3D Mapping"))
    spec.inputs["Color1"] = p.tex_in(node, "Brick Color 1")
    spec.inputs["Color2"] = p.tex_in(node, "Brick Color 2")
    spec.inputs["Mortar"] = p.tex_in(node, "Mortar Color")
    spec.inputs["Scale"] = 1.0
    spec.inputs["Mortar Size"] = node.mortarsize
    spec.inputs["Brick Width"] = node.brickwidth
    spec.inputs["Row Height"] = node.brickheight
    return Ref(spec.name, "Color")


TEXTURE_HANDLERS = {
    "LuxCoreNodeTexImagemap": _tex_imagemap,
    "LuxCoreNodeTexMath": _tex_math,
    "LuxCoreNodeTexColorMix": _tex_colormix,
    "LuxCoreNodeTexVectorMath": _tex_vectormath,
    "LuxCoreNodeTexBand": _tex_band,
    "LuxCoreNodeTexConstfloat1": _tex_constfloat1,
    "LuxCoreNodeTexConstfloat3": _tex_constfloat3,
    "LuxCoreNodeTexHSV": _tex_hsv,
    "LuxCoreNodeTexInvert": _tex_invert,
    "LuxCoreNodeTexBrightContrast": _tex_brightcontrast,
    "LuxCoreNodeTexRemap": _tex_remap,
    "LuxCoreNodeTexSplitFloat3": _tex_splitfloat3,
    "LuxCoreNodeTexMakeFloat3": _tex_makefloat3,
    "LuxCoreNodeTexDotProduct": _tex_dotproduct,
    "LuxCoreNodeTexCheckerboard2D": _tex_checkerboard,
    "LuxCoreNodeTexCheckerboard3D": _tex_checkerboard,
    "LuxCoreNodeTexBlackbody": _tex_blackbody,
    "LuxCoreNodeTexHitpointInfo": _tex_hitpointinfo,
    "LuxCoreNodeTexObjectID": _tex_objectid,
    "LuxCoreNodeTexHitpoint": _tex_hitpoint,
    "LuxCoreNodeTexPointiness": _tex_pointiness,
    "LuxCoreNodeTexUV": _tex_uv,
    "LuxCoreNodeTexWireframe": _tex_wireframe,
    "LuxCoreNodeTexIORPreset": _tex_iorpreset,
    "LuxCoreNodeTexTriplanar": _tex_triplanar,
    "LuxCoreNodeTreePointer": _tex_pointer,
    "LuxCoreNodeTexfBM": _tex_noise,
    "LuxCoreNodeTexWrinkled": _tex_noise,
    "LuxCoreNodeTexWindy": _tex_noise,
    "LuxCoreNodeTexMarble": _tex_noise,
    "LuxCoreNodeTexBlenderClouds": _tex_noise,
    "LuxCoreNodeTexBlenderMusgrave": _tex_noise,
    "LuxCoreNodeTexBlenderNoise": _tex_noise,
    "LuxCoreNodeTexBlenderDistortedNoise": _tex_noise,
    "LuxCoreNodeTexBlenderStucci": _tex_noise,
    "LuxCoreNodeTexBlenderVoronoi": _tex_voronoi,
    "LuxCoreNodeTexBlenderMarble": _tex_wave,
    "LuxCoreNodeTexBlenderWood": _tex_wave,
    "LuxCoreNodeTexBlenderMagic": _tex_magic,
    "LuxCoreNodeTexBlenderBlend": _tex_blend,
    "LuxCoreNodeTexBrick": _tex_brick,
}


# ------------------------------------------------------------------------------
# Applying a plan
# ------------------------------------------------------------------------------

def _find_socket(sockets, key):
    """A socket by identifier, else by name (preferring an enabled one)"""
    for socket in sockets:
        if socket.identifier == key:
            return socket
    fallback = None
    for socket in sockets:
        if socket.name == key:
            if getattr(socket, "enabled", True):
                return socket
            if fallback is None:
                fallback = socket
    return fallback


def _coerce(current, value):
    """value shaped like a socket's current default value"""
    try:
        length = len(current)
    except TypeError:
        return _as_float(value)
    if isinstance(value, (tuple, list)):
        values = tuple(float(v) for v in value[:length])
    else:
        v = float(value)
        values = (v, v, v)[:length] if length <= 3 else (v, v, v)
    while len(values) < length:
        # Alpha of a color, or the missing component of a vector
        values += (1.0,) if length == 4 and len(values) == 3 else (0.0,)
    return values


def _differs(a, b):
    if isinstance(a, (tuple, list)) or isinstance(b, (tuple, list)):
        try:
            return any(abs(float(x) - float(y)) > 1e-6 for x, y in zip(a, b)) or len(a) != len(b)
        except TypeError:
            return True
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) > 1e-6
        except (TypeError, ValueError):
            return True
    return a != b


def _set_ramp(color_ramp, stops, interpolation):
    changed = False
    if interpolation and color_ramp.interpolation != interpolation:
        color_ramp.interpolation = interpolation
        changed = True
    elements = color_ramp.elements
    current = [(e.position, tuple(e.color)) for e in elements]
    if len(current) == len(stops) and not any(
            _differs(c[0], s[0]) or _differs(c[1], s[1]) for c, s in zip(current, stops)):
        return changed

    while len(elements) > max(len(stops), 1):
        elements.remove(elements[len(elements) - 1])
    while len(elements) < len(stops):
        elements.new(1.0)
    positions = [stop[0] for stop in stops]
    # Setting a position re-sorts the elements: repeat until they settle
    for _ in range(len(stops) + 1):
        for element, position in zip(elements, positions):
            element.position = position
        if all(abs(e.position - pos) < 1e-6 for e, pos in zip(elements, positions)):
            break
    for element, stop in zip(elements, stops):
        element.color = stop[1]
    return True


def _apply_props(node, spec):
    changed = False
    for key, value in spec.props.items():
        if key == "color_ramp":
            changed |= _set_ramp(node.color_ramp, value, spec.props.get("color_ramp_interpolation"))
            continue
        if key == "color_ramp_interpolation" or not hasattr(node, key):
            continue
        current = getattr(node, key)
        if key == "image":
            if current == value:
                continue
        elif not _differs(current, value):
            continue
        try:
            setattr(node, key, value)
            changed = True
        except (TypeError, AttributeError, ValueError) as error:
            logger.debug(f"EEVEE nodes: {node.name}.{key} = {value!r}: {error}")
    return changed


def _apply_values(node, spec):
    changed = False
    for key, value in spec.inputs.items():
        if value is None or isinstance(value, Ref):
            continue
        socket = _find_socket(node.inputs, key)
        if socket is None or not hasattr(socket, "default_value"):
            continue
        try:
            new = _coerce(socket.default_value, value)
            if _differs(socket.default_value, new):
                socket.default_value = new
                changed = True
        except (TypeError, ValueError, AttributeError) as error:
            logger.debug(f"EEVEE nodes: {node.name} input {key} = {value!r}: {error}")
    for key, value in spec.outputs.items():
        socket = _find_socket(node.outputs, key)
        if socket is None or not hasattr(socket, "default_value"):
            continue
        new = _coerce(socket.default_value, value)
        if _differs(socket.default_value, new):
            socket.default_value = new
            changed = True
    return changed


def _resolve_links(plan, nodes):
    """The plan's links as (from node, from identifier, to node, to identifier), None if one can't be resolved"""
    result = set()
    for from_name, from_key, to_name, to_key in plan.links():
        from_node = nodes.get(from_name)
        to_node = nodes.get(to_name)
        if from_node is None or to_node is None:
            return None
        from_socket = _find_socket(from_node.outputs, from_key)
        to_socket = _find_socket(to_node.inputs, to_key)
        if from_socket is None or to_socket is None:
            return None
        result.add((from_name, from_socket.identifier, to_name, to_socket.identifier))
    return result


def apply_plan(plan, node_tree):
    """Write a plan into a Cycles node tree; return (rebuilt, changed)"""
    existing = {node.name: node for node in node_tree.nodes if node.bl_idname != "NodeFrame"}
    wanted_nodes = {(spec.name, spec.bl_idname) for spec in plan.nodes.values()}
    current_nodes = {(name, node.bl_idname) for name, node in existing.items()}

    if wanted_nodes == current_nodes:
        changed = False
        for spec in plan.nodes.values():
            changed |= _apply_props(existing[spec.name], spec)
        wanted_links = _resolve_links(plan, existing)
        current_links = {(link.from_node.name, link.from_socket.identifier,
                          link.to_node.name, link.to_socket.identifier) for link in node_tree.links}
        if wanted_links is not None and wanted_links == current_links:
            for spec in plan.nodes.values():
                changed |= _apply_values(existing[spec.name], spec)
            return False, changed

    node_tree.nodes.clear()
    created = {}
    for spec in plan.nodes.values():
        node = node_tree.nodes.new(spec.bl_idname)
        node.name = spec.name
        if node.name != spec.name:
            logger.debug(f"EEVEE nodes: node name {spec.name} became {node.name}")
        node.label = spec.label
        node.location = spec.location
        node.select = False
        created[spec.name] = node
        _apply_props(node, spec)
    for spec in plan.nodes.values():
        _apply_values(created[spec.name], spec)
    for from_name, from_key, to_name, to_key in plan.links():
        from_socket = _find_socket(created[from_name].outputs, from_key)
        to_socket = _find_socket(created[to_name].inputs, to_key)
        if from_socket is None or to_socket is None:
            logger.debug(f"EEVEE nodes: no socket for link {from_name}.{from_key} -> {to_name}.{to_key}")
            continue
        node_tree.links.new(from_socket, to_socket)
    output = created.get(OUTPUT_NAME)
    if output is not None:
        output.is_active_output = True
    return True, True


# ------------------------------------------------------------------------------
# Signature and ownership
# ------------------------------------------------------------------------------

def _fmt(value):
    if isinstance(value, bpy.types.ID):
        return "ID:" + value.name
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, (int, float)):
        return f"{float(value):.5f}"
    if isinstance(value, str):
        return value
    try:
        return "(" + ",".join(_fmt(v) for v in value) + ")"
    except TypeError:
        return str(value)


def tree_signature(node_tree):
    """A hash of the content of a Cycles node tree (locations, labels and frames excluded)"""
    if node_tree is None:
        return ""
    parts = []
    for node in sorted(node_tree.nodes, key=lambda n: n.name):
        if node.bl_idname == "NodeFrame":
            continue
        parts.append(f"N|{node.name}|{node.bl_idname}|{node.mute}")
        for prop in SIGNED_PROPS:
            if hasattr(node, prop):
                try:
                    parts.append(f"P|{prop}={_fmt(getattr(node, prop))}")
                except Exception:
                    pass
        color_ramp = getattr(node, "color_ramp", None)
        if color_ramp is not None:
            parts.append("R|" + color_ramp.interpolation + "|"
                         + ";".join(_fmt((e.position, *e.color)) for e in color_ramp.elements))
        for socket in node.inputs:
            if socket.is_linked:
                value = "<link>"
            elif hasattr(socket, "default_value"):
                try:
                    value = _fmt(socket.default_value)
                except Exception:
                    value = "?"
            else:
                value = "-"
            parts.append(f"I|{socket.identifier}={value}")
        if node.bl_idname in {"ShaderNodeValue", "ShaderNodeRGB"}:
            parts.append("O|" + _fmt(node.outputs[0].default_value))
    for link in sorted(node_tree.links, key=lambda l: (l.from_node.name, l.from_socket.identifier,
                                                       l.to_node.name, l.to_socket.identifier)):
        parts.append(f"L|{link.from_node.name}:{link.from_socket.identifier}>"
                     f"{link.to_node.name}:{link.to_socket.identifier}")
    return hashlib.blake2b("\n".join(parts).encode("utf-8"), digest_size=8).hexdigest()


def _principled_defaults():
    """{identifier: value} of a fresh Principled BSDF, None if it can't be made in this context (e.g. UI draw)"""
    global _principled_defaults_cache
    if _principled_defaults_cache is not None:
        return _principled_defaults_cache
    try:
        tree = bpy.data.node_groups.new(".luxcore_principled_defaults", "ShaderNodeTree")
    except (AttributeError, RuntimeError, TypeError):
        return None
    try:
        node = tree.nodes.new("ShaderNodeBsdfPrincipled")
        defaults = {}
        for socket in node.inputs:
            if hasattr(socket, "default_value"):
                value = socket.default_value
                try:
                    defaults[socket.identifier] = tuple(value)
                except TypeError:
                    defaults[socket.identifier] = value
        _principled_defaults_cache = defaults
    finally:
        bpy.data.node_groups.remove(tree)
    return _principled_defaults_cache


def is_default_tree(node_tree, material=None):
    """True if a Cycles tree is None, empty, or the untouched default (Principled BSDF into a Material Output).

    The default Principled BSDF may also carry the viewport color, metallic
    and roughness of the material (the values Blender copies when it makes
    the default nodes).
    """
    if node_tree is None:
        return True
    nodes = [node for node in node_tree.nodes if node.bl_idname != "NodeFrame"]
    if not nodes:
        return True
    if len(nodes) != 2:
        return False
    bsdf = next((n for n in nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    output = next((n for n in nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)
    if bsdf is None or output is None or len(node_tree.links) != 1:
        return False
    link = node_tree.links[0]
    if link.from_node != bsdf or link.to_node != output or link.to_socket.name != "Surface":
        return False
    defaults = _principled_defaults()
    from_material = {}
    if material is not None:
        from_material = {
            "Base Color": tuple(material.diffuse_color),
            "Metallic": material.metallic,
            "Roughness": material.roughness,
        }
    for socket in bsdf.inputs:
        if socket.is_linked:
            return False
        if defaults is None or not hasattr(socket, "default_value"):
            continue
        default = defaults.get(socket.identifier)
        if default is None:
            continue
        value = socket.default_value
        try:
            value = tuple(value)
        except TypeError:
            pass
        if _differs(value, default):
            alternative = from_material.get(socket.identifier)
            if alternative is None or _differs(value, alternative):
                return False
    return True


def is_ours(material):
    """True if the Cycles tree of a material may be overwritten by the generator"""
    node_tree = material.node_tree
    if is_default_tree(node_tree, material):
        return True
    signature = material.luxcore.eevee_nodes_signature
    return bool(signature) and tree_signature(node_tree) == signature


def ensure_cycles_tree(material):
    """Make sure the material uses a Cycles node tree (Blender < 5.0: use_nodes)"""
    if material.node_tree is not None and (bpy.app.version >= (5, 0, 0) or material.use_nodes):
        return
    try:
        material.use_nodes = True
    except (AttributeError, TypeError) as error:
        logger.debug(f"EEVEE nodes: could not enable the nodes of {material.name}: {error}")


# ------------------------------------------------------------------------------
# Entry points
# ------------------------------------------------------------------------------

def plan_material(material):
    planner = _Planner(material)
    planner.plan_output(material.luxcore.node_tree)
    return planner.plan


def generate(material, force=False):
    """Generate the Cycles nodes of a material from its LuxCore node tree.

    Returns "rebuilt", "updated", "unchanged", "skipped_handmade" (the Cycles
    tree was edited by hand and force is False), "skipped_library" or
    "skipped_no_tree".
    """
    if material.library or getattr(material, "override_library", None):
        return "skipped_library"
    if material.luxcore.node_tree is None:
        return "skipped_no_tree"
    if not force and not is_ours(material):
        return "skipped_handmade"
    ensure_cycles_tree(material)
    node_tree = material.node_tree
    if node_tree is None:
        return "skipped_no_tree"

    plan = plan_material(material)
    rebuilt, changed = apply_plan(plan, node_tree)

    signature = tree_signature(node_tree)
    if signature != material.luxcore.eevee_nodes_signature:
        material.luxcore.eevee_nodes_signature = signature

    warnings = tuple(plan.warnings)
    if force or rebuilt or _last_warnings.get(material.name) != warnings:
        for message in warnings:
            LuxCoreErrorLog.add_warning(message, obj_name=material.name)
        _last_warnings[material.name] = warnings

    if rebuilt:
        return "rebuilt"
    return "updated" if changed else "unchanged"
