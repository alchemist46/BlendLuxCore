"""OpenPBR: the Principled BSDF read as an OpenPBR material, and the OpenPBR node.

A sphere in front of a gray backdrop, lit by an area light, seen by an
orthographic camera. For each parameter set the Principled BSDF, read by the
Cycles node reader (an "openpbr" material), must look like Cycles: the mean
color over the sphere's disk per channel, and the mean absolute error of the
pixels relative to the mean color. Cycles' glossy blur and indirect clamping
are off. The OpenPBR node must export only what differs from the defaults,
and render.

LuxCore's image can be one row off Cycles' (a core camera bug, found
2026-10-17): a diffuse sphere first finds the row shift that aligns the two,
and must match within 2% once aligned.

blender -b --factory-startup --python tests/test_openpbr_principled.py -- <site-packages>
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
from blendluxcore.utils.errorlog import LuxCoreErrorLog
from blendluxcore.utils.luxutils import has_material_type

checks = harness.Checks("test_openpbr_principled")
if not has_material_type("openpbr"):
    # pyluxcore before 3.0: the Principled BSDF is read as before
    print("SKIP pyluxcore has no openpbr material")
    checks.finish()
    raise SystemExit(0)
OUT = tempfile.mkdtemp(prefix="blc-openpbr-")
R = 64
ORTHO = 3.0
SAMPLES = 64

# Name, Principled inputs, tolerance of the mean (per channel), tolerance of
# the pixel error. Set from the first run (2026-10-19, 64 spp) with margin
# for the noise. Known differences, where OpenPBR is the physical model and
# Cycles leaves something out:
# - the coat darkens the base by its internal reflections (Cycles doesn't):
#   the coated base is white (no darkening) and has no specular (Specular IOR
#   Level 0), which Cycles keeps at its IOR against air under the coat where
#   OpenPBR takes the IOR relative to the coat (with Coat IOR = IOR it
#   vanishes);
# - subsurface: LuxCore is 2-4% brighter without the specular layer (the
#   Subsurface node too), up to 12% in the short-radius channels with it:
#   LuxCore's walk is reflected back at the boundary (the Fresnel of the
#   specular IOR) and its color stays the albedo of the medium with that
#   boundary (the OpenPBR definition); Cycles' walk enters by refraction and
#   leaves at its first hit of the surface;
# - anisotropic: 3% brighter;
# - thin film: another interference model, not compared.
CASES = (
    ("dielectric", {"Base Color": (0.8, 0.3, 0.1), "Roughness": 0.4}, 0.02, 0.03),
    ("metal", {"Base Color": (0.9, 0.6, 0.3), "Metallic": 1.0, "Roughness": 0.3}, 0.02, 0.08),
    ("anisotropic", {"Base Color": (0.9, 0.9, 0.9), "Metallic": 1.0, "Roughness": 0.4,
                     "Anisotropic": 0.8, "Anisotropic Rotation": 0.1}, 0.05, 0.35),
    ("coat", {"Base Color": (1.0, 1.0, 1.0), "Roughness": 0.5, "Specular IOR Level": 0.0,
              "Coat Weight": 1.0, "Coat Roughness": 0.1, "Coat Tint": (0.6, 0.8, 1.0)}, 0.03, 0.10),
    ("sheen", {"Base Color": (0.2, 0.4, 0.1), "Roughness": 0.6, "Sheen Weight": 1.0,
               "Sheen Tint": (1.0, 0.8, 0.8)}, 0.02, 0.03),
    ("subsurface", {"Base Color": (0.8, 0.5, 0.4), "Roughness": 0.4, "Subsurface Weight": 1.0,
                    "Subsurface Radius": (1.0, 0.2, 0.1), "Subsurface Scale": 0.2}, 0.13, 0.30),
    ("glass", {"Base Color": (1.0, 1.0, 1.0), "Roughness": 0.1, "Transmission Weight": 1.0}, 0.03, 0.15),
    # Smooth: delta lobes (SPECULAR events)
    ("smooth glass", {"Base Color": (1.0, 1.0, 1.0), "Roughness": 0.0, "Transmission Weight": 1.0}, 0.03, 0.15),
)

scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)
scene.cycles.blur_glossy = 0
scene.cycles.sample_clamp_indirect = 0

bpy.ops.mesh.primitive_uv_sphere_add(radius=1, segments=64, ring_count=32)
sphere = bpy.context.object
bpy.ops.object.shade_smooth()

# The backdrop: what the glass refracts and the reflections show
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


def cycles_material(name, shader, inputs):
    material = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    node = tree.nodes.new(shader)
    for key, value in inputs.items():
        socket = node.inputs[key]
        if isinstance(value, tuple) and len(socket.default_value) == 4:
            value = (*value, 1)
        socket.default_value = value
    tree.links.new(node.outputs[0], output.inputs["Surface"])
    material.luxcore.use_cycles_nodes = True
    return material


backdrop.data.materials.append(cycles_material("backdrop", "ShaderNodeBsdfDiffuse",
                                               {"Color": (0.5, 0.5, 0.5)}))

y, x = np.mgrid[0:R, 0:R]
DISK = np.hypot((x + 0.5) / R - 0.5, (y + 0.5) / R - 0.5) * ORTHO < 0.9


def compare(cycles, luxcore, row_shift):
    """Mean ratio per channel over the disk, mean absolute pixel error in the disk relative to the mean"""
    c = cycles[..., :3]
    l = np.roll(luxcore[..., :3], row_shift, axis=0)
    ratio = l[DISK].mean(axis=0) / np.maximum(c[DISK].mean(axis=0), 1e-6)
    error = np.abs(l - c)[DISK].mean() / max(c[DISK].mean(), 1e-6)
    return ratio, error


def render_both(name, shader, inputs):
    sphere.data.materials.clear()
    sphere.data.materials.append(cycles_material(name, shader, inputs))
    LuxCoreErrorLog.clear()
    cycles = harness.render(scene, "CYCLES", os.path.join(OUT, name + "_cycles.exr"), SAMPLES)
    luxcore = harness.render(scene, "LUXCORE", os.path.join(OUT, name + "_luxcore.exr"), SAMPLES)
    checks.check(not LuxCoreErrorLog.errors, f"{name}: no export errors")
    return cycles, luxcore


# The reference: Cycles' Diffuse BSDF (a LuxCore matte material)
cycles, luxcore = render_both("diffuse", "ShaderNodeBsdfDiffuse", {"Color": (0.8, 0.3, 0.1)})
row_shift = min((0, 1, -1), key=lambda shift: compare(cycles, luxcore, shift)[1])
ratio, error = compare(cycles, luxcore, row_shift)
checks.check(bool(np.all(np.abs(ratio - 1) < 0.02)) and error < 0.03,
             f"diffuse reference: mean {np.round(ratio, 3)}, error {error:.3f} (LuxCore row shift {row_shift})")

for name, inputs, mean_tol, error_tol in CASES:
    cycles, luxcore = render_both(name, "ShaderNodeBsdfPrincipled", inputs)
    ratio, error = compare(cycles, luxcore, row_shift)
    checks.check(bool(np.all(np.abs(ratio - 1) < mean_tol)),
                 f"{name}: LuxCore/Cycles mean {np.round(ratio, 3)} (tolerance {mean_tol})")
    checks.check(error < error_tol, f"{name}: pixel error {error:.3f} (tolerance {error_tol})")

# The OpenPBR node: only the non-default inputs of the enabled groups
material = bpy.data.materials.new("luxcore_openpbr")
tree = bpy.data.node_groups.new("luxcore_openpbr", "luxcore_material_nodes")
material.luxcore.node_tree = tree
output = tree.nodes.new("LuxCoreNodeMatOutput")
node = tree.nodes.new("LuxCoreNodeMatOpenPBR")
node.inputs["Base Color"].default_value = (0.8, 0.3, 0.1)
node.use_coat = True
node.inputs[node.inputs.find("Fuzz Weight")].default_value = 0.7  # in a disabled group
tree.links.new(node.outputs[0], output.inputs[0])

# Dispersion from the IOR Preset node (diamond: 2.417, Abbe 55.3)
exporter = type("Exporter", (), {"node_cache": {}, "scene": scene})()
node.use_transmission = True
node.inputs[node.inputs.find("Dispersion Scale")].default_value = 1
preset = tree.nodes.new("LuxCoreNodeTexIORPreset")
tree.links.new(preset.outputs["IOR"], node.inputs[node.inputs.find("Specular IOR")])
tree.links.new(preset.outputs["Abbe Number"], node.inputs[node.inputs.find("Abbe Number")])
exported = pyluxcore.Properties()
node.export(exporter, None, exported, "d")
abbe_tex = exported.Get("scene.materials.d.transmission.dispersionabbenumber").GetString()
abbe = exported.Get(f"scene.textures.{abbe_tex}.value").GetFloat()
checks.check(exported.Get("scene.materials.d.transmission.dispersionscale").GetFloat() == 1 and
             abs(abbe - preset.abbe_number) < 1e-3 and preset.abbe_number > 0,
             f"OpenPBR node exports the dispersion (Abbe {abbe:.1f} from the IOR Preset node)")
for link in list(tree.links):
    if link.from_node == preset:
        tree.links.remove(link)
tree.nodes.remove(preset)
node.inputs[node.inputs.find("Dispersion Scale")].default_value = 0
node.use_transmission = False
node.inputs[node.inputs.find("Transmission Weight")].default_value = 0

exported = pyluxcore.Properties()
node.export(type("Exporter", (), {"node_cache": {}, "scene": scene})(), None, exported, "m")
keys = sorted(k[len("scene.materials.m."):] for k in exported.GetAllNames("scene.materials.m."))
checks.check(keys == ["base.color", "coat.weight", "type"], f"OpenPBR node exports {keys}")

sphere.data.materials.clear()
sphere.data.materials.append(material)
LuxCoreErrorLog.clear()
mean = harness.render(scene, "LUXCORE", os.path.join(OUT, "node_luxcore.exr"), 16)[..., :3][DISK].mean(axis=0)
checks.check(not LuxCoreErrorLog.errors, "OpenPBR node: no export errors")
checks.check(bool(mean[0] > 0.02 and mean[0] > mean[2]), f"OpenPBR node renders (mean {np.round(mean, 3)})")

print("images in", OUT)
checks.finish()
