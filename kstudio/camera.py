"""2.5D camera: depth parallax, a dolly with continuity across cuts, handheld drift.

The camera is a pure function of (scene, film time), so any worker can draw any frame.

Model (all optional keys of the mode/scene ``camera`` dict, px values are at 1080):
    push     dolly per shot: the depth-1 layer grows by this fraction over the shot
    carry    "world" (default): the backdrop dolly, drift and roll run continuously across
             cuts, each shot's content starts at rest scale but inherits the dolly speed.
             "full": the content scale also carries over from the previous shot (used for
             match cuts). "none": v0.1 behaviour (everything resets per shot).
    drift    handheld amplitude (px); fbm noise, scaled by section energy (energy 2 = 1x)
    drift_hz breathing frequency (Hz, default 0.35)
    energy   how strongly drift follows energy (0 = constant, default 0.45)
    roll     roll amplitude (radians, as in v0.1) driven by the same slow noise
    truck    deliberate lateral move over a shot (px, alternating direction per shot)
    yaw / pitch   degrees, a number or [from, to] animated over the shot (in_out_quint)
    ground_depth  parallax depth of the ground (default 3; 1 = moves with the content)
    bump / shake  beat pulse scale and hit-driven shake (as in v0.1)

Depth: a layer at depth d (1 = the scene plane, >1 further away, <1 closer) is scaled by
d / (d - z) and translated by the camera offset / d, so far layers move less (parallax).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import skia

from . import draw as D
from . import ease as E

if TYPE_CHECKING:  # pragma: no cover
    from .scenes.base import Ctx, Scene

CAMERA_KEYS = ("push", "carry", "drift", "drift_hz", "energy", "roll", "truck", "yaw", "pitch",
               "ground_depth", "bump", "shake", "dist")
WORLD_ZMAX = 0.6     # the backdrop dolly saturates here (depth-3 ground tops out at ~1.25x)
END_SLOPE = 0.8      # dolly is still moving at the end of a shot (never parks dead still)


def in_out_quint(u: float) -> float:
    u = E.clamp(u)
    return 16 * u ** 5 if u < 0.5 else 1 - (-2 * u + 2) ** 5 / 2


def hermite(u: float, m0: float, m1: float) -> float:
    """Cubic from 0 to 1 with start slope m0 and end slope m1 (monotone for slopes in [0, 3])."""
    u = E.clamp(u)
    u2, u3 = u * u, u * u * u
    return (u3 - 2 * u2 + u) * m0 + (-2 * u3 + 3 * u2) + (u3 - u2) * m1


def push_z(push: float) -> float:
    """Dolly distance that scales the depth-1 plane by (1 + push)."""
    return 1.0 - 1.0 / (1.0 + max(push, -0.5))


@dataclass
class CamState:
    """Camera at one instant. Offsets are px; angles degrees; z is dolly distance."""
    cx: float
    cy: float
    x: float = 0.0          # continuous camera offset (drift), applies with parallax
    y: float = 0.0
    tx: float = 0.0         # shot-local offset (truck), applies with parallax, not to the ground
    z: float = 0.0          # shot dolly (content plane scale = 1 / (1 - z))
    zw: float = 0.0         # world dolly (ground / backdrop, continuous across cuts)
    roll: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    bump: float = 0.0       # additive scale (beat pulse)
    sx: float = 0.0         # shake (screen space, no parallax)
    sy: float = 0.0
    dist: float = 1800.0
    ground_depth: float = 3.0

    def scale_at(self, depth: float = 1.0, world: bool = False) -> float:
        z = self.zw if world else self.z
        d = max(depth, 0.05)
        return d / max(d - z, 0.02) + self.bump

    def matrix(self, depth: float = 1.0, world: bool = False) -> skia.Matrix:
        """2D affine part for a layer at `depth` (tilt is applied separately, see apply())."""
        d = max(depth, 0.05)
        ox = (self.x + (0.0 if world else self.tx)) / d + self.sx
        oy = self.y / d + self.sy
        s = self.scale_at(depth, world)
        m = skia.Matrix.Translate(self.cx + ox, self.cy + oy)
        m = skia.Matrix.Concat(m, skia.Matrix.RotateDeg(self.roll))
        m = skia.Matrix.Concat(m, skia.Matrix.Scale(s, s))
        return skia.Matrix.Concat(m, skia.Matrix.Translate(-self.cx, -self.cy))

    def tilt(self) -> skia.M44 | None:
        if abs(self.yaw) < 1e-4 and abs(self.pitch) < 1e-4:
            return None
        return D.persp3d(self.cx, self.cy, math.radians(self.pitch), math.radians(self.yaw), 0.0, dist=self.dist)

    def apply(self, c: skia.Canvas, depth: float = 1.0) -> None:
        t = self.tilt()
        if t is not None:
            c.concat(t)
        c.concat(self.matrix(depth))

    def relative(self, depth: float, world: bool = False) -> skia.Matrix:
        """Matrix to concat inside a depth-1 camera so a layer behaves as if at `depth`."""
        inv = skia.Matrix()
        if not self.matrix(1.0).invert(inv):
            return skia.Matrix()
        return skia.Matrix.Concat(inv, self.matrix(depth, world))

    def as_dict(self) -> dict[str, float]:
        return {k: round(float(getattr(self, k)), 5) for k in ("x", "y", "tx", "z", "zw", "roll", "yaw", "pitch", "bump")}


# ---------------------------------------------------------------------- shot linking

def link_shots(scenes: list[Scene]) -> None:
    """Chain camera continuity data through consecutive scenes (called once by the Film)."""
    zw, prev = 0.0, None
    for i, sc in enumerate(scenes):
        cam = sc.cam
        push = float(sc.p.get("push") if sc.p.get("push") is not None else cam.get("push", 0.04))
        carry = str(cam.get("carry", "world"))
        m0 = 0.55
        if prev is not None and carry != "none":
            p_prev, d_prev = prev._cam_link["push"], max(prev.dur, 1e-3)
            v_end = p_prev * END_SLOPE / d_prev               # scale units per second
            m0 = E.clamp(v_end * max(sc.dur, 1e-3) / push, 0.25, 2.5) if abs(push) > 1e-6 else 0.0
        s_in = 1.0
        if prev is not None and carry == "full":
            s_in = prev._cam_link["s_end"]
        s_end = s_in * (1.0 + push)
        sc._cam_link = {"index": i, "push": push, "m0": m0, "zw0": zw, "s_in": s_in, "s_end": s_end,
                        "carry": carry}
        zw += push_z(push)
        prev = sc


def _energy_smooth(ctx: Ctx, span: float = 0.6, n: int = 6) -> float:
    tl = ctx.tl
    return sum(tl.energy_at(ctx.t - span * k / (n - 1)) for k in range(n)) / n


def _angle(v: Any, u: float) -> float:
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return float(v[0]) + (float(v[1]) - float(v[0])) * in_out_quint(u)
    return float(v or 0.0)


def shot_state(sc: Scene, ctx: Ctx) -> CamState:
    """Camera state for scene `sc` at the sample described by ctx (ctx.lt, ctx.t)."""
    cam = sc.cam
    link = getattr(sc, "_cam_link", None) or {"index": 0, "push": float(cam.get("push", 0.04)), "m0": 0.55,
                                              "zw0": 0.0, "s_in": 1.0, "carry": cam.get("carry", "world")}
    s1 = ctx.s
    d = max(sc.dur, 1e-3)
    u = E.clamp(ctx.lt / d)
    push = link["push"]
    h = hermite(u, link["m0"], END_SLOPE)
    shot_scale = link["s_in"] * (1.0 + push * h)
    z = 1.0 - 1.0 / max(shot_scale, 0.05)
    world = link["carry"] != "none"
    zw_raw = link["zw0"] + push_z(push) * h
    zw = WORLD_ZMAX * math.tanh(zw_raw / WORLD_ZMAX) if world else z
    # deliberate, slow, energy-aware handheld drift (continuous in film time)
    t = ctx.t
    hz = float(cam.get("drift_hz", 0.35))
    k_e = float(cam.get("energy", 0.45))
    e = _energy_smooth(ctx) if k_e else 2.0
    amp = float(cam.get("drift", 0.0)) * s1 * ((1 - k_e) + k_e * (0.3 + 0.35 * e))
    x = amp * E.fbm(t * hz, 11, 3)
    y = amp * 0.7 * E.fbm(t * hz * 0.83 + 5.3, 23, 3)
    roll = float(cam.get("roll", 0.0)) * 57.2958 * E.fbm(t * hz * 0.6 + 2.1, 37, 2) * 1.6
    truck = float(cam.get("truck", 0.0)) * s1
    tx = truck * (h - 0.5) * (1 if link["index"] % 2 == 0 else -1)
    bump = float(cam.get("bump", 0.0)) * ctx.tl.pulse(ctx.t, 10.0) * ctx.tl.energy_at(ctx.t) / 3
    shake = float(cam.get("shake", 0.0)) * ctx.tl.hit_env(ctx.t, 14.0)
    sx = shake * 14 * s1 * E.noise1(ctx.t * 37.0, 3)
    sy = shake * 14 * s1 * E.noise1(ctx.t * 41.0, 9)
    return CamState(ctx.cx, ctx.cy, x=x, y=y, tx=tx, z=z, zw=zw, roll=roll,
                    yaw=_angle(cam.get("yaw", 0.0), u), pitch=_angle(cam.get("pitch", 0.0), u),
                    bump=bump, sx=sx, sy=sy, dist=float(cam.get("dist", 1800.0)) * s1,
                    ground_depth=float(cam.get("ground_depth", 3.0)))
