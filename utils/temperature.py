"""Normalization of the blackbody colour of the LuxCore Temperature color mode
(lights and world) and of the LuxCore Blackbody texture node.

LuxCore's ".normalization" (pyluxcore 3.0 builds from 2026-10-23 on):
"luminance" scales the blackbody colour to luminance 1, as Cycles does;
"max" divides it by the largest RGB value of the blackbody spectrum, which is
what the older addon versions exported (0.72x the luminance at 6500 K, 0.43x
at 3000 K).

New lights, worlds and nodes use Luminance. Data saved by an older addon
version has no value stored: it is set to Max on load, so old files render as
before, and when the addon is enabled with a saved file open. Every value is
written when a file is saved (save_pre), and the value of a new light or world
in the first depsgraph update it is in (an autosave runs no save_pre), so
"nothing stored" means "saved by an older addon version" (or created while
the addon was disabled)."""

import bpy

NORMALIZATION_ITEMS = [
    ("luminance", "Luminance", "Scale the blackbody color to luminance 1, as Cycles does", 0),
    ("max", "Max (Legacy)", "Divide the blackbody color by the largest value of the blackbody spectrum, "
                            "as older BlendLuxCore versions did (darker than Luminance: 0.72x at 6500 K, "
                            "0.43x at 3000 K)", 1),
]
_INDEX = {item[0]: item[3] for item in NORMALIZATION_ITEMS}


# Max (Legacy) against Luminance: the luminance of LuxCore's Max blackbody color, the
# Luminance one being 1, per temperature in K (measured with pyluxcore 3.0, 2026-10-29).
# Without normalization the color is the Max one times MAX_NORMALIZATION.
_MAX_TO_LUMINANCE = (
    (1000, 0.0227), (1100, 0.0316), (1200, 0.0424), (1300, 0.0550), (1400, 0.0693),
    (1500, 0.0852), (1600, 0.1026), (1700, 0.1214), (1800, 0.1415), (1900, 0.1628),
    (2000, 0.1847), (2250, 0.2429), (2500, 0.3044), (2750, 0.3676), (3000, 0.4315),
    (3250, 0.4951), (3500, 0.5578), (3750, 0.6190), (4000, 0.6785), (4250, 0.7308),
    (4500, 0.7687), (4750, 0.7933), (5000, 0.8059), (5250, 0.8084), (5500, 0.8025),
    (5750, 0.7897), (6000, 0.7716), (6250, 0.7495), (6500, 0.7245), (6750, 0.6974),
    (7000, 0.6691), (7250, 0.6401), (7500, 0.6110), (7750, 0.5825), (8000, 0.5568),
    (8250, 0.5338), (8500, 0.5132), (8750, 0.4945), (9000, 0.4777), (9250, 0.4624),
    (9500, 0.4484), (9750, 0.4357), (10000, 0.4239), (10250, 0.4131), (10500, 0.4032),
    (10750, 0.3940), (11000, 0.3854), (11250, 0.3775), (11500, 0.3701), (11750, 0.3631),
    (12000, 0.3567), (12250, 0.3506), (12500, 0.3449), (12750, 0.3396), (13000, 0.3346),
)
MAX_NORMALIZATION = 89159.6


def luminance_factor(temperature, normalization):
    """The luminance of LuxCore's blackbody color with normalization (None: Normalize off),
    relative to Luminance; for a preview with a color at luminance 1, as Cycles' Blackbody node"""
    if normalization == "luminance":
        return 1.0
    table = _MAX_TO_LUMINANCE
    if temperature <= table[0][0]:
        # Down to 0 at 0 K
        factor = table[0][1] * max(temperature, 0) / table[0][0]
    elif temperature >= table[-1][0]:
        factor = table[-1][1]
    else:
        for (t0, f0), (t1, f1) in zip(table, table[1:]):
            if temperature <= t1:
                factor = f0 + (f1 - f0) * (temperature - t0) / (t1 - t0)
                break
    return factor if normalization == "max" else factor * MAX_NORMALIZATION

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


def store_all(ids=None):
    """Write the value of every local light, world and Blackbody node into the file (save_pre),
    or of those in ids (new ones, from a depsgraph update: an autosave runs no save_pre)"""
    for id_data, owner, prop in _owners(ids):
        if id_data.library is None and not owner.is_property_set(prop):
            store(owner, prop, getattr(owner, prop))
