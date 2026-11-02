"""EEVEE node sync: the Cycles/EEVEE nodes generated from LuxCore node trees.

1. Structure: LuxCore materials (OpenPBR, Disney, Glass, Matte + Emission,
   Mix, image map with 2D mapping, Math and Color Math, Metal with a Fresnel
   preset) give the expected Cycles nodes and values.
2. Renders: the generated Cycles nodes rendered by Cycles look roughly like
   the LuxCore tree rendered by LuxCore (an approximation: loose tolerances).
3. Sync: a socket value change marks the LuxCore tree and the flush updates
   the Cycles nodes in place; a link change rebuilds them; a flush with
   nothing changed changes nothing.
4. Protection: Cycles nodes edited by hand are not overwritten, the sync
   switches itself off with a warning; "Update EEVEE Nodes" overwrites them.
5. Default tree detection.
6. With the Cycles conversion operator: the converted material keeps its
   original Cycles nodes until the LuxCore tree is edited, and a hidden backup.

EEVEE needs a GPU, so Cycles (CPU) renders the generated nodes.

blender -b --factory-startup --python tests/test_eevee_sync.py -- <site-packages>
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
from blendluxcore.utils import eevee_nodes, eevee_sync
from blendluxcore.utils.errorlog import LuxCoreErrorLog
from blendluxcore.utils.luxutils import has_material_type
from blendluxcore.utils.node import get_active_output, get_linked_node

checks = harness.Checks("test_eevee_sync")
OUT = tempfile.mkdtemp(prefix="blc-eevee-")
R = 64
ORTHO = 3.0
SAMPLES = 64
HAS_OPENPBR = has_material_type("openpbr")


def close(a, b, tolerance=1e-4):
    try:
        return all(abs(x - y) <= tolerance for x, y in zip(a, b))
    except TypeError:
        return abs(a - b) <= tolerance


def lux_material(name, idname, inputs=None, props=None):
    """A material with a LuxCore tree: output <- one material node"""
    material = bpy.data.materials.new(name)
    tree = bpy.data.node_groups.new(name, "luxcore_material_nodes")
    tree.use_fake_user = True
    material.luxcore.node_tree = tree
    output = tree.nodes.new("LuxCoreNodeMatOutput")
    output.location = (300, 0)
    node = tree.nodes.new(idname)
    for key, value in (props or {}).items():
        setattr(node, key, value)
    for key, value in (inputs or {}).items():
        node.inputs[node.inputs.find(key)].default_value = value
    tree.links.new(node.outputs[0], output.inputs["Material"])
    return material, tree, node


def surface_node(material):
    for node in material.node_tree.nodes:
        if node.bl_idname == "ShaderNodeOutputMaterial" and node.inputs["Surface"].links:
            return node.inputs["Surface"].links[0].from_node
    return None


def linked_from(node, input_key):
    socket = eevee_nodes._find_socket(node.inputs, input_key)
    return socket.links[0].from_node if socket is not None and socket.links else None


def value(node, input_key):
    return eevee_nodes._find_socket(node.inputs, input_key).default_value


# ------------------------------------------------------------------------------
# 1. Structure
# ------------------------------------------------------------------------------

material, tree, node = lux_material("openpbr", "LuxCoreNodeMatOpenPBR", {
    "Base Color": (0.8, 0.3, 0.1),
    "Specular Roughness": 0.4,
    "Specular Weight": 1.6,
})
status = eevee_nodes.generate(material)
bsdf = surface_node(material)
checks.check(status == "rebuilt" and bsdf is not None and bsdf.bl_idname == "ShaderNodeBsdfPrincipled"
             and close(value(bsdf, "Base Color")[:3], (0.8, 0.3, 0.1))
             and close(value(bsdf, "Roughness"), 0.4)
             and close(value(bsdf, "Specular IOR Level"), 0.8),
             f"OpenPBR: Principled BSDF with base color, roughness and Specular IOR Level = weight / 2 ({status})")
checks.check(material.luxcore.eevee_nodes_signature == eevee_nodes.tree_signature(material.node_tree)
             and eevee_nodes.is_ours(material), "OpenPBR: the signature of the generated tree is stored")

material, tree, node = lux_material("disney_metal", "LuxCoreNodeMatDisney", {
    "Base Color": (0.9, 0.6, 0.3), "Metallic": 1.0, "Roughness": 0.3,
})
eevee_nodes.generate(material)
bsdf = surface_node(material)
checks.check(bsdf.bl_idname == "ShaderNodeBsdfPrincipled" and close(value(bsdf, "Metallic"), 1)
             and close(value(bsdf, "Roughness"), 0.3), "Disney metal: Metallic 1, roughness as is")

material, tree, node = lux_material("glass", "LuxCoreNodeMatGlass", {"IOR": 1.45})
eevee_nodes.generate(material)
bsdf = surface_node(material)
checks.check(close(value(bsdf, "Transmission Weight"), 1) and close(value(bsdf, "IOR"), 1.45)
             and close(value(bsdf, "Roughness"), 0), "Glass: transmission 1, IOR, smooth")

material, tree, matte = lux_material("emission", "LuxCoreNodeMatMatte", {"Diffuse Color": (0, 0, 0)})
emission = tree.nodes.new("LuxCoreNodeMatEmission")
emission.gain = 2
emission.exposure = 1
emission.inputs["Color"].default_value = (1, 0.5, 0.2)
tree.links.new(emission.outputs["Emission"], matte.inputs["Emission"])
eevee_nodes.generate(material)
shader = surface_node(material)
checks.check(shader.bl_idname == "ShaderNodeEmission" and close(value(shader, "Strength"), 4)
             and close(value(shader, "Color")[:3], (1, 0.5, 0.2)),
             f"Matte black + Emission: Emission shader with strength gain * 2^exposure ({shader.bl_idname})")

material, tree, mix = lux_material("mix", "LuxCoreNodeMatMix", {"Mix Factor": 0.3})
matte1 = tree.nodes.new("LuxCoreNodeMatMatte")
matte2 = tree.nodes.new("LuxCoreNodeMatMatte")
matte1.inputs["Diffuse Color"].default_value = (0.8, 0.1, 0.1)
matte2.inputs["Diffuse Color"].default_value = (0.1, 0.1, 0.8)
tree.links.new(matte1.outputs[0], mix.inputs["Material 1"])
tree.links.new(matte2.outputs[0], mix.inputs["Material 2"])
eevee_nodes.generate(material)
shader = surface_node(material)
first, second = linked_from(shader, "Shader"), linked_from(shader, "Shader_001")
checks.check(shader.bl_idname == "ShaderNodeMixShader" and close(value(shader, "Fac"), 0.3)
             and first is not None and first.name == "lux_" + matte1.name
             and second is not None and second.name == "lux_" + matte2.name
             and first.bl_idname == "ShaderNodeBsdfDiffuse",
             "Mix: Mix Shader of two Diffuse BSDFs named after the LuxCore nodes")

# An image map with a 2D mapping, saved so that both engines read the same file
grid = bpy.data.images.new("grid", 16, 16)
grid.generated_type = "COLOR_GRID"
grid.filepath_raw = os.path.join(OUT, "grid.png")
grid.file_format = "PNG"
grid.save()
grid = bpy.data.images.load(os.path.join(OUT, "grid.png"))

material, tree, matte = lux_material("imagemap", "LuxCoreNodeMatMatte")
imagemap = tree.nodes.new("LuxCoreNodeTexImagemap")
imagemap.image = grid
mapping = tree.nodes.new("LuxCoreNodeTexMapping2D")
mapping.use_uniform_scale = False
mapping.uscale = 2
mapping.vscale = 3
mapping.rotation = math.radians(30)
mapping.udelta = 0.1
mapping.vdelta = 0.2
tree.links.new(mapping.outputs[0], imagemap.inputs["2D Mapping"])
tree.links.new(imagemap.outputs["Color"], matte.inputs["Diffuse Color"])
eevee_nodes.generate(material)
diffuse = surface_node(material)
image_node = linked_from(diffuse, "Color")
# A gamma helper may sit between them when the image is not read like LuxCore does (gamma 2.2 here)
if image_node is not None and image_node.bl_idname == "ShaderNodeGamma":
    image_node = linked_from(image_node, "Color")
mapping_node = linked_from(image_node, "Vector") if image_node else None
uv_node = linked_from(mapping_node, "Vector") if mapping_node else None
checks.check(image_node is not None and image_node.bl_idname == "ShaderNodeTexImage" and image_node.image == grid
             and mapping_node is not None and mapping_node.vector_type == "POINT"
             and close(value(mapping_node, "Location"), (0.1, -0.2, 0))
             and close(value(mapping_node, "Rotation"), (0, 0, math.radians(30)))
             and close(value(mapping_node, "Scale"), (2, 3, 1))
             and uv_node is not None and uv_node.bl_idname == "ShaderNodeUVMap",
             "Image map + 2D mapping: Image Texture <- Mapping (POINT) <- UV Map with the mapping values")

material, tree, matte = lux_material("math", "LuxCoreNodeMatMatte")
math_node = tree.nodes.new("LuxCoreNodeTexMath")
math_node.mode = "mix"
math_node.inputs[0].default_value = 0.2
math_node.inputs[1].default_value = 0.8
math_node.inputs[2].default_value = 0.25
color_math = tree.nodes.new("LuxCoreNodeTexColorMix")
color_math.mode = "scale"
color_math.inputs[2].default_value = (0.5, 0.5, 0.5)
tree.links.new(math_node.outputs[0], color_math.inputs[1])
tree.links.new(color_math.outputs[0], matte.inputs["Diffuse Color"])
eevee_nodes.generate(material)
diffuse = surface_node(material)
mix_rgb = linked_from(diffuse, "Color")
mix_float = linked_from(mix_rgb, "A_Color") if mix_rgb else None
checks.check(mix_rgb is not None and mix_rgb.bl_idname == "ShaderNodeMix" and mix_rgb.data_type == "RGBA"
             and mix_rgb.blend_type == "MULTIPLY"
             and mix_float is not None and mix_float.bl_idname == "ShaderNodeMix" and mix_float.data_type == "FLOAT"
             and close(value(mix_float, "Factor_Float"), 0.25),
             "Math (mix) and Color Math (multiply): Mix FLOAT into Mix RGBA MULTIPLY")

material, tree, metal = lux_material("gold", "LuxCoreNodeMatMetal")
metal.input_type = "fresnel"  # adds and links a Fresnel node the first time
fresnel = get_linked_node(metal.inputs["Fresnel"])
if fresnel is None:
    fresnel = tree.nodes.new("LuxCoreNodeTexFresnel")
    tree.links.new(fresnel.outputs[0], metal.inputs["Fresnel"])
fresnel.input_type = "preset"
fresnel.preset = "gold"
eevee_nodes.generate(material)
bsdf = surface_node(material)
checks.check(close(value(bsdf, "Metallic"), 1)
             and close(value(bsdf, "Base Color")[:3], eevee_nodes.METAL_PRESET_COLORS["gold"]),
             "Metal with the gold Fresnel preset: metallic with the preset color")
fresnel.input_type = "custom_nk"
fresnel.n = (0.2, 0.4, 1.4)
fresnel.k = (3.6, 2.4, 1.8)
eevee_nodes.generate(material)
expected = [((n - 1) ** 2 + k ** 2) / ((n + 1) ** 2 + k ** 2) for n, k in zip(fresnel.n, fresnel.k)]
checks.check(close(value(surface_node(material), "Base Color")[:3], expected, 1e-3),
             "Metal with custom n, k: Base Color is F0 per channel")

# ------------------------------------------------------------------------------
# 2. Renders: Cycles with the generated nodes vs LuxCore with the LuxCore tree
# ------------------------------------------------------------------------------

scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)

bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=64, ring_count=32)
sphere = bpy.context.object
bpy.ops.object.shade_smooth()
bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 2, 0), rotation=(1.5708, 0, 0))
backdrop = bpy.context.object
backdrop_material, _, _ = lux_material("backdrop", "LuxCoreNodeMatMatte", {"Diffuse Color": (0.5, 0.5, 0.5)})
eevee_nodes.generate(backdrop_material)
backdrop.data.materials.append(backdrop_material)

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

y, x = np.mgrid[0:R, 0:R]
DISK = np.hypot((x + 0.5) / R - 0.5, (y + 0.5) / R - 0.5) * ORTHO < 0.9


def render_both(name, material):
    sphere.data.materials.clear()
    sphere.data.materials.append(material)
    LuxCoreErrorLog.clear()
    cycles = harness.render(scene, "CYCLES", os.path.join(OUT, name + "_cycles.exr"), SAMPLES)
    luxcore = harness.render(scene, "LUXCORE", os.path.join(OUT, name + "_luxcore.exr"), SAMPLES)
    checks.check(not LuxCoreErrorLog.errors, f"{name}: no LuxCore export errors")
    return cycles, luxcore


def disk_ratio(cycles, luxcore, row_shift=0):
    c = cycles[..., :3]
    l = np.roll(luxcore[..., :3], row_shift, axis=0)
    return l[DISK].mean(axis=0) / np.maximum(c[DISK].mean(axis=0), 1e-6)


# LuxCore's image can be one row off Cycles' (a core camera bug): find the shift on a diffuse sphere
material, _, _ = lux_material("render_matte", "LuxCoreNodeMatMatte", {"Diffuse Color": (0.8, 0.3, 0.1)})
eevee_nodes.generate(material)
cycles, luxcore = render_both("matte", material)


def pixel_error(shift):
    return np.abs(np.roll(luxcore[..., :3], shift, axis=0) - cycles[..., :3])[DISK].mean()


row_shift = min((0, 1, -1), key=pixel_error)
ratio = disk_ratio(cycles, luxcore, row_shift)
checks.check(bool(np.all(np.abs(ratio - 1) < 0.05)), f"matte: LuxCore/Cycles mean {np.round(ratio, 3)}")

dielectric_type = "LuxCoreNodeMatOpenPBR" if HAS_OPENPBR else "LuxCoreNodeMatDisney"
dielectric_inputs = ({"Base Color": (0.8, 0.3, 0.1), "Specular Roughness": 0.4} if HAS_OPENPBR
                     else {"Base Color": (0.8, 0.3, 0.1), "Roughness": 0.4})
for name, idname, inputs in (
        ("dielectric", dielectric_type, dielectric_inputs),
        ("metal", "LuxCoreNodeMatDisney", {"Base Color": (0.9, 0.6, 0.3), "Metallic": 1.0, "Roughness": 0.3}),
):
    material, _, _ = lux_material("render_" + name, idname, inputs)
    eevee_nodes.generate(material)
    cycles, luxcore = render_both(name, material)
    ratio = disk_ratio(cycles, luxcore, row_shift)
    checks.check(bool(np.all(np.abs(ratio - 1) < 0.15)), f"{name}: LuxCore/Cycles mean {np.round(ratio, 3)} (0.15)")

material, _, _ = lux_material("render_glass", "LuxCoreNodeMatGlass")
eevee_nodes.generate(material)
cycles, luxcore = render_both("glass", material)
mean_c, mean_l = cycles[..., :3][DISK].mean(), luxcore[..., :3][DISK].mean()
checks.check(mean_c > 0.02 and mean_l > 0.02, f"glass: both render something ({mean_c:.3f}, {mean_l:.3f})")

material, tree, matte = lux_material("render_emission", "LuxCoreNodeMatMatte", {"Diffuse Color": (0, 0, 0)})
emission = tree.nodes.new("LuxCoreNodeMatEmission")
emission.inputs["Color"].default_value = (1, 0.5, 0.2)
tree.links.new(emission.outputs["Emission"], matte.inputs["Emission"])
eevee_nodes.generate(material)
cycles, luxcore = render_both("emission", material)
ratio = disk_ratio(cycles, luxcore, row_shift)
checks.check(cycles[..., 0][DISK].mean() > 0.02 and bool(np.all(np.abs(ratio - 1) < 0.15)),
             f"emission: LuxCore/Cycles mean {np.round(ratio, 3)} (0.15)")

material, tree, mix = lux_material("render_mix", "LuxCoreNodeMatMix", {"Mix Factor": 0.3})
matte1 = tree.nodes.new("LuxCoreNodeMatMatte")
matte2 = tree.nodes.new("LuxCoreNodeMatMatte")
matte1.inputs["Diffuse Color"].default_value = (0.8, 0.1, 0.1)
matte2.inputs["Diffuse Color"].default_value = (0.1, 0.1, 0.8)
tree.links.new(matte1.outputs[0], mix.inputs["Material 1"])
tree.links.new(matte2.outputs[0], mix.inputs["Material 2"])
eevee_nodes.generate(material)
cycles, luxcore = render_both("mix", material)
ratio = disk_ratio(cycles, luxcore, row_shift)
checks.check(bool(np.all(np.abs(ratio - 1) < 0.15)), f"mix: LuxCore/Cycles mean {np.round(ratio, 3)} (0.15)")

# The 2D mapping of the generated Image Texture must place the image like LuxCore:
# an inverted rotation, scale or V axis moves the grid cells (large pixel error)
for name, mapping_props in (
        ("image_identity", {}),
        ("image_mapping", {"use_uniform_scale": False, "uscale": 2, "vscale": 3,
                           "rotation": math.radians(30), "udelta": 0.1, "vdelta": 0.2}),
        ("image_centered", {"use_uniform_scale": True, "uniform_scale": 2, "center_map": True}),
):
    material, tree, matte = lux_material("render_" + name, "LuxCoreNodeMatMatte")
    image = tree.nodes.new("LuxCoreNodeTexImagemap")
    image.image = grid
    tree.links.new(image.outputs["Color"], matte.inputs["Diffuse Color"])
    if mapping_props:
        mapping = tree.nodes.new("LuxCoreNodeTexMapping2D")
        for key, prop_value in mapping_props.items():
            setattr(mapping, key, prop_value)
        tree.links.new(mapping.outputs["2D Mapping"], image.inputs["2D Mapping"])
    eevee_nodes.generate(material)
    cycles, luxcore = render_both(name, material)
    error = (np.abs(np.roll(luxcore[..., :3], row_shift, axis=0) - cycles[..., :3])[DISK].mean()
             / max(cycles[..., :3][DISK].mean(), 1e-6))
    checks.check(error < 0.15, f"{name}: Cycles and LuxCore place the image alike (pixel error {error:.3f})")

# ------------------------------------------------------------------------------
# 3. Sync
# ------------------------------------------------------------------------------

material, tree, matte = lux_material("sync", "LuxCoreNodeMatMatte", {"Diffuse Color": (0.2, 0.4, 0.6)})
status = eevee_sync.enable(material)
checks.check(material.luxcore.sync_eevee_nodes and status == "rebuilt", f"enable() switches the sync on ({status})")
diffuse = surface_node(material)
diffuse.label = "marker"  # a label survives an in-place update, not a rebuild
pointer = diffuse.as_pointer()
eevee_sync.reset()

matte.inputs["Diffuse Color"].default_value = (0.9, 0.1, 0.1)
checks.check(tree.name_full in eevee_sync._dirty_trees, "a LuxCore socket value change marks the tree")
eevee_sync.mark_tree(tree)  # keep testing the flush when the mark is missing
processed = eevee_sync.flush()
diffuse = surface_node(material)
checks.check(processed == 1 and diffuse.label == "marker" and diffuse.as_pointer() == pointer
             and close(value(diffuse, "Color")[:3], (0.9, 0.1, 0.1)),
             "the flush updates the value in place (same node)")

bright = tree.nodes.new("LuxCoreNodeTexBrightContrast")
bright.inputs["Color"].default_value = (0.3, 0.6, 0.3)
tree.links.new(bright.outputs[0], matte.inputs["Diffuse Color"])
checks.check(tree.name_full in eevee_sync._dirty_trees, "a LuxCore link change marks the tree")
eevee_sync.mark_tree(tree)
eevee_sync.flush()
diffuse = surface_node(material)
source = linked_from(diffuse, "Color")
checks.check(diffuse.label != "marker" and source is not None and source.bl_idname == "ShaderNodeBrightContrast"
             and source.name == "lux_" + bright.name, "a link change rebuilds the Cycles nodes")

signature = material.luxcore.eevee_nodes_signature
names = sorted(node.name for node in material.node_tree.nodes)
status = eevee_nodes.generate(material)
checks.check(status == "unchanged" and material.luxcore.eevee_nodes_signature == signature
             and sorted(node.name for node in material.node_tree.nodes) == names,
             f"generating again without a change changes nothing ({status})")

# ------------------------------------------------------------------------------
# 4. Protection of hand-made Cycles nodes
# ------------------------------------------------------------------------------

diffuse.inputs["Roughness"].default_value = 0.9  # edited by hand
LuxCoreErrorLog.clear()
bright.inputs["Brightness"].default_value = 0.2
eevee_sync.mark_tree(tree)
eevee_sync.flush()
checks.check(close(value(surface_node(material), "Roughness"), 0.9) and not material.luxcore.sync_eevee_nodes
             and any(material.name in w.message for w in LuxCoreErrorLog.warnings),
             "hand-edited Cycles nodes are kept, the sync switches off with a warning")

if hasattr(bpy.types, "LUXCORE_OT_material_update_eevee_nodes"):
    with bpy.context.temp_override(material=material):
        result = bpy.ops.luxcore.material_update_eevee_nodes()
    checks.check(result == {"FINISHED"} and eevee_nodes.is_ours(material)
                 and close(value(surface_node(material), "Roughness"), 0),
                 f"Update EEVEE Nodes overwrites them ({result})")
else:
    checks.check(False, "the operator luxcore.material_update_eevee_nodes is registered")

# ------------------------------------------------------------------------------
# 5. Default tree detection
# ------------------------------------------------------------------------------

fresh = bpy.data.materials.new("fresh")
eevee_nodes.ensure_cycles_tree(fresh)
checks.check(fresh.node_tree is not None and eevee_nodes.is_default_tree(fresh.node_tree),
             "a new material has a default Cycles tree")
fresh.node_tree.nodes.new("ShaderNodeTexNoise")
checks.check(not eevee_nodes.is_default_tree(fresh.node_tree), "an added node makes it hand-made")
fresh.luxcore.node_tree = bpy.data.node_groups.new("fresh", "luxcore_material_nodes")
checks.check(eevee_sync.enable(fresh) == "skipped_handmade" and not fresh.luxcore.sync_eevee_nodes,
             "enable() leaves hand-made Cycles nodes alone")

# ------------------------------------------------------------------------------
# 6. The Cycles conversion operator keeps the original nodes until the first edit
# ------------------------------------------------------------------------------

if hasattr(bpy.types, "LUXCORE_OT_material_convert_cycles"):
    converted = bpy.data.materials.new("converted")
    eevee_nodes.ensure_cycles_tree(converted)
    principled = next(n for n in converted.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    principled.inputs["Base Color"].default_value = (0.2, 0.7, 0.3, 1)
    converted.luxcore.use_cycles_nodes = True
    original_names = sorted(node.name for node in converted.node_tree.nodes)
    with bpy.context.temp_override(material=converted):
        result = bpy.ops.luxcore.material_convert_cycles()
    backup = bpy.data.materials.get(eevee_sync.backup_name(converted))
    checks.check(result == {"FINISHED"} and converted.luxcore.sync_eevee_nodes
                 and sorted(node.name for node in converted.node_tree.nodes) == original_names
                 and eevee_nodes.is_ours(converted) and backup is not None,
                 f"conversion: sync on, original Cycles nodes kept, hidden backup ({result})")

    root = get_linked_node(get_active_output(converted.luxcore.node_tree).inputs["Material"])
    root.inputs[0].default_value = (0.7, 0.2, 0.3)
    eevee_sync.mark_tree(converted.luxcore.node_tree)
    eevee_sync.flush()
    checks.check(any(node.name.startswith("lux_") for node in converted.node_tree.nodes)
                 and backup is not None
                 and sorted(node.name for node in backup.node_tree.nodes) == original_names,
                 "the first LuxCore edit replaces the Cycles nodes, the backup keeps the original")
else:
    print("SKIP the Cycles conversion operator is not registered")

print("images in", OUT)
checks.finish()
