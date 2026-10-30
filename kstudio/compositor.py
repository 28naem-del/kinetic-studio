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
      4. if the cap still leaves neighbouring sub-frames more than ~3 px apart (whips, pushes,
         zooms), close the residual gaps with a per-pixel vector smear (``gap_filled``): dense
         flow between every pair of consecutive rendered sub-frames gives, per pixel, the step
         between copies (radial for zooms, rotational for rolls); the accumulated frame is then
         box-filtered along that local vector over exactly one step, in linear light, with a
         cascade of 2-tap remaps (2^k taps), so a fast move reads as one continuous streak.
         Static pixels have a zero vector and stay sharp. ``unresolved`` counts frames where a
         step was longer than the smear may close (copies could remain); ``undersampled`` counts
         strata whose middle sub-frame was not the blend of its neighbours (flow under-read).
    The shutter never integrates across a hard cut: sample times are clamped to the shot that
    owns the frame (``film.shot_bounds``), so the first frame after a cut carries none of the
    outgoing scene and the last frame before it none of the incoming one, and no flow or smear
    is ever computed between two scenes. Frames under an accent flash (opacity >= 0.5) keep the
    base strata and skip the flow refinement and the smear (``flash_frames``).

    Flashes (``film.flash_at``) are applied once per delivered frame, after the blur, so a 2-frame
    flash is exactly 2 frames at every shutter. Grain and dither are re-seeded per frame from
    (frame index, spec hash), so the frames of a spec are bit-identical across runs, processes and
    thread counts; ``render.encoder_cmd`` keeps the delivered stream bit-identical too (one x264
    thread per chunk, see ``render.ENCODER_THREADS``).

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
_FILL_PX = 3.0           # residual spacing above this (sample cap reached) is closed by the vector smear
_SMEAR_MAX = 480.0       # longest step (px at 1080) the smear may close; longer counts as unresolved
_SMEAR_SPAN = 1.5      # smear length in steps: > 1 so neighbouring copies overlap where the flow field is coarse
_CUT_EPS = 1e-5          # s: the last sample before a hard cut sits this far before it
_FLASH_VEIL = 0.5        # flash opacity from which a frame skips flow refinement and the smear
_FIELD_CACHE: dict[tuple, tuple[list[np.ndarray], np.ndarray]] = {}


def _theme_name(film: Film) -> str | None:
    spec = getattr(film, "spec", {}) or {}
    th = spec.get("theme", (getattr(film, "cfg", {}) or {}).get("theme"))
    if isinstance(th, dict):
        th = th.get("grade_preset", th.get("base", "ink"))
    return th if isinstance(th, str) else None


def spec_seed(film: Film) -> int:
    """32-bit seed from the film's spec (private "_" keys excluded): grain and sample jitter are
    a function of (frame index, spec), so every render of a spec draws bit-identical frames."""
    from .spec import spec_hash
    return int(spec_hash(getattr(film, "spec", {}) or {})[:8], 16)


def _hash32(*xs: int) -> int:
    h = 0x811C9DC5
    for x in xs:
        h = ((h ^ (x & 0xFFFFFFFF)) * 0x01000193) & 0xFFFFFFFF
        h ^= h >> 15
        h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
        h ^= h >> 12
    return h


