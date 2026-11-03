import bpy
from bpy.props import PointerProperty, BoolProperty, FloatProperty, IntProperty, EnumProperty, StringProperty
from bpy.types import PropertyGroup, Image
from .imagepipeline import LuxCoreImagepipeline
from .image_user import LuxCoreImageUser

CLIPPING_PLANE_DESC = (
    "The arbitrary clipping plane is used to clip the scene at any position and angle. "
    "It is recommended to use a plane object for better preview. "
    "The clipping plane object will not be exported"
)

SHUTTER_TIME_DESC = (
    "Amount of frames between shutter open and shutter close, higher values lead to more blur. \n"
    "A value of 1.0 blurs over the length of 1 frame, a value of 2.0 over 2 frames etc"  # no dot, Blender adds it
)

AUTO_VOLUME_DESC = "Use the exterior volume of the object in the middle of the film as camera volume"


class LuxCoreMotionBlur(PropertyGroup):
    enable: BoolProperty(name="Enable Motion Blur", default=False)
    object_blur: BoolProperty(name="Object", default=True, description="Blur moving objects")
    camera_blur: BoolProperty(name="Camera", default=False, description="Blur if camera moves")
    shutter: FloatProperty(name="Shutter (frames)", default=0.1, min=0, soft_max=2, description=SHUTTER_TIME_DESC)
    # Note: Embree allows a maximum of 129 motion steps
    steps: IntProperty(name="Steps", default=2, min=2, soft_max=20, max=129, description="Number of substeps")


class LuxCoreBokeh(PropertyGroup):
    non_uniform: BoolProperty(name="Non-Uniform Bokeh", default=False)
    blades: IntProperty(name="Blades", default=8, min=3,
                              description="Number of aperture blades")
    anisotropy: FloatProperty(name="Anisotropy", default=0, min=-1, max=1,
                                    description="Values > 0 expand the bokeh horizontally, "
                                                "values < 0 expand the bokeh vertically")
    distribution_items = [
        ("UNIFORM", "Uniform", "", 0),
        ("EXPONENTIAL", "Exponential", "", 1),
        ("INVERSEEXPONENTIAL", "Inverse Exponential", "", 2),
        ("GAUSSIAN", "Gaussian", "", 3),
        ("INVERSEGAUSSIAN", "Inverse Gaussian", "", 4),
        ("TRIANGULAR", "Triangular", "", 5),
        ("CUSTOM", "Image", "Use a custom image as bokeh distribution. Note that the image must not be totally black", 6),
    ]
    distribution: EnumProperty(name="Bokeh Distribution", items=distribution_items, default="UNIFORM")
    # The power affects only EXPONENTIAL and INVERSEEXPONENTIAL distributions
    power: IntProperty(name="Power", default=3, min=1,
                       description="Higher values lead to more pronounced exponential effects")

    def update_image(self, context):
        self.image_user.update(self.image)

    image: PointerProperty(name="Image", type=Image, update=update_image)
    image_user: PointerProperty(type=LuxCoreImageUser)

    # The aperture shape (pyluxcore 3.0+)
    rotation: FloatProperty(name="Rotation", default=0, subtype="ANGLE", soft_min=-3.14159, soft_max=3.14159,
                            description="Rotation of the aperture blades")
    roundness: FloatProperty(name="Roundness", default=0, min=0, max=1, subtype="FACTOR",
                             description="Rounds the blades of the aperture polygon toward a circle")
    obstruction: FloatProperty(name="Obstruction", default=0, min=0, max=0.95, subtype="FACTOR",
                               description="Radius of a central obstruction, as a fraction of the aperture: "
                                           "the ring bokeh of a mirror (catadioptric) lens")


