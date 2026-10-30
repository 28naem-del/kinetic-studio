"""Original synthesized SFX kit. Every sound is generated from code (no samples), so the
kit ships royalty-free. Each generator returns stereo float32 at 48 kHz.

Timing convention: sounds whose energy *peaks* later than their start (whoosh, riser)
document their peak offset in PEAK, so the mixer can align the peak to the cue time.
"""
from __future__ import annotations

from collections.abc import Callable
from functools import cache

import numpy as np

from . import dsp as A
from . import instruments as I
from .dsp import SR

PEAK: dict[str, float] = {"whoosh": 0.18, "whoosh_big": 0.42, "swish": 0.12, "riser": 2.0, "riser_short": 1.0,
                          "reverse": 1.2}


def _norm(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    return (x * (peak / (np.abs(x).max() + 1e-9))).astype(np.float32)


def whoosh(dur: float = 0.55, peak: float = 0.18, lo: float = 350, hi: float = 3200, seed: int = 1) -> np.ndarray:
    n = int(dur * SR)
    t = A.tvec(dur)
    x = A.pink(n, seed) * 0.6 + A.noise(n, seed + 3) * 0.4
    u = np.clip(t / peak, 0, 1)
    d = np.clip((t - peak) / (dur - peak), 0, 1)
    centre = lo + (hi - lo) * (u ** 1.5) * (1 - d) + d * (hi * 0.35)
    y = A.stft_sweep(x, centre, q=0.9, n_fft=512)
    y *= A.env_ad(n, peak, peak, (dur - peak) / 3, 2.4)
    p = np.clip((t / dur) * 2 - 1, -1, 1) * 0.7
    return _norm(A.pan(y, p), 0.85)


def whoosh_big() -> np.ndarray:
    a = whoosh(1.1, 0.42, 160, 2400, seed=11)
    n = len(a)
    t = np.arange(n) / SR
    sub = A.sine(np.maximum(42, 90 - 40 * np.clip(t - 0.4, 0, 1)), n) * np.exp(-np.clip(t - 0.42, 0, None) / 0.25)
    sub *= np.clip((t - 0.3) / 0.12, 0, 1)
    return _norm(a + A.stereo(sub * 0.6), 0.9)


def swish() -> np.ndarray:
    return whoosh(0.32, 0.12, 900, 6500, seed=5) * 0.8


def riser(dur: float = 2.0, seed: int = 21) -> np.ndarray:
    n = int(dur * SR)
    t = A.tvec(dur)
    u = t / dur
    x = A.noise(n, seed)
    y = A.stft_sweep(x, 300 + 7000 * u ** 2.2, q=1.0)
    f = 180 + 900 * u ** 2
    tone = (A.sine(f * (1 + 0.004 * np.sin(2 * np.pi * 6 * t)), n) + 0.5 * A.sine(f * 1.5, n)) * 0.25
    out = (y + tone) * (u ** 2.0)
    out[-int(0.01 * SR):] *= np.linspace(1, 0, int(0.01 * SR))
    st = A.convolve_reverb(out, A.reverb_ir(0.8, 0.25, seed), 0.3)[:n]
    return _norm(st, 0.8)


def riser_short() -> np.ndarray:
    return riser(1.0, seed=22)


def reverse() -> np.ndarray:
    """Reversed cymbal-like swell (peaks at its end)."""
    n = int(1.2 * SR)
    x = A.fft_filter(A.noise(n, 31), 3000, 16000) * A.env_exp(n, 0.35)
    x = A.convolve_reverb(x, A.reverb_ir(1.0, 0.4, 33), 0.5)[:n]
    return _norm(x[::-1].copy(), 0.6)


def impact(seed: int = 41, size: float = 1.0) -> np.ndarray:
    dur = 0.9 * size + 0.3
    n = int(dur * SR)
    t = A.tvec(dur)
    f = 45 + 110 * np.exp(-t / 0.045)
    body = A.sine(f, n) * np.exp(-t / (0.32 * size))
    click = A.fft_filter(A.noise(n, seed), 1500, 9000) * np.exp(-t / 0.006)
    tail = A.fft_filter(A.pink(n, seed + 1), 60, 900) * np.exp(-t / (0.18 * size)) * 0.6
    x = A.softclip(body * 1.2 + click * 0.5 + tail, 1.6)
    st = A.convolve_reverb(x, A.reverb_ir(1.2 * size, 0.35 * size, seed), 0.22)[:n]
    return _norm(st, 0.95)


def impact_big() -> np.ndarray:
    a = impact(43, 1.8)
    n = len(a)
    t = np.arange(n) / SR
    crack = A.fft_filter(A.noise(n, 44), 2000, 12000) * np.exp(-t / 0.05) * 0.35
    return _norm(a + A.stereo(crack.astype(np.float32)), 0.97)


def hit() -> np.ndarray:
    n = int(0.45 * SR)
    t = A.tvec(0.45)
    kick = A.sine(55 + 120 * np.exp(-t / 0.03), n) * np.exp(-t / 0.16)
    snap = A.fft_filter(A.noise(n, 51), 900, 7000) * np.exp(-t / 0.05) * 0.55
    x = A.softclip(kick + snap, 1.4)
    return _norm(A.convolve_reverb(x, A.reverb_ir(0.6, 0.15, 52), 0.18)[:n], 0.9)


def thud() -> np.ndarray:
    n = int(0.35 * SR)
    t = A.tvec(0.35)
    x = A.sine(50 + 50 * np.exp(-t / 0.04), n) * np.exp(-t / 0.12)
    return _norm(A.stereo(x), 0.7)


def logo_hit() -> np.ndarray:
    dur = 3.2
    n = int(dur * SR)
    t = A.tvec(dur)
    base = np.zeros((n, 2), np.float32)
    imp = impact_big()
    base[: len(imp)] += imp[:n] * 0.9
    bell = np.zeros(n, np.float32)
    for k, semi in enumerate((0, 7, 12, 16, 19)):
        f = A.note_hz(semi - 9 + 12)  # C major voicing around C5
        bell += A.fm(f, n, 3.5, 2.2, 2.0) * np.exp(-t / (1.4 - 0.15 * k)) * (0.5 / (1 + 0.3 * k))
    pad = A.convolve_reverb(bell, A.reverb_ir(2.4, 0.9, 61), 0.45)[:n]
    return _norm(base + pad * 0.7, 0.95)


def click() -> np.ndarray:
    n = int(0.04 * SR)
    t = A.tvec(0.04)
    x = A.fft_filter(A.noise(n, 71), 2000, 14000) * np.exp(-t / 0.003) + A.sine(2200, n) * np.exp(-t / 0.008) * 0.4
    return _norm(A.stereo(x), 0.6)


def key(seed: int = 81) -> np.ndarray:
    n = int(0.09 * SR)
    t = A.tvec(0.09)
    tick = A.fft_filter(A.noise(n, seed), 2500, 9000) * np.exp(-t / 0.004)
    thock = A.sine(240 + 60 * np.exp(-t / 0.01), n) * np.exp(-t / 0.02) * 0.6
    return _norm(A.pan(tick + thock, ((seed % 7) - 3) * 0.08), 0.5)


def _room(x: np.ndarray, wet: float = 0.12, rt: float = 0.35, seed: int = 0) -> np.ndarray:
    """Small, bright room so UI sounds sit in the same space as the bed."""
    n = len(x)
    ir = A.room_ir(rt, 0.006, 9000.0, seed=seed, width=1.0, hp=500.0)
    w = A.fft_convolve(A.stereo(x).astype(np.float32), ir)[:n]
    return (A.stereo(x) + w * wet).astype(np.float32)


def pop(f0: float = 520.0, f1: float = 1350.0, dur: float = 0.12, seed: int = 72) -> np.ndarray:
    """Bubble pop: fast upward sine sweep with a 2nd harmonic, a noise click and a short room."""
    n = int(dur * SR)
    t = A.tvec(dur)[:n]
    f = f0 + (f1 - f0) * (1 - np.exp(-t / 0.018))
    env = A.env_exp(n, 0.032, 0.0008)
    x = (A.sine(f, n) + 0.22 * A.sine(2 * f, n) * np.exp(-t / 0.012)) * env
    x += A.fft_filter(A.noise(n, seed), 2500, 12000) * np.exp(-t / 0.0015) * 0.35
    return _norm(_room(x, 0.10, 0.25, seed), 0.6)


def tick(f: float = 1700.0, seed: int = 73) -> np.ndarray:
    """Clock tick: inharmonic pair of partials over a sub-millisecond noise transient."""
    n = int(0.035 * SR)
    t = A.tvec(0.035)[:n]
    x = A.sine(f, n) * np.exp(-t / 0.006) + 0.45 * A.sine(f * 2.76, n) * np.exp(-t / 0.003)
    x += A.fft_filter(A.noise(n, seed), 4000, 16000) * np.exp(-t / 0.0007) * 0.6
    return _norm(A.stereo(x), 0.45)


def blip(notes: tuple[float, ...] = (880.0, 1320.0), step: float = 0.045, seed: int = 74) -> np.ndarray:
    """Two-step data blip: band-limited square-ish tone with an octave-down body, slight stereo detune."""
    dur = step * len(notes) + 0.075
    n = int(dur * SR)
    t = A.tvec(dur)[:n]
    idx = np.minimum((t / step).astype(int), len(notes) - 1)
    f = np.asarray(notes, np.float64)[idx]
    body = A.sine(f, n) + 0.3 * A.sine(f * 3, n) + 0.12 * A.sine(f * 5, n) + 0.25 * A.sine(f * 0.5, n)
    env = A.env_exp(n, dur * 0.42, 0.001)
    l = body * env
    r = (A.sine(f * 1.004, n) + 0.3 * A.sine(f * 3.012, n) + 0.25 * A.sine(f * 0.5, n)) * env
    return _norm(_room(np.stack([l, r], 1), 0.08, 0.25, seed), 0.5)


def ui_click(seed: int = 75) -> np.ndarray:
    """Crisp interface click: 1 ms bright transient, a 3.4 kHz ping and a tiny low body."""
    n = int(0.05 * SR)
    t = A.tvec(0.05)[:n]
    x = A.fft_filter(A.noise(n, seed), 3000, 15000) * np.exp(-t / 0.001)
    x += A.sine(3400, n) * np.exp(-t / 0.005) * 0.35 + A.sine(620, n) * np.exp(-t / 0.004) * 0.3
    return _norm(A.stereo(x), 0.55)


def ui_tap(seed: int = 76) -> np.ndarray:
    """Soft tap: falling 900 Hz body with a muted noise touch."""
    n = int(0.06 * SR)
    t = A.tvec(0.06)[:n]
    x = A.sine(700 + 400 * np.exp(-t / 0.006), n) * A.env_exp(n, 0.014, 0.0005)
    x += A.fft_filter(A.noise(n, seed), 1200, 6000) * np.exp(-t / 0.002) * 0.3
    return _norm(A.stereo(x), 0.5)


def tick_hi(seed: int = 77) -> np.ndarray:
    return tick(4200.0, seed)


def pop_soft(seed: int = 78) -> np.ndarray:
    n = int(0.1 * SR)
    x = pop(300.0, 720.0, 0.1, seed)[:n]
    a = int(0.003 * SR)
    x[:a] *= np.linspace(0, 1, a)[:, None]
    return _norm(x, 0.5)


def blip_up() -> np.ndarray:
    return blip((660.0, 990.0, 1320.0), 0.04, 79)


def blip_down() -> np.ndarray:
    return blip((1320.0, 990.0, 660.0), 0.04, 80)


def snap(seed: int = 82) -> np.ndarray:
    """Finger snap: resonant 1.5-5 kHz noise burst with a 2.1 kHz body and a short room."""
    n = int(0.12 * SR)
    t = A.tvec(0.12)[:n]
    x = A.fft_filter(A.noise(n, seed), 1500, 5500) * np.exp(-t / 0.012)
    x += A.sine(2100, n) * np.exp(-t / 0.008) * 0.4
    return _norm(_room(x, 0.18, 0.4, seed), 0.6)


def sub_drop() -> np.ndarray:
    """Sub drop: 62 -> 28 Hz pitch-falling sine, lightly saturated so it reads on small speakers."""
    return _norm(A.stereo(A.softclip(I.sub_drop(1.1, 62.0, 28.0) * 1.6, 1.8)), 0.9)


def downlifter(dur: float = 1.4, seed: int = 83) -> np.ndarray:
    """Falling filtered-noise sweep with a short sub tail (energy at its start)."""
    n = int(dur * SR)
    t = A.tvec(dur)[:n]
    u = t / dur
    nl, nr = A.noise(n, seed), A.noise(n, seed + 1)
    centre = 300 + 8000 * (1 - u) ** 2.5
    y = np.stack([A.stft_sweep(nl, centre, q=1.0), A.stft_sweep(nr, centre * 1.05, q=1.0)], 1)
    a = int(0.004 * SR)
    env = (1 - u) ** 1.6
    env[:a] *= np.linspace(0, 1, a)
    sub = A.sine(55 - 20 * u, n) * np.exp(-t / 0.3) * 0.4
    return _norm(y * env[:, None] + A.stereo(sub), 0.8)


def glitch(seed: int = 91) -> np.ndarray:
    n = int(0.3 * SR)
    r = A.rng(seed)
    x = A.noise(n, seed)
    seg = 512
    for s in range(0, n, seg):
        mode = r.integers(0, 4)
        if mode == 0:
            x[s:s + seg] = 0
        elif mode == 1:
            x[s:s + seg] = np.round(x[s:s + seg] * 3) / 3
        elif mode == 2:
            x[s:s + seg] = A.sine(r.uniform(200, 3000), len(x[s:s + seg]))
    x *= np.exp(-np.arange(n) / SR / 0.15)
    return _norm(A.pan(x, 0.3), 0.55)


def ding() -> np.ndarray:
    n = int(1.4 * SR)
    t = A.tvec(1.4)
    x = A.fm(1318.5, n, 3.5, 1.6, 3.0) * np.exp(-t / 0.5) + 0.4 * A.fm(1975.5, n, 2.0, 1.0, 4.0) * np.exp(-t / 0.35)
    return _norm(A.convolve_reverb(x, A.reverb_ir(1.2, 0.4, 101), 0.3)[:n], 0.55)


def chime() -> np.ndarray:
    n = int(1.8 * SR)
    out = np.zeros(n, np.float32)
    for k, semi in enumerate((3, 7, 10, 15)):
        st = int(k * 0.07 * SR)
        m = n - st
        tt = np.arange(m) / SR
        out[st:] += A.fm(A.note_hz(semi + 12), m, 3.0, 1.2, 3.0) * np.exp(-tt / 0.6) * 0.35
    return _norm(A.convolve_reverb(out, A.reverb_ir(1.6, 0.6, 111), 0.4)[:n], 0.5)


KIT: dict[str, Callable[[], np.ndarray]] = {
    "whoosh": whoosh, "whoosh_big": whoosh_big, "swish": swish, "riser": riser, "riser_short": riser_short,
    "reverse": reverse, "impact": impact, "impact_big": impact_big, "hit": hit, "thud": thud, "logo_hit": logo_hit,
    "click": click, "key": key, "pop": pop, "tick": tick, "blip": blip, "data_blip": blip, "glitch": glitch,
    "ding": ding, "chime": chime,
    "sub_drop": sub_drop, "downlifter": downlifter, "ui_click": ui_click, "ui_tap": ui_tap, "tick_hi": tick_hi,
    "pop_soft": pop_soft, "blip_up": blip_up, "blip_down": blip_down, "snap": snap,
}

# default mix gains (dB) so every cue sits well without hand-tuning
GAIN: dict[str, float] = {"whoosh": -6, "whoosh_big": -4, "swish": -8, "riser": -9, "riser_short": -10, "reverse": -10,
                          "impact": -3, "impact_big": -1, "hit": -5, "thud": -8, "logo_hit": -1, "click": -12,
                          "key": -12, "pop": -11, "tick": -16, "blip": -13, "data_blip": -13, "glitch": -9,
                          "ding": -10, "chime": -9, "sub_drop": -4, "downlifter": -10, "ui_click": -13,
                          "ui_tap": -12, "tick_hi": -18, "pop_soft": -11, "blip_up": -13, "blip_down": -13, "snap": -11}

# short interface sounds: the mixer randomises their pitch slightly per hit so repeats never sound copy-pasted
UI_SOUNDS = {"click", "key", "pop", "tick", "blip", "data_blip", "ui_click", "ui_tap", "tick_hi", "pop_soft",
             "blip_up", "blip_down", "snap", "glitch"}


@cache
def get(name: str) -> np.ndarray:
    if name not in KIT:
        raise KeyError(f"unknown sound {name!r}; kit: {sorted(KIT)}")
    x = KIT[name]()
    x.setflags(write=False)
    return x


@cache
def render(name: str, velocity: float = 1.0, pitch: float = 0.0, seed: int = 0) -> np.ndarray:
    """A kit sound at a velocity (0..1: level and brightness, softer = darker) and a pitch offset in
    semitones (varispeed, so the clip also gets shorter/longer). `seed` gives a small stereo offset so
    repeated hits are not bit-identical. Cached; call with rounded arguments."""
    x = get(name)
    if abs(pitch) > 1e-3:
        x = A.resample(x, 2 ** (pitch / 12))
    v = float(np.clip(velocity, 0.05, 1.5))
    if v < 0.999:
        x = A.eq(A.stereo(x), [("highshelf", 2500.0, 0.7, -10.0 * (1 - min(v, 1.0)))])
    x = A.stereo(x) * v
    if seed:
        p = ((seed * 0.61803) % 1.0 - 0.5) * 0.24
        x = x * np.array([1 - p, 1 + p], np.float32)
    y = np.clip(x, -1.0, 1.0).astype(np.float32)
    y.setflags(write=False)
    return y


def peak_of(name: str, pitch: float = 0.0) -> float:
    """Peak offset (s) of a sound rendered at `pitch` semitones."""
    return PEAK.get(name, 0.0) / (2 ** (pitch / 12))


def export_kit(folder: str) -> list[str]:
    import os
    os.makedirs(folder, exist_ok=True)
    out = []
    for name in KIT:
        p = os.path.join(folder, f"{name}.wav")
        A.write_wav(p, get(name))
        out.append(p)
    return out
