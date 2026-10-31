"""Portable, content-based render dependency identities (never output-directory scans)."""
from __future__ import annotations

import hashlib
import os
from typing import Any

from .font_paths import FONT_DIR, resolve_font_path
from .modes import mode_defaults, resolve_theme

BUNDLED_FONT_DIR = FONT_DIR
MEDIA_KEYS = {"src", "file", "video", "character", "screen", "mark"}
FONT_KEYS = {"role", "face", "title_role", "font", "family"}


def content_digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def asset_fingerprints(base: str, spec: dict[str, Any]) -> list[tuple[str, str]]:
    """Hash referenced media and fonts, not generated or unrelated project files.

    Media paths follow the scene's project-relative resolution. Custom font paths
    follow fonts._base: relative to process cwd (not the spec folder). All bundled
    fonts participate because scene defaults can select them outside the spec.
    Digests are cached only within this call, including swap-key construction.
    """
    from .spec import SWAP_KEYS, swap_dir, swap_key, swap_requests

    base = os.path.abspath(base)
    defaults = mode_defaults(spec.get("mode", "kinetic"))
    theme = resolve_theme(spec.get("theme", defaults["theme"]), spec.get("theme_overrides"))
    paths: dict[str, str] = {}
    digests: dict[str, str] = {}

    def add(path: str, label: str | None = None) -> None:
        path = os.path.abspath(path)
        if os.path.isfile(path):
            paths[path] = label or os.path.relpath(path, base).replace(os.sep, "/")

    def digest(path: str) -> str:
        path = os.path.abspath(path)
        if path not in digests:
            digests[path] = content_digest(path)
        return digests[path]

    def add_font(selection: str) -> None:
        family = theme.get(selection, selection)
        if isinstance(family, str):
            try:
                add(resolve_font_path(family))
            except KeyError:
                pass  # an invalid font selection is reported by spec/render validation

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str):
                    if key in MEDIA_KEYS:
                        add(item if os.path.isabs(item) else os.path.join(base, item))
                    if key in FONT_KEYS:
                        add_font(item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for role in ("display", "body", "mono"):
        add_font(role)
    visit(spec)
    if os.path.isdir(BUNDLED_FONT_DIR):
        for name in sorted(os.listdir(BUNDLED_FONT_DIR)):
            if name.lower().endswith((".ttf", ".otf")):
                add(os.path.join(BUNDLED_FONT_DIR, name), ".kstudio-fonts/" + name)

    seen: set[tuple[Any, ...]] = set()
    for request in swap_requests({**spec, "_dir": base}):
        identity = tuple(request[k] for k in SWAP_KEYS)
        if identity in seen:
            continue
        seen.add(identity)
        if all(os.path.isfile(request[k]) for k in ("video", "character")):
            add(request["video"])
            add(request["character"])
            add(os.path.join(swap_dir(base), swap_key(request, digest=digest) + ".mp4"))
    return sorted((label, digest(path)) for path, label in paths.items())
