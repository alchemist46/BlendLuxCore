"""Convert Cycles Nodes to LuxCore: the converted node tree renders like the Cycles node reader.

A sphere in front of a gray backdrop, lit by an area light, seen by an
orthographic camera. Each Cycles material is rendered by LuxCore twice: through
the Cycles node reader (use_cycles_nodes on), then through the LuxCore node
tree converted from it. Both renders use the same seed, so they differ only by
what the two paths export: the mean color over the sphere's disk per channel
and the mean absolute pixel error must agree. For materials without images the
material properties of both paths are also compared.

blender -b --factory-startup --python tests/test_cycles_convert.py -- <site-packages> [disney]

With "disney" (test_cycles_convert_disney.py), both paths read the Principled
BSDF as before pyluxcore 3.0 (Disney, Glass or a mix of both), as with the
pyluxcore version the addon installs.
"""

import math
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

ARGS = harness.setup()

import bpy
import numpy as np
import pyluxcore
from blendluxcore.export import cycles_node_reader
from blendluxcore.operators.cycles_convert import assign_converted_tree, candidates
from blendluxcore.operators.cycles_converter import convert_material, PLACEHOLDER_LABEL
from blendluxcore.utils.errorlog import LuxCoreErrorLog
from blendluxcore.utils import luxutils
from blendluxcore.utils.luxutils import has_material_type
from blendluxcore.utils.node import get_active_output

if "disney" in ARGS:
    # Both paths ask has_material_type(), which caches its answer here
    luxutils._known_material_types["openpbr"] = False
checks = harness.Checks("test_cycles_convert" + ("_disney" if "disney" in ARGS else ""))
OUT = tempfile.mkdtemp(prefix="blc-convert-")
R = 64
ORTHO = 3.0
SAMPLES = 32
HAS_OPENPBR = has_material_type("openpbr")
PRINCIPLED_ROOT = "LuxCoreNodeMatOpenPBR" if HAS_OPENPBR else "LuxCoreNodeMatDisney"
print("pyluxcore", pyluxcore.Version(), "openpbr" if HAS_OPENPBR else "no openpbr")

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

y, x = np.mgrid[0:R, 0:R]
DISK = np.hypot((x + 0.5) / R - 0.5, (y + 0.5) / R - 0.5) * ORTHO < 0.9


