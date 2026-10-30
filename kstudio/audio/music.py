"""Procedural music beds. Five original styles, arranged live from the film's timeline:
the beat grid places every note, and section markers (energy 0..3) add/remove layers,
open filters and trigger fills, reverse swells and crashes at section changes.
A cue marker named "music_stop" hard-stops the bed (e.g. under a logo sting).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..timeline import Timeline
from . import dsp as A
from .dsp import SR

CHORDS = {"M": (0, 4, 7), "m": (0, 3, 7), "M7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "7": (0, 4, 7, 10),
          "6": (0, 4, 7, 9), "sus2": (0, 2, 7)}


@dataclass
class Style:
    name: str
    bpm: float
    key: int                        # semitones from A4 of the chord-register root
    prog: list[tuple[int, str]]
    layers: dict[str, int]          # layer -> minimum energy
    sidechain: float = 0.0
    swing: float = 0.0
    lowpass: float = 16000.0
    gains: dict[str, float] = field(default_factory=dict)


STYLES: dict[str, Style] = {
    "drive": Style("drive", 128, -12, [(0, "m"), (-4, "M"), (3, "M"), (-2, "M")],
                   {"pad": 0, "kick": 1, "hat": 1, "bass_root": 1, "clap": 2, "bass8": 2, "arp16": 3, "ohat": 3},
                   sidechain=0.6, gains={"pad": 0.35, "arp16": 0.22, "bass8": 0.5, "bass_root": 0.4}),
    "calm": Style("calm", 84, -7, [(0, "M7"), (-3, "m7"), (-7, "M7"), (-5, "6")],
                  {"pad": 0, "rhodes": 0, "softkick": 1, "shaker": 1, "sub": 1, "melody": 2, "rim": 3},
                  sidechain=0.12, lowpass=9000, gains={"pad": 0.35, "rhodes": 0.32, "melody": 0.16}),
    "bright": Style("bright", 112, -9, [(0, "M"), (7, "M"), (9, "m"), (5, "M")],
                    {"pad": 0, "arp8": 1, "kick": 1, "bass8": 1, "clap": 2, "hat16": 2, "bells": 3},
                    sidechain=0.5, gains={"pad": 0.3, "arp8": 0.28, "bass8": 0.45, "bells": 0.15}),
    "pulse": Style("pulse", 120, -3, [(0, "m"), (0, "m"), (-4, "M"), (-2, "M")],
                   {"pad": 0, "seq16": 0, "kick": 1, "subpulse": 1, "hat16": 2, "rim": 2, "blips": 2, "ohat": 3},
                   sidechain=0.45, gains={"pad": 0.22, "seq16": 0.2, "subpulse": 0.5}),
    "lofi": Style("lofi", 86, -6, [(0, "M7"), (-3, "m7"), (-7, "M7"), (-5, "7")],
                  {"rhodes": 0, "vinyl": 0, "bbkick": 1, "snare": 1, "swhat": 1, "bass_lofi": 1, "pad": 2},
                  sidechain=0.15, swing=0.14, lowpass=6500, gains={"rhodes": 0.4, "pad": 0.2, "bass_lofi": 0.5}),
}


# ---------------------------------------------------------------------- one-shots

def _kick(n_dur=0.32, click=0.4, f0=150, f1=48, decay=0.26, seed=1):
    n = int(n_dur * SR); t = A.tvec(n_dur)
    x = A.sine(f1 + (f0 - f1) * np.exp(-t / 0.035), n) * np.exp(-t / decay)
    x += A.fft_filter(A.noise(n, seed), 2000, 9000) * np.exp(-t / 0.004) * click
    return A.softclip(x, 1.3)


def _clap(seed=3):
    n = int(0.35 * SR); t = A.tvec(0.35)
    env = np.zeros(n, np.float32)
    for k, off in enumerate((0.0, 0.011, 0.022)):
        env += np.exp(-np.clip(t - off, 0, None) / (0.008 if k < 2 else 0.12)) * (t >= off)
    x = A.fft_filter(A.noise(n, seed), 900, 6000) * env
    return A.convolve_reverb(x, A.reverb_ir(0.6, 0.18, seed), 0.25)[:n]


def _snare(seed=5, decay=0.12):
    n = int(0.3 * SR); t = A.tvec(0.3)
    x = A.fft_filter(A.noise(n, seed), 250, 8000) * np.exp(-t / decay) * 0.8 + A.sine(190, n) * np.exp(-t / 0.05) * 0.5
    return A.convolve_reverb(x, A.reverb_ir(0.5, 0.15, seed), 0.2)[:n]


def _hat(decay=0.03, seed=7, lo=7000):
    dur = max(0.08, decay * 5); n = int(dur * SR); t = A.tvec(dur)
    return A.fft_filter(A.noise(n, seed), lo, 18000) * np.exp(-t / decay)


def _shaker(seed=9):
    n = int(0.09 * SR)
    return A.fft_filter(A.noise(n, seed), 4000, 11000) * A.env_ad(n, 0.02, 0.02, 0.025, 1.5)


def _rim(seed=11):
    n = int(0.08 * SR); t = A.tvec(0.08)
    return A.sine(1700, n) * np.exp(-t / 0.01) * 0.6 + A.fft_filter(A.noise(n, seed), 2000, 8000) * np.exp(-t / 0.006) * 0.5


def _crash(seed=13):
    n = int(2.0 * SR); t = A.tvec(2.0)
    x = A.fft_filter(A.noise(n, seed), 3000, 16000) * np.exp(-t / 0.7)
    return A.convolve_reverb(x, A.reverb_ir(1.2, 0.5, seed), 0.3)[:n]


def _reverse_swell(dur: float, seed=15):
    n = int(dur * SR); t = A.tvec(dur)
    x = A.fft_filter(A.noise(n, seed), 2500, 15000) * np.exp(-t / (dur * 0.35))
    return x[::-1].copy()


# ---------------------------------------------------------------------- tonal voices

def _pluck(f, dur, bright=1.0, seed=0):
    n = int(dur * SR)
    x = A.additive(f, n, harmonics=10, tilt=1.0, bright_decay=9.0 / max(bright, 0.1), seed=seed)
    return x * A.env_exp(n, dur * 0.45, 0.002)


def _bass(f, dur, seed=0, harmonics=6):
    n = int(dur * SR)
    x = A.additive(f, n, harmonics=harmonics, tilt=1.2, bright_decay=4.0, seed=seed)
    env = np.minimum(np.clip(np.arange(n) / (0.004 * SR), 0, 1), np.exp(-np.arange(n) / SR / (dur * 0.9)))
    tail = int(min(0.02, dur * 0.2) * SR)
    env[-tail:] *= np.linspace(1, 0, tail)
    return x * env


def _pad_note(f, dur, seed=0):
    n = int((dur + 0.6) * SR)
    t = np.arange(n) / SR
    x = (A.additive(f, n, 7, 1.1, detune=0.003, seed=seed) + A.additive(f * 1.003, n, 7, 1.1, detune=0.003, seed=seed + 1))
    env = np.clip(t / 0.35, 0, 1) * np.where(t > dur, np.exp(-(t - dur) / 0.25), 1.0)
    return x * env * 0.5


def _rhodes(f, dur, seed=0):
    n = int((dur + 0.5) * SR)
    t = np.arange(n) / SR
    x = A.fm(f, n, 1.0, 1.4, 2.5) + 0.25 * A.fm(f * 2, n, 7.0, 0.6, 6.0)
    env = np.exp(-t / 1.4) * np.where(t > dur, np.exp(-(t - dur) / 0.15), 1.0) * np.clip(t / 0.004, 0, 1)
    trem = 1 + 0.12 * np.sin(2 * np.pi * 4.5 * t + seed)
    return x * env * trem


def _square(f, dur, seed=0):
    n = int(dur * SR)
    return A.additive(f, n, 9, 1.0, bright_decay=12.0, odd_only=True, seed=seed) * A.env_exp(n, dur * 0.5, 0.002)


# ---------------------------------------------------------------------- arranger

def _chord_at(st: Style, bar: int) -> list[float]:
    root, q = st.prog[bar % len(st.prog)]
    return [A.note_hz(st.key + root + iv) for iv in CHORDS[q]]


def generate(style: str, tl: Timeline, duration: float, seed: int = 0, stop_at: float | None = None) -> np.ndarray:
    st = STYLES[style]
    n = int(math.ceil(duration * SR))
    pad_n = n + int(3 * SR)
    stems: dict[str, np.ndarray] = {k: np.zeros((pad_n, 2), np.float32) for k in ("drums", "bass", "keys", "pad", "fx")}
    rng = A.rng(seed + 97)
    cache: dict[tuple, np.ndarray] = {}

    def once(key: tuple, fn):
        if key not in cache:
            cache[key] = fn()
        return cache[key]

    def at(b: float) -> int:
        return int(round(tl.beat_to_time(b) * SR))

    def on(layer: str, e: float) -> bool:
        return layer in st.layers and e >= st.layers[layer]

    bpb = tl.beats_per_bar
    total_beats = int(math.ceil(tl.time_to_beat(duration))) + 1
    spb = max(0.2, tl.beat_to_time(1.0) - tl.beat_to_time(0.0))
    kick_times: list[int] = []
    g = st.gains
    prev_e = None
    first_b = max(0, int(math.floor(tl.time_to_beat(0.0))))
    for b in range(first_b, total_beats + 1):
        tb = tl.beat_to_time(b)
        if tb > duration + 0.05:
            break
        e = tl.energy_at(tb + 1e-4)
        bar, beat = divmod(b, bpb)
        chord = _chord_at(st, bar)
        sw = st.swing * spb
        # section change: swell + crash + fill
        if beat == 0 and prev_e is not None and e > prev_e and tb > 0.5:
            sw_len = min(1.2, tb)
            A.add_at(stems["fx"], A.pan(_reverse_swell(sw_len, seed + b), 0.0), at(b) - int(sw_len * SR), 0.18)
            A.add_at(stems["fx"], A.stereo(once(("crash",), _crash)), at(b), 0.14 if e >= 3 else 0.08)
        prev_e = e if beat == 0 else (e if prev_e is None else prev_e)
        nxt = tl.energy_at(tl.beat_to_time(b + 1) + 1e-4)
        fill = beat == bpb - 1 and nxt > e and on("kick", nxt)
        # drums ---------------------------------------------------------
        if on("kick", e):
            A.add_at(stems["drums"], A.stereo(once(("kick",), _kick)), at(b), 0.9)
            kick_times.append(at(b))
        if on("softkick", e) and beat in (0, 2):
            A.add_at(stems["drums"], A.stereo(once(("skick",), lambda: _kick(0.3, 0.1, 110, 50, 0.2))), at(b), 0.55)
            kick_times.append(at(b))
        if on("bbkick", e) and beat in (0, 2):
            A.add_at(stems["drums"], A.stereo(once(("bbk",), lambda: A.fft_filter(_kick(0.35, 0.2, 120, 46, 0.24), 0, 4000))),
                     at(b), 0.75)
            kick_times.append(at(b))
            if beat == 2:
                A.add_at(stems["drums"], A.stereo(once(("bbk",), lambda: _kick())), at(b + 0.5) + int(sw * SR), 0.45)
        if on("clap", e) and beat % 2 == 1:
            A.add_at(stems["drums"], A.stereo(once(("clap",), _clap)), at(b), 0.42)
        if on("snare", e) and beat % 2 == 1:
            A.add_at(stems["drums"], A.stereo(once(("snare",), lambda: A.fft_filter(_snare(5, 0.1), 0, 7000))), at(b), 0.4)
        if on("rim", e) and beat == 3:
            A.add_at(stems["drums"], A.pan(once(("rim",), _rim), 0.25), at(b + 0.5), 0.18)
        if on("hat", e):
            A.add_at(stems["drums"], A.pan(once(("hat",), _hat), 0.2), at(b + 0.5), 0.22)
        if on("ohat", e):
            A.add_at(stems["drums"], A.pan(once(("ohat",), lambda: _hat(0.16, 8, 6000)), -0.2), at(b + 0.5), 0.07)
        if on("hat16", e):
            for k in range(4):
                A.add_at(stems["drums"], A.pan(once(("hat",), _hat), 0.25 if k % 2 else -0.15), at(b + k / 4),
                         0.13 if k % 2 else 0.07)
        if on("swhat", e):
            for k in range(2):
                A.add_at(stems["drums"], A.pan(once(("hat",), _hat), 0.2), at(b + k / 2) + (int(sw * SR) if k else 0),
                         0.12 if k else 0.08)
        if on("shaker", e):
            for k in range(2):
                A.add_at(stems["drums"], A.pan(once(("shk",), _shaker), 0.3), at(b + k / 2), 0.08 if k else 0.05)
        if fill:
            for k in range(4):
                A.add_at(stems["drums"], A.stereo(once(("snare",), _snare)), at(b + k / 4), 0.12 + 0.07 * k)
        # bass ------------------------------------------------------------
        root = chord[0] / 4
        if on("bass8", e):
            for kb in (0.5,) if not on("arp16", e) else (0.25, 0.5, 0.75):
                A.add_at(stems["bass"], A.stereo(once(("b8", round(root, 2), kb > 0.5), lambda: _bass(root, spb * 0.22, b))),
                         at(b + kb), g.get("bass8", 0.5))
            if beat == 0:
                A.add_at(stems["bass"], A.stereo(once(("b8r", round(root, 2)), lambda: _bass(root, spb * 0.4, b))), at(b),
                         g.get("bass8", 0.5) * 0.8)
        elif on("bass_root", e) and beat == 0:
            A.add_at(stems["bass"], A.stereo(once(("br", round(root, 2)), lambda: _bass(root, spb * bpb * 0.95, b, 4))), at(b),
                     g.get("bass_root", 0.4))
        if on("sub", e) and beat == 0:
            A.add_at(stems["bass"], A.stereo(once(("sub", round(root, 2)), lambda: A.sine(root, int(spb * bpb * SR))
                                                  * np.minimum(1, np.linspace(0, 40, int(spb * bpb * SR)))
                                                  * np.exp(-np.arange(int(spb * bpb * SR)) / SR / (spb * 3)))), at(b), 0.5)
        if on("subpulse", e):
            for kb in (0.0, 0.5):
                A.add_at(stems["bass"], A.stereo(once(("sp", round(root, 2)), lambda: _bass(root, spb * 0.3, b, 3))), at(b + kb),
                         g.get("subpulse", 0.5) * (1.0 if kb == 0 else 0.7))
        if on("bass_lofi", e) and beat in (0, 2):
            f = root if beat == 0 else root * 1.4983
            A.add_at(stems["bass"], A.stereo(once(("bl", round(f, 2)), lambda: A.fft_filter(_bass(f, spb * 1.6, b, 5), 0, 900))),
                     at(b) + int(sw * SR * 0.5), g.get("bass_lofi", 0.5))
        # keys ------------------------------------------------------------
        if on("arp16", e) or on("arp8", e) or on("seq16", e):
            steps = 2 if on("arp8", e) and not on("arp16", e) and not on("seq16", e) else 4
            notes = chord + [chord[0] * 2, chord[1] * 2]
            for k in range(steps):
                idx = (b * steps + k) % len(notes)
                f = notes[idx] * (1 if on("seq16", e) else 2)
                if on("seq16", e):
                    clip = once(("sq", round(f, 2)), lambda: _square(f / 2, spb / steps * 0.9, idx))
                    gain = g.get("seq16", 0.2) * (0.5 if e < 1 else 1.0)
                else:
                    clip = once(("pl", round(f, 2)), lambda: _pluck(f, spb * 0.9, 1.0, idx))
                    gain = g.get("arp16" if on("arp16", e) else "arp8", 0.25)
                A.add_at(stems["keys"], A.pan(clip, 0.35 * math.sin(b * 1.7 + k)), at(b + k / steps), gain)
        if on("rhodes", e) and beat in ((0, 2) if style != "lofi" else (0,)):
            dur = spb * (2 if style != "lofi" else 4) * 0.95
            for j, f in enumerate(chord):
                strum = int(j * 0.018 * SR)
                A.add_at(stems["keys"], A.pan(once(("rh", round(f, 2), round(dur, 2)), lambda: _rhodes(f, dur, j)), (j - 1.5) * 0.25),
                         at(b) + strum + (int(sw * SR) if style == "lofi" else 0), g.get("rhodes", 0.3) / len(chord) * 2)
        if on("melody", e) and rng.random() < 0.45:
            scale = [0, 2, 4, 7, 9, 12, 14]
            f = A.note_hz(st.key + st.prog[bar % len(st.prog)][0] + 12 + scale[int(rng.integers(0, len(scale)))])
            A.add_at(stems["keys"], A.pan(once(("mel", round(f, 2)), lambda: _pluck(f, spb * 1.5, 0.6)), float(rng.uniform(-0.5, 0.5))),
                     at(b + (0.5 if rng.random() < 0.5 else 0.0)), g.get("melody", 0.15))
        if on("bells", e) and beat == 0:
            for j, f in enumerate(chord):
                A.add_at(stems["keys"], A.pan(once(("bell", round(f, 2)), lambda: A.fm(f * 4, int(1.5 * SR), 3.5, 1.5, 3.0)
                                                   * np.exp(-np.arange(int(1.5 * SR)) / SR / 0.5)), 0.4 - 0.4 * j),
                         at(b + j * 0.25), g.get("bells", 0.15))
        if on("blips", e) and rng.random() < 0.3:
            f = chord[int(rng.integers(0, len(chord)))] * 4
            A.add_at(stems["fx"], A.pan(once(("blp", round(f, 2)), lambda: A.sine(f, int(0.06 * SR)) * A.env_exp(int(0.06 * SR), 0.02)),
                                        float(rng.uniform(-0.7, 0.7))), at(b + 0.75), 0.06)
        # pad -------------------------------------------------------------
        if on("pad", e) and beat == 0:
            dur = tl.beat_to_time(b + bpb) - tb
            for j, f in enumerate(chord):
                A.add_at(stems["pad"], A.pan(once(("pad", round(f, 2), round(dur, 2)), lambda: _pad_note(f, dur, j)), (j - 1) * 0.4),
                         at(b), g.get("pad", 0.3) / len(chord))
    # filter sweep on pad follows energy
    t_axis = np.arange(pad_n) / SR
    cut = np.array([600 + 1500 * tl.energy_at(float(x)) for x in t_axis[:: SR // 20]], np.float32)
    cut = np.interp(t_axis, t_axis[:: SR // 20][: len(cut)], cut).astype(np.float32)
    for ch in range(2):
        stems["pad"][:, ch] = A.stft_sweep(stems["pad"][:, ch], cut, mode="low", n_fft=2048)
    # sidechain pump
    if st.sidechain > 0 and kick_times:
        pump = np.zeros(pad_n, np.float32)
        imp = np.zeros(pad_n, np.float32)
        for k in kick_times:
            if 0 <= k < pad_n:
                imp[k] = 1.0
        rel = np.exp(-np.arange(int(0.25 * SR)) / SR / 0.09).astype(np.float32)
        pump = np.convolve(imp, rel)[:pad_n]
        duck = 1 - st.sidechain * np.clip(pump, 0, 1)
        stems["pad"] *= duck[:, None]
        stems["bass"] *= (1 - (1 - duck) * 0.6)[:, None]
    mix = stems["drums"] * 0.9 + stems["bass"] + stems["keys"] + stems["pad"] + stems["fx"]
    if style == "lofi":
        r = A.rng(seed + 5)
        crackle = np.zeros(pad_n, np.float32)
        pos = r.integers(0, pad_n, int(pad_n / SR * 18))
        crackle[pos] = r.uniform(-1, 1, len(pos))
        crackle_f = A.fft_filter(crackle, 1500, 9000) * 0.25 + A.fft_filter(A.noise(pad_n, seed), 400, 3000) * 0.004
        mix += A.stereo(crackle_f.astype(np.float32))
    mix = A.convolve_reverb(mix, A.reverb_ir(1.4, 0.4, 211), 0.12)[:pad_n]
    if st.lowpass < 16000:
        mix = A.fft_filter(mix, 25, st.lowpass, 1.5)
    else:
        mix = A.fft_filter(mix, 25)
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
