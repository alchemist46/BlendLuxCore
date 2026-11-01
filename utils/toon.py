"""Cycles' Toon BSDF as the parameters of LuxCore's toon material (pyluxcore 3.0)"""

import math

# The ramp steps of a smoothed diffuse toon (the material has 8 at most)
FALLOFF_STEPS = 7


def highlight_roughness(size):
    """The roughness of the toon highlight that is full within the angle
    size * pi/2 between the normal and the half vector: the inverse of the
    core's ToonMaterial_HighlightAngle(), where the GGX distribution of
    alpha = roughness^2 falls to half its peak,
    tan^2 = alpha^2 (sqrt(2) - 1) / (1 - sqrt(2) alpha^2)"""
    angle = min(max(size, 0.0), 1.0) * math.pi / 2
    if angle >= math.pi / 2:
        return 1.0
    t2 = math.tan(angle) ** 2
    alpha2 = t2 / (math.sqrt(2) - 1 + math.sqrt(2) * t2)
    return alpha2 ** 0.25


def cycles_toon_definitions(component, size, smooth):
    """The toon material definitions of a Cycles Toon BSDF, without its colour.

    Cycles lights a direction within size * pi/2 of the normal (diffuse) or of
    the mirror direction (glossy) fully, then falls off linearly over
    smooth * pi/2, and normalizes the lobe over the cone it covers: the
    direct light is intensity / (2 pi (1 - cos(cone))) per unit of radiance,
    where LuxCore's toon gives factor / pi. The diffuse lobe is a ramp on the
    cosine of the light direction: one step, or FALLOFF_STEPS steps along
    Cycles' smooth falloff; the glossy one is the toon specular, on
    the angle between the normal and the half vector, half the angle between
    the mirror and the light directions. The indirect light of LuxCore's toon
    is the Lambert one of kd (Cycles samples the toon lobe).

    Returns (definitions, colour key, colour scale): the Color socket goes in
    the colour key ("kd" or "ks"), multiplied by the scale. The definitions
    are the factor ramp of the core (ramp.thresholds, ramp.factors, see
    ramp_keys()) and turn lightcenter off: Cycles' toon steps blur with the
    size of the lights.
    """
    size = min(max(size, 0.0), 1.0)
    smooth = min(max(smooth, 0.0), 1.0)
    cone = min(size + smooth, 1.0) * math.pi / 2
    factor = 1.0 / (2.0 * max(1.0 - math.cos(cone), 1e-4))

    if component == "GLOSSY":
        return {
            "kd": [0, 0, 0],
            "ramp.thresholds": [0.0],
            "ramp.factors": [0.0],
            "specular.roughness": highlight_roughness(size / 2),
            "specular.smooth": smooth / 2,
            "lightcenter": False,
        }, "ks", factor

    # kd is clamped to 1 by the material: the normalization is in the ramp
    max_angle = size * math.pi / 2
    smooth_angle = cone - max_angle
    if smooth_angle <= 0:
        return {
            "ks": [0, 0, 0],
            "ramp.thresholds": [math.cos(cone)],
            "ramp.factors": [factor],
            "lightcenter": False,
        }, "kd", 1.0

    # Cycles' falloff, linear in the angle, through STEPS points: a step of
    # factor / STEPS at the cosine of each, each blended over the smallest gap
    # between them (the ramp is then close to piecewise linear between them)
    cosines = [math.cos(max_angle + smooth_angle * k / FALLOFF_STEPS) for k in range(FALLOFF_STEPS, -1, -1)]
    return {
        "ks": [0, 0, 0],
        "ramp.thresholds": cosines[:-1],
        "ramp.factors": [factor * (j + 1) / FALLOFF_STEPS for j in range(FALLOFF_STEPS)],
        "ramp.smooth": min(b - a for a, b in zip(cosines[:-1], cosines[1:])),
        "lightcenter": False,
    }, "kd", 1.0


def ramp_keys(definitions):
    """The (position, color) keys of the colour ramp of the Toon node with the
    look of a factor ramp of the definitions: grey keys at its thresholds, after
    a black one at 0 (a factor ramp is 0 below its first threshold, a colour
    ramp has the colour of its first key there)"""
    keys = [(t, (f, f, f)) for t, f in zip(definitions["ramp.thresholds"], definitions["ramp.factors"])]
    if keys[0][0] > 0:
        keys.insert(0, (0.0, (0.0, 0.0, 0.0)))
    return keys
