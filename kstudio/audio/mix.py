"""Soundtrack mixer: music bed (procedural or user file) + SFX at the film's markers,
ducking under hits, then loudness normalisation to a target (default -14 LUFS integrated)
with a look-ahead limiter holding true peak under the ceiling (default -1 dBTP)."""
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

DUCK_SOUNDS = {"impact", "impact_big", "logo_hit", "hit"}


def measure(path: str) -> dict[str, float]:
    """Integrated loudness, true peak, LRA via ffmpeg's EBU R128 loudnorm analyser."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
                        "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        raise RuntimeError(f"loudness measurement failed: {r.stderr[-400:]}")
    d = json.loads(m.group(0))
    return {"lufs": float(d["input_i"]), "true_peak": float(d["input_tp"]), "lra": float(d["input_lra"])}


def limiter(x: np.ndarray, ceiling_db: float = -1.5, lookahead: float = 0.005, release: float = 0.06) -> np.ndarray:
    """Look-ahead brickwall: gain = min(1, ceil/|x|), min-filtered over the look-ahead window,
    then smoothed, so gain is already down when the peak arrives (no clipping, no clicks)."""
    ceil = 10 ** (ceiling_db / 20)
    env = np.abs(x).max(axis=1).astype(np.float32)
    g = np.minimum(1.0, ceil / np.maximum(env, 1e-9)).astype(np.float32)
    k = max(3, int(lookahead * SR) | 1)
    g = cv2.erode(g.reshape(1, -1), np.ones((1, 2 * k + 1), np.uint8)).ravel().astype(np.float32)
    g = cv2.blur(g.reshape(1, -1), (k, 1)).ravel().astype(np.float32)
    # slower release: running max of the attack-smoothed curve's recovery
    # release: one-pole recovery so gain rises slowly after a peak
    a = np.float32(np.exp(-1.0 / (release * SR)))
    prev = 1.0
    gl = g.tolist()
    for i, v in enumerate(gl):
        prev = v if v < prev else prev * a + v * (1 - a)
        gl[i] = prev
    g = np.asarray(gl, np.float32)
    y = x * g[:, None]
    return np.clip(y, -ceil, ceil)


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


def mix_film(film: Any, out_wav: str, target_lufs: float = -14.0, ceiling_dbtp: float = -1.0,
             seed: int = 0) -> dict[str, Any]:
    n = int(np.ceil(film.duration * SR))
    bed, info = build_bed(film, seed)
    mcfg = film.cfg.get("music", {})
    sfx_buf = np.zeros((n + 4 * SR, 2), np.float32)
    ducks: list[tuple[float, float]] = []
    placed: list[dict] = []
    counts: dict[str, int] = {}
    for m in film.tl.of("sfx"):
        name = m.data.get("sound") or m.name
        if not name or name not in synth.KIT:
            continue
        k = counts.get(name, 0)
        counts[name] = k + 1
        g_db = synth.GAIN.get(name, -8) + float(m.data.get("gain", 0.0)) + (1.2 if k % 2 else -0.4) * (name in ("key", "tick"))
        clip = synth.get(name)
        if m.data.get("align") == "peak":
            start_t = m.t - synth.PEAK.get(name, 0.0)
        else:
            start_t = m.t
        A.add_at(sfx_buf, clip, int(start_t * SR), A.db(g_db) * float(mcfg.get("sfx", 1.0)))
        placed.append({"t": round(m.t, 3), "sound": name, "gain_db": round(g_db, 1)})
        if name in DUCK_SOUNDS:
            ducks.append((max(0.0, start_t), 1.0 if name != "hit" else 0.6))
    for m in film.tl.of("hit"):
        if float(m.data.get("strength", 0.5)) >= 0.6:
            ducks.append((m.t, 0.5))
    bed = bed * duck_curve(n, ducks)[:, None] * A.db(float(mcfg.get("gain", -3.0)))
    mix = bed + sfx_buf[:n]
    # tiny end fade so the last sample is silent (no click at the video end)
    f = int(0.02 * SR)
    mix[-f:] *= np.linspace(1, 0, f)[:, None]
    tmp = out_wav + ".pre.wav"
    A.write_wav(tmp, mix)
    pre = measure(tmp)
    gain = target_lufs - pre["lufs"] if np.isfinite(pre["lufs"]) and pre["lufs"] > -70 else 0.0
    ceiling = ceiling_dbtp - 0.6
    final = None
    for _ in range(4):
        y = limiter(mix * A.db(gain), ceiling)
        A.write_wav(out_wav, y)
        final = measure(out_wav)
        lufs_err = target_lufs - final["lufs"]
        if final["true_peak"] > ceiling_dbtp:
            ceiling -= final["true_peak"] - ceiling_dbtp + 0.1
        elif abs(lufs_err) > 0.4:
            gain += lufs_err
        else:
            break
    os.remove(tmp)
    return {"music": info, "sfx_count": len(placed), "sfx": placed, "pre": pre, "gain_db": round(gain, 2),
            "final": final, "target_lufs": target_lufs, "ceiling_dbtp": ceiling_dbtp}
