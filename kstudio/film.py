"""Film = spec -> timeline + scenes + transitions + overlays. Draws one time sample.

The Film is deterministic: any process can rebuild it from the spec and draw any
time t, which is what makes chunked, parallel, resumable rendering possible.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import skia

from . import __version__
from . import draw as D
from . import ease as E
from .modes import mode_defaults, resolve_theme
from .scenes import Ctx, Scene, draw_ground, get_scene_class
from .scenes.common import draw_caption, draw_handle, draw_lower_third, draw_progress
from .spec import SpecError, spec_hash
from .timeline import TimeExprError, Timeline

RESERVED = {"type", "at", "dur", "transition", "transition_dur"}

TRANSITIONS: dict[str, dict[str, Any]] = {
    # name: default window (s), shutter multiplier inside the window, sound at the boundary
    "cut": {"dur": 0.0, "shutter": 1.0, "sfx": None},
    "flash": {"dur": 0.16, "shutter": 1.0, "sfx": "hit"},
    "fade": {"dur": 0.45, "shutter": 1.0, "sfx": None},
    "whip": {"dur": 0.26, "shutter": 3.0, "sfx": "whoosh"},
    "push": {"dur": 0.4, "shutter": 2.0, "sfx": "swish"},
    "zoom": {"dur": 0.34, "shutter": 2.5, "sfx": "whoosh"},
    "squash": {"dur": 0.3, "shutter": 2.0, "sfx": "swish"},
}
DIRS = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}


def _deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass
class Boundary:
    t: float
    kind: str
    direction: tuple[int, int]
    dur: float
    a: Scene
    b: Scene


@lru_cache(maxsize=1)
def engine_hash() -> str:
    """Hash of the engine's own source, so resumable chunks are invalidated after an upgrade/patch."""
    import hashlib
    h = hashlib.sha1()
    pkg = os.path.dirname(os.path.abspath(__file__))
    for root, dirs, files in os.walk(pkg):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(files):
            if f.endswith(".py"):
                with open(os.path.join(root, f), "rb") as fh:
                    h.update(f.encode() + fh.read())
    return h.hexdigest()[:12]


