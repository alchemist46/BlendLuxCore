"""Edge-adaptive upscale of the viewport film (Pixel Size > 1)

A port of the EASU pass of AMD FidelityFX Super Resolution 1 to a Blender
GPU shader. Only uses the gpu module, so it can be built and tested on its own.
"""

import gpu

# The EASU kernel below is derived from ffx_fsr1.h of FidelityFX Super
# Resolution 1.0:
#
# Copyright (c) 2021 Advanced Micro Devices, Inc. All rights reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

_VERTEX_SOURCE = """
void main()
{
    texCoord_interp = texCoord;
    gl_Position = ModelViewProjectionMatrix * vec4(pos.xy, 0.0, 1.0);
}
"""

_FRAGMENT_SOURCE = """
/* The film is linear and can be far above 1: the kernel runs on the reversible
 * tone map c / (1 + max(c)), as AMD advises for HDR input, so that one bright
 * texel does not decide the edge direction or ring. */
vec4 easu_fetch(ivec2 p, ivec2 size)
{
    vec4 c = texelFetch(image, clamp(p, ivec2(0), size - 1), 0);
    c.rgb = max(c.rgb, vec3(0.0));
    c.rgb *= 1.0 / (1.0 + max(max(c.r, c.g), c.b));
    return c;
}

/* Luma times 2 */
float easu_luma(vec4 c)
{
    return c.b * 0.5 + (c.r * 0.5 + c.g);
}

/* Accumulate the direction and the length of the edge around one of the four
 * nearest texels (c), from the '+' of its neighbours:
 *     a
 *   b c d
 *     e
 * w is the bilinear weight of c. */
void easu_set(inout vec2 dir, inout float len, float w,
              float lA, float lB, float lC, float lD, float lE)
{
    float dc = lD - lC;
    float cb = lC - lB;
    float lenX = 1.0 / max(max(abs(dc), abs(cb)), 1e-8);
    float dirX = lD - lB;
    dir.x += dirX * w;
    lenX = clamp(abs(dirX) * lenX, 0.0, 1.0);
    lenX *= lenX;
    len += lenX * w;

    float ec = lE - lC;
    float ca = lC - lA;
    float lenY = 1.0 / max(max(abs(ec), abs(ca)), 1e-8);
    float dirY = lE - lA;
    dir.y += dirY * w;
    lenY = clamp(abs(dirY) * lenY, 0.0, 1.0);
    lenY *= lenY;
    len += lenY * w;
}

/* One tap of the lanczos-like kernel, stretched along the edge */
void easu_tap(inout vec4 aC, inout float aW, vec2 off, vec2 dir, vec2 len2,
              float lob, float clp, vec4 c)
{
    vec2 v = vec2(off.x * dir.x + off.y * dir.y,
                  off.y * dir.x - off.x * dir.y);
    v *= len2;
    float d2 = min(dot(v, v), clp);
    /* (25/16 * (2/5 * x^2 - 1)^2 - (25/16 - 1)) * (lob * x^2 - 1)^2 */
    float wB = 2.0 / 5.0 * d2 - 1.0;
    float wA = lob * d2 - 1.0;
    wB *= wB;
    wA *= wA;
    wB = 25.0 / 16.0 * wB - (25.0 / 16.0 - 1.0);
    float w = wB * wA;
    aC += c * w;
    aW += w;
}

void main()
{
    ivec2 size = textureSize(image, 0);
    vec2 pp = texCoord_interp * vec2(size) - 0.5;
    vec2 fp = floor(pp);
    pp -= fp;
    ivec2 p = ivec2(fp);

    /* 12 taps around the four nearest texels f, g, j, k:
     *     b c
     *   e f g h
     *   i j k l
     *     n o
     */
    vec4 b = easu_fetch(p + ivec2(0, -1), size);
    vec4 c = easu_fetch(p + ivec2(1, -1), size);
    vec4 e = easu_fetch(p + ivec2(-1, 0), size);
    vec4 f = easu_fetch(p, size);
    vec4 g = easu_fetch(p + ivec2(1, 0), size);
    vec4 h = easu_fetch(p + ivec2(2, 0), size);
    vec4 i = easu_fetch(p + ivec2(-1, 1), size);
    vec4 j = easu_fetch(p + ivec2(0, 1), size);
    vec4 k = easu_fetch(p + ivec2(1, 1), size);
    vec4 l = easu_fetch(p + ivec2(2, 1), size);
    vec4 n = easu_fetch(p + ivec2(0, 2), size);
    vec4 o = easu_fetch(p + ivec2(1, 2), size);

    float bL = easu_luma(b);
    float cL = easu_luma(c);
    float eL = easu_luma(e);
    float fL = easu_luma(f);
    float gL = easu_luma(g);
    float hL = easu_luma(h);
    float iL = easu_luma(i);
    float jL = easu_luma(j);
    float kL = easu_luma(k);
    float lL = easu_luma(l);
    float nL = easu_luma(n);
    float oL = easu_luma(o);

    vec2 dir = vec2(0.0);
    float len = 0.0;
    easu_set(dir, len, (1.0 - pp.x) * (1.0 - pp.y), bL, eL, fL, gL, jL);
    easu_set(dir, len, pp.x * (1.0 - pp.y), cL, fL, gL, hL, kL);
    easu_set(dir, len, (1.0 - pp.x) * pp.y, fL, iL, jL, kL, nL);
    easu_set(dir, len, pp.x * pp.y, gL, jL, kL, lL, oL);

    /* Normalize the direction, no direction where there is no gradient */
    float dirR = dot(dir, dir);
    bool zro = dirR < 1.0 / 32768.0;
    dirR = zro ? 1.0 : inversesqrt(dirR);
    dir.x = zro ? 1.0 : dir.x;
    dir *= dirR;

    /* {0 to 2} to {0 to 1}, shaped with a square */
    len = len * 0.5;
    len *= len;
    /* Stretch the kernel from 1 on a horizontal or vertical edge to sqrt(2)
     * on a diagonal one */
    float stretch = dot(dir, dir) / max(abs(dir.x), abs(dir.y));
    /* Anisotropic length after the rotation: x from 1 to stretch along an
     * edge, y from 1 to 0.5 across it */
    vec2 len2 = vec2(1.0 + (stretch - 1.0) * len, 1.0 - 0.5 * len);
    /* The negative lobe, and the window clipped at its end */
    float lob = 0.5 + ((1.0 / 4.0 - 0.04) - 0.5) * len;
    float clp = 1.0 / lob;

    vec4 aC = vec4(0.0);
    float aW = 0.0;
    easu_tap(aC, aW, vec2(0.0, -1.0) - pp, dir, len2, lob, clp, b);
    easu_tap(aC, aW, vec2(1.0, -1.0) - pp, dir, len2, lob, clp, c);
    easu_tap(aC, aW, vec2(-1.0, 1.0) - pp, dir, len2, lob, clp, i);
    easu_tap(aC, aW, vec2(0.0, 1.0) - pp, dir, len2, lob, clp, j);
    easu_tap(aC, aW, vec2(0.0, 0.0) - pp, dir, len2, lob, clp, f);
    easu_tap(aC, aW, vec2(-1.0, 0.0) - pp, dir, len2, lob, clp, e);
    easu_tap(aC, aW, vec2(1.0, 1.0) - pp, dir, len2, lob, clp, k);
    easu_tap(aC, aW, vec2(2.0, 1.0) - pp, dir, len2, lob, clp, l);
    easu_tap(aC, aW, vec2(2.0, 0.0) - pp, dir, len2, lob, clp, h);
    easu_tap(aC, aW, vec2(1.0, 0.0) - pp, dir, len2, lob, clp, g);
    easu_tap(aC, aW, vec2(1.0, 2.0) - pp, dir, len2, lob, clp, o);
    easu_tap(aC, aW, vec2(0.0, 2.0) - pp, dir, len2, lob, clp, n);

    /* Deringing: within the range of the four nearest texels */
    vec4 min4 = min(min(f, g), min(j, k));
    vec4 max4 = max(max(f, g), max(j, k));
    vec4 col = clamp(aC / aW, min4, max4);

    /* Back from the tone map */
    col.rgb *= 1.0 / max(1.0 - max(max(col.r, col.g), col.b), 1.0 / 65536.0);

    /* As the built-in IMAGE shader (blender_srgb_to_framebuffer_space()) */
    if (srgbTarget) {
        vec3 cs = max(col.rgb, vec3(0.0));
        vec3 c1 = cs * (1.0 / 12.92);
        vec3 c2 = pow((cs + 0.055) * (1.0 / 1.055), vec3(2.4));
        col.rgb = mix(c1, c2, step(vec3(0.04045), cs));
    }
    fragColor = col;
}
"""

_shader = None
_shader_error = None


def easu_shader():
    """The upscale shader (same inputs as the built-in IMAGE shader: pos,
    texCoord and the image sampler), or None where it can't be built"""
    global _shader, _shader_error
    if _shader is not None or _shader_error is not None:
        return _shader
    try:
        interface = gpu.types.GPUStageInterfaceInfo("luxcore_easu_interface")
        interface.smooth("VEC2", "texCoord_interp")

        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant("MAT4", "ModelViewProjectionMatrix")
        info.push_constant("BOOL", "srgbTarget")
        info.sampler(0, "FLOAT_2D", "image")
        info.vertex_in(0, "VEC2", "pos")
        info.vertex_in(1, "VEC2", "texCoord")
        info.vertex_out(interface)
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source(_VERTEX_SOURCE)
        info.fragment_source(_FRAGMENT_SOURCE)
        _shader = gpu.shader.create_from_info(info)
    except Exception as error:
        _shader_error = error
        print("LuxCore viewport: the edge-adaptive upscale is not available, "
              "using the linear filter:", error)
    return _shader
