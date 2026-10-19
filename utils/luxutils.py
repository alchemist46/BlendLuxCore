"""Various utilities requiring pyluxcore."""

import pyluxcore

from . import colorspace


def working_space_props():
    """The scene properties of the working colour space of the .blend file

    They are empty in Rec.709 (the default), so older pyluxcore builds keep
    working. They must be parsed before anything else in the scene.
    """
    props = pyluxcore.Properties()
    working_space = colorspace.get_working_space()
    if working_space != colorspace.DEFAULT_WORKING_SPACE:
        props.Set(pyluxcore.Property("scene.colorspace.working", working_space))
    return props


def create_props(prefix, definitions):
    """
    :param prefix: string, will be prepended to each key part of the definitions.
                   Example: "scene.camera." (note the trailing dot)
    :param definitions: dictionary of definition pairs. Example: {"fieldofview", 45}
    :return: pyluxcore.Properties() object, initialized with the given definitions.
    """
    props = pyluxcore.Properties()

    for key, value in definitions.items():
        props.Set(pyluxcore.Property(prefix + key, value))

    return props


def matrix_to_list(matrix, invert=False):
    """Flatten a 4x4 matrix into a list

    Returns list[16]
    """
    # Copy required for BlenderMatrix4x4ToList(), not sure why, but if we don't
    # make a copy, we only get an identity matrix in C++
    matrix = matrix.copy()

    if invert:
        matrix.invert_safe()

    return pyluxcore.BlenderMatrix4x4ToList(matrix)


def is_opencl_build():
    """Check if pyluxcore has been built with OpenCL support."""
    return (
        pyluxcore.GetPlatformDesc()
        .Get("compile.LUXRAYS_ENABLE_OPENCL")
        .GetBool()
    )


def is_cuda_build():
    """Check if pyluxcore has been built with Cuda support."""
    return (
        pyluxcore.GetPlatformDesc()
        .Get("compile.LUXRAYS_ENABLE_CUDA")
        .GetBool()
    )


def is_hip_build():
    """Check if pyluxcore has been built with HIP support (AMD GPUs)."""
    platform_desc = pyluxcore.GetPlatformDesc()
    # pyluxcore versions without HIP support don't define the property
    if not platform_desc.IsDefined("compile.LUXRAYS_ENABLE_HIP"):
        return False
    return platform_desc.Get("compile.LUXRAYS_ENABLE_HIP").GetBool()


def is_hiprt_available():
    """Check if pyluxcore can use HIPRT (hardware ray tracing on AMD GPUs).

    It requires a pyluxcore with HIPRT support and the HIPRT library."""
    platform_desc = pyluxcore.GetPlatformDesc()
    # pyluxcore versions without HIPRT support don't define the property
    if not platform_desc.IsDefined("compile.LUXRAYS_ENABLE_HIPRT"):
        return False
    return platform_desc.Get("compile.LUXRAYS_ENABLE_HIPRT").GetBool()


def is_gpu_build():
    """Check if pyluxcore has been built with any GPU support."""
    return is_opencl_build() or is_cuda_build() or is_hip_build()


_known_material_types = {}


def has_material_type(type_name):
    """Check if pyluxcore knows a material type (e.g. "openpbr", new in pyluxcore 3.0)"""
    if type_name not in _known_material_types:
        props = pyluxcore.Properties()
        props.Set(pyluxcore.Property("scene.materials.__blc_probe__.type", type_name))
        try:
            pyluxcore.Scene().Parse(props)
            _known_material_types[type_name] = True
        except RuntimeError:
            _known_material_types[type_name] = False
    return _known_material_types[type_name]
