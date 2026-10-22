"""test_cycles_convert.py with the Principled BSDF read as before pyluxcore 3.0
(Disney, Glass or a mix of both), as with the pyluxcore version the addon installs.

blender -b --factory-startup --python tests/test_cycles_convert_disney.py -- <site-packages>
"""

import os
import runpy
import sys

sys.argv.append("disney")
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_cycles_convert.py"),
               run_name="__main__")
