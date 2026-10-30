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


# ====================================================================== v0.2 synthesis toolbox
# Everything below is vectorised NumPy (no per-sample Python loops on long signals).

_WT_N = 2048
_WT_LEVELS = np.geomspace(40.0, 22000.0, 24).astype(np.float64)


def _lp_mag(r: np.ndarray, q: float, poles: int) -> np.ndarray:
    """Magnitude of a resonant low-pass at normalised frequency r = f / fc (2 or 4 poles)."""
    h2 = 1.0 / np.sqrt((1 - r ** 2) ** 2 + (r / max(q, 0.3)) ** 2)
    if poles >= 4:
        return h2 / np.sqrt(1 + r ** 4) * min(1.0, 1.0 / max(q, 0.707) ** 0.5)
    return h2


def _wave_amps(wave: str, k: np.ndarray, pw: float = 0.5) -> np.ndarray:
    if wave == "square":
        return np.where(k % 2 == 1, 1.0 / k, 0.0)
    if wave == "pulse":
        return np.abs(np.sin(np.pi * k * pw)) * 2 / (np.pi * k) * np.pi / 2
    if wave == "tri":
        return np.where(k % 2 == 1, 1.0 / k ** 2, 0.0)
    return 1.0 / k                                            # saw


def wavetables(f0: float, wave: str = "saw", q: float = 0.8, poles: int = 2, pw: float = 0.5) -> np.ndarray:
    """Band-limited single-cycle tables of `wave` at pitch f0, one per cutoff level in _WT_LEVELS
    (rows), each already passed through the resonant low-pass magnitude at that cutoff."""
    kmax = max(1, min(_WT_N // 2 - 1, int((SR / 2 - 300) / max(f0, 1.0))))
    k = np.arange(1, kmax + 1, dtype=np.float64)
    base = _wave_amps(wave, k, pw)
    spec = np.zeros((len(_WT_LEVELS), _WT_N // 2 + 1), np.complex128)
    for i, fc in enumerate(_WT_LEVELS):
        a = base * _lp_mag(k * f0 / fc, q, poles)
        spec[i, 1:kmax + 1] = -1j * a * (_WT_N / 2)
    tabs = np.fft.irfft(spec, _WT_N, axis=1)
    return np.concatenate([tabs, tabs[:, :1]], 1).astype(np.float32)   # guard sample for interpolation


def osc(f0: float, n: int, cutoff: float | np.ndarray = 20000.0, q: float = 0.8, wave: str = "saw",
        poles: int = 2, cents: float | np.ndarray = 0.0, phase: float = 0.0, pw: float = 0.5,
        tables: np.ndarray | None = None) -> np.ndarray:
    """Band-limited oscillator through a time-varying resonant low-pass (filter envelope).
    `cutoff` and `cents` (pitch offset) may be per-sample arrays. Implemented as a 2-D
    wavetable lookup (phase x cutoff level), so cost is O(n) whatever the harmonic count."""
    tabs = wavetables(f0, wave, q, poles, pw) if tables is None else tables
    ratio = 2.0 ** (np.asarray(cents, np.float64) / 1200.0)
    inc = (f0 * ratio / SR) * np.ones(n)
    ph = (np.cumsum(inc) - inc[0] + phase) % 1.0
    pos = ph * _WT_N
    i0 = pos.astype(np.int64)
    fr = (pos - i0).astype(np.float32)
    lv = np.interp(np.log(np.clip(np.asarray(cutoff, np.float64) * np.ones(n), _WT_LEVELS[0], _WT_LEVELS[-1])),
                   np.log(_WT_LEVELS), np.arange(len(_WT_LEVELS)))
    l0 = np.minimum(lv.astype(np.int64), len(_WT_LEVELS) - 2)
    lf = (lv - l0).astype(np.float32)
    a = tabs[l0, i0] * (1 - fr) + tabs[l0, i0 + 1] * fr
    b = tabs[l0 + 1, i0] * (1 - fr) + tabs[l0 + 1, i0 + 1] * fr
    return (a * (1 - lf) + b * lf).astype(np.float32)


def adsr(n: int, a: float, d: float, s: float, r: float, gate: float | None = None) -> np.ndarray:
    """Linear attack, exponential decay to sustain level s, exponential release after `gate` s."""
    t = np.arange(n, dtype=np.float32) / SR
    g = gate if gate is not None else n / SR
    env = np.where(t < a, t / max(a, 1e-5), s + (1 - s) * np.exp(-(t - a) / max(d, 1e-5)))
    lvl_g = float(s + (1 - s) * np.exp(-(max(g - a, 0)) / max(d, 1e-5))) if g >= a else g / max(a, 1e-5)
    env = np.where(t > g, lvl_g * np.exp(-(t - g) / max(r, 1e-5)), env)
    return env.astype(np.float32)


def supersaw(f0: float, n: int, cutoff: float | np.ndarray = 4000.0, voices: int = 5, spread: float = 18.0,
             width: float = 0.85, q: float = 0.7, seed: int = 0, wave: str = "saw") -> np.ndarray:
    """Unison stack: `voices` detuned saws (outer voices widest, +-spread cents), random phases,
    spread across the stereo field (equal-power) -> stereo (n, 2)."""
    r = rng(seed)
    tabs = wavetables(f0, wave, q, 2)
    out = np.zeros((n, 2), np.float32)
    pos = np.linspace(-1, 1, voices) if voices > 1 else np.zeros(1)
    for v, p in enumerate(pos):
        det = spread * np.sign(p) * abs(p) ** 1.3 + r.uniform(-1.5, 1.5)
        x = osc(f0, n, cutoff, q, wave, cents=det, phase=float(r.uniform()), tables=tabs)
        out += pan(x, float(p * width))
    return (out / np.sqrt(voices)).astype(np.float32)


def karplus(f0: float, dur: float, rho: float = 0.996, bright: float = 0.6, seed: int = 0) -> np.ndarray:
    """Karplus-Strong plucked string, block-vectorised (block = one period), pitch-corrected by
    resampling, so tuning is exact. bright 0..1 low-passes the excitation."""
    n = int(dur * SR)
    N = max(2, int(SR / f0))
    exc = noise(N, seed).astype(np.float64)
    exc -= exc.mean()
    if bright < 1:
        cut = f0 * (1.5 + 24 * max(bright, 0.0) ** 1.5)
        exc = np.real(np.fft.ifft(np.fft.fft(exc) * (1 / (1 + (np.abs(np.fft.fftfreq(N)) * SR / cut) ** 2))))
    m = int(n * (N + 0.5) * f0 / SR) + N + 2
    y = np.zeros(m + N)
    y[:N] = exc
    prev_last = 0.0
    for s in range(N, m, N):
        blk = y[s - N:s]
        shifted = np.concatenate([[prev_last], blk[:-1]])
        e = min(s + N, len(y))
        y[s:e] = (rho * 0.5 * (blk + shifted))[: e - s]
        prev_last = blk[-1]
    # resample so the true period is SR/f0
    pos = np.arange(n) * ((N + 0.5) * f0 / SR)
    out = np.interp(pos, np.arange(len(y)), y)
    out -= np.convolve(out, np.ones(N) / N, mode="same")       # remove residual DC drift
    return out.astype(np.float32)


def biquad_mag(f: np.ndarray, kind: str, f0: float, q: float = 0.707, gain_db: float = 0.0) -> np.ndarray:
    """|H(f)| of the RBJ cookbook biquads (lowpass, highpass, bandpass, peak, lowshelf, highshelf)."""
    w0 = 2 * np.pi * min(f0, SR / 2 - 10) / SR
    A_ = 10 ** (gain_db / 40)
    al = np.sin(w0) / (2 * q)
    c = np.cos(w0)
    if kind == "lowpass":
        b = [(1 - c) / 2, 1 - c, (1 - c) / 2]; a = [1 + al, -2 * c, 1 - al]
    elif kind == "highpass":
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]; a = [1 + al, -2 * c, 1 - al]
    elif kind == "bandpass":
        b = [al, 0, -al]; a = [1 + al, -2 * c, 1 - al]
    elif kind == "peak":
        b = [1 + al * A_, -2 * c, 1 - al * A_]; a = [1 + al / A_, -2 * c, 1 - al / A_]
    elif kind in ("lowshelf", "highshelf"):
        sq = 2 * np.sqrt(A_) * al
        if kind == "lowshelf":
            b = [A_ * ((A_ + 1) - (A_ - 1) * c + sq), 2 * A_ * ((A_ - 1) - (A_ + 1) * c), A_ * ((A_ + 1) - (A_ - 1) * c - sq)]
            a = [(A_ + 1) + (A_ - 1) * c + sq, -2 * ((A_ - 1) + (A_ + 1) * c), (A_ + 1) + (A_ - 1) * c - sq]
        else:
            b = [A_ * ((A_ + 1) + (A_ - 1) * c + sq), -2 * A_ * ((A_ - 1) + (A_ + 1) * c), A_ * ((A_ + 1) + (A_ - 1) * c - sq)]
            a = [(A_ + 1) - (A_ - 1) * c + sq, 2 * ((A_ - 1) - (A_ + 1) * c), (A_ + 1) - (A_ - 1) * c - sq]
    else:
        raise ValueError(kind)
    z = np.exp(-1j * 2 * np.pi * np.asarray(f, np.float64) / SR)
    return np.abs((b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z))


def eq(x: np.ndarray, bands: list[tuple]) -> np.ndarray:
    """Zero-phase FFT EQ from biquad magnitudes: bands = [(kind, f0, q, gain_db), ...]."""
    n = len(x)
    if n < 8 or not bands:
        return x
    X = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(n, 1 / SR)
    g = np.ones_like(f)
    for bd in bands:
        kind, f0 = bd[0], bd[1]
        q = bd[2] if len(bd) > 2 else 0.707
        gd = bd[3] if len(bd) > 3 else 0.0
        g = g * biquad_mag(f, kind, f0, q, gd)
    if X.ndim == 2:
        g = g[:, None]
    return np.fft.irfft(X * g, n, axis=0).astype(np.float32)


def fft_convolve(x: np.ndarray, ir: np.ndarray) -> np.ndarray:
    """Full linear convolution (stereo-aware), output length len(x) + len(ir) - 1."""
    x2, ir2 = stereo(x), stereo(ir)
    n = len(x2) + len(ir2) - 1
    nf = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x2, nf, axis=0) * np.fft.rfft(ir2, nf, axis=0), nf, axis=0)[:n]
    return y.astype(np.float32)


def room_ir(rt60: float = 1.8, predelay: float = 0.02, damp: float = 5000.0, seed: int = 7,
            width: float = 1.0, hp: float = 300.0) -> np.ndarray:
    """Stereo reverb impulse response: decorrelated noise with a pre-delay, an RT60 envelope that is
    shorter at high frequencies (frequency-dependent damping) and a high-passed return. Unit energy."""
    n = int((rt60 * 1.1 + predelay) * SR)
    pd = int(predelay * SR)
    t = np.arange(n - pd, dtype=np.float32) / SR
    lo_env = np.exp(-6.9 * t / rt60)
    hi_env = np.exp(-6.9 * t / (rt60 * 0.45))
    ir = np.zeros((n, 2), np.float32)
    for ch in range(2):
        w = noise(n - pd, seed + ch * 13)
        lo = fft_filter(w, 0, damp) * lo_env
        hi = fft_filter(w, damp, SR / 2 - 1) * hi_env
        ir[pd:, ch] = lo + hi
    if width < 1:
        m = ir.mean(1, keepdims=True)
        ir = m + (ir - m) * width
    att = int(0.006 * SR)
    ir[pd:pd + att] *= np.linspace(0, 1, att)[:, None]
    ir = eq(ir, [("highpass", hp, 0.6)])
    return (ir / np.sqrt((ir ** 2).sum() / 2 + 1e-9)).astype(np.float32)


def delay_ir(time_s: float, feedback: float = 0.35, taps: int = 6, pingpong: bool = True,
             damp: float = 4000.0) -> np.ndarray:
    """Impulse response of a (ping-pong) feedback delay with a low-pass in the loop: tap k at
    k*time with gain feedback^(k-1), alternating L/R, progressively darker. Wet only."""
    d = int(time_s * SR)
    n = d * taps + 1
    ir = np.zeros((n, 2), np.float32)
    for k in range(1, taps + 1):
        ch = (k - 1) % 2 if pingpong else None
        g = feedback ** (k - 1)
        if ch is None:
            ir[k * d, :] = g
        else:
            ir[k * d, ch] = g
    # loop damping: each repeat passes the low-pass once more -> approximate with a per-tap blur
    out = np.zeros_like(ir)
    for k in range(1, taps + 1):
        seg = np.zeros((n, 2), np.float32)
        seg[k * d] = ir[k * d]
        out += fft_filter(seg, 120, max(800.0, damp / (1 + 0.35 * (k - 1))), 1.0)
    return out


def haas(x: np.ndarray, ms: float = 12.0, side: int = 1, mix: float = 1.0) -> np.ndarray:
    """Haas widening: delay one channel by `ms` (precedence effect)."""
    y = stereo(x).copy()
    d = int(ms * SR / 1000)
    if d <= 0:
        return y
    ch = 1 if side > 0 else 0
    sh = np.concatenate([np.zeros(d, np.float32), y[:-d, ch]])
    y[:, ch] = y[:, ch] * (1 - mix) + sh * mix
    return y


def chorus(x: np.ndarray, depth_ms: float = 2.0, base_ms: float = 11.0, rate: float = 0.5, mix: float = 0.4,
           seed: int = 0) -> np.ndarray:
    """Two-voice stereo chorus (modulated fractional delay via interpolation; LFOs in quadrature)."""
    y = stereo(x).astype(np.float32)
    n = len(y)
    t = np.arange(n) / SR
    out = y * (1 - mix * 0.5)
    for ch, ph in ((0, 0.0), (1, np.pi / 2)):
        dl = (base_ms + depth_ms * np.sin(2 * np.pi * rate * t + ph + seed)) * SR / 1000
        src = np.arange(n) - dl
        out[:, ch] += np.interp(src, np.arange(n), y[:, ch], left=0.0) * mix
    return out.astype(np.float32)


def width(x: np.ndarray, amount: float = 1.0) -> np.ndarray:
    """Mid/side width: 0 = mono, 1 = unchanged, >1 wider."""
    y = stereo(x)
    m = (y[:, 0] + y[:, 1]) / 2
    s = (y[:, 0] - y[:, 1]) / 2 * amount
    return np.stack([m + s, m - s], 1).astype(np.float32)


def saturate(x: np.ndarray, drive: float = 1.5, mix: float = 1.0) -> np.ndarray:
    """tanh(drive*x)/tanh(drive) waveshaper with dry/wet."""
    return (x * (1 - mix) + softclip(x, drive) * mix).astype(np.float32)


def resample(x: np.ndarray, ratio: float) -> np.ndarray:
    """Varispeed by `ratio` (>1 = higher and shorter), linear interpolation, stereo-aware."""
    if abs(ratio - 1.0) < 1e-6:
        return x
    n_out = max(1, int(len(x) / ratio))
    pos = np.arange(n_out) * ratio
    if x.ndim == 1:
        return np.interp(pos, np.arange(len(x)), x).astype(np.float32)
    return np.stack([np.interp(pos, np.arange(len(x)), x[:, c]) for c in range(x.shape[1])], 1).astype(np.float32)


# ---------------------------------------------------------------------- metering

def _tp_kernel(factor: int = 4, taps: int = 48) -> np.ndarray:
    """Polyphase windowed-sinc interpolation kernels, one row per fractional phase 1..factor-1."""
    k = np.arange(-taps // 2 + 1, taps // 2 + 1)
    rows = []
    for p in range(1, factor):
        d = k - p / factor
        h = np.sinc(d) * np.kaiser(len(k), 8.0)
        rows.append(h / h.sum())
    return np.asarray(rows, np.float64)


def true_peak_env(x: np.ndarray, factor: int = 4) -> np.ndarray:
    """Per-sample true-peak envelope (max |x| over the original and `factor`x oversampled points
    between sample i and i+1, across channels), as in ITU-R BS.1770 annex 2."""
    y = stereo(x).astype(np.float64)
    env = np.abs(y).max(1)
    ker = _tp_kernel(factor)
    half = ker.shape[1] // 2
    for h in ker:
        for c in range(y.shape[1]):
            v = np.convolve(y[:, c], h[::-1], mode="full")[half - 1: half - 1 + len(y)]
            env = np.maximum(env, np.abs(v))
    return env.astype(np.float32)


def true_peak_db(x: np.ndarray, factor: int = 4) -> float:
    pk = float(true_peak_env(x, factor).max()) if len(x) else 0.0
    return 20 * np.log10(pk) if pk > 0 else -120.0


def lufs(x: np.ndarray) -> float:
    """Integrated loudness (ITU-R BS.1770-4: K-weighting, 400 ms blocks with 75 % overlap,
    absolute -70 LUFS and relative -10 LU gates). K-weighting applied as its exact magnitude
    response in the frequency domain."""
    y = stereo(x).astype(np.float64)
    n = len(y)
    if n < int(0.4 * SR):
        return -70.0
    f = np.fft.rfftfreq(n, 1 / SR)
    z1 = np.exp(-1j * 2 * np.pi * f / SR)
    g = (np.abs(np.polyval([1.19839281085285, -2.69169618940638, 1.53512485958697], z1)
                / np.polyval([0.73248077421585, -1.69065929318241, 1.0], z1))
         * np.abs(np.polyval([1.0, -2.0, 1.0], z1) / np.polyval([0.99007225036621, -1.99004745483398, 1.0], z1)))
    yk = np.fft.irfft(np.fft.rfft(y, axis=0) * g[:, None], n, axis=0)
    blk, hop = int(0.4 * SR), int(0.1 * SR)
    p = (yk ** 2).sum(1)
    c = np.concatenate([[0.0], np.cumsum(p)])
    starts = np.arange(0, n - blk + 1, hop)
    z = (c[starts + blk] - c[starts]) / blk
    ld = -0.691 + 10 * np.log10(z + 1e-12)
    z = z[ld > -70]
    if not len(z):
        return -70.0
    rel = -0.691 + 10 * np.log10(z.mean()) - 10
    z2 = z[(-0.691 + 10 * np.log10(z + 1e-12)) > rel]
    return float(-0.691 + 10 * np.log10(z2.mean())) if len(z2) else -70.0
