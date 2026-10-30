"""Bundled OFL font registry: variable-axis instancing, optical tracking and pair kerning."""
from __future__ import annotations

import math
import os
from functools import cache, lru_cache

import skia


def _init_fontmgr_quietly() -> None:
    """Initialise Skia's font manager once with stderr muted. Fonts are always loaded from the
    bundled files; on some hosts the system fontconfig emits harmless version-mismatch warnings
    (e.g. 'unknown element "reset-dirs"') on first use, which would spam every worker's output."""
    try:
        fd = os.dup(2)
    except OSError:
        skia.FontMgr.RefDefault()
        return
    try:
        with open(os.devnull, "w") as null:
            os.dup2(null.fileno(), 2)
            skia.FontMgr.RefDefault().countFamilies()
    finally:
        os.dup2(fd, 2)
        os.close(fd)


_init_fontmgr_quietly()

FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")

FAMILIES = {
    "Archivo": "Archivo.ttf",            # wght 100-900, wdth 62-125: kinetic display
    "Inter": "Inter.ttf",                # wght 100-900, opsz 14-32: UI / body
    "Fraunces": "Fraunces.ttf",          # wght 100-900, opsz 9-144, SOFT, WONK: editorial serif
    "JetBrainsMono": "JetBrainsMono.ttf",  # wght 100-800: data, code, tickers
    "SpaceGrotesk": "SpaceGrotesk.ttf",  # wght 300-700: tech / product headings
}
ALIASES = {"display": "Archivo", "sans": "Inter", "serif": "Fraunces", "mono": "JetBrainsMono",
           "grotesk": "SpaceGrotesk"}

_VP = skia.FontArguments.VariationPosition


def _tag(s: str) -> int:
    return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])


def _untag(t: int) -> str:
    return "".join(chr((t >> k) & 0xFF) for k in (24, 16, 8, 0))


@cache
def _base(family: str) -> skia.Typeface:
    fam = ALIASES.get(family, family)
    fn = FAMILIES.get(fam)
    if fn is None:
        if os.path.isfile(family):  # user-supplied font file
            tf = skia.Typeface.MakeFromFile(family)
        else:
            raise KeyError(f"unknown font family {family!r}; bundled: {sorted(FAMILIES)}")
    else:
        tf = skia.Typeface.MakeFromFile(os.path.join(FONT_DIR, fn))
    if tf is None:
        raise RuntimeError(f"failed to load font {family}")
    return tf


@cache
def axes(family: str) -> dict[str, tuple[float, float, float]]:
    """Variation axes of a family: {'wght': (min, default, max), ...} (empty for static fonts)."""
    return {_untag(a.tag): (a.min, getattr(a, "def"), a.max)
            for a in (_base(family).getVariationDesignParameters() or [])}


def clamp_axis(family: str, tag: str, v: float) -> float:
    a = axes(family).get(tag)
    return v if a is None else max(a[0], min(a[2], v))


@lru_cache(maxsize=512)
def typeface(family: str, weight: int = 700, width: float = 100.0, opsz: float | None = None,
             soft: float | None = None, wonk: float | None = None) -> skia.Typeface:
    """A typeface instanced at the given axis values (clamped to each axis' range; axes the
    font does not have are ignored).

    skia-python's VariationPosition stores a pointer to its Coordinates array without owning it,
    so the Coordinates object must stay referenced until makeClone() has copied the values;
    passing a temporary silently yields the default instance."""
    base = _base(family)
    have = axes(family)
    want = {"wght": float(weight), "wdth": float(width), "opsz": opsz, "SOFT": soft, "WONK": wonk}
    coords = [_VP.Coordinate(_tag(t), clamp_axis(family, t, float(v)))
              for t, v in want.items() if v is not None and t in have]
    if not coords:
        return base
    arr = _VP.Coordinates(coords)
    pos = _VP(arr)
    fa = skia.FontArguments()
    fa.setVariationDesignPosition(pos)
    tf = base.makeClone(fa)
    del fa, pos, arr
    return tf if tf is not None else base


def font(family: str, size: float, weight: int = 700, width: float = 100.0, **kw) -> skia.Font:
    f = skia.Font(typeface(family, int(round(weight / 10) * 10), round(width, 1), **kw), size)
    f.setEdging(skia.Font.Edging.kAntiAlias)
    f.setSubpixel(True)
    f.setHinting(skia.FontHinting.kNone)
    return f


