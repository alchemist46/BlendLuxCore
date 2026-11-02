"""OpenPBR: the coat's own normal and the MaterialX import.

The OpenPBR node's Coat Bump input (coat.bumptex, pyluxcore 3.0 builds since
2026-11-01) is in the Coat group after Coat Darkening, exported only when
linked, and added to an old node; the Principled BSDF's Coat Normal is read
as coat.bumptex (the reader and "Convert Cycles Nodes to LuxCore"), and a
Subsurface IOR away from Cycles' default warns. "Import MaterialX" builds an
OpenPBR node from an open_pbr_surface with values, a tiled image, a normal
map on the coat and emission, and the material renders.

blender -b --factory-startup --python tests/test_openpbr_coat_materialx.py -- <site-packages>
"""

import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
import numpy as np
import pyluxcore
from blendluxcore.utils.errorlog import LuxCoreErrorLog
from blendluxcore.utils import compatibility
from blendluxcore.export import cycles_node_reader

checks = harness.Checks("test_openpbr_coat_materialx")
OUT = tempfile.mkdtemp(prefix="blc-openpbr-coat-")
R = 48

scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)

bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=48, ring_count=24)
sphere = bpy.context.object
bpy.ops.object.shade_smooth()

light_data = bpy.data.lights.new("key", "AREA")
light_data.size = 2
light_data.energy = 400
light = bpy.data.objects.new("key", light_data)
scene.collection.objects.link(light)
light.location = (2, -2, 3)
light.rotation_euler = (0.785, 0, 0.785)

camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
camera.data.type = "ORTHO"
camera.data.ortho_scale = 2.4
scene.collection.objects.link(camera)
camera.location = (0, -5, 0)
camera.rotation_euler = (1.5708, 0, 0)
scene.camera = camera

scene.render.resolution_x = scene.render.resolution_y = R
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = "Standard"


def exported(node):
    props = pyluxcore.Properties()
    exporter = types.SimpleNamespace(node_cache={}, scene=scene, lightgroup_cache=set())
    name = node.sub_export(exporter, None, props, "test")
    return props, "scene.materials." + name + "."


# --- The Coat Bump input -----------------------------------------------------

material = bpy.data.materials.new("coated")
tree = bpy.data.node_groups.new("coated", "luxcore_material_nodes")
material.luxcore.node_tree = tree
output = tree.nodes.new("LuxCoreNodeMatOutput")
pbr = tree.nodes.new("LuxCoreNodeMatOpenPBR")
tree.links.new(pbr.outputs[0], output.inputs[0])
sphere.data.materials.append(material)

names = [s.name for s in pbr.inputs]
coat_bump = pbr.inputs[pbr.inputs.find("Coat Bump")]
checks.check(names[names.index("Coat Darkening") + 1] == "Coat Bump" and not coat_bump.enabled,
             "Coat Bump follows Coat Darkening, disabled with the Coat group off")
pbr.use_coat = True
checks.check(coat_bump.enabled, "switching the Coat group on enables Coat Bump")

props, prefix = exported(pbr)
checks.check(not props.IsDefined(prefix + "coat.bumptex"), "an unlinked Coat Bump is not exported")

noise = tree.nodes.new("LuxCoreNodeTexfBM")
bump = tree.nodes.new("LuxCoreNodeTexBump")
tree.links.new(noise.outputs[0], bump.inputs["Value"])
bump.inputs["Bump Height"].default_value = 0.02
tree.links.new(bump.outputs["Bump"], coat_bump)
props, prefix = exported(pbr)
checks.check(props.IsDefined(prefix + "coat.bumptex") and not props.IsDefined(prefix + "bumptex"),
             "a linked Coat Bump is exported as coat.bumptex, the base keeps no bumptex")

pbr.use_coat = False
props, prefix = exported(pbr)
checks.check(not props.IsDefined(prefix + "coat.bumptex"), "the Coat group off leaves coat.bumptex out")
pbr.use_coat = True

# An old node (no Coat Bump) gets it after Coat Darkening, enabled as its group
old_tree = bpy.data.node_groups.new("old_openpbr", "luxcore_material_nodes")
old = old_tree.nodes.new("LuxCoreNodeMatOpenPBR")
old.use_coat = True
old.inputs.remove(old.inputs[old.inputs.find("Coat Bump")])
compatibility.update_openpbr_add_coat_bump(old_tree)
old_names = [s.name for s in old.inputs]
checks.check(old_names[old_names.index("Coat Darkening") + 1] == "Coat Bump"
             and old.inputs[old.inputs.find("Coat Bump")].enabled, "old node converted")
bpy.data.node_groups.remove(old_tree)

