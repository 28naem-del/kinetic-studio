"""Soundtrack mixer: music bed (procedural or user file) + SFX at the film's markers,
ducking under hits, then loudness normalisation to a target (default -14 LUFS integrated)
with a look-ahead limiter holding true peak under the ceiling (default -1.3 dBTP: the -1 dBTP
delivery spec with 0.3 dB of margin for the AAC encode and meter disagreement)."""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

import cv2
import numpy as np

from . import dsp as A
from . import music as M
from . import synth
from .dsp import SR

DUCK_SOUNDS = {"impact", "impact_big", "logo_hit", "hit", "sub_drop"}
TP_MARGIN = 0.6          # dB the limiter stays under the delivery ceiling (AAC encode overshoot headroom)


def _ebur128_summary(stderr: str) -> dict[str, float]:
    tail = stderr[stderr.rfind("Summary:"):] if "Summary:" in stderr else stderr
    out: dict[str, float] = {}
    for key, pat in (("lufs", r"I:\s+(-?[\d.]+|-inf)\s+LUFS"), ("lra", r"LRA:\s+(-?[\d.]+)\s+LU\b"),
                     ("true_peak", r"True peak:\s+Peak:\s+(-?[\d.]+|-inf)\s+dBFS")):
        m = re.search(pat, tail, re.S)
        if m:
            out[key] = float(m.group(1)) if m.group(1) != "-inf" else -120.0
    return out


