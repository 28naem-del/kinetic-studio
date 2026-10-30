"""Frame compositor: Skia draw -> sub-frame motion blur -> filmic post (numpy/OpenCV).

Post chain (float32, linear-ish 0..1):
    bloom      quarter-res threshold + two gaussian octaves, added back
    chroma     lateral R/B split, driven by the hit envelope (impacts feel physical)
    vignette   precomputed radial mask
    grain      precomputed luminance noise field, randomly offset per frame (deterministic)
"""
from __future__ import annotations

import math

import cv2
import numpy as np
import skia

from .film import Film
from .presets import Output


class Compositor:
    def __init__(self, film: Film, out: Output, post: dict | None = None, seed: int = 1):
        self.film, self.out = film, out
        self.W, self.H = out.width, out.height
        self.post = dict(film.cfg.get("post", {}))
        if post:
            self.post.update(post)
        self.rgba = np.zeros((self.H, self.W, 4), np.uint8)
        self.surface = skia.Surface(self.rgba)
        self.canvas = self.surface.getCanvas()
        self.acc = np.zeros((self.H, self.W, 3), np.float32)
        s = min(self.W, self.H) / 1080.0
        self.s = s
        yy, xx = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
        r = np.sqrt(((xx - self.W / 2) / (self.W / 2)) ** 2 + ((yy - self.H / 2) / (self.H / 2)) ** 2) / math.sqrt(2)
        v = float(self.post.get("vignette", 0.0))
        self.vig = (1.0 - v * np.clip(r - 0.25, 0, 1) ** 1.6 * 1.35).clip(0, 1)[..., None].astype(np.float32)
        del xx, yy, r
        rng = np.random.default_rng(seed)
        self.pad = 128
        g = rng.normal(0, 1, (self.H + self.pad, self.W + self.pad)).astype(np.float32)
        grain = cv2.GaussianBlur(g, (0, 0), 0.6 * max(s, 0.5))
        self.grain = (grain / max(float(grain.std()), 1e-6)).astype(np.float32)
        self.stats = {"samples": 0, "frames": 0, "adaptive_skips": 0}

    # ------------------------------------------------------------------ sampling
    def _sample(self, t: float) -> np.ndarray:
        self.canvas.clear(0xFF000000)
        self.canvas.save()
        self.film.draw(self.canvas, t)
        self.canvas.restore()
        self.stats["samples"] += 1
        return self.rgba[..., :3]

    def frame_float(self, i: int) -> np.ndarray:
        fps = self.out.fps
        t = i / fps
        n = self.out.samples
        mult = self.film.shutter_mult(t)
        span = self.out.shutter / 360.0 / fps * mult
        if n <= 1 or span <= 0:
            return self._sample(t).astype(np.float32) * (1 / 255.0)
        times = [t + (k / (n - 1) - 0.5) * span for k in range(n)]
        times = [min(max(x, 0.0), max(self.film.duration - 1e-4, 0.0)) for x in times]
        first = self._sample(times[0]).astype(np.float32)
        last = self._sample(times[-1]).astype(np.float32)
        # adaptive: if the open-shutter endpoints barely differ, the frame is effectively still
        small_a = cv2.resize(first, (self.W // 8, self.H // 8), interpolation=cv2.INTER_AREA)
        small_b = cv2.resize(last, (self.W // 8, self.H // 8), interpolation=cv2.INTER_AREA)
        if float(np.abs(small_a - small_b).mean()) < 0.35:
            self.stats["adaptive_skips"] += 1
            return (first + last) * (0.5 / 255.0)
        acc = self.acc
        np.add(first, last, out=acc)
        for x in times[1:-1]:
            acc += self._sample(x)
        acc *= 1.0 / (255.0 * n)
        return acc

    # ------------------------------------------------------------------ post
    def post_process(self, img: np.ndarray, i: int) -> np.ndarray:
        p, s = self.post, self.s
        t = i / self.out.fps
        bloom = float(p.get("bloom", 0.0))
        if bloom > 0:
            q = cv2.resize(img, (self.W // 4, self.H // 4), interpolation=cv2.INTER_AREA)
            lum = q.max(axis=2, keepdims=True)
            bright = q * np.clip((lum - 0.62) / 0.38, 0, 1)
            b1 = cv2.GaussianBlur(bright, (0, 0), 6 * s)
            b2 = cv2.GaussianBlur(cv2.resize(bright, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA), (0, 0), 10 * s)
            b = b1 + cv2.resize(b2, (b1.shape[1], b1.shape[0]), interpolation=cv2.INTER_LINEAR)
            img = img + cv2.resize(b, (self.W, self.H), interpolation=cv2.INTER_LINEAR) * (bloom * 0.6)
        chroma = float(p.get("chroma", 0.0))
        if chroma > 0:
            k = int(round(chroma * s * (0.8 + 7.0 * min(self.film.tl.hit_env(t, 10.0), 1.5))))
            if k >= 1:
                img = img.copy() if img is self.acc else img
                img[..., 0] = np.roll(img[..., 0], k, axis=1)
                img[..., 2] = np.roll(img[..., 2], -k, axis=1)
        if float(p.get("vignette", 0.0)) > 0:
            img = img * self.vig
        gr = float(p.get("grain", 0.0))
        if gr > 0:
            h = (i * 2654435761) & 0xFFFFFFFF
            ox, oy = h % self.pad, (h >> 8) % self.pad
            noise = self.grain[oy:oy + self.H, ox:ox + self.W]
            img = img + (noise * (gr * 0.5))[..., None] * (0.35 + 0.65 * (1 - np.abs(img - 0.5) * 2))
        np.clip(img, 0.0, 1.0, out=img)
        return img

    def frame(self, i: int) -> np.ndarray:
        """Final RGB uint8 frame i."""
        self.stats["frames"] += 1
        img = self.post_process(self.frame_float(i), i)
        return (img * 255.0 + 0.5).astype(np.uint8)
