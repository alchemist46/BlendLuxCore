"""The Metal Preset node: the Fresnel node's metal presets as the two colors
of the OpenPBR metal (F0 and the F82 edge tint).

The table matches what nk_to_f82() gives from pyluxcore's own n, k of the
preset; the node exports it as two constant colors; an OpenPBR sphere fed by
the node renders like the Metal node with the same Fresnel preset (the
OpenPBR roughness is the square root of the Metal node's, which is the GGX
alpha); the EEVEE nodes get the same colors as Base Color and Specular Tint.

blender -b --factory-startup --python tests/test_metal_preset.py -- <site-packages>
"""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
import numpy as np
import pyluxcore
from blendluxcore.nodes.textures.fresnel import LuxCoreNodeTexFresnel
from blendluxcore.utils import eevee_nodes
from blendluxcore.utils.colorspace import get_working_space
from blendluxcore.utils.errorlog import LuxCoreErrorLog
from blendluxcore.utils.luxutils import has_material_type
from blendluxcore.utils.metal_presets import METAL_PRESETS, get_preset, nk_to_f82

checks = harness.Checks("test_metal_preset")
OUT = tempfile.mkdtemp(prefix="blc-metalpreset-")
R = 64
ORTHO = 3.0
SAMPLES = 64
METAL_ROUGHNESS = 0.09


def close(a, b, tolerance=1e-3):
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def preset_nk(preset, space):
    props = pyluxcore.Properties()
    props.Set(pyluxcore.Property("scene.colorspace.working", space))
    props.Set(pyluxcore.Property("scene.textures.t.type", "fresnelpreset"))
    props.Set(pyluxcore.Property("scene.textures.t.preset", preset.replace("_", " ")))
    luxcore_scene = pyluxcore.Scene()
    luxcore_scene.Parse(props)
    out = luxcore_scene.ToProperties()
    return out.Get("scene.textures.t.n").GetFloats(), out.Get("scene.textures.t.k").GetFloats()


# The table: every preset of the Fresnel node, as nk_to_f82() of pyluxcore's n, k
presets = [item[0] for item in LuxCoreNodeTexFresnel.preset_items]
checks.check(sorted(presets) == sorted(METAL_PRESETS), "the table has every preset of the Fresnel node")
worst = 0
for preset in presets:
    for space in ("rec709", "rec2020", "acescg"):
        expected = nk_to_f82(*preset_nk(preset, space))
        got = get_preset(preset, space)
        worst = max(worst, max(abs(x - y) for a, b in zip(expected, got) for x, y in zip(a, b)))
checks.check(worst < 2e-4, f"the table is nk_to_f82() of pyluxcore's n, k (largest difference {worst:.1e})")

# F0 of the custom n, k is the normal incidence reflectance
n, k = (0.2, 0.4, 1.4), (3.6, 2.4, 1.8)
f0 = [((n_c - 1) ** 2 + k_c ** 2) / ((n_c + 1) ** 2 + k_c ** 2) for n_c, k_c in zip(n, k)]
checks.check(close(nk_to_f82(n, k)[0], f0, 1e-6), "custom n, k: F0 is the normal incidence reflectance")

# The scene: a sphere in front of a gray backdrop, an area light
scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)

bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=64, ring_count=32)
sphere = bpy.context.object
bpy.ops.object.shade_smooth()
bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 2, 0), rotation=(1.5708, 0, 0))
backdrop = bpy.context.object

light_data = bpy.data.lights.new("key", "AREA")
light_data.size = 2
light_data.energy = 400
light_data.luxcore.use_cycles_settings = True
light = bpy.data.objects.new("key", light_data)
scene.collection.objects.link(light)
light.location = (2, -2, 3)
light.rotation_euler = (0.785, 0, 0.785)

camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
camera.data.type = "ORTHO"
camera.data.ortho_scale = ORTHO
scene.collection.objects.link(camera)
camera.location = (0, -5, 0)
camera.rotation_euler = (1.5708, 0, 0)
scene.camera = camera

scene.render.resolution_x = scene.render.resolution_y = R
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = "Standard"


def lux_material(name, node_type):
    material = bpy.data.materials.new(name)
    tree = bpy.data.node_groups.new(name, "luxcore_material_nodes")
    material.luxcore.node_tree = tree
    output = tree.nodes.new("LuxCoreNodeMatOutput")
    node = tree.nodes.new(node_type)
    tree.links.new(node.outputs[0], output.inputs[0])
    return material, tree, node


backdrop_material, _, backdrop_node = lux_material("backdrop", "LuxCoreNodeMatMatte")
backdrop_node.inputs["Diffuse Color"].default_value = (0.5, 0.5, 0.5)
backdrop.data.materials.append(backdrop_material)

