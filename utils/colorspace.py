# Working colour space and image colour spaces.
#
# Blender 5.x renders in a per file working space (Linear Rec.709, Linear
# Rec.2020 or ACEScg). LuxCore renders in the space set by
# scene.colorspace.working, and converts the image maps to it from the colour
# space given by their "colorspace" properties:
# - "nop": already in the working space (also used for non-colour data)
# - "luxcore" (gamma): Rec.709 colours
# - "opencolorio": a colour space of an OCIO config, here Blender's own
#
# This module must not import pyluxcore (see utils/__init__.py).

import os

import bpy

# Blender's interop ids of the working spaces, to LuxCore's values
_WORKING_SPACES = {
    "lin_rec709_scene": "rec709",
    "lin_rec2020_scene": "rec2020",
    "lin_ap1_scene": "acescg",
}
DEFAULT_WORKING_SPACE = "rec709"


def get_working_space():
    """The working space of the .blend file as a scene.colorspace.working value"""
    colorspace = getattr(bpy.data, "colorspace", None)
    if colorspace is None:
        # Blender < 5.0: always Linear Rec.709
        return DEFAULT_WORKING_SPACE
    interop_id = getattr(colorspace, "working_space_interop_id", "")
    return _WORKING_SPACES.get(interop_id, DEFAULT_WORKING_SPACE)


def get_ocio_config():
    """The OCIO config Blender uses ($OCIO if set, its bundled one otherwise)"""
    config = os.environ.get("OCIO")
    if config:
        return config
    return os.path.join(bpy.utils.system_resource("DATAFILES"), "colormanagement", "config.ocio")


def _is_linear_scene_space(name):
    # Blender's scene linear colour spaces: the "Linear ..." ones and the ACES
    # linear ones (the encoded ones are "sRGB", "Filmic Log", "AgX Log", ...)
    return name.startswith("Linear") or name in {"ACEScg", "ACES2065-1"}


def image_colorspace(image):
    """The colour space properties of an image read the way Cycles reads it"""
    settings = image.colorspace_settings
    if settings.is_data:
        return {"colorspace": "nop"}
    return {
        "colorspace": "opencolorio",
        "colorspace.config": get_ocio_config(),
        "colorspace.name": settings.name,
    }


def image_colorspace_with_gamma(image, gamma):
    """The colour space properties of an image with a LuxCore gamma setting

    A gamma other than 1 keeps LuxCore's gamma decoding (Rec.709 colours). With
    a gamma of 1 the image is either data (it would have a gamma of 2.2 if it
    were an encoded colour image) or a linear colour image, which is converted
    from its own colour space (e.g. an ACEScg EXR). In a Rec.709 working space
    this gives the same pixels as the gamma of 1 always did, except for linear
    images in another space, which were not converted at all.
    """
    if gamma != 1:
        return {"gamma": gamma}

    settings = image.colorspace_settings
    if settings.is_data or not _is_linear_scene_space(settings.name):
        return {"colorspace": "nop"}
    return image_colorspace(image)
