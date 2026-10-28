from time import time

_needs_reload = "bpy" in locals()

import bpy
import pyluxcore
from .. import export
from .. import draw
from ..draw.viewport import FrameBuffer
from .. import utils
from ..utils import render as utils_render
from ..utils import get_addon_preferences
from ..utils.log import LuxCoreLog, logger, set_verbose
from ..utils.errorlog import LuxCoreErrorLog
from ..export.config import convert_viewport_engine

if _needs_reload:
    import importlib

    importlib.reload(export)
    importlib.reload(utils)
    importlib.reload(draw)


# Executed in separate thread
def start_session(engine):
    try:
        engine.session.Start()
        engine.viewport_start_time = time()
    except ReferenceError:
        # Could not start render session because RenderEngine struct was deleted (caused
        # by the user cancelling the viewport render before this function is called)
        return
    except Exception as error:
        engine.session = None
        # Reset the exporter to invalidate all caches
        engine.exporter = None

        engine.update_stats("Error: ", str(error))
        LuxCoreErrorLog.add_error(error)

        import traceback

        traceback.print_exc()

    # Note: Due to CPython implementation details, it's not necessary to use a
    # lock here (this modification is atomic)
    engine.starting_session = False


def force_session_restart(engine):
    """
    https://github.com/LuxCoreRender/BlendLuxCore/issues/577
    For unknown reasons, the old way of handling changes to the renderconfig,
    like a viewport resize or renderengine settings edit, now causes a memory
    leak. I have not been able to track it down. (The original code is in
    export/__init__.py in the method _update_config()) As a workaround, I stop
    and delete the session to trigger a full re-export of the scene and a fresh
    restart of the viewport render.
    """
    wait_denoiser(engine)
    if engine.session is not None:
        engine.session.Stop()
        engine.session = None


def wait_denoiser(engine):
    """A running viewport denoiser must finish before the session changes"""
    if engine.framebuffer:
        engine.framebuffer.wait_denoiser()


def view_update(engine, context, depsgraph, changes=None):
    start = time()

    if engine.starting_session or engine.viewport_fatal_error:
        # Prevent deadlock
        return

    LuxCoreErrorLog.clear(force_ui_update=False)

    if engine.session is None:
        if not engine.viewport_starting_message_shown:
            # Let one engine.view_draw() happen so it shows a message in the UI
            return

        if not engine.is_first_viewport_start:
            filmsize = utils.calc_filmsize(depsgraph.scene_eval, context)
            was_resized = engine.last_viewport_size != filmsize
            engine.last_viewport_size = filmsize

            if was_resized:
                engine.time_of_last_viewport_resize = time()
                return
            elif (
                time() - engine.time_of_last_viewport_resize
                < engine.VIEWPORT_RESIZE_TIMEOUT
            ):
                # Don't re-export the session before the timeout is done, to
                # prevent constant re-exports while resizing
                return
        else:
            display_luxcore_logs = get_addon_preferences(
                bpy.context
            ).display_luxcore_logs
            set_verbose(display_luxcore_logs)
            if display_luxcore_logs:
                pyluxcore.SetLogHandler(LuxCoreLog.add)
            else:
                pyluxcore.SetLogHandler(LuxCoreLog.silent)

        try:
            logger.info("=" * 50)
            logger.info("[Engine/Viewport] New session")
            engine.exporter = export.Exporter()
            engine.session = engine.exporter.create_session(
                depsgraph, context, engine=engine
            )
            # Start in separate thread to avoid blocking the UI
            engine.starting_session = True
            engine.is_first_viewport_start = False
            import threading

            threading.Thread(
                target=start_session, args=(engine,), name="BLC session start", daemon=True
            ).start()
        except Exception as error:
            if engine.session is not None:
                # in case engine.session was already set in the try block
                # before the error
                del engine.session
            engine.session = None
            # Reset the exporter to invalidate all caches
            engine.exporter = None
            engine.viewport_fatal_error = str(error)

            engine.update_stats("Error: ", str(error))
            LuxCoreErrorLog.add_error(error)

            import traceback

            traceback.print_exc()
        return

    s = time()
    changes = engine.exporter.get_changes(depsgraph, context, changes)
    delta_t = (time() - s) * 1000
    logger.debug(
        f"view_update(): checking for other changes took {delta_t:.1f} ms"
    )

    if changes:
        if changes & export.Change.REQUIRES_VIEW_UPDATE:
            # Only restart the session if the view transform didn't change by
            # itself
            force_session_restart(engine)
            return
        s = time()
        if engine.framebuffer:
            engine.framebuffer.reset_denoiser()
        # We have to re-assign the session because it might have been replaced
        # due to filmsize change
        engine.session = engine.exporter.update(
            depsgraph, context, engine.session, changes
        )
        engine.viewport_start_time = time()
        # An edit can change the image without new samples (image pipeline)
        engine.viewport_film_key = None
        delta_t = (time() - s) * 1000
        logger.debug(f"view_update(): applying changes took {delta_t:.1f} ms")

    delta_t = (time() - start) * 1000
    logger.debug(f"view_update() took {delta_t:.1f} ms")


