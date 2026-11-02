import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, StringProperty)
from bpy.types import PropertyGroup
from ..base import LuxCoreNodeMaterial
from ... import icons
from ... import utils
from ...utils import node as utils_node

# The keys of a ramp the core takes at most
MAX_KEYS = 8

RAMP_DESCRIPTION = ("The base tonemap gives the direct light of each light: the color of the ramp at the "
                    "cosine of the light direction (1 facing the light, 0 at the terminator) times the "
                    "Base Color")

INTERPOLATION_ITEMS = [
    ("constant", "Constant", "Each key's color up to the next key: bands", 0),
    ("linear", "Linear", "Linear blend between the keys", 1),
    ("smooth", "Smooth", "Smoothstep blend between the keys", 2),
]

# The ramps of the node: (collection, interpolation, edge softness, add and
# remove buttons, the fewest keys, the core's prefix)
RAMPS = {
    "base": ("ramp_items", "interpolation", "ramp_smooth", "add_item", "remove_item", 1, "ramp"),
    "reflection": ("reflection_ramp_items", "reflection_interpolation", "reflection_ramp_smooth",
                   "reflection_add_item", "reflection_remove_item", 0, "specular.ramp"),
    "rim": ("rim_ramp_items", "rim_interpolation", "rim_ramp_smooth",
            "rim_add_item", "rim_remove_item", 0, "rim.ramp"),
}

# The default tonemaps of a new node: the base one has two bands, the
# reflection one is linear from black to white (Redshift's default: the glossy
# highlight of the Roughness, up to the brightness of a white matte surface
# facing the light), the rim one is off (a plain rim)
DEFAULT_RAMPS = {
    "base": [(0, (0.3, 0.3, 0.3)), (0.3, (1, 1, 1))],
    "reflection": [(0, (0, 0, 0)), (1, (1, 1, 1))],
    "rim": [],
}


class LuxCoreToonRampKey(PropertyGroup):
    """A key of a ramp of the Toon node (as the ColorRamp texture node's items)"""
    offset: FloatProperty(update=utils_node.force_viewport_update, name="Position", default=0, min=0, max=1,
                          description="Where the key starts on the ramp's input")
    value: FloatVectorProperty(update=utils_node.force_viewport_update, name="", min=0, soft_max=1,
                               subtype="COLOR")
    # For internal use
    index: IntProperty()
    node_name: StringProperty()


def _add_key(ramp):
    def update(self, context):
        items = getattr(self, RAMPS[ramp][0])
        if len(items) == 0:
            # A tonemap that was off: the node's default
            self.set_ramp(DEFAULT_RAMPS["reflection"] if ramp == "reflection" else [(0, (1, 1, 1))], ramp)
        elif len(items) < MAX_KEYS:
            last = max(items, key=lambda item: item.offset)
            new_item = items.add()
            new_item.offset = min(last.offset + 0.1, 1)
            new_item.value = last.value
            new_item.index = len(items) - 1
            new_item.node_name = self.name
        utils_node.set_silently(self, RAMPS[ramp][3], False)
        utils_node.force_viewport_update(self, context)
    return update


def _remove_key(ramp):
    def update(self, context):
        items = getattr(self, RAMPS[ramp][0])
        if len(items) > RAMPS[ramp][5]:
            items.remove(len(items) - 1)
        utils_node.set_silently(self, RAMPS[ramp][4], False)
        utils_node.force_viewport_update(self, context)
    return update


