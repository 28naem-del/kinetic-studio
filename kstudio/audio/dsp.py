"""Small NumPy DSP toolbox (48 kHz float32): envelopes, FFT filters, STFT sweeps,
oscillators, FFT-convolution reverb, panning, WAV I/O."""
from __future__ import annotations

import subprocess
import wave

import numpy as np

SR = 48000


def tvec(dur: float) -> np.ndarray:
    return np.arange(int(round(dur * SR)), dtype=np.float32) / SR


def rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def noise(n: int, seed: int = 0) -> np.ndarray:
    return rng(seed).uniform(-1, 1, n).astype(np.float32)


def pink(n: int, seed: int = 0) -> np.ndarray:
    w = np.fft.rfft(rng(seed).normal(0, 1, n))
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = f[1] if n > 1 else 1
    x = np.fft.irfft(w / np.sqrt(f), n).astype(np.float32)
    return x / (np.abs(x).max() + 1e-9)


def env_exp(n: int, decay: float, attack: float = 0.002) -> np.ndarray:
    t = np.arange(n, dtype=np.float32) / SR
    a = np.clip(t / max(attack, 1e-5), 0, 1)
    return a * np.exp(-t / max(decay, 1e-5))


def env_ad(n: int, attack: float, peak_at: float | None = None, release: float = 0.1, curve: float = 2.0) -> np.ndarray:
    """Rise (curved) to a peak, then exponential release."""
    t = np.arange(n, dtype=np.float32) / SR
    pk = peak_at if peak_at is not None else attack
    up = np.clip(t / max(pk, 1e-5), 0, 1) ** curve
    down = np.exp(-np.clip(t - pk, 0, None) / max(release, 1e-5))
    return (up * down).astype(np.float32)


def fft_filter(x: np.ndarray, lo: float = 0.0, hi: float = SR / 2, slope: float = 2.0) -> np.ndarray:
    """Zero-phase band filter with soft (Butterworth-like) shoulders."""
    n = len(x)
    if n < 4:
        return x
    X = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(n, 1 / SR).astype(np.float32)
    g = np.ones_like(f)
    if lo > 0:
        g *= 1 / np.sqrt(1 + (lo / np.maximum(f, 1e-3)) ** (2 * slope))
    if hi < SR / 2:
        g *= 1 / np.sqrt(1 + (f / hi) ** (2 * slope))
    if X.ndim == 2:
        g = g[:, None]
    return np.fft.irfft(X * g, n, axis=0).astype(np.float32)