# The Metal node with the gold Fresnel preset
metal_material, metal_tree, metal = lux_material("metal", "LuxCoreNodeMatMetal")
metal.input_type = "fresnel"
fresnel = next((link.from_node for link in metal_tree.links if link.to_node == metal), None)
if fresnel is None:
    fresnel = metal_tree.nodes.new("LuxCoreNodeTexFresnel")
    metal_tree.links.new(fresnel.outputs[0], metal.inputs["Fresnel"])
fresnel.input_type = "preset"
fresnel.preset = "gold"
metal.inputs["Roughness"].default_value = METAL_ROUGHNESS

# The OpenPBR node fed by the Metal Preset node
pbr_material, pbr_tree, pbr = lux_material("openpbr", "LuxCoreNodeMatOpenPBR")
preset_node = pbr_tree.nodes.new("LuxCoreNodeTexMetalPreset")
preset_node.preset = "gold"
pbr.inputs["Metalness"].default_value = 1
pbr.inputs["Specular Roughness"].default_value = math.sqrt(METAL_ROUGHNESS)
pbr_tree.links.new(preset_node.outputs["Color"], pbr.inputs["Base Color"])
pbr_tree.links.new(preset_node.outputs["Specular Color"], pbr.inputs["Specular Color"])
checks.check(preset_node.label == "Metal: Gold", f"the node label names the preset ({preset_node.label!r})")

# The export: two constant colors, from the table in the working space
color, tint = get_preset("gold", get_working_space())
exporter = type("Exporter", (), {"node_cache": {}, "scene": scene})()
exported = pyluxcore.Properties()
pbr.export(exporter, None, exported, "m")
base_tex = exported.Get("scene.materials.m.base.color").GetString()
spec_tex = exported.Get("scene.materials.m.specular.color").GetString()
checks.check(exported.Get(f"scene.textures.{base_tex}.type").GetString() == "constfloat3"
             and close(exported.Get(f"scene.textures.{base_tex}.value").GetFloats(), color, 1e-6)
             and close(exported.Get(f"scene.textures.{spec_tex}.value").GetFloats(), tint, 1e-6),
             f"exports Color {np.round(color, 3)} and Specular Color {np.round(tint, 3)} as constant colors")

preset_node.input_type = "custom_nk"
preset_node.n, preset_node.k = n, k
checks.check(preset_node.label == "Metal: Custom n, k"
             and close(preset_node.get_colors()[0], f0, 1e-6), "custom n, k: the node gives nk_to_f82()")
preset_node.input_type = "preset"

# The render: OpenPBR with the node against the Metal node
y, x = np.mgrid[0:R, 0:R]
DISK = np.hypot((x + 0.5) / R - 0.5, (y + 0.5) / R - 0.5) * ORTHO < 0.9


def render(material, name):
    sphere.data.materials.clear()
    sphere.data.materials.append(material)
    LuxCoreErrorLog.clear()
    image = harness.render(scene, "LUXCORE", os.path.join(OUT, name + ".exr"), SAMPLES)[..., :3]
    checks.check(not LuxCoreErrorLog.errors, f"{name}: no export errors")
    return image


if has_material_type("openpbr"):
    metal_image = render(metal_material, "metal")
    pbr_image = render(pbr_material, "openpbr")
    ratio = pbr_image[DISK].mean(axis=0) / np.maximum(metal_image[DISK].mean(axis=0), 1e-6)
    error = np.abs(pbr_image - metal_image)[DISK].mean() / max(metal_image[DISK].mean(), 1e-6)
    checks.check(bool(np.all(np.abs(ratio - 1) < 0.02)) and error < 0.08,
                 f"OpenPBR with the gold Metal Preset renders like the Metal node with the gold Fresnel: "
                 f"mean {np.round(ratio, 3)}, pixel error {error:.3f}")
else:
    print("SKIP pyluxcore has no openpbr material")

# The EEVEE nodes: Base Color and Specular Tint
eevee_nodes.generate(pbr_material)
bsdf = next(node for node in pbr_material.node_tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
checks.check(close(bsdf.inputs["Base Color"].default_value[:3], color)
             and close(bsdf.inputs["Specular Tint"].default_value[:3], tint),
             "EEVEE nodes of OpenPBR with the Metal Preset: Base Color and Specular Tint")
eevee_nodes.generate(metal_material)
bsdf = next(node for node in metal_material.node_tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
checks.check(close(bsdf.inputs["Base Color"].default_value[:3], color)
             and close(bsdf.inputs["Specular Tint"].default_value[:3], tint),
             "EEVEE nodes of the Metal node with the gold Fresnel: Base Color and Specular Tint")

print("images in", OUT)
checks.finish()
