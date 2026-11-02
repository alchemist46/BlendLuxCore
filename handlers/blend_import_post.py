from bpy.app.handlers import persistent
from ..utils import temperature

# Data copied into this file; linked data (KEEP_LINKED) cannot be changed and is
# handled at export (utils.temperature.get())
_LOCAL_ACTIONS = {"MAKE_LOCAL", "COPY_LOCAL"}


@persistent
def handler(context, *_):
    # Appended lights, worlds and Blackbody nodes from a file of an older addon version
    # keep their Max temperature normalization
    ids = [item.id for item in context.import_items
           if item.id is not None and item.append_action in _LOCAL_ACTIONS]
    if ids:
        temperature.set_legacy(ids)
