from contextlib import contextmanager
from time import time
import numpy as np

_needs_reload = "bpy" in locals()

import bpy

from . import caches
from .. import utils
from ..utils.errorlog import LuxCoreErrorLog
from ..utils.log import logger

if _needs_reload:
    import importlib

    importlib.reload(caches)
    importlib.reload(utils)


# https://blenderartists.org/t/\
# efficient-copying-of-vertex-coords-to-and-from-numpy-arrays/661467/2
def get_ndarray(
    bpy_collection: bpy.types.bpy_prop_collection,
    attr: str,
    stride: int,
    dtype: np.dtype,
):
    """Get a numpy array from a Blender collection.

    If stride == 0, the array is raveled
    """
    count = len(bpy_collection)
    buffer = np.empty(shape=(count, stride if stride else 1), dtype=dtype)
    bpy_collection.foreach_get(attr, np.ravel(buffer))
    if not stride:
        buffer = buffer.ravel()
    return buffer


def convert(
    obj,
    mesh_key,
    depsgraph,
    luxcore_scene,
    is_viewport_render,
    use_instancing,
    transform,
    exporter=None,
):
    start_time = time()

    with _prepare_mesh(obj, depsgraph) as mesh:
        if mesh is None:
            return None

        # Blender API may not be always consistent with naming, for the mesh object.
        # For the sake of clarity, we list here our naming conventions.
        # They may specially differ from attribute domains...
        # https://docs.blender.org/api/current/bpy_types_enum_items/attribute_domain_items.html#rna-enum-attribute-domain-items
        # Point: a point in the 3D-space, (float, float, float)
        # Vertex: an index to the mesh array of points
        # Loop: a vertex and an edge

        # Loop vertices
        loop_vertices = get_ndarray(mesh.loops, "vertex_index", 0, np.uint32)

        # Points
        vertex_points = get_ndarray(mesh.vertices, "co", 3, np.float32)

        # Normals: one per corner, exact for flat faces, sharp edges and
        # custom normals (no need to split the faces)
        loop_normals = get_ndarray(mesh.corner_normals, "vector", 3, np.float32)

        # Triangle loop indices
        triangle_loops = get_ndarray(
            mesh.loop_triangles, "loops", 3, np.uint32
        )

        # Material slot index for each triangle
        loop_triangle_materials = get_ndarray(
            mesh.loop_triangles, "material_index", 1, np.uint32
        ).ravel()
        unique_mats = np.unique(loop_triangle_materials)

        # UV
        uvs = [
            get_ndarray(uv_layer.uv, "vector", 2, np.float32)
            for uv_layer in mesh.uv_layers
        ]

        # Vertex colors
        def reshape_colors(colors, domain):
            if domain == "POINT":
                return colors[loop_vertices]
            elif domain == "CORNER":
                return colors
            else:
                raise ValueError(f"Unhandled attribute domain: '{domain}'")

        rgba_colors = [
            reshape_colors(
                get_ndarray(attribute.data, "color", 4, np.float32),
                attribute.domain,
            )
            for attribute in mesh.color_attributes
        ]

        # Transformation
        if is_viewport_render or use_instancing:
            mesh_transform = None
        else:
            mesh_transform = np.array(
                [
                    transform[0][0:4],
                    transform[1][0:4],
                    transform[2][0:4],
                    transform[3][0:4],
                ],
                dtype=np.float32,
            )

        # Log
        def fmt_layer(layers, layer_name):
            nlayers = len(layers)
            suffix = "layers" if nlayers > 1 else "layer"
            return f"{nlayers} {layer_name} {suffix}"
        logger.debug(f"Exporting '{str(mesh_key)}' - {len(unique_mats)} submesh(es)")
        logger.debug(f"- {len(vertex_points)} points, {len(loop_vertices)} corners")
        logger.debug(f"- {fmt_layer(uvs, 'uv')}")
        logger.debug(f"- {fmt_layer(rgba_colors, 'color')}")

        mesh_definitions = []
        for mat in unique_mats:
            mat_triangles = triangle_loops[loop_triangle_materials == mat]
            name = f"{str(mesh_key)}{mat:03d}"

            # One LuxCore vertex per distinct corner of this submesh
            loops, triangles = _share_corners(
                mat_triangles, loop_vertices, loop_normals, uvs, rgba_colors
            )
            logger.debug(f"- Submesh #{mat:03d}: {len(mat_triangles)} triangles, "
                         f"{len(loops)} vertices")

            luxcore_scene.DefineMeshExt(
                name=name,
                points=vertex_points[loop_vertices[loops]],
                triangles=triangles,
                normals=loop_normals[loops],
                uvs=[uv[loops] for uv in uvs],
                colors=[np.ascontiguousarray(rgba[loops, :3]) for rgba in rgba_colors],
                alphas=[np.ascontiguousarray(rgba[loops, 3]) for rgba in rgba_colors],
                transformation=mesh_transform,
            )
            mesh_definitions.append((name, mat))

        duration = time() - start_time
        if exporter and exporter.stats:
            exporter.stats.export_time_meshes.value += duration
        logger.debug(f"Export duration: {duration:.3f}s")

        return caches.exported_data.ExportedMesh(mesh_definitions)


