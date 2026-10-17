"""Mesh export: every triangle corner gets Blender's position, corner normal, UVs and colors,
and corners with the same data share one vertex.

blender -b --factory-startup --python tests/test_mesh_export.py -- <site-packages>
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
import numpy as np
from blendluxcore.export import mesh_converter

checks = harness.Checks("test_mesh_export")


class RecordingScene:
    """Stands in for pyluxcore.Scene: keeps the DefineMeshExt() arguments"""

    def __init__(self):
        self.meshes = []

    def DefineMeshExt(self, **kwargs):
        self.meshes.append(kwargs)


def blender_corners(obj):
    """Per loop data of the evaluated mesh, as Blender sees it"""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh = obj.evaluated_get(depsgraph).to_mesh()
    get = mesh_converter.get_ndarray
    loop_vertices = get(mesh.loops, "vertex_index", 0, np.uint32)
    data = {
        "points": get(mesh.vertices, "co", 3, np.float32)[loop_vertices],
        "normals": get(mesh.corner_normals, "vector", 3, np.float32),
        "uvs": [get(layer.uv, "vector", 2, np.float32) for layer in mesh.uv_layers],
        "colors": [],
    }
    for attribute in mesh.color_attributes:
        colors = get(attribute.data, "color", 4, np.float32)
        data["colors"].append(colors[loop_vertices] if attribute.domain == "POINT" else colors)
    triangles = get(mesh.loop_triangles, "loops", 3, np.uint32)
    materials = get(mesh.loop_triangles, "material_index", 1, np.uint32).ravel()
    obj.evaluated_get(depsgraph).to_mesh_clear()
    return data, triangles, materials


def check_export(name, obj, expected_ratio=None):
    expected, triangles, materials = blender_corners(obj)
    scene = RecordingScene()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    mesh_converter.convert(obj, "key", depsgraph, scene, False, False, obj.matrix_world)

    ok = len(scene.meshes) == len(np.unique(materials))
    worst = 0.0
    corners = 0
    vertices = 0
    for mesh, material in zip(scene.meshes, np.unique(materials)):
        loops = triangles[materials == material]
        exported = np.asarray(mesh["triangles"]).reshape(-1, 3)
        pairs = [(np.asarray(mesh["points"]), expected["points"]),
                 (np.asarray(mesh["normals"]), expected["normals"])]
        pairs += [(np.asarray(a), b) for a, b in zip(mesh["uvs"], expected["uvs"])]
        pairs += [(np.asarray(a), b[:, :3]) for a, b in zip(mesh["colors"], expected["colors"])]
        pairs += [(np.asarray(a), b[:, 3]) for a, b in zip(mesh["alphas"], expected["colors"])]
        ok &= len(mesh["uvs"]) == len(expected["uvs"]) and len(mesh["colors"]) == len(expected["colors"])
        for exported_data, blender_data in pairs:
            worst = max(worst, float(np.abs(exported_data[exported] - blender_data[loops]).max()))
        corners += len(np.unique(loops))
        vertices += len(mesh["points"])
        # No two vertices with the same data (all of it, so the comparison is exact)
        rows = np.hstack([np.asarray(mesh["points"]).reshape(len(mesh["points"]), -1),
                          np.asarray(mesh["normals"])] + [np.asarray(uv) for uv in mesh["uvs"]]
                         + [np.asarray(c) for c in mesh["colors"]]
                         + [np.asarray(a)[:, None] for a in mesh["alphas"]])
        ok &= len(np.unique(rows, axis=0)) == len(rows)
    ok &= worst == 0.0
    ratio = corners / max(vertices, 1)
    if expected_ratio is not None:
        ok &= ratio >= expected_ratio
    checks.check(ok, f"{name}: data of every corner exact (max diff {worst}), "
                     f"{corners} loops -> {vertices} vertices ({ratio:.2f}x)")


harness.remove_all_objects()
random.seed(1)

bpy.ops.mesh.primitive_cube_add()
check_export("flat cube", bpy.context.object)

bpy.ops.mesh.primitive_monkey_add()
monkey = bpy.context.object
bpy.ops.object.shade_smooth()
monkey.modifiers.new("subdivision", "SUBSURF").levels = 2
check_export("smooth subdivided monkey", monkey, expected_ratio=3.0)

bpy.ops.mesh.primitive_monkey_add(location=(3, 0, 0))
bpy.ops.object.shade_auto_smooth(angle=0.5)
check_export("smooth by angle", bpy.context.object)

bpy.ops.mesh.primitive_monkey_add(location=(6, 0, 0))
custom = bpy.context.object
bpy.ops.object.shade_smooth()
custom.data.normals_split_custom_set([(random.uniform(-1, 1), random.uniform(-1, 1), 1) for _ in custom.data.loops])
check_export("custom normals", custom)

bpy.ops.mesh.primitive_monkey_add(location=(9, 0, 0))
layered = bpy.context.object
bpy.ops.object.shade_smooth()
for i in range(3):
    layered.data.materials.append(bpy.data.materials.new(f"material {i}"))
for polygon in layered.data.polygons:
    polygon.material_index = polygon.index % 3
layered.data.uv_layers.new(name="second")
for item in layered.data.uv_layers[1].uv:
    item.vector = (random.random(), random.random())
point_colors = layered.data.color_attributes.new("point", "FLOAT_COLOR", "POINT")
for item in point_colors.data:
    item.color = (random.random(), random.random(), random.random(), random.random())
corner_colors = layered.data.color_attributes.new("corner", "BYTE_COLOR", "CORNER")
for item in corner_colors.data:
    item.color = (random.random(), random.random(), random.random(), 1)
check_export("3 materials, 2 UV layers, point and corner colors", layered)

bpy.ops.mesh.primitive_uv_sphere_add(location=(12, 0, 0))
bpy.ops.object.shade_smooth()
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project()
bpy.ops.object.mode_set(mode="OBJECT")
check_export("UV seams (smart project)", bpy.context.object)

checks.finish()
