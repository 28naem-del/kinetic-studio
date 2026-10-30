"""Scene base class, render context and registry.

Beat units: every timing parameter of a scene (`stagger`, `per`, `first`, `delay`, ...; see
TIME_PARAMS) accepts seconds or beat expressions ("1b", "1/2b", "1bar"). They are resolved
to seconds on the scene's own grid when the scene is built, so scene code keeps reading
floats. Inside draw(), `ctx.beats(n)`, `ctx.time(expr)`, `ctx.beat_len` and `ctx.local_beat`
give grid-aware timing.

Camera: Scene.camera(ctx) applies the 2.5D camera from kstudio.camera (depth-1 plane) and
leaves the state on ctx.cam; `with ctx.layer(depth):` draws a layer with parallax.
Match cuts: `ctx.publish(name, rect)` exposes an element's on-screen rect; the next scene
calls `ctx.handoff(name, rect)` to start that element where the previous scene left it.
"""
from __future__ import annotations

import contextlib
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, ClassVar

import numpy as np
import skia

from .. import camera as CAM
from .. import draw as D
from .. import ease as E
from .. import fonts as F
from ..timeline import Timeline, TimeExprError, is_beat_expr

# Scene parameters that hold a time in seconds (or a beat expression) relative to the scene.
TIME_PARAMS: tuple[str, ...] = ("stagger", "per", "first", "grow", "count", "delay", "lead", "tail", "tap_at",
                                "rate", "hold", "gap", "settle", "enter_dur", "exit_dur", "beat_offset")
# List parameters whose items (numbers, strings, or dicts with "t"/"at"/"dur") are times.
TIME_LIST_PARAMS: tuple[str, ...] = ("times", "cues", "hits", "words")
# Parameters that hold a count of beats rather than seconds, per scene type.
BEAT_COUNT_PARAMS: dict[str, tuple[str, ...]] = {"karaoke": ("per",)}

REGISTRY: dict[str, type[Scene]] = {}


def register(name: str) -> Callable[[type[Scene]], type[Scene]]:
    def deco(cls: type[Scene]) -> type[Scene]:
        cls.type = name
        REGISTRY[name] = cls
        return cls
    return deco