# Rendered with and without the coat bump: the bump changes the image, no errors
images = {}
for linked in (False, True):
    if not linked:
        tree.links.remove(coat_bump.links[0])
    else:
        tree.links.new(bump.outputs["Bump"], coat_bump)
    LuxCoreErrorLog.clear()
    images[linked] = harness.render(scene, "LUXCORE", os.path.join(OUT, f"coatbump{int(linked)}.exr"), 32)
    checks.check(not LuxCoreErrorLog.errors, f"coat bump {linked}: no export errors")
diff = np.abs(images[True][..., :3] - images[False][..., :3]).mean() / images[False][..., :3].mean()
checks.check(diff > 0.01, f"the coat bump changes the image (mean difference {diff:.3f})")

# --- The Principled BSDF's Coat Normal and Subsurface IOR ---------------------

cycles = bpy.data.materials.new("principled")
cycles.use_nodes = True
principled = cycles.node_tree.nodes["Principled BSDF"]
principled.inputs["Coat Weight"].default_value = 1
normal_map = cycles.node_tree.nodes.new("ShaderNodeNormalMap")
checker = cycles.node_tree.nodes.new("ShaderNodeTexChecker")
cycles.node_tree.links.new(checker.outputs["Color"], normal_map.inputs["Color"])
cycles.node_tree.links.new(normal_map.outputs["Normal"], principled.inputs["Coat Normal"])
LuxCoreErrorLog.clear()
_, props = cycles_node_reader.convert(cycles, pyluxcore.Properties(), "m")
checks.check(props.IsDefined("scene.materials.m.coat.bumptex") and not props.IsDefined("scene.materials.m.bumptex"),
             "the reader maps Coat Normal to coat.bumptex, not to bumptex")
checks.check(not LuxCoreErrorLog.warnings, f"no warnings ({[w.message for w in LuxCoreErrorLog.warnings]})")

# Random Walk takes the IOR, as OpenPBR; only Random Walk (Skin) has a
# Subsurface IOR of its own
principled.inputs["Subsurface Weight"].default_value = 1
LuxCoreErrorLog.clear()
cycles_node_reader.convert(cycles, pyluxcore.Properties(), "m")
checks.check(not LuxCoreErrorLog.warnings, "Random Walk subsurface: no warning")
principled.subsurface_method = "RANDOM_WALK_SKIN"
LuxCoreErrorLog.clear()
cycles_node_reader.convert(cycles, pyluxcore.Properties(), "m")
checks.check(not LuxCoreErrorLog.warnings, "Random Walk (Skin), Subsurface IOR at Cycles' 1.4: no warning")
next(s for s in principled.inputs if s.name == "Subsurface IOR").default_value = 1.33
LuxCoreErrorLog.clear()
cycles_node_reader.convert(cycles, pyluxcore.Properties(), "m")
checks.check(any("Subsurface IOR" in w.message for w in LuxCoreErrorLog.warnings),
             "Random Walk (Skin), Subsurface IOR 1.33 warns")

# "Convert Cycles Nodes to LuxCore" links the converted normal map to Coat Bump
sphere.data.materials.clear()
sphere.data.materials.append(cycles)
sphere.active_material_index = 0
bpy.context.view_layer.objects.active = sphere
with bpy.context.temp_override(object=sphere, material=cycles):
    bpy.ops.luxcore.material_convert_cycles()
converted = next(n for n in cycles.luxcore.node_tree.nodes if n.bl_idname == "LuxCoreNodeMatOpenPBR")
checks.check(converted.use_coat and converted.inputs[converted.inputs.find("Coat Bump")].is_linked,
             "the converter links Coat Normal to Coat Bump")

# --- MaterialX import ---------------------------------------------------------

image = bpy.data.images.new("mtlx_grad", 16, 16)
pixels = np.zeros((16, 16, 4), dtype=np.float32)
pixels[..., 0] = np.linspace(0, 1, 16)[None, :]
pixels[..., 1] = 0.5
pixels[..., 2] = np.linspace(0, 1, 16)[:, None]
pixels[..., 3] = 1
image.pixels = pixels.ravel()
image.filepath_raw = os.path.join(OUT, "grad.png")
image.file_format = "PNG"
image.save()
normal_image = bpy.data.images.new("mtlx_normal", 16, 16)
normal_pixels = np.zeros((16, 16, 4), dtype=np.float32)
normal_pixels[..., 0] = 0.5 + 0.3 * np.sin(np.linspace(0, 6.28, 16))[None, :]
normal_pixels[..., 1] = 0.5
normal_pixels[..., 2] = 1
normal_pixels[..., 3] = 1
normal_image.pixels = normal_pixels.ravel()
normal_image.filepath_raw = os.path.join(OUT, "normal.png")
normal_image.file_format = "PNG"
normal_image.save()

