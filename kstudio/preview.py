"""Fast previews: stills at chosen times (default: the middle of every scene plus every
transition) and a labelled contact sheet, rendered in-process at reduced scale."""
from __future__ import annotations

import os
from typing import Any

import cv2
import numpy as np

from .compositor import Compositor
from .film import Film
from .presets import make_output


def default_times(film: Film) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for sc in film.scenes:
        out.append((sc.start + min(sc.dur * 0.55, sc.dur - 0.05), sc.id))
    for b in film.boundaries:
        out.append((b.t + 0.02, f"{b.kind} @{b.t:.2f}s"))
    return sorted(out)


def preview(spec: dict[str, Any], out_dir: str, preset: str = "reel", times: list[float] | None = None,
            scale: float = 0.5, quality: str = "draft", beat_grid: Any = None, cols: int = 4) -> dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    out = make_output(preset, 30, quality, scale=scale)
    film = Film(spec, out.width, out.height, beat_grid=beat_grid)
    comp = Compositor(film, out)
    pts = [(t, f"{t:.2f}s") for t in times] if times else default_times(film)
    stills = []
    tiles = []
    for t, label in pts:
        i = min(int(round(t * out.fps)), int(film.duration * out.fps) - 1)
        rgb = comp.frame(i)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        p = os.path.join(out_dir, f"still_{i:05d}.jpg")
        cv2.imwrite(p, bgr, [cv2.IMWRITE_JPEG_QUALITY, 90])
        stills.append({"t": round(i / out.fps, 3), "label": label, "path": p})
        tw = 270
        th = int(round(tw * out.height / out.width))
        tile = cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_AREA)
        bar = np.full((28, tw, 3), 18, np.uint8)
        cv2.putText(bar, f"{i / out.fps:5.2f}s {label}"[:34], (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (235, 235, 235), 1,
                    cv2.LINE_AA)
        tiles.append(np.vstack([tile, bar]))
    sheet_path = None
    if tiles:
        rows = []
        for r in range(0, len(tiles), cols):
            row = tiles[r:r + cols]
            while len(row) < cols:
                row.append(np.zeros_like(tiles[0]))
            rows.append(np.hstack(row))
        sheet_path = os.path.join(out_dir, "contact_sheet.jpg")
        cv2.imwrite(sheet_path, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return {"stills": stills, "contact_sheet": sheet_path, "summary": film.summary()}
