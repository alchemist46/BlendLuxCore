_needs_reload = "bpy" in locals()

import os
import tempfile
import bpy
from bpy.app.handlers import persistent

import pyluxcore
from .. import utils, operators
from ..utils import compatibility, temperature
from ..utils.lol import utils as utils_lol
from . import frame_change_pre
from ..utils.errorlog import LuxCoreErrorLog
from ..operators.manual_compatibility import LUXCORE_OT_convert_to_v23

if _needs_reload:
    import importlib
    modules = (
        utils,
        operators,
        frame_change_pre,
    )
    for module in modules:
        importlib.reload(module)

def _init_persistent_cache_file_path(settings, suffix):
    if not settings.file_path:
        blend_name = utils.get_blendfile_name()
        if blend_name:
            pgi_path = "//" + blend_name + "." + suffix
        else:
            # Blend file was not saved yet
            pgi_path = os.path.join(tempfile.gettempdir(), "Untitled." + suffix)
        settings.file_path = pgi_path


def _init_LuxCoreOnlineLibrary():
    user_preferences = utils.get_addon_preferences(bpy.context)
    ui_props = bpy.context.scene.luxcoreOL.ui

    bpy.context.scene.luxcoreOL.on_search = False
    bpy.context.scene.luxcoreOL.search_category = ""

    ui_props.assetbar_on = False
    ui_props.turn_off = False
    ui_props.ToC_loaded = False
    bpy.context.scene.luxcoreOL.model.thumbnails_loaded = False
    bpy.context.scene.luxcoreOL.scene.thumbnails_loaded = False
    bpy.context.scene.luxcoreOL.material.thumbnails_loaded = False

    utils_lol.ensure_library_dirs(user_preferences.global_dir)


@persistent
def handler(_):
    """ Note: the only argument Blender passes is always None """

    # Record the working colour space of the LuxCore colours (or convert them
    # if the file's space was changed without the addon)
    utils.colorspace.sync_luxcore_colors()

    for scene in bpy.data.scenes:
        # Update OpenCL devices if .blend is opened on
        # a different computer than it was saved on
        scene.luxcore.devices.update_devices_if_necessary()

        if pyluxcore.GetPlatformDesc().Get("compile.LUXRAYS_DISABLE_OPENCL").GetBool():
            # OpenCL not available, make sure we are using CPU device
            scene.luxcore.config.device = "CPU"

        # Use Blender output path for filesaver by default
        if not scene.luxcore.config.filesaver_path:
            scene.luxcore.config.filesaver_path = scene.render.filepath

        _init_persistent_cache_file_path(scene.luxcore.config.photongi, "pgi")
        _init_persistent_cache_file_path(scene.luxcore.config.envlight_cache, "env")
        _init_persistent_cache_file_path(scene.luxcore.config.dls_cache, "dlsc")

        _init_LuxCoreOnlineLibrary()

    # Run converters for backwards compatibility
    compatibility.run()
    # The lights, worlds and Blackbody nodes of a file saved by an older addon version keep
    # their Max temperature normalization. Not on the startup file (no path): its lights
    # are new ones.
    if bpy.data.filepath:
        temperature.set_legacy()

    frame_change_pre.have_to_check_node_trees = False
    # Nothing of the previous file is pending (also drops the marks of the converters above)
    utils.eevee_sync.reset()
    LuxCoreErrorLog.clear()

    # After loading a .blend file, make it possible to execute the conversion operator again
    LUXCORE_OT_convert_to_v23.was_executed = False
