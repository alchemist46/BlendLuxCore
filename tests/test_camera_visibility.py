"""Blender's Camera ray visibility (Object > Visibility > Ray Visibility, used by Cycles).

A mesh with it off is hidden from the camera like with the LuxCore "Visible to
Camera" option off, and still lights the scene: an emitting plane between the
camera and a diffuse ground, rendered by Cycles and LuxCore (direct light).
Instances follow Cycles: hidden when the instanced object or its instancer has
Camera off.

blender -b --factory-startup --python tests/test_camera_visibility.py -- <site-packages>
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

harness.setup()

import bpy
import numpy as np
import pyluxcore
from types import SimpleNamespace
from blendluxcore import utils
from blendluxcore.export import light as light_export

checks = harness.Checks("test_camera_visibility")
OUT = tempfile.mkdtemp(prefix="blc-camvis-")

scene = bpy.context.scene
harness.remove_all_objects()
harness.black_world(scene)


def cycles_material(name, shader, inputs):
    material = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        material.use_nodes = True
    nodes = material.node_tree.nodes
    for node in list(nodes):
        nodes.remove(node)
    output = nodes.new("ShaderNodeOutputMaterial")
    node = nodes.new(shader)
    for key, value in inputs.items():
        node.inputs[key].default_value = value
    material.node_tree.links.new(node.outputs[0], output.inputs[0])
    material.luxcore.use_cycles_nodes = True
    return material


bpy.ops.mesh.primitive_plane_add(size=40)
ground = bpy.context.object
ground.data.materials.append(cycles_material("ground", "ShaderNodeBsdfDiffuse", {"Color": (0.8, 0.8, 0.8, 1)}))

# Emitting downwards, between the camera and the ground
bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 2), rotation=(3.14159, 0, 0))
emitter = bpy.context.object
emitter.data.materials.append(cycles_material("emission", "ShaderNodeEmission",
                                              {"Color": (1, 1, 1, 1), "Strength": 50}))

camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
scene.collection.objects.link(camera)
camera.location = (0, 0, 8)
scene.camera = camera
scene.render.resolution_x = scene.render.resolution_y = 24
scene.render.resolution_percentage = 100
scene.view_settings.view_transform = "Standard"
scene.cycles.max_bounces = 0
scene.luxcore.config.path.depth_total = 1
scene.luxcore.config.path.depth_diffuse = 1
scene.luxcore.config.path.depth_glossy = 1


def center(pixels):
    h, w = pixels.shape[:2]
    return float(pixels[h // 2 - 2:h // 2 + 2, w // 2 - 2:w // 2 + 2, :3].mean())


def camera_flags():
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return {inst.object.name + (" (instance)" if inst.is_instance else ""): utils.visible_to_camera(inst, False)
            for inst in depsgraph.object_instances if inst.object.type == "MESH"}


# The flags: hidden when either Blender's Camera or LuxCore's option is off
for blender_flag, luxcore_flag in ((True, True), (False, True), (True, False), (False, False)):
    emitter.visible_camera = blender_flag
    emitter.luxcore.visible_to_camera = luxcore_flag
    visible = camera_flags()["Plane.001"]
    checks.check(visible == (blender_flag and luxcore_flag),
                 f"Camera {blender_flag}, Visible to Camera {luxcore_flag}: visible {visible}")

# Renders: the camera sees the lit ground through the hidden emitter, as in Cycles
emitter.visible_camera = False
emitter.luxcore.visible_to_camera = True
cycles = center(harness.render(scene, "CYCLES", os.path.join(OUT, "cycles.exr"), 128))
luxcore = center(harness.render(scene, "LUXCORE", os.path.join(OUT, "luxcore.exr"), 128))
checks.check(abs(luxcore / cycles - 1) < 0.03,
             f"emitter hidden from the camera: ground under it LuxCore / Cycles = {luxcore / cycles:.3f} "
             f"(Cycles {cycles:.3f})")
emitter.visible_camera = True
seen = center(harness.render(scene, "LUXCORE", os.path.join(OUT, "luxcore_seen.exr"), 16))
checks.check(abs(seen / cycles - 1) > 0.2, f"emitter seen by the camera: center {seen:.3f} (ground {cycles:.3f})")

# Instances: a collection instance of the emitter, hidden by its own flag or the instancer's
collection = bpy.data.collections.new("instanced")
collection.objects.link(emitter)
instancer = bpy.data.objects.new("instancer", None)
instancer.instance_type = "COLLECTION"
instancer.instance_collection = collection
scene.collection.objects.link(instancer)
for object_flag, instancer_flag in ((True, True), (False, True), (True, False)):
    emitter.visible_camera = object_flag
    instancer.visible_camera = instancer_flag
    visible = camera_flags()["Plane.001 (instance)"]
    checks.check(visible == (object_flag and instancer_flag),
                 f"instance: object Camera {object_flag}, instancer Camera {instancer_flag}: visible {visible}")

# An area light with LuxCore settings follows the Camera flag too
data = bpy.data.lights.new("area", "AREA")
data.luxcore.use_cycles_settings = False
lamp = bpy.data.objects.new("area", data)
scene.collection.objects.link(lamp)
for flag in (True, False):
    lamp.visible_camera = flag
    props, _ = light_export.convert_light(SimpleNamespace(lightgroup_cache=set()), lamp, "area",
                                          bpy.context.evaluated_depsgraph_get(), pyluxcore.Scene(),
                                          lamp.matrix_world.copy(), False)
    hidden = [props.Get(k).GetBool() for k in props.GetAllNames() if k.endswith(".camerainvisible")]
    checks.check(hidden == [not flag], f"LuxCore area light, Camera {flag}: camerainvisible {hidden}")

checks.finish()
