from typing import NamedTuple
from bpy.props import BoolProperty
from bpy.types import PropertyGroup


class AOV(NamedTuple):
    """
    One AOV: its property, its Blender render pass and its LuxCore film output.
    This table drives the properties below, the pass registration in the engine
    and the film output import in draw/final.py.
    """
    # Property name. The LuxCore film output name is the uppercase version.
    prop: str
    label: str
    description: str
    # Channel ids of the Blender pass. Blender can only handle passes with
    # 1, 3 or 4 elements.
    channels: str = "RGB"
    # Socket type in the compositor, one of "COLOR", "VECTOR", "VALUE"
    pass_type: str = "COLOR"
    # Elements per pixel in the LuxCore film output, if not the pass depth
    film_depth: int = 0
    film_dtype: str = "float32"
    # Scale the pass so the values range from 0 to 1
    normalize: bool = False
    is_id: bool = False

    @property
    def output(self):
        return self.prop.upper()

    @property
    def pass_name(self):
        # Depth is pre-defined by Blender and not uppercase
        return "Depth" if self.prop == "depth" else self.output

    @property
    def pass_depth(self):
        return len(self.channels)


# In the order of the passes in the compositor
AOVS = (
    # Basic Information
    AOV("rgb", "RGB", "Raw RGB values (HDR)"),
    AOV("rgba", "RGBA", "Raw RGBA values (HDR)", channels="RGBA"),
    AOV("alpha", "Alpha", "Alpha value [0..1]", channels="A", pass_type="VALUE"),
    AOV("depth", "Depth", "Distance from camera (Z-Pass)", channels="Z", pass_type="VALUE",
        normalize=True),
    AOV("albedo", "Albedo", "Unlit material/texture colors"),

    # Material/Object Information
    AOV("material_id", "Material ID",
        "Material ID (1 value per material, use the ID Mask Node in compositing nodes to extract a mask)",
        channels="X", pass_type="VALUE", film_dtype="uint32", is_id=True),
    AOV("material_id_color", "Material ID Color", "Material ID (1 color per material, anti-aliased)"),
    AOV("object_id", "Object ID",
        "Object ID (1 value per object, use the ID Mask Node in compositing nodes to extract a mask)",
        channels="X", pass_type="VALUE", film_dtype="uint32", is_id=True),

    # Light Information
    AOV("emission", "Emission", "Emission R, G, B"),
    AOV("caustic", "Caustic",
        "Light that was refracted/reflected by glossy or specular materials. "
        "This AOV is only available when using Path + Light Tracing. "
        "Note that it only contains light traced caustics, not caustics "
        "computed by the PhotonGI caustics cache"),

    # Direct Light Information
    AOV("direct_diffuse", "Combined", "Direct Diffuse R, G, B"),
    AOV("direct_diffuse_reflect", "Reflect", ""),
    AOV("direct_diffuse_transmit", "Transmit", ""),
    AOV("direct_glossy", "Combined", "Direct Glossy R, G, B (e.g. glossy, metal materials)"),
    AOV("direct_glossy_reflect", "Reflect", ""),
    AOV("direct_glossy_transmit", "Transmit", ""),

    # Indirect Light Information
    AOV("indirect_diffuse", "Combined", "Indirect diffuse R, G, B"),
    AOV("indirect_diffuse_reflect", "Reflect", ""),
    AOV("indirect_diffuse_transmit", "Transmit", ""),
    AOV("indirect_glossy", "Combined", "Indirect glossy R, G, B (e.g. glossy, metal materials)"),
    AOV("indirect_glossy_reflect", "Reflect", ""),
    AOV("indirect_glossy_transmit", "Transmit", ""),
    AOV("indirect_specular", "Combined", "Indirect specular R, G, B (e.g. glass, mirror materials)"),
    AOV("indirect_specular_reflect", "Reflect", ""),
    AOV("indirect_specular_transmit", "Transmit", ""),

    # Geometry Information
    AOV("position", "Position", "World X, Y, Z", channels="XYZ", pass_type="VECTOR"),
    AOV("shading_normal", "Shading Normal", "Normal vector X, Y, Z with mesh smoothing",
        channels="XYZ", pass_type="VECTOR"),
    AOV("avg_shading_normal", "Avg. Shading Normal", "Same as shading normal, but with anti-aliasing",
        channels="XYZ", pass_type="VECTOR"),
    AOV("geometry_normal", "Geometry Normal", "Normal vector X, Y, Z without mesh smoothing",
        channels="XYZ", pass_type="VECTOR"),
    # LuxCore's UV output has 2 elements, the third one in Blender is a mask
    # that is 1 where a UV map exists
    AOV("uv", "UV", "Texture coordinates U, V", channels="UVA", pass_type="VECTOR", film_depth=2),

    # Shadow Information
    AOV("direct_shadow_mask", "Direct", "Mask containing shadows by direct light",
        channels="X", pass_type="VALUE"),
    AOV("indirect_shadow_mask", "Indirect", "Mask containing shadows by indirect light",
        channels="X", pass_type="VALUE"),

    # Render Information
    AOV("raycount", "Raycount", "Ray count per pixel (normalized so the values range from 0 to 1)",
        channels="X", pass_type="VALUE", normalize=True),
    AOV("samplecount", "Samplecount", "Samples per pixel (normalized so the values range from 0 to 1)",
        channels="X", pass_type="VALUE", film_dtype="uint32", normalize=True),
    AOV("convergence", "Convergence",
        "The convergence per pixel. The lower the value, the more converged "
        "(noise-free) the pixel is. If the convergence halt condition is "
        "enabled, the render is stopped once all pixels fall below the "
        "convergence threshold",
        channels="X", pass_type="VALUE"),
    AOV("noise", "Noise", "The noise amount per pixel. High values mean more noise, low values less noise",
        channels="X", pass_type="VALUE"),
    AOV("irradiance", "Irradiance", "Surface irradiance"),
)


# Attached to view layer
class LuxCoreAOVSettings(PropertyGroup):
    __annotations__ = {
        aov.prop: BoolProperty(name=aov.label, default=False, description=aov.description)
        for aov in AOVS
    }
