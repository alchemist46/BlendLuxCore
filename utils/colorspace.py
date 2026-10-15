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


# ------------------------------------------------------------------------------
# Working space changes
#
# Blender's "Working Space" selector (wm.set_working_color_space with
# convert_colors) converts Blender's own colours, not the ones of the addon.
# The space the LuxCore colours are in is stored on the scenes, and the
# LuxCore colours are converted when the working space no longer matches it.
# A file is never converted on load: its colours were picked in its space.
# ------------------------------------------------------------------------------

_STORED_SPACE_KEY = "luxcore_colors_working_space"


def _get_stored_spaces():
    return {scene.get(_STORED_SPACE_KEY) for scene in bpy.data.scenes} - {None}


def _store_space(space):
    for scene in bpy.data.scenes:
        if scene.get(_STORED_SPACE_KEY) != space:
            scene[_STORED_SPACE_KEY] = space


def _convert_struct(struct, convert, builtin_props=frozenset()):
    """Convert the colour properties of an addon struct, recursing into its
    property groups (never into other ID data-blocks)"""
    for prop in struct.bl_rna.properties:
        identifier = prop.identifier
        if identifier == "rna_type" or identifier in builtin_props:
            continue

        if prop.type == "FLOAT" and prop.subtype == "COLOR" and prop.array_length in (3, 4) \
                and not prop.is_readonly:
            value = getattr(struct, identifier)
            new_value = list(convert(value[:3]))
            if prop.array_length == 4:
                new_value.append(value[3])
            setattr(struct, identifier, new_value)
        elif prop.type == "POINTER":
            value = getattr(struct, identifier)
            if isinstance(value, bpy.types.PropertyGroup):
                _convert_struct(value, convert)
        elif prop.type == "COLLECTION":
            for item in getattr(struct, identifier):
                if isinstance(item, bpy.types.PropertyGroup):
                    _convert_struct(item, convert)


def _convert_luxcore_colors(convert):
    # The "luxcore" settings of every data-block (some are listed by more than
    # one collection of bpy.data)
    done = set()
    for collection_name in dir(bpy.data):
        collection = getattr(bpy.data, collection_name, None)
        if not isinstance(collection, bpy.types.bpy_prop_collection):
            continue
        for datablock in collection:
            if not isinstance(datablock, bpy.types.ID):
                break
            if datablock.as_pointer() in done:
                continue
            done.add(datablock.as_pointer())

            settings = getattr(datablock, "luxcore", None)
            if isinstance(settings, bpy.types.PropertyGroup):
                _convert_struct(settings, convert)

    # The LuxCore node trees: the properties the addon adds to its nodes and
    # sockets (Blender's own node properties are not colours of the scene)
    node_props = frozenset(p.identifier for p in bpy.types.Node.bl_rna.properties)
    socket_props = frozenset(p.identifier for p in bpy.types.NodeSocket.bl_rna.properties)
    for node_tree in bpy.data.node_groups:
        if not node_tree.bl_idname.startswith("luxcore"):
            continue
        for node in node_tree.nodes:
            _convert_struct(node, convert, node_props)
            for socket in node.inputs:
                _convert_struct(socket, convert, socket_props)


def sync_luxcore_colors():
    """Convert the LuxCore colours if the working space has changed since they
    were last converted; called often, so it returns quickly when nothing
    changed"""
    colorspace = getattr(bpy.data, "colorspace", None)
    if colorspace is None:
        return

    current = colorspace.working_space
    stored = _get_stored_spaces()
    if not stored or stored == {current}:
        # Nothing to convert; record the space on the scenes without it (new
        # files, new scenes)
        if any(scene.get(_STORED_SPACE_KEY) != current for scene in bpy.data.scenes):
            _store_space(current)
        return

    # All the scenes of a file share the working space: any stored value that
    # differs from the current one is the space the colours are in
    previous = next(iter(stored - {current}))
    try:
        import PyOpenColorIO as OCIO
        config = OCIO.Config.CreateFromFile(get_ocio_config())
        cpu = config.getProcessor(previous, current).getDefaultCPUProcessor()
    except Exception as error:
        print(f"[BLC] Could not convert the LuxCore colours from {previous} to {current}: {error}")
        _store_space(current)
        return

    print(f"[BLC] Converting the LuxCore colours from {previous} to {current}")
    _convert_luxcore_colors(lambda rgb: cpu.applyRGB(list(rgb)))
    _store_space(current)
