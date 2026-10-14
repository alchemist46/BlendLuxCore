import bpy
from bpy.props import IntProperty, BoolProperty, FloatProperty, EnumProperty

USE_SAMPLES_DESC = (
    "The rendering will stop when the number of samples reaches "
    "the specified value"
)

SAMPLES_DESC = (
    "At least this many samples will be rendered before stopping"
)

USE_LIGHT_PATH_SAMPLES_DESC = (
    "The rendering will stop when the number of light path samples reaches "
    "the specified value"
)

LIGHT_PATH_SAMPLES_DESC = (
    "At least this many light path samples will be rendered before stopping"
)

USE_NOISE_THRESH_DESC = (
    "The rendering will stop when the noise in the image falls "
    "below the specified threshold"
)

NOISE_THRESH_DESC = (
    "Value between 0 and 255. If the noise falls below this value, the rendering is stopped. "
    "Smaller values mean less noise. "
    "It may be hard to reach a smaller noise value than 3"
)
NOISE_THRESH_WARMUP_DESC = (
    "How many samples to render before doing the first convergence test. "
    "Note that the first actual noise check only happens at the second test, after (warmup + step) samples"
)
NOISE_THRESH_STEP_DESC = (
    "How many samples to render between convergence tests. Use smaller values if "
    "your scene renders very slowly, and higher values if it renders very fast"
)
NOISE_THRESH_MODE_DESC = "How the noise of the image is measured"
NOISE_THRESH_MODE_ITEMS = [
    ("CLASSIC", "Classic",
     "Compare two snapshots of the image taken some samples apart and stop when no pixel "
     "changed by more than the threshold (fireflies can delay the end a lot)", 0),
    ("PER_PIXEL", "Per Pixel",
     "Estimate the noise of each pixel, stop sampling the pixels as they converge and stop the "
     "rendering when all of them are done; predictable render times. Used with the Sobol and Random "
     "samplers without tiles (needs a recent pyluxcore build), the classic test otherwise", 1),
]
NOISE_THRESH_PERPIXEL_DESC = (
    "Target noise of the image, as a percentage of the brightness of the pixels (RMS over the image). "
    "Smaller values mean less noise and longer renders. On the test scenes 1% gave about the quality "
    "of a classic threshold of 8, 2% of a classic threshold of 25"
)
NOISE_THRESH_PASSES_DESC = (
    "A pixel that passes this many tests in a row is not tested again until the rendering is "
    "restarted, so single fireflies don't hold up the end of the rendering (0: every pixel has to "
    "pass in the same test, the original behavior)"
)
NOISE_THRESH_TOLERANCE_DESC = (
    "Percentage of the pixels allowed to stay above the threshold when the rendering stops"
)


# Attached to view layer and scene
class LuxCoreHaltConditions(bpy.types.PropertyGroup):
    enable: BoolProperty(name="Enable", default=False)

    use_time: BoolProperty(name="Use Time", default=False)
    time: IntProperty(name="Time (s)", default=600, min=1)

    use_samples: BoolProperty(name="Use Samples", default=True,
                               description=USE_SAMPLES_DESC)
    samples: IntProperty(name="Samples", default=32, min=2, soft_max=16384, 
                          description=SAMPLES_DESC)

    use_light_samples: BoolProperty(name="Use Light Path Samples", default=False,
                                     description=USE_LIGHT_PATH_SAMPLES_DESC)
    light_samples: IntProperty(name="Light Path Samples", default=100, min=1,
                                description=LIGHT_PATH_SAMPLES_DESC)

    # Noise threshold
    use_noise_thresh: BoolProperty(name="Use Noise Threshold", default=False,
                                    description=USE_NOISE_THRESH_DESC)
    noise_thresh: IntProperty(name="Noise Threshold", default=5, min=0, soft_min=3, max=255,
                               description=NOISE_THRESH_DESC)
    noise_thresh_warmup: IntProperty(name="Warmup Samples", default=64, min=1,
                                      description=NOISE_THRESH_WARMUP_DESC)
    noise_thresh_step: IntProperty(name="Test Step Samples", default=64, min=1, soft_min=16,
                                    description=NOISE_THRESH_STEP_DESC)
    noise_thresh_mode: EnumProperty(name="Test", items=NOISE_THRESH_MODE_ITEMS, default="CLASSIC",
                                     description=NOISE_THRESH_MODE_DESC)
    noise_thresh_perpixel: FloatProperty(name="Noise Level", default=1, min=0.01, soft_min=0.5,
                                          max=100, soft_max=10, precision=2, step=10,
                                          subtype="PERCENTAGE", description=NOISE_THRESH_PERPIXEL_DESC)
    noise_thresh_passes: IntProperty(name="Sticky Passes", default=0, min=0, soft_max=4, max=255,
                                      description=NOISE_THRESH_PASSES_DESC)
    noise_thresh_tolerance: FloatProperty(name="Tolerance", default=0, min=0, max=100, soft_max=1,
                                           precision=3, step=1, subtype="PERCENTAGE",
                                           description=NOISE_THRESH_TOLERANCE_DESC)

    def is_enabled(self):
        return self.enable and (self.use_time or self.use_samples or self.use_noise_thresh)