# The built-in lens designs of the realistic camera: a design keeps its f-number
# and its field of view (degrees) at any focal length
LENS_PRESET_ITEMS = [
    ("dgauss", "Double-Gauss f/2, \u00b122\u00b0", "The classic standard lens (US patent 2,673,491): sharp, "
                                                "strongly vignetted wide open. 50mm by default", 0),
    ("triplet", "Cooke Triplet f/5, \u00b120\u00b0", "The three-element anastigmat of 1893: sharp in the center, "
                                                  "softer and curved toward the corners. 50mm by default", 2),
    ("tessar", "Tessar f/4.5, \u00b120\u00b0", "The four-element Tessar of the folding and early 35mm cameras. "
                                              "50mm by default", 3),
    ("heliar", "Heliar f/5, \u00b110\u00b0", "The five-element Heliar, a very sharp portrait lens of the large "
                                            "format cameras. 120mm by default", 4),
    ("petzval", "Petzval f/1.4, \u00b17\u00b0", "A fast Petzval portrait lens: sharp in the center, the swirly "
                                               "bokeh of its curved field beyond its design field. 85mm by default", 5),
    ("telephoto", "Telephoto f/5.6, \u00b110\u00b0", "A five-element telephoto, shorter than its focal length. "
                                                    "200mm by default", 6),
    ("retrofocus", "Retrofocus Wide Angle f/4, \u00b150\u00b0", "A retrofocus wide angle, a strong barrel distortion "
                                                              "(33% at the edge of its field). 27mm by default", 7),
    ("fisheye", "Fisheye f/1.8, \u00b182\u00b0", "A fisheye: a round image, 2.35 times its focal length across. "
                                                "10mm by default", 8),
    ("FILE", "Lens File", "A lens prescription file: one surface per line, "
                          "radius thickness nd vd semi-aperture (mm), from the front; "
                          "a radius of 0 is the aperture stop. The 4-column files of pbrt work too", 1),
]

# The usual focal length of each design (mm, the one of "Use Camera Focal Length" off) and the image height at the
# edge of its field per mm of focal length (from a trace of the prescription), for the coverage of the frame
LENS_PRESET_DATA = {
    "dgauss": (50.36, 0.3983),
    "triplet": (50.0, 0.3627),
    "tessar": (50.0, 0.3731),
    "heliar": (120.0, 0.1763),
    "petzval": (85.0, 0.1226),
    "telephoto": (200.0, 0.1763),
    "retrofocus": (27.0, 0.8011),
    "fisheye": (10.0, 1.1773),
}


