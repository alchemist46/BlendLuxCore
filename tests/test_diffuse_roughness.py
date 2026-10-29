"""The roughness of Cycles' Diffuse BSDF and of the LuxCore Matte node.

Cycles renders a rough Diffuse BSDF with the energy-preserving Oren-Nayar
model (EON), as LuxCore's roughmatte (pyluxcore 3.0). A sphere in front of a
gray backdrop, lit from the side by an area light so the rough lobe's flat
look shows, seen by an orthographic camera: the Diffuse BSDF read by the
Cycles node reader must match Cycles in the mean over the sphere's disk per
channel, after aligning LuxCore's image row (a core camera bug), within 2%
at roughness 0.5 and 6% at roughness 1. The models differ by one thing:
Cycles multiplies the whole lobe by the color (the closure weight) and also
puts the albedo squared into the multiple scattering term, which then scales
with albedo^3, where the EON paper and LuxCore have albedo^2. LuxCore is
brighter by up to 5% at roughness 1 (albedo 0.3 at 1024 spp: +4.6%, 0.8:
+3.2%, 0.9: +1.5%), about 1% at roughness 0.5, and equal at albedo 1. The reader
exports roughness 0 as matte and anything else as roughmatte with the
roughness as sigma; "Convert Cycles Nodes to LuxCore" gives the Matte node
that sigma; "Keep EEVEE Nodes in Sync" gives a Matte node's sigma to the
Diffuse BSDF as the roughness.

blender -b --factory-startup --python tests/test_diffuse_roughness.py -- <site-packages>
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
import numpy as np
import pyluxcore
from blendluxcore.export import cycles_node_reader
from blendluxcore.utils import eevee_nodes
from blendluxcore.utils.errorlog import LuxCoreErrorLog
from blendluxcore.utils.luxutils import has_material_type

checks = harness.Checks("test_diffuse_roughness")
OUT = tempfile.mkdtemp(prefix="blc-diffuse-")
R = 64
ORTHO = 3.0
SAMPLES = 128

scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)

bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=64, ring_count=32)
sphere = bpy.context.object
bpy.ops.object.shade_smooth()
bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 2, 0), rotation=(1.5708, 0, 0))
backdrop = bpy.context.object

light_data = bpy.data.lights.new("key", "AREA")
light_data.size = 1
light_data.energy = 600
light_data.luxcore.use_cycles_settings = True
light = bpy.data.objects.new("key", light_data)
scene.collection.objects.link(light)
# From the side and a little behind the sphere: the rough lobe brightens the terminator
light.location = (3, 0.5, 1)
light.rotation_euler = (0, 1.4, 0.15)

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


def diffuse_material(name, color, roughness):
    material = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    node = tree.nodes.new("ShaderNodeBsdfDiffuse")
    node.inputs["Color"].default_value = (*color, 1)
    node.inputs["Roughness"].default_value = roughness
    tree.links.new(node.outputs[0], output.inputs["Surface"])
    material.luxcore.use_cycles_nodes = True
    return material


def read(material):
    props = pyluxcore.Properties()
    name = cycles_node_reader.convert(material, props, "test_obj")[0]
    prefix = f"scene.materials.{name}."
    sigma = props.Get(prefix + "sigma").GetFloat() if props.IsDefined(prefix + "sigma") else None
    return props.Get(prefix + "type").GetString(), sigma


# The export
for roughness, expected in ((0.0, ("matte", None)), (0.5, ("roughmatte", 0.5)), (1.0, ("roughmatte", 1.0))):
    found = read(diffuse_material(f"read_{roughness}", (0.8, 0.8, 0.8), roughness))
    checks.check(found[0] == expected[0] and (found[1] is None) == (expected[1] is None)
                 and (found[1] is None or abs(found[1] - expected[1]) < 1e-6),
                 f"Diffuse roughness {roughness}: exported as {found}")

# The converter: the Roughness becomes the Matte node's Sigma
converted = diffuse_material("convert", (0.8, 0.3, 0.1), 0.6)
with bpy.context.temp_override(material=converted):
    bpy.ops.luxcore.material_convert_cycles(sync_eevee=False)
tree = converted.luxcore.node_tree
matte = [n for n in tree.nodes if n.bl_idname == "LuxCoreNodeMatMatte"] if tree else []
sigma = matte[0].inputs["Sigma"].default_value if matte else None
checks.check(sigma is not None and abs(sigma - 0.6) < 1e-6,
             f"Convert Cycles Nodes: the Diffuse BSDF's Roughness 0.6 is the Matte node's Sigma ({sigma})")

# The EEVEE sync: the Matte node's Sigma is the Diffuse BSDF's Roughness
material = bpy.data.materials.new("eevee_matte")
tree = bpy.data.node_groups.new("eevee_matte", "luxcore_material_nodes")
material.luxcore.node_tree = tree
output = tree.nodes.new("LuxCoreNodeMatOutput")
node = tree.nodes.new("LuxCoreNodeMatMatte")
node.inputs["Sigma"].default_value = 0.6
tree.links.new(node.outputs[0], output.inputs["Material"])
eevee_nodes.generate(material)
diffuse = [n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfDiffuse"]
roughness = diffuse[0].inputs["Roughness"].default_value if len(diffuse) == 1 else None
checks.check(roughness is not None and abs(roughness - 0.6) < 1e-6,
             f"EEVEE sync: the Matte node's Sigma 0.6 is the Diffuse BSDF's Roughness ({roughness})")

if not has_material_type("openpbr"):
    # pyluxcore before 3.0: roughmatte is the older Oren-Nayar lobe
    print("SKIP the renders: pyluxcore before 3.0")
    checks.finish()
    raise SystemExit(0)

backdrop.data.materials.append(diffuse_material("backdrop", (0.5, 0.5, 0.5), 0.0))
y, x = np.mgrid[0:R, 0:R]
DISK = np.hypot((x + 0.5) / R - 0.5, (y + 0.5) / R - 0.5) * ORTHO < 0.9


def render_both(name, color, roughness):
    sphere.data.materials.clear()
    sphere.data.materials.append(diffuse_material(name, color, roughness))
    LuxCoreErrorLog.clear()
    cycles = harness.render(scene, "CYCLES", os.path.join(OUT, name + "_cycles.exr"), SAMPLES)
    luxcore = harness.render(scene, "LUXCORE", os.path.join(OUT, name + "_luxcore.exr"), SAMPLES)
    checks.check(not LuxCoreErrorLog.errors, f"{name}: no export errors")
    return cycles[..., :3], luxcore[..., :3]


def ratio(cycles, luxcore, row_shift):
    luxcore = np.roll(luxcore, row_shift, axis=0)
    return luxcore[DISK].mean(axis=0) / np.maximum(cycles[DISK].mean(axis=0), 1e-6)


def error(cycles, luxcore, row_shift):
    luxcore = np.roll(luxcore, row_shift, axis=0)
    return np.abs(luxcore - cycles)[DISK].mean() / max(cycles[DISK].mean(), 1e-6)


cycles, luxcore = render_both("smooth", (0.8, 0.3, 0.1), 0.0)
row_shift = min((0, 1, -1), key=lambda shift: error(cycles, luxcore, shift))
reference = ratio(cycles, luxcore, row_shift)
checks.check(bool(np.all(np.abs(reference - 1) < 0.02)),
             f"roughness 0: LuxCore/Cycles mean {np.round(reference, 3)} (row shift {row_shift})")
smooth_cycles = cycles[DISK].mean()

for name, color, roughness, tolerance in (("rough 0.5", (0.8, 0.3, 0.1), 0.5, 0.02),
                                          ("rough 1", (0.8, 0.3, 0.1), 1.0, 0.06),
                                          ("rough 1 white", (0.9, 0.9, 0.9), 1.0, 0.03)):
    cycles, luxcore = render_both(name, color, roughness)
    found = ratio(cycles, luxcore, row_shift)
    # The roughness must show in Cycles' image too, or the scene tests nothing
    change = cycles[DISK].mean() / smooth_cycles if color == (0.8, 0.3, 0.1) else None
    checks.check(bool(np.all(np.abs(found - 1) < tolerance)),
                 f"{name}: LuxCore/Cycles mean {np.round(found, 3)} (tolerance {tolerance})"
                 + (f" (Cycles {change:.3f}x roughness 0)" if change is not None else ""))

print("images in", OUT)
checks.finish()
