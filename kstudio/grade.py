"""Colour grade: lift/gamma/gain, S tone curve, highlight shoulder, saturation, split toning,
vignette strength and bloom halation tint, compiled into small cached lookup tables.

A grade is a frozen :class:`Grade`; :func:`resolve_grade` accepts a preset name (``GRADES``), a
dict (``{"base": "ink", "saturation": 0.9}``), a Grade, or None / "none" (identity).

Per-channel maths in display (sRGB-encoded) space, x in [0, 1]:
    x = shoulder(x)                                   soft knee above ``shoulder`` (bloom > 1.0)
    x = (gain * (x + lift * (1 - x))) ** (1 / gamma)  lift / gamma / gain (MLT / Resolve style)
    x = mix(x, S(x), contrast)                        S curve (smoothstep about ``pivot``)
then across channels, with Rec.709 luma L:
    x = L + saturation * (x - L)
    x += shadow_amount * (1 - L)^2 * (shadow_tint - luma(shadow_tint))       luminance-neutral
    x += highlight_amount * L^2 * (highlight_tint - luma(highlight_tint))     split toning

The compositor evaluates the per-channel part through a 256-knot table indexed by sqrt(linear)
with first-order interpolation (exact to < 0.1 code value), so a grade costs a few vector ops.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from functools import lru_cache
from typing import Any

import numpy as np

RGB = tuple[float, float, float]
LUMA = (0.2126, 0.7152, 0.0722)
LIN_MAX = 2.0  # headroom of the linear domain the compositor tabulates (bloom can exceed 1.0)


def _rgb(v: Any) -> RGB:
    if isinstance(v, str):
        h = v.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)
    if isinstance(v, (int, float)):
        return (float(v),) * 3  # type: ignore[return-value]
    a = tuple(float(x) for x in v)
    if len(a) != 3:
        raise ValueError(f"expected 3 components, got {v!r}")
    return a  # type: ignore[return-value]


@dataclass(frozen=True)
class Grade:
    lift: RGB = (0.0, 0.0, 0.0)          # + raises blacks (milky), - crushes
    gamma: RGB = (1.0, 1.0, 1.0)         # > 1 brightens mids
    gain: RGB = (1.0, 1.0, 1.0)          # white point per channel
    contrast: float = 0.0                # S-curve strength, 0..0.4 sensible
    pivot: float = 0.5                   # S-curve pivot (display value kept fixed)
    shoulder: float = 0.95               # knee for values above 1.0 (1.0 = hard clip)
    saturation: float = 1.0
    shadow_tint: RGB = (0.5, 0.5, 0.5)
    shadow_amount: float = 0.0
    highlight_tint: RGB = (0.5, 0.5, 0.5)
    highlight_amount: float = 0.0
    vignette: float | None = None        # strength used when the post settings carry none
    halation: RGB = (1.0, 1.0, 1.0)      # bloom tint (warm = film halation)
    grain: float | None = None           # recommended grain gain when the post settings carry none

    @property
    def is_identity(self) -> bool:
        return self == IDENTITY

    def per_channel_identity(self) -> bool:
        return (self.lift == (0.0, 0.0, 0.0) and self.gamma == (1.0, 1.0, 1.0) and self.gain == (1.0, 1.0, 1.0)
                and self.contrast == 0.0 and self.shoulder >= 1.0)


IDENTITY = Grade(shoulder=1.0)
NEUTRAL = Grade()

# Five theme grades. Subtle by design: the theme palettes are already chosen; the grade adds a
# film response (soft shoulder, gentle S) and a hue bias in the shadows / highlights.
GRADES: dict[str, Grade] = {
    "none": IDENTITY,
    "neutral": NEUTRAL,
    # kinetic type: black / bone / signal red -> punchy, cool-black shadows, warm bone highlights
    "ink": Grade(lift=(0.0, 0.0, 0.004), gain=(1.0, 0.99, 0.975), contrast=0.12, saturation=1.04,
                 shadow_tint="#1c2a3a", shadow_amount=0.10, highlight_tint="#ffd8b0", highlight_amount=0.06,
                 vignette=0.35, halation=(1.0, 0.62, 0.48), grain=0.05),
    # explainer: navy / teal / amber -> cold tech, clean whites
    "lab": Grade(lift=(0.0, 0.003, 0.008), gain=(0.975, 1.0, 1.02), contrast=0.06, saturation=0.96,
                 shadow_tint="#14394f", shadow_amount=0.10, highlight_tint="#fff1de", highlight_amount=0.04,
                 vignette=0.3, halation=(0.85, 1.0, 1.0), grain=0.04),
    # promo: ink violet + cyan -> rich, slightly lifted violet blacks
    "studio": Grade(lift=(0.004, 0.0, 0.01), gain=(1.01, 0.99, 1.0), contrast=0.10, saturation=1.06,
                    shadow_tint="#2a1a5c", shadow_amount=0.10, highlight_tint="#ffe8f4", highlight_amount=0.05,
                    vignette=0.25, halation=(0.9, 0.8, 1.0), grain=0.04),
    # data / trading: near-black + neon -> high contrast, faint green-cyan shadows
    "terminal": Grade(lift=(0.0, 0.002, 0.003), gain=(0.985, 1.0, 1.0), contrast=0.14, saturation=1.02,
                      shadow_tint="#0b3326", shadow_amount=0.08, highlight_tint="#f2fff8", highlight_amount=0.03,
                      vignette=0.35, halation=(0.8, 1.0, 0.9), grain=0.045),
    # social / quote: espresso / amber -> editorial warm paper, milky blacks
    "warm": Grade(lift=(0.012, 0.008, 0.004), gamma=(1.03, 1.03, 1.03), gain=(1.0, 0.98, 0.94), contrast=0.06,
                  saturation=0.92, shadow_tint="#3a2414", shadow_amount=0.12, highlight_tint="#ffd29a",
                  highlight_amount=0.08, vignette=0.4, halation=(1.0, 0.55, 0.3), grain=0.055),
}
# hex tints are normalised to RGB tuples so presets stay hashable and comparable
GRADES = {k: replace(g, shadow_tint=_rgb(g.shadow_tint), highlight_tint=_rgb(g.highlight_tint))
          for k, g in GRADES.items()}

_FIELDS = {f.name for f in fields(Grade)}
_RGB_FIELDS = {"lift", "gamma", "gain", "shadow_tint", "highlight_tint", "halation"}


def resolve_grade(spec: Any = None, theme: str | None = None) -> Grade:
    """Grade from a preset name, dict (optional "base" preset + overrides), Grade, or None.

    None falls back to the preset named like ``theme`` (if any), else "neutral"."""
    if isinstance(spec, Grade):
        return spec
    if spec is None or spec is True:
        return GRADES.get(str(theme), NEUTRAL) if theme else NEUTRAL
    if spec is False:
        return IDENTITY
    if isinstance(spec, str):
        if spec not in GRADES:
            raise ValueError(f"unknown grade {spec!r}; choose from {sorted(GRADES)}")
        return GRADES[spec]
    if isinstance(spec, dict):
        base_name = spec.get("base", theme if theme in GRADES else "neutral")
        base = resolve_grade(base_name) if base_name is not None else NEUTRAL
        kw: dict[str, Any] = {}
        for k, v in spec.items():
            if k == "base":
                continue
            if k not in _FIELDS:
                raise ValueError(f"unknown grade field {k!r}; fields: {sorted(_FIELDS)}")
            kw[k] = _rgb(v) if k in _RGB_FIELDS else (None if v is None else float(v))
        return replace(base, **kw)
    raise TypeError(f"cannot resolve a grade from {spec!r}")


# ---------------------------------------------------------------------------- transfer functions
def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, np.float64)
    return np.where(x <= 0.04045, x / 12.92, ((np.maximum(x, 0.0) + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    x = np.maximum(np.asarray(x, np.float64), 0.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1 / 2.4) - 0.055)


@lru_cache(maxsize=1)
def decode_lut() -> np.ndarray:
    """uint8 sRGB code -> linear light (float32, 256 entries)."""
    return srgb_to_linear(np.arange(256) / 255.0).astype(np.float32)


def _shoulder(x: np.ndarray, knee: float) -> np.ndarray:
    if knee >= 1.0:
        return x
    w = 1.0 - knee
    hi = knee + w * np.tanh((x - knee) / w)
    return np.where(x > knee, hi, x)


def _s_curve(x: np.ndarray, amount: float, pivot: float) -> np.ndarray:
    if amount == 0.0:
        return x
    p = min(max(pivot, 0.05), 0.95)
    e = np.log(0.5) / np.log(p)              # remap so the pivot sits at 0.5
    xc = np.clip(x, 0.0, 1.0)
    y = xc ** e
    s = y * y * (3 - 2 * y)
    s = s ** (1 / e)
    return x + amount * (s - xc)


def per_channel(grade: Grade, x: np.ndarray) -> np.ndarray:
    """Per-channel part of the grade on display values x (..., 3), float64, unclipped input."""
    x = _shoulder(np.asarray(x, np.float64), grade.shoulder)
    lift, gam, gain = (np.asarray(v, np.float64) for v in (grade.lift, grade.gamma, grade.gain))
    y = gain * (x + lift * (1 - np.clip(x, 0.0, 1.0)))
    y = np.maximum(y, 0.0) ** (1 / np.maximum(gam, 1e-3))
    y = _s_curve(y, grade.contrast, grade.pivot)
    return np.clip(y, 0.0, 1.0)


def apply_display(grade: Grade, img: np.ndarray) -> np.ndarray:
    """Reference (slow, float64) grade of a display-space image (..., 3) in [0, 1+]."""
    y = per_channel(grade, img)
    L = (y * np.asarray(LUMA)).sum(-1, keepdims=True)
    if grade.saturation != 1.0:
        y = L + grade.saturation * (y - L)
    y = y + split_offsets(grade, L)
    return np.clip(y, 0.0, 1.0)


def split_offsets(grade: Grade, L: np.ndarray) -> np.ndarray:
    ts, th = np.asarray(grade.shadow_tint), np.asarray(grade.highlight_tint)
    cs = ts - float((ts * LUMA).sum())
    ch = th - float((th * LUMA).sum())
    Lc = np.clip(L, 0.0, 1.0)
    return grade.shadow_amount * (1 - Lc) ** 2 * cs + grade.highlight_amount * Lc ** 2 * ch


# ---------------------------------------------------------------------------- compiled tables
@lru_cache(maxsize=32)
def encode_tables(grade: Grade, lin_max: float = LIN_MAX) -> tuple[np.ndarray, np.ndarray]:
    """Tables for q = 255 * sqrt(linear / lin_max): value V[k] and slope D[k] (per unit of q) so
    that out = V[round(q)] + (q - round(q)) * D[round(q)]. Shapes (256, 1, 3) float32 (cv2.LUT)."""
    k = np.arange(256, dtype=np.float64)
    q = np.stack([k - 0.5, k, k + 0.5])  # central differences
    lin = lin_max * (np.clip(q, 0, None) / 255.0) ** 2
    disp = linear_to_srgb(lin)[..., None].repeat(3, -1)
    out = per_channel(grade, disp) if not grade.per_channel_identity() else np.clip(disp, 0.0, 1.0)
    V = out[1]
    D = out[2] - out[0]
    return V.reshape(256, 1, 3).astype(np.float32), D.reshape(256, 1, 3).astype(np.float32)


@lru_cache(maxsize=32)
def split_table(grade: Grade) -> np.ndarray | None:
    """Split-tone offsets indexed by 8-bit luma, (256, 1, 3) float32, or None when inactive."""
    if grade.shadow_amount == 0.0 and grade.highlight_amount == 0.0:
        return None
    L = (np.arange(256) / 255.0)[:, None]
    return split_offsets(grade, L).reshape(256, 1, 3).astype(np.float32)


@lru_cache(maxsize=32)
def saturation_matrix(grade: Grade) -> np.ndarray | None:
    s = grade.saturation
    if s == 1.0:
        return None
    w = np.asarray(LUMA, np.float64)[None, :]
    m = s * np.eye(3) + (1 - s) * np.repeat(w, 3, 0)
    return m.astype(np.float32)


@lru_cache(maxsize=32)
def uint8_lut(grade: Grade) -> np.ndarray:
    """Per-channel 8-bit LUT (256, 3) uint8 of the per-channel part (for previews / thumbnails)."""
    x = (np.arange(256) / 255.0)[:, None].repeat(3, -1)
    return (per_channel(grade, x) * 255.0 + 0.5).astype(np.uint8)


def describe(name_or_grade: Any) -> dict[str, Any]:
    g = resolve_grade(name_or_grade)
    return {f.name: getattr(g, f.name) for f in fields(Grade)}
