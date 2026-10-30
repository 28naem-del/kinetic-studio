"""Easing curves, springs, stagger maths, envelopes and deterministic noise. All functions are pure.

House easing grammar (what the scenes should reach for)
-------------------------------------------------------
- Entries decelerate: ``out_expo`` (snap then glide, the default) or ``out_quint`` (softer).
- Exits accelerate and run 30-40 % shorter than the entry: ``in_quart`` or ``in_expo``.
- Moves from A to B on screen (camera, layout, swaps): ``in_out_quint``.
- Never ease-in-out a single entrance.
- Overshoot is opt-in only: ``back(0.04)``; overshoot is capped at 4 % (``MAX_OVERSHOOT``).
- Springs are for physical objects only (a device, a pill, a sticker), never for type or opacity:
  ``spring(0.6, 0.25)`` = settle in 0.6 s with bounce 0.25.

Every easing can be addressed by a string through :func:`resolve` (cached)::

    resolve("out_expo")                      # any name in EASINGS (also "outExpo", "expo-out")
    resolve("enter") / ("exit") / ("move")   # house roles, see ROLES
    resolve("cubic-bezier(0.2,0.8,0.2,1)")   # CSS-compatible bezier
    resolve("spring(0.6,0.25)")              # settle duration in seconds + bounce (0..1)
    resolve("spring(k=170,c=26)")            # raw physics: stiffness/damping/mass/velocity
    resolve("back(0.04)")                    # out-back with a 4 % overshoot
    resolve("steps(4)")                      # stop-motion cadence
    resolve("m3.emphasized_decelerate")      # design-system tokens, see TOKENS

The result is an :class:`Ease`: call it with normalised progress u in [0, 1]. For springs
``Ease.duration`` holds the natural (settle) duration in seconds, so ``animate(t, start, e)``
lands exactly ``e.duration`` seconds after ``start`` (e.g. on the next beat).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Sequence


def clamp(x: float, a: float = 0.0, b: float = 1.0) -> float:
    return a if x < a else b if x > b else x


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def prog(t: float, start: float, dur: float) -> float:
    """Normalised 0..1 progress of t through [start, start+dur]."""
    if dur <= 0:
        return 1.0 if t >= start else 0.0
    return clamp((t - start) / dur)


def remap(x: float, a: float, b: float, c: float, d: float,
          ease: "Callable[[float], float] | str | None" = None, clamp_: bool = True) -> float:
    """Map x from [a, b] to [c, d] (After Effects linear()/ease()), optionally through an easing."""
    u = (x - a) / (b - a) if b != a else 1.0
    if clamp_:
        u = clamp(u)
    if ease is not None:
        u = resolve(ease)(u)
    return c + (d - c) * u


# ---------------------------------------------------------------------------- Penner family
# Formulas re-derived from the polynomial / exponential definitions (no third-party code).

def linear(t: float) -> float:
    return clamp(t)


def in_quad(t: float) -> float:
    t = clamp(t); return t * t


def out_quad(t: float) -> float:
    t = clamp(t); return 1 - (1 - t) ** 2


def in_out_quad(t: float) -> float:
    t = clamp(t); return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


def in_cubic(t: float) -> float:
    t = clamp(t); return t ** 3


def out_cubic(t: float) -> float:
    t = clamp(t); return 1 - (1 - t) ** 3


def in_out_cubic(t: float) -> float:
    t = clamp(t); return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def in_quart(t: float) -> float:
    """House exit curve: accelerate away."""
    t = clamp(t); return t ** 4


def out_quart(t: float) -> float:
    t = clamp(t); return 1 - (1 - t) ** 4


def in_out_quart(t: float) -> float:
    t = clamp(t); return 8 * t ** 4 if t < 0.5 else 1 - (-2 * t + 2) ** 4 / 2


def in_quint(t: float) -> float:
    t = clamp(t); return t ** 5


def out_quint(t: float) -> float:
    """House soft entry: like out_expo with a gentler landing."""
    t = clamp(t); return 1 - (1 - t) ** 5


def in_out_quint(t: float) -> float:
    """House move curve (A -> B on screen)."""
    t = clamp(t); return 16 * t ** 5 if t < 0.5 else 1 - (-2 * t + 2) ** 5 / 2


def in_sine(t: float) -> float:
    t = clamp(t); return 1 - math.cos(t * math.pi / 2)


def out_sine(t: float) -> float:
    t = clamp(t); return math.sin(t * math.pi / 2)


def in_out_sine(t: float) -> float:
    t = clamp(t); return -(math.cos(math.pi * t) - 1) / 2


def in_circ(t: float) -> float:
    t = clamp(t); return 1 - math.sqrt(1 - t * t)


def out_circ(t: float) -> float:
    t = clamp(t); return math.sqrt(1 - (t - 1) ** 2)


def in_expo(t: float) -> float:
    t = clamp(t); return 0.0 if t == 0 else 2 ** (10 * t - 10)


def out_expo(t: float) -> float:
    """House default entry: snap then glide (82 % of the way at t = 0.25)."""
    t = clamp(t); return 1.0 if t == 1 else 1 - 2 ** (-10 * t)


def in_out_expo(t: float) -> float:
    t = clamp(t)
    if t in (0.0, 1.0):
        return t
    return 2 ** (20 * t - 10) / 2 if t < 0.5 else (2 - 2 ** (-20 * t + 10)) / 2


def out_back(t: float, k: float = 1.70158) -> float:
    """Classic out-back. The default k = 1.70158 overshoots 10 %: prefer ``back(0.04)``."""
    t = clamp(t) - 1
    return 1 + (k + 1) * t ** 3 + k * t ** 2


def out_elastic(t: float) -> float:
    t = clamp(t)
    if t in (0.0, 1.0):
        return t
    return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * (2 * math.pi) / 3) + 1


def spring(t: float, freq: float = 4.5, damp: float = 6.0) -> float:
    """Legacy closed-form wobble settling to 1. t in seconds (not normalised).
    New code should use :class:`Spring` / ``resolve("spring(dur,bounce)")``."""
    if t <= 0:
        return 0.0
    return 1 - math.exp(-damp * t) * math.cos(2 * math.pi * freq * t)


# ---------------------------------------------------------------------------- back(overshoot)
MAX_OVERSHOOT = 0.04  # house cap for back() overshoot (fraction of the travel)


def back_overshoot(k: float) -> float:
    """Peak overshoot fraction of ``out_back(t, k)``: 4k^3 / (27 (k+1)^2) (k=1.70158 -> 0.100)."""
    return 4 * k ** 3 / (27 * (k + 1) ** 2) if k > 0 else 0.0


@lru_cache(maxsize=64)
def back_k(overshoot: float) -> float:
    """Inverse of :func:`back_overshoot` (bisection): the out_back constant for a given overshoot."""
    if overshoot <= 0:
        return 0.0
    lo, hi = 0.0, 50.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if back_overshoot(mid) < overshoot:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def back(overshoot: float = MAX_OVERSHOOT, cap: float = MAX_OVERSHOOT) -> "Ease":
    """Out-back ease that overshoots by ``overshoot`` (fraction of travel, clamped to ``cap``)."""
    ov = max(0.0, min(float(overshoot), float(cap)))
    k = back_k(ov)
    return Ease(lambda u: out_back(u, k), f"back({ov:g})")


# ---------------------------------------------------------------------------- cubic bezier
def cubic_bezier(x1: float, y1: float, x2: float, y2: float) -> "Ease":
    """CSS ``cubic-bezier(x1, y1, x2, y2)``: solve x(s) = u for s (Newton, bisection fallback),
    return y(s). x1, x2 are clamped to [0, 1] as CSS requires; y may overshoot."""
    x1, x2 = clamp(float(x1)), clamp(float(x2))
    y1, y2 = float(y1), float(y2)
    cx = 3 * x1; bx = 3 * (x2 - x1) - cx; ax = 1 - cx - bx
    cy = 3 * y1; by = 3 * (y2 - y1) - cy; ay = 1 - cy - by

    def bx_(s: float) -> float:
        return ((ax * s + bx) * s + cx) * s

    def dbx(s: float) -> float:
        return (3 * ax * s + 2 * bx) * s + cx

    def f(u: float) -> float:
        u = clamp(u)
        if u in (0.0, 1.0):
            return u
        s = u
        for _ in range(8):
            err = bx_(s) - u
            if abs(err) < 1e-7:
                break
            d = dbx(s)
            if abs(d) < 1e-6:
                break
            s -= err / d
        if not (0.0 <= s <= 1.0) or abs(bx_(s) - u) > 1e-6:
            lo, hi = 0.0, 1.0
            s = u
            for _ in range(60):
                xv = bx_(s)
                if abs(xv - u) < 1e-7:
                    break
                if xv < u:
                    lo = s
                else:
                    hi = s
                s = (lo + hi) / 2
        return ((ay * s + by) * s + cy) * s

    return Ease(f, f"cubic-bezier({x1:g},{y1:g},{x2:g},{y2:g})")


def steps(n: int, jump: str = "end") -> "Ease":
    """Stop-motion cadence: n discrete steps (CSS ``steps(n, jump-end)`` / ``jump-start``)."""
    n = max(1, int(n))
    if jump == "start":
        return Ease(lambda u: min(1.0, math.ceil(clamp(u) * n) / n), f"steps({n},start)")
    return Ease(lambda u: 1.0 if u >= 1 else math.floor(clamp(u) * n) / n, f"steps({n})")


# ---------------------------------------------------------------------------- physical spring
@dataclass(frozen=True)
class Spring:
    """Damped harmonic oscillator m x'' + c x' + k (x - 1) = 0, x(0) = 0, x'(0) = velocity.

    Closed form (under-, critically and over-damped branches), so any time can be evaluated
    directly and deterministically. ``velocity`` is in travels per second (hand the velocity of
    an interrupted move to the next spring). Positions are fractions of the travel (0 -> 1).
    """
    stiffness: float = 100.0
    damping: float = 10.0
    mass: float = 1.0
    velocity: float = 0.0

    @property
    def omega0(self) -> float:
        return math.sqrt(self.stiffness / self.mass)

    @property
    def zeta(self) -> float:
        return self.damping / (2 * math.sqrt(self.stiffness * self.mass))

    @property
    def bounce(self) -> float:
        z = self.zeta
        return 1 - z if z <= 1 else 1 / z - 1

    @property
    def overshoot(self) -> float:
        """Peak overshoot fraction for velocity 0 (exp(-zeta pi / sqrt(1 - zeta^2)))."""
        z = self.zeta
        return math.exp(-z * math.pi / math.sqrt(1 - z * z)) if z < 1 else 0.0

    def _state(self, t: float) -> tuple[float, float]:
        """(x, x') at time t >= 0."""
        w0, z, v0 = self.omega0, self.zeta, self.velocity
        if t <= 0:
            return 0.0, v0
        if z < 1 - 1e-9:
            wd = w0 * math.sqrt(1 - z * z)
            a = -1.0
            b = (v0 + z * w0 * a) / wd
            e = math.exp(-z * w0 * t)
            cs, sn = math.cos(wd * t), math.sin(wd * t)
            y = e * (a * cs + b * sn)
            dy = e * ((b * wd - z * w0 * a) * cs - (a * wd + z * w0 * b) * sn)
            return 1 + y, dy
        if z <= 1 + 1e-9:
            a = -1.0
            b = v0 - w0
            e = math.exp(-w0 * t)
            y = e * (a + b * t)
            dy = e * (b - w0 * (a + b * t))
            return 1 + y, dy
        sq = math.sqrt(z * z - 1)
        r1, r2 = -w0 * (z - sq), -w0 * (z + sq)
        c2 = (v0 + r1) / (r2 - r1)
        c1 = -1.0 - c2
        e1, e2 = math.exp(r1 * t), math.exp(r2 * t)
        return 1 + c1 * e1 + c2 * e2, c1 * r1 * e1 + c2 * r2 * e2

    def value(self, t: float) -> float:
        """Position (0 -> 1) at t seconds."""
        return self._state(t)[0]

    def velocity_at(self, t: float) -> float:
        """Velocity (travels per second) at t seconds."""
        return self._state(t)[1]

    def __call__(self, t: float) -> float:
        return self.value(t)

    def settle_time(self, tol: float = 1e-3) -> float:
        """Time after which |x - 1| stays below ``tol`` and |x'| below ``tol * omega0``."""
        return _settle_time(self.stiffness, self.damping, self.mass, self.velocity, tol)

    def ease(self, tol: float = 1e-3) -> "Ease":
        """Normalised ease: u in [0, 1] maps to [0, settle_time]; exactly 1 at u = 1."""
        ts = self.settle_time(tol)

        def f(u: float) -> float:
            if u >= 1:
                return 1.0
            return self.value(max(u, 0.0) * ts)
        return Ease(f, f"spring(k={self.stiffness:g},c={self.damping:g},m={self.mass:g},v={self.velocity:g})",
                    duration=ts)

    # -- constructors ---------------------------------------------------------
    @staticmethod
    def zeta_for_bounce(bounce: float) -> float:
        """bounce 0 = critically damped, (0, 1) = underdamped, < 0 = overdamped (SwiftUI semantics)."""
        b = max(-0.95, min(float(bounce), 0.95))
        return 1 - b if b >= 0 else 1 / (1 + b)

    @classmethod
    def perceptual(cls, duration: float = 0.5, bounce: float = 0.0, velocity: float = 0.0) -> "Spring":
        """Apple-style perceptual duration/bounce: k = (2 pi / d)^2, c = 4 pi zeta / d, m = 1.
        (The tail keeps moving after ``duration``; use :meth:`settling` to land on a beat.)"""
        d = max(float(duration), 1e-3)
        z = cls.zeta_for_bounce(bounce)
        return cls((2 * math.pi / d) ** 2, 4 * math.pi * z / d, 1.0, velocity)

    @classmethod
    def settling(cls, duration: float, bounce: float = 0.0, velocity: float = 0.0,
                 tol: float = 1e-3) -> "Spring":
        """Spring with the given bounce whose settle time (``tol``) is exactly ``duration`` s.

        Settle time scales as 1/omega0 for a fixed damping ratio, so omega0 is solved by a
        short fixed-point iteration (exact for velocity 0, converges for moderate velocities)."""
        d = max(float(duration), 1e-3)
        z = cls.zeta_for_bounce(bounce)
        w0 = 2 * math.pi / d
        for _ in range(40):
            sp = cls(w0 * w0, 2 * z * w0, 1.0, velocity)
            ts = sp.settle_time(tol)
            if abs(ts - d) < 1e-6 * d:
                break
            w0 *= ts / d
        return cls(w0 * w0, 2 * z * w0, 1.0, velocity)


@lru_cache(maxsize=512)
def _settle_time(k: float, c: float, m: float, v0: float, tol: float) -> float:
    sp = Spring(k, c, m, v0)
    w0 = sp.omega0
    z = sp.zeta
    # slowest decay rate -> generous scan horizon
    lam = z * w0 if z < 1 else w0 * (z - math.sqrt(max(z * z - 1, 0.0))) if z > 1 else w0 * 0.5
    lam = max(lam, 1e-6)
    amp = 1.0 + abs(v0) / max(w0, 1e-9)
    horizon = (math.log(amp / tol) + 4.0) / lam + 1.0 / w0
    n = 4000
    dt = horizon / n
    last = 0.0
    for i in range(n + 1):
        t = i * dt
        x, dx = sp._state(t)
        if abs(x - 1) > tol or abs(dx) > tol * w0:
            last = t
    lo, hi = last, last + dt
    for _ in range(40):
        mid = (lo + hi) / 2
        x, dx = sp._state(mid)
        if abs(x - 1) > tol or abs(dx) > tol * w0:
            lo = mid
        else:
            hi = mid
    return hi


# ---------------------------------------------------------------------------- Ease wrapper + registry
class Ease:
    """A named easing u -> value with an optional natural duration in seconds (springs)."""
    __slots__ = ("fn", "name", "duration")

    def __init__(self, fn: Callable[[float], float], name: str = "", duration: float | None = None):
        self.fn, self.name, self.duration = fn, name, duration

    def __call__(self, u: float) -> float:
        return self.fn(u)

    def __repr__(self) -> str:
        return f"Ease({self.name!r}" + (f", duration={self.duration:.3f})" if self.duration else ")")


def smoothstep(e0: float, e1: float, x: float) -> float:
    t = clamp((x - e0) / (e1 - e0) if e1 != e0 else 1.0)
    return t * t * (3 - 2 * t)


def window(t: float, a: float, b: float, fade_in: float, fade_out: float) -> float:
    """1 inside [a,b] with linear-ish smooth fades at both ends."""
    if t < a or t > b:
        return 0.0
    i = smoothstep(a, a + fade_in, t) if fade_in > 0 else 1.0
    o = 1 - smoothstep(b - fade_out, b, t) if fade_out > 0 else 1.0
    return min(i, o)


EASINGS = {
    "linear": linear, "in_quad": in_quad, "out_quad": out_quad, "in_out_quad": in_out_quad,
    "in_cubic": in_cubic, "out_cubic": out_cubic, "in_out_cubic": in_out_cubic,
    "out_quart": out_quart, "in_expo": in_expo, "out_expo": out_expo, "in_out_expo": in_out_expo,
    "out_back": out_back, "out_elastic": out_elastic,
    # v0.2 additions
    "in_quart": in_quart, "in_out_quart": in_out_quart,
    "in_quint": in_quint, "out_quint": out_quint, "in_out_quint": in_out_quint,
    "in_sine": in_sine, "out_sine": out_sine, "in_out_sine": in_out_sine,
    "in_circ": in_circ, "out_circ": out_circ,
}

# House roles: scenes should ask for a role, not a curve.
ROLES: dict[str, str] = {
    "enter": "out_expo", "enter_soft": "out_quint",
    "exit": "in_quart", "exit_fast": "in_expo",
    "move": "in_out_quint",
    "pop": "back(0.04)",
    "land": "spring(0.5,0.15)",
    "fx": "out_quint",  # opacity / colour / blur: never overshoot
}

# Design-system bezier tokens (published values).
TOKENS: dict[str, tuple[float, float, float, float]] = {
    "m3.emphasized": (0.2, 0.0, 0.0, 1.0),
    "m3.emphasized_decelerate": (0.05, 0.7, 0.1, 1.0),
    "m3.emphasized_accelerate": (0.3, 0.0, 0.8, 0.15),
    "m3.standard": (0.2, 0.0, 0.0, 1.0),
    "m3.standard_decelerate": (0.0, 0.0, 0.0, 1.0),
    "m3.standard_accelerate": (0.3, 0.0, 1.0, 1.0),
    "carbon.productive": (0.2, 0.0, 0.38, 0.9),
    "carbon.productive_entrance": (0.0, 0.0, 0.38, 0.9),
    "carbon.productive_exit": (0.2, 0.0, 1.0, 0.9),
    "carbon.expressive": (0.4, 0.14, 0.3, 1.0),
    "carbon.expressive_entrance": (0.0, 0.0, 0.3, 1.0),
    "carbon.expressive_exit": (0.4, 0.14, 1.0, 1.0),
    "css.ease": (0.25, 0.1, 0.25, 1.0),
    "css.ease_in": (0.42, 0.0, 1.0, 1.0),
    "css.ease_out": (0.0, 0.0, 0.58, 1.0),
    "css.ease_in_out": (0.42, 0.0, 0.58, 1.0),
}

_CALL = re.compile(r"^([a-z_][a-z0-9_.-]*)\s*\((.*)\)$")
_SPRING_KEYS = {"k": "stiffness", "stiffness": "stiffness", "c": "damping", "damping": "damping",
                "m": "mass", "mass": "mass", "v": "velocity", "v0": "velocity", "velocity": "velocity",
                "d": "duration", "dur": "duration", "duration": "duration", "b": "bounce", "bounce": "bounce"}


def _norm(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name.strip())  # outExpo -> out_Expo
    s = s.lower().replace("-", "_").replace(" ", "")
    s = s[5:] if s.startswith("ease_") else s
    parts = s.split("_")
    if len(parts) == 2 and parts[1] in ("in", "out", "inout"):   # expo_out -> out_expo
        s = f"{parts[1]}_{parts[0]}"
    elif len(parts) == 3 and parts[1:] == ["in", "out"]:         # expo_in_out -> in_out_expo
        s = f"in_out_{parts[0]}"
    return s.replace("inout_", "in_out_")


def _args(body: str) -> tuple[list[float], dict[str, float]]:
    pos: list[float] = []
    kw: dict[str, float] = {}
    for part in [p.strip() for p in body.split(",") if p.strip()]:
        if "=" in part:
            k, v = part.split("=", 1)
            kw[k.strip().lower()] = float(v)
        else:
            pos.append(float(part))
    return pos, kw


@lru_cache(maxsize=256)
def _resolve_str(spec: str) -> Ease:
    raw = spec.strip()
    low = raw.lower().replace(" ", "")
    if low in ROLES:
        e = _resolve_str(ROLES[low])
        return Ease(e.fn, low, e.duration)
    if low in TOKENS:
        e = cubic_bezier(*TOKENS[low])
        return Ease(e.fn, low)
    m = _CALL.match(low)
    if m:
        fn, body = m.group(1).replace("-", "_"), m.group(2)
        pos, kw = _args(body)
        if fn in ("cubic_bezier", "bezier"):
            if len(pos) != 4:
                raise ValueError(f"cubic-bezier needs 4 numbers: {raw!r}")
            return cubic_bezier(*pos)
        if fn == "back":
            return back(pos[0] if pos else kw.get("overshoot", MAX_OVERSHOOT))
        if fn == "steps":
            return steps(int(pos[0]) if pos else int(kw.get("n", 4)))
        if fn == "spring":
            opts = {_SPRING_KEYS[k]: v for k, v in kw.items() if k in _SPRING_KEYS}
            if any(k not in _SPRING_KEYS for k in kw):
                raise ValueError(f"unknown spring option in {raw!r}; use duration/bounce/velocity or k/c/m/v")
            names = ["duration", "bounce", "velocity"]
            for i, v in enumerate(pos[:3]):
                opts.setdefault(names[i], v)
            if "stiffness" in opts or "damping" in opts:
                sp = Spring(opts.get("stiffness", 100.0), opts.get("damping", 10.0), opts.get("mass", 1.0),
                            opts.get("velocity", 0.0))
            else:
                sp = Spring.settling(opts.get("duration", 0.5), opts.get("bounce", 0.0), opts.get("velocity", 0.0))
            e = sp.ease()
            return Ease(e.fn, raw, e.duration)
        raise ValueError(f"unknown easing function {fn!r} in {raw!r}")
    name = _norm(raw)
    if name in EASINGS:
        return Ease(EASINGS[name], name)
    if name in ROLES:
        return _resolve_str(name)
    raise ValueError(f"unknown easing {raw!r}; names: {sorted(EASINGS)}, roles: {sorted(ROLES)}, "
                     "or cubic-bezier(a,b,c,d) / spring(dur,bounce) / back(overshoot) / steps(n)")


def resolve(spec: "str | Callable[[float], float] | Ease | Spring | dict | None",
            default: str = "out_expo") -> Ease:
    """Turn a string / callable / Spring / dict into an :class:`Ease` (string results are cached).

    dict form: ``{"spring": {"duration": 0.6, "bounce": 0.2}}``, ``{"bezier": [0.2, 0.8, 0.2, 1]}``,
    ``{"back": 0.04}`` or ``{"ease": "out_quint"}``."""
    if spec is None:
        return _resolve_str(default)
    if isinstance(spec, Ease):
        return spec
    if isinstance(spec, Spring):
        return spec.ease()
    if isinstance(spec, str):
        return _resolve_str(spec)
    if isinstance(spec, dict):
        if "spring" in spec:
            o = spec["spring"] if isinstance(spec["spring"], dict) else {}
            o = {_SPRING_KEYS.get(k.lower(), k): float(v) for k, v in o.items()}
            if "stiffness" in o or "damping" in o:
                return Spring(o.get("stiffness", 100.0), o.get("damping", 10.0), o.get("mass", 1.0),
                              o.get("velocity", 0.0)).ease()
            sp = Spring.settling(o.get("duration", 0.5), o.get("bounce", 0.0), o.get("velocity", 0.0))
            return sp.ease()
        if "bezier" in spec or "cubic_bezier" in spec:
            return cubic_bezier(*spec.get("bezier", spec.get("cubic_bezier")))
        if "back" in spec:
            return back(float(spec["back"]))
        if "ease" in spec:
            return resolve(spec["ease"], default)
        raise ValueError(f"cannot resolve easing from {spec!r}")
    if callable(spec):
        return Ease(spec, getattr(spec, "__name__", "custom"))
    raise TypeError(f"cannot resolve easing from {spec!r}")


def animate(t: float, start: float, ease: "str | Callable[[float], float] | Ease | None" = None,
            dur: float | None = None, a: float = 0.0, b: float = 1.0) -> float:
    """Value of a tween from ``a`` to ``b`` starting at ``start``. ``dur`` defaults to the ease's
    natural duration (springs) or 0.5 s."""
    e = resolve(ease)
    d = dur if dur is not None else (e.duration or 0.5)
    return a + (b - a) * e(prog(t, start, d))


# ---------------------------------------------------------------------------- stagger
def _order_metric(i: int, n: int, order: "str | int | float") -> float:
    mid = (n - 1) / 2
    if isinstance(order, (int, float)) and not isinstance(order, bool):
        return abs(i - float(order))
    o = str(order).lower()
    if o in ("first", "start", "left", "top"):
        return float(i)
    if o in ("last", "end", "right", "bottom"):
        return float(n - 1 - i)
    if o in ("centre", "center", "middle"):
        return abs(i - mid)
    if o in ("edges", "edge", "outside"):
        return mid - abs(i - mid)
    raise ValueError(f"unknown stagger order {order!r}")


def _random_ranks(n: int, seed: int) -> list[float]:
    keys = sorted(range(n), key=lambda k: _hash(k, seed + 991))
    ranks = [0.0] * n
    for r, k in enumerate(keys):
        ranks[k] = float(r)
    return ranks


def _distribute(metric: Sequence[float], each: float, start: float, max_total: float | None,
                ease: "str | Callable[[float], float] | None") -> list[float]:
    mmax = max(metric) if metric else 0.0
    total = each * mmax
    if max_total is not None:
        total = min(total, max(0.0, float(max_total)))
    if mmax <= 0:
        return [start] * len(metric)
    f = resolve(ease) if ease is not None else None
    out = []
    for m in metric:
        u = m / mmax
        out.append(start + total * (f(u) if f else u))
    return out


def stagger(n: int, each: float = 0.05, *, start: float = 0.0, order: "str | int | float" = "first",
            max_total: float | None = None, ease: "str | Callable[[float], float] | None" = None,
            seed: int = 0) -> list[float]:
    """Per-item start delays (seconds) for n siblings.

    order: "first" | "last" | "centre" | "edges" | "random" | an index (ripple from that item).
    each: delay between neighbours (30-80 ms reads as a cascade). max_total caps the whole spread
    (keeps long strings from crawling). ease: distribute the delays along an easing of the
    normalised rank, e.g. "in_quad" bunches the tail. Delays start at ``start``."""
    n = int(n)
    if n <= 0:
        return []
    if str(order).lower() == "random":
        metric = _random_ranks(n, seed)
    else:
        metric = [_order_metric(i, n, order) for i in range(n)]
    return _distribute(metric, each, start, max_total, ease)


def stagger_grid(cols: int, rows: int, each: float = 0.05, *, origin: "str | tuple[float, float]" = "centre",
                 axis: str | None = None, start: float = 0.0, max_total: float | None = None,
                 ease: "str | Callable[[float], float] | None" = None) -> list[float]:
    """Delays for a cols x rows grid (row-major list). Delay grows with the distance of each cell
    from ``origin`` ("centre", "first" = top-left, "last" = bottom-right, or (col, row));
    ``axis`` = "x" or "y" measures only that axis (column or row sweeps)."""
    cols, rows = int(cols), int(rows)
    if isinstance(origin, str):
        o = origin.lower()
        ox, oy = {"centre": ((cols - 1) / 2, (rows - 1) / 2), "center": ((cols - 1) / 2, (rows - 1) / 2),
                  "first": (0.0, 0.0), "last": (cols - 1.0, rows - 1.0)}[o]
    else:
        ox, oy = float(origin[0]), float(origin[1])
    metric = []
    for r in range(rows):
        for c in range(cols):
            dx, dy = c - ox, r - oy
            metric.append(abs(dx) if axis == "x" else abs(dy) if axis == "y" else math.hypot(dx, dy))
    return _distribute(metric, each, start, max_total, ease)


def stagger_progress(t: float, i: int, delays: Sequence[float], dur: float,
                     ease: "str | Callable[[float], float] | None" = None) -> float:
    """Eased 0..1 progress of item i at time t, given ``delays`` (from stagger()) and a per-item dur."""
    return resolve(ease)(prog(t, delays[i], dur))


def _hash(i: int, seed: int) -> float:
    n = (i * 374761393 + seed * 668265263) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFFFF) / 0xFFFFFF * 2 - 1


def noise1(x: float, seed: int = 0) -> float:
    """Smooth deterministic value noise in [-1, 1]."""
    i = math.floor(x)
    f = x - i
    u = f * f * (3 - 2 * f)
    return lerp(_hash(i, seed), _hash(i + 1, seed), u)


def fbm(x: float, seed: int = 0, octaves: int = 3) -> float:
    v, a, fr, norm = 0.0, 1.0, 1.0, 0.0
    for o in range(octaves):
        v += a * noise1(x * fr, seed + o * 17)
        norm += a
        a *= 0.5
        fr *= 2.0
    return v / norm


def wiggle(t: float, freq: float = 2.0, amp: float = 1.0, octaves: int = 1, amp_mult: float = 0.5,
           seed: int = 0) -> float:
    """After Effects style wiggle(freq, amp, octaves, amp_mult): smooth deterministic jitter in [-amp, amp]."""
    v, a, norm = 0.0, 1.0, 0.0
    for o in range(max(1, int(octaves))):
        v += a * noise1(t * freq * (2 ** o), seed + o * 31)
        norm += a
        a *= amp_mult
    return amp * v / norm
