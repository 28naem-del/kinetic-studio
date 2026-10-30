"""Procedural music beds. Five original styles, arranged live from the film's timeline.

The beat grid places every note; section markers (energy 0..3) add or remove layers; scene cuts
(the timeline's per-scene cue markers) get a drum fill and, where the energy rises, a riser that
both END EXACTLY ON THE CUT, then a crash/impact scaled by the new section's energy. Timeline
"hit" markers add chord stabs scaled by strength x energy. Cue markers can request music events
explicitly: {"kind": "cue", "at": "drop", "music": "riser" | "fill" | "hit" | "break" | "drop" | "none"}
(the event ends / lands at the marker time; a marker `dur` sets the riser / break length).
A cue marker named "music_stop" hard-stops the bed (e.g. under a logo sting).

Signal flow per style: drum machine (808/909-style kick, snare/clap, closed + open hats on 16ths
with accents, humanised timing) -> dry drum bus; filter-enveloped saturated bass; voice-led
chords on unison pads / FM keys; a 2-bar melodic hook on the style's scale; arps; per-voice
reverb and dotted-8th ping-pong delay sends; kick-keyed sidechain ducking; glue compression,
EQ (mud cut, air shelf) and soft saturation on the master. `plan()` exposes the arrangement.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..timeline import Timeline
from . import dsp as A
from . import instruments as I
from .dsp import SR

CHORDS = {"M": (0, 4, 7), "m": (0, 3, 7), "M7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "7": (0, 4, 7, 10),
          "6": (0, 4, 7, 9), "sus2": (0, 2, 7), "sus4": (0, 5, 7), "add9": (0, 4, 7, 14), "madd9": (0, 3, 7, 14),
          "M9": (0, 4, 7, 11, 14), "m9": (0, 3, 7, 10, 14), "9": (0, 4, 7, 10, 14), "7sus4": (0, 5, 7, 10),
          "m6": (0, 3, 7, 9)}

SCALES = {"major": (0, 2, 4, 5, 7, 9, 11), "minor": (0, 2, 3, 5, 7, 8, 10), "dorian": (0, 2, 3, 5, 7, 9, 10),
          "mixolydian": (0, 2, 4, 5, 7, 9, 10)}

# 16-step patterns per bar ("x" = hit). Keys: instrument -> {min_energy: pattern}
Pattern = dict[str, dict[int, str]]


@dataclass
class Style:
    name: str
    bpm: float
    key: int                        # semitones from A4 of the chord-register root
    prog: list[tuple[int, str]]     # 4-bar progression (root offset, chord quality)
    layers: dict[str, int]          # layer -> minimum energy
    sidechain: float = 0.0          # 0..1 depth scale of the kick-keyed ducking
    swing: float = 0.0              # off-beat delay as a fraction of a beat (16ths get half)
    lowpass: float = 16000.0
    gains: dict[str, float] = field(default_factory=dict)
    scale: str = "minor"
    prog8: list[tuple[int, str]] | None = None          # 8-bar progression with a bar 7-8 variation
    hook: list[tuple[float, float, int, float]] = field(default_factory=list)  # (beat, beats, degree, vel)
    hook_voice: str = "pluck"
    drums: Pattern = field(default_factory=dict)
    bass_pat: dict[int, str] = field(default_factory=dict)
    bass_voice: dict[str, Any] = field(default_factory=dict)
    kick: dict[str, float] = field(default_factory=dict)
    reverb: tuple[float, float] = (1.6, 0.02)          # RT60 s, pre-delay s
    sends: dict[str, float] = field(default_factory=dict)
    duck_db: dict[str, float] = field(default_factory=dict)
    air_db: float = 2.0
    pad_cutoff: float = 2400.0
    fill_voice: str = "snare"


_ACC = (1.0, 0.68, 0.84, 0.7)

STYLES: dict[str, Style] = {
    "drive": Style(
        "drive", 128, -12, [(0, "m9"), (-4, "M7"), (3, "add9"), (-2, "6")],
        {"pad": 0, "kick": 1, "hat": 1, "bass": 1, "clap": 2, "ohat": 2, "arp16": 2, "hook": 2, "rim": 3},
        sidechain=0.6, scale="minor",
        prog8=[(0, "m9"), (-4, "M7"), (3, "add9"), (-2, "6"), (0, "m9"), (-4, "M7"), (3, "add9"), (-5, "7sus4")],
        hook=[(0, .5, 4, 1.0), (.75, .5, 4, .8), (1.5, .5, 6, .9), (2.5, .5, 7, 1.0), (3, 1, 6, .8),
              (4.5, .5, 4, .9), (5, .5, 3, .8), (5.75, .75, 2, .9), (7, .75, 0, .8)],
        hook_voice="lead",
        drums={"kick": {1: "x...x...x...x..."}, "clap": {2: "....x.......x..."},
               "hat": {1: "xx.xxx.xxx.xxx.x"}, "ohat": {2: "..x...x...x...x."}, "rim": {3: "...x.....x....x."}},
        bass_pat={1: "..x...x...x...x.", 3: ".xxx.xxx.xxx.xxx"},
        bass_voice={"cutoff": 220, "env_oct": 3.2, "decay": 0.09, "q": 1.4, "drive": 2.4, "len": 0.22},
        kick={"f_start": 230, "tau_p": 0.03, "decay": 0.3, "click": 0.6},
        reverb=(1.5, 0.018), sends={"pad": 0.3, "keys": 0.2, "hook": 0.22, "drums": 0.05, "clap": 0.16},
        duck_db={"bass": 9, "pad": 6, "keys": 3, "hook": 2}, gains={"pad": 0.30, "arp16": 0.13, "bass": 0.42,
                                                                   "hook": 0.2, "kick": 0.85},
        air_db=2.5, pad_cutoff=2600),
    "calm": Style(
        "calm", 84, -7, [(0, "M9"), (-3, "m7"), (-7, "M7"), (-5, "6")],
        {"pad": 0, "rhodes": 0, "softkick": 1, "shaker": 1, "rim": 1, "sub": 1, "hook": 1, "hat": 2},
        sidechain=0.15, lowpass=14000, scale="major",
        prog8=[(0, "M9"), (-3, "m7"), (-7, "M7"), (-5, "6"), (0, "M9"), (-3, "m9"), (-7, "M7"), (-5, "7sus4")],
        hook=[(0, 1, 4, .8), (1, .5, 2, .6), (1.5, 1.5, 1, .7), (4, 1, 0, .7), (5, .5, 1, .6), (5.5, .5, 2, .6),
              (6, 1.5, 4, .8)],
        hook_voice="ks",
        drums={"softkick": {1: "x.......x.....x."}, "rim": {1: "....x.......x..."}, "shaker": {1: "xxxxxxxxxxxxxxxx"},
               "hat": {2: "..x...x...x...x."}},
        bass_pat={1: "x.......x......."},
        bass_voice={"cutoff": 160, "env_oct": 1.5, "decay": 0.25, "q": 0.8, "drive": 1.4, "len": 1.8, "wave": "tri",
                    "sub": 0.9},
        kick={"f_start": 150, "tau_p": 0.04, "decay": 0.26, "click": 0.25, "drive": 1.6},
        reverb=(2.2, 0.028), sends={"pad": 0.35, "keys": 0.3, "hook": 0.3, "drums": 0.1, "clap": 0.2},
        duck_db={"bass": 3, "pad": 2, "keys": 1}, gains={"pad": 0.26, "rhodes": 0.34, "bass": 0.36, "hook": 0.3,
                                                         "kick": 0.6, "lv_bass": 0.36, "lv_hats": 6.5, "lv_hook": 2.6, "lv_pad_haas": 6.0, "lv_hook_haas": 2.0,
                                                         "lv_kick": 0.5},
        air_db=3.0, pad_cutoff=2600, fill_voice="tom"),
    "bright": Style(
        "bright", 112, -9, [(0, "add9"), (7, "6"), (9, "m7"), (5, "M9")],
        {"pad": 0, "arp8": 1, "kick": 1, "bass": 1, "hat": 1, "clap": 2, "hook": 2, "ohat": 3, "bells": 3},
        sidechain=0.5, scale="major",
        prog8=[(0, "add9"), (7, "6"), (9, "m7"), (5, "M9"), (0, "add9"), (7, "6"), (2, "m7"), (7, "sus4")],
        hook=[(0, .5, 0, 1.0), (.5, .5, 2, .8), (1, .5, 4, .9), (1.5, .5, 7, .8), (2.5, .5, 6, .8), (3, 1, 4, .9),
              (4, .5, 5, .9), (4.5, .5, 4, .8), (5, .5, 2, .8), (6, 1.5, 4, .9)],
        hook_voice="bell",
        drums={"kick": {1: "x...x...x...x..."}, "clap": {2: "....x.......x..."},
               "hat": {1: "..x...x...x...x.", 2: "xxxxxxxxxxxxxxxx"}, "ohat": {3: "..x...x...x...x."}},
        bass_pat={1: "..x...x...x...x."},
        bass_voice={"cutoff": 300, "env_oct": 2.6, "decay": 0.08, "q": 1.1, "drive": 1.8, "len": 0.25, "octave_hop": True},
        kick={"f_start": 200, "tau_p": 0.03, "decay": 0.28, "click": 0.5},
        reverb=(1.7, 0.02), sends={"pad": 0.3, "keys": 0.25, "hook": 0.25, "drums": 0.06, "clap": 0.18},
        duck_db={"bass": 8, "pad": 5, "keys": 3, "hook": 2}, gains={"pad": 0.26, "arp8": 0.16, "bass": 0.4,
                                                                   "hook": 0.2, "bells": 0.1, "kick": 0.8},
        air_db=2.5, pad_cutoff=3000),
    "pulse": Style(
        "pulse", 120, -3, [(0, "m9"), (0, "m7"), (-4, "M7"), (-2, "6")],
        {"pad": 0, "seq16": 0, "kick": 1, "hat": 1, "bass": 1, "rim": 2, "blips": 2, "hook": 2, "ohat": 3, "clap": 3},
        sidechain=0.45, scale="minor",
        prog8=[(0, "m9"), (0, "m7"), (-4, "M7"), (-2, "6"), (0, "m9"), (0, "m7"), (-4, "M9"), (-5, "7sus4")],
        hook=[(0, .25, 0, 1.0), (.5, .25, 7, .8), (1, .25, 3, .9), (1.5, .25, 4, .8), (2, .5, 6, .9), (3, .5, 4, .8),
              (4, .25, 0, 1.0), (4.5, .25, 7, .8), (5, .25, 3, .9), (5.5, .5, 2, .8), (6.5, 1, 0, .8)],
        hook_voice="pulse_lead",
        drums={"kick": {1: "x...x...x...x..."}, "hat": {1: "xxxxxxxxxxxxxxxx"}, "rim": {2: "...x..x....x..x."},
               "ohat": {3: "..x...x...x...x."}, "clap": {3: "....x.......x..."}},
        bass_pat={1: "x.x.x.x.x.x.x.x."},
        bass_voice={"cutoff": 200, "env_oct": 2.2, "decay": 0.06, "q": 1.3, "drive": 2.0, "len": 0.32, "wave": "square",
                    "sub": 0.8},
        kick={"f_start": 190, "tau_p": 0.028, "decay": 0.26, "click": 0.55},
        reverb=(1.4, 0.015), sends={"pad": 0.28, "keys": 0.2, "hook": 0.25, "drums": 0.05, "clap": 0.15},
        duck_db={"bass": 7, "pad": 5, "keys": 3, "hook": 2}, gains={"pad": 0.22, "seq16": 0.13, "bass": 0.4,
                                                                   "hook": 0.18, "kick": 0.8, "lv_hats": 5.5,
                                                                   "lv_bass": 0.5, "lv_keys": 2.4},
        air_db=2.5, pad_cutoff=2200, fill_voice="rim"),
    "lofi": Style(
        "lofi", 86, -6, [(2, "m9"), (7, "9"), (0, "M9"), (-3, "m9")],
        {"rhodes": 0, "vinyl": 0, "bbkick": 1, "snare": 1, "hat": 1, "bass": 1, "hook": 1, "pad": 2},
        sidechain=0.15, swing=0.14, lowpass=11000, scale="major",
        prog8=[(2, "m9"), (7, "9"), (0, "M9"), (-3, "m9"), (2, "m9"), (7, "9"), (0, "M9"), (7, "7sus4")],
        hook=[(.5, .5, 4, .7), (1, .5, 2, .6), (1.5, 1, 1, .7), (3, .5, 0, .6), (4.5, .5, 4, .7), (5, .5, 5, .6),
              (5.5, 1, 4, .7), (7, 1, 2, .6)],
        hook_voice="ks",
        drums={"bbkick": {1: "x.........x..x.."}, "snare": {1: "....x.......x..."}, "hat": {1: "x.xxx.xxx.xxx.xx"}},
        bass_pat={1: "x.........x..x.."},
        bass_voice={"cutoff": 140, "env_oct": 1.6, "decay": 0.15, "q": 0.9, "drive": 1.8, "len": 1.3, "wave": "tri",
                    "sub": 0.8},
        kick={"f_start": 140, "tau_p": 0.04, "decay": 0.3, "click": 0.35, "drive": 2.0},
        reverb=(1.8, 0.03), sends={"pad": 0.3, "keys": 0.25, "hook": 0.3, "drums": 0.1, "clap": 0.2},
        duck_db={"bass": 3, "pad": 2, "keys": 1.5}, gains={"rhodes": 0.42, "pad": 0.16, "bass": 0.42, "hook": 0.3,
                                                           "kick": 0.75},
        air_db=1.5, pad_cutoff=1600, fill_voice="snare"),
}


# ---------------------------------------------------------------------- harmony

def _chord_pcs(st: Style, bar: int) -> tuple[int, list[int]]:
    prog = st.prog8 or (st.prog * 2)
    root, q = prog[bar % len(prog)]
    tones = list(CHORDS[q])
    if len(tones) > 4:
        tones = [x for x in tones if x != 7]          # drop the fifth from 5-note chords
    return root, [(st.key + root + iv) for iv in tones]


def voice_lead(prev: list[int] | None, tones: list[int], lo: int, hi: int) -> list[int]:
    """Choose one octave placement per chord tone in [lo, hi] (semitones from A4) minimising the
    total movement from the previous voicing (sorted voice to sorted voice), with a compact span."""
    opts = []
    for tn in tones:
        pc = tn % 12
        cands = [p for p in range(lo, hi + 1) if p % 12 == pc]
        opts.append(cands or [tn])
    centre = (lo + hi) / 2
    best, best_cost = None, float("inf")
    for combo in itertools.product(*opts):
        v = sorted(combo)
        span = v[-1] - v[0]
        if span > 16 or len(set(v)) < len(v):
            continue
        if prev is None or len(prev) != len(v):
            cost = abs(sum(v) / len(v) - centre) + 0.2 * span
        else:
            cost = sum(abs(a - b) for a, b in zip(v, sorted(prev))) + 0.05 * span
        if v[1] - v[0] < 2:
            cost += 2.0                                 # avoid seconds at the bottom of the voicing
        if cost < best_cost:
            best, best_cost = v, cost
    return best or sorted(tones)


def _bass_hz(st: Style, root: int) -> float:
    pc = (st.key + root) % 12
    f = A.note_hz(pc)
    while f > 80.0:
        f /= 2
    while f < 40.0:
        f *= 2
    return f


def _hook_notes(st: Style, pair: int, chord_pcs: list[set[int]]) -> list[tuple[float, float, int, float]]:
    """The 2-bar hook for bar pair `pair` (0..3 in the 8-bar phrase): A, A', A, B variations,
    nudging strong-beat notes onto chord tones. Returns (beat_in_pair, beats, semis_from_A4, vel)."""
    sc = SCALES[st.scale]
    out = []
    notes = list(st.hook)
    for i, (bt, ln, deg, vel) in enumerate(notes):
        d = deg
        if pair % 4 == 1 and i >= len(notes) - 2:
            d = deg + 1                                 # A': lift the tail
        elif pair % 4 == 3:
            d = deg + 2 if i < len(notes) - 1 else 0    # B: answer, resolving to the root
        octv, k = divmod(d, 7)
        semi = st.key + 12 + sc[k] + 12 * octv
        pcs = chord_pcs[0 if bt < 4 else min(1, len(chord_pcs) - 1)]
        if abs(bt - round(bt)) < 1e-6 and semi % 12 not in pcs:
            for delta in (1, -1, 2, -2):
                if (semi + delta) % 12 in pcs:
                    semi += delta
                    break
        out.append((bt, ln, semi, vel))
    return out


# ---------------------------------------------------------------------- arrangement plan

def scene_cuts(tl: Timeline, duration: float) -> list[float]:
    """Scene boundaries (the per-scene cue markers the Film adds), excluding t=0 and the end."""
    ts = sorted({round(m.t, 6) for m in tl.of("cue") if "scene" in m.data})
    return [t for t in ts if 0.05 < t < duration - 0.05]


def plan(style: str, tl: Timeline, duration: float) -> dict[str, Any]:
    """Arrangement events derived from the timeline: fills, risers, crashes/impacts, breaks and
    stabs, each with exact start/end times (seconds). Used by generate(); handy for tests/tools."""
    st = STYLES[style]
    spb = max(0.2, tl.beat_to_time(1.0) - tl.beat_to_time(0.0))
    cuts = scene_cuts(tl, duration)
    req: dict[float, list[tuple[str, float]]] = {}
    for m in tl.of("cue"):
        what = m.data.get("music")
        if what and 0.0 < m.t <= duration:
            req.setdefault(round(m.t, 6), []).append((str(what), float(m.dur or 0.0)))
    events: list[dict[str, Any]] = []
    starts = [0.0] + cuts
    points = sorted(set(cuts) | set(req))
    for c in points:
        rq = [w for w, _ in req.get(c, [])]
        if "none" in rq:
            continue
        rdur = max([d for w, d in req.get(c, []) if w in ("riser", "break", "drop")] or [0.0])
        prev_start = max([s for s in starts if s < c - 1e-6] or [0.0])
        seg = c - prev_start
        e0 = tl.energy_at(max(0.0, c - 1e-3))
        e1 = tl.energy_at(c + 1e-4)
        is_cut = c in cuts
        if is_cut or "fill" in rq or "drop" in rq:
            flen = min(spb, seg * 0.5) if seg >= 1.5 * spb else min(spb * 0.5, seg * 0.5)
            events.append({"kind": "fill", "start": c - flen, "end": c, "energy": e1,
                           "vel": min(1.0, 0.55 + 0.15 * e1), "voice": st.fill_voice})
        rise = e1 > e0 or "riser" in rq or "drop" in rq or (is_cut and e1 >= 3 and seg >= 4 * spb)
        if rise:
            rl = rdur or min(spb * tl.beats_per_bar, max(spb, seg * 0.9))
            events.append({"kind": "riser", "start": max(0.0, c - rl), "end": c, "energy": e1,
                           "vel": min(1.0, 0.5 + 0.17 * e1)})
        if ("break" in rq or "drop" in rq or (is_cut and e1 >= 3 > e0)) and c > spb:
            bl = rdur if ("break" in rq and rdur) else spb
            events.append({"kind": "break", "start": c - bl, "end": c, "energy": e1})
        if is_cut or "hit" in rq or "drop" in rq:
            events.append({"kind": "crash" if e1 >= 1 else "swell", "start": c, "end": c, "energy": e1,
                           "vel": min(1.0, 0.3 + 0.22 * e1 + (0.15 if e1 > e0 else 0.0))})
    for m in tl.of("hit"):
        if m.t > duration or any(abs(m.t - c) < 0.04 for c in points):
            continue
        s = float(m.data.get("strength", 0.5))
        if s >= 0.3:
            e = tl.energy_at(m.t + 1e-4)
            events.append({"kind": "stab", "start": m.t, "end": m.t, "energy": e,
                           "vel": min(1.0, s * (0.5 + 0.17 * e))})
    events.sort(key=lambda ev: (ev["start"], ev["kind"]))
    return {"style": style, "bpm": tl.bpm, "spb": spb, "cuts": cuts, "events": events}


# ---------------------------------------------------------------------- render

class _Bus:
    def __init__(self, n: int):
        self.n = n
        self.stems: dict[str, np.ndarray] = {}

    def add(self, stem: str, clip: np.ndarray, t: float, gain: float = 1.0, pan: float | None = None) -> None:
        buf = self.stems.get(stem)
        if buf is None:
            buf = self.stems[stem] = np.zeros((self.n, 2), np.float32)
        x = clip if (clip.ndim == 2 and pan is None) else A.pan(clip, 0.0 if pan is None else pan)
        if clip.ndim == 1 and pan is None:
            x = A.stereo(clip)
        A.add_at(buf, x, int(round(t * SR)), gain)

    def get(self, stem: str) -> np.ndarray:
        return self.stems.get(stem, np.zeros((self.n, 2), np.float32))


def _sum(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a, b = A.stereo(a), A.stereo(b)
    out = np.zeros((max(len(a), len(b)), 2), np.float32)
    out[: len(a)] += a
    out[: len(b)] += b
    return out


# per-stem output levels (linear), overridable per style with gains["lv_<stem>"]
LEVELS: dict[str, float] = {"kick": 0.36, "clap": 1.5, "hats": 3.0, "perc": 1.5, "fill": 1.0, "bass": 0.65, "pad": 3.2,
                            "keys": 1.8, "hook": 1.6, "riser": 1.0, "crash": 1.2, "pad_haas": 13.0, "wet": 1.0,
                            "hook_haas": 7.0, "keys_haas": 9.0}

DEBUG_STEMS: dict[str, np.ndarray] | None = None     # set to {} to capture the stems of the next generate()


def _duck(n: int, kicks: list[float], spb: float, depth_db: float) -> np.ndarray:
    if depth_db <= 0 or not kicks:
        return np.ones(n, np.float32)
    pre = int(0.002 * SR)
    rel = max(int(0.7 * spb * SR), 64)
    u = np.arange(rel, dtype=np.float32) / rel
    ker = np.concatenate([np.linspace(0, 1, pre * 2, endpoint=False, dtype=np.float32), (1 - u) ** 2])
    env = np.zeros(n, np.float32)
    for k in sorted({int(t * SR) - pre for t in kicks}):   # sparse convolution of kick impulses with ker
        if 0 <= k < n:
            m = min(len(ker), n - k)
            env[k:k + m] += ker[:m]
    env = np.clip(env, 0, 1)
    depth = 1 - 10 ** (-depth_db / 20)
    return (1 - depth * env).astype(np.float32)


def _glue(x: np.ndarray, ratio: float = 2.0, win: float = 0.03, over_db: float = 3.0) -> np.ndarray:
    """Bus glue: RMS detector (30 ms), threshold `over_db` below the loud passages, smoothed gain."""
    import cv2
    m = (x ** 2).mean(1)
    k = max(3, int(win * SR))
    env = np.sqrt(cv2.blur(m.reshape(1, -1).astype(np.float32), (k, 1)).ravel() + 1e-12)
    ref = np.percentile(env[env > 1e-6], 90) if (env > 1e-6).any() else 1.0
    thr = ref * 10 ** (-over_db / 20)
    g = np.where(env > thr, (env / thr) ** (1 / ratio - 1), 1.0).astype(np.float32)
    g = cv2.blur(g.reshape(1, -1), (int(0.08 * SR), 1)).ravel()
    return (x * g[:, None]).astype(np.float32)


def generate(style: str, tl: Timeline, duration: float, seed: int = 0, stop_at: float | None = None) -> np.ndarray:
    st = STYLES[style]
    n = int(math.ceil(duration * SR))
    tail = int(3 * SR)
    N = n + tail
    bus = _Bus(N)
    rng = A.rng(seed + 97)
    g = st.gains
    bpb = tl.beats_per_bar
    spb = max(0.2, tl.beat_to_time(1.0) - tl.beat_to_time(0.0))
    arr = plan(style, tl, duration)
    events = arr["events"]
    breaks = [(ev["start"], ev["end"]) for ev in events if ev["kind"] == "break"]

    def in_break(t: float) -> bool:
        return any(a - 1e-4 <= t < b - 1e-4 for a, b in breaks)

    def on(layer: str, e: float) -> bool:
        return layer in st.layers and e >= st.layers[layer]

    def pat(name: str, e: float) -> str | None:
        p = st.drums.get(name)
        if not p or not on(name, e):
            return None
        keys = [k for k in p if k <= e]
        return p[max(keys)] if keys else None

    def step_time(b: int, s: int, swing: bool = True) -> float:
        t = tl.beat_to_time(b + s / 4)
        if swing and st.swing > 0:
            if s % 4 == 2:
                t += st.swing * spb
            elif s % 2 == 1:
                t += st.swing * spb * 0.5
        return t

    # one-shot banks (velocity applied as gain; round-robin variants against machine-gunning)
    kp = {"f_end": _bass_hz(st, 0) if _bass_hz(st, 0) < 62 else _bass_hz(st, 0) / 2 * 1.0, **st.kick}
    kp["f_end"] = min(62.0, max(42.0, kp["f_end"]))
    kick_c = I.kick(**kp)
    soft_kick = I.kick(f_start=kp.get("f_start", 150), f_end=kp["f_end"], tau_p=0.04, decay=0.22, click=0.2, drive=1.4)
    bb_kick = A.fft_filter(kick_c, 0, 5000)
    snares = [I.snare(seed=5 + i) for i in range(3)]
    if style == "lofi":
        snares = [A.fft_filter(s, 120, 9000) for s in snares]
    claps = [I.clap(seed=3 + i) for i in range(2)]
    hats_c = [I.hat(False, seed=7 + i) for i in range(4)]
    hats_o = [I.hat(True, seed=17 + i) for i in range(2)]
    shk = [I.shaker(seed=9 + i) for i in range(3)]
    rims = [I.rim(seed=11 + i) for i in range(2)]
    crash_c = I.crash()
    kicks: list[float] = []

    first_b = max(0, int(math.floor(tl.time_to_beat(0.0))))
    total_beats = int(math.ceil(tl.time_to_beat(duration))) + 1
    prev_voicing: list[int] | None = None
    chord_cache: dict[int, tuple[int, list[int], list[int]]] = {}

    def chord(bar: int) -> tuple[int, list[int], list[int]]:
        nonlocal prev_voicing
        if bar not in chord_cache:
            root, tones = _chord_pcs(st, bar)
            v = voice_lead(prev_voicing, tones, st.key - 5, st.key + 12)
            prev_voicing = v
            chord_cache[bar] = (root, tones, v)
        return chord_cache[bar]

    hum = 0.007 if style == "lofi" else 0.004
    for b in range(first_b, total_beats + 1):
        tb = tl.beat_to_time(b)
        if tb > duration + 0.05:
            break
        bar, beat = divmod(b, bpb)
        e = tl.energy_at(tb + 1e-4)
        ef = 0.75 + 0.1 * e
        root, tones, voicing = chord(bar)
        nxt_root = chord(bar + 1)[0]
        # ---------------------------------------------------------------- drums
        for s in range(4):
            step = beat * 4 + s
            ts = step_time(b, s)
            brk = in_break(ts)
            acc = _ACC[step % 4]
            for name, bank, gain, human in (("kick", None, g.get("kick", 0.85), 0.0), ("softkick", None, g.get("kick", 0.6), 0.0),
                                            ("bbkick", None, g.get("kick", 0.75), 0.0)):
                p = pat(name, e)
                if p and p[step % 16] == "x" and not brk:
                    clip = kick_c if name == "kick" else (soft_kick if name == "softkick" else bb_kick)
                    tk = tl.beat_to_time(b + s / 4) if name != "bbkick" else ts
                    bus.add("kick", clip, tk, gain * (1.0 if s == 0 else 0.8))
                    kicks.append(tk)
            p = pat("clap", e)
            if p and p[step % 16] == "x" and not brk:
                bus.add("clap", claps[(b // 2) % 2], ts, 0.42 * ef)
            p = pat("snare", e)
            if p and p[step % 16] == "x" and not brk:
                lag = 0.006 + float(rng.uniform(-0.004, 0.004)) if style == "lofi" else 0.0
                bus.add("clap", snares[(b // 2) % 3], ts + lag, 0.5 * ef)
            p = pat("hat", e)
            ohp = pat("ohat", e)
            if p and p[step % 16] == "x" and not (ohp and ohp[step % 16] == "x"):
                v = acc * ef * float(rng.uniform(0.88, 1.08))
                bus.add("hats", hats_c[int(rng.integers(0, 4))], ts + float(rng.uniform(-hum, hum)),
                        0.22 * v, pan=(0.55 if step % 2 else -0.35) + float(rng.uniform(-0.08, 0.08)))
            if ohp and ohp[step % 16] == "x":
                # choke: open hat stops at the next closed-hat step
                clip = hats_o[step // 4 % 2]
                nxt = step_time(b, s + 2) - ts if s + 2 < 4 else tl.beat_to_time(b + (s + 2) / 4) - ts
                k = int(max(0.03, nxt) * SR)
                c2 = clip[:k].copy()
                f = min(len(c2), int(0.006 * SR))
                c2[-f:] *= np.linspace(1, 0, f)
                bus.add("hats", c2, ts, 0.16 * ef, pan=-0.45)
            p = pat("shaker", e)
            if p and p[step % 16] == "x":
                v = acc * ef * float(rng.uniform(0.85, 1.1))
                bus.add("hats", shk[step % 3], ts + float(rng.uniform(-hum, hum)), 0.2 * v, pan=-0.5 if step % 2 else 0.3)
            p = pat("rim", e)
            if p and p[step % 16] == "x" and not brk:
                bus.add("perc", rims[step % 2], ts + float(rng.uniform(-hum, hum)), 0.2 * ef, pan=0.4)
        # ---------------------------------------------------------------- bass
        bp_keys = [k for k in st.bass_pat if k <= e] if on("bass", e) or on("sub", e) else []
        if bp_keys:
            bpat = st.bass_pat[max(bp_keys)]
            bv = dict(st.bass_voice)
            ln_b = bv.pop("len", 0.25)
            hop = bv.pop("octave_hop", False)
            f0 = _bass_hz(st, root)
            for s in range(4):
                step = beat * 4 + s
                if bpat[step % 16] != "x":
                    continue
                ts = step_time(b, s)
                if in_break(ts):
                    continue
                f = f0
                if style == "lofi" and step % 16 == 13:
                    f = f0 * 1.4983
                if hop and (step // 4) % 2 == 1:
                    f = f0 * 2
                if beat == bpb - 1 and s >= 2 and nxt_root != root and style in ("drive", "bright", "pulse"):
                    f = _bass_hz(st, nxt_root)                # walk into the next chord
                v = _ACC[step % 4] * float(rng.uniform(0.9, 1.05))
                dur = ln_b * spb * float(rng.uniform(0.92, 1.05))
                bus.add("bass", I.bass(f, dur, v, **bv), ts, g.get("bass", 0.4))
        # ---------------------------------------------------------------- chords
        if beat == 0:
            bar_len = tl.beat_to_time(b + bpb) - tb
            if on("pad", e):
                for j, semi in enumerate(voicing):
                    x = I.pad(A.note_hz(semi), bar_len, st.pad_cutoff * (0.7 + 0.15 * e), voices=5 if style != "lofi" else 3,
                              spread=16.0, attack=0.35 if style in ("drive", "pulse") else 0.6, seed=seed + j + bar * 7,
                              wave="saw" if style not in ("calm", "lofi") else "tri")
                    bus.add("pad", x, tb, g.get("pad", 0.3) / len(voicing))
        if on("rhodes", e):
            comp = {"calm": (0, 2), "lofi": (0, 1.5, 3)}.get(style, (0,))
            for cb in comp:
                if beat != int(cb) or (cb % 1 and False):
                    continue
            hits = [cb for cb in comp if int(cb) == beat]
            for cb in hits:
                ts = tl.beat_to_time(b + (cb - beat)) + (st.swing * spb if (cb % 1) else 0.0)
                dur = spb * (2.0 if style == "calm" else 1.4) * 0.95
                v = 0.75 if cb == 0 else 0.6
                for j, semi in enumerate(voicing):
                    strum = j * (0.012 if style == "calm" else 0.02)
                    bus.add("keys", I.rhodes(A.note_hz(semi), dur, v * float(rng.uniform(0.9, 1.05)), seed=j),
                            ts + strum, g.get("rhodes", 0.34) / len(voicing) * 1.6)
        # arps / sequences
        if on("arp16", e) or on("arp8", e) or on("seq16", e):
            steps = 4 if (on("arp16", e) or on("seq16", e) or (on("arp8", e) and e >= 3)) else 2
            notes = sorted(voicing) + [voicing[0] + 12, voicing[1] + 12]
            for k in range(steps):
                idx = (b * steps + k) % len(notes)
                ts = step_time(b, k * (4 // steps))
                if in_break(ts) and style == "pulse":
                    continue
                semi = notes[idx] + (12 if not on("seq16", e) else 0)
                v = _ACC[(k * (4 // steps)) % 4] * float(rng.uniform(0.85, 1.05))
                dur = spb / steps * 0.85
                if on("seq16", e):
                    x = I.square_seq(A.note_hz(semi), dur, v, cutoff=900 + 700 * e)
                    gain = g.get("seq16", 0.13)
                else:
                    x = I.pluck_saw(A.note_hz(semi), dur, v, cutoff=700 + 250 * e)
                    gain = g.get("arp16" if on("arp16", e) else "arp8", 0.15)
                bus.add("keys", x, ts, gain * v, pan=0.45 * math.sin(b * 1.7 + k * 0.9))
        # bells (bright, top layer)
        if on("bells", e) and beat == 0:
            for j, semi in enumerate(voicing[:3]):
                bus.add("hook", I.bell(A.note_hz(semi + 24), 1.4, 0.7), tl.beat_to_time(b + j * 0.5),
                        g.get("bells", 0.1), pan=0.5 - 0.5 * j)
        if on("blips", e):
            for k in range(4):
                if rng.random() < 0.28:
                    semi = voicing[int(rng.integers(0, len(voicing)))] + 24
                    bus.add("hook", I.bell(A.note_hz(semi), 0.25, 0.5) * 0.6, step_time(b, k), 0.06,
                            pan=float(rng.uniform(-0.7, 0.7)))
        # ---------------------------------------------------------------- hook (2-bar motif)
        if on("hook", e) and beat == 0 and bar % 2 == 0 and st.hook:
            pair = (bar // 2) % 4
            pcs = [{x % 12 for x in chord(bar)[1]}, {x % 12 for x in chord(bar + 1)[1]}]
            for bt, ln, semi, vel in _hook_notes(st, pair, pcs):
                bi = b + bt
                ts = tl.beat_to_time(bi)
                if st.swing and abs((bt * 2) % 2 - 1) < 1e-6:
                    ts += st.swing * spb
                if ts >= duration:
                    continue
                dur = ln * spb * 0.92
                f = A.note_hz(semi)
                v = vel * (0.85 + 0.05 * e)
                if st.hook_voice == "lead":
                    x = I.lead(f, dur, v, cutoff=1400 + 300 * e)
                elif st.hook_voice == "pulse_lead":
                    x = I.lead(f, dur, v, cutoff=1600, wave="pulse", detune=6.0, vib=0.05)
                elif st.hook_voice == "bell":
                    x = _sum(A.stereo(I.bell(f * 2, max(0.6, dur * 2), v) * 0.7), I.lead(f, dur, v * 0.6, 2200, "tri", 5.0))
                else:
                    x = A.haas(I.pluck_ks(f, dur, v, seed=int(bi * 4)), 9.0, 1 if int(bt * 2) % 2 else -1)
                bus.add("hook", x, ts, g.get("hook", 0.2) * v)
    # -------------------------------------------------------------------- arrangement events
    for ev in events:
        k = ev["kind"]
        if k == "fill":
            a, c = ev["start"], ev["end"]
            n16 = max(1, int(round((c - a) / (spb / 4))))
            fb = []
            for i in range(n16):
                t = c - (n16 - i) * (spb / 4)
                v = ev["vel"] * (0.45 + 0.55 * (i + 1) / n16)
                if ev["voice"] == "tom":
                    clip = I.tom(A.note_hz(st.key - 12 - 3 * (i % 3)) * 1.0, 0.16)
                elif ev["voice"] == "rim":
                    clip = rims[i % 2] * 0.9 + snares[i % 3][: len(rims[0])] * 0.25 if False else rims[i % 2]
                else:
                    clip = snares[i % 3]
                fb.append((clip, t, v))
            fill = np.zeros((int((c - a + 0.6) * SR) + 2, 2), np.float32)
            base = a - 0.05
            for clip, t, v in fb:
                A.add_at(fill, A.pan(clip, 0.15 * math.sin(t * 9)), int(round((t - base) * SR)), 0.42 * v)
            # gate: the fill ends exactly on the cut (3 ms fade into the cut sample)
            endk = int(round((c - base) * SR))
            f = int(0.003 * SR)
            fill[max(0, endk - f):endk] *= np.linspace(1, 0, min(f, endk))[:, None]
            fill[endk:] = 0
            bus.add("fill", fill, base, 1.0)
        elif k == "riser":
            dur = ev["end"] - ev["start"]
            if dur < 0.08:
                continue
            bar = int(tl.time_to_beat(ev["end"] - 1e-3) // bpb)
            ch = [A.note_hz(x) for x in chord(max(0, bar))[2][:3]]
            x = I.riser(dur, seed=seed + int(ev["end"] * 100), chord=ch)
            bus.add("riser", x, ev["end"] - len(x) / SR, 0.3 * ev["vel"])
        elif k == "crash":
            bus.add("crash", crash_c, ev["start"], 0.22 * ev["vel"], pan=-0.15)
            if ev["energy"] >= 2:
                bus.add("crash", I.sub_drop(0.8, 62, 32), ev["start"], 0.35 * ev["vel"])
        elif k == "swell":
            ln = min(1.2, ev["start"])
            if ln > 0.1:
                sw = A.fft_filter(A.noise(int(ln * SR), int(ev["start"] * 7)), 2500, 15000)
                sw *= np.linspace(0, 1, len(sw)) ** 2.2
                bus.add("riser", A.width(A.pan(sw, 0.0), 1.0), ev["start"] - ln, 0.12)
        elif k == "stab":
            bar = int(tl.time_to_beat(ev["start"] + 1e-4) // bpb)
            for j, semi in enumerate(chord(max(0, bar))[2]):
                bus.add("hook", I.pluck_saw(A.note_hz(semi + 12), spb * 0.5, ev["vel"], cutoff=1200),
                        ev["start"], 0.12 * ev["vel"], pan=(j - 1.5) * 0.35)
    # -------------------------------------------------------------------- mix: ducking, sends, master
    ducks = {k: _duck(N, kicks, spb, v * st.sidechain / 0.5) for k, v in st.duck_db.items()}
    lv = {**LEVELS, **{k[3:]: v for k, v in g.items() if k.startswith("lv_")}}
    for k, v in lv.items():
        if k in bus.stems:
            bus.stems[k] *= np.float32(v)
    for k, side in (("pad", 1), ("hook", -1), ("keys", 1)):
        if lv.get(k + "_haas", 0) > 0 and k in bus.stems:
            bus.stems[k] = A.haas(bus.stems[k], lv[k + "_haas"], side, 0.7)
    drums = bus.get("kick") * 1.0 + bus.get("clap") + bus.get("hats") + bus.get("perc") + bus.get("fill")
    stems = {
        "drums": drums,
        "bass": bus.get("bass") * ducks.get("bass", 1.0)[:, None] if "bass" in ducks else bus.get("bass"),
        "pad": bus.get("pad") * (ducks["pad"][:, None] if "pad" in ducks else 1.0),
        "keys": bus.get("keys") * (ducks["keys"][:, None] if "keys" in ducks else 1.0),
        "hook": bus.get("hook") * (ducks["hook"][:, None] if "hook" in ducks else 1.0),
        "fx": bus.get("riser") + bus.get("crash"),
    }
    # high-pass everything that is not kick/bass (mud control), cut 300 Hz on the pad
    stems["pad"] = A.eq(stems["pad"], [("highpass", 140, 0.7), ("peak", 320, 1.0, -3.0)])
    stems["keys"] = A.eq(stems["keys"], [("highpass", 120, 0.7)])
    stems["hook"] = A.eq(stems["hook"], [("highpass", 150, 0.7)])
    rv_in = (stems["pad"] * st.sends.get("pad", 0.3) + stems["keys"] * st.sends.get("keys", 0.2)
             + stems["hook"] * st.sends.get("hook", 0.2) + (bus.get("clap") + bus.get("fill")) * st.sends.get("clap", 0.15)
             + (bus.get("hats") + bus.get("perc")) * st.sends.get("drums", 0.05) + bus.get("crash") * 0.15)
    ir = A.room_ir(st.reverb[0], st.reverb[1], seed=211 + seed)
    dl_in = stems["hook"] * 0.3 + stems["keys"] * (0.18 if style in ("drive", "bright", "pulse") else 0.08)
    dl = A.fft_convolve(dl_in, A.delay_ir(0.75 * spb, 0.33, 5, True, 4000))[:N]
    wet = A.fft_convolve(rv_in + dl * 0.3, ir)[:N]
    wet = wet * np.float32(lv.get("wet", 1.0))
    mix = sum(stems.values()) + wet * 0.55 + dl * 0.35
    if DEBUG_STEMS is not None:
        DEBUG_STEMS.clear()
        DEBUG_STEMS.update({k: v[:n] for k, v in stems.items() if k != "drums"}, wet=wet[:n] * 0.55, delay=dl[:n] * 0.35)
        DEBUG_STEMS.update({"d_" + k: bus.stems[k][:n] for k in ("kick", "clap", "hats", "perc", "fill") if k in bus.stems})
    if style == "lofi" and on("vinyl", 0):
        mix += A.stereo(I.vinyl(N, seed + 5)) * np.array([1.0, 0.85], np.float32) * 0.5
    mix = _glue(mix.astype(np.float32), 2.0, 0.03, 3.0)
    bands = [("highpass", 28, 0.7), ("highshelf", 9500, 0.7, st.air_db)]
    if st.lowpass < 16000:
        bands.append(("lowpass", st.lowpass, 0.6))
    mix = A.eq(mix, bands)
    mix = A.saturate(mix / (np.abs(mix).max() + 1e-9) * 0.9, 1.3, 0.5)
    mix = mix[:n]
    if stop_at is not None and 0 < stop_at < duration:
        k = int(stop_at * SR)
        f = int(0.03 * SR)
        mix[k:k + f] *= np.linspace(1, 0, len(mix[k:k + f]))[:, None]
        mix[k + f:] = 0
    else:
        f = min(int(0.6 * SR), n)
        mix[-f:] *= np.linspace(1, 0, f)[:, None] ** 1.5
    peak = np.abs(mix).max()
    return (mix / peak * 0.7 if peak > 0 else mix).astype(np.float32)