MTLX = """<?xml version="1.0"?>
<materialx version="1.39">
  <nodegraph name="NG_paint">
    <tiledimage name="base_tex" type="color3">
      <input name="file" type="filename" value="grad.png" colorspace="srgb_texture" />
      <input name="uvtiling" type="vector2" value="2, 3" />
    </tiledimage>
    <image name="peel_tex" type="vector3">
      <input name="file" type="filename" value="normal.png" />
    </image>
    <normalmap name="peel" type="vector3">
      <input name="in" type="vector3" nodename="peel_tex" />
      <input name="scale" type="float" value="0.5" />
    </normalmap>
    <multiply name="unsupported" type="float">
      <input name="in1" type="float" value="0.5" />
      <input name="in2" type="float" value="0.5" />
    </multiply>
    <output name="base_color_out" type="color3" nodename="base_tex" />
    <output name="coat_normal_out" type="vector3" nodename="peel" />
    <output name="rough_out" type="float" nodename="unsupported" />
  </nodegraph>
  <open_pbr_surface name="SR_paint" type="surfaceshader">
    <input name="base_color" type="color3" nodegraph="NG_paint" output="base_color_out" />
    <input name="base_metalness" type="float" value="0.25" />
    <input name="specular_roughness" type="float" nodegraph="NG_paint" output="rough_out" />
    <input name="coat_weight" type="float" value="1" />
    <input name="coat_roughness" type="float" value="0.1" />
    <input name="coat_ior" type="float" value="1.5" />
    <input name="geometry_coat_normal" type="vector3" nodegraph="NG_paint" output="coat_normal_out" />
    <input name="thin_film_weight" type="float" value="1" />
    <input name="thin_film_thickness" type="float" value="0.3" />
    <input name="emission_luminance" type="float" value="2" />
    <input name="emission_color" type="color3" value="1, 0.5, 0.25" />
  </open_pbr_surface>
  <surfacematerial name="Paint" type="material">
    <input name="surfaceshader" type="surfaceshader" nodename="SR_paint" />
  </surfacematerial>
  <standard_surface name="SR_other" type="surfaceshader" />
  <surfacematerial name="Other" type="material">
    <input name="surfaceshader" type="surfaceshader" nodename="SR_other" />
  </surfacematerial>
</materialx>
"""
mtlx_path = os.path.join(OUT, "paint.mtlx")
with open(mtlx_path, "w", encoding="utf-8") as f:
    f.write(MTLX)

bpy.context.view_layer.objects.active = sphere
result = bpy.ops.luxcore.import_materialx(filepath=mtlx_path, assign=True)
checks.check(result == {"FINISHED"}, f"Import MaterialX finished ({result})")
paint = bpy.data.materials.get("Paint")
checks.check(paint is not None and "Other" not in bpy.data.materials,
             "the open_pbr_surface material is imported, the standard_surface one is not")
if paint is not None:
    node = next(n for n in paint.luxcore.node_tree.nodes if n.bl_idname == "LuxCoreNodeMatOpenPBR")

    def socket(name):
        return node.inputs[node.inputs.find(name)]

    checks.check(abs(socket("Metalness").default_value - 0.25) < 1e-6
                 and abs(socket("Coat IOR").default_value - 1.5) < 1e-6
                 and abs(socket("Thin Film Thickness (nm)").default_value - 300) < 1e-3,
                 "values: metalness, coat IOR, thin film thickness in nanometres")
    checks.check(node.use_coat and node.use_thin_film and not node.use_transmission, "groups switched on")
    base = socket("Base Color").links[0].from_node if socket("Base Color").is_linked else None
    checks.check(base is not None and base.bl_idname == "LuxCoreNodeTexImagemap" and abs(base.gamma - 2.2) < 1e-6
                 and base.inputs["2D Mapping"].is_linked
                 and abs(base.inputs["2D Mapping"].links[0].from_node.vscale - 3) < 1e-6,
                 "tiledimage: an sRGB image with a 2x3 mapping")
    coat = socket("Coat Bump").links[0].from_node if socket("Coat Bump").is_linked else None
    checks.check(coat is not None and coat.is_normal_map and abs(coat.normal_map_scale - 0.5) < 1e-6,
                 "normalmap on the coat: a normal-map image of scale 0.5")
    checks.check(not socket("Specular Roughness").is_linked
                 and abs(socket("Specular Roughness").default_value - 0.3) < 1e-6,
                 "an unsupported node keeps the default value")
    checks.check(socket("Emission").is_linked, "emission linked")
    checks.check(sphere.active_material == paint, "assigned to the active object")

    props, prefix = exported(node)
    checks.check(props.IsDefined(prefix + "coat.bumptex") and props.IsDefined(prefix + "base.color"),
                 "the imported material exports coat.bumptex and a textured base.color")

    LuxCoreErrorLog.clear()
    harness.render(scene, "LUXCORE", os.path.join(OUT, "materialx.exr"), 16)
    checks.check(not LuxCoreErrorLog.errors, "the imported material renders without export errors")

print("images in", OUT)
checks.finish()