def _share_corners(triangle_loops, loop_vertices, loop_normals, uvs, rgba_colors):
    """Merge the corners of the triangles that have the same vertex and attributes.

    Returns the loop index of each LuxCore vertex and the triangles as indices
    into them. Corners are merged only when all their data is equal, so seams,
    sharp edges and flat faces keep their own vertices.
    """
    # Work on loops: the loops of a quad or an n-gon are corners of several triangles
    used_loops = np.zeros(len(loop_vertices), dtype=bool)
    used_loops[triangle_loops] = True
    all_loops = bool(used_loops.all())
    loops = None if all_loops else np.flatnonzero(used_loops)

    def of_loops(array):
        return array if all_loops else array[loops]

    vertices = of_loops(loop_vertices)
    attributes = [of_loops(a) for a in [loop_normals] + uvs + rgba_colors]

    # Most loops of a vertex carry the same data: one representative loop per
    # vertex, and the loops equal to it share its LuxCore vertex
    representative = np.empty(int(vertices.max()) + 1, dtype=np.intp)
    representative[vertices] = np.arange(len(vertices))
    reference = representative[vertices]
    same = np.ones(len(vertices), dtype=bool)
    for attribute in attributes:
        same &= (attribute == attribute[reference]).all(axis=1)

    used = np.zeros(len(representative), dtype=bool)
    used[vertices] = True
    used_vertices = np.flatnonzero(used)
    vertex_index = np.cumsum(used) - 1

    inverse = np.empty(len(vertices), dtype=np.int64)
    inverse[same] = vertex_index[vertices[same]]
    first = representative[used_vertices]

    # The other loops (seams, sharp edges, flat faces): exact comparison of
    # whole rows, vertex included
    others = np.flatnonzero(~same)
    if len(others):
        rows = np.ascontiguousarray(np.hstack(
            [vertices[others, None]] + [a[others].view(np.uint32) for a in attributes]))
        rows = rows.view(np.dtype((np.void, rows.dtype.itemsize * rows.shape[1]))).ravel()
        _, other_first, other_inverse = np.unique(rows, return_index=True, return_inverse=True)
        inverse[others] = len(used_vertices) + other_inverse.ravel()
        first = np.concatenate([first, others[other_first]])

    if all_loops:
        triangles = inverse[triangle_loops]
        vertex_loops = first
    else:
        loop_to_vertex = np.empty(len(loop_vertices), dtype=np.int64)
        loop_to_vertex[loops] = inverse
        triangles = loop_to_vertex[triangle_loops]
        vertex_loops = loops[first]
    return vertex_loops, triangles.astype(np.uint32)


@contextmanager
def _prepare_mesh(obj, depsgraph):
    """
    Create a temporary mesh from an object.
    The mesh is guaranteed to be removed when the calling block ends.
    Can return None if no mesh could be created from the object (e.g. for empties)

    Use it like this:

    with mesh_converter.convert(obj, depsgraph) as mesh:
        if mesh:
            print(mesh.name)
            ...
    """

    mesh = None
    object_eval = None

    try:
        object_eval = obj.evaluated_get(depsgraph)
        if object_eval:
            mesh = object_eval.to_mesh()

            if mesh:
                # TODO test if this makes sense
                # has been tested briefly for_v2.10. Seems to work, also
                # including custom normals now
                ## but leaving this out on purpose because
                ## a) users should clean their meshes themselves, and
                ## b) this will allow some artistic effects
                # if object_eval.matrix_world.determinant() < 0.0:
                #     mesh.flip_normals()

                if not mesh.loop_triangles:
                    object_eval.to_mesh_clear()
                    mesh = None


        yield mesh
    finally:
        if object_eval and mesh:
            object_eval.to_mesh_clear()
