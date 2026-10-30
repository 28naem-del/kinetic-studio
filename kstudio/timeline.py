"""Timeline + marker system.

A Timeline owns the beat grid (from a BPM or from an analysed music file) and a
list of typed markers. Every time value in a spec is a *time expression*:

    3.5        seconds
    "3.5s"     seconds
    "12b" / "b12"   beat 12 (0-based beat index on the grid, fractional allowed)
    "bar3"     start of bar 3 (1-based, like a DAW)
    "drop"     a named marker (section, cue, scene id ...)
    "drop+2b", "bar5-0.5b", "intro+1.25s", "end-1b"   compound offsets

Durations accept the same units, but without names ("2b", "1.5s", "1bar", 0.8).
Fractions are allowed in beat terms ("1/2b", "3/4bar"), and "beat"/"beats" spell "b".

Grid helpers: `snap(t, div)` puts a time on the grid (div 1 = beats, 2 = 8ths, 4 = 16ths,
0.25 = bars of 4), `grid_error(t, div)` measures how far a time is off it, `beats_of(dur, at)`
converts a duration back to beats and `is_beat_expr(x)` tells a beat-unit value from seconds.

Marker kinds:
    beat     - generated from the grid (every beat; `strength` 1.0 on downbeats)
    section  - named arrangement region with an `energy` 0..3 (drives music + visuals)
    cue      - named point (logo hit, music stop, lower-third, anything a scene asks for)
    caption  - subtitle text with a duration (burned-in caption track)
    sfx      - a sound to place (`sound`, `gain`)
    hit      - a visual accent (camera shake / flash / chroma) with `strength`
"""
from __future__ import annotations

import bisect
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

MARKER_KINDS = ("beat", "section", "cue", "caption", "sfx", "hit")

_TERM = re.compile(r"([+-])?\s*([^+-]+)")
_NUM = re.compile(r"^\d+(?:\.\d+)?$|^\.\d+$|^\d+(?:\.\d+)?/\d+(?:\.\d+)?$")
_BEATY = re.compile(r"(?:\d|\.)(?:b|bars?|beats?)(?:$|[+-])|(?:^|[+-])(?:b|bar)\d", re.I)


def _num(s: str) -> float:
    if "/" in s:
        a, b = s.split("/", 1)
        den = float(b)
        if den == 0:
            raise TimeExprError(f"division by zero in {s!r}")
        return float(a) / den
    return float(s)


def is_beat_expr(expr: Any) -> bool:
    """True when `expr` is a string written in beat/bar units ("2b", "1bar", "bar3", "drop-1b")."""
    return isinstance(expr, str) and bool(_BEATY.search(expr.replace(" ", "")))


class TimeExprError(ValueError):
    pass


@dataclass
class Marker:
    kind: str
    t: float
    name: str = ""
    dur: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def end(self) -> float:
        return self.t + self.dur


