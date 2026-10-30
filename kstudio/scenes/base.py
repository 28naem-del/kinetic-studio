"""Scene base class, render context and registry."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

import skia

from .. import draw as D
from .. import ease as E
from .. import fonts as F
from ..timeline import Timeline

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


class Scene:
    type: ClassVar[str] = "scene"
    defaults: ClassVar[dict[str, Any]] = {}

    def __init__(self, params: dict[str, Any], start: float, dur: float, tl: Timeline, mode_cfg: dict):
        self.p: dict[str, Any] = {**self.defaults, **params}
        self.start = start
        self.dur = dur
        self.tl = tl
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

    def camera(self, ctx: Ctx) -> None:
        if not self.camera_on:
            return
        lt, d = ctx.lt, max(self.dur, 1e-3)
        p_push = self.p.get("push")
        push = float(p_push if p_push is not None else self.cam.get("push", 0.04))
        roll = float(self.cam.get("roll", 0.0))
        bump = float(self.cam.get("bump", 0.0)) * ctx.tl.pulse(ctx.t, 10.0) * ctx.tl.energy_at(ctx.t) / 3
        # Linear+eased blend: the push never reaches zero velocity, so no scene "parks" dead still.
        u = min(max(lt / d, 0.0), 1.0)
        sc = 1.0 + push * (0.55 * u + 0.45 * E.in_out_quad(u)) + bump
        rot = roll * math.sin(lt * 0.9 + self.start) * 57.2958
        shake = float(self.cam.get("shake", 0.0)) * ctx.tl.hit_env(ctx.t, 14.0)
        # "drift": slow handheld-style float in px @1080 (continuous across scene boundaries).
        drift = float(self.cam.get("drift", 0.0)) * ctx.s
        dx = shake * 14 * ctx.s * E.noise1(ctx.t * 37.0, 3) + drift * math.sin(ctx.t * 0.83 + 0.4)
        dy = shake * 14 * ctx.s * E.noise1(ctx.t * 41.0, 9) + drift * 0.7 * math.sin(ctx.t * 0.61 + 1.9)
        c = ctx.c
        c.translate(ctx.cx + dx, ctx.cy + dy)
        c.rotate(rot)
        c.scale(sc, sc)
        c.translate(-ctx.cx, -ctx.cy)


# ---------------------------------------------------------------------- grounds

def draw_ground(ctx: Ctx, ground: str = "bg", style: str | None = None) -> None:
    c, pal, W, H = ctx.c, ctx.pal, ctx.W, ctx.H
    style = style or ctx.theme.get("ground_style", "solid")
    base = pal.rgb(ground)
    c.drawRect(skia.Rect(-W, -H, 2 * W, 2 * H), D.paint(D.argb(base)))
    if ground != "bg" and style in ("grid", "dots", "blobs"):
        style = "solid"  # flat accent/paper grounds stay clean
    t = ctx.t
    if style == "gradient":
        top = D.argb(D.mix_rgb(base, pal.rgb("bg2"), 0.0))
        bot = D.argb(pal.rgb("bg2"))
        c.drawRect(skia.Rect(-W, -H, 2 * W, 2 * H), D.paint(0, shader=D.linear_grad(0, 0, 0, H, [top, bot])))
    elif style == "grid":
        step = 90 * ctx.s
        off = (t * 6 * ctx.s) % step
        p = D.paint(pal(ctx.theme.get("muted", "#888888"), 0.10), stroke=1.2 * ctx.s, cap_round=False)
        x = -step + off
        while x < W + step:
            c.drawLine(x, -10, x, H + 10, p)
            x += step
        y = -step + off * 0.5
        while y < H + step:
            c.drawLine(-10, y, W + 10, y, p)
            y += step
        glow = D.radial_grad(W * 0.5, H * 0.38, max(W, H) * 0.7,
                             [pal("bg2", 0.9), pal("bg", 0.0)])
        c.drawRect(skia.Rect(0, 0, W, H), D.paint(0, shader=glow))
    elif style == "dots":
        step = 48 * ctx.s
        p = D.paint(pal("muted", 0.22))
        off = (t * 10 * ctx.s) % step
        y = -step + off
        while y < H + step:
            x = step / 2
            while x < W:
                c.drawCircle(x, y, 1.6 * ctx.s, p)
                x += step
            y += step
    elif style == "blobs":
        R = max(W, H)
        specs = [("accent", 0.33, 0.30, 0.55, 0.0), ("accent2", 0.75, 0.62, 0.45, 2.1), ("bg2", 0.4, 0.85, 0.6, 4.2)]
        for key, bx, by, br, ph in specs:
            x = W * (bx + 0.08 * math.sin(t * 0.35 + ph))
            y = H * (by + 0.06 * math.cos(t * 0.29 + ph))
            sh = D.radial_grad(x, y, R * br, [pal(key, 0.30 if key != "bg2" else 0.8), pal(key, 0.0)])
            c.drawRect(skia.Rect(0, 0, W, H), D.paint(0, shader=sh))


def get_scene_class(name: str) -> type[Scene]:
    if name not in REGISTRY:
        raise KeyError(f"unknown scene type {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name]