class LuxCoreNodeMatToon(LuxCoreNodeMaterial, bpy.types.Node):
    """Toon shading (non-physical), with the parameters of Redshift's Toon material: the
    direct light of each light is the base tonemap, a color ramp on the cosine of its
    direction, times the Base Color, plus the reflection tonemap, a color ramp on the
    glossy highlight of the Roughness, and a rim with its own tonemap; the indirect light
    is the one of a matte material of the Base Color, times the Indirect Scale"""
    bl_label = "Toon Material"
    bl_width_default = 260

    # Base
    ramp_items: CollectionProperty(type=LuxCoreToonRampKey)
    # BoolProperties used as buttons, as in the ColorRamp texture node
    add_item: BoolProperty(name="Add", description="Add a key", default=False, update=_add_key("base"))
    remove_item: BoolProperty(name="Remove", description="Remove the last key", default=False,
                              update=_remove_key("base"))
    interpolation: EnumProperty(update=utils_node.force_viewport_update, name="Interpolation",
                                items=INTERPOLATION_ITEMS, default="constant", description=RAMP_DESCRIPTION)
    ramp_smooth: FloatProperty(update=utils_node.force_viewport_update, name="Edge Softness", default=0,
                               min=0, soft_max=0.2,
                               description="The width (on the ramp's input) over which each constant key "
                                           "blends into the next one")
    base_weight: FloatProperty(update=utils_node.force_viewport_update, name="Weight", default=1,
                               min=0, soft_max=1,
                               description="The brightness of the base: multiplies the Base Color of the "
                                           "tonemap and of the indirect light")
    light_center: BoolProperty(update=utils_node.force_viewport_update, name="Light Center", default=True,
                               description="The bands and the highlight follow the direction of the "
                                           "center of each light, so that area lights give hard bands "
                                           "(their shadows stay soft) and a highlight of the light's "
                                           "size with hard edges; off, each point of a light makes its "
                                           "own band edge, and large lights blur the bands")

    # Reflection
    reflection_weight: FloatProperty(update=utils_node.force_viewport_update, name="Weight", default=1,
                                     min=0, soft_max=1, description="The brightness of the highlight")
    reflection_ramp_items: CollectionProperty(type=LuxCoreToonRampKey)
    reflection_add_item: BoolProperty(name="Add", description="Add a key", default=False,
                                      update=_add_key("reflection"))
    reflection_remove_item: BoolProperty(name="Remove", description="Remove the last key (no keys: a "
                                         "highlight of one step of the Roughness's size, with a "
                                         "softness, as the nodes made before the tonemap)", default=False,
                                         update=_remove_key("reflection"))
    reflection_interpolation: EnumProperty(update=utils_node.force_viewport_update, name="Interpolation",
                                           items=INTERPOLATION_ITEMS, default="linear",
                                           description="The reflection tonemap: the color of the "
                                                       "highlight at the intensity of the glossy "
                                                       "highlight of the Roughness (1: as bright as a "
                                                       "white matte surface facing the light), the "
                                                       "reflection of the whole light (larger lights, "
                                                       "larger highlights). A low Roughness gives a "
                                                       "small bright highlight, a high one a wide dim "
                                                       "one, so a constant key also "
                                                       "makes the highlight smaller as the Roughness "
                                                       "grows, until it is gone")
    reflection_ramp_smooth: FloatProperty(update=utils_node.force_viewport_update, name="Edge Softness",
                                          default=0.05, min=0, soft_max=0.5,
                                          description="The width (in intensity) over which each constant "
                                                      "key blends into the next one")
    # The step of the highlight without a reflection tonemap (the nodes made
    # before the tonemap)
    specular_smooth: FloatProperty(update=utils_node.force_viewport_update, name="Softness", default=0.05,
                                   min=0, max=1, subtype="FACTOR",
                                   description="The angle over which the edge of the highlight fades, as a "
                                               "fraction of 90 degrees (0: a hard edge)")
    # Not in the UI any more, still exported for the nodes that set them
    specular_stretch_u: FloatProperty(update=utils_node.force_viewport_update, name="Stretch U", default=0,
                                      min=-0.95, soft_max=2,
                                      description="Stretches the highlight along the U direction of the UV "
                                                  "map (its size times 1 + stretch)")
    specular_stretch_v: FloatProperty(update=utils_node.force_viewport_update, name="Stretch V", default=0,
                                      min=-0.95, soft_max=2,
                                      description="Stretches the highlight along the V direction of the UV "
                                                  "map (its size times 1 + stretch)")

    # Rim
    rim_weight: FloatProperty(update=utils_node.force_viewport_update, name="Weight", default=1,
                              min=0, soft_max=1, description="The brightness of the rim")
    rim_size: FloatProperty(update=utils_node.force_viewport_update, name="Width", default=0.2,
                            min=0, max=1, subtype="FACTOR",
                            description="The rim covers the eye directions within this angle of the "
                                        "surface, as a fraction of 90 degrees")
    rim_smooth: FloatProperty(update=utils_node.force_viewport_update, name="Softness", default=0.05,
                              min=0, max=1, subtype="FACTOR",
                              description="The angle over which the inner edge of the rim fades, as a "
                                          "fraction of 90 degrees")
    rim_ramp_items: CollectionProperty(type=LuxCoreToonRampKey)
    rim_add_item: BoolProperty(name="Add", description="Add a key", default=False, update=_add_key("rim"))
    rim_remove_item: BoolProperty(name="Remove", description="Remove the last key (no keys: a plain rim)",
                                  default=False, update=_remove_key("rim"))
    rim_interpolation: EnumProperty(update=utils_node.force_viewport_update, name="Interpolation",
                                    items=INTERPOLATION_ITEMS, default="linear",
                                    description="The rim tonemap: the color of the rim across its width "
                                                "(0 at its inner edge, 1 at the silhouette)")
    rim_ramp_smooth: FloatProperty(update=utils_node.force_viewport_update, name="Edge Softness",
                                   default=0, min=0, soft_max=0.2,
                                   description="The width over which each constant key blends into the "
                                               "next one")

    # Advanced. The default 1 is the one of the nodes made before this option
    # (their look); a new node starts at 0 (init()), as Redshift's Base
    # Indirect Scale
    indirect_scale: FloatProperty(update=utils_node.force_viewport_update, name="Base Indirect Scale",
                                  default=1, min=0, max=1, subtype="FACTOR",
                                  description="The weight of the indirect light (global illumination) on "
                                              "the base: 0 keeps the shading flat, 1 lights it as a matte "
                                              "material of the Base Color")

    def init(self, context):
        # inputs[0] gives the viewport color (utils.node.update_opengl_materials)
        self.add_input("LuxCoreSocketColor", "Base Color", (1, 1, 1))
        self.add_input("LuxCoreSocketColor", "Reflection Color", (1, 1, 1))
        # The glossy highlight of the reflection tonemap
        self.add_input("LuxCoreSocketFloat0to1", "Roughness", 0.5)
        self.add_input("LuxCoreSocketColor", "Rim Color", (0, 0, 0))
        self.add_common_inputs()

        self.outputs.new("LuxCoreSocketMaterial", "Material")

        for ramp, keys in DEFAULT_RAMPS.items():
            self.set_ramp(keys, ramp)
        self.indirect_scale = 0

    def copy(self, orig_node):
        for ramp in RAMPS.values():
            for item in getattr(self, ramp[0]):
                # The parent node's name is a StringProperty
                item.node_name = self.name

    def set_ramp(self, keys, ramp="base"):
        """Replace the keys of a ramp ("base" or "rim") by (position, color) pairs"""
        items = getattr(self, RAMPS[ramp][0])
        items.clear()
        for index, (position, color) in enumerate(keys[:MAX_KEYS]):
            item = items.add()
            item.offset = position
            item.value = color
            item.index = index
            item.node_name = self.name

    def _draw_ramp(self, layout, ramp, label):
        items_name, interpolation, smooth, add, remove, fewest, _ = RAMPS[ramp]
        items = getattr(self, items_name)
        row = layout.row(align=True)
        row.label(text=label)
        sub = row.row(align=True)
        sub.enabled = len(items) < MAX_KEYS
        sub.prop(self, add, icon=icons.ADD, text="")
        sub = row.row(align=True)
        sub.enabled = len(items) > fewest
        sub.prop(self, remove, icon=icons.REMOVE, text="")
        if not items:
            return False

        layout.prop(self, interpolation, expand=True)
        col = layout.column(align=True)
        for item in items:
            split = col.split(align=True, factor=0.6)
            split.prop(item, "offset", text="Position", slider=True)
            split.prop(item, "value")
        if getattr(self, interpolation) == "constant":
            layout.prop(self, smooth)
        return True

    def draw_buttons(self, context, layout):
        if not utils.luxutils.has_material_type("toon"):
            layout.label(text="Needs pyluxcore 3.0 or newer", icon=icons.ERROR)

        box = layout.box()
        box.label(text="Base")
        box.prop(self, "base_weight")
        self._draw_ramp(box, "base", "Tonemap")
        box.prop(self, "light_center")

        # The Color and the Roughness of the highlight are sockets
        box = layout.box()
        box.label(text="Reflection")
        box.prop(self, "reflection_weight")
        if not self._draw_ramp(box, "reflection", "Tonemap"):
            box.prop(self, "specular_smooth")

        box = layout.box()
        box.label(text="Rim")
        box.prop(self, "rim_weight")
        col = box.column(align=True)
        col.prop(self, "rim_size")
        col.prop(self, "rim_smooth")
        self._draw_ramp(box, "rim", "Tonemap")

        box = layout.box()
        box.label(text="Advanced")
        box.prop(self, "indirect_scale")

    def _export_ramp(self, ramp, definitions):
        items_name, interpolation, smooth, _, _, _, prefix = RAMPS[ramp]
        items = getattr(self, items_name)
        if not items:
            return
        # The positions in ascending order, as the core requires
        keys = sorted(((item.offset, tuple(item.value)) for item in items), key=lambda k: k[0])
        definitions[prefix + ".positions"] = [k[0] for k in keys]
        definitions[prefix + ".colors"] = [c for k in keys for c in k[1]]
        definitions[prefix + ".interpolation"] = getattr(self, interpolation)
        definitions[prefix + ".smooth"] = getattr(self, smooth)

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        definitions = {
            "type": "toon",
            "kd": self.inputs["Base Color"].export(exporter, depsgraph, props),
            "ks": self.inputs["Reflection Color"].export(exporter, depsgraph, props),
            "specular.roughness": self.inputs["Roughness"].export(exporter, depsgraph, props),
            "specular.smooth": self.specular_smooth,
            "specular.stretch.u": self.specular_stretch_u,
            "specular.stretch.v": self.specular_stretch_v,
            "rim": self.inputs["Rim Color"].export(exporter, depsgraph, props),
            "lightcenter": self.light_center,
            "rim.size": self.rim_size,
            "rim.smooth": self.rim_smooth,
        }
        for ramp in RAMPS:
            self._export_ramp(ramp, definitions)

        # The options of the Redshift layout (pyluxcore from b4ef21f51), only
        # when they differ from the core's defaults
        for key, value, default in (("base.weight", self.base_weight, 1),
                                    ("specular.weight", self.reflection_weight, 1),
                                    ("rim.weight", self.rim_weight, 1),
                                    ("indirect.scale", self.indirect_scale, 1)):
            if value != default:
                definitions[key] = value

        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, luxcore_name)