@dataclass
class Ctx:
    """Everything a scene needs to draw one sample."""
    c: skia.Canvas
    W: int
    H: int
    pal: D.Palette
    theme: dict
    tl: Timeline
    t: float            # global film time (s)
    lt: float = 0.0     # local scene time (s)
    dur: float = 1.0    # scene duration (s)
    assets: str = "."
    seed: int = 0
    start: float = 0.0  # scene start in film time (s)
    fps: float = 30.0
    cam: Any = None     # camera.CamState after Scene.camera(); None when the camera is off
    film: Any = None    # owning Film (match-cut registry); None when drawn standalone
    scene: Any = None   # the Scene being drawn
    capture: dict | None = None  # set while the Film records published rects

    @property
    def s(self) -> float:
        """Unit scale: 1.0 when the short side is 1080 px."""
        return min(self.W, self.H) / 1080.0

    @property
    def portrait(self) -> bool:
        return self.H > self.W * 1.15

    @property
    def landscape(self) -> bool:
        return self.W > self.H * 1.15

    @property
    def cx(self) -> float:
        return self.W / 2

    @property
    def cy(self) -> float:
        return self.H / 2

    @property
    def margin(self) -> float:
        return 0.075 * min(self.W, self.H)

    @property
    def safe_w(self) -> float:
        return self.W - 2 * self.margin

    @property
    def beat(self) -> float:
        return self.tl.spb

    # -- beat-unit helpers ------------------------------------------------
    @property
    def beat_len(self) -> float:
        """Seconds per beat at the scene start (grid aware)."""
        b0 = self.tl.time_to_beat(self.start)
        return self.tl.beat_to_time(b0 + 1) - self.tl.beat_to_time(b0)

    @property
    def frame(self) -> float:
        return 1.0 / max(self.fps, 1.0)

    def beats(self, n: float) -> float:
        """Scene-local seconds of `n` beats from the scene start."""
        return self.tl.beat_to_time(self.tl.time_to_beat(self.start) + n) - self.start

    def time(self, expr: Any) -> float:
        """Scene-local seconds for a duration expression ("2b", "1/2b", "1bar", 0.4)."""
        return self.tl.resolve_dur(expr, self.start)

    @property
    def local_beat(self) -> float:
        """Beats elapsed since the scene start (fractional)."""
        return self.tl.time_to_beat(self.start + self.lt) - self.tl.time_to_beat(self.start)

    def beat_u(self, at: Any, dur: Any = "1b") -> float:
        """0..1 progress of a window starting at `at` and lasting `dur` (both beat or seconds)."""
        a = self.time(at)
        d = self.tl.resolve_dur(dur, self.start + a)
        return E.clamp((self.lt - a) / max(d, 1e-6))

    # -- camera layers ----------------------------------------------------
    @contextlib.contextmanager
    def layer(self, depth: float = 1.0, world: bool = False) -> Iterator[None]:
        """Draw inside with parallax as if the layer sat at `depth` (1 = scene plane)."""
        if self.cam is None or (abs(depth - 1.0) < 1e-6 and not world):
            yield
            return
        self.c.save()
        self.c.concat(self.cam.relative(depth, world))
        try:
            yield
        finally:
            self.c.restore()

    # -- match-cut hand-off -----------------------------------------------
    def publish(self, name: str, rect: Any, **data: Any) -> None:
        """Expose element `name` (rect in current local coords) for a match cut into the next scene."""
        if self.capture is None:
            return
        r = _as_rect(rect)
        self.capture[name] = {"rect": self.c.getTotalMatrix().mapRect(r), "data": data}

    def handoff(self, name: str, rect: Any, dur: Any = "1/2b", ease: Callable[[float], float] | None = None,
                at: Any = 0.0) -> skia.Rect:
        """Where to draw element `name` now: starts at the previous scene's published rect and
        eases to `rect` over `dur` from `at`. Returns `rect` when nothing was handed off."""
        target = _as_rect(rect)
        film = self.film
        if film is None or self.scene is None:
            return target
        src = film.handoff_source(self.scene, name)
        if src is None:
            return target
        inv = skia.Matrix()
        if not self.c.getTotalMatrix().invert(inv):
            return target
        a = inv.mapRect(src["rect"])
        u = (ease or E.out_expo)(self.beat_u(at, dur))
        return skia.Rect.MakeLTRB(a.left() + (target.left() - a.left()) * u, a.top() + (target.top() - a.top()) * u,
                                  a.right() + (target.right() - a.right()) * u,
                                  a.bottom() + (target.bottom() - a.bottom()) * u)

    def handed_off(self, name: str) -> bool:
        return self.film is not None and self.scene is not None and self.film.handoff_source(self.scene, name) is not None

    def font(self, role: str, size: float, weight: int = 700, width: float = 100.0, **kw) -> skia.Font:
        return F.font(self.theme.get(role, role), size, weight, width, **kw)

    def fit(self, role: str, text: str, max_w: float, max_size: float, weight: int = 800,
            width: float = 100.0, tracking: float = 0.0, **kw) -> skia.Font:
        return D.fit_font(self.theme.get(role, role), text, max_w, max_size, weight, width, tracking, **kw)


@dataclass
class Cue:
    """A scene-relative marker the scene asks the film to place (sfx, hit, cue)."""
    kind: str
    at: float
    name: str = ""
    data: dict[str, Any] = field(default_factory=dict)


def _as_rect(r: Any) -> skia.Rect:
    if isinstance(r, skia.Rect):
        return r
    x, y, w, h = (float(v) for v in r)
    return skia.Rect.MakeXYWH(x, y, w, h)


