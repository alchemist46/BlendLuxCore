"""Keep the generated EEVEE nodes of LuxCore materials in sync with their LuxCore node trees.

LuxCore node trees are marked dirty from three places:
- utils.node.force_viewport_update(), the update callback of the LuxCore
  socket and node properties (value changes, which Blender does not list in
  the depsgraph updates);
- LuxCoreMaterialNodeTree.update() / LuxCoreTextureNodeTree.update()
  (structure changes: links, new or removed nodes);
- the depsgraph_update_post handler, as a safety net for node tree updates.

A timer coalesces the marks (a slider drag gives one flush when it stops) and
flush() regenerates the Cycles nodes (utils/eevee_nodes.py) of every material
that uses a dirty tree, has "Keep EEVEE Nodes in Sync" on and renders with its
LuxCore tree. Tests and operators call flush() directly (timers do not run in
background mode).

Only names are stored, so undo, redo and deleted data never leave a dead
reference here. This module must not import pyluxcore or utils.node at module
level (utils.node imports it).
"""

import time

import bpy

from .errorlog import LuxCoreErrorLog
from .log import logger

LUXCORE_TREE_TYPES = {"luxcore_material_nodes", "luxcore_texture_nodes", "luxcore_volume_nodes"}
# Seconds without a new mark before the flush runs
DELAY = 0.2

_dirty_trees = set()
_dirty_materials = set()
_last_mark = 0.0
_flushing = False


def mark_tree(node_tree):
    """Mark a LuxCore node tree as changed (other node trees are ignored)"""
    if node_tree is None or getattr(node_tree, "bl_idname", None) not in LUXCORE_TREE_TYPES:
        return
    try:
        name = node_tree.name_full
    except ReferenceError:
        return
    _dirty_trees.add(name)
    _schedule()


def mark_material(material):
    """Mark a material whose EEVEE nodes must be generated again"""
    if material is None:
        return
    try:
        name = material.name_full
    except ReferenceError:
        return
    _dirty_materials.add(name)
    _schedule()


def discard(material):
    """Forget the pending marks of a material and of its LuxCore node tree"""
    _dirty_materials.discard(material.name_full)
    node_tree = material.luxcore.node_tree
    if node_tree is not None:
        _dirty_trees.discard(node_tree.name_full)


def _schedule():
    global _last_mark
    _last_mark = time.monotonic()
    if bpy.app.background:
        return
    try:
        if not bpy.app.timers.is_registered(_timer):
            bpy.app.timers.register(_timer, first_interval=DELAY)
    except (ValueError, RuntimeError) as error:
        logger.debug(f"EEVEE node sync: could not register the timer: {error}")


def _timer():
    elapsed = time.monotonic() - _last_mark
    if elapsed < DELAY:
        # Marks are still arriving (e.g. a slider drag)
        return max(DELAY - elapsed, 0.05)
    try:
        flush()
    except Exception:
        logger.exception("EEVEE node sync failed")
    return None


def reset():
    """Drop all pending marks and the timer (file load, unregister)"""
    _dirty_trees.clear()
    _dirty_materials.clear()
    try:
        if bpy.app.timers.is_registered(_timer):
            bpy.app.timers.unregister(_timer)
    except (ValueError, RuntimeError):
        pass


def is_eligible(material):
    """True if the EEVEE nodes of a material are generated from its LuxCore tree"""
    if not hasattr(material, "luxcore"):
        return False
    luxcore = material.luxcore
    return (not material.library
            and not getattr(material, "override_library", None)
            and luxcore.sync_eevee_nodes
            and not luxcore.use_cycles_nodes
            and luxcore.node_tree is not None)


def _luxcore_tree(material):
    luxcore = getattr(material, "luxcore", None)
    return luxcore.node_tree if luxcore is not None else None


def _uses_trees(node_tree, names, visited):
    """True if a node tree reaches one of the named trees through Pointer nodes"""
    for node in node_tree.nodes:
        if node.bl_idname != "LuxCoreNodeTreePointer":
            continue
        target = node.node_tree
        if target is None:
            continue
        target_name = target.name_full
        if target_name in names:
            return True
        if target_name not in visited:
            visited.add(target_name)
            if _uses_trees(target, names, visited):
                return True
    return False