def stft_sweep(x: np.ndarray, centre: np.ndarray, q: float = 1.2, mode: str = "band", n_fft: int = 1024) -> np.ndarray:
    """Time-varying filter. `centre` is a per-sample cutoff/centre curve (Hz).
    mode: band (bandpass around centre), low (lowpass at centre), high."""
    hop = n_fft // 4
    win = np.hanning(n_fft).astype(np.float32)
    n = len(x)
    pad = np.concatenate([np.zeros(n_fft, np.float32), x.astype(np.float32), np.zeros(n_fft, np.float32)])
    out = np.zeros_like(pad)
    norm = np.zeros_like(pad)
    f = np.fft.rfftfreq(n_fft, 1 / SR).astype(np.float32)
    for s in range(0, len(pad) - n_fft, hop):
        mid = min(max(s + n_fft // 2 - n_fft, 0), n - 1)
        fc = float(centre[mid])
        X = np.fft.rfft(pad[s:s + n_fft] * win)
        if mode == "band":
            g = np.exp(-0.5 * (np.log2(np.maximum(f, 1.0) / fc) * q * 1.6) ** 2)
        elif mode == "low":
            g = 1 / np.sqrt(1 + (f / fc) ** 4)
        else:
            g = 1 / np.sqrt(1 + (fc / np.maximum(f, 1.0)) ** 4)
        out[s:s + n_fft] += np.fft.irfft(X * g, n_fft).astype(np.float32) * win
        norm[s:s + n_fft] += win ** 2
    out /= np.maximum(norm, 1e-3)
    return out[n_fft:n_fft + n]


def sine(freq: np.ndarray | float, n: int, phase: float = 0.0) -> np.ndarray:
    if np.isscalar(freq):
        return np.sin(2 * np.pi * float(freq) * np.arange(n, dtype=np.float64) / SR + phase).astype(np.float32)
    ph = 2 * np.pi * np.cumsum(np.asarray(freq, np.float64)) / SR + phase
    return np.sin(ph).astype(np.float32)


def additive(freq: float, n: int, harmonics: int = 8, tilt: float = 1.0, bright_decay: float = 0.0,
             detune: float = 0.0, seed: int = 0, odd_only: bool = False) -> np.ndarray:
    """Band-limited saw/square-ish tone; bright_decay > 0 makes upper partials die faster (pluck)."""
    t = np.arange(n, dtype=np.float32) / SR
    out = np.zeros(n, np.float32)
    r = rng(seed)
    for k in range(1, harmonics + 1):
        if odd_only and k % 2 == 0:
            continue
        fk = freq * k * (1 + detune * r.uniform(-1, 1))
        if fk >= SR / 2 - 500:
            break
        amp = 1.0 / k ** tilt
        if bright_decay > 0:
            amp = amp * np.exp(-t * bright_decay * (k - 1))
        out += amp * np.sin(2 * np.pi * fk * t + r.uniform(0, 6.28)).astype(np.float32)
    return out


def fm(freq: float, n: int, ratio: float = 1.0, index: float = 2.0, index_decay: float = 1.5) -> np.ndarray:
    t = np.arange(n, dtype=np.float32) / SR
    idx = index * np.exp(-t * index_decay)
    return np.sin(2 * np.pi * freq * t + idx * np.sin(2 * np.pi * freq * ratio * t)).astype(np.float32)


def reverb_ir(length: float = 1.6, decay: float = 0.45, seed: int = 7, damp: float = 6000) -> np.ndarray:
    n = int(length * SR)
    t = np.arange(n, dtype=np.float32) / SR
    ir = np.stack([noise(n, seed), noise(n, seed + 1)], 1) * np.exp(-t / decay)[:, None]
    ir = fft_filter(ir, 120, damp)
    ir[: int(0.012 * SR)] *= np.linspace(0, 1, int(0.012 * SR))[:, None]
    return (ir / np.sqrt((ir ** 2).sum(0, keepdims=True) + 1e-9)).astype(np.float32)


def convolve_reverb(x: np.ndarray, ir: np.ndarray, wet: float = 0.25) -> np.ndarray:
    """Stereo FFT convolution, output length len(x)+len(ir)."""
    if x.ndim == 1:
        x = np.stack([x, x], 1)
    n = len(x) + len(ir) - 1
    nf = 1 << (n - 1).bit_length()
    X = np.fft.rfft(x, nf, axis=0)
    H = np.fft.rfft(ir, nf, axis=0)
    y = np.fft.irfft(X * H, nf, axis=0)[:n].astype(np.float32)
    dry = np.zeros_like(y)
    dry[: len(x)] = x
    return dry * (1 - wet * 0.5) + y * wet


def pan(x: np.ndarray, p: float | np.ndarray = 0.0) -> np.ndarray:
    """Equal-power pan, p in [-1, 1] (scalar or per-sample)."""
    if x.ndim == 2:
        x = x.mean(1)
    a = (np.asarray(p, np.float32) + 1) * np.pi / 4
    return np.stack([x * np.cos(a), x * np.sin(a)], 1).astype(np.float32)


def stereo(x: np.ndarray) -> np.ndarray:
    return x if x.ndim == 2 else np.stack([x, x], 1)


def db(g: float) -> float:
    return float(10 ** (g / 20))


def add_at(buf: np.ndarray, clip: np.ndarray, start: int, gain: float = 1.0) -> None:
    if start >= len(buf) or len(clip) == 0:
        return
    if start < 0:
        clip = clip[-start:]
        start = 0
    end = min(len(buf), start + len(clip))
    buf[start:end] += stereo(clip)[: end - start] * gain


def write_wav(path: str, x: np.ndarray, sr: int = SR, seed: int = 0) -> None:
    x = stereo(x)
    d = (rng(seed).uniform(-0.5, 0.5, x.shape) + rng(seed + 1).uniform(-0.5, 0.5, x.shape)) / 32768.0
    pcm = np.clip((x + d) * 32767.0, -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def read_audio(path: str, sr: int = SR) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"cannot decode {path}: {r.stderr.decode(errors='replace')[-400:]}")
    return np.frombuffer(r.stdout, np.float32).reshape(-1, 2).copy()


def softclip(x: np.ndarray, drive: float = 1.0) -> np.ndarray:
    return (np.tanh(x * drive) / np.tanh(drive)).astype(np.float32)


def note_hz(semitones_from_a4: float) -> float:
    return 440.0 * 2 ** (semitones_from_a4 / 12)
