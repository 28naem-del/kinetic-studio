"""Easing curves, envelopes and deterministic noise. All functions are pure."""
from __future__ import annotations

import math


def clamp(x: float, a: float = 0.0, b: float = 1.0) -> float:
    return a if x < a else b if x > b else x


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def prog(t: float, start: float, dur: float) -> float:
    """Normalised 0..1 progress of t through [start, start+dur]."""
    if dur <= 0:
        return 1.0 if t >= start else 0.0
    return clamp((t - start) / dur)


def linear(t: float) -> float:
    return clamp(t)


def in_quad(t: float) -> float:
    t = clamp(t); return t * t


def out_quad(t: float) -> float:
    t = clamp(t); return 1 - (1 - t) ** 2


def in_out_quad(t: float) -> float:
    t = clamp(t); return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


def in_cubic(t: float) -> float:
    t = clamp(t); return t ** 3


def out_cubic(t: float) -> float:
    t = clamp(t); return 1 - (1 - t) ** 3


def in_out_cubic(t: float) -> float:
    t = clamp(t); return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def out_quart(t: float) -> float:
    t = clamp(t); return 1 - (1 - t) ** 4


def in_expo(t: float) -> float:
    t = clamp(t); return 0.0 if t == 0 else 2 ** (10 * t - 10)


def out_expo(t: float) -> float:
    t = clamp(t); return 1.0 if t == 1 else 1 - 2 ** (-10 * t)


def in_out_expo(t: float) -> float:
    t = clamp(t)
    if t in (0.0, 1.0):
        return t
    return 2 ** (20 * t - 10) / 2 if t < 0.5 else (2 - 2 ** (-20 * t + 10)) / 2


def out_back(t: float, k: float = 1.70158) -> float:
    t = clamp(t) - 1
    return 1 + (k + 1) * t ** 3 + k * t ** 2


def out_elastic(t: float) -> float:
    t = clamp(t)
    if t in (0.0, 1.0):
        return t
    return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * (2 * math.pi) / 3) + 1


def spring(t: float, freq: float = 4.5, damp: float = 6.0) -> float:
    """Damped spring settling to 1. t in seconds (not normalised)."""
    if t <= 0:
        return 0.0
    return 1 - math.exp(-damp * t) * math.cos(2 * math.pi * freq * t)


def smoothstep(e0: float, e1: float, x: float) -> float:
    t = clamp((x - e0) / (e1 - e0) if e1 != e0 else 1.0)
    return t * t * (3 - 2 * t)


def window(t: float, a: float, b: float, fade_in: float, fade_out: float) -> float:
    """1 inside [a,b] with linear-ish smooth fades at both ends."""
    if t < a or t > b:
        return 0.0
    i = smoothstep(a, a + fade_in, t) if fade_in > 0 else 1.0
    o = 1 - smoothstep(b - fade_out, b, t) if fade_out > 0 else 1.0
    return min(i, o)


EASINGS = {
    "linear": linear, "in_quad": in_quad, "out_quad": out_quad, "in_out_quad": in_out_quad,
    "in_cubic": in_cubic, "out_cubic": out_cubic, "in_out_cubic": in_out_cubic,
    "out_quart": out_quart, "in_expo": in_expo, "out_expo": out_expo, "in_out_expo": in_out_expo,
    "out_back": out_back, "out_elastic": out_elastic,
}


def _hash(i: int, seed: int) -> float:
    n = (i * 374761393 + seed * 668265263) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFFFF) / 0xFFFFFF * 2 - 1


def noise1(x: float, seed: int = 0) -> float:
    """Smooth deterministic value noise in [-1, 1]."""
    i = math.floor(x)
    f = x - i
    u = f * f * (3 - 2 * f)
    return lerp(_hash(i, seed), _hash(i + 1, seed), u)


def fbm(x: float, seed: int = 0, octaves: int = 3) -> float:
    v, a, fr, norm = 0.0, 1.0, 1.0, 0.0
    for o in range(octaves):
        v += a * noise1(x * fr, seed + o * 17)
        norm += a
        a *= 0.5
        fr *= 2.0
    return v / norm
