"""Synthesised instruments for the procedural music beds (no samples).

Drums follow analogue drum-machine recipes (808/909-style kick with a pitch envelope and click,
two-tone snare plus band-passed noise, multi-burst clap, metallic six-square hats); tonal voices
are band-limited oscillators through a resonant low-pass with a filter envelope (dsp.osc),
unison stacks, Karplus-Strong plucks and a velocity-sensitive FM electric piano.
Every function returns float32 at 48 kHz: drums and bass mono (n,), stereo voices (n, 2).
"""
from __future__ import annotations

import numpy as np

from . import dsp as A
from .dsp import SR

HAT_FREQS = (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)   # commonly cited TR-808 metal oscillators


def _t(dur: float) -> np.ndarray:
    return np.arange(int(dur * SR), dtype=np.float32) / SR


# ---------------------------------------------------------------------- drums

def kick(f_start: float = 210.0, f_end: float = 52.0, tau_p: float = 0.032, decay: float = 0.34,
         click: float = 0.5, drive: float = 2.2, seed: int = 1) -> np.ndarray:
    """808/909-style kick: sine with exponential pitch envelope, amp decay, 2-4 ms click, soft clip."""
    dur = min(1.4, decay * 4 + 0.05)
    n = int(dur * SR)
    t = _t(dur)
    f = f_end + (f_start - f_end) * np.exp(-t / tau_p)
    body = A.sine(f, n) * np.minimum(1.0, t / 0.0015) * np.exp(-t / decay)
    body += 0.18 * A.sine(f * 2, n) * np.exp(-t / (decay * 0.3))
    clk = A.fft_filter(A.noise(n, seed), 1800, 9000) * np.exp(-t / 0.0028)
    blip = A.sine(1000.0, n) * np.exp(-t / 0.004) * 0.25
    x = A.softclip(body * 1.1 + (clk + blip) * click, drive)
    return x.astype(np.float32)


def snare(tone: tuple[float, float] = (185.0, 330.0), tone_decay: float = 0.07, noise_decay: float = 0.17,
          noise_db: float = 2.0, lo: float = 1500.0, hi: float = 9500.0, seed: int = 5) -> np.ndarray:
    """Two sines with a ~1 octave 30 ms pitch drop + band-passed noise (noise above body)."""
    dur = max(0.35, noise_decay * 3)
    n = int(dur * SR)
    t = _t(dur)
    drop = 1 + np.exp(-t / 0.03)
    body = (A.sine(tone[0] * drop, n) + 0.6 * A.sine(tone[1] * drop, n)) * np.exp(-t / tone_decay) / 1.6
    nz = A.fft_filter(A.noise(n, seed), lo, hi, 1.5) * np.exp(-t / noise_decay)
    nz += A.fft_filter(A.noise(n, seed + 1), 5000, 16000) * np.exp(-t / 0.012) * 0.6
    x = body + nz * A.db(noise_db) * 1.6
    return A.softclip(x * 0.9, 1.5)


def clap(seed: int = 3, tail: float = 0.16) -> np.ndarray:
    n = int(0.45 * SR)
    t = _t(0.45)
    env = np.zeros(n, np.float32)
    for k, off in enumerate((0.0, 0.010, 0.021, 0.031)):
        dec = tail if k == 3 else 0.007
        env += np.where(t >= off, np.exp(-np.clip(t - off, 0, None) / dec), 0.0) * (0.8 if k < 3 else 1.0)
    x = A.fft_filter(A.noise(n, seed), 900, 3200, 1.2) * env + A.fft_filter(A.noise(n, seed + 2), 3500, 12000) * env * 0.35
    return x.astype(np.float32)


def hat(open_: bool = False, seed: int = 7, tune: float = 1.0, decay: float | None = None) -> np.ndarray:
    """Six detuned square oscillators -> HPF 7 kHz, plus a little noise sizzle."""
    d = decay if decay is not None else (0.32 if open_ else 0.045)
    dur = min(1.2, d * 5 + 0.02)
    n = int(dur * SR)
    t = _t(dur)
    r = A.rng(seed)
    metal = np.zeros(n, np.float32)
    for f in HAT_FREQS:
        ph = r.uniform(0, 1)
        metal += np.sign(np.sin(2 * np.pi * (f * tune * 1.0 + r.uniform(-2, 2)) * t + ph * 6.283)).astype(np.float32)
    metal = A.fft_filter(metal / 6, 6500, 13000, 2.0)
    sizzle = A.fft_filter(A.noise(n, seed + 1), 7000, 14000) * 0.45
    att = np.minimum(1.0, t / 0.0006)
    env = att * np.exp(-t / d)
    return ((metal + sizzle) * env).astype(np.float32)


def shaker(seed: int = 9, dur: float = 0.09) -> np.ndarray:
    n = int(dur * SR)
    x = A.fft_filter(A.noise(n, seed), 4500, 14000) * A.env_ad(n, 0.012, 0.014, 0.03, 1.6)
    return x.astype(np.float32)


