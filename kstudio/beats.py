"""Beat-grid estimation from a music file (numpy only, ffmpeg for decoding).

Spectral-flux onset envelope -> autocorrelation tempo (70..190 BPM, octave-folded
toward a preferred range) -> phase search -> per-beat refinement snapping to local
onset peaks. Good enough for steady-tempo music beds; for live/rubato material pass
an explicit BPM + offset in the spec instead.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np

SR = 22050
HOP = 256


@dataclass
class BeatGrid:
    bpm: float
    offset: float
    beat_times: list[float]
    confidence: float

    def as_dict(self) -> dict:
        return {"bpm": round(self.bpm, 3), "offset": round(self.offset, 4),
                "beats": len(self.beat_times), "confidence": round(self.confidence, 3)}


def decode_mono(path: str, sr: int = SR) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def onset_envelope(x: np.ndarray, n_fft: int = 1024, hop: int = HOP) -> np.ndarray:
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    n = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    frames = x[idx] * np.hanning(n_fft)[None, :].astype(np.float32)
    mag = np.log1p(30.0 * np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(0.0, np.diff(mag, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    # remove slow trend, normalise
    k = 16
    trend = np.convolve(flux, np.ones(k) / k, mode="same")
    env = np.maximum(0.0, flux - trend)
    return env / (env.max() + 1e-9)


def estimate(x: np.ndarray, sr: int = SR, lo: float = 70.0, hi: float = 190.0,
             prefer: tuple[float, float] = (85.0, 170.0)) -> BeatGrid:
    env = onset_envelope(x)
    fps = sr / HOP
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(len(ac))
    valid = (lags >= fps * 60 / hi) & (lags <= fps * 60 / lo)
    if not valid.any():
        return BeatGrid(120.0, 0.0, [], 0.0)
    scores = np.where(valid, ac, 0)
    lag = int(np.argmax(scores))
    # parabolic refinement
    if 1 <= lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        den = a - 2 * b + c
        lagf = float(lag) + (0.5 * (a - c) / den if den != 0 else 0)
    else:
        lagf = float(lag)
    bpm = 60 * fps / lagf
    while bpm < prefer[0]:
        bpm *= 2
    while bpm > prefer[1]:
        bpm /= 2
    period = 60 * fps / bpm
    # phase: maximise onset energy sampled on the comb
    best, best_phase = -1.0, 0.0
    for ph in np.linspace(0, period, 64, endpoint=False):
        pos = np.arange(ph, len(env) - 1, period).astype(int)
        s = env[pos].sum()
        if s > best:
            best, best_phase = s, ph
    pos = np.arange(best_phase, len(env) - 1, period)
    # refine each beat to nearest local peak within +-12% of a period
    w = max(1, int(period * 0.12))
    refined = []
    for p in pos:
        i0, i1 = max(0, int(p) - w), min(len(env), int(p) + w + 1)
        seg = env[i0:i1]
        j = i0 + int(np.argmax(seg)) if seg.size and seg.max() > 0.15 else int(round(p))
        refined.append(0.7 * j + 0.3 * p)  # keep it mostly grid-like
    times = [r / fps for r in refined]
    conf = float(best / (len(pos) + 1e-9))
    return BeatGrid(float(bpm), float(best_phase / fps), times, conf)


def analyse_file(path: str) -> BeatGrid:
    return estimate(decode_mono(path))