# Seconds between two change checks in view_draw() while the view does not change
VIEW_CHANGE_CHECK_INTERVAL = 0.5


def update_film(engine, framebuffer, force=False):
    """Read the film into the framebuffer if it has new samples: most draws
    come before the next frame, and a read costs more than the draw, so the
    draws in between show the last one. Not throttled further: a delay on a
    read after a film reset (a camera move) shows on screen"""
    stats = engine.session.GetStats()
    key = (id(engine.session), stats.Get("stats.renderengine.total.samplecount").GetFloat())
    if key == engine.viewport_film_key and not force:
        return
    framebuffer.update(engine.session)
    engine.viewport_film_key = key


def _view_state(context):
    """Everything view_draw() can see change without a view_update() call"""
    region = context.region
    region_data = context.region_data
    space = context.space_data
    return (
        region.width,
        region.height,
        region_data.view_perspective,
        tuple(tuple(row) for row in region_data.view_matrix),
        tuple(tuple(row) for row in region_data.window_matrix),
        region_data.view_distance,
        region_data.view_camera_zoom,
        tuple(region_data.view_camera_offset),
        space.lens,
        space.clip_start,
        space.clip_end,
        space.camera,
        space.use_render_border,
        space.render_border_min_x,
        space.render_border_min_y,
        space.render_border_max_x,
        space.render_border_max_y,
        space.shading.type,
    )


