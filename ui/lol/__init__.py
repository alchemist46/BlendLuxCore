from ... import utils
from . import panel

classes = (
    panel.VIEW3D_PT_LUXCORE_ONLINE_LIBRARY,
    panel.VIEW3D_PT_LUXCORE_ONLINE_LIBRARY_DOWNLOADS,
    panel.VIEW3D_PT_LUXCORE_ONLINE_LIBRARY_LOCAL,
    panel.VIEW3D_PT_LUXCORE_ONLINE_LIBRARY_SCAN_RESULT,
)


def register():
    utils.register_module("UI.Lol", classes)


def unregister():
    utils.unregister_module("UI.Lol", classes)