def save_image(name, colors, non_color=False):
    """A 16x16 image of 4x4 cells in two colors, saved as PNG so both paths read the same file"""
    image = bpy.data.images.new(name, 16, 16)
    cells = ((np.arange(16)[:, None] // 4 + np.arange(16)[None, :] // 4) % 2).astype(bool)
    pixels = np.empty((16, 16, 4), dtype=np.float32)
    pixels[~cells] = (*colors[0], 1)
    pixels[cells] = (*colors[1], 1)
    image.pixels[:] = pixels.ravel()
    image.filepath_raw = os.path.join(OUT, name + ".png")
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    image = bpy.data.images.load(os.path.join(OUT, name + ".png"))
    if non_color:
        image.colorspace_settings.name = "Non-Color"
    return image


# Bright colors: the Imagemap node decodes sRGB with gamma 2.2, the reader with the sRGB curve
CHECKER = save_image("checker", ((0.9, 0.5, 0.4), (0.4, 0.6, 0.9)))
NORMALS = save_image("normals", ((0.5, 0.5, 1.0), (0.65, 0.4, 0.9)), non_color=True)


def set_inputs(node, inputs):
    for key, value in inputs.items():
        socket = node.inputs[key]
        if isinstance(value, tuple) and len(socket.default_value) == 4:
            value = (*value, 1)
        socket.default_value = value


def cycles_material(name, build):
    """A Cycles material; build(tree, output) adds and links the nodes"""
    material = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    build(tree, output)
    material.luxcore.use_cycles_nodes = True
    return material


def shader(idname, inputs):
    def build(tree, output):
        node = tree.nodes.new(idname)
        set_inputs(node, inputs)
        tree.links.new(node.outputs[0], output.inputs["Surface"])
    return build


backdrop.data.materials.append(cycles_material("backdrop", shader("ShaderNodeBsdfDiffuse",
                                                                  {"Color": (0.5, 0.5, 0.5)})))


def compare(a, b):
    """Mean ratio per channel over the disk, mean absolute pixel error in the disk relative to the mean"""
    a, b = a[..., :3], b[..., :3]
    ratio = b[DISK].mean(axis=0) / np.maximum(a[DISK].mean(axis=0), 1e-6)
    error = np.abs(b - a)[DISK].mean() / max(a[DISK].mean(), 1e-6)
    return ratio, error


def numbers(props, key):
    try:
        return [float(v) for v in props.Get(key).GetValuesString().split()]
    except ValueError:
        return None


def compare_props(name, material, tree):
    """The material properties both paths define, where both are numbers"""
    _, reader = cycles_node_reader.convert(material, pyluxcore.Properties(), "m")
    native = pyluxcore.Properties()
    exporter = SimpleNamespace(node_cache={}, scene=scene, lightgroup_cache=set())
    get_active_output(tree).export(exporter, None, native, "m")
    prefix = "scene.materials.m."
    mismatches = []
    for key in reader.GetAllNames(prefix):
        if not native.IsDefined(key) or key.startswith(prefix + "emission"):
            continue
        if key == prefix + "type":
            if reader.Get(key).GetString() != native.Get(key).GetString():
                mismatches.append((key, reader.Get(key).GetString(), native.Get(key).GetString()))
            continue
        a, b = numbers(reader, key), numbers(native, key)
        if a is None or b is None or len(a) != len(b):
            continue
        if any(abs(u - v) > 1e-4 for u, v in zip(a, b)):
            mismatches.append((key, a, b))
    checks.check(not mismatches, f"{name}: same material properties as the reader {mismatches}")


def root_node(tree):
    return get_active_output(tree).inputs["Material"].links[0].from_node


def run_case(name, build, root_type, mean_tol=0.02, error_tol=0.05, compare_renders=True,
             allow_warnings=False, props=False):
    material = cycles_material(name, build)
    sphere.data.materials.clear()
    sphere.data.materials.append(material)

    LuxCoreErrorLog.clear()
    reader = harness.render(scene, "LUXCORE", os.path.join(OUT, name + "_reader.exr"), SAMPLES)
    reader_errors = [e.message for e in LuxCoreErrorLog.errors]

    tree, result = convert_material(material)
    assign_converted_tree(material, tree)
    root = root_node(tree)
    checks.check(material.luxcore.node_tree == tree and not material.luxcore.use_cycles_nodes
                 and root.bl_idname == root_type,
                 f"{name}: converted, root {root.bl_idname} (expected {root_type}), {result.nodes} nodes")
    checks.check(bool(result.warnings) == allow_warnings, f"{name}: warnings {result.warnings}")
    if props:
        compare_props(name, material, tree)

    LuxCoreErrorLog.clear()
    native = harness.render(scene, "LUXCORE", os.path.join(OUT, name + "_native.exr"), SAMPLES)
    checks.check(not reader_errors and not LuxCoreErrorLog.errors,
                 f"{name}: no export errors {reader_errors} {[e.message for e in LuxCoreErrorLog.errors]}")
    ratio, error = compare(reader, native)
    if not compare_renders:
        print(f"INFO {name}: native/reader mean {np.round(ratio, 3)}, pixel error {error:.3f} (not checked)")
    else:
        checks.check(bool(np.all(np.abs(ratio - 1) < mean_tol)) and error < error_tol,
                     f"{name}: native/reader mean {np.round(ratio, 3)}, pixel error {error:.3f} "
                     f"(tolerance {mean_tol}, {error_tol})")
    return material, tree, root, result, native


def principled(inputs):
    return shader("ShaderNodeBsdfPrincipled", inputs)


material_dielectric, *_ = run_case("principled_dielectric", principled({"Base Color": (0.8, 0.3, 0.1),
                                                                        "Roughness": 0.4}),
                                   PRINCIPLED_ROOT, props=True)
run_case("principled_metal", principled({"Base Color": (0.9, 0.6, 0.3), "Metallic": 1.0, "Roughness": 0.3}),
         PRINCIPLED_ROOT, props=True)
run_case("principled_glass", principled({"Base Color": (1.0, 1.0, 1.0), "Roughness": 0.1,
                                         "Transmission Weight": 1.0}),
         PRINCIPLED_ROOT if HAS_OPENPBR else "LuxCoreNodeMatGlass", 0.03, 0.10, props=True)
run_case("principled_coat_sheen", principled({"Base Color": (0.2, 0.4, 0.1), "Roughness": 0.5,
                                              "Coat Weight": 1.0, "Coat Roughness": 0.1,
                                              "Coat Tint": (0.6, 0.8, 1.0), "Sheen Weight": 1.0,
                                              "Sheen Tint": (1.0, 0.8, 0.8)}),
         # Same properties, but the Disney clearcoat and sheen are noisy at 32 samples (pixel error 0.1)
         PRINCIPLED_ROOT, 0.03, 0.15, props=True)
if not HAS_OPENPBR:
    run_case("principled_half_glass", principled({"Base Color": (0.8, 0.8, 0.8), "Roughness": 0.2,
                                                  "Transmission Weight": 0.5}),
             "LuxCoreNodeMatMix", 0.03, 0.10, props=True)


def build_image_mapping(tree, output):
    node = tree.nodes.new("ShaderNodeBsdfPrincipled")
    coordinates = tree.nodes.new("ShaderNodeTexCoord")
    mapping = tree.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (2, 3, 1)
    mapping.inputs["Rotation"].default_value = (0, 0, math.radians(30))
    mapping.inputs["Location"].default_value = (0.1, 0.2, 0)
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = CHECKER
    value = tree.nodes.new("ShaderNodeValue")
    value.outputs[0].default_value = 0.8
    multiply = tree.nodes.new("ShaderNodeMath")
    multiply.operation = "MULTIPLY"
    multiply.inputs[1].default_value = 0.5
    tree.links.new(coordinates.outputs["UV"], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs[0], texture.inputs["Vector"])
    tree.links.new(texture.outputs["Color"], node.inputs["Base Color"])
    tree.links.new(value.outputs[0], multiply.inputs[0])
    tree.links.new(multiply.outputs[0], node.inputs["Roughness"])
    tree.links.new(node.outputs[0], output.inputs["Surface"])


_, tree, *_ = run_case("principled_image_mapping", build_image_mapping, PRINCIPLED_ROOT, 0.03, 0.10)
idnames = {node.bl_idname for node in tree.nodes}
checks.check({"LuxCoreNodeTexImagemap", "LuxCoreNodeTexMapping2D", "LuxCoreNodeTexMath"} <= idnames,
             f"principled_image_mapping: Imagemap, 2D Mapping and Math nodes ({sorted(idnames)})")

_, tree, root, *_ = run_case("principled_emission", principled({"Base Color": (0.5, 0.5, 0.5),
                                                                "Emission Color": (1.0, 0.5, 0.2),
                                                                "Emission Strength": 2.0}),
                             PRINCIPLED_ROOT)
emission_input = root.inputs[root.inputs.find("Emission")]
checks.check(emission_input.is_linked and emission_input.links[0].from_node.bl_idname == "LuxCoreNodeMatEmission",
             "principled_emission: the Emission input has an Emission node")


def build_mix(tree, output):
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = CHECKER
    separate = tree.nodes.new("ShaderNodeSeparateColor")
    ramp = tree.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.45
    ramp.color_ramp.elements[1].position = 0.7
    mix = tree.nodes.new("ShaderNodeMixShader")
    diffuse = tree.nodes.new("ShaderNodeBsdfDiffuse")
    set_inputs(diffuse, {"Color": (0.8, 0.3, 0.1)})
    glossy = tree.nodes.new("ShaderNodeBsdfGlossy")
    set_inputs(glossy, {"Color": (0.9, 0.9, 0.9), "Roughness": 0.2})
    tree.links.new(texture.outputs["Color"], separate.inputs["Color"])
    tree.links.new(separate.outputs["Red"], ramp.inputs["Fac"])
    tree.links.new(ramp.outputs["Color"], mix.inputs["Fac"])
    tree.links.new(diffuse.outputs[0], mix.inputs[1])
    tree.links.new(glossy.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], output.inputs["Surface"])


_, tree, root, *_ = run_case("mix_diffuse_glossy", build_mix, "LuxCoreNodeMatMix", 0.03, 0.10)
idnames = {node.bl_idname for node in tree.nodes}
checks.check({"LuxCoreNodeTexBand", "LuxCoreNodeTexSplitFloat3", "LuxCoreNodeMatMatte",
              "LuxCoreNodeMatMetal"} <= idnames,
             f"mix_diffuse_glossy: Band, Split RGB, Matte and Metal nodes ({sorted(idnames)})")


def build_mix_rgb(tree, output):
    node = tree.nodes.new("ShaderNodeBsdfDiffuse")
    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    for socket in mix.inputs:
        if socket.identifier == "Factor_Float":
            socket.default_value = 0.7
        elif socket.identifier == "A_Color":
            socket.default_value = (0.9, 0.6, 0.4, 1)
        elif socket.identifier == "B_Color":
            socket.default_value = (0.5, 0.9, 0.7, 1)
    result = next(socket for socket in mix.outputs if socket.identifier == "Result_Color")
    tree.links.new(result, node.inputs["Color"])
    tree.links.new(node.outputs[0], output.inputs["Surface"])


# The Mix node has a Float, Vector and Color socket of each name: the Color ones must be read
run_case("mix_rgb_multiply", build_mix_rgb, "LuxCoreNodeMatMatte")
material_mix = bpy.data.materials["mix_rgb_multiply"]
kd = root_node(material_mix.luxcore.node_tree).inputs["Diffuse Color"]
color_mix = kd.links[0].from_node if kd.is_linked else None
checks.check(color_mix is not None and color_mix.bl_idname == "LuxCoreNodeTexColorMix",
             f"mix_rgb_multiply: Color Math nodes {color_mix and color_mix.bl_idname}")

run_case("emission_shader", shader("ShaderNodeEmission", {"Color": (1.0, 0.6, 0.2), "Strength": 3.0}),
         "LuxCoreNodeMatMatte", props=True)
run_case("transparent", shader("ShaderNodeBsdfTransparent", {"Color": (1.0, 1.0, 1.0)}),
         "LuxCoreNodeMatNull", props=True)


def build_group(tree, output):
    group = bpy.data.node_groups.new("diffuse_group", "ShaderNodeTree")
    group.interface.new_socket(name="Color", in_out="INPUT", socket_type="NodeSocketColor")
    group.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    group_input = group.nodes.new("NodeGroupInput")
    group_output = group.nodes.new("NodeGroupOutput")
    diffuse = group.nodes.new("ShaderNodeBsdfDiffuse")
    group.links.new(group_input.outputs["Color"], diffuse.inputs["Color"])
    group.links.new(diffuse.outputs[0], group_output.inputs["Shader"])
    instance = tree.nodes.new("ShaderNodeGroup")
    instance.node_tree = group
    instance.inputs["Color"].default_value = (0.2, 0.6, 0.3, 1)
    tree.links.new(instance.outputs["Shader"], output.inputs["Surface"])


_, tree, root, *_ = run_case("node_group", build_group, "LuxCoreNodeMatMatte", props=True)
color = list(root.inputs["Diffuse Color"].default_value)
checks.check(all(abs(a - b) < 1e-4 for a, b in zip(color, (0.2, 0.6, 0.3))),
             f"node_group: the Matte has the group input color {np.round(color, 3)}")


def build_normal_map(tree, output):
    node = tree.nodes.new("ShaderNodeBsdfPrincipled")
    set_inputs(node, {"Base Color": (0.7, 0.7, 0.7), "Roughness": 0.5})
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = NORMALS
    normal_map = tree.nodes.new("ShaderNodeNormalMap")
    tree.links.new(texture.outputs["Color"], normal_map.inputs["Color"])
    tree.links.new(normal_map.outputs[0], node.inputs["Normal"])
    tree.links.new(node.outputs[0], output.inputs["Surface"])


_, tree, root, *_ = run_case("normal_map", build_normal_map, PRINCIPLED_ROOT, 0.03, 0.10)
bump = root.inputs[root.inputs.find("Bump")]
checks.check(bump.is_linked and bump.links[0].from_node.bl_idname == "LuxCoreNodeTexImagemap"
             and bump.links[0].from_node.is_normal_map,
             "normal_map: the Bump input has an Imagemap node in normal map mode")

# An unsupported node, through the operator: a placeholder and a warning in the error log
material = cycles_material("unsupported_noise", lambda tree, output: (
    tree.links.new(tree.nodes.new("ShaderNodeTexNoise").outputs["Color"],
                   tree.nodes.new("ShaderNodeBsdfPrincipled").inputs["Base Color"]),
    tree.links.new(tree.nodes["Principled BSDF"].outputs[0], output.inputs["Surface"]),
))
LuxCoreErrorLog.clear()
with bpy.context.temp_override(material=material):
    result = bpy.ops.luxcore.material_convert_cycles(sync_eevee=False)
tree = material.luxcore.node_tree
checks.check(result == {"FINISHED"} and tree is not None and not material.luxcore.use_cycles_nodes,
             f"unsupported_noise: the operator converted it ({result})")
checks.check(tree is not None and any(node.label.startswith(PLACEHOLDER_LABEL) for node in tree.nodes),
             "unsupported_noise: a labelled placeholder node")
checks.check(any("Noise" in w.message and w.obj_name == material.name for w in LuxCoreErrorLog.warnings),
             f"unsupported_noise: a warning in the error log {[w.message for w in LuxCoreErrorLog.warnings]}")

# Converting again replaces the tree only with replace_existing, and removes the old tree
first = material_dielectric.luxcore.node_tree
count = len(bpy.data.node_groups)
with bpy.context.temp_override(material=material_dielectric):
    cancelled = bpy.ops.luxcore.material_convert_cycles(sync_eevee=False)
checks.check(cancelled == {"CANCELLED"} and material_dielectric.luxcore.node_tree == first,
             f"convert again: cancelled without Replace Existing Tree ({cancelled})")
with bpy.context.temp_override(material=material_dielectric):
    finished = bpy.ops.luxcore.material_convert_cycles(replace_existing=True, sync_eevee=False)
checks.check(finished == {"FINISHED"} and len(bpy.data.node_groups) == count
             and material_dielectric.luxcore.node_tree.name.startswith(material_dielectric.name),
             f"convert again: replaced, {len(bpy.data.node_groups)} node trees (before {count})")

# The bulk operator: Cycles materials converted, a hand-made tree and a library material left alone
for i in range(3):
    cycles_material(f"bulk_{i}", principled({"Base Color": (0.1 * i, 0.5, 0.5)}))
hand_made = bpy.data.materials.new("hand_made")
if bpy.app.version < (5, 0, 0):
    hand_made.use_nodes = True
hand_tree = bpy.data.node_groups.new("hand_made", "luxcore_material_nodes")
hand_output = hand_tree.nodes.new("LuxCoreNodeMatOutput")
hand_tree.links.new(hand_tree.nodes.new("LuxCoreNodeMatMatte").outputs[0], hand_output.inputs[0])
hand_made.luxcore.node_tree = hand_tree

library_path = os.path.join(OUT, "library.blend")
library_material = cycles_material("library_material", principled({"Base Color": (0.3, 0.3, 0.9)}))
bpy.data.libraries.write(library_path, {library_material})
bpy.data.materials.remove(library_material)
with bpy.data.libraries.load(library_path, link=True) as (data_from, data_to):
    data_to.materials = ["library_material"]
library_material = data_to.materials[0]

# A hidden backup of original Cycles nodes (made by the EEVEE sync) is never converted
backup = cycles_material(".bulk_0.cycles_original", principled({"Base Color": (0.5, 0.5, 0.5)}))
backup["luxcore_cycles_backup"] = True

expected = candidates(False)
checks.check(backup not in expected, "bulk: the hidden backup of Cycles nodes is skipped")
bulk = [bpy.data.materials[f"bulk_{i}"] for i in range(3)] + [bpy.data.materials["backdrop"]]
checks.check(all(m in expected for m in bulk) and hand_made not in expected and library_material not in expected,
             f"bulk: candidates {[m.name for m in expected]}")
# The factory startup file has a grease pencil material
grease_pencil = [m for m in bpy.data.materials if m.is_grease_pencil]
checks.check(not any(m in expected for m in grease_pencil),
             f"bulk: grease pencil materials are skipped {[m.name for m in grease_pencil]}")
result = bpy.ops.luxcore.convert_all_cycles_materials(sync_eevee=False)
converted = [m for m in expected if m.luxcore.node_tree is not None and not m.luxcore.use_cycles_nodes]
checks.check(result == {"FINISHED"} and len(converted) == len(expected),
             f"bulk: converted {len(converted)} of {len(expected)}")
checks.check(hand_made.luxcore.node_tree == hand_tree, "bulk: the hand-made LuxCore tree is kept")
checks.check(library_material.luxcore.node_tree is None, "bulk: the library material is skipped")

print("images in", OUT)
checks.finish()
