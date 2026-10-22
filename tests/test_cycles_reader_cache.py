"""Cycles node reader cache: a material read from its Cycles nodes is read once per export.

Twenty cubes share one Cycles material. The final render must call the Cycles
node reader once (not once per object), render without errors, and a changed
material must be read again during a viewport update (MaterialCache).

blender -b --factory-startup --python tests/test_cycles_reader_cache.py -- <site-packages>
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
import pyluxcore
from blendluxcore import export
from blendluxcore.export import cycles_node_reader, material as material_export
from blendluxcore.export.caches import MaterialCache
from blendluxcore.utils.errorlog import LuxCoreErrorLog

checks = harness.Checks("test_cycles_reader_cache")
OUT = tempfile.mkdtemp(prefix="blc-reader-cache-")

calls = []
original_convert = cycles_node_reader.convert


def counting_convert(material, *args, **kwargs):
    calls.append(material.name)
    return original_convert(material, *args, **kwargs)


cycles_node_reader.convert = counting_convert

scene = bpy.context.scene
harness.remove_all_objects()
scene.render.resolution_x = scene.render.resolution_y = 32
scene.render.resolution_percentage = 100

material = bpy.data.materials.new("shared")
if bpy.app.version < (5, 0, 0):
    material.use_nodes = True
principled = material.node_tree.nodes["Principled BSDF"]
principled.inputs["Base Color"].default_value = (0.8, 0.3, 0.1, 1)
material.luxcore.use_cycles_nodes = True

for i in range(20):
    bpy.ops.mesh.primitive_cube_add(size=0.4, location=((i % 5) - 2, 0, (i // 5) - 1.5))
    bpy.context.object.data.materials.append(material)

light = bpy.data.objects.new("light", bpy.data.lights.new("light", "SUN"))
scene.collection.objects.link(light)
camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
scene.collection.objects.link(camera)
camera.location = (0, -10, 0)
camera.rotation_euler = (1.5708, 0, 0)
scene.camera = camera

LuxCoreErrorLog.clear()
pixels = harness.render(scene, "LUXCORE", os.path.join(OUT, "cubes.exr"), 4)
checks.check(calls.count("shared") == 1, f"final render: the reader ran {calls.count('shared')} times for 20 objects")
checks.check(not LuxCoreErrorLog.errors, f"no export errors {[e.message for e in LuxCoreErrorLog.errors]}")
checks.check(float(pixels[..., :3].mean()) > 0.005, f"the image is not black (mean {pixels[..., :3].mean():.4f})")

# The cache of one exporter, as during a viewport render
calls.clear()
exporter = export.Exporter()
depsgraph = bpy.context.evaluated_depsgraph_get()
first = material_export.convert(exporter, depsgraph, material, True, "a")
second = material_export.convert(exporter, depsgraph, material, True, "b")
checks.check(len(calls) == 1 and first is second, f"same exporter: read once ({len(calls)} reads)")

# A material update reads the Cycles nodes again
cache = MaterialCache()
cache.changed_materials.add(material)
props = pyluxcore.Properties()
cache.update(exporter, depsgraph, True, props)
lux_name = first[0]
checks.check(len(calls) == 2, f"changed material: read again ({len(calls)} reads)")
checks.check(props.IsDefined(f"scene.materials.{lux_name}.type"), "changed material: its props are sent again")
checks.check(material_export.convert(exporter, depsgraph, material, True, "c") is exporter.cycles_material_cache[lux_name]
             and len(calls) == 2, "after the update the new result is cached")

checks.finish()