class Compositor:
    def __init__(self, film: Film, out: Output, post: dict | None = None, seed: int | None = None,
                 grade: Any = None):
        """``grade``: a kstudio.grade preset name, dict, Grade or False; by default the
        compositor uses ``post["grade"]``, then ``theme["grade"]``, then the preset named after
        the theme, then "neutral". ``seed``: grain/jitter seed; default derived from the spec."""
        self.film, self.out = film, out
        self.W, self.H = out.width, out.height
        self.post = dict(film.cfg.get("post", {}))
        if post:
            self.post.update(post)
        theme = getattr(film, "theme", {}) or {}
        gspec = grade if grade is not None else self.post.get("grade", theme.get("grade"))
        self.grade = G.resolve_grade(gspec, theme=_theme_name(film))
        self.seed = int(seed) if seed is not None else spec_seed(film)
        if hasattr(film, "defer_flash"):
            film.defer_flash = True  # flashes are applied per delivered frame in _post_linear
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
        self._grid_xy: tuple[np.ndarray, np.ndarray] | None = None
        self.stats: dict[str, Any] = {"samples": 0, "frames": 0, "adaptive_skips": 0,
                                      "slow_frames": 0, "refined_frames": 0, "gap_filled": 0, "unresolved": 0,
                                      "undersampled": 0, "max_samples": 0, "max_step_px": 0.0,
                                      "flash_frames": 0}
        self._shot: tuple[float, float] = (-math.inf, math.inf)

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
        self.pad = 64
        want = float(self.post.get("grain", self.grade.grain or 0.0)) > 0
        key = (self.W, self.H, self.seed, want)
        if key in _FIELD_CACHE:  # one build per process (tests / previews make many compositors)
            self.grain_fields, self.dither = _FIELD_CACHE[key]
            self.grain = self.grain_fields[0] if self.grain_fields else None
            return
        rng = np.random.default_rng(self.seed)
        s = self.s
        gh, gw = self.H + self.pad, self.W + self.pad
        self.grain_fields: list[np.ndarray] = []
        if want:
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
        _FIELD_CACHE.clear()
        _FIELD_CACHE[key] = (self.grain_fields, self.dither)

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

    def _flash_alpha(self, i: int) -> float:
        """Opacity of the accent flash the compositor lays over delivered frame i (0 if none)."""
        flash = getattr(self.film, "flash_at", None)
        if flash is None or not getattr(self.film, "defer_flash", False):
            return 0.0
        return float(flash(i)[1])

    @staticmethod
    def _tent(u: float) -> float:
        return 0.5 + 0.5 * math.sin(math.pi * min(max(u, 0.0), 1.0))

    def _time(self, t: float, span: float, u: float) -> float:
        lo, hi = self._shot
        tt = min(max(t + (u - 0.5) * span, lo), hi)
        return min(max(tt, 0.0), max(self.film.duration - 1e-4, 0.0))

    def frame_linear(self, i: int) -> np.ndarray:
        """Motion-blurred frame i in linear light (float32 RGB; aliases self.acc when blurred)."""
        fps = self.out.fps
        t = i / fps
        n = max(1, int(self.out.samples))
        mult = self.film.shutter_mult(t)
        span = self.out.shutter / 360.0 / fps * mult
        st = self.stats
        # the shutter never straddles a hard cut: samples are clamped to the shot that owns t
        bounds = getattr(self.film, "shot_bounds", None)
        lo, hi = bounds(t) if bounds is not None else (-math.inf, math.inf)
        self._shot = (lo, hi - _CUT_EPS if hi < math.inf else hi)
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
        if self._flash_alpha(i) >= _FLASH_VEIL:
            # under a 64-74 % accent flash the motion reads only as a tint: the base strata,
            # no flow-driven subdivision and no vector smear (nothing to resolve under the veil)
            st["flash_frames"] += 1
            for k in range(1, n - 1):
                rgbs[k], _ = self._sample_rgb(self._time(t, span, us[k]))
            return self._finish([(rgbs[k], self._tent(us[k]) / n) for k in range(n)], n)
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
        flagged = False
        for k in range(1, n - 1):
            if max(gaps[k - 1], gaps[k]) > 2 * _GAP_PX:  # flow already asked for subdivision
                continue
            blend = cv2.addWeighted(smalls[k - 1], 0.5, smalls[k + 1], 0.5, 0.0)  # type: ignore[arg-type]
            if int(cv2.absdiff(smalls[k], blend).max()) > _MID_TOL * 2:  # type: ignore[arg-type]
                st["undersampled"] += 1
                flagged = True
                gaps[k - 1], gaps[k] = max(gaps[k - 1], full), max(gaps[k], full)
        need = [max(1, int(math.ceil(max(gaps[k - 1] if k > 0 else 0.0, gaps[k] if k < n - 1 else 0.0) / _GAP_PX)))
                for k in range(n)]
        extra, budget = sum(m - 1 for m in need), cap - n
        if extra > budget:
            need = [1 + int((m - 1) * budget / extra) for m in need]
        parts: list[tuple[np.ndarray, float]] = []
        rendered = n
        seq = [(us[k], smalls[k]) for k in range(n)]  # every rendered sub-frame (time, 1/8 gray)
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
                rgb, sm = self._sample_rgb(self._time(t, span, u))
                seq.append((u, sm))
                self._accumulate_now(parts, rgb, self._tent(u) / (n * m))
                rendered += 1
        if rendered > n:
            st["refined_frames"] += 1
        acc = self._finish(parts, rendered)
        # estimated residual step once refined; flagged strata (flow under-read) always get checked
        res = [gaps[k] / max(1.0, 0.5 * (need[k] + need[k + 1])) for k in range(n - 1)]
        if max(res) > _FILL_PX * 0.5 or flagged:
            self._smear(acc, seq)
        return acc

    # ------------------------------------------------------------------ smear
    def _step_field(self, seq: list[tuple[float, np.ndarray]]) -> tuple[np.ndarray, np.ndarray, float] | None:
        """Per-pixel step between consecutive copies (1/8 res, full-res px): for every pair of
        consecutive rendered sub-frames the dense flow, keeping per pixel the longest step seen
        where the pair actually changed; grown over the swept path so the gaps between copies
        get the vector of the copies around them. None if no step exceeds _FILL_PX."""
        seq = sorted(seq, key=lambda x: x[0])
        sh, sw = seq[0][1].shape
        best = np.zeros((sh, sw), np.float32)
        fx = np.zeros((sh, sw), np.float32)
        fy = np.zeros((sh, sw), np.float32)
        if self._dis is None:
            self._dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST)
        k3 = np.ones((3, 3), np.uint8)
        for (_, a), (_, b) in zip(seq, seq[1:]):
            d = cv2.absdiff(a, b)
            if int(d.max()) <= _STATIC_TOL:
                continue
            flow = self._dis.calc(a, b, None)
            np.nan_to_num(flow, copy=False)
            mag = cv2.magnitude(flow[..., 0], flow[..., 1])
            # only where the pair changed (dilated: an edge's flow belongs to both of its sides)
            mag *= cv2.dilate((d > 4).astype(np.uint8), k3).astype(np.float32)
            sel = mag > best
            best[sel] = mag[sel]
            fx[sel] = flow[..., 0][sel]
            fy[sel] = flow[..., 1][sel]
        scale = self.W / float(sw)
        peak = float(best.max()) * scale
        self.stats["max_step_px"] = max(float(self.stats.get("max_step_px", 0.0)), round(peak, 2))
        if peak <= _FILL_PX:
            return None
        # grow over the swept path: magnitude-weighted average of the vectors around each pixel
        r = 1 + int(math.ceil(float(best.max())))
        ksz = (2 * r + 1, 2 * r + 1)
        w = best * best + 1e-6
        wx = cv2.boxFilter(fx * w, -1, ksz, normalize=False)
        wy = cv2.boxFilter(fy * w, -1, ksz, normalize=False)
        ws = cv2.boxFilter(w, -1, ksz, normalize=False)
        grown = cv2.dilate(best, np.ones(ksz, np.uint8))
        vx, vy = wx / ws, wy / ws
        vm = cv2.magnitude(vx, vy) + 1e-6
        k = np.where(grown * scale > _FILL_PX * 0.5, grown / vm, 0.0).astype(np.float32)
        return (vx * k * scale).astype(np.float32), (vy * k * scale).astype(np.float32), peak

    def _smear(self, acc: np.ndarray, seq: list[tuple[float, np.ndarray]]) -> None:
        """Close the gaps between copies of a fast move: box-filter acc (linear light, in place)
        along the local step vector over one step, via log2(taps) cascaded 2-tap remaps."""
        if len(seq) < 2:
            return
        fld = self._step_field(seq)
        if fld is None:
            return
        vx_s, vy_s, peak = fld
        self.stats["gap_filled"] += 1
        if peak > _SMEAR_MAX * self.s:
            self.stats["unresolved"] += 1
        lim = _SMEAR_MAX * self.s
        mag = cv2.magnitude(vx_s, vy_s)
        over = mag > lim
        if over.any():
            f = np.where(over, lim / np.maximum(mag, 1e-6), 1.0).astype(np.float32)
            vx_s, vy_s = vx_s * f, vy_s * f
        # region that moves (plus a margin of one step) - the remaps run on that box only
        mv = (cv2.magnitude(vx_s, vy_s) > 0.5).astype(np.uint8)
        ys, xs = np.nonzero(mv)
        if ys.size == 0:
            return
        sh, sw = vx_s.shape
        kx, ky = self.W / float(sw), self.H / float(sh)
        mpx = min(float(cv2.magnitude(vx_s, vy_s).max()), lim)
        x0 = max(0, int(xs.min() * kx - mpx) - 2); x1 = min(self.W, int((xs.max() + 1) * kx + mpx) + 2)
        y0 = max(0, int(ys.min() * ky - mpx) - 2); y1 = min(self.H, int((ys.max() + 1) * ky + mpx) + 2)
        vx = cv2.resize(vx_s, (self.W, self.H), interpolation=cv2.INTER_LINEAR)[y0:y1, x0:x1]
        vy = cv2.resize(vy_s, (self.W, self.H), interpolation=cv2.INTER_LINEAR)[y0:y1, x0:x1]
        if self._grid_xy is None:
            gx, gy = np.meshgrid(np.arange(self.W, dtype=np.float32), np.arange(self.H, dtype=np.float32))
            self._grid_xy = (gx, gy)
        gx, gy = self._grid_xy[0][y0:y1, x0:x1], self._grid_xy[1][y0:y1, x0:x1]
        # 2^levels taps over span x step: tap spacing <= ~1.5 px at the longest step in the box
        levels = max(1, min(9, int(math.ceil(math.log2(max(mpx * _SMEAR_SPAN, 3.0) / 1.5)))))
        src = acc  # full frame: taps may reach outside the box
        cur = np.ascontiguousarray(acc[y0:y1, x0:x1])
        for lv in range(levels):
            f = _SMEAR_SPAN * 0.5 ** (lv + 2)  # +-V/4, +-V/8, ... (x span): 2^levels taps over the step
            ox, oy = vx * f, vy * f
            if lv == 0:
                a = cv2.remap(src, gx + ox, gy + oy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                b = cv2.remap(src, gx - ox, gy - oy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            else:
                lx, ly = gx - x0 + ox, gy - y0 + oy
                a = cv2.remap(cur, lx, ly, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
                lx, ly = gx - x0 - ox, gy - y0 - oy
                b = cv2.remap(cur, lx, ly, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            cur = cv2.addWeighted(a, 0.5, b, 0.5, 0.0)
        acc[y0:y1, x0:x1] = cur

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
        L8 = None
        if self._split is not None:
            L8 = cv2.convertScaleAbs(cv2.transform(disp, self._luma, dst=self._m1), dst=self._u1, alpha=255.0)
            cv2.add(disp, cv2.LUT(cv2.merge([L8, L8, L8], dst=self._u3b), self._split, dst=self._b2), dst=disp)
        flash = getattr(self.film, "flash_at", None)
        if flash is not None and getattr(self.film, "defer_flash", False):
            rgb, fa = flash(i)
            if fa > 0.002:  # accent tint over the whole delivered frame (display space, ungraded)
                disp *= np.float32(1.0 - fa)
                disp += (np.asarray(rgb, np.float32) * np.float32(fa / 255.0)).reshape(1, 1, 3)
                L8 = None
        if use_grain and L8 is None:
            L8 = cv2.convertScaleAbs(cv2.transform(disp, self._luma, dst=self._m1), dst=self._u1, alpha=255.0)
        noise = self._field(self.dither, i, 0xD17, self._m2) if p.get("dither", True) else None
        if use_grain:
            pick = _hash32(i, 0x6A0, self.seed) % len(self.grain_fields)  # re-seeded per frame
            field = self._field(self.grain_fields[pick], i, 0x6A1, self._m3)
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
