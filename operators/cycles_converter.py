"""Convert the Cycles node tree of a material into a LuxCore material node tree.

The result is meant to render like the Cycles node reader
(export/cycles_node_reader.py) renders the same Cycles nodes, but as a native
LuxCore node tree: it is exported through the node cache, can use every LuxCore
feature (shapes, displacement, volumes) and can be edited with LuxCore nodes.
Every handler mirrors the matching branch of the reader (the "reader :<line>"
comments); when a mapping changes there, change it here too.

Known differences to the reader:
- Image textures with an encoded colour space (sRGB) are decoded with gamma 2.2
  (the Imagemap node has no OCIO setting for colour images); the reader uses
  the exact OCIO curve. Dark values differ by a few percent.
- An unsupported texture node leaves the LuxCore socket at its default value;
  the reader uses 0.
- LuxCore roughness sockets have a minimum of 0.001; the reader can send 0.
- Combine RGB/XYZ inputs are 0..1 on the LuxCore node; constants are clamped.
- The Bump node exports a bump sampling distance, which the reader never sets.
- Normal maps that are not Non-Color are decoded as data by the Imagemap
  normal map mode (correct); the reader decodes them as colour.
- Image Texture nodes keep their Closest interpolation and Box projection,
  which the reader ignores.

This module creates the nodes by their bl_idname and never imports the nodes
package. It does not use the context, so it also works in background mode.
"""

import math
from dataclasses import dataclass, field
from typing import NamedTuple

import bpy
from mathutils import Vector

from ..utils import node as utils_node
from ..utils import colorspace
from ..utils.luxutils import has_material_type
from ..export.cycles_node_reader import PRINCIPLED_OPENPBR_UNSUPPORTED, MISSING_IMAGE_COLOR, texcoord_output

PLACEHOLDER_LABEL = "Unsupported: "
PLACEHOLDER_COLOR = (0.55, 0.1, 0.1)
# Rec. 709 luminance, also used by Cycles (reader :678)
LUMA = (0.2126729, 0.7151522, 0.0721750)

HELPER_OFFSET_X = -220
HELPER_OFFSET_Y = -160

EXTENSION_MAP = {
    "REPEAT": "repeat",
    "EXTEND": "clamp",
    "CLIP": "black",
}

MATH_MODES = {
    "ADD": "add",
    "SUBTRACT": "subtract",
    "MULTIPLY": "scale",
    "DIVIDE": "divide",
    "GREATER_THAN": "greaterthan",
    "LESS_THAN": "lessthan",
    "POWER": "power",
    "ABSOLUTE": "abs",
    "ROUND": "rounding",
    "MODULO": "modulo",
    "SQRT": "sqrt",
}
# In Cycles these operations take the average of a colour input (reader :466)
MATH_TO_FLOAT = {"ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "ABSOLUTE"}
# Operations with one input
MATH_UNARY = {"ABSOLUTE", "ROUND", "SQRT"}

COLOR_MIX_MODES = {
    "MIX": "mix",
    "MULTIPLY": "scale",
    "ADD": "add",
    "SUBTRACT": "subtract",
}

SEPARATE_CHANNELS = {
    "ShaderNodeSeparateXYZ": (("X", "Y", "Z"), "Vector"),
    "ShaderNodeSeparateColor": (("Red", "Green", "Blue"), "Color"),
    "ShaderNodeSeparateRGB": (("R", "G", "B"), "Image"),
}
LUX_CHANNELS = ("R", "G", "B")


class Linked(NamedTuple):
    """A LuxCore node output that a converted Cycles output becomes"""
    node: bpy.types.Node
    output: str

    @property
    def socket(self):
        return self.node.outputs[self.node.outputs.find(self.output)]


@dataclass
class ConversionResult:
    nodes: int = 0
    placeholders: int = 0
    warnings: list = field(default_factory=list)

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)


def can_convert(material):
    return (material is not None
            and not material.library
            and not getattr(material, "is_grease_pencil", False)
            and utils_node.uses_nodes(material)
            and material.node_tree is not None)


def convert_material(material):
    """Build a new LuxCore material node tree from the Cycles nodes of the material.

    The tree is not assigned to the material. Unsupported nodes become
    labelled placeholders and warnings in the result, they never make the
    conversion fail. Returns (node_tree, ConversionResult).
    """
    if not can_convert(material):
        raise ValueError(f'Material "{material.name}" has no local Cycles node tree')

    node_tree = bpy.data.node_groups.new(name=material.name, type="luxcore_material_nodes")
    # Like init_mat_node_tree(): the material's PointerProperty is not always counted as a user
    node_tree.use_fake_user = True
    result = ConversionResult()
    try:
        _Converter(material, node_tree, result).run()
    except Exception:
        bpy.data.node_groups.remove(node_tree)
        raise
    result.nodes = len(node_tree.nodes)
    return node_tree, result


_NOT_CACHED = object()


def _is_linked(term):
    return isinstance(term, Linked)


def _as_float(value, to_float=True):
    """A constant as a float: the average of a colour (Cycles' implicit conversion
    in math nodes), or its luminance (what LuxCore takes from a colour in a float
    property)"""
    if isinstance(value, (list, tuple)):
        if to_float:
            return sum(value) / len(value)
        return sum(c * w for c, w in zip(value, LUMA))
    return float(value)


def _default(socket):
    """The unlinked value of a Cycles socket: a float or a list of 3 floats"""
    if not hasattr(socket, "default_value"):
        return None
    value = socket.default_value
    try:
        return [float(v) for v in value][:3]
    except TypeError:
        return float(value)


def _input(node, key):
    """A socket of a node by index or name (also finds disabled sockets)"""
    if isinstance(key, int):
        return node.inputs[key]
    index = node.inputs.find(key)
    if index == -1:
        raise KeyError(f'Node "{node.name}" has no input "{key}"')
    return node.inputs[index]


def _input_by_identifier(node, identifier):
    for socket in node.inputs:
        if socket.identifier == identifier:
            return socket
    return None