def rim(seed: int = 11) -> np.ndarray:
    n = int(0.09 * SR)
    t = _t(0.09)
    x = (A.sine(1720.0, n) * 0.55 + A.sine(520.0, n) * 0.4) * np.exp(-t / 0.012)
    x += A.fft_filter(A.noise(n, seed), 2500, 10000) * np.exp(-t / 0.005) * 0.6
    return x.astype(np.float32)


def tom(f: float = 140.0, decay: float = 0.2, seed: int = 13) -> np.ndarray:
    dur = decay * 3
    n = int(dur * SR)
    t = _t(dur)
    x = A.sine(f * (1 + 0.9 * np.exp(-t / 0.04)), n) * np.exp(-t / decay)
    x += A.fft_filter(A.noise(n, seed), 1200, 7000) * np.exp(-t / 0.01) * 0.3
    return A.softclip(x, 1.4)


def crash(seed: int = 15, decay: float = 1.1) -> np.ndarray:
    dur = decay * 2.2
    n = int(dur * SR)
    t = _t(dur)
    r = A.rng(seed)
    metal = np.zeros(n, np.float32)
    for k in range(9):
        f = r.uniform(340, 1400)
        metal += np.sign(np.sin(2 * np.pi * f * t + r.uniform(0, 6.28))).astype(np.float32)
    x = A.fft_filter(metal / 9, 4000, 18000) * 0.5 + A.fft_filter(A.noise(n, seed + 1), 3000, 17000)
    env = np.minimum(1.0, t / 0.002) * (0.65 * np.exp(-t / decay) + 0.35 * np.exp(-t / 0.08))
    return (x * env).astype(np.float32)


def vinyl(n: int, seed: int = 5, rate: float = 14.0) -> np.ndarray:
    r = A.rng(seed)
    cr = np.zeros(n, np.float32)
    pos = r.integers(0, n, max(1, int(n / SR * rate)))
    cr[pos] = r.uniform(-1, 1, len(pos)) * r.uniform(0.2, 1.0, len(pos))
    hiss = A.fft_filter(A.noise(n, seed + 1), 3000, 12000) * 0.012
    return (A.fft_filter(cr, 1500, 12000) * 0.5 + hiss).astype(np.float32)


# ---------------------------------------------------------------------- tonal voices

def bass(f: float, dur: float, vel: float = 1.0, cutoff: float = 260.0, env_oct: float = 3.0, decay: float = 0.16,
         q: float = 1.2, drive: float = 2.0, wave: str = "saw", sub: float = 0.6, release: float = 0.03,
         sustain: float = 0.85) -> np.ndarray:
    """Subtractive bass: saw/square -> resonant LPF whose cutoff jumps env_oct octaves (by velocity)
    and decays; a sine sub an octave-equal underneath; tanh saturation."""
    n = int((dur + release * 4) * SR)
    t = np.arange(n, dtype=np.float32) / SR
    fc = cutoff * (f / 55.0) ** 0.5 * 2 ** (env_oct * vel * np.exp(-t / decay))
    x = A.osc(f, n, fc, q, wave)
    x += A.sine(f, n) * sub
    env = A.adsr(n, 0.003, decay * 2, sustain, release, gate=dur)
    return A.saturate(x * env * 0.8, drive)


def pluck_saw(f: float, dur: float, vel: float = 1.0, cutoff: float = 500.0, env_oct: float = 4.0,
              decay: float = 0.09, q: float = 1.0, wave: str = "saw", cents: float = 0.0) -> np.ndarray:
    n = int((dur + 0.25) * SR)
    t = np.arange(n, dtype=np.float32) / SR
    fc = cutoff * (f / 220) ** 0.5 * 2 ** (env_oct * (0.6 + 0.4 * vel) * np.exp(-t / decay))
    x = A.osc(f, n, fc, q, wave, cents=cents)
    return x * A.adsr(n, 0.002, dur * 0.35, 0.0, 0.08, gate=dur)


def pluck_ks(f: float, dur: float, vel: float = 1.0, seed: int = 0, rho: float = 0.995) -> np.ndarray:
    x = A.karplus(f, dur + 0.3, rho, 0.25 + 0.6 * vel, seed)
    n = len(x)
    t = np.arange(n) / SR
    rel = np.where(t > dur, np.exp(-(t - dur) / 0.08), 1.0).astype(np.float32)
    return x * rel / (np.abs(x).max() + 1e-9)


def pad(f: float, dur: float, cutoff: float = 2200.0, voices: int = 5, spread: float = 16.0, attack: float = 0.5,
        release: float = 0.6, seed: int = 0, wave: str = "saw", width: float = 0.9) -> np.ndarray:
    n = int((dur + release * 3) * SR)
    t = np.arange(n, dtype=np.float32) / SR
    lfo = cutoff * (1 + 0.18 * np.sin(2 * np.pi * 0.11 * t + seed))
    x = A.supersaw(f, n, lfo, voices, spread, width, 0.75, seed, wave)
    env = A.adsr(n, attack, 1.0, 0.85, release, gate=dur)
    return x * env[:, None]