class LuxCoreLens(PropertyGroup):
    """The thin lens effects and the realistic lens of a perspective camera (pyluxcore 3.0+)"""
    # Thin lens effects, all off at 0
    vignetting_distance: FloatProperty(name="Barrel Distance", default=0, min=0, soft_max=0.1, subtype="DISTANCE",
                                       description="Distance of the lens barrel opening in front of the lens: "
                                                   "it clips the out-of-focus bokeh into cat's eyes toward the "
                                                   "corners (optical vignetting, with Depth of Field). 0 disables it")
    vignetting_scale: FloatProperty(name="Barrel Radius", default=1, min=0.001, soft_max=3,
                                    description="Radius of the barrel opening, in aperture radii")
    distortion_k1: FloatProperty(name="Distortion K1", default=0, soft_min=-0.5, soft_max=0.5,
                                 description="Radial distortion at the image height squared (1 at the corner): "
                                             "positive is barrel, negative pincushion")
    distortion_k2: FloatProperty(name="K2", default=0, soft_min=-0.5, soft_max=0.5,
                                 description="Radial distortion at the image height to the 4th power")
    aberration_spherical: FloatProperty(name="Spherical", default=0, soft_min=-0.2, soft_max=0.2,
                                        description="Spherical aberration: the focus of the aperture's edge moves "
                                                    "by this fraction of the focus distance (with Depth of Field)")
    aberration_coma: FloatProperty(name="Coma", default=0, soft_min=-1, soft_max=1,
                                   description="Coma: comet shaped blur growing toward the corners "
                                               "(with Depth of Field)")
    aberration_astigmatism: FloatProperty(name="Astigmatism", default=0, soft_min=-0.2, soft_max=0.2,
                                          description="Astigmatism: different focus for the radial and the "
                                                      "tangential directions toward the corners "
                                                      "(with Depth of Field)")
    aberration_field_curvature: FloatProperty(name="Field Curvature", default=0, soft_min=-0.2, soft_max=0.2,
                                              description="The focus at the corners moves by this fraction of "
                                                          "the focus distance (with Depth of Field)")
    tilt_x: FloatProperty(name="Tilt X", default=0, subtype="ANGLE", min=-1.396, max=1.396,
                          description="Tilt of the plane of focus about the horizontal axis of the image "
                                      "(tilt-shift, with Depth of Field)")
    tilt_y: FloatProperty(name="Y", default=0, subtype="ANGLE", min=-1.396, max=1.396,
                          description="Tilt of the plane of focus about the vertical axis of the image "
                                      "(tilt-shift, with Depth of Field)")
    dispersion_longitudinal: FloatProperty(name="Longitudinal", default=0, soft_min=-0.1, soft_max=0.1,
                                           description="Axial chromatic aberration: the focus of each wavelength "
                                                       "moves by this fraction of the focus distance, times its "
                                                       "dispersion (with Depth of Field). Adds colour noise")
    dispersion_lateral: FloatProperty(name="Lateral", default=0, soft_min=-0.05, soft_max=0.05,
                                      description="Lateral chromatic aberration: each wavelength scales the image "
                                                  "by 1 + this times its dispersion. Adds colour noise")

    # Realistic lens
    use_realistic: BoolProperty(name="Realistic Lens", default=False,
                                description="Trace the rays through the glass surfaces of a real lens design: "
                                            "vignetting, aberrations, distortion and bokeh come from the lens. "
                                            "Uses the Depth of Field f-stop and focus even when Depth of Field "
                                            "is off. Not supported by the Bidir and Light engines")
    preset: EnumProperty(name="Lens Design", items=LENS_PRESET_ITEMS, default="dgauss")
    lens_file: StringProperty(name="Lens File", subtype="FILE_PATH", default="")
    use_camera_focal_length: BoolProperty(name="Use Camera Focal Length", default=True,
                                          description="Scale the lens design to the focal length of the camera, "
                                                      "else use the focal length of the design")
    dispersion: BoolProperty(name="Dispersion", default=False,
                             description="The glasses refract each wavelength differently: true chromatic "
                                         "aberrations, at the cost of colour noise")


class LuxCoreCameraProps(PropertyGroup):
    use_clipping: BoolProperty(name="Clipping", default=True,
                                description="Use near/far clipping for the LuxCore camera "
                                            "(clipping still affects the Blender OpenGL viewport even if disabled)")
    use_autofocus: BoolProperty(name="Use Autofocus", default=False,
                                 description="Focus on the surface in the center of the film")
    use_clipping_plane: BoolProperty(name="Use Clipping Plane", default=False, description=CLIPPING_PLANE_DESC)
    clipping_plane: PointerProperty(name="Clipping Plane", type=bpy.types.Object, description=CLIPPING_PLANE_DESC)

    motion_blur: PointerProperty(type=LuxCoreMotionBlur)
    imagepipeline: PointerProperty(type=LuxCoreImagepipeline)
    bokeh: PointerProperty(type=LuxCoreBokeh)
    lens: PointerProperty(type=LuxCoreLens)

    volume: PointerProperty(type=bpy.types.NodeTree)
    auto_volume: BoolProperty(name="Auto-Detect Camera Volume", default=True, description=AUTO_VOLUME_DESC)

    # Deprecated properties, we now use the Cycles properties instead.
    # Only kept for backwards compatibility reasons.
    use_dof: BoolProperty(name="Use Depth of Field", default=False)
    fstop: FloatProperty(name="F-Stop", default=2.8)
    
    @classmethod
    def register(cls):
        bpy.types.Camera.luxcore = PointerProperty(
            name="LuxCore Camera Settings",
            description="LuxCore camera settings",
            type=cls,
        )

    @classmethod
    def unregister(cls):
        del bpy.types.Camera.luxcore