class Timeline:
    def __init__(self, bpm: float = 120.0, offset: float = 0.0, beats_per_bar: int = 4,
                 beat_times: list[float] | None = None, duration: float = 0.0):
        if bpm <= 0:
            raise ValueError("bpm must be > 0")
        self.bpm = float(bpm)
        self.offset = float(offset)
        self.beats_per_bar = int(beats_per_bar)
        self.beat_times = list(beat_times) if beat_times else None
        self.duration = float(duration)
        self.markers: list[Marker] = []
        self._named: dict[str, Marker] = {}

    # ---------------------------------------------------------------- grid
    @property
    def spb(self) -> float:
        """Seconds per beat."""
        return 60.0 / self.bpm

    def beat_to_time(self, b: float) -> float:
        bt = self.beat_times
        if not bt or len(bt) < 2:
            return self.offset + b * self.spb
        if b <= 0:
            return bt[0] + b * (bt[1] - bt[0])
        i = int(math.floor(b))
        if i >= len(bt) - 1:
            last = (bt[-1] - bt[-2])
            return bt[-1] + (b - (len(bt) - 1)) * last
        return bt[i] + (b - i) * (bt[i + 1] - bt[i])

    def time_to_beat(self, t: float) -> float:
        bt = self.beat_times
        if not bt or len(bt) < 2:
            return (t - self.offset) / self.spb
        if t <= bt[0]:
            return (t - bt[0]) / (bt[1] - bt[0])
        i = bisect.bisect_right(bt, t) - 1
        if i >= len(bt) - 1:
            return len(bt) - 1 + (t - bt[-1]) / (bt[-1] - bt[-2])
        return i + (t - bt[i]) / (bt[i + 1] - bt[i])

    def beat_phase(self, t: float) -> tuple[int, float]:
        b = self.time_to_beat(t)
        i = math.floor(b)
        return i, b - i

    def pulse(self, t: float, decay: float = 8.0, every: float = 1.0) -> float:
        """1.0 exactly on a beat, decaying exponentially until the next one."""
        b = self.time_to_beat(t) / every
        if b < 0:
            return 0.0
        frac = (b - math.floor(b)) * every * self.spb
        return math.exp(-frac * decay)

    def beats_in(self, a: float, b: float) -> list[float]:
        """Times of grid beats in [a, b)."""
        i0 = math.ceil(self.time_to_beat(a) - 1e-9)
        out = []
        i = i0
        while True:
            t = self.beat_to_time(i)
            if t >= b:
                break
            out.append(t)
            i += 1
        return out

    # ---------------------------------------------------------------- snapping
    def snap(self, t: float, div: float = 1.0, mode: str = "nearest") -> float:
        """Snap time `t` to the grid subdivided `div` times per beat.

        div 1 = beats, 2 = 8ths, 4 = 16ths, 1/beats_per_bar = bar lines. `mode` is
        "nearest", "floor" or "ceil". Works on tempo-mapped grids (beat_times) too."""
        if div <= 0:
            raise ValueError("div must be > 0")
        b = self.time_to_beat(t) * div
        r = {"nearest": round, "floor": math.floor, "ceil": math.ceil}[mode](b + (1e-9 if mode == "floor" else -1e-9 if mode == "ceil" else 0.0))
        return self.beat_to_time(r / div)

    def snap_bar(self, t: float, mode: str = "nearest") -> float:
        return self.snap(t, 1.0 / self.beats_per_bar, mode)

    def grid_error(self, t: float, div: float = 1.0) -> float:
        """Signed seconds from `t` to the nearest grid point (positive = late)."""
        return t - self.snap(t, div)

    def on_grid(self, t: float, div: float = 1.0, tol: float = 1e-3) -> bool:
        return abs(self.grid_error(t, div)) <= tol

    def is_downbeat(self, t: float, tol: float = 1e-3) -> bool:
        return self.on_grid(t, 1.0 / self.beats_per_bar, tol)

    def bar_of(self, t: float) -> int:
        """1-based bar number containing time t (DAW style, matches "barN" expressions)."""
        return int(math.floor(self.time_to_beat(t) / self.beats_per_bar + 1e-9)) + 1

    def beats_of(self, dur: float, at: float = 0.0) -> float:
        """Number of grid beats covered by `dur` seconds starting at `at`."""
        return self.time_to_beat(at + dur) - self.time_to_beat(at)

    def snap_dur(self, dur: float, at: float = 0.0, div: float = 1.0, min_units: float = 1.0) -> float:
        """Snap a duration starting at `at` so that its end lands on the grid (>= min_units grid steps)."""
        b0 = self.time_to_beat(at)
        n = max(round(self.beats_of(dur, at) * div), min_units)
        return self.beat_to_time(b0 + n / div) - at

    @staticmethod
    def is_beat_expr(expr: Any) -> bool:
        return is_beat_expr(expr)

    # ---------------------------------------------------------------- markers
    def add(self, kind: str, t: float, name: str = "", dur: float = 0.0, **data: Any) -> Marker:
        if kind not in MARKER_KINDS:
            raise ValueError(f"unknown marker kind {kind!r}; expected one of {MARKER_KINDS}")
        m = Marker(kind, float(t), name, float(dur), data)
        self.markers.append(m)
        self.markers.sort(key=lambda x: x.t)
        if name:
            self._named[name] = m
        return m

    def named(self, name: str) -> Marker | None:
        return self._named.get(name)

    def of(self, kind: str) -> list[Marker]:
        return [m for m in self.markers if m.kind == kind]

    def section_at(self, t: float) -> Marker | None:
        cur = None
        for m in self.markers:
            if m.kind == "section" and m.t <= t + 1e-9:
                cur = m
        return cur

    def energy_at(self, t: float, default: float = 2.0) -> float:
        s = self.section_at(t)
        return float(s.data.get("energy", default)) if s else default

    def hit_env(self, t: float, decay: float = 9.0) -> float:
        """Summed envelope of hit markers at time t (for shake / flash / chroma)."""
        v = 0.0
        for m in self.markers:
            if m.kind != "hit":
                continue
            dt = t - m.t
            if 0 <= dt < 1.2:
                v += float(m.data.get("strength", 1.0)) * math.exp(-dt * decay)
        return v

    def captions_at(self, t: float) -> list[Marker]:
        return [m for m in self.markers if m.kind == "caption" and m.t <= t < m.end]

    def generate_beats(self, until: float) -> None:
        """Add beat markers for the whole grid up to `until` seconds."""
        for _i, bt in enumerate(self.beats_in(0.0, until)):
            bi = round(self.time_to_beat(bt))
            self.markers.append(Marker("beat", bt, "", 0.0,
                                       {"index": bi, "downbeat": bi % self.beats_per_bar == 0,
                                        "strength": 1.0 if bi % self.beats_per_bar == 0 else 0.6}))
        self.markers.sort(key=lambda x: x.t)

    # ---------------------------------------------------------------- expressions
    def _unit(self, tok: str, allow_names: bool) -> float:
        tok = tok.strip()
        if not tok:
            raise TimeExprError("empty term")
        low = tok.lower()
        if _NUM.match(low):
            return _num(low)
        if low.endswith("ms") and _NUM.match(low[:-2]):
            return _num(low[:-2]) / 1000.0
        if low.endswith("s") and _NUM.match(low[:-1]):
            return _num(low[:-1])
        for suf, mult in (("bars", self.beats_per_bar), ("bar", self.beats_per_bar), ("beats", 1), ("beat", 1), ("b", 1)):
            if low.endswith(suf) and _NUM.match(low[:-len(suf)]):
                return ("beat", _num(low[:-len(suf)]) * mult)  # type: ignore[return-value]
        if low.startswith("bar") and _NUM.match(low[3:]):
            return ("beat", (_num(low[3:]) - 1) * self.beats_per_bar)  # type: ignore[return-value]
        if low.startswith("b") and _NUM.match(low[1:]):
            return ("beat", _num(low[1:]))  # type: ignore[return-value]
        if allow_names:
            if low == "end":
                return ("abs", self.duration)  # type: ignore[return-value]
            if low in ("start", "begin"):
                return ("abs", 0.0)  # type: ignore[return-value]
            m = self._named.get(tok)
            if m is None:
                raise TimeExprError(f"unknown marker {tok!r} (known: {sorted(self._named)})")
            return ("abs", m.t)  # type: ignore[return-value]
        raise TimeExprError(f"cannot parse time term {tok!r}")

    def resolve(self, expr: Any) -> float:
        """Resolve an absolute time expression to seconds."""
        if isinstance(expr, (int, float)):
            return float(expr)
        if not isinstance(expr, str):
            raise TimeExprError(f"bad time expression {expr!r}")
        src = expr.replace(" ", "")
        total, first = 0.0, 0
        # Marker names may contain '-' or '+' ("intro-end"): match the longest known name first.
        for name in sorted(self._named, key=len, reverse=True):
            if src.startswith(name) and (len(src) == len(name) or src[len(name)] in "+-"):
                total, src, first = self._named[name].t, src[len(name):], 1
                break
        terms = _TERM.findall(src)
        if not terms and not first:
            raise TimeExprError(f"bad time expression {expr!r}")
        for idx, (sign, tok) in enumerate(terms, start=first):
            sgn = -1.0 if sign == "-" else 1.0
            v = self._unit(tok, allow_names=(idx == 0))
            if isinstance(v, tuple):
                kind, val = v
                if kind == "abs":
                    total += sgn * val
                elif idx == 0:  # absolute beat position
                    total += self.beat_to_time(sgn * val)
                else:           # beat offset relative to running position
                    b0 = self.time_to_beat(total)
                    total = self.beat_to_time(b0 + sgn * val)
            else:
                total += sgn * v
        return total

    def resolve_dur(self, expr: Any, at: float = 0.0, snap: float | None = None) -> float:
        """Resolve a duration expression (beats are measured from `at`).

        With `snap` (a grid subdivision, e.g. 1 for beats, 4 for 16ths) seconds values are
        moved so the duration ends on that grid; beat expressions are already on it."""
        if snap and not is_beat_expr(expr):
            return self.snap_dur(self.resolve_dur(expr, at), at, snap, min_units=0.0)
        if isinstance(expr, (int, float)):
            return float(expr)
        if not isinstance(expr, str):
            raise TimeExprError(f"bad duration {expr!r}")
        total = 0.0
        for sign, tok in _TERM.findall(expr.replace(" ", "")):
            sgn = -1.0 if sign == "-" else 1.0
            v = self._unit(tok, allow_names=False)
            if isinstance(v, tuple):
                b0 = self.time_to_beat(at + total)
                total = self.beat_to_time(b0 + sgn * v[1]) - at
            else:
                total += sgn * v
        return total

    def resolve_times(self, exprs: Iterable[Any], at: float = 0.0, snap: float | None = None) -> list[float]:
        """Resolve a list of offsets measured from `at` (scene-local cue lists)."""
        return [self.resolve_dur(x, at, snap) for x in exprs]

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for m in self.markers:
            kinds[m.kind] = kinds.get(m.kind, 0) + 1
        return {"bpm": self.bpm, "offset": self.offset, "duration": round(self.duration, 3),
                "markers": kinds,
                "named": {k: round(v.t, 3) for k, v in sorted(self._named.items(), key=lambda kv: kv[1].t)}}


def iter_kind(markers: Iterable[Marker], kind: str) -> Iterable[Marker]:
    return (m for m in markers if m.kind == kind)