class _Converter:
    def __init__(self, material, tree, result):
        self.material = material
        self.tree = tree
        self.result = result
        # ShaderNodeGroup nodes the conversion is inside of, outermost first
        self.group_stack = []
        # (Cycles node pointer, output identifier, group key) -> term
        self.term_cache = {}
        # (Cycles node pointer, group key, role) -> LuxCore node
        self.node_cache = {}
        # (LuxCore node name, output) -> LuxCore Math node that converts it to a float
        self.float_cache = {}
        self.in_progress = set()
        self.helper_count = {}
        self.group_offsets = {}
        self.holdout_nodes = set()
        self.has_openpbr = has_material_type("openpbr")

    # --------------------------------------------------------------------------
    # Infrastructure
    # --------------------------------------------------------------------------

    def warn(self, message):
        self.result.warn(message)

    def group_key(self):
        return tuple(group.as_pointer() for group in self.group_stack)

    def abs_location(self, node):
        location = getattr(node, "location_absolute", None)
        if location is not None:
            location = Vector(location)
        else:
            location = Vector(node.location)
            parent = node.parent
            while parent:
                location += parent.location
                parent = parent.parent
        for group in self.group_stack:
            location += self.group_offset(group)
        return location

    def group_offset(self, group):
        """Places the nodes of a group around the group node"""
        key = group.as_pointer()
        if key not in self.group_offsets:
            group_location = Vector(group.location)
            output_location = Vector((0, 0))
            for node in group.node_tree.nodes:
                if node.bl_idname == "NodeGroupOutput" and node.is_active_output:
                    output_location = Vector(node.location)
                    break
            self.group_offsets[key] = group_location - output_location
        return self.group_offsets[key]

    def new_node(self, bl_idname, near=None, helper=False, label=None):
        node = self.tree.nodes.new(bl_idname)
        node.select = False
        if near is not None:
            location = self.abs_location(near)
            if helper:
                key = (near.as_pointer(), self.group_key())
                count = self.helper_count.get(key, 0) + 1
                self.helper_count[key] = count
                location += Vector((HELPER_OFFSET_X, HELPER_OFFSET_Y * count))
            node.location = location
        if label:
            node.label = label
        return node

    def shared_node(self, cycles_node, bl_idname, role=""):
        """The LuxCore node of a Cycles node with several outputs, created once.
        Returns (node, is_new)."""
        key = (cycles_node.as_pointer(), self.group_key(), role)
        node = self.node_cache.get(key)
        if node is not None:
            return node, False
        node = self.new_node(bl_idname, cycles_node)
        self.node_cache[key] = node
        return node, True

    def resolve(self, socket):
        """The term of a Cycles input socket: what is linked to it, or its value"""
        link = utils_node.get_link(socket)
        if link:
            return self.output(link.from_node, link.from_socket)
        return _default(socket)

    def output(self, node, out_socket):
        """The term of a Cycles node output"""
        if node.bl_idname == "ShaderNodeGroup":
            return self.group_output(node, out_socket)
        if node.bl_idname == "NodeGroupInput":
            return self.group_input(out_socket)

        key = (node.as_pointer(), out_socket.identifier, self.group_key())
        term = self.term_cache.get(key, _NOT_CACHED)
        if term is not _NOT_CACHED:
            return term
        if key in self.in_progress:
            self.warn(f'Node "{node.name}" is part of a loop, it is skipped')
            return None

        self.in_progress.add(key)
        try:
            handler = HANDLERS.get(node.bl_idname)
            if node.bl_idname == "ShaderNodeMix" and node.data_type != "RGBA":
                handler = None
            if handler is None:
                term = self.unsupported(node, out_socket)
            else:
                term = handler(self, node, out_socket)
        finally:
            self.in_progress.discard(key)
        self.term_cache[key] = term
        return term

    def group_output(self, group_node, out_socket):
        # reader :511
        if group_node.node_tree is None:
            self.warn(f'Group node "{group_node.name}" has no node group')
            return None
        active_output = None
        for node in group_node.node_tree.nodes:
            if node.bl_idname == "NodeGroupOutput" and node.is_active_output:
                active_output = node
                break
        if active_output is None:
            return None
        index = active_output.inputs.find(out_socket.name)
        if index == -1:
            return None
        self.group_stack.append(group_node)
        try:
            return self.resolve(active_output.inputs[index])
        finally:
            self.group_stack.pop()

    def group_input(self, out_socket):
        # reader :534
        if not self.group_stack:
            return None
        group_node = self.group_stack.pop()
        try:
            index = group_node.inputs.find(out_socket.name)
            if index == -1:
                return None
            return self.resolve(group_node.inputs[index])
        finally:
            self.group_stack.append(group_node)

    def connect(self, cycles_socket, lux_node, key, to_float=False):
        """Give a LuxCore input the value or link of a Cycles input"""
        term = self.resolve(cycles_socket)
        self.apply(term, _input(lux_node, key), to_float)
        return term

    def apply(self, term, lux_input, to_float=False):
        if term is None:
            return
        if _is_linked(term):
            source = term.socket
            if to_float and source.bl_idname in {"LuxCoreSocketColor", "LuxCoreSocketVector"}:
                source = self.to_float(term)
            self.tree.links.new(source, lux_input)
            return
        if not hasattr(lux_input, "default_value"):
            return
        self.set_value(lux_input, term, to_float)

    def set_value(self, lux_input, value, to_float=False):
        current = lux_input.default_value
        if hasattr(current, "__len__"):
            size = len(current)
            if isinstance(value, (list, tuple)):
                value = list(value)[:size]
            else:
                value = [float(value)] * size
        else:
            value = _as_float(value, to_float)

        prop = lux_input.bl_rna.properties["default_value"]
        low, high = prop.hard_min, prop.hard_max
        values = value if isinstance(value, list) else [value]
        clamped = [min(max(v, low), high) for v in values]
        if any(abs(a - b) > 1e-3 for a, b in zip(values, clamped)):
            self.warn(f'"{lux_input.name}" of node "{lux_input.node.name}": the value '
                      f'{[round(v, 4) for v in values]} is outside {low:g}..{high:g} and was clamped')
        lux_input.default_value = clamped if isinstance(value, list) else clamped[0]

    def to_float(self, term):
        """A Math node that turns a colour texture into its float value (reader :1066)"""
        key = (term.node.name, term.output)
        node = self.float_cache.get(key)
        if node is None:
            node = self.new_node("LuxCoreNodeTexMath", label="To float")
            node.location = term.node.location + Vector((180, -60))
            node.mode = "power"
            self.tree.links.new(term.socket, node.inputs[0])
            node.inputs[1].default_value = 1
            self.float_cache[key] = node
        return node.outputs["Value"]

    def material_input(self, cycles_socket, lux_node, key):
        """Link the material a Cycles shader socket becomes; a black matte when there is none"""
        term = self.resolve(cycles_socket)
        lux_input = _input(lux_node, key)
        if _is_linked(term) and term.socket.bl_idname == "LuxCoreSocketMaterial":
            self.tree.links.new(term.socket, lux_input)
            return term
        black = self.black_matte(cycles_socket.node, "Black")
        self.tree.links.new(black.outputs["Material"], lux_input)
        return Linked(black, "Material")

    def black_matte(self, near, label=None):
        # reader black() :38
        node = self.new_node("LuxCoreNodeMatMatte", near, helper=True, label=label)
        node.inputs["Diffuse Color"].default_value = (0, 0, 0)
        return node

    def math_node(self, mode, near, label=None):
        node = self.new_node("LuxCoreNodeTexMath", near, helper=True, label=label)
        node.mode = mode
        return node

    def scaled(self, cycles_socket, factor, near, label):
        """The term of a Cycles input times a factor (reader :968)"""
        term = self.resolve(cycles_socket)
        if term is None:
            return None
        if not _is_linked(term):
            return _as_float(term) * factor
        node = self.math_node("scale", near, label)
        self.apply(term, node.inputs[0])
        node.inputs[1].default_value = factor
        return Linked(node, "Value")

    def squared_roughness(self, cycles_socket, near):
        """Cycles roughness to LuxCore's linear roughness (reader :1045)"""
        term = self.resolve(cycles_socket)
        if _is_linked(term):
            node = self.math_node("power", near, "Roughness²")
            self.apply(term, node.inputs[0])
            node.inputs[1].default_value = 2
            return Linked(node, "Value")
        if term is None:
            return 0.0
        return _as_float(term) ** 2

    def unsupported(self, node, out_socket):
        """Pass through like a muted node if possible (reader :848), else a placeholder"""
        self.warn(f'Unsupported node "{node.name}" ({node.bl_idname})')
        for internal_link in node.internal_links:
            if internal_link.to_socket == out_socket and utils_node.get_link(internal_link.from_socket):
                return self.resolve(internal_link.from_socket)

        self.result.placeholders += 1
        if out_socket.type == "SHADER":
            placeholder = self.black_matte(node)
        else:
            # Shows where the unsupported texture was; the consumer keeps its own value
            placeholder = self.new_node("LuxCoreNodeTexConstfloat3", node)
            placeholder.value = MISSING_IMAGE_COLOR
        placeholder.label = PLACEHOLDER_LABEL + node.name
        placeholder.use_custom_color = True
        placeholder.color = PLACEHOLDER_COLOR
        if out_socket.type == "SHADER":
            return Linked(placeholder, "Material")
        return None

    # --------------------------------------------------------------------------
    # Entry
    # --------------------------------------------------------------------------

    def run(self):
        cycles_output = self.material.node_tree.get_output_node("CYCLES")
        output = self.new_node("LuxCoreNodeMatOutput", cycles_output)
        if cycles_output is None:
            self.warn("The Cycles node tree has no Material Output node")
            black = self.black_matte(None, "Black")
            black.location = output.location + Vector((-250, 0))
            self.tree.links.new(black.outputs["Material"], output.inputs["Material"])
            return

        if cycles_output.inputs["Volume"].is_linked:
            self.warn("The Volume output is not converted: add a LuxCore volume to the Interior Volume "
                      "of the output node")
        if cycles_output.inputs["Displacement"].is_linked:
            self.warn("The Displacement output is not converted: use a LuxCore Height Displacement "
                      "node on the Shape input of the output node")

        root = self.material_input(cycles_output.inputs["Surface"], output, "Material")
        if root.node.name in self.holdout_nodes:
            # reader :390
            output.is_holdout = True
        elif self.holdout_nodes:
            self.warn("A Holdout node that is not directly connected to the output renders black")

    # --------------------------------------------------------------------------
    # Shaders
    # --------------------------------------------------------------------------

    def common_inputs(self, node, lux, normal=True):
        """Normal to Bump (only when linked, reader :209)"""
        if normal and "Normal" in node.inputs and node.inputs["Normal"].is_linked:
            self.connect(node.inputs["Normal"], lux, "Bump")

    def emission_node(self, color_socket, strength_socket, near):
        """A LuxCore Emission node: Color times Strength (reader :543)"""
        emission = self.new_node("LuxCoreNodeMatEmission", near, helper=True)
        emission.emission_unit = "artistic"
        emission.exposure = 0
        strength = self.resolve(strength_socket)
        if _is_linked(strength):
            emission.gain = 1
            scale = self.new_node("LuxCoreNodeTexColorMix", near, helper=True, label="× Strength")
            scale.mode = "scale"
            self.apply(strength, scale.inputs[1])
            self.connect(color_socket, scale, 2)
            self.tree.links.new(scale.outputs["Color"], emission.inputs["Color"])
        else:
            emission.gain = 0 if strength is None else _as_float(strength)
            self.connect(color_socket, emission, "Color")
        return emission

    def principled(self, node, out_socket):
        # reader :70
        if self.has_openpbr:
            lux = self.principled_openpbr(node)
        else:
            lux = self.principled_disney(node)

        # reader :179-210: on the right-most material node
        color_name = "Emission Color" if "Emission Color" in node.inputs else "Emission"
        color_socket = node.inputs[color_name]
        strength_socket = node.inputs["Emission Strength"]
        color = _default(color_socket)
        if (color_socket.is_linked or strength_socket.is_linked
                or (any(c > 0 for c in color) and strength_socket.default_value > 0)):
            emission = self.emission_node(color_socket, strength_socket, node)
            self.tree.links.new(emission.outputs["Emission"], _input(lux, "Emission"))
        self.connect(node.inputs["Alpha"], lux, "Opacity")
        self.common_inputs(node, lux)
        return Linked(lux, "Material")

    def used(self, socket):
        term = self.resolve(socket)
        return _is_linked(term) or (term is not None and _as_float(term) > 0)

    def principled_openpbr(self, node):
        # reader :942
        lux = self.new_node("LuxCoreNodeMatOpenPBR", node)
        inputs = node.inputs

        def connect(cycles_name, lux_name):
            self.connect(inputs[cycles_name], lux, lux_name)

        connect("Base Color", "Base Color")
        connect("Metallic", "Metalness")
        if "Diffuse Roughness" in inputs:
            connect("Diffuse Roughness", "Diffuse Roughness")
        connect("Roughness", "Specular Roughness")
        connect("IOR", "Specular IOR")
        # Specular IOR Level 0.5 keeps the F0 of the IOR (reader :987)
        self.apply(self.scaled(inputs["Specular IOR Level"], 2, node, "Specular ×2"),
                   _input(lux, "Specular Weight"))
        connect("Specular Tint", "Specular Color")
        connect("Anisotropic", "Specular Anisotropy")
        connect("Anisotropic Rotation", "Tangent Rotation")

        # Switch a group on before setting its inputs: switching on sets a weight of 0 to 1
        if self.used(inputs["Transmission Weight"]):
            lux.use_transmission = True
            connect("Transmission Weight", "Transmission Weight")
            connect("Base Color", "Transmission Color")

        if self.used(inputs["Subsurface Weight"]):
            lux.use_subsurface = True
            connect("Subsurface Weight", "Subsurface Weight")
            connect("Base Color", "Subsurface Color")
            connect("Subsurface Scale", "Subsurface Radius")
            connect("Subsurface Radius", "Radius Scale")
            connect("Subsurface Anisotropy", "Subsurface Anisotropy")

        if self.used(inputs["Coat Weight"]):
            lux.use_coat = True
            connect("Coat Weight", "Coat Weight")
            connect("Coat Tint", "Coat Color")
            connect("Coat Roughness", "Coat Roughness")
            connect("Coat IOR", "Coat IOR")

        if self.used(inputs["Sheen Weight"]):
            lux.use_fuzz = True
            connect("Sheen Weight", "Fuzz Weight")
            connect("Sheen Tint", "Fuzz Color")
            connect("Sheen Roughness", "Fuzz Roughness")

        if "Thin Film Thickness" in inputs and self.used(inputs["Thin Film Thickness"]):
            lux.use_thin_film = True
            _input(lux, "Thin Film Weight").default_value = 1
            # Both in nanometres; the node converts to micrometres
            connect("Thin Film Thickness", "Thin Film Thickness (nm)")
            connect("Thin Film IOR", "Thin Film IOR")

        if "Thin Wall" in inputs:
            # Blender 5.2
            if inputs["Thin Wall"].is_linked:
                self.warn('Principled BSDF: a linked "Thin Wall" is not supported, its value is used')
            lux.thin_walled = bool(inputs["Thin Wall"].default_value)

        for name in PRINCIPLED_OPENPBR_UNSUPPORTED:
            if name in inputs and inputs[name].is_linked:
                self.warn(f'Principled BSDF: "{name}" is not supported yet and is ignored')
        return lux

    def principled_disney(self, node):
        """pyluxcore before 3.0: Disney, glass or a mix of both (reader :75-178)"""
        inputs = node.inputs
        metallic = self.resolve(inputs["Metallic"])
        transmission = self.resolve(inputs["Transmission Weight"])

        def glass():
            lux = self.new_node("LuxCoreNodeMatGlass", node, helper=_is_linked(metallic) or metallic != 0)
            self.connect(inputs["Base Color"], lux, "Transmission Color")
            _input(lux, "Reflection Color").default_value = (1, 1, 1)
            self.connect(inputs["IOR"], lux, "IOR")
            self.glass_roughness(lux, inputs["Roughness"], node)
            return lux

        if transmission == 1 and metallic == 0:
            # Effectively glass; no mix for performance reasons
            return glass()

        disney = self.new_node("LuxCoreNodeMatDisney", node)
        self.connect(inputs["Base Color"], disney, "Base Color")
        self.connect(inputs["Subsurface Weight"], disney, "Subsurface")
        self.apply(metallic, _input(disney, "Metallic"))
        self.connect(inputs["Specular IOR Level"], disney, "Specular")
        self.connect(inputs["Specular Tint"], disney, "Specular Tint")
        # Both use squared roughness here (reader :112)
        self.connect(inputs["Roughness"], disney, "Roughness")
        self.connect(inputs["Anisotropic"], disney, "Anisotropic")
        self.connect(inputs["Sheen Weight"], disney, "Sheen")
        self.connect(inputs["Sheen Tint"], disney, "Sheen Tint")
        self.connect(inputs["Coat Weight"], disney, "Clearcoat")

        transmission_socket = inputs["Transmission Weight"]
        if metallic == 1 or not (transmission_socket.is_linked or transmission_socket.default_value > 0):
            return disney

        mix = self.new_node("LuxCoreNodeMatMix", node, helper=True, label="Disney / Glass")
        self.tree.links.new(disney.outputs["Material"], mix.inputs["Material 1"])
        self.tree.links.new(glass().outputs["Material"], mix.inputs["Material 2"])
        # transmission * (1 - metallic) (reader :143-169)
        if _is_linked(metallic) or _is_linked(transmission):
            if _is_linked(metallic):
                inverted = self.math_node("subtract", node, "1 - Metallic")
                inverted.inputs[0].default_value = 1
                self.apply(metallic, inverted.inputs[1])
                inverted_term = Linked(inverted, "Value")
            else:
                inverted_term = 1 - _as_float(metallic)
            amount = self.math_node("scale", node, "Glass Amount")
            self.apply(inverted_term, amount.inputs[0])
            self.apply(transmission, amount.inputs[1])
            self.tree.links.new(amount.outputs["Value"], mix.inputs["Mix Factor"])
        else:
            self.set_value(mix.inputs["Mix Factor"], _as_float(transmission) * (1 - _as_float(metallic)))
        return mix

    def glass_roughness(self, lux, roughness_socket, near):
        roughness = self.squared_roughness(roughness_socket, near)
        if _is_linked(roughness) or roughness != 0:
            lux.rough = True
            self.apply(roughness, _input(lux, "Roughness"))
        else:
            lux.rough = False

    def mix_shader(self, node, out_socket):
        # reader :211
        lux = self.new_node("LuxCoreNodeMatMix", node)
        self.material_input(node.inputs[1], lux, "Material 1")
        self.material_input(node.inputs[2], lux, "Material 2")
        fac = self.resolve(node.inputs["Fac"])
        if fac is None and node.inputs["Fac"].is_linked:
            fac = 0.5
        self.apply(fac, lux.inputs["Mix Factor"])
        return Linked(lux, "Material")

    def diffuse(self, node, out_socket):
        # reader :232 (Roughness is not converted, as in the reader)
        lux = self.new_node("LuxCoreNodeMatMatte", node)
        self.connect(node.inputs["Color"], lux, "Diffuse Color")
        self.common_inputs(node, lux)
        return Linked(lux, "Material")

    def sheen(self, node, out_socket):
        # reader :240
        lux = self.new_node("LuxCoreNodeMatVelvet", node)
        self.connect(node.inputs["Color"], lux, "Diffuse Color")
        self.connect(node.inputs["Roughness"], lux, "Roughness")
        self.common_inputs(node, lux)
        return Linked(lux, "Material")

    def subsurface(self, node, out_socket):
        # reader :250
        lux = self.new_node("LuxCoreNodeMatSubsurface", node)
        skin = getattr(node, "falloff", "") == "RANDOM_WALK_SKIN"
        self.connect(node.inputs["Color"], lux, "Color")
        # The node exports Scatter Color as the radius and Radius as the scale
        self.connect(node.inputs["Radius"], lux, "Scatter Color")
        self.connect(node.inputs["Scale"], lux, "Radius")
        self.connect(node.inputs["Anisotropy"], lux, "Anisotropy")
        if skin:
            self.connect(node.inputs["IOR"], lux, "IOR")
            self.connect(node.inputs["Roughness"], lux, "Roughness")
        else:
            _input(lux, "IOR").default_value = 1
            _input(lux, "Roughness").default_value = 0.3
        self.common_inputs(node, lux)
        return Linked(lux, "Material")

    def glossy(self, node, out_socket):
        # reader :267 (Blender 4.0 merged Glossy and Anisotropic)
        lux = self.new_node("LuxCoreNodeMatMetal", node)
        lux.input_type = "color"
        self.connect(node.inputs["Color"], lux, "Color")
        self.apply(self.squared_roughness(node.inputs["Roughness"], node), _input(lux, "Roughness"))
        for name in ("Anisotropy", "Rotation", "Tangent"):
            if name in node.inputs:
                socket = node.inputs[name]
                value = _default(socket)
                if socket.is_linked or (name == "Anisotropy" and value):
                    self.warn(f'Glossy BSDF "{node.name}": "{name}" is not supported and is ignored')
        self.common_inputs(node, lux)
        return Linked(lux, "Material")

    def glass(self, node, out_socket):
        # reader :316, :333
        lux = self.new_node("LuxCoreNodeMatGlass", node)
        self.connect(node.inputs["Color"], lux, "Transmission Color")
        if node.bl_idname == "ShaderNodeBsdfRefraction":
            _input(lux, "Reflection Color").default_value = (0, 0, 0)
        else:
            self.connect(node.inputs["Color"], lux, "Reflection Color")
        self.connect(node.inputs["IOR"], lux, "IOR")
        self.glass_roughness(lux, node.inputs["Roughness"], node)
        self.common_inputs(node, lux)
        return Linked(lux, "Material")

    def translucent(self, node, out_socket):
        # reader :373
        lux = self.new_node("LuxCoreNodeMatMatteTranslucent", node)
        _input(lux, "Transmission Color").default_value = (1, 1, 1)
        self.connect(node.inputs["Color"], lux, "Reflection Color")
        self.common_inputs(node, lux)
        return Linked(lux, "Material")

    def transparent(self, node, out_socket):
        # reader :382
        lux = self.new_node("LuxCoreNodeMatNull", node)
        self.connect(node.inputs["Color"], lux, "Transmission Color")
        return Linked(lux, "Material")

    def holdout(self, node, out_socket):
        # reader :390
        lux = self.black_matte(None, "Holdout")
        lux.location = self.abs_location(node)
        self.holdout_nodes.add(lux.name)
        return Linked(lux, "Material")

    def emission(self, node, out_socket):
        # reader :536
        lux = self.new_node("LuxCoreNodeMatMatte", node)
        lux.inputs["Diffuse Color"].default_value = (0, 0, 0)
        emission = self.emission_node(node.inputs["Color"], node.inputs["Strength"], node)
        self.tree.links.new(emission.outputs["Emission"], _input(lux, "Emission"))
        return Linked(lux, "Material")

    # --------------------------------------------------------------------------
    # Textures
    # --------------------------------------------------------------------------

    def image_node(self, image_node, key_node, role):
        """An Imagemap node for a Cycles Image Texture node, with its mapping.
        Returns None (and warns) when there is no image."""
        image = image_node.image
        if image is None:
            self.warn(f'Image Texture "{image_node.name}" has no image')
            return None
        lux, is_new = self.shared_node(key_node, "LuxCoreNodeTexImagemap", role)
        if not is_new:
            return lux
        if key_node is not image_node:
            lux.location = self.abs_location(image_node) + Vector((0, HELPER_OFFSET_Y))

        lux.image = image
        if image.source in {"MOVIE", "TILED"}:
            self.warn(f'Image "{image.name}": the source {image.source} is not supported by LuxCore')
        extension = image_node.extension
        if extension not in EXTENSION_MAP:
            self.warn(f'Image Texture "{image_node.name}": extension {extension} is not supported, '
                      'using Repeat')
        lux.wrap = EXTENSION_MAP.get(extension, "repeat")
        if image_node.interpolation == "Closest":
            lux.filter = "nearest"
        if image_node.projection == "BOX":
            lux.projection = "box"

        mapping = self.mapping_node(image_node.inputs["Vector"])
        if mapping is not None:
            self.tree.links.new(mapping.outputs["2D Mapping"], lux.inputs["2D Mapping"])
        return lux

    def image_gamma(self, image):
        """The Imagemap gamma that decodes an image like the reader does"""
        settings = image.colorspace_settings
        if settings.is_data or colorspace.is_linear_scene_space(settings.name):
            # Gamma 1: no conversion for data, OCIO for linear colour images
            return 1.0
        if settings.name not in {"sRGB", "sRGB EOTF"}:
            self.warn(f'Image "{image.name}": the colour space {settings.name} is approximated '
                      'with gamma 2.2')
        return 2.2

    def mapping_node(self, vector_socket):
        """A 2D Mapping node for what feeds an Image Texture's Vector input (reader :880),
        None when the default UV mapping does"""
        scale = [1, 1]
        rotation = 0
        location = [0, 0]
        name = self.material.name

        node = utils_node.get_linked_node(vector_socket)
        mapping = None
        if node and node.bl_idname == "ShaderNodeMapping":
            mapping = node
            if node.vector_type not in {"POINT", "VECTOR"}:
                self.warn(f"In material {name}: unsupported Mapping type: {node.vector_type}, using the UVs")
                mapping = None
            else:
                inputs = node.inputs
                if any(utils_node.get_link(inputs[n]) for n in ("Location", "Rotation", "Scale") if n in inputs):
                    self.warn(f"In material {name}: linked Mapping inputs are not supported, using their values")
                rot = inputs["Rotation"].default_value
                if abs(rot[0]) > 1e-6 or abs(rot[1]) > 1e-6:
                    self.warn(f"In material {name}: Mapping rotation around X or Y is not supported on UVs")
                scale = list(inputs["Scale"].default_value)[:2]
                rotation = rot[2]
                if node.vector_type == "POINT":
                    location = list(inputs["Location"].default_value)[:2]
            node = utils_node.get_linked_node(node.inputs["Vector"])

        if node and node.bl_idname not in {"ShaderNodeTexCoord", "ShaderNodeUVMap"}:
            self.warn(f"In material {name}: unsupported texture coordinates from {node.name}, using the UVs")
        elif node and node.bl_idname == "ShaderNodeTexCoord" and texcoord_output(vector_socket) != "UV":
            self.warn(f"In material {name}: unsupported texture coordinates "
                      f"{texcoord_output(vector_socket)}, using the UVs")

        if mapping is None:
            return None
        lux, is_new = self.shared_node(mapping, "LuxCoreNodeTexMapping2D")
        if is_new:
            # The node flips V itself: uvscale (u, -v), uvdelta (u, v + 1), see
            # LuxCoreNodeTexMapping2D.export_uvmapping2d(); the reader sends
            # uvscale (sx, -sy), uvdelta (lx, 1 - ly)
            lux.use_uniform_scale = False
            lux.uscale = scale[0]
            lux.vscale = scale[1]
            lux.rotation = math.remainder(rotation, 2 * math.pi)
            lux.udelta = location[0]
            lux.vdelta = -location[1]
        return lux

    def image(self, node, out_socket):
        # reader :289
        lux = self.image_node(node, node, "")
        if lux is None:
            return list(MISSING_IMAGE_COLOR)
        lux.channel = "rgb"
        lux.gamma = self.image_gamma(node.image)
        # The node exports the alpha channel for its Alpha output
        return Linked(lux, "Alpha" if out_socket.name == "Alpha" else "Color")

    def gamma(self, node, out_socket):
        # reader :689
        link = utils_node.get_link(node.inputs["Color"])
        gamma_socket = node.inputs["Gamma"]
        if link and link.from_node.bl_idname == "ShaderNodeTexImage" and link.from_node.image:
            if gamma_socket.is_linked:
                self.warn(f'Gamma node "{node.name}": a linked Gamma is not supported, its value is used')
            lux = self.image_node(link.from_node, node, "gamma")
            lux.gamma = gamma_socket.default_value or 1
            return Linked(lux, "Color")

        color = self.resolve(node.inputs["Color"])
        gamma = self.resolve(gamma_socket)
        if _is_linked(color) or _is_linked(gamma):
            self.warn(f'Gamma node "{node.name}" is only supported with a constant or an image texture '
                      'input, it is skipped')
            return color if _is_linked(color) else None
        if color is None or gamma is None:
            return None
        gamma = _as_float(gamma)
        if isinstance(color, list):
            return [max(c, 0) ** gamma for c in color]
        return max(color, 0) ** gamma

    def normal_map(self, node, out_socket):
        # reader :736
        if node.space != "TANGENT":
            self.warn(f"Unsupported normal map space: {node.space}")
            return None
        strength_socket = node.inputs["Strength"]
        link = utils_node.get_link(node.inputs["Color"])
        if (link and link.from_node.bl_idname == "ShaderNodeTexImage" and link.from_node.image
                and link.from_socket.name == "Color" and not strength_socket.is_linked):
            image = link.from_node.image
            lux = self.image_node(link.from_node, node, "normal")
            # Before any link: this swaps the outputs
            lux.is_normal_map = True
            lux.normal_map_orientation = "opengl"
            lux.normal_map_scale = max(strength_socket.default_value, 0)
            if not image.colorspace_settings.is_data:
                self.warn(f'Normal map image "{image.name}" is not Non-Color; LuxCore reads it as data')
            return Linked(lux, "Bump")

        lux = self.new_node("LuxCoreNodeTexNormalmap", node)
        self.connect(node.inputs["Color"], lux, "Color")
        strength = self.resolve(strength_socket)
        if _is_linked(strength):
            # The normalmap scale can't be textured (reader :749)
            lux.scale = 1
            scale = self.math_node("scale", node, "× Strength")
            self.tree.links.new(lux.outputs["Bump"], scale.inputs[0])
            self.apply(strength, scale.inputs[1])
            return Linked(scale, "Value")
        lux.scale = max(_as_float(strength), 0) if strength is not None else 1
        return Linked(lux, "Bump")

    def bump(self, node, out_socket):
        # reader :761
        if node.inputs["Distance"].is_linked:
            self.warn("Bump node Distance socket is not supported")
        if node.inputs["Normal"].is_linked:
            self.warn("Bump node Normal socket is not supported")
        lux = self.new_node("LuxCoreNodeTexBump", node)
        self.connect(node.inputs["Height"], lux, "Value")
        strength = self.resolve(node.inputs["Strength"])
        if node.invert and not _is_linked(strength):
            strength = -_as_float(strength)
        self.apply(strength, lux.inputs["Bump Height"])
        if node.invert and _is_linked(strength):
            invert = self.math_node("scale", node, "Invert")
            self.tree.links.new(lux.outputs["Bump"], invert.inputs[0])
            invert.inputs[1].default_value = -1
            return Linked(invert, "Value")
        return Linked(lux, "Bump")

    def mix_rgb(self, node, out_socket):
        # reader :397
        if node.bl_idname == "ShaderNodeMixRGB":
            fac_socket = node.inputs["Fac"]
            a_socket = node.inputs["Color1"]
            b_socket = node.inputs["Color2"]
            clamp = node.use_clamp
        else:
            # The Mix node has a socket per data type with the same name
            fac_socket = _input_by_identifier(node, "Factor_Float")
            a_socket = _input_by_identifier(node, "A_Color")
            b_socket = _input_by_identifier(node, "B_Color")
            clamp = node.clamp_result

        fac = self.resolve(fac_socket)
        if fac is None and fac_socket.is_linked:
            fac = 0.5
        a = self.resolve(a_socket)
        b = self.resolve(b_socket)
        if fac == 0:
            return a

        blend_type = node.blend_type
        if blend_type == "MIX":
            if fac == 1:
                return b
            lux = self.new_node("LuxCoreNodeTexColorMix", node)
            lux.mode = "mix"
            self.apply(fac, lux.inputs[0])
            self.apply(a, lux.inputs[1])
            self.apply(b, lux.inputs[2])
        elif blend_type in COLOR_MIX_MODES:
            lux = self.new_node("LuxCoreNodeTexColorMix", node)
            lux.mode = COLOR_MIX_MODES[blend_type]
            self.apply(a, lux.inputs[1])
            self.apply(b, lux.inputs[2])
        elif blend_type == "DIVIDE":
            # No divide mode on the Color Math node; the Math node's divide
            # texture also divides colours
            lux = self.new_node("LuxCoreNodeTexMath", node)
            lux.mode = "divide"
            for term, index in ((a, 0), (b, 1)):
                if term is not None and not _is_linked(term) and isinstance(term, list):
                    constant = self.new_node("LuxCoreNodeTexConstfloat3", node, helper=True)
                    constant.value = term
                    term = Linked(constant, "Color")
                self.apply(term, lux.inputs[index])
        else:
            self.warn(f"Unsupported MixRGB mode: {blend_type}")
            return None

        output = "Value" if lux.bl_idname == "LuxCoreNodeTexMath" else "Color"
        last = lux
        if blend_type != "MIX" and (_is_linked(fac) or 0 < fac < 1):
            # Mix the result with the first colour by the factor (reader :442)
            mix = self.new_node("LuxCoreNodeTexColorMix", node, helper=True, label="Factor")
            mix.mode = "mix"
            self.apply(fac, mix.inputs[0])
            self.apply(a, mix.inputs[1])
            self.tree.links.new(lux.outputs[output], mix.inputs[2])
            last, output = mix, "Color"
        if clamp:
            last.clamp_output = True
        return Linked(last, output)

    def math(self, node, out_socket):
        # reader :452
        operation = node.operation
        mode = MATH_MODES.get(operation)
        if mode is None:
            self.warn(f"Unsupported Math mode: {operation}")
            return None
        lux = self.new_node("LuxCoreNodeTexMath", node)
        # Before the inputs: the mode renames them
        lux.mode = mode
        to_float = operation in MATH_TO_FLOAT
        self.connect(node.inputs[0], lux, 0, to_float)
        if operation == "ROUND":
            lux.inputs[1].default_value = 1
        elif operation not in MATH_UNARY:
            self.connect(node.inputs[1], lux, 1, to_float)
        if node.use_clamp:
            lux.clamp_output = True
        return Linked(lux, "Value")

    def hue_saturation(self, node, out_socket):
        # reader :495
        fac = node.inputs["Fac"]
        if fac.is_linked or abs(fac.default_value - 1) > 1e-6:
            self.warn(f'Hue/Saturation/Value node "{node.name}": Fac is not supported and is ignored')
        lux = self.new_node("LuxCoreNodeTexHSV", node)
        for name in ("Color", "Hue", "Saturation", "Value"):
            self.connect(node.inputs[name], lux, name)
        return Linked(lux, "Color")

    def value(self, node, out_socket):
        # reader :560. One knob stays one knob when the value is used several times.
        value = float(node.outputs[0].default_value)
        if len(node.outputs[0].links) > 1:
            lux, is_new = self.shared_node(node, "LuxCoreNodeTexConstfloat1")
            lux.value = value
            return Linked(lux, "Value")
        return value

    def rgb(self, node, out_socket):
        # reader :567
        color = list(node.outputs[0].default_value)[:3]
        if len(node.outputs[0].links) > 1:
            lux, is_new = self.shared_node(node, "LuxCoreNodeTexConstfloat3")
            lux.value = color
            return Linked(lux, "Color")
        return color

    def color_ramp(self, node, out_socket):
        # reader :574 (both outputs give the colour, as in the reader)
        ramp = node.color_ramp
        if out_socket.name != "Color":
            self.warn(f'Color Ramp "{node.name}": the Alpha output is not supported, the colour is used')
        lux, is_new = self.shared_node(node, "LuxCoreNodeTexBand")
        if not is_new:
            return Linked(lux, "Color")
        if ramp.interpolation == "CONSTANT":
            lux.interpolation = "none"
        elif ramp.interpolation == "LINEAR":
            lux.interpolation = "linear"
        else:
            if ramp.interpolation != "B_SPLINE":
                self.warn(f'Color Ramp "{node.name}": {ramp.interpolation} interpolation is '
                          'approximated with cubic')
            lux.interpolation = "cubic"
        if ramp.color_mode != "RGB":
            self.warn(f'Color Ramp "{node.name}": the {ramp.color_mode} colour mode is not supported')

        elements = list(ramp.elements)
        items = lux.ramp_items
        # The node keeps at least two items
        while len(items) > max(len(elements), 2):
            items.remove(len(items) - 1)
        for i, element in enumerate(elements):
            item = items[i] if i < len(items) else items.add()
            item.offset = element.position
            item.value = list(element.color)[:3]
            item.index = i
            item.node_name = lux.name
        if len(elements) == 1 and len(items) > 1:
            items[1].offset = elements[0].position
            items[1].value = list(elements[0].color)[:3]
        self.connect(node.inputs["Fac"], lux, "Amount")
        return Linked(lux, "Color")

    def checker(self, node, out_socket):
        # reader :597: object coordinates only, untextured scale
        if node.inputs["Scale"].is_linked or node.inputs["Vector"].is_linked:
            self.warn(f'Checker Texture "{node.name}": only object coordinates and a constant scale '
                      'are supported')
        lux, is_new = self.shared_node(node, "LuxCoreNodeTexCheckerboard3D")
        if not is_new:
            return Linked(lux, "Color")
        self.connect(node.inputs["Color2"], lux, "Color 1")
        self.connect(node.inputs["Color1"], lux, "Color 2")
        mapping = self.new_node("LuxCoreNodeTexMapping3D", node, helper=True)
        mapping.mapping_type = "localmapping3d"
        mapping.use_uniform_scale = True
        mapping.uniform_scale = node.inputs["Scale"].default_value
        self.tree.links.new(mapping.outputs["3D Mapping"], lux.inputs["3D Mapping"])
        return Linked(lux, "Color")

    def invert(self, node, out_socket):
        # reader :612
        fac_socket = node.inputs["Fac"]
        fac = self.resolve(fac_socket)
        if fac is None and fac_socket.is_linked:
            fac = 1
        color = self.resolve(node.inputs["Color"])
        if fac == 0:
            return color
        lux = self.new_node("LuxCoreNodeTexInvert", node)
        self.apply(color, lux.inputs["Color"])
        lux.inputs["Maximum"].default_value = 1
        if _is_linked(fac) or 0 < fac < 1:
            mix = self.new_node("LuxCoreNodeTexColorMix", node, helper=True, label="Factor")
            mix.mode = "mix"
            self.apply(fac, mix.inputs[0])
            self.apply(color, mix.inputs[1])
            self.tree.links.new(lux.outputs["Color"], mix.inputs[2])
            return Linked(mix, "Color")
        return Linked(lux, "Color")

    def separate(self, node, out_socket):
        # reader :641
        channels, input_name = SEPARATE_CHANNELS[node.bl_idname]
        if node.bl_idname == "ShaderNodeSeparateColor" and node.mode != "RGB":
            self.warn(f'Separate Color "{node.name}": the {node.mode} mode is not supported, using RGB')
        lux, is_new = self.shared_node(node, "LuxCoreNodeTexSplitFloat3")
        if is_new:
            self.connect(node.inputs[input_name], lux, "Color")
        if out_socket.name not in channels:
            return None
        return Linked(lux, LUX_CHANNELS[channels.index(out_socket.name)])

    def combine(self, node, out_socket):
        # reader :660
        if node.bl_idname == "ShaderNodeCombineColor" and node.mode != "RGB":
            self.warn(f'Combine Color "{node.name}": the {node.mode} mode is not supported, using RGB')
        lux = self.new_node("LuxCoreNodeTexMakeFloat3", node)
        for index, name in enumerate(LUX_CHANNELS):
            self.connect(node.inputs[index], lux, name)
        return Linked(lux, "Color")

    def rgb_to_bw(self, node, out_socket):
        # reader :670
        lux = self.new_node("LuxCoreNodeTexDotProduct", node)
        self.connect(node.inputs["Color"], lux, "Vector 1")
        lux.inputs["Vector 2"].default_value = LUMA
        return Linked(lux, "Value")

    def bright_contrast(self, node, out_socket):
        # reader :680
        lux = self.new_node("LuxCoreNodeTexBrightContrast", node)
        self.connect(node.inputs["Color"], lux, "Color")
        self.connect(node.inputs["Bright"], lux, "Brightness")
        self.connect(node.inputs["Contrast"], lux, "Contrast")
        return Linked(lux, "Color")

    def geometry(self, node, out_socket):
        # reader :783
        outputs = {"Position": "Position", "Normal": "Shading Normal"}
        if out_socket.name not in outputs:
            self.warn(f"Unsupported Geometry output socket: {out_socket.name}")
            return None
        lux, is_new = self.shared_node(node, "LuxCoreNodeTexHitpointInfo")
        return Linked(lux, outputs[out_socket.name])

    def object_info(self, node, out_socket):
        # reader :798
        modes = {"Object Index": "objectid", "Random": "objectidnormalized"}
        if out_socket.name == "Material Index":
            return float(self.material.pass_index)
        if out_socket.name not in modes:
            self.warn(f"Unsupported Object Info output socket: {out_socket.name}")
            return None
        lux, is_new = self.shared_node(node, "LuxCoreNodeTexObjectID", out_socket.name)
        lux.mode = modes[out_socket.name]
        return Linked(lux, "Value")

    def blackbody(self, node, out_socket):
        # reader :812
        temperature = node.inputs["Temperature"]
        if temperature.is_linked:
            self.warn("LuxCore does not support textured blackbody temperature")
            return None
        lux = self.new_node("LuxCoreNodeTexBlackbody", node)
        lux.temperature = temperature.default_value
        lux.normalize = True
        return Linked(lux, "Color")

    def map_range(self, node, out_socket):
        # reader :825
        if node.interpolation_type != "LINEAR" or getattr(node, "data_type", "FLOAT") != "FLOAT":
            self.warn(f"In material {self.material.name}: unsupported map range "
                      f"{getattr(node, 'data_type', 'FLOAT')} {node.interpolation_type}")
            return None
        if not node.clamp:
            self.warn(f"In material {self.material.name}: map range node will be clamped")
        lux = self.new_node("LuxCoreNodeTexRemap", node)
        self.connect(node.inputs["Value"], lux, "Value", to_float=True)
        self.connect(node.inputs["From Min"], lux, "Source Min")
        self.connect(node.inputs["From Max"], lux, "Source Max")
        self.connect(node.inputs["To Min"], lux, "Target Min")
        self.connect(node.inputs["To Max"], lux, "Target Max")
        return Linked(lux, "Value")