def view_draw(engine, context, depsgraph):
    scene = depsgraph.scene_eval

    if engine.starting_session:
        engine.tag_redraw()
        return

    if engine.viewport_fatal_error:
        engine.update_stats("Error:", engine.viewport_fatal_error)
        engine.tag_redraw()
        return

    if engine.session is None:
        config = scene.luxcore.config
        definitions = {}
        luxcore_engine, _ = convert_viewport_engine(
            context, scene, definitions, config
        )
        message = ""

        if luxcore_engine.endswith("OCL"):
            # Create dummy renderconfig to check if we have to compile OpenCL kernels
            luxcore_scene = pyluxcore.Scene()
            # The working colour space is a kernel parameter
            luxcore_scene.Parse(utils.luxutils.working_space_props())
            definitions = {
                "scene.camera.type": "perspective",
            }
            luxcore_scene.Parse(utils.luxutils.create_props("", definitions))

            devices = scene.luxcore.devices
            definitions = {
                "renderengine.type": "RTPATHOCL",
                "sampler.type": "TILEPATHSAMPLER",
                "scene.epsilon.min": config.min_epsilon,
                "scene.epsilon.max": config.max_epsilon,
                "opencl.devices.select": devices.devices_to_selection_string(),
            }
            config_props = utils.luxutils.create_props("", definitions)
            renderconfig = pyluxcore.RenderConfig(config_props, luxcore_scene)

            if not renderconfig.HasCachedKernels():
                gpu_backend = utils.get_addon_preferences(context).gpu_backend
                message = (
                    f"Compiling {gpu_backend} kernels ("
                    "((just once, usually takes 15-30 minutes)"
                )

        engine.update_stats("Starting viewport render", message)
        engine.viewport_starting_message_shown = True
        engine.tag_update()
        engine.tag_redraw()
        return

    if not engine.framebuffer or engine.framebuffer.needs_replacement(
        context, scene
    ):
        wait_denoiser(engine)
        engine.framebuffer = FrameBuffer(engine, context, scene)
        # A new framebuffer starts black
        engine.viewport_film_key = None

    framebuffer = engine.framebuffer
    # A display setting: from the scene itself, the evaluated copy of the
    # depsgraph is not updated for it
    framebuffer.mag_filter = context.scene.luxcore.viewport.mag_filter

    # Check for changes because some actions in Blender (e.g. moving the viewport
    # camera) do not trigger a view_update() call, but only a view_draw() call.
    # The check exports the whole config, so only run it when the view changed,
    # and at a low rate for what the view state does not cover (addon preferences)
    view_state = _view_state(context)
    now = time()
    if (view_state != engine.viewport_last_view_state
            or now - engine.viewport_last_change_check > VIEW_CHANGE_CHECK_INTERVAL):
        engine.viewport_last_view_state = view_state
        engine.viewport_last_change_check = now
        changes = engine.exporter.get_viewport_changes(depsgraph, context)
    else:
        changes = export.Change.NONE

    if changes & export.Change.REQUIRES_VIEW_UPDATE:
        engine.tag_redraw()
        # view_update(engine, context, depsgraph, changes)  # Disabled, see comment on force_session_restart()
        force_session_restart(engine)
        return
    elif changes & (export.Change.CAMERA | export.Change.MATERIAL):
        # Only update in view_draw if it is a camera update,
        # for everything else we call view_update().
        # We have to re-assign the session because it might have been
        # replaced due to filmsize change.
        framebuffer.reset_denoiser()
        engine.session = engine.exporter.update(
            depsgraph, context, engine.session, changes
        )
        engine.viewport_start_time = time()
        engine.viewport_film_key = None

    if utils.in_material_shading_mode(context):
        if not engine.session.IsInPause():
            engine.session.WaitNewFrame()
            engine.session.UpdateStats()
            framebuffer.update(engine.session)
            engine.update_stats("", "")

            stats = engine.session.GetStats()
            samples = stats.Get("stats.renderengine.pass").GetInt()

            if samples >= 5:
                logger.info("[Engine/Viewport] Pausing session")
                engine.session.Pause()
            else:
                engine.tag_redraw()
        framebuffer.draw()
        return

    # Check if we need to pause the viewport render
    # (note: the LuxCore stat "stats.renderengine.time" is not reliable here)
    rendered_time = time() - engine.viewport_start_time
    halt_time = scene.luxcore.viewport.halt_time
    status_message = ""

    if rendered_time > halt_time:
        # Put in pause...
        if not engine.session.IsInPause():
            logger.info("[Engine/Viewport] Pausing session")
            engine.session.Pause()
            # The samples since the last read
            engine.session.UpdateStats()
            update_film(engine, framebuffer, force=True)
        status_message = "(Paused)"

        # ...and denoise
        use_oidn = context.scene.luxcore.viewport.get_denoiser(context) == "OIDN"
        if use_oidn and not framebuffer.denoised:
            if not framebuffer.is_denoiser_active():
                logger.info("Starting OIDN denoiser...")
                framebuffer.start_denoiser(
                    engine.session, engine.exporter.imagepipeline_cache.props
                )
            if framebuffer.poll_denoiser():
                status_message = "(Paused, OIDN Denoiser Done)"
            else:
                status_message = "(Paused, OIDN Denoiser Working ...)"
                engine.tag_redraw()
        else:
            if use_oidn:
                status_message = "(Paused, OIDN Denoiser Done)"
            else:
                status_message = "(Paused)"

    else:
        # Not in pause yet, keep drawing
        engine.session.WaitNewFrame()
        try:
            engine.session.UpdateStats()
        except RuntimeError as error:
            logger.warning(f"[Engine/Viewport] Error during UpdateStats(): {error}")
        update_film(engine, framebuffer)
        engine.tag_redraw()

    framebuffer.draw()

    # Show formatted statistics in Blender UI
    config = engine.session.GetRenderConfig()
    stats = engine.session.GetStats()
    pretty_stats = utils_render.get_pretty_stats(config, stats, scene, context)
    engine.update_stats(pretty_stats, status_message)