class Scene:
    type: ClassVar[str] = "scene"
    defaults: ClassVar[dict[str, Any]] = {}
    # extra timing keys (seconds/beat expressions) and beat-count keys for a scene class
    time_params: ClassVar[tuple[str, ...]] = ()
    beat_params: ClassVar[tuple[str, ...]] = ()

    def __init__(self, params: dict[str, Any], start: float, dur: float, tl: Timeline, mode_cfg: dict):
        self.p_raw: dict[str, Any] = {**self.defaults, **params}
        self.start = start
        self.tl = tl
        self.snap_div = _snap_div(mode_cfg.get("snap"))
        self.p: dict[str, Any] = self._resolve_times(dict(self.p_raw))
        self.dur = dur
        self.mode = mode_cfg
        self.id = params.get("id", "")
        cam = dict(mode_cfg.get("camera", {}))
        cam.update(params.get("camera", {}) if isinstance(params.get("camera"), dict) else {})
        self.cam = cam
        self.camera_on = params.get("camera", True) is not False
        self.ground = params.get("ground", "bg")
        self.ground_style = params.get("ground_style")

    @property
    def end(self) -> float:
        return self.start + self.dur

    # -- hooks -----------------------------------------------------------
    def cues(self) -> list[Cue]:
        """Sound + hit markers relative to the scene start. Override per scene."""
        return []

    def shutter(self, lt: float) -> float:
        """Shutter multiplier at local time (1 = mode default). Override for smears."""
        return 1.0

    def ground_at(self, lt: float) -> str:
        """Ground colour key at local time (scenes that flip grounds override this)."""
        return self.ground

    def draw(self, ctx: Ctx) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers ---------------------------------------------------------
    def bt(self, beats: float) -> float:
        """Seconds for `beats` beats starting at this scene's start (grid aware)."""
        return self.tl.beat_to_time(self.tl.time_to_beat(self.start) + beats) - self.start

    def beats(self, n: float) -> float:
        """Alias of bt(): scene-local seconds of `n` beats."""
        return self.bt(n)

    def time(self, expr: Any) -> float:
        """Scene-local seconds for a duration expression ("2b", "1/2b", 0.4), snapped when enabled."""
        return self._dur(expr)

    @property
    def beat_len(self) -> float:
        return self.bt(1.0)

    def _resolve_times(self, p: dict[str, Any]) -> dict[str, Any]:
        keys = set(TIME_PARAMS) | set(self.time_params)
        beat_keys = set(BEAT_COUNT_PARAMS.get(self.type, ())) | set(self.beat_params)
        spb = max(self.bt(1.0), 1e-6)
        for k in list(p):
            v = p[k]
            try:
                if k in beat_keys:
                    if isinstance(v, str):
                        p[k] = self._dur(v) / spb if not is_beat_expr(v) else self.tl.beats_of(self._dur(v), self.start)
                elif k in keys:
                    if isinstance(v, str) or (self.snap_div and isinstance(v, (int, float)) and not isinstance(v, bool)):
                        p[k] = self._dur(v)
                elif k in TIME_LIST_PARAMS and isinstance(v, list):
                    p[k] = [self._time_item(x) for x in v]
            except TimeExprError as e:
                raise TimeExprError(f"scene {self.type!r} parameter {k!r}: {e}") from None
        return p

    def _dur(self, v: Any) -> float:
        d = self.tl.resolve_dur(v, self.start)
        if self.snap_div and not is_beat_expr(v) and d > 0:
            d = self.tl.snap_dur(d, self.start, self.snap_div, min_units=1.0)
        return d

    def _time_item(self, x: Any) -> Any:
        if isinstance(x, str) and is_beat_expr(x):
            return self._dur(x)
        if isinstance(x, dict):
            out = dict(x)
            for key in ("t", "at", "dur"):
                if isinstance(out.get(key), str):
                    with contextlib.suppress(TimeExprError):
                        out[key] = self._dur(out[key])
            return out
        return x

    def camera_state(self, ctx: Ctx) -> CAM.CamState:
        return CAM.shot_state(self, ctx)

    def camera(self, ctx: Ctx) -> None:
        if not self.camera_on:
            return
        st = self.camera_state(ctx)
        ctx.cam = st
        st.apply(ctx.c, 1.0)


def _snap_div(v: Any) -> float | None:
    """Spec "snap" option -> grid subdivision: true/"beat" = 1, "8th" = 2, "16th" = 4, "bar" = 1/4, number."""
    if v in (None, False, 0, "off", "none"):
        return None
    if v is True or v == "beat":
        return 1.0
    named = {"8th": 2.0, "eighth": 2.0, "16th": 4.0, "sixteenth": 4.0, "bar": 0.25, "half": 2.0, "quarter": 1.0}
    if isinstance(v, str):
        if v in named:
            return named[v]
        return float(v)
    return float(v)


