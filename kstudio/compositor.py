"""Frame compositor: Skia draw -> linear-light sub-frame motion blur -> filmic post -> dithered uint8.

Motion blur (``frame_linear``)
    Sub-frames are decoded sRGB -> linear through a 256-entry LUT and averaged in linear light, so
    bright streaks keep their energy instead of going muddy. Sample times are stratified and
    jittered inside the shutter (180 degrees by default; transitions widen it via
    ``film.shutter_mult``) and weighted by a soft tent so streak ends feather off.
    The sample count is velocity-aware:
      1. render the first and last strata; if they are identical to within ~1 code value at 1/8
         resolution the frame is static -> 1-2 samples (``adaptive_skips``);
      2. estimate on-screen displacement across the shutter with dense optical flow at 1/8 res;
         <= 2 px, and the middle sub-frame matches the blend of the ends -> 3 samples already
         blur correctly (``slow_frames``);
      3. otherwise render ``out.samples`` strata (8 standard, 16 high), measure the displacement
         between neighbouring sub-frames and subdivide every stratum whose neighbours are more
         than ~4 px apart, up to ``out.max_samples`` per frame (``refined_frames``);
      4. if the cap still leaves sub-frames more than ~6 px apart (whips), close the residual
         gaps with a line blur of that length along the dominant flow, masked to the pixels the
         sub-frames disagree on (``gap_filled``), so a fast move reads as one smear, not copies.

Post chain (``_post_linear``, float32)
    linear:  bloom (soft-knee threshold in linear light, two octaves, halation tint)
             radial chromatic aberration (R scaled out / B scaled in about the centre, hit-driven)
             vignette (aspect-corrected, multiplied in linear)
    encode:  sRGB OETF fused with the grade's per-channel curve (kstudio.grade tables)
    display: saturation, split toning, luminance-weighted film grain (4 seeded fields, rotated
             per frame with random flips and offsets), triangular-PDF dither, uint8 quantise.
"""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np
import skia

from . import grade as G
from .film import Film
from .presets import Output

_STATIC_TOL = 2          # max |diff| in 8-bit codes at 1/8 res for "the frame did not change"
_SLOW_PX = 2.0           # displacement (full-res px over the shutter) still covered by 2 samples
_GAP_PX = 4.0            # refinement target: neighbouring sub-frames at most ~4 px apart
_MID_TOL = 8             # max |mid - mean(ends)| (8-bit codes, 1/8 res) for a frame to count as slow
_FILL_PX = 6.0           # residual spacing above this (sample cap reached) is closed by a line blur


def _theme_name(film: Film) -> str | None:
    spec = getattr(film, "spec", {}) or {}
    th = spec.get("theme", (getattr(film, "cfg", {}) or {}).get("theme"))
    if isinstance(th, dict):
        th = th.get("grade_preset", th.get("base", "ink"))
    return th if isinstance(th, str) else None


def _hash32(*xs: int) -> int:
    h = 0x811C9DC5
    for x in xs:
        h = ((h ^ (x & 0xFFFFFFFF)) * 0x01000193) & 0xFFFFFFFF
        h ^= h >> 15
        h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
        h ^= h >> 12
    return h