HANDLERS = {
    "ShaderNodeBsdfPrincipled": _Converter.principled,
    "ShaderNodeMixShader": _Converter.mix_shader,
    "ShaderNodeBsdfDiffuse": _Converter.diffuse,
    "ShaderNodeBsdfSheen": _Converter.sheen,
    "ShaderNodeSubsurfaceScattering": _Converter.subsurface,
    "ShaderNodeBsdfGlossy": _Converter.glossy,
    "ShaderNodeBsdfAnisotropic": _Converter.glossy,
    "ShaderNodeBsdfGlass": _Converter.glass,
    "ShaderNodeBsdfRefraction": _Converter.glass,
    "ShaderNodeBsdfTranslucent": _Converter.translucent,
    "ShaderNodeBsdfTransparent": _Converter.transparent,
    "ShaderNodeHoldout": _Converter.holdout,
    "ShaderNodeEmission": _Converter.emission,
    "ShaderNodeTexImage": _Converter.image,
    "ShaderNodeGamma": _Converter.gamma,
    "ShaderNodeNormalMap": _Converter.normal_map,
    "ShaderNodeBump": _Converter.bump,
    "ShaderNodeMixRGB": _Converter.mix_rgb,
    "ShaderNodeMix": _Converter.mix_rgb,
    "ShaderNodeMath": _Converter.math,
    "ShaderNodeHueSaturation": _Converter.hue_saturation,
    "ShaderNodeValue": _Converter.value,
    "ShaderNodeRGB": _Converter.rgb,
    "ShaderNodeValToRGB": _Converter.color_ramp,
    "ShaderNodeTexChecker": _Converter.checker,
    "ShaderNodeInvert": _Converter.invert,
    "ShaderNodeSeparateXYZ": _Converter.separate,
    "ShaderNodeSeparateColor": _Converter.separate,
    "ShaderNodeSeparateRGB": _Converter.separate,
    "ShaderNodeCombineXYZ": _Converter.combine,
    "ShaderNodeCombineColor": _Converter.combine,
    "ShaderNodeCombineRGB": _Converter.combine,
    "ShaderNodeRGBToBW": _Converter.rgb_to_bw,
    "ShaderNodeBrightContrast": _Converter.bright_contrast,
    "ShaderNodeNewGeometry": _Converter.geometry,
    "ShaderNodeObjectInfo": _Converter.object_info,
    "ShaderNodeBlackbody": _Converter.blackbody,
    "ShaderNodeMapRange": _Converter.map_range,
}