# ---------------------------------------------------------------------- grounds

GROUND_STYLES = ("solid", "gradient", "grid", "dots", "blobs", "paper", "mesh")


def _luma(rgb: tuple[int, int, int]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _lift(pal: D.Palette, base: tuple[int, int, int], key: str, delta: float) -> tuple[int, int, int]:
    """Mix `key` into the base until the luma differs by about `delta` (0..1), for visible patterns."""
    tgt = pal.rgb(key)
    gap = abs(_luma(tgt) - _luma(base)) / 255.0
    if gap < 1e-3:
        tgt = (255, 255, 255) if _luma(base) < 128 else (0, 0, 0)
        gap = abs(_luma(tgt) - _luma(base)) / 255.0
    return D.mix_rgb(base, tgt, min(1.0, delta / max(gap, 1e-3)))


@lru_cache(maxsize=8)
def _paper_image(w: int, h: int, base: tuple[int, int, int], ink: tuple[int, int, int], seed: int) -> skia.Image:
    """A low-res paper/noise field (cloudy fibres + speckle) tinted between base and ink."""
    import cv2
    rng = np.random.default_rng(seed)
    f = np.zeros((h, w), np.float32)
    for sigma, amp in ((w / 10, 0.55), (w / 40, 0.3), (1.2, 0.35)):
        n = rng.normal(0, 1, (h, w)).astype(np.float32)
        n = cv2.GaussianBlur(n, (0, 0), max(sigma, 0.6))
        f += amp * n / max(float(n.std()), 1e-6)
    f = (f - f.mean()) / max(float(f.std()), 1e-6)
    f = np.clip(f * 0.33 + 0.5, 0, 1)[..., None]
    b, k = np.array(base, np.float32), np.array(ink, np.float32)
    rgb = b + (k - b) * f
    rgba = np.concatenate([rgb, np.full((h, w, 1), 255, np.float32)], -1).astype(np.uint8)
    return skia.Image.fromarray(np.ascontiguousarray(rgba), colorType=skia.ColorType.kRGBA_8888_ColorType)


def draw_ground(ctx: Ctx, ground: str = "bg", style: str | None = None) -> None:
    """Fill the frame with a ground colour key and an optional pattern style.

    Styles: solid, gradient, grid, dots, blobs, paper (noise field), mesh (soft gradient mesh).
    Patterns are tuned to read at >= 6 % luma from the base and are skipped on flat
    accent/paper grounds (except paper/mesh, which tint whatever ground they are on)."""
    c, pal, W, H = ctx.c, ctx.pal, ctx.W, ctx.H
    style = style or ctx.theme.get("ground_style", "solid")
    base = pal.rgb(ground)
    c.drawRect(skia.Rect(-W, -H, 2 * W, 2 * H), D.paint(D.argb(base)))
    if ground != "bg" and style in ("grid", "dots", "blobs"):
        style = "solid"  # flat accent/paper grounds stay clean
    t = ctx.t
    x0, x1, y0, y1 = -0.15 * W, 1.15 * W, -0.15 * H, 1.15 * H
    if style == "gradient":
        top = D.argb(D.mix_rgb(base, pal.rgb("bg2"), 0.0))
        bot = D.argb(pal.rgb("bg2"))
        c.drawRect(skia.Rect(-W, -H, 2 * W, 2 * H), D.paint(0xFF000000, shader=D.linear_grad(0, 0, 0, H, [top, bot])))
    elif style == "grid":
        glow = D.radial_grad(W * 0.5, H * 0.38, max(W, H) * 0.7, [pal("bg2", 0.9), pal("bg", 0.0)])
        c.drawRect(skia.Rect(-W, -H, 2 * W, 2 * H), D.paint(0xFF000000, shader=glow))
        step = 90 * ctx.s
        off = (t * 6 * ctx.s) % step
        minor = D.paint(D.argb(_lift(pal, base, "muted", 0.075)), stroke=1.6 * ctx.s, cap_round=False)
        major = D.paint(D.argb(_lift(pal, base, "muted", 0.13)), stroke=2.0 * ctx.s, cap_round=False)
        k0 = math.floor((x0 - off) / step)
        for k in range(k0, k0 + int((x1 - x0) / step) + 3):
            x = k * step + off
            c.drawLine(x, y0, x, y1, major if k % 4 == 0 else minor)
        k0 = math.floor((y0 - off * 0.5) / step)
        for k in range(k0, k0 + int((y1 - y0) / step) + 3):
            y = k * step + off * 0.5
            c.drawLine(x0, y, x1, y, major if k % 4 == 0 else minor)
    elif style == "dots":
        step = 48 * ctx.s
        col = _lift(pal, base, "muted", 0.16)
        hi = _lift(pal, base, "accent", 0.22)
        off = (t * 10 * ctx.s) % step
        r = 2.3 * ctx.s
        p, ph = D.paint(D.argb(col)), D.paint(D.argb(hi))
        # one diagonal band of brighter dots sweeps slowly (reads as a lit surface, not a template)
        band = (t * 0.08) % 1.6 - 0.3
        ky0 = math.floor((y0 - off) / step)
        for ky in range(ky0, ky0 + int((y1 - y0) / step) + 3):
            y = ky * step + off
            x = step / 2 + math.floor(x0 / step) * step
            while x < x1:
                q = (x / W + y / H) * 0.5 - band
                c.drawCircle(x, y, r, ph if abs(q) < 0.06 else p)
                x += step
    elif style == "blobs":
        R = max(W, H)
        specs = [("accent", 0.33, 0.30, 0.55, 0.0), ("accent2", 0.75, 0.62, 0.45, 2.1), ("bg2", 0.4, 0.85, 0.6, 4.2)]
        for key, bx, by, br, ph in specs:
            x = W * (bx + 0.08 * math.sin(t * 0.35 + ph))
            y = H * (by + 0.06 * math.cos(t * 0.29 + ph))
            col = _lift(pal, base, key, 0.16 if key != "bg2" else 0.09)
            sh = D.radial_grad(x, y, R * br, [D.argb(col, 1.0), D.argb(col, 0.45), D.argb(col, 0.0)], [0.0, 0.45, 1.0])
            c.drawRect(skia.Rect(-W, -H, 2 * W, 2 * H), D.paint(0xFF000000, shader=sh))
    elif style == "paper":
        ink = _lift(pal, base, "ink" if ground == "bg" else "ink_dark", 0.085)
        img = _paper_image(max(16, W // 4), max(16, H // 4), base, ink, 7)
        dx = (t * 3.0 * ctx.s) % (W * 0.1)
        c.drawImageRect(img, skia.Rect(x0 - dx, y0, x1 - dx + W * 0.1, y1),
                        skia.SamplingOptions(skia.FilterMode.kLinear), D.paint(0xFFFFFFFF))
    elif style == "mesh":
        cols_ = [base, _lift(pal, base, "bg2", 0.08), _lift(pal, base, "accent", 0.12), _lift(pal, base, "accent2", 0.10)]
        nx, ny = 4, 6
        pts, colors = [], []
        for j in range(ny):
            for i in range(nx):
                u, v = i / (nx - 1), j / (ny - 1)
                wob = 0.05
                px = x0 + (x1 - x0) * u + W * wob * math.sin(t * 0.21 + j * 1.7 + i * 0.6) * (0 < i < nx - 1)
                py = y0 + (y1 - y0) * v + H * wob * math.cos(t * 0.17 + i * 1.3 + j * 0.4) * (0 < j < ny - 1)
                pts.append(skia.Point(px, py))
                k = (i * 7 + j * 5 + 3) % 11
                colors.append(D.argb(cols_[0] if k < 5 else cols_[1] if k < 8 else cols_[2] if k < 10 else cols_[3]))
        idx = []
        for j in range(ny - 1):
            for i in range(nx - 1):
                a = j * nx + i
                idx += [a, a + 1, a + nx, a + 1, a + nx + 1, a + nx]
        verts = skia.Vertices(skia.Vertices.VertexMode.kTriangles_VertexMode, pts, None, colors, idx)
        c.drawVertices(verts, D.paint(0xFFFFFFFF))


def get_scene_class(name: str) -> type[Scene]:
    if name not in REGISTRY:
        raise KeyError(f"unknown scene type {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name]