def measure(path: str) -> dict[str, Any]:
    """Integrated loudness, true peak and LRA of a file's first audio stream (any container: WAV, or the
    AAC track of the final MP4), in one ffmpeg pass through both EBU R128 meters.

    lufs / lra come from the `ebur128` filter (BS.1770-4). `true_peak` is the stricter of ebur128's
    (printed at 0.1 dB) and loudnorm's (0.01 dB) 4x-oversampled true peak, so a rounding step can never
    hide an over. The individual readings are returned too."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-map", "0:a:0", "-af",
                        "ebur128=peak=true,loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    eb = _ebur128_summary(r.stderr)
    if not m and "lufs" not in eb:
        raise RuntimeError(f"loudness measurement failed: {r.stderr[-400:]}")
    ln = json.loads(m.group(0)) if m else {}

    def f(v: Any) -> float:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return -120.0
        return x if np.isfinite(x) else -120.0

    ln_tp, ln_i = f(ln.get("input_tp")), f(ln.get("input_i"))
    tp = max(eb.get("true_peak", ln_tp), ln_tp) if ln else eb["true_peak"]
    return {"lufs": eb.get("lufs", ln_i), "true_peak": round(tp, 2), "lra": eb.get("lra", f(ln.get("input_lra"))),
            "tp_ebur128": eb.get("true_peak"), "tp_loudnorm": ln_tp if ln else None,
            "lufs_loudnorm": ln_i if ln else None, "meter": "ebur128"}


def _release(g: np.ndarray, release: float) -> np.ndarray:
    """Instant attack, one-pole release (gain falls immediately, recovers with time constant `release`)."""
    a = float(np.exp(-1.0 / (max(release, 1e-4) * SR)))
    out = np.empty_like(g)
    prev = 1.0
    gl = g.tolist()
    for i, v in enumerate(gl):
        prev = v if v < prev else prev * a + v * (1 - a)
        out[i] = prev
    return out


def limiter(x: np.ndarray, ceiling_db: float = -1.5, lookahead: float = 0.005, release: float = 0.06,
            true_peak: bool = True) -> np.ndarray:
    """Look-ahead true-peak brickwall.

    Detection uses the 4x oversampled (BS.1770 annex 2) peak envelope, so inter-sample overs count.
    gain = min(1, ceil/peak) is min-filtered over the look-ahead window, smoothed and given a
    one-pole release, so the gain is already down when the peak arrives (no clipping, no clicks).
    Any residual over (smoothing never fully reaches a single-sample dip) is caught by a second pass,
    and a final static trim guarantees the output true peak never exceeds the ceiling."""
    ceil = 10 ** (ceiling_db / 20)
    x = np.asarray(x, np.float32)
    mono = x.ndim == 1
    y = A.stereo(x).astype(np.float32)
    k = max(3, int(lookahead * SR) | 1)

    def env_of(sig: np.ndarray) -> np.ndarray:
        e = np.abs(sig).max(axis=1)
        if true_peak and len(sig) > 64:
            e = np.maximum(e, A.true_peak_env(sig))
        return e.astype(np.float32)

    target = ceil * 0.995
    for _ in range(3):
        env = env_of(y)
        if not len(env) or float(env.max()) <= ceil:
            break
        g = np.minimum(1.0, target / np.maximum(env, 1e-9)).astype(np.float32)
        g = cv2.erode(g.reshape(1, -1), np.ones((1, 2 * k + 1), np.uint8)).ravel().astype(np.float32)
        g = cv2.blur(g.reshape(1, -1), (k, 1)).ravel().astype(np.float32)
        g = _release(g, release)
        y = (y * g[:, None]).astype(np.float32)
    if len(y):
        pk = float(env_of(y).max())
        if pk > ceil:
            y = (y * (target / pk)).astype(np.float32)
    y = np.clip(y, -ceil, ceil)
    return y[:, 0].copy() if mono else y


def duck_curve(n: int, times: list[tuple[float, float]], depth_db: float = -5.0, attack: float = 0.006,
               hold: float = 0.06, release: float = 0.35) -> np.ndarray:
    g = np.ones(n, np.float32)
    for t, strength in times:
        s = int(t * SR)
        a, h, r = int(attack * SR), int(hold * SR), int(release * SR)
        depth = 1 - 10 ** (depth_db * strength / 20)
        seg = np.concatenate([np.linspace(0, 1, a, endpoint=False), np.ones(h), np.linspace(1, 0, r) ** 1.5])
        lo, hi = max(0, s - a), min(n, s - a + len(seg))
        if hi > lo:
            g[lo:hi] = np.minimum(g[lo:hi], 1 - depth * seg[lo - (s - a): hi - (s - a)])
    return g


def build_bed(film: Any, seed: int = 0) -> tuple[np.ndarray, dict]:
    cfg = film.cfg.get("music", {})
    n = int(np.ceil(film.duration * SR))
    stop = film.tl.named("music_stop")
    stop_at = stop.t if stop else None
    info: dict[str, Any] = {}
    f = cfg.get("file")
    if f:
        path = f if os.path.isabs(f) else os.path.join(film.dir, f)
        x = A.read_audio(path)
        start = int(float(cfg.get("start", 0.0)) * SR)
        x = x[start:start + n]
        if len(x) < n:
            x = np.concatenate([x, np.zeros((n - len(x), 2), np.float32)])
        fade = int(min(1.2, film.duration / 4) * SR)
        x[-fade:] *= np.linspace(1, 0, fade)[:, None]
        if stop_at is not None:
            k = int(stop_at * SR)
            x[k:k + int(0.04 * SR)] *= np.linspace(1, 0, len(x[k:k + int(0.04 * SR)]))[:, None]
            x[k + int(0.04 * SR):] = 0
        info = {"source": "file", "file": path}
    elif cfg.get("style", "drive") != "none":
        style = cfg.get("style", "drive")
        if style not in M.STYLES:
            raise ValueError(f"unknown music style {style!r}; choose from {sorted(M.STYLES)} or 'none'")
        x = M.generate(style, film.tl, film.duration, seed=seed, stop_at=stop_at)
        info = {"source": "procedural", "style": style, "bpm": film.tl.bpm}
    else:
        x = np.zeros((n, 2), np.float32)
        info = {"source": "none"}
    return x[:n], info


UI_PITCH_SEMI = 0.5      # +- semitone pitch randomisation on short UI sounds
VEL_JITTER = 0.06        # +- velocity randomisation per hit
SNAP_TOL = 0.04          # anchors within this many seconds of the 16th grid are pulled onto it


def sfx_velocity(energy: float) -> float:
    """Section energy (0..3) -> SFX velocity: quiet intros get softer, darker hits."""
    return float(np.clip(0.7 + 0.1 * energy, 0.5, 1.0))


def _grid_snap(tl: Any, t: float, div: float) -> float:
    if hasattr(tl, "snap"):
        return float(tl.snap(t, div))
    spb = 60.0 / float(getattr(tl, "bpm", 120.0) or 120.0)
    return round(t / (spb / div)) * (spb / div)


def place_sfx(film: Any, seed: int = 0) -> list[dict[str, Any]]:
    """Resolve every sfx marker to a concrete clip placement.

    anchor  = marker time, pulled onto the 16th-note grid when within SNAP_TOL (marker `quantize`
              False, or music cfg `sfx_quantize` False, disables; `grid` sets the division per beat)
    align   = "start" (default; clip onset on the anchor), "peak" (the sound's PEAK lands on it) or
              "end" (the clip ends on it, for risers/reverses into a cut)
    velocity= marker `velocity`, else from the section energy (sfx_velocity) with a small jitter
    pitch   = marker `pitch` (semitones), else a small random offset for UI sounds."""
    mcfg = film.cfg.get("music", {})
    tl = film.tl
    rng = np.random.default_rng(1009 + seed)
    out: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for m in tl.of("sfx"):
        name = m.data.get("sound") or m.name
        if not name or name not in synth.KIT:
            continue
        k = counts.get(name, 0)
        counts[name] = k + 1
        jit_v, jit_p = float(rng.uniform(-1, 1)), float(rng.uniform(-1, 1))
        anchor = float(m.t)
        qz = m.data.get("quantize", mcfg.get("sfx_quantize", True))
        div = float(m.data.get("grid", mcfg.get("sfx_grid", 4)))
        if qz and getattr(tl, "bpm", None):
            g = _grid_snap(tl, anchor, div)
            if abs(g - anchor) <= SNAP_TOL:
                anchor = g
        if "velocity" in m.data:
            vel = float(m.data["velocity"])
        else:
            vel = sfx_velocity(tl.energy_at(anchor) if hasattr(tl, "energy_at") else 2.0) + VEL_JITTER * jit_v
        vel = round(float(np.clip(vel, 0.3, 1.2)), 2)
        if "pitch" in m.data:
            pitch = float(m.data["pitch"])
        else:
            pitch = UI_PITCH_SEMI * jit_p if name in synth.UI_SOUNDS else 0.0
        pitch = round(pitch, 2)
        clip = synth.render(name, vel, pitch, (k % 7) + 1 if name in synth.UI_SOUNDS else 0)
        align = m.data.get("align", "start")
        if align == "peak":
            start = anchor - synth.peak_of(name, pitch)
        elif align == "end":
            start = anchor - len(clip) / SR
        else:
            start = anchor
        g_db = synth.GAIN.get(name, -8) + float(m.data.get("gain", 0.0)) + (1.2 if k % 2 else -0.4) * (name in ("key", "tick"))
        out.append({"t": round(float(m.t), 4), "anchor": round(anchor, 4), "start": round(start, 4),
                    "sound": name, "align": align, "velocity": vel, "pitch": pitch, "gain_db": round(g_db, 1),
                    "_clip": clip})
    return out


def mix_film(film: Any, out_wav: str, target_lufs: float = -14.0, ceiling_dbtp: float = -1.3,
             seed: int = 0) -> dict[str, Any]:
    """Bed + SFX -> ducking -> loudness loop -> true-peak limiter -> WAV.

    The loop runs on the numpy BS.1770 meter (fast, exact K-weighting) and the limiter holds the
    true peak `TP_MARGIN` dB under the ceiling so the AAC encode has headroom; the final WAV is then
    measured with ffmpeg's EBU R128 analyser for the report. render.mux re-checks after encoding."""
    n = int(np.ceil(film.duration * SR))
    bed, info = build_bed(film, seed)
    mcfg = film.cfg.get("music", {})
    sfx_buf = np.zeros((n + 4 * SR, 2), np.float32)
    ducks: list[tuple[float, float]] = []
    placed = place_sfx(film, seed)
    for p in placed:
        clip = p.pop("_clip")
        A.add_at(sfx_buf, clip, int(round(p["start"] * SR)), A.db(p["gain_db"]) * float(mcfg.get("sfx", 1.0)))
        if p["sound"] in DUCK_SOUNDS:
            ducks.append((max(0.0, p["start"] + synth.peak_of(p["sound"], p["pitch"])),
                          1.0 if p["sound"] != "hit" else 0.6))
    for m in film.tl.of("hit"):
        if float(m.data.get("strength", 0.5)) >= 0.6:
            ducks.append((m.t, 0.5))
    bed = bed * duck_curve(n, ducks)[:, None] * A.db(float(mcfg.get("gain", -3.0)))
    mix = bed + sfx_buf[:n]
    # tiny end fade so the last sample is silent (no click at the video end)
    f = int(0.02 * SR)
    mix[-f:] *= np.linspace(1, 0, f)[:, None]
    pre_l = A.lufs(mix)
    pre = {"lufs": round(pre_l, 2), "true_peak": round(A.true_peak_db(mix), 2), "lra": None}
    gain = target_lufs - pre_l if np.isfinite(pre_l) and pre_l > -70 else 0.0
    ceiling = ceiling_dbtp - TP_MARGIN
    y = mix
    for _ in range(6):
        y = limiter(mix * A.db(gain), ceiling)
        err = target_lufs - A.lufs(y)
        if abs(err) <= 0.2 or pre_l <= -70:
            break
        gain += err
    A.write_wav(out_wav, y)
    final = measure(out_wav)
    for _ in range(3):  # belt and braces: ffmpeg's meter is the reference
        if final["true_peak"] <= ceiling_dbtp - 0.3:
            break
        ceiling -= final["true_peak"] - (ceiling_dbtp - 0.3) + 0.05
        y = limiter(mix * A.db(gain), ceiling)
        A.write_wav(out_wav, y)
        final = measure(out_wav)
    return {"music": info, "sfx_count": len(placed), "sfx": placed, "pre": pre, "gain_db": round(gain, 2),
            "final": final, "target_lufs": target_lufs, "ceiling_dbtp": ceiling_dbtp,
            "limiter_ceiling_dbtp": round(ceiling, 2)}