# --------------------------------------------------------------------- optical tracking

def track(px: float, caps: bool = False, family: str | None = None) -> float:
    """Default tracking (em) for a size in reference px (short frame edge = 1080).

    Lower case follows Inter's dynamic-metrics curve (-0.0223 + 0.185 e^(-0.1745 px)), tightened
    further for display sizes (-0.045 em at >= 224 px). All caps are opened up: +0.12 em at
    kicker sizes (<= 32 px) easing to +0.02 em at headline sizes (>= 126 px). Faces with an
    optical-size axis (set by opsz_for) already tighten at display sizes, so they skip the extra
    display tightening when `family` is given."""
    px = max(1.0, float(px))
    if caps:
        k = min(1.0, max(0.0, (px - 32.0) / (126.0 - 32.0)))
        return round(0.12 + (0.02 - 0.12) * k, 4)
    base = -0.0223 + 0.185 * math.exp(-0.1745 * px)
    k = min(1.0, max(0.0, (px - 96.0) / (224.0 - 96.0)))
    k = k * k * (3 - 2 * k)
    if family is not None and "opsz" in axes(family):
        k = 0.0
    return round(base - 0.0227 * k, 4)


def opsz_for(family: str, px: float) -> float | None:
    """Optical-size axis value for a size in reference px, or None if the face has no opsz."""
    a = axes(family).get("opsz")
    return None if a is None else max(a[0], min(a[2], float(px)))


# --------------------------------------------------------------------- pair kerning (GPOS)
#
# skia.Font.measureText / drawString do no shaping, and Typeface.getKerningPairAdjustments only
# reads a legacy 'kern' table (the bundled fonts kern through GPOS). SkParagraph shapes with
# HarfBuzz using the typeface's variation position, so each pair adjustment is measured once as
# width("ab") - width("a") - width("b") at a large reference size and cached per instance.

_REF = 1000.0
_TL = skia.textlayout
_provider = None
_collection = None
_registered: dict[int, str] = {}


def _family_name(tf: skia.Typeface) -> str:
    global _provider, _collection
    uid = tf.uniqueID()
    name = _registered.get(uid)
    if name is None:
        if _provider is None:
            _provider = _TL.TypefaceFontProvider()
        name = f"ks-{uid}"
        _provider.registerTypeface(tf, skia.String(name))
        _registered[uid] = name
        _collection = _TL.FontCollection()  # fresh collection so the new family is visible
        _collection.setDefaultFontManager(_provider)
    return name


def _shaped_width(tf: skia.Typeface, s: str) -> float:
    fam = _family_name(tf)
    st = _TL.TextStyle()
    st.setFontFamilies([skia.String(fam)])
    st.setFontSize(_REF)
    ps = _TL.ParagraphStyle()
    ps.setTextStyle(st)
    b = _TL.ParagraphBuilder.make(ps, _collection, skia.Unicode())
    b.pushStyle(st)
    b.addText(s)
    p = b.Build()
    p.layout(1e9)
    return float(p.MaxIntrinsicWidth)


_pairs: dict[tuple[int, str], float] = {}


def pair_kern(tf: skia.Typeface, a: str, b: str) -> float:
    """Kerning adjustment (em) between two characters for this typeface instance."""
    if a.isspace() or b.isspace():
        return 0.0
    key = (tf.uniqueID(), a + b)
    v = _pairs.get(key)
    if v is None:
        f = skia.Font(tf, _REF)
        g = f.textToGlyphs(a + b)
        if len(g) != 2 or 0 in g:
            v = 0.0
        else:
            blob = skia.TextBlob.MakeFromShapedText(a + b, f)
            shaped = [gid for run in blob for gid in run.fGlyphIndices] if blob is not None else []
            if shaped != list(g):  # a ligature or contextual substitution: not a kerning pair
                v = 0.0
            else:
                adv = f.getWidths(g)
                v = (_shaped_width(tf, a + b) - adv[0] - adv[1]) / _REF
                if abs(v) < 1e-4:
                    v = 0.0
        _pairs[key] = v
    return v


def kerning(tf: skia.Typeface, s: str) -> list[float]:
    """Pair adjustments (em) between consecutive characters of s (length len(s) - 1)."""
    return [pair_kern(tf, s[i], s[i + 1]) for i in range(len(s) - 1)]
