"""Normalization of the blackbody colour of the LuxCore Temperature color mode
(lights and world) and of the LuxCore Blackbody texture node.

LuxCore's ".normalization" (pyluxcore 3.0 builds from 2026-10-23 on):
"luminance" scales the blackbody colour to luminance 1, as Cycles does;
"max" divides it by the largest RGB value of the blackbody spectrum, which is
what the older addon versions exported (0.72x the luminance at 6500 K, 0.43x
at 3000 K).

New lights, worlds and nodes use Luminance. Data saved by an older addon
version has no value stored: it is set to Max on load, so old files render as
before. Every value is written when a file is saved (save_pre), so "nothing
stored" always means "saved by an older addon version".
"""

import bpy

NORMALIZATION_ITEMS = [
    ("luminance", "Luminance", "Scale the blackbody color to luminance 1, as Cycles does", 0),
    ("max", "Max (Legacy)", "Divide the blackbody color by the largest value of the blackbody spectrum, "
                            "as older BlendLuxCore versions did (darker than Luminance: 0.72x at 6500 K, "
                            "0.43x at 3000 K)", 1),
]
_INDEX = {item[0]: item[3] for item in NORMALIZATION_ITEMS}

# Property names: on the luxcore settings of lights and worlds, on the Blackbody node
LIGHT_PROP = "temperature_normalization"
NODE_PROP = "normalization"
NODE_IDNAME = "LuxCoreNodeTexBlackbody"


def get(owner, prop):
    """The normalization of a light's or world's luxcore settings, or of a Blackbody node.
    Data linked from a file of an older addon version has none stored: Max, as it rendered there"""
    id_data = owner.id_data
    id_data = getattr(id_data, "original", None) or id_data
    if id_data.library is not None and not owner.is_property_set(prop):
        return "max"
    return getattr(owner, prop)


def export(definitions, prefix, normalization):
    """Set the normalization of the temperature at prefix ("" for a texture's own keys). The
    pyluxcore builds before 2026-10-23 ignore ".normalization" and use ".normalize" (Max)"""
    key = prefix + "." if prefix else ""
    definitions[key + "normalize"] = True
    if normalization != "max":
        definitions[key + "normalization"] = normalization


def store(owner, prop, value):
    """Store value without running the update callback"""
    if isinstance(owner, bpy.types.Node) and hasattr(owner, "bl_system_properties_get"):
        # Blender 5: node["name"] is a user property, not the bpy.props value
        owner.bl_system_properties_get(do_create=True)[prop] = _INDEX[value]
    else:
        owner[prop] = _INDEX[value]


def _owners(ids=None):
    """(ID, owner, property name) of every light, world and Blackbody node, or of those in ids"""
    if ids is None:
        ids = list(bpy.data.lights) + list(bpy.data.worlds) + list(bpy.data.node_groups)
    for id_data in ids:
        if isinstance(id_data, (bpy.types.Light, bpy.types.World)):
            yield id_data, id_data.luxcore, LIGHT_PROP
        elif isinstance(id_data, bpy.types.NodeTree):
            for node in id_data.nodes:
                if node.bl_idname == NODE_IDNAME:
                    yield id_data, node, NODE_PROP


def set_legacy(ids=None):
    """Max on the lights, worlds and Blackbody nodes saved by an older addon version (none
    stored). Called after loading a file and after appending; ids: the appended ones"""
    count = 0
    for id_data, owner, prop in _owners(ids):
        if id_data.library is None and not owner.is_property_set(prop):
            store(owner, prop, "max")
            count += 1
    return count


def store_all():
    """Write the value of every local light, world and Blackbody node into the file (save_pre)"""
    for id_data, owner, prop in _owners():
        if id_data.library is None and not owner.is_property_set(prop):
            store(owner, prop, getattr(owner, prop))