def flush():
    """Generate the EEVEE nodes of the materials marked so far; return how many were processed"""
    global _flushing
    if _flushing or not (_dirty_trees or _dirty_materials):
        return 0
    _flushing = True
    try:
        trees = set(_dirty_trees)
        names = set(_dirty_materials)
        _dirty_trees.clear()
        _dirty_materials.clear()

        materials = {}
        if trees:
            matched = set()
            for material in bpy.data.materials:
                node_tree = _luxcore_tree(material)
                if node_tree is not None and node_tree.name_full in trees:
                    materials[material.name_full] = material
                    matched.add(node_tree.name_full)
            unmatched = trees - matched
            if unmatched:
                # Texture or material trees used through Pointer nodes
                for material in bpy.data.materials:
                    node_tree = _luxcore_tree(material)
                    if node_tree is not None and _uses_trees(node_tree, unmatched, set()):
                        materials[material.name_full] = material
        for name in names:
            material = bpy.data.materials.get(name)
            if material is not None:
                materials[material.name_full] = material

        from . import eevee_nodes
        count = 0
        for material in materials.values():
            if not is_eligible(material):
                continue
            status = eevee_nodes.generate(material)
            if status == "skipped_handmade":
                # Bypass the update callback of the property
                material.luxcore["sync_eevee_nodes"] = False
                LuxCoreErrorLog.add_warning(
                    f'Material "{material.name}": the EEVEE nodes were edited by hand, "Keep EEVEE Nodes '
                    'in Sync" was switched off (Update EEVEE Nodes overwrites them)',
                    obj_name=material.name)
            count += 1
        return count
    finally:
        _flushing = False


def enable(material):
    """Switch the sync on and generate the EEVEE nodes now, if the Cycles tree may be overwritten.

    Returns the status of eevee_nodes.generate(), or "skipped_handmade" (the
    sync stays off) or "skipped_library".
    """
    from . import eevee_nodes
    if material.library:
        return "skipped_library"
    if not eevee_nodes.is_ours(material):
        return "skipped_handmade"
    material.luxcore["sync_eevee_nodes"] = True
    status = eevee_nodes.generate(material)
    discard(material)
    return status


# ID property of the hidden materials that keep the original Cycles nodes
BACKUP_PROPERTY = "luxcore_cycles_backup"


def is_backup(material):
    """A hidden copy made by adopt(), kept only for its original Cycles nodes"""
    return bool(material.get(BACKUP_PROPERTY, False))


def backup_name(material):
    name = "." + material.name + ".cycles_original"
    # Blender names are limited to 63 bytes
    while len(name.encode("utf-8")) > 63:
        name = name[:-1]
    return name


def _backup_cycles_nodes(material):
    """A hidden copy of the material that keeps its current Cycles nodes"""
    name = backup_name(material)
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing
    backup = material.copy()
    backup.name = name
    backup.use_fake_user = True
    backup.luxcore.node_tree = None
    backup.luxcore["use_cycles_nodes"] = True
    backup.luxcore["sync_eevee_nodes"] = False
    backup.luxcore.eevee_nodes_signature = ""
    backup[BACKUP_PROPERTY] = True
    return backup


def adopt(material, backup=True):
    """Take over the Cycles tree of a material that was just converted to a LuxCore tree.

    The original Cycles nodes count as generated, so EEVEE keeps showing them
    exactly until the LuxCore tree is first edited; then the generated
    approximation replaces them. With backup, a hidden copy of the material
    (".<name>.cycles_original") keeps the original nodes.
    """
    from . import eevee_nodes
    if backup and material.node_tree is not None:
        _backup_cycles_nodes(material)
    material.luxcore["sync_eevee_nodes"] = True
    material.luxcore.eevee_nodes_signature = eevee_nodes.tree_signature(material.node_tree)
    # Building the LuxCore tree marked it many times
    discard(material)