def rhodes(f: float, dur: float, vel: float = 0.8, seed: int = 0, trem: float = 4.2) -> np.ndarray:
    """FM electric piano: velocity -> modulation index (0.8-2.5), inharmonic tine at ratio 14
    (50 ms), stereo auto-pan tremolo."""
    n = int((dur + 0.6) * SR)
    t = np.arange(n, dtype=np.float32) / SR
    idx = (0.8 + 1.7 * vel) * np.exp(-t / 0.6)
    body = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
    tine = np.sin(2 * np.pi * f * 14 * t) * np.exp(-t / 0.05) * 0.18 * vel
    bark = np.sin(2 * np.pi * f * 2 * t + 0.6 * vel * np.sin(2 * np.pi * f * 7 * t)) * np.exp(-t / 0.25) * 0.2
    env = np.minimum(1.0, t / 0.003) * np.exp(-t / 1.8) * np.where(t > dur, np.exp(-(t - dur) / 0.12), 1.0)
    x = ((body + tine + bark) * env).astype(np.float32)
    p = 0.45 * np.sin(2 * np.pi * trem * t + seed)
    return A.pan(x, p)


def bell(f: float, dur: float = 1.4, vel: float = 0.8) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    x = np.sin(2 * np.pi * f * t + (1.2 + vel) * np.exp(-t / 0.4) * np.sin(2 * np.pi * f * 3.5 * t))
    x += 0.3 * np.sin(2 * np.pi * f * 2.76 * t) * np.exp(-t / 0.2)
    return (x * np.minimum(1.0, t / 0.002) * np.exp(-t / (dur * 0.35))).astype(np.float32)


def lead(f: float, dur: float, vel: float = 0.9, cutoff: float = 1800.0, wave: str = "saw", detune: float = 9.0,
         vib: float = 0.15, seed: int = 0) -> np.ndarray:
    """Three-voice detuned lead with filter envelope and delayed vibrato -> stereo."""
    n = int((dur + 0.2) * SR)
    t = np.arange(n, dtype=np.float32) / SR
    vib_c = 18 * vib * np.clip((t - 0.15) / 0.2, 0, 1) * np.sin(2 * np.pi * 5.2 * t)
    fc = cutoff * 2 ** (1.6 * vel * np.exp(-t / 0.12))
    tabs = A.wavetables(f, wave, 0.9, 2)
    out = np.zeros((n, 2), np.float32)
    for v, (c, p) in enumerate(((-detune, -0.55), (0.0, 0.0), (detune, 0.55))):
        out += A.pan(A.osc(f, n, fc, 0.9, wave, cents=vib_c + c, phase=0.31 * v + seed * 0.1, tables=tabs), p)
    env = A.adsr(n, 0.006, 0.25, 0.7, 0.07, gate=dur)
    return out * env[:, None] / 2.2


def square_seq(f: float, dur: float, vel: float = 0.8, cutoff: float = 900.0, pw: float = 0.3) -> np.ndarray:
    n = int((dur + 0.1) * SR)
    t = np.arange(n, dtype=np.float32) / SR
    fc = cutoff * 2 ** (2.5 * vel * np.exp(-t / 0.05))
    return A.osc(f, n, fc, 1.4, "pulse", pw=pw) * A.adsr(n, 0.002, 0.08, 0.3, 0.03, gate=dur)


# ---------------------------------------------------------------------- fx

def riser(dur: float, seed: int = 21, chord: list[float] | None = None, end_hz: float = 7000.0) -> np.ndarray:
    """Noise + rising unison chord (1 octave, exponential), HPF sweep 200 Hz -> 4 kHz, t^2 ramp.
    The returned clip's last sample is the end of the riser (place its end on the cut)."""
    n = max(16, int(dur * SR))
    t = np.arange(n, dtype=np.float32) / SR
    u = t / max(dur, 1e-3)
    out = np.zeros((n, 2), np.float32)
    for ch in range(2):                                  # decorrelated L/R noise sweeps
        y = A.stft_sweep(A.noise(n, seed + 7 * ch), 300 + (end_hz - 300) * u ** 2.0, q=0.8, mode="band", n_fft=1024)
        out[:, ch] = A.stft_sweep(y, 200 + 3800 * u ** 1.5, mode="high", n_fft=1024)
    if chord:
        cents = 1200 * u ** 2
        for j, f in enumerate(chord[:3]):
            out += A.pan(A.osc(f, n, 1500 + 5000 * u, 0.9, "saw", cents=cents + (j - 1) * 7), (j - 1) * 0.6) * 0.18
    out *= (u ** 2)[:, None]
    f = int(0.002 * SR)
    out[-f:] *= np.linspace(1, 0, f)[:, None]
    return out.astype(np.float32)


def sub_drop(dur: float = 0.9, f0: float = 60.0, f1: float = 30.0) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    f = f1 + (f0 - f1) * np.exp(-t / (dur * 0.35))
    return (A.sine(f, n) * np.minimum(1, t / 0.004) * np.exp(-t / (dur * 0.45))).astype(np.float32)
