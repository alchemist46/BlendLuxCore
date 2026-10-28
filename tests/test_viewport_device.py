"""Viewport device: the Auto default follows the final render's device.

New scenes use Auto; the viewport engine is RTPATHOCL when the final render
uses the GPU (and pyluxcore has GPU support), else RTPATHCPU; CPU and GPU
still force their device; a file saved before Auto existed keeps its device
(the enum numbers of CPU and GPU are unchanged).

blender -b --factory-startup --python tests/test_viewport_device.py -- <site-packages>
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
from blendluxcore.export.config import convert_viewport_engine
from blendluxcore.utils import luxutils

checks = harness.Checks("test_viewport_device")
scene = bpy.context.scene
viewport = scene.luxcore.viewport
config = scene.luxcore.config
# convert_viewport_engine() only reads the shading type from the context
context = types.SimpleNamespace(space_data=types.SimpleNamespace(
    shading=types.SimpleNamespace(type="RENDERED")))
gpu_build = luxutils.is_gpu_build()


def engine():
    return convert_viewport_engine(context, scene, {}, config)[0]


checks.check(viewport.device == "AUTO", f"a new scene uses Auto ({viewport.device})")

config.device = "CPU"
checks.check(viewport.get_device(scene) == "CPU" and engine() == "RTPATHCPU",
             f"Auto with a CPU final render: {viewport.get_device(scene)}, {engine()}")

config.device = "OCL"
expected = "RTPATHOCL" if gpu_build else "RTPATHCPU"
checks.check(viewport.get_device(scene) == "OCL" and engine() == expected,
             f"Auto with a GPU final render: {viewport.get_device(scene)}, {engine()} "
             f"(GPU build: {gpu_build})")

viewport.device = "CPU"
checks.check(engine() == "RTPATHCPU", f"CPU forces the CPU with a GPU final render: {engine()}")

config.device = "CPU"
viewport.device = "OCL"
checks.check(engine() == expected, f"GPU forces the GPU with a CPU final render: {engine()}")

# The enum values stored in files saved before Auto existed
viewport["device"] = 0
checks.check(viewport.device == "CPU", f"stored value 0 reads as CPU ({viewport.device})")
viewport["device"] = 1
checks.check(viewport.device == "OCL", f"stored value 1 reads as GPU ({viewport.device})")

checks.finish()