class Film:
    def __init__(self, spec: dict[str, Any], width: int = 1080, height: int = 1920, beat_grid: Any = None):
        self.spec = spec
        self.W, self.H = width, height
        self.dir = spec.get("_dir", ".")
        mode = spec.get("mode", "kinetic")
        self.cfg = _deep_merge(mode_defaults(mode), {k: spec[k] for k in ("music", "camera", "post", "audit", "transition")
                                                     if k in spec})
        self.mode = mode
        theme_name = spec.get("theme", self.cfg["theme"])
        self.theme = resolve_theme(theme_name, spec.get("theme_overrides"))
        self.pal = D.Palette(self.theme)
        self.handle = spec.get("handle", "")
        self.show_progress = bool(spec.get("progress", False))
        music = self.cfg.get("music", {})
        if "bpm" in spec:
            music["bpm"] = spec["bpm"]
        bpm = float(music.get("bpm", 120))
        if beat_grid is not None:
            self.tl = Timeline(beat_grid.bpm, beat_grid.offset, beat_times=beat_grid.beat_times)
        else:
            self.tl = Timeline(bpm, float(music.get("offset", 0.0)))
        self.scenes: list[Scene] = []
        self.boundaries: list[Boundary] = []
        self._build()

    # ------------------------------------------------------------------ build
    def _marker_kwargs(self, m: dict) -> dict:
        return {k: v for k, v in m.items() if k not in ("kind", "at", "name", "dur")}

    def _try_marker(self, m: dict) -> bool:
        try:
            t = self.tl.resolve(m.get("at", 0))
        except TimeExprError:
            return False
        dur = self.tl.resolve_dur(m.get("dur", 0), t)
        kind = m.get("kind", "cue")
        if kind in ("source", "lower_third"):
            kind = "cue"
            m = {**m, "lower_third": {"tag": m.get("tag", "Source"), "text": m.get("text", "")}}
            dur = dur or 3.0
        self.tl.add(kind, t, m.get("name", ""), dur, **self._marker_kwargs(m))
        return True

    def _build(self) -> None:
        sp = self.spec
        pending: list[dict] = list(sp.get("markers", []))
        for c in sp.get("captions", []):
            pending.append({"kind": "caption", **c})
        for s_ in sp.get("sources", []):
            pending.append({"kind": "source", **s_})
        pending = [m for m in pending if not self._try_marker(m)]
        # scenes, sequential by default
        cursor = 0.0
        default_tr = self.cfg.get("transition", "cut")
        raw = sp.get("scenes", [])
        if not raw:
            raise SpecError("spec has no scenes")
        for i, sc in enumerate(raw):
            if "type" not in sc:
                raise SpecError(f"scene #{i} has no type")
            cls = get_scene_class(sc["type"])
            start = self.tl.resolve(sc["at"]) if "at" in sc else cursor
            dur = self.tl.resolve_dur(sc.get("dur", 2.0), start)
            if dur <= 0:
                raise SpecError(f"scene #{i} ({sc['type']}) has non-positive duration")
            params = {k: v for k, v in sc.items() if k not in RESERVED}
            params.setdefault("id", f"s{i}_{sc['type']}")
            scene = cls(params, start, dur, self.tl, self.cfg)
            self.tl.add("cue", start, scene.id, dur, scene=sc["type"])
            if self.scenes:
                tr = str(sc.get("transition", default_tr))
                kind, _, d = tr.partition("-")
                if kind not in TRANSITIONS:
                    raise SpecError(f"unknown transition {tr!r}; choose from {sorted(TRANSITIONS)}")
                direction = DIRS.get(d or ("left" if i % 2 else "up"), (-1, 0))
                tdur = float(sc.get("transition_dur", TRANSITIONS[kind]["dur"]))
                self.boundaries.append(Boundary(start, kind, direction, tdur, self.scenes[-1], scene))
            self.scenes.append(scene)
            cursor = start + dur
        self.duration = float(self.tl.resolve(sp["duration"])) if "duration" in sp else max(s.end for s in self.scenes)
        self.tl.duration = self.duration
        still = [m for m in pending if not self._try_marker(m)]
        if still:
            raise SpecError(f"could not resolve markers: {[m.get('at') for m in still]}")
        if not self.tl.of("section"):
            self.tl.add("section", 0.0, "main", self.duration, energy=2)
        # scene cues -> markers
        for sc in self.scenes:
            for cue in sc.cues():
                t = sc.start + cue.at
                if t < -1e-6 or t > self.duration + 0.5:
                    continue
                self.tl.add(cue.kind, max(0.0, t), cue.name, 0.0, **cue.data)
        for b in self.boundaries:
            snd = TRANSITIONS[b.kind]["sfx"]
            if snd and b.b.p.get("transition_sfx", True):
                lead = 0.18 if snd in ("whoosh", "swish") else 0.0
                self.tl.add("sfx", max(0.0, b.t - lead), "", 0.0, sound=snd, gain=-3.0)
            if b.kind in ("whip", "flash"):
                self.tl.add("hit", b.t, "", 0.0, strength=0.35 if b.kind == "whip" else 0.6,
                            flash=0.5 if b.kind == "flash" else 0.0)
        self.tl.generate_beats(self.duration)

    # ------------------------------------------------------------------ query
    def fingerprint(self, extra: Any = None) -> str:
        assets = []
        for root, _, files in os.walk(self.dir):
            if any(x in root for x in ("/out", "/.kstudio", "/.venv", "/node_modules", "/.git", "/dist", "/demos")):
                continue
            for f in sorted(files):
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".ttf", ".otf")):
                    p = os.path.join(root, f)
                    assets.append((f, os.path.getsize(p), int(os.path.getmtime(p))))
        return spec_hash(self.spec, [__version__, engine_hash(), self.W, self.H, extra, assets])

    def scene_at(self, t: float) -> Scene:
        cur = self.scenes[0]
        for s in self.scenes:
            if s.start <= t + 1e-9:
                cur = s
        return cur

    def boundary_at(self, t: float) -> tuple[Boundary, float] | None:
        for b in self.boundaries:
            if b.dur > 0 and b.t - b.dur / 2 <= t < b.t + b.dur / 2:
                return b, (t - (b.t - b.dur / 2)) / b.dur
        return None

    def shutter_mult(self, t: float) -> float:
        bt = self.boundary_at(t)
        if bt:
            return TRANSITIONS[bt[0].kind]["shutter"]
        s = self.scene_at(t)
        return s.shutter(t - s.start)

    # ------------------------------------------------------------------ draw
    def _ctx(self, c: skia.Canvas, t: float) -> Ctx:
        return Ctx(c, self.W, self.H, self.pal, self.theme, self.tl, t, assets=self.dir)

    def _draw_scene(self, c: skia.Canvas, sc: Scene, t: float, lt_min: float | None = None,
                    lt_max: float | None = None) -> None:
        lt = t - sc.start
        if lt_min is not None:
            lt = max(lt, lt_min)
        if lt_max is not None:
            lt = min(lt, lt_max)
        ctx = self._ctx(c, t)
        ctx.lt, ctx.dur = lt, sc.dur
        c.save()
        sc.camera(ctx)
        draw_ground(ctx, sc.ground_at(lt), sc.ground_style)
        sc.draw(ctx)
        c.restore()

    def draw(self, c: skia.Canvas, t: float) -> None:
        W, H = self.W, self.H
        bt = self.boundary_at(t)
        if bt is None:
            self._draw_scene(c, self.scene_at(t), t)
        else:
            b, p = bt
            a_max = b.a.dur + b.dur / 2
            k = b.kind
            if k == "fade":
                self._draw_scene(c, b.a, t, lt_max=a_max)
                c.saveLayerAlpha(None, int(255 * E.in_out_quad(p)))
                self._draw_scene(c, b.b, t, lt_min=0.0)
                c.restore()
            elif k == "flash":
                self._draw_scene(c, b.a if p < 0.5 else b.b, t, lt_min=0.0, lt_max=a_max)
                c.drawRect(skia.Rect(0, 0, W, H), D.paint(self.pal("paper", 1 - abs(2 * p - 1))))
            elif k in ("whip", "push"):
                e = E.in_out_expo(p) if k == "whip" else E.in_out_cubic(p)
                dx, dy = b.direction
                c.save(); c.translate(dx * W * e, dy * H * e)
                if k == "push":
                    sc_ = 1 - 0.08 * e
                    c.translate(W / 2, H / 2); c.scale(sc_, sc_); c.translate(-W / 2, -H / 2)
                self._draw_scene(c, b.a, t, lt_max=a_max)
                c.restore()
                c.save(); c.translate(-dx * W * (1 - e), -dy * H * (1 - e))
                self._draw_scene(c, b.b, t, lt_min=0.0)
                c.restore()
            elif k == "zoom":
                if p < 0.5:
                    u = E.in_cubic(p * 2)
                    c.save(); c.translate(W / 2, H / 2); c.scale(1 + 0.9 * u, 1 + 0.9 * u); c.translate(-W / 2, -H / 2)
                    self._draw_scene(c, b.a, t, lt_max=a_max)
                    c.restore()
                    c.drawRect(skia.Rect(0, 0, W, H), D.paint(self.pal("bg", u)))
                else:
                    u = E.out_cubic((p - 0.5) * 2)
                    sc_ = 0.6 + 0.4 * u
                    self._ground_only(c, b.b, t)
                    c.save(); c.translate(W / 2, H / 2); c.scale(sc_, sc_); c.translate(-W / 2, -H / 2)
                    c.saveLayerAlpha(None, int(255 * u))
                    self._draw_scene(c, b.b, t, lt_min=0.0)
                    c.restore(); c.restore()
            elif k == "squash":
                if p < 0.5:
                    u = E.in_cubic(p * 2)
                    self._ground_only(c, b.b, t)
                    c.save(); c.translate(W / 2, H / 2); c.scale(1 + 0.2 * u, max(1 - u, 0.001)); c.translate(-W / 2, -H / 2)
                    self._draw_scene(c, b.a, t, lt_max=a_max)
                    c.restore()
                else:
                    u = E.out_back((p - 0.5) * 2)
                    self._ground_only(c, b.a, t)
                    c.save(); c.translate(W / 2, H / 2); c.scale(1.2 - 0.2 * u, max(u, 0.001)); c.translate(-W / 2, -H / 2)
                    self._draw_scene(c, b.b, t, lt_min=0.0)
                    c.restore()
            else:
                self._draw_scene(c, self.scene_at(t), t)
        self._overlays(c, t)

    def _ground_only(self, c: skia.Canvas, sc: Scene, t: float) -> None:
        ctx = self._ctx(c, t)
        draw_ground(ctx, "bg", sc.ground_style)

    def _overlays(self, c: skia.Canvas, t: float) -> None:
        ctx = self._ctx(c, t)
        for m in self.tl.markers:
            if m.kind == "cue" and "lower_third" in m.data and m.t <= t < m.end + 0.01:
                lt3 = m.data["lower_third"]
                draw_lower_third(ctx, lt3.get("tag", "Source"), lt3.get("text", ""),
                                 (t - m.t) / 0.55, (t - (m.end - 0.4)) / 0.4)
        for m in self.tl.captions_at(t):
            local = t - m.t
            u = min(local / 0.2, (m.end - t) / 0.2)
            draw_caption(ctx, str(m.data.get("text", "")), u, local, m.dur, bool(m.data.get("karaoke", True)))
        if self.handle:
            draw_handle(ctx, self.handle)
        if self.show_progress:
            draw_progress(ctx, t / max(self.duration, 1e-3))
        fl = 0.0
        for m in self.tl.of("hit"):
            f = float(m.data.get("flash", 0.0))
            if f and 0 <= t - m.t < 0.4:
                fl += f * E.clamp(1 - (t - m.t) / 0.25) ** 2
        if fl > 0.002:
            c.drawRect(skia.Rect(0, 0, self.W, self.H), D.paint(self.pal("paper", min(fl, 1.0))))

    # ------------------------------------------------------------------ info
    def summary(self) -> dict[str, Any]:
        return {"mode": self.mode, "duration": round(self.duration, 3), "size": [self.W, self.H],
                "scenes": [{"id": s.id, "type": s.type, "start": round(s.start, 3), "dur": round(s.dur, 3)} for s in self.scenes],
                "transitions": [{"t": round(b.t, 3), "kind": b.kind, "dur": b.dur} for b in self.boundaries],
                "timeline": self.tl.summary()}
