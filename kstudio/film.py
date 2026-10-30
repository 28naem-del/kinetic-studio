"""Film = spec -> timeline + scenes + transitions + overlays. Draws one time sample.

The Film is deterministic: any process can rebuild it from the spec and draw any
time t, which is what makes chunked, parallel, resumable rendering possible.
"""
from __future__ import annotations

import copy
import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import skia

from . import __version__
from . import draw as D
from . import transitions as TR
from .camera import link_shots
from .modes import mode_defaults, resolve_theme
from .scenes import Ctx, Scene, draw_ground, get_scene_class
from .scenes.base import TIME_PARAMS, _snap_div
from .scenes.common import draw_caption, draw_handle, draw_lower_third, draw_progress
from .spec import SpecError, spec_hash
from .timeline import TimeExprError, Timeline, is_beat_expr

RESERVED = {"type", "at", "dur", "transition", "transition_dur"}

TRANSITIONS = TR.TRANSITIONS
DIRS = TR.DIRS
GRID_TOL = 0.001          # seconds: a cut / scene edge counts as on the grid within 1 ms


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
    t: float                      # the cut (incoming scene start), on the beat for beat-unit specs
    kind: str
    direction: tuple[int, int]
    dur: float                    # window length (s) = t1 - t0
    a: Scene
    b: Scene
    params: dict[str, Any] = field(default_factory=dict)
    t0: float | None = None       # window start (s)
    t1: float | None = None       # window end (s)
    beats: float = 0.0            # window length in beats

    def __post_init__(self) -> None:
        if self.t0 is None:
            self.t0 = self.t - self.dur / 2
        if self.t1 is None:
            self.t1 = self.t0 + self.dur


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
        self.cfg = _deep_merge(mode_defaults(mode), {k: spec[k] for k in ("music", "camera", "post", "audit", "transition",
                                                                       "snap") if k in spec})
        self.fps = float(spec.get("_fps") or spec.get("fps") or 30.0)
        # snap: scene lengths/starts to whole beats, in-scene seconds and cue times to `snap_div`
        self.snap_div = _snap_div(True if self.cfg.get("snap") is True else self.cfg.get("snap"))
        if self.cfg.get("snap") is True:
            self.cfg["snap"] = "16th"
            self.snap_div = 4.0
        self._captures: dict[tuple[int, float], dict[str, Any]] = {}
        self._lint: list[dict[str, Any]] = []
        self.mode = mode
        theme_name = spec.get("theme", self.cfg["theme"])
        self.theme = resolve_theme(theme_name, spec.get("theme_overrides"))
        self.pal = D.Palette(self.theme)
        self.handle = spec.get("handle", "")
        # the Compositor sets this: it applies flashes once per delivered frame, after motion blur,
        # so they stay exactly 1-2 frames long at every shutter; a bare Film.draw draws them itself
        self.defer_flash = False
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
        if self.snap_div and m.get("kind", "cue") in ("sfx", "hit") and not is_beat_expr(m.get("at", 0)):
            t = self.tl.snap(t, self.snap_div)
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
            kind, tparams = "cut", {}
            if i:
                try:
                    kind, tparams = TR.parse(sc.get("transition", default_tr), sc.get("transition_dur"))
                except KeyError:
                    raise SpecError(f"unknown transition {sc.get('transition', default_tr)!r}; "
                                    f"choose from {sorted(TRANSITIONS)}") from None
            if self.snap_div:
                if "at" in sc and not is_beat_expr(sc["at"]):
                    start = self.tl.snap(start, 1.0)
                on = str(tparams.get("on", ""))
                if i and on in ("downbeat", "bar") and not self.tl.is_downbeat(start, GRID_TOL):
                    start = self.tl.snap_bar(start, "ceil")
                    if self.scenes:  # stretch the previous scene to the bar line
                        prev = self.scenes[-1]
                        prev.dur = start - prev.start
                        pm = self.tl.named(prev.id)
                        if pm is not None:
                            pm.dur = prev.dur
            dur = self.tl.resolve_dur(sc.get("dur", 2.0), start)
            if self.snap_div and not is_beat_expr(sc.get("dur", 2.0)):
                dur = self.tl.snap_dur(dur, start, 1.0, min_units=1.0)
            if dur <= 0:
                raise SpecError(f"scene #{i} ({sc['type']}) has non-positive duration")
            params = {k: v for k, v in sc.items() if k not in RESERVED}
            params.setdefault("id", f"s{i}_{sc['type']}")
            scene = cls(params, start, dur, self.tl, self.cfg)
            self.tl.add("cue", start, scene.id, dur, scene=sc["type"])
            if i and kind == "match" and not (isinstance(sc.get("camera"), dict) and "carry" in sc["camera"]):
                scene.cam["carry"] = "full"
            if self.scenes:
                self.boundaries.append(self._boundary(i, kind, tparams, start, self.scenes[-1], scene))
            self.scenes.append(scene)
            cursor = start + dur
        link_shots(self.scenes)
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
                if self.snap_div and cue.kind in ("sfx", "hit"):
                    t = self.tl.snap(t, self.snap_div)
                if t < -1e-6 or t > self.duration + 0.5:
                    continue
                self.tl.add(cue.kind, max(0.0, t), cue.name, 0.0, **cue.data)
        for b in self.boundaries:
            snd = b.params.get("sfx", TRANSITIONS[b.kind]["sfx"])
            if snd and b.b.p.get("transition_sfx", True):
                # the sound's peak lands on the cut (the mixer offsets the clip start by its PEAK)
                self.tl.add("sfx", b.t, "", 0.0, sound=snd, gain=-3.0, align="peak")
            if b.kind == "whip":
                self.tl.add("hit", b.t, "", 0.0, strength=0.35, flash=0.0)
            elif b.kind == "flash":
                self.tl.add("hit", b.t, "", 0.0, strength=0.6, flash=0.0)
        self.tl.generate_beats(self.duration)

    def _boundary(self, i: int, kind: str, tparams: dict[str, Any], cut: float, a: Scene, b: Scene) -> Boundary:
        """Window for a transition, sized in beats around the cut (anchor = fraction before it)."""
        reg = TRANSITIONS[kind]
        d = tparams.get("dur")
        if kind == "match":
            d = tparams.get("morph", "1/2b") if tparams.get("fit") else 0
        if d is None:
            beats = float(reg["beats"])
        elif is_beat_expr(d):
            beats = self.tl.beats_of(self.tl.resolve_dur(d, cut), cut)
        else:  # seconds: quantise to 16ths so the window edges sit on the grid
            beats = round(self.tl.beats_of(float(self.tl.resolve_dur(d, cut)), cut) * 4) / 4
            if float(self.tl.resolve_dur(d, cut)) > 0 and beats == 0:
                beats = 0.25
        anchor = float(tparams.get("anchor", reg["anchor"]))
        cb = self.tl.time_to_beat(cut)
        t0 = self.tl.beat_to_time(cb - anchor * beats) if beats else cut
        t1 = self.tl.beat_to_time(cb + (1 - anchor) * beats) if beats else cut
        clipped = t0 < a.start - 1e-9 or t1 > b.end + 1e-9
        t0, t1 = max(t0, a.start), min(t1, b.end)
        dname = tparams.get("dir") or ("left" if i % 2 else "up")
        direction = DIRS.get(str(dname), (-1, 0))
        bd = Boundary(cut, kind, direction, max(t1 - t0, 0.0), a, b, params=tparams, t0=t0, t1=t1, beats=beats)
        bd.clipped = clipped  # type: ignore[attr-defined]
        return bd

    # ------------------------------------------------------------------ lint
    def lint(self) -> list[dict[str, Any]]:
        """Pre-render grid check: off-beat scene lengths/starts/cuts and seconds-valued timings."""
        out: list[dict[str, Any]] = []
        tl = self.tl

        def add(level: str, where: str, msg: str) -> None:
            out.append({"level": level, "where": where, "msg": msg})

        raw = self.spec.get("scenes", [])
        for i, sc in enumerate(self.scenes):
            where = f"scene #{i} {sc.id}"
            nb = tl.beats_of(sc.dur, sc.start)
            if abs(nb - round(nb)) * tl.spb > GRID_TOL:
                add("warn", where, f"length {sc.dur:.3f}s = {nb:.2f} beats is not a whole number of beats "
                    f"(write \"{max(1, round(nb))}b\" or set \"snap\": true)")
            elif round(nb) not in (1, 2, 4, 8, 16, 3, 6, 12):
                add("info", where, f"length {round(nb)} beats; 2/4/8 read as phrases")
            if not tl.on_grid(sc.start, 1.0, GRID_TOL):
                add("warn", where, f"starts {tl.grid_error(sc.start) * 1000:+.1f} ms off the beat")
            src = raw[i] if i < len(raw) else {}
            for k in TIME_PARAMS:
                if k in src and not is_beat_expr(src[k]) and not isinstance(src[k], bool) and k not in sc.beat_params:
                    add("info", where, f"{k}={src[k]!r} is in seconds; beat units (\"1/2b\") stay on the grid")
            off = sorted({round(sc.start + c.at, 3) for c in sc.cues() if c.kind in ("sfx", "hit")
                          and not tl.on_grid(sc.start + c.at, 2.0, 0.0334)})
            if off:
                add("info", where, f"{len(off)} scene cue(s) off the 8th grid at {off[:6]}s (scene code timing)")
        for b in self.boundaries:
            where = f"cut @{b.t:.3f}s {b.kind}"
            if not tl.on_grid(b.t, 1.0, GRID_TOL):
                add("warn", where, f"cut is {tl.grid_error(b.t) * 1000:+.1f} ms off the beat")
            if str(b.params.get("on", "")) in ("downbeat", "bar") and not tl.is_downbeat(b.t, GRID_TOL):
                add("warn", where, "cut asked for the downbeat but is not on a bar line")
            if getattr(b, "clipped", False):
                add("warn", where, "transition window clipped by a short neighbouring scene")
        self._lint = out
        return out

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
            if b.dur > 0 and b.t0 <= t < b.t1:  # type: ignore[operator]
                return b, (t - b.t0) / b.dur  # type: ignore[operator]
        return None

    def shutter_mult(self, t: float) -> float:
        bt = self.boundary_at(t)
        if bt:
            return TRANSITIONS[bt[0].kind]["shutter"]
        s = self.scene_at(t)
        return s.shutter(t - s.start)

    # ------------------------------------------------------------------ draw
    def _ctx(self, c: skia.Canvas, t: float) -> Ctx:
        return Ctx(c, self.W, self.H, self.pal, self.theme, self.tl, t, assets=self.dir, fps=self.fps, film=self)

    def _draw_scene(self, c: skia.Canvas, sc: Scene, t: float, lt_min: float | None = None,
                    lt_max: float | None = None, content_alpha: float = 1.0,
                    content_offset: tuple[float, float, float] | None = None, ground: bool = True,
                    capture: dict | None = None, clip: bool = False, lt_force: float | None = None) -> None:
        lt = t - sc.start if lt_force is None else lt_force
        if lt_min is not None:
            lt = max(lt, lt_min)
        if lt_max is not None:
            lt = min(lt, lt_max)
        ctx = self._ctx(c, t)
        ctx.lt, ctx.dur, ctx.start, ctx.scene, ctx.capture = lt, sc.dur, sc.start, sc, capture
        c.save()
        if clip:
            c.clipRect(skia.Rect(0, 0, self.W, self.H))
        sc.camera(ctx)
        if ground:
            gd = ctx.cam.ground_depth if ctx.cam is not None else 1.0
            with ctx.layer(gd, world=True):
                draw_ground(ctx, sc.ground_at(lt), sc.ground_style)
        if content_alpha < 0.999 or content_offset is not None:
            if content_alpha <= 0.002:
                c.restore()
                return
            if content_alpha < 0.999:
                c.saveLayerAlpha(None, int(round(255 * content_alpha)))
            else:
                c.save()
            if content_offset is not None:
                dx, dy, k = content_offset
                c.translate(self.W / 2 + dx, self.H / 2 + dy); c.scale(k, k); c.translate(-self.W / 2, -self.H / 2)
            sc.draw(ctx)
            c.restore()
        else:
            sc.draw(ctx)
        c.restore()

    def draw(self, c: skia.Canvas, t: float) -> None:
        bt = self.boundary_at(t)
        if bt is None:
            self._draw_scene(c, self.scene_at(t), t)
        else:
            b, p = bt
            TR.draw_boundary(self, c, b, p, t)
        self._overlays(c, t)

    def _ground_only(self, c: skia.Canvas, sc: Scene, t: float) -> None:
        ctx = self._ctx(c, t)
        draw_ground(ctx, "bg", sc.ground_style)

    # ------------------------------------------------------------------ match cuts
    def capture(self, sc: Scene, t: float) -> dict[str, Any]:
        """Rects published by scene `sc` when drawn at film time t (screen space, cached)."""
        key = (id(sc), round(t, 6))
        if key not in self._captures:
            self._captures[key] = {}
            rec = skia.PictureRecorder()
            cv = rec.beginRecording(skia.Rect(0, 0, self.W, self.H))
            got: dict[str, Any] = {}
            self._draw_scene(cv, sc, t, lt_min=0.0, lt_max=sc.dur, capture=got)
            rec.finishRecordingAsPicture()
            self._captures[key] = got
        return self._captures[key]

    def _incoming(self, sc: Scene) -> Boundary | None:
        for b in self.boundaries:
            if b.b is sc:
                return b
        return None

    def handoff_source(self, sc: Scene, name: str) -> dict[str, Any] | None:
        """The previous scene's published element `name` at the cut into `sc` (None if absent)."""
        b = self._incoming(sc)
        if b is None or not (b.kind in TR.HANDOFF_KINDS or b.params.get("handoff")):
            return None
        if b.params.get("element") and b.params.get("element") != name and not b.params.get("any_element"):
            return None
        return self.capture(b.a, b.t - 1e-4).get(name)

    def match_matrix(self, b: Boundary, u: float) -> skia.Matrix | None:
        """Whole-scene fit for a match cut: maps the incoming element onto the outgoing one at u = 0."""
        ca = self.capture(b.a, b.t - 1e-4)
        cb = self.capture(b.b, b.b.start)
        name = b.params.get("element") or next((k for k in cb if k in ca), None)
        if not name or name not in ca or name not in cb:
            return None
        ra, rb = ca[name]["rect"], cb[name]["rect"]
        k0 = math.sqrt(max(ra.width() * ra.height(), 1.0) / max(rb.width() * rb.height(), 1.0))
        k = k0 ** (1 - u)
        ax, ay, bx, by = ra.centerX(), ra.centerY(), rb.centerX(), rb.centerY()
        m = skia.Matrix.Translate(bx + (ax - bx) * (1 - u), by + (ay - by) * (1 - u))
        m = skia.Matrix.Concat(m, skia.Matrix.Scale(k, k))
        return skia.Matrix.Concat(m, skia.Matrix.Translate(-bx, -by))

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
        if not self.defer_flash:
            rgb, fl = self.flash_at(int(math.floor(t * self.fps + 0.5 + 1e-9)))
            if fl > 0.002:
                c.drawRect(skia.Rect(0, 0, self.W, self.H), D.paint(D.argb(rgb, fl)))

    def flash_at(self, i: int) -> tuple[tuple[int, int, int], float]:
        """Accent flash for delivered frame i: (rgb, opacity). Flash cuts and hit markers with a
        flash light exactly 2 whole frames (1 for light hits, flash < 0.3) from the first frame at
        or after the cut, at 60-80 % accent (never a white/paper frame)."""
        fl = 0.0
        for b in self.boundaries:
            if b.kind == "flash" or b.params.get("flash"):
                fl = max(fl, TR.flash_frame_alpha(b.t, i, self.fps))
        t = i / self.fps
        for m in self.tl.of("hit"):
            f = float(m.data.get("flash", 0.0))
            if f > 0 and -0.2 <= t - m.t < 0.2:
                fl = max(fl, TR.flash_frame_alpha(m.t, i, self.fps, 2 if f >= 0.3 else 1))
        return self.pal.rgb(self.theme.get("flash", "accent")), fl

    # ------------------------------------------------------------------ info
    def summary(self) -> dict[str, Any]:
        return {"mode": self.mode, "duration": round(self.duration, 3), "size": [self.W, self.H],
                "scenes": [{"id": s.id, "type": s.type, "start": round(s.start, 3), "dur": round(s.dur, 3)} for s in self.scenes],
                "transitions": [{"t": round(b.t, 3), "kind": b.kind, "dur": round(b.dur, 4), "beats": round(b.beats, 3),
                                 "window": [round(b.t0, 4), round(b.t1, 4)],  # type: ignore[arg-type]
                                 "beat": round(self.tl.time_to_beat(b.t), 3)} for b in self.boundaries],
                "timeline": self.tl.summary()}
