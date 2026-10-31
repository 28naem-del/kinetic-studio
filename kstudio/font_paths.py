"""Pure font registry and path selection shared by loading and render dependencies."""
from __future__ import annotations

import os

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


def resolve_font_path(family: str) -> str:
    """Resolve aliases/bundled families first, then any existing cwd-relative file.

    Custom fonts need no particular extension. No expansion or project-relative
    fallback: this is exactly the path selection used by the Skia font loader.
    """
    fam = ALIASES.get(family, family)
    name = FAMILIES.get(fam)
    if name is not None:
        return os.path.join(FONT_DIR, name)
    if os.path.isfile(family):
        return family
    raise KeyError(f"unknown font family {family!r}; bundled: {sorted(FAMILIES)}")