class Compositor:
    def __init__(self, film: Film, out: Output, post: dict | None = None, seed: int = 1,
                 grade: Any = None):
        """``grade``: a kstudio.grade preset name, dict, Grade or False; by default the
        compositor uses ``post["grade"]``, then ``theme["grade"]``, then the preset named after
        the theme, then "neutral"."""
        self.film, self.out = film, out
        self.W, self.H = out.width, out.height
        self.post = dict(film.cfg.get("post", {}))
        if post:
            self.post.update(post)
        theme = getattr(film, "theme", {}) or {}
        gspec = grade if grade is not None else self.post.get("grade", theme.get("grade"))
        self.grade = G.resolve_grade(gspec, theme=_theme_name(film))
        self.seed = int(seed)
        self.rgba = np.zeros((self.H, self.W, 4), np.uint8)
        self.surface = skia.Surface(self.rgba)
        self.canvas = self.surface.getCanvas()
        self.acc = np.zeros((self.H, self.W, 3), np.float32)
        s = min(self.W, self.H) / 1080.0
        self.s = s
        self._dec = G.decode_lut()
        self._dis = None
        self._last_vec = (0.0, 0.0)
        self._gray = np.empty((self.H, self.W), np.uint8)
        self._sw = cv2.pyrDown(cv2.pyrDown(cv2.pyrDown(self._gray))).shape[1]
        self._tmp = np.empty((self.H, self.W, 3), np.float32)
        self._b1 = np.empty((self.H, self.W, 3), np.float32)
        self._b2 = np.empty((self.H, self.W, 3), np.float32)
        self._b3 = np.empty((self.H, self.W, 3), np.float32)
        self._m1 = np.empty((self.H, self.W), np.float32)
        self._m2 = np.empty((self.H, self.W), np.float32)
        self._m3 = np.empty((self.H, self.W), np.float32)
        self._u3 = np.empty((self.H, self.W, 3), np.uint8)
        self._u3b = np.empty((self.H, self.W, 3), np.uint8)
        self._u1 = np.empty((self.H, self.W), np.uint8)
        self._acc_live, self._acc_w = False, 0.0
        self._build_vignette()
        self._build_textures()
        self._V, self._D = G.encode_tables(self.grade)
        self._ramp = np.arange(256, dtype=np.float32)
        self._split = G.split_table(self.grade)
        self._sat = G.saturation_matrix(self.grade)
        self._luma = np.asarray([G.LUMA], np.float32)
        Lk = np.arange(256) / 255.0
        # grain weight by display luma: most in shadows / low mids, less in deep black, least in highlights
        wl = (1.0 - 0.78 * Lk ** 1.4) * (0.6 + 0.4 * np.clip(Lk / 0.12, 0, 1))
        self._grain_w = wl.astype(np.float32).reshape(256, 1)
        hal = np.asarray(self.grade.halation, np.float32)
        self._halation = hal / max(float((hal * np.asarray(G.LUMA)).sum()), 1e-3)
        self.stats: dict[str, Any] = {"samples": 0, "frames": 0, "adaptive_skips": 0,
                                      "slow_frames": 0, "refined_frames": 0, "gap_filled": 0, "unresolved": 0,
                                      "max_samples": 0}

    # ------------------------------------------------------------------ setup
    def _vignette_strength(self) -> float:
        if "vignette" in self.post:
            return float(self.post.get("vignette") or 0.0)
        return float(self.grade.vignette or 0.0)

    def _build_vignette(self) -> None:
        v = self._vignette_strength()
        self.vig = None
        if v <= 0:
            return
        yy, xx = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
        r = np.sqrt(((xx - self.W / 2) / (self.W / 2)) ** 2 + ((yy - self.H / 2) / (self.H / 2)) ** 2) / math.sqrt(2)
        disp = (1.0 - v * np.clip(r - 0.25, 0, 1) ** 1.6 * 1.35).clip(0, 1)
        lin = disp ** 2.2  # same perceived falloff as v0.1, applied in linear light
        # the encode scale (255^2 / LIN_MAX, see _post_linear) is folded in: one multiply does both
        self.vig = cv2.merge([(lin * (255.0 * 255.0 / G.LIN_MAX)).astype(np.float32)] * 3)

    def _build_textures(self) -> None:
        rng = np.random.default_rng(self.seed)
        s = self.s
        self.pad = 64
        gh, gw = self.H + self.pad, self.W + self.pad
        self.grain_fields: list[np.ndarray] = []
        if float(self.post.get("grain", self.grade.grain or 0.0)) > 0:
            for _ in range(4):
                # a strong coarse (~2-3 px) layer: film-like clumps that also survive x264 at delivery
                # bitrates, so the grain keeps dithering dark gradients after encode (pure 1 px noise is
                # the first thing the encoder removes, which re-exposes the 8-bit steps)
                fine = cv2.GaussianBlur(rng.standard_normal((gh, gw), dtype=np.float32), (0, 0), 0.7 * max(s, 0.5))
                coarse = cv2.GaussianBlur(rng.standard_normal((gh, gw), dtype=np.float32), (0, 0), 1.6 * max(s, 0.5))
                f = fine / max(float(fine.std()), 1e-6) + 0.9 * coarse / max(float(coarse.std()), 1e-6)
                self.grain_fields.append((f / max(float(f.std()), 1e-6)).astype(np.float32))
        # triangular-PDF dither, +-1 LSB (sum of two uniforms), in display units
        self.dither = ((rng.random((gh, gw), dtype=np.float32) + rng.random((gh, gw), dtype=np.float32) - 1.0)
                       * (1.0 / 255.0)).astype(np.float32)
        self.grain = self.grain_fields[0] if self.grain_fields else None  # v0.1 attribute name

    def _field(self, base: np.ndarray, i: int, salt: int, dst: np.ndarray) -> np.ndarray:
        """Frame i's view of a texture: random offset + one of 4 flips (no sliding pattern)."""
        h = _hash32(i, salt, self.seed)
        ox, oy = h % self.pad, (h >> 8) % self.pad
        v = base[oy:oy + self.H, ox:ox + self.W]
        flip = (h >> 16) & 3
        if flip == 0:
            return v  # read-only view, no copy
        return cv2.flip(v, {1: 0, 2: 1, 3: -1}[flip], dst=dst)

    # ------------------------------------------------------------------ sampling
    def _sample(self, t: float) -> np.ndarray:
        self.canvas.clear(0xFF000000)
        self.canvas.save()
        self.film.draw(self.canvas, t)
        self.canvas.restore()
        self.stats["samples"] += 1
        return self.rgba[..., :3]

    def _sample_rgb(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        """Render t -> (contiguous RGB uint8 copy, 1/8-res gray uint8)."""
        self._sample(t)
        rgb = cv2.cvtColor(self.rgba, cv2.COLOR_RGBA2RGB)
        cv2.cvtColor(self.rgba, cv2.COLOR_RGBA2GRAY, dst=self._gray)
        small = cv2.pyrDown(cv2.pyrDown(cv2.pyrDown(self._gray)))
        return rgb, small

    def _add(self, rgb: np.ndarray, w: float, first: bool = False) -> None:
        """acc (+)= w * linear(rgb), via a weight-scaled decode LUT."""
        lut = self._dec * np.float32(w)
        if first:
            cv2.LUT(rgb, lut, dst=self.acc)
        else:
            cv2.add(self.acc, cv2.LUT(rgb, lut, dst=self._tmp), dst=self.acc)

    def _flow_px(self, a: np.ndarray, b: np.ndarray) -> float:
        """Robust max displacement (full-res px) between two 1/8-res gray frames."""
        d = cv2.absdiff(a, b)
        if int(d.max()) <= _STATIC_TOL:
            return 0.0
        if self._dis is None:
            self._dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST)
        flow = self._dis.calc(a, b, None)
        mag = cv2.magnitude(flow[..., 0], flow[..., 1])
        mask = d > 6
        if int(mask.sum()) >= 4:
            fx, fy = flow[..., 0][mask], flow[..., 1][mask]
            ok = np.isfinite(fx) & np.isfinite(fy)
            if int(ok.sum()) >= 4:
                self._last_vec = (float(np.median(fx[ok])), float(np.median(fy[ok])))
        # appearance changes without motion (cuts, flashes, glyph swaps) read as ~0 flow: they
        # need no extra sub-frames, a cross-fade of two samples already integrates them
        if int(mask.sum()) < 4:
            return 0.0
        m = mag[mask]
        m = m[np.isfinite(m)]
        # flow can fail on tiny or textureless images: assume a moderate move rather than none
        px = float(np.percentile(m, 98)) if m.size >= 4 else 4.0
        return px * (self.W / self._sw)

    @staticmethod
    def _tent(u: float) -> float:
        return 0.5 + 0.5 * math.sin(math.pi * min(max(u, 0.0), 1.0))

    def _time(self, t: float, span: float, u: float) -> float:
        return min(max(t + (u - 0.5) * span, 0.0), max(self.film.duration - 1e-4, 0.0))

    def frame_linear(self, i: int) -> np.ndarray:
        """Motion-blurred frame i in linear light (float32 RGB; aliases self.acc when blurred)."""
        fps = self.out.fps
        t = i / fps
        n = max(1, int(self.out.samples))
        mult = self.film.shutter_mult(t)
        span = self.out.shutter / 360.0 / fps * mult
        st = self.stats
        if n <= 1 or span <= 0:
            rgb, _ = self._sample_rgb(t)
            st["max_samples"] = max(st["max_samples"], 1)
            return cv2.LUT(rgb, self._dec, dst=self.acc)
        cap = max(n, int(getattr(self.out, "max_samples", 0) or n * 4))
        rng = np.random.default_rng(_hash32(i, 0x5EED, self.seed))
        jit = rng.random(n)
        us = [(k + float(jit[k])) / n for k in range(n)]
        rgbs: list[np.ndarray | None] = [None] * n
        smalls: list[np.ndarray | None] = [None] * n
        rgbs[0], smalls[0] = self._sample_rgb(self._time(t, span, us[0]))
        rgbs[-1], smalls[-1] = self._sample_rgb(self._time(t, span, us[-1]))
        sa, sb = smalls[0], smalls[-1]
        assert sa is not None and sb is not None
        if int(cv2.absdiff(sa, sb).max()) <= _STATIC_TOL:
            st["adaptive_skips"] += 1
            return self._finish([(rgbs[0], 0.5), (rgbs[-1], 0.5)], 2)
        km = n // 2
        rgbs[km], smalls[km] = self._sample_rgb(self._time(t, span, us[km]))
        # slow: small flow AND the middle sub-frame is explained by blending the ends (this also
        # catches displacements too large for the flow search, e.g. thin objects at whip speed)
        mid_err = int(cv2.absdiff(smalls[km], cv2.addWeighted(sa, 0.5, sb, 0.5, 0.0)).max())
        if mid_err <= _MID_TOL and self._flow_px(sa, sb) / max(us[-1] - us[0], 1e-3) <= _SLOW_PX:
            st["slow_frames"] += 1
            return self._finish([(rgbs[0], 1 / 3), (rgbs[km], 1 / 3), (rgbs[-1], 1 / 3)], 3)
        for k in range(1, n - 1):
            if rgbs[k] is None:
                rgbs[k], smalls[k] = self._sample_rgb(self._time(t, span, us[k]))
        # subdivide strata whose neighbouring sub-frames are more than _GAP_PX apart
        gaps, vecs = [], []
        for k in range(n - 1):
            self._last_vec = (0.0, 0.0)
            gaps.append(self._flow_px(smalls[k], smalls[k + 1]))  # type: ignore[arg-type]
            vecs.append(self._last_vec)
        # flow under-reads moves larger than a feature's own width (thin strokes at whip speed);
        # a stratum that is not the blend of its neighbours is under-sampled whatever flow says
        full = _GAP_PX * cap / n
        for k in range(1, n - 1):
            if max(gaps[k - 1], gaps[k]) > 2 * _GAP_PX:  # flow already asked for subdivision
                continue
            blend = cv2.addWeighted(smalls[k - 1], 0.5, smalls[k + 1], 0.5, 0.0)  # type: ignore[arg-type]
            if int(cv2.absdiff(smalls[k], blend).max()) > _MID_TOL * 2:  # type: ignore[arg-type]
                st["unresolved"] += 1
                gaps[k - 1], gaps[k] = max(gaps[k - 1], full), max(gaps[k], full)
        need = [max(1, int(math.ceil(max(gaps[k - 1] if k > 0 else 0.0, gaps[k] if k < n - 1 else 0.0) / _GAP_PX)))
                for k in range(n)]
        extra, budget = sum(m - 1 for m in need), cap - n
        if extra > budget:
            need = [1 + int((m - 1) * budget / extra) for m in need]
        parts: list[tuple[np.ndarray, float]] = []
        rendered = n
        for k in range(n):
            m = need[k]
            if m == 1:
                parts.append((rgbs[k], self._tent(us[k]) / n))  # type: ignore[arg-type]
                continue
            j0 = min(int(jit[k] * m), m - 1)  # sub-stratum already holding the base sample
            parts.append((rgbs[k], self._tent(us[k]) / (n * m)))  # type: ignore[arg-type]
            rgbs[k] = None
            sub = rng.random(m)
            for j in range(m):
                if j == j0:
                    continue
                u = (k + (j + float(sub[j])) / m) / n
                rgb, _ = self._sample_rgb(self._time(t, span, u))
                self._accumulate_now(parts, rgb, self._tent(u) / (n * m))
                rendered += 1
        if rendered > n:
            st["refined_frames"] += 1
        acc = self._finish(parts, rendered)
        # residual spacing between neighbouring sub-frames once the cap is reached
        res = [gaps[k] / max(1.0, 0.5 * (need[k] + need[k + 1])) for k in range(n - 1)]
        kmax = int(np.argmax(res))
        if res[kmax] > _FILL_PX:
            self._gap_fill(acc, res[kmax], vecs[kmax], smalls)  # type: ignore[arg-type]
        return acc

    def _gap_fill(self, acc: np.ndarray, length: float, vec: tuple[float, float],
                  smalls: list[np.ndarray]) -> None:
        """Close the residual gaps of a capped fast frame: a line blur ``length`` px long along the
        dominant motion, applied only where the sub-frames differ (static pixels stay sharp)."""
        dx, dy = vec
        if math.hypot(dx, dy) < 1e-6:
            dx, dy = 1.0, 0.0
        L = int(round(length)) | 1
        if L < 3:
            return
        self.stats["gap_filled"] += 1
        blur = self._b1
        if abs(dy) <= 0.18 * abs(dx):
            cv2.blur(acc, (L, 1), dst=blur, borderType=cv2.BORDER_REFLECT)
        elif abs(dx) <= 0.18 * abs(dy):
            cv2.blur(acc, (1, L), dst=blur, borderType=cv2.BORDER_REFLECT)
        else:
            ker = np.zeros((L, L), np.float32)
            c, r = L // 2, (L - 1) / 2.0
            nx, ny = dx / math.hypot(dx, dy), dy / math.hypot(dx, dy)
            cv2.line(ker, (int(round(c - nx * r)), int(round(c - ny * r))),
                     (int(round(c + nx * r)), int(round(c + ny * r))), 1.0, 1, cv2.LINE_AA)
            ker /= max(float(ker.sum()), 1e-6)
            cv2.filter2D(acc, -1, ker, dst=blur, borderType=cv2.BORDER_REFLECT)
        mx, mn = smalls[0].copy(), smalls[0].copy()
        for sm in smalls[1:]:
            cv2.max(mx, sm, dst=mx)
            cv2.min(mn, sm, dst=mn)
        # generous, soft mask: anything the sub-frames disagree on, grown by the blur length
        r = 1 + int(math.ceil(L * self._sw / self.W))
        m = cv2.dilate(cv2.absdiff(mx, mn), np.ones((2 * r + 1, 2 * r + 1), np.uint8)).astype(np.float32)
        np.clip(m * (1.0 / 8.0), 0.0, 1.0, out=m)
        m = cv2.GaussianBlur(m, (0, 0), 1.0)
        cv2.resize(m, (self.W, self.H), dst=self._m1, interpolation=cv2.INTER_LINEAR)
        cv2.subtract(blur, acc, dst=self._b2)
        cv2.merge([self._m1, self._m1, self._m1], dst=self._b3)
        cv2.multiply(self._b2, self._b3, dst=self._b2)
        cv2.add(acc, self._b2, dst=acc)

    def _accumulate_now(self, parts: list, rgb: np.ndarray, w: float) -> None:
        """Fold a freshly rendered sub-sample into acc immediately (keeps memory flat)."""
        if not self._acc_live:
            self._add(rgb, w, first=True)
            self._acc_live = True
            self._acc_w = w
        else:
            self._add(rgb, w)
            self._acc_w += w

    def _finish(self, parts: list, used: int) -> np.ndarray:
        live = self._acc_live
        wsum = self._acc_w if live else 0.0
        for k, (rgb, w) in enumerate(parts):
            self._add(rgb, w, first=(k == 0 and not live))
            wsum += w
            live = True
        self._acc_live, self._acc_w = False, 0.0
        self.acc *= 1.0 / max(wsum, 1e-12)
        self.stats["max_samples"] = max(self.stats["max_samples"], used)
        return self.acc

    def frame_float(self, i: int) -> np.ndarray:
        """Motion-blurred frame i, display-encoded float (v0.1 API; ungraded, no post)."""
        lin = self.frame_linear(i)
        return G.linear_to_srgb(lin).astype(np.float32)

    # ------------------------------------------------------------------ post
    def _bloom(self, lin: np.ndarray, amount: float) -> None:
        """In place: lin += soft-knee (linear-light threshold) two-octave glow, halation-tinted."""
        s = self.s
        q = cv2.pyrDown(cv2.pyrDown(lin))
        lum = q.max(axis=2, keepdims=True)
        knee = np.clip((lum - 0.40) * (1 / 0.55), 0, 1)  # linear 0.40 ~ sRGB 0.66
        bright = q * (knee * knee)
        b1 = cv2.GaussianBlur(bright, (0, 0), 6 * s)
        b2 = cv2.GaussianBlur(cv2.pyrDown(bright), (0, 0), 5 * s)
        b = b1 + cv2.resize(b2, (b1.shape[1], b1.shape[0]), interpolation=cv2.INTER_LINEAR)
        b *= self._halation.reshape(1, 1, 3) * np.float32(amount * 0.45)
        cv2.add(lin, cv2.resize(b, (self.W, self.H), dst=self._b1, interpolation=cv2.INTER_LINEAR), dst=lin)

    def _chroma(self, lin: np.ndarray, px: float) -> None:
        """In place radial lateral CA: red scaled out, blue scaled in about the centre; px = corner shift."""
        half_diag = 0.5 * math.hypot(self.W, self.H)
        k = px / half_diag
        cx, cy = self.W / 2.0, self.H / 2.0
        r, g, b = self._m1, self._m2, self._m3
        cv2.split(lin, [r, g, b])

        def scaled(ch: np.ndarray, f: float, dst: np.ndarray) -> np.ndarray:
            # destination x' = c + f (x - c)  <-  source x = c + (x' - c) / f
            m = np.float32([[1 / f, 0, cx - cx / f], [0, 1 / f, cy - cy / f]])
            return cv2.warpAffine(ch, m, (self.W, self.H), dst=dst, flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REFLECT)
        rr = scaled(r, 1 + k, self._b1[..., 0].copy())
        bb = scaled(b, 1 - k, self._b1[..., 2].copy())
        cv2.merge([rr, g, bb], dst=lin)

    def _post_linear(self, lin: np.ndarray, i: int) -> np.ndarray:
        """Linear-light post + grade -> display-space float32 RGB (grain + dither added, unclipped).
        ``lin`` is modified in place; the result lives in an internal buffer."""
        p, s = self.post, self.s
        t = i / self.out.fps
        if lin is not self.acc:
            np.copyto(self.acc, lin)
            lin = self.acc
        bloom = float(p.get("bloom", 0.0))
        if bloom > 0:
            self._bloom(lin, bloom)
        chroma = float(p.get("chroma", 0.0))
        if chroma > 0:
            px = chroma * s * (0.8 + 4.5 * min(self.film.tl.hit_env(t, 10.0), 1.5))
            if px >= 0.3:
                self._chroma(lin, px)
        # vignette (linear) and the encode scale in one multiply: q = 255 sqrt(lin / LIN_MAX)
        q = self._b1
        if self.vig is not None:
            cv2.multiply(lin, self.vig, dst=q)
        else:
            np.multiply(lin, np.float32(255.0 * 255.0 / G.LIN_MAX), out=q)
        cv2.sqrt(q, dst=q)
        idx = cv2.convertScaleAbs(q, dst=self._u3)
        frac = cv2.subtract(q, cv2.LUT(idx, self._ramp, dst=self._b2), dst=self._b2)
        cv2.multiply(frac, cv2.LUT(idx, self._D, dst=self._b3), dst=frac)
        disp = cv2.add(cv2.LUT(idx, self._V, dst=self._b3), frac, dst=self._b3)
        if self._sat is not None:
            disp = cv2.transform(disp, self._sat, dst=self._b1)
        gr = float(p.get("grain", self.grade.grain or 0.0))
        use_grain = gr > 0 and bool(self.grain_fields)
        if self._split is not None or use_grain:
            L8 = cv2.convertScaleAbs(cv2.transform(disp, self._luma, dst=self._m1), dst=self._u1, alpha=255.0)
            if self._split is not None:
                cv2.add(disp, cv2.LUT(cv2.merge([L8, L8, L8], dst=self._u3b), self._split, dst=self._b2), dst=disp)
        noise = self._field(self.dither, i, 0xD17, self._m2) if p.get("dither", True) else None
        if use_grain:
            field = self._field(self.grain_fields[i % len(self.grain_fields)], i, 0x6A1, self._m3)
            wl = cv2.LUT(L8, self._grain_w, dst=self._m1)
            g = cv2.multiply(field, wl, dst=self._m1, scale=gr * 0.5)
            noise = g if noise is None else cv2.add(g, noise, dst=self._m1)
        if noise is not None:
            cv2.add(disp, cv2.merge([noise, noise, noise], dst=self._b2), dst=disp)
        return disp

    def post_process(self, img: np.ndarray, i: int) -> np.ndarray:
        """v0.1 API: display-encoded float in -> graded display float out (clipped 0..1)."""
        lin = G.srgb_to_linear(np.clip(img, 0.0, None)).astype(np.float32)
        out = self._post_linear(lin, i).copy()
        np.clip(out, 0.0, 1.0, out=out)
        return out

    def frame(self, i: int) -> np.ndarray:
        """Final RGB uint8 frame i."""
        self.stats["frames"] += 1
        disp = self._post_linear(self.frame_linear(i), i)
        cv2.threshold(disp, 0.0, 0.0, cv2.THRESH_TOZERO, dst=disp)
        return cv2.convertScaleAbs(disp, alpha=255.0)
