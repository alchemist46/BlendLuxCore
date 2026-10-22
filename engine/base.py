from time import sleep
_needs_reload = "bpy" in locals()

import bpy
import pyluxcore
from . import final, preview, viewport
from .. import icons, utils, properties
from ..utils.statistics import TileStats
from ..utils.log import LuxCoreLog
from ..utils.errorlog import LuxCoreErrorLog
from ..utils import view_layer as utils_view_layer
from ..utils import get_addon_preferences
from ..properties.display import LuxCoreDisplaySettings
from ..properties.aovs import AOVS


if _needs_reload:
    import importlib

    modules = (icons, utils, properties, final, preview, viewport)
    for module in modules:
        importlib.reload(module)


class LuxCoreRenderEngine(bpy.types.RenderEngine):
    bl_idname = "LUXCORE"
    bl_label = "LuxCoreRender"

    # Apply compositing on render results.
    bl_use_postprocess = True

    # Enables material preview (but not texture preview) for this render engine.
    # Previews call self.render() like a final render. We have to check self.is_preview
    # to see if we should render a preview.
    bl_use_preview = True

    # Hides Cycles node trees in the node editor.
    bl_use_shading_nodes_custom = False

    # Sets the value of the property scene.render.use_spherical_stereo
    # (this is all it does as far as I could see in the Blender source code).
    bl_use_spherical_stereo = False

    # Texture previews are disabled intentionally. It is faster and easier to let
    # Blender Internal render them. They are only shown for brush textures,
    # displacement textures etc., not for LuxCore textures.
    bl_use_texture_preview = False

    # Use Eevee nodes in look dev ("MATERIAL") shading mode in the viewport.
    bl_use_eevee_viewport = False

    final_running = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = None
        self.DENOISED_OUTPUT_NAME = "DENOISED"
        self.VIEWPORT_RESIZE_TIMEOUT = 0.3
        self.reset()

    def reset(self):
        self.framebuffer = None
        self.exporter = None
        self.aov_imagepipelines = {}
        self.is_first_viewport_start = True
        self.viewport_start_time = 0
        self.starting_session = False
        self.viewport_starting_message_shown = False
        self.viewport_fatal_error = None
        self.time_of_last_viewport_resize = 0
        self.last_viewport_size = (0, 0)
        self.viewport_last_view_state = None
        self.viewport_last_change_check = 0

    def __del__(self):
        # Note: this method is also called when unregister() is called (for some reason I don't understand)
        try:
            if getattr(self, "session", None):
                if not self.is_preview:
                    print("[Engine] del: stopping session")
                if hasattr(getattr(self, "framebuffer", None), "wait_denoiser"):
                    # A running viewport denoiser must finish first
                    self.framebuffer.wait_denoiser()
                if self.session.IsStarted():
                    self.session.Stop()
                del self.session
        except ReferenceError:
            print("[Engine] del: RenderEngine struct was already deleted")

    def log_listener(self, msg):
        if "Direct light sampling cache entries" in msg:
            self.update_stats("", msg)
            # We have to sleep for a bit, otherwise Blender does not update the UI
            sleep(0.01)

    def render(self, depsgraph):
        display_luxcore_logs = get_addon_preferences(bpy.context).display_luxcore_logs
        if display_luxcore_logs:
            pyluxcore.SetLogHandler(LuxCoreLog.add)
        else:
            pyluxcore.SetLogHandler(LuxCoreLog.silent)
        if self.is_preview:
            self.render_preview(depsgraph)
        else:
            self.render_final(depsgraph)

    def render_final(self, depsgraph):
        try:
            LuxCoreRenderEngine.final_running = True
            LuxCoreDisplaySettings.paused = False
            LuxCoreDisplaySettings.stop_requested = False
            TileStats.reset()
            LuxCoreLog.add_listener(self.log_listener)
            final.render(self, depsgraph)
        except Exception as error:
            error_str = str(error)
            if error_str.startswith("OpenCL device selection string has the wrong length"):
                error_str += ". To fix this, update the OpenCL device list in the device settings"

            self.report({"ERROR"}, error_str)
            self.error_set(error_str)
            import traceback
            traceback.print_exc()
            # Add error to error log so the user can inspect and copy/paste it
            LuxCoreErrorLog.add_error(error_str)

            # Clean up
            del self.session
            self.session = None
        finally:
            utils_view_layer.State.reset()
            LuxCoreRenderEngine.final_running = False
            TileStats.reset()
            LuxCoreLog.remove_listener(self.log_listener)

    def render_preview(self, depsgraph):
        try:
            preview.render(self, depsgraph)
        except Exception as error:
            import traceback
            traceback.print_exc()
            # Clean up
            del self.session
            self.session = None

    def view_update(self, context, depsgraph):
        viewport.view_update(self, context, depsgraph)

    def view_draw(self, context, depsgraph):
        try:
            viewport.view_draw(self, context, depsgraph)
        except Exception as error:
            del self.session
            self.session = None

            self.update_stats("Error: ", str(error))
            import traceback
            traceback.print_exc()

    def has_denoiser(self):
        return self.DENOISED_OUTPUT_NAME in self.aov_imagepipelines

    def update_render_passes(self, scene=None, renderlayer=None):
        """
        Blender API defined method.
        Called by compositor to display sockets of custom render passes.
        """
        self.register_pass(scene, renderlayer, "Combined", 4, "RGBA", 'COLOR')

        # Denoiser
        if scene.luxcore.denoiser.enabled:
            transparent = scene.camera.data.luxcore.imagepipeline.transparent_film
            if transparent:
                self.register_pass(scene, renderlayer, "DENOISED", 4, "RGBA", "COLOR")
            else:
                self.register_pass(scene, renderlayer, "DENOISED", 3, "RGB", "COLOR")

        aovs = renderlayer.luxcore.aovs

        # Note: the last argument must be in ("COLOR", "VECTOR", "VALUE") and controls the socket color.
        for aov in AOVS:
            if getattr(aovs, aov.prop):
                self.register_pass(scene, renderlayer, aov.pass_name, aov.pass_depth, aov.channels, aov.pass_type)

        # Light groups
        lightgroups = scene.luxcore.lightgroups
        lightgroup_pass_names = lightgroups.get_pass_names()
        default_group_name = lightgroups.get_lightgroup_pass_name(is_default_group=True)
        # If only the default group is in the list, it doesn't make sense to show lightgroups
        # Note: this behaviour has to be the same as in the _add_passes() function in the engine/final.py file
        if lightgroup_pass_names != [default_group_name]:
            for name in lightgroup_pass_names:
                self.register_pass(scene, renderlayer, name, 3, "RGB", "COLOR")


def template_refresh_button(is_refreshing, operator_name, layout, run_msg="Refreshing..."):
    col = layout.column()
    col.enabled = LuxCoreRenderEngine.final_running

    col.operator(operator_name, icon=icons.REFRESH)
    if is_refreshing:
        col.label(text=run_msg)
