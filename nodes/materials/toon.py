import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, StringProperty)
from bpy.types import PropertyGroup
from ..base import LuxCoreNodeMaterial
from ... import icons
from ... import utils
from ...utils import node as utils_node

# The keys of the ramp the core takes at most
MAX_KEYS = 8

RAMP_DESCRIPTION = ("The ramp gives the direct light of each light: the color of the ramp at the cosine "
                    "of the light direction (1 facing the light, 0 at the terminator) times the Tint")


class LuxCoreToonRampKey(PropertyGroup):
    """A key of the ramp of the Toon node (as the ColorRamp texture node's items)"""
    offset: FloatProperty(update=utils_node.force_viewport_update, name="Position", default=0, min=0, max=1,
                          description="The cosine of the light direction where the key starts "
                                      "(1 facing the light, 0 at the terminator)")
    value: FloatVectorProperty(update=utils_node.force_viewport_update, name="", min=0, soft_max=1,
                               subtype="COLOR")
    # For internal use
    index: IntProperty()
    node_name: StringProperty()


class LuxCoreNodeMatToon(LuxCoreNodeMaterial, bpy.types.Node):
    """Toon shading (non-physical): the direct light of each light is a color ramp on the
    cosine of its direction, times the Tint, with a flat highlight and a rim; the indirect
    light is the one of a matte material of the Tint color"""
    bl_label = "Toon Material"
    bl_width_default = 240

    def update_add(self, context):
        last = max(self.ramp_items, key=lambda item: item.offset)
        new_item = self.ramp_items.add()
        new_item.offset = min(last.offset + 0.1, 1)
        new_item.value = last.value
        new_item.index = len(self.ramp_items) - 1
        new_item.node_name = self.name
        utils_node.set_silently(self, "add_item", False)
        utils_node.force_viewport_update(self, context)

    def update_remove(self, context):
        if len(self.ramp_items) > 1:
            self.ramp_items.remove(len(self.ramp_items) - 1)
        utils_node.set_silently(self, "remove_item", False)
        utils_node.force_viewport_update(self, context)

    ramp_items: CollectionProperty(type=LuxCoreToonRampKey)
    # BoolProperties used as buttons, as in the ColorRamp texture node
    add_item: BoolProperty(name="Add", description="Add a key", default=False, update=update_add)
    remove_item: BoolProperty(name="Remove", description="Remove the last key", default=False,
                              update=update_remove)
    interpolation: EnumProperty(update=utils_node.force_viewport_update, name="Interpolation",
                                items=[
                                    ("constant", "Constant", "Each key's color up to the next key: bands", 0),
                                    ("linear", "Linear", "Linear blend between the keys", 1),
                                    ("smooth", "Smooth", "Smoothstep blend between the keys", 2),
                                ],
                                default="constant", description=RAMP_DESCRIPTION)
    ramp_smooth: FloatProperty(update=utils_node.force_viewport_update, name="Edge Softness", default=0,
                               min=0, soft_max=0.2,
                               description="The width (in cosine) over which each constant key blends "
                                           "into the next one")
    light_center: BoolProperty(update=utils_node.force_viewport_update, name="Light Center", default=True,
                               description="The bands and the toon highlight follow the direction of the "
                                           "center of each light, so that area lights give hard bands "
                                           "(their shadows stay soft); off, each point of a light makes "
                                           "its own band edge, and large lights blur the bands")

    specular_smooth: FloatProperty(update=utils_node.force_viewport_update, name="Softness", default=0.05,
                                   min=0, max=1, subtype="FACTOR",
                                   description="The angle over which the edge of the highlight fades, as a "
                                               "fraction of 90 degrees (0: a hard edge)")
    specular_stretch_u: FloatProperty(update=utils_node.force_viewport_update, name="Stretch U", default=0,
                                      min=-0.95, soft_max=2,
                                      description="Stretches the highlight along the U direction of the UV "
                                                  "map (its size times 1 + stretch)")
    specular_stretch_v: FloatProperty(update=utils_node.force_viewport_update, name="Stretch V", default=0,
                                      min=-0.95, soft_max=2,
                                      description="Stretches the highlight along the V direction of the UV "
                                                  "map (its size times 1 + stretch)")
    rim_size: FloatProperty(update=utils_node.force_viewport_update, name="Rim Size", default=0.2,
                            min=0, max=1, subtype="FACTOR",
                            description="The rim covers the eye directions within this angle of the "
                                        "surface, as a fraction of 90 degrees")
    rim_smooth: FloatProperty(update=utils_node.force_viewport_update, name="Rim Smooth", default=0.05,
                              min=0, max=1, subtype="FACTOR",
                              description="The angle over which the rim fades, as a fraction of 90 degrees")

    def init(self, context):
        # inputs[0] gives the viewport color (utils.node.update_opengl_materials)
        self.add_input("LuxCoreSocketColor", "Tint", (1, 1, 1))
        self.add_input("LuxCoreSocketColor", "Specular Color", (0, 0, 0))
        # The size of the highlight: the one of a glossy highlight of this roughness
        self.add_input("LuxCoreSocketFloat0to1", "Roughness", 0.5)
        self.add_input("LuxCoreSocketColor", "Rim Color", (0, 0, 0))
        self.add_common_inputs()

        self.outputs.new("LuxCoreSocketMaterial", "Material")

        # Two bands: 30% from the terminator, full light from cos 0.3
        self.set_ramp([(0, (0.3, 0.3, 0.3)), (0.3, (1, 1, 1))])

    def copy(self, orig_node):
        for item in self.ramp_items:
            # The parent node's name is a StringProperty
            item.node_name = self.name

    def set_ramp(self, keys):
        """Replace the keys of the ramp by (position, color) pairs"""
        self.ramp_items.clear()
        for index, (position, color) in enumerate(keys[:MAX_KEYS]):
            item = self.ramp_items.add()
            item.offset = position
            item.value = color
            item.index = index
            item.node_name = self.name

    def draw_buttons(self, context, layout):
        if not utils.luxutils.has_material_type("toon"):
            layout.label(text="Needs pyluxcore 3.0 or newer", icon=icons.ERROR)

        layout.prop(self, "interpolation", expand=True)
        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.enabled = len(self.ramp_items) < MAX_KEYS
        sub.prop(self, "add_item", icon=icons.ADD)
        sub = row.row(align=True)
        sub.enabled = len(self.ramp_items) > 1
        sub.prop(self, "remove_item", icon=icons.REMOVE)
        col = layout.column(align=True)
        for item in self.ramp_items:
            split = col.split(align=True, factor=0.6)
            split.prop(item, "offset", text="Position", slider=True)
            split.prop(item, "value")
        if self.interpolation == "constant":
            layout.prop(self, "ramp_smooth")
        layout.prop(self, "light_center")

        col = layout.column(align=True)
        col.label(text="Highlight:")
        col.prop(self, "specular_smooth")
        col.prop(self, "specular_stretch_u")
        col.prop(self, "specular_stretch_v")

        col = layout.column(align=True)
        col.prop(self, "rim_size")
        col.prop(self, "rim_smooth")

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        # The positions in ascending order, as the core requires
        keys = sorted(((item.offset, tuple(item.value)) for item in self.ramp_items), key=lambda k: k[0])

        definitions = {
            "type": "toon",
            "kd": self.inputs["Tint"].export(exporter, depsgraph, props),
            "ks": self.inputs["Specular Color"].export(exporter, depsgraph, props),
            "specular.roughness": self.inputs["Roughness"].export(exporter, depsgraph, props),
            "specular.smooth": self.specular_smooth,
            "specular.stretch.u": self.specular_stretch_u,
            "specular.stretch.v": self.specular_stretch_v,
            "rim": self.inputs["Rim Color"].export(exporter, depsgraph, props),
            "ramp.positions": [k[0] for k in keys],
            "ramp.colors": [c for k in keys for c in k[1]],
            "ramp.interpolation": self.interpolation,
            "ramp.smooth": self.ramp_smooth,
            "lightcenter": self.light_center,
            "rim.size": self.rim_size,
            "rim.smooth": self.rim_smooth,
        }
        self.export_common_inputs(exporter, depsgraph, props, definitions)
        return self.create_props(props, definitions, luxcore_name)
