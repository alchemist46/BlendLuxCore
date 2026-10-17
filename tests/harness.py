"""Shared setup of the headless tests.

Every test runs in a fresh Blender:

    blender -b --factory-startup --python tests/<test>.py -- <site-packages>

<site-packages> is a directory where pyluxcore is installed (a venv's
lib/python3.x/site-packages). The addon is loaded from this repository, not
from the installed extension, and the wheel loader is stubbed out so nothing
is downloaded or installed.
"""

import os
import sys
import tempfile
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = "blendluxcore"


def setup():
    """Put pyluxcore and the addon on sys.path and enable the addon, return the arguments after the site-packages"""
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        raise SystemExit("usage: blender -b --factory-startup --python <test>.py -- <site-packages> [args]")
    sys.path.insert(0, argv[0])

    # The addon must be imported as a package: a directory with a link to the repository
    package_dir = tempfile.mkdtemp(prefix="blc-test-")
    os.symlink(REPO, os.path.join(package_dir, PACKAGE))
    sys.path.insert(0, package_dir)

    loader = types.ModuleType(PACKAGE + ".luxloader")
    loader.ensure_pyluxcore = lambda: None
    sys.modules[PACKAGE + ".luxloader"] = loader

    import addon_utils
    addon_utils.enable(PACKAGE, default_set=True)
    return argv[1:]


class Checks:
    """Collects the results, prints one line per check and a summary line"""

    def __init__(self, name):
        self.name = name
        self.failures = 0

    def check(self, ok, what):
        self.failures += not ok
        print(("OK   " if ok else "FAIL ") + what)
        return ok

    def finish(self):
        print(f"{self.name}: " + ("PASS" if self.failures == 0 else f"FAIL ({self.failures})"))
        # Blender's exit code with --python-exit-code only covers exceptions
        if self.failures:
            raise SystemExit(1)


def remove_all_objects():
    import bpy
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)


def black_world(scene):
    """No background light in either engine"""
    import bpy
    world = bpy.data.worlds.new("black")
    if bpy.app.version < (5, 0, 0):
        world.use_nodes = True
    background = world.node_tree.nodes["Background"]
    background.inputs["Strength"].default_value = 0
    background.inputs["Color"].default_value = (0, 0, 0, 1)
    world.luxcore.light = "none"
    scene.world = world


def render(scene, engine, path, samples):
    """Render a still with Cycles or LuxCore (CPU) and return its pixels as an (h, w, 4) array"""
    import bpy
    import numpy as np

    scene.render.engine = engine
    scene.render.filepath = path
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    if engine == "CYCLES":
        scene.cycles.device = "CPU"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = False
        scene.cycles.use_adaptive_sampling = False
    else:
        scene.luxcore.config.engine = "PATH"
        scene.luxcore.config.device = "CPU"
        scene.luxcore.halt.enable = True
        scene.luxcore.halt.use_samples = True
        scene.luxcore.halt.samples = samples
        scene.luxcore.denoiser.enabled = False
    bpy.ops.render.render(write_still=True)

    # The Render Result has no pixels in background mode: read the file back
    image = bpy.data.images.load(path, check_existing=False)
    width, height = image.size
    pixels = np.array(image.pixels[:], dtype=np.float32).reshape(height, width, 4)
    bpy.data.images.remove(image)
    return pixels
