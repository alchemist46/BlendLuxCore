from bpy.app.handlers import persistent
from ..utils import temperature


@persistent
def handler(*_):
    # Store the temperature normalization of every light, world and Blackbody node, so the
    # file is not taken for one of an older addon version when it is loaded again
    temperature.store_all()
