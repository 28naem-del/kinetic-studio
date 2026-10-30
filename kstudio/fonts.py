"""Bundled OFL font registry with variable-axis instancing and caching."""
from __future__ import annotations

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
    "Inter": "Inter.ttf",                # wght 100-900, opsz: UI / body
    "Fraunces": "Fraunces.ttf",          # serif display: quotes, editorial titles
    "JetBrainsMono": "JetBrainsMono.ttf",  # mono: data, code, tickers
    "SpaceGrotesk": "SpaceGrotesk.ttf",  # wght 300-700: tech / product headings
}
ALIASES = {"display": "Archivo", "sans": "Inter", "serif": "Fraunces", "mono": "JetBrainsMono",
           "grotesk": "SpaceGrotesk"}


def _tag(s: str) -> int:
    return (ord(s[0]) << 24) | (ord(s[1]) << 16) | (ord(s[2]) << 8) | ord(s[3])


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


@lru_cache(maxsize=512)
def typeface(family: str, weight: int = 700, width: float = 100.0, opsz: float | None = None,
             soft: float | None = None) -> skia.Typeface:
    base = _base(family)
    axes = {a.tag: a for a in (base.getVariationDesignParameters() or [])}
    coords = []

    def put(tag: str, v: float) -> None:
        t = _tag(tag)
        if t in axes:
            a = axes[t]
            coords.append(skia.FontArguments.VariationPosition.Coordinate(t, max(a.min, min(a.max, v))))

    put("wght", float(weight))
    put("wdth", float(width))
    if opsz is not None:
        put("opsz", float(opsz))
    if soft is not None:
        put("SOFT", float(soft))
    if not coords:
        return base
    pos = skia.FontArguments.VariationPosition(skia.FontArguments.VariationPosition.Coordinates(coords))
    fa = skia.FontArguments()
    fa.setVariationDesignPosition(pos)
    return base.makeClone(fa)


def font(family: str, size: float, weight: int = 700, width: float = 100.0, **kw) -> skia.Font:
    f = skia.Font(typeface(family, int(round(weight / 10) * 10), round(width, 1), **kw), size)
    f.setEdging(skia.Font.Edging.kAntiAlias)
    f.setSubpixel(True)
    f.setHinting(skia.FontHinting.kNone)
    return f
