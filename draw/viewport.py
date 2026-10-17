_needs_reload = "bpy" in locals()

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

import math
import os
from time import sleep
import numpy as np
import tempfile
from shutil import which
from os.path import dirname
import pyluxcore
from .. import utils
from ..utils import pfm

if _needs_reload:
    import importlib

    importlib.reload(pyluxcore)
    importlib.reload(utils)


def denoiser_imagepipeline_props(pipeline_props):
    """The viewport image pipeline with OIDN appended (as Film.ApplyOIDN() does)"""
    prefix = "film.imagepipelines.000."
    props = pyluxcore.Properties()
    props.Set(pipeline_props)
    indices = [int(field) for field in
               (name[len(prefix):].split(".")[0] for name in props.GetAllNames(prefix))
               if field.isdigit()]
    index = max(indices) + 1 if indices else 0
    props.Set(pyluxcore.Property(f"{prefix}{index}.type", "INTEL_OIDN"))
    props.Set(pyluxcore.Property(f"{prefix}{index}.sharpness", 0.0))
    return props


class FrameBuffer:
    """FrameBuffer used for viewport render"""

    def __init__(self, engine, context, scene):
        filmsize = utils.calc_filmsize(scene, context)
        self._width, self._height = filmsize
        self._border = utils.calc_blender_border(scene, context)
        self._offset_x, self._offset_y = self._calc_offset(
            context, scene, self._border
        )
        self._pixel_size = int(scene.luxcore.viewport.pixel_size)

        self._transparent = self._initialize_transparency(scene, context)
        bufferdepth = 4 if self._transparent else 3
        self._output_type = (
            pyluxcore.FilmOutputType.RGBA_IMAGEPIPELINE
            if self._transparent
            else pyluxcore.FilmOutputType.RGB_IMAGEPIPELINE
        )

        self._data = np.zeros(
            self._width * self._height * bufferdepth, dtype=np.float32
        )
        self.buffer = gpu.types.Buffer("FLOAT", self._data.size, self._data)
        # Built from the buffer on the first draw after an update
        self._texture = None
        self._init_opengl()

        # Denoiser: runs as an asynchronous image pipeline, so Blender does not block
        self.denoised = False  # Set to true after denoising
        self._denoiser_session = None
        self._denoiser_restore_props = None

    def _initialize_transparency(self, scene, context):
        if utils.is_valid_camera(
            scene.camera
        ) and not utils.in_material_shading_mode(context):
            return scene.camera.data.luxcore.imagepipeline.transparent_film
        return False

    def _init_opengl(self):
        width, height = (
            self._width * self._pixel_size,
            self._height * self._pixel_size,
        )
        x, y = self._offset_x, self._offset_y

        position = [
            (x, y),
            (x + width, y),
            (x + width, y + height),
            (x, y + height),
            (x, y),
            (x + width, y + height),
        ]

        self.shader = gpu.shader.from_builtin("IMAGE")
        self.batch = batch_for_shader(
            self.shader,
            "TRIS",
            {
                "pos": position,
                "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0), (1, 1)],
            },
        )

    def __del__(self):
        self._texture = None
        del self.buffer

    def needs_replacement(self, context, scene):
        if (self._width, self._height) != utils.calc_filmsize(scene, context):
            return True
        valid_cam = utils.is_valid_camera(scene.camera)
        if valid_cam:
            if (
                self._transparent
                != scene.camera.data.luxcore.imagepipeline.transparent_film
            ):
                return True
        elif self._transparent:
            # By default (if no camera is available), the film is not transparent
            return True
        new_border = utils.calc_blender_border(scene, context)
        if self._border != new_border:
            return True
        if (self._offset_x, self._offset_y) != self._calc_offset(
            context, scene, new_border
        ):
            return True
        if self._pixel_size != int(scene.luxcore.viewport.pixel_size):
            return True
        return False

    def _calc_offset(self, context, scene, border):
        region_size = context.region.width, context.region.height
        view_camera_offset = list(context.region_data.view_camera_offset)
        view_camera_zoom = context.region_data.view_camera_zoom
        zoom = 0.25 * ((math.sqrt(2) + view_camera_zoom / 50) ** 2)

        render = scene.render
        region_width, region_height = region_size
        border_min_x, border_max_x, border_min_y, border_max_y = border

        if (
            context.region_data.view_perspective == "CAMERA"
            and render.use_border
        ):
            # Offset is only needed if viewport is in camera mode and uses
            # border rendering
            sensor_fit = scene.camera.data.sensor_fit

            aspectratio, aspect_x, aspect_y = utils.calc_aspect(
                render.resolution_x * render.pixel_aspect_x,
                render.resolution_y * render.pixel_aspect_y,
                sensor_fit,
            )

            base = 0.5 * zoom
            if sensor_fit == "AUTO":
                base *= max(region_width, region_height)
            elif sensor_fit == "HORIZONTAL":
                base *= region_width
            elif sensor_fit == "VERTICAL":
                base *= region_height

            offset_x = self._cam_border_offset(
                aspect_x,
                base,
                border_min_x,
                region_width,
                view_camera_offset[0],
                zoom,
            )
            offset_y = self._cam_border_offset(
                aspect_y,
                base,
                border_min_y,
                region_height,
                view_camera_offset[1],
                zoom,
            )

        else:
            offset_x = region_width * border_min_x + 1
            offset_y = region_height * border_min_y + 1

        # offset_x, offset_y are in pixels
        return int(offset_x), int(offset_y)

    def _cam_border_offset(
        self, aspect, base, border_min, region_width, view_camera_offset, zoom
    ):
        return (
            0.5 - 2 * zoom * view_camera_offset
        ) * region_width + aspect * base * (2 * border_min - 1)

    def start_denoiser(self, session, pipeline_props):
        """Start OIDN on the paused session; pipeline_props is the image pipeline it uses"""
        session.Parse(denoiser_imagepipeline_props(pipeline_props))
        self._denoiser_session = session
        self._denoiser_restore_props = pipeline_props
        session.GetFilm().AsyncExecuteImagePipeline(0)

    def is_denoiser_active(self):
        return self._denoiser_session is not None

    def poll_denoiser(self):
        """Load the denoised image once OIDN is done, return True then"""
        session = self._denoiser_session
        if session is None or not session.GetFilm().HasDoneAsyncExecuteImagePipeline():
            return False
        try:
            self.update(session, execute_imagepipeline=False)
            self.denoised = True
        finally:
            self._end_denoiser()
        return True

    def wait_denoiser(self):
        """Wait for a running denoiser, before the session is edited or stopped"""
        session = self._denoiser_session
        if session is None:
            return
        # Polling instead of WaitAsyncExecuteImagePipeline(): older pyluxcore
        # builds join the thread a second time in the film destructor
        film = session.GetFilm()
        while not film.HasDoneAsyncExecuteImagePipeline():
            sleep(0.005)
        self._end_denoiser()

    def _end_denoiser(self):
        # Back to the image pipeline without OIDN
        session = self._denoiser_session
        self._denoiser_session = None
        session.Parse(self._denoiser_restore_props)
        self._denoiser_restore_props = None

    def reset_denoiser(self):
        self.wait_denoiser()
        self.denoised = False

    def update(self, luxcore_session, execute_imagepipeline=True):
        # The gpu buffer uses 16-bit float. Values >= 65520 get cast to
        # infinty, leading to a black viewport.
        # Here, I need to get the data into a separate numpy array to handle
        # the clamping.
        data = self._data
        luxcore_session.GetFilm().GetOutputFloat(
            self._output_type,
            data,
            0,  # index
            execute_imagepipeline
        )
        np.minimum(data, 65519, out=data)
        self.buffer = gpu.types.Buffer("FLOAT", data.size, data)
        self._texture = None

    def draw(self):
        if self._texture is None:
            format = "RGBA16F" if self._transparent else "RGB16F"
            self._texture = gpu.types.GPUTexture(
                size=(self._width, self._height),
                layers=0,
                is_cubemap=False,
                format=format,
                data=self.buffer,
            )
        self.shader.uniform_sampler("image", self._texture)
        self.batch.draw(self.shader)
