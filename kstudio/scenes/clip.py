"""Footage: the `clip` scene plays a local video file inside any mode.

The clip is trimmed (`in` / `out`, seconds or beats), fitted to the frame or a box (`cover` with a
focus point, or `contain` over a blurred copy of itself), optionally graded, blurred and faded, and
every scene listed in `over` (title, callout, karaoke, counter ...) is drawn on top of the footage
in its own time window.

Decoding is local and done once: ffmpeg trims, crops to the visible region and scales to the size
the frame needs (never upscaling), then the raw frames are written to a disk cache keyed by the
file (path, size, mtime), the trim, the target size and the look. Render workers share the cache
through a memory map, so a clip costs one decode per look however many processes draw it. Film
time maps to the nearest decoded frame, so any source frame rate plays at real speed.

`source: {"swap": {...}}` takes the footage from the optional swap backend (kstudio.swap): the
swapped file is used when it exists or can be made, otherwise the input video plays and a warning
is printed, so a missing backend never stops a render.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from typing import Any

import numpy as np
import skia

from .. import draw as D
from .. import ease as E
from .. import layout as L
from ..spec import SpecError, swap_source
from .base import Ctx, Cue, Scene, get_scene_class, register

T = L.TYPE
CACHE_VERSION = 1
FPS_CAP = 60.0
_RESERVED = ("type", "at", "dur", "transition", "transition_dur")


# --------------------------------------------------------------------------- probing and decoding

@dataclasses.dataclass(frozen=True)
class Probe:
    width: int
    height: int
    fps: float
    duration: float


def _rate(v: Any) -> float:
    try:
        if isinstance(v, str) and "/" in v:
            n, d = v.split("/", 1)
            return float(n) / float(d) if float(d) else 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def probe(path: str) -> Probe:
    """Display size (rotation applied), frame rate and duration of the first video stream."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format",
                        "-of", "json", path], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise ValueError(f"cannot read video {path}: {r.stderr.strip()[:200]}")
    info = json.loads(r.stdout or "{}")
    streams = info.get("streams") or []
    if not streams:
        raise ValueError(f"no video stream in {path}")
    st = streams[0]
    w, h = int(st.get("width", 0)), int(st.get("height", 0))
    rot = _rate((st.get("tags") or {}).get("rotate", 0))
    for sd in st.get("side_data_list") or []:
        if "rotation" in sd:
            rot = _rate(sd["rotation"])
    if int(round(abs(rot))) % 180 == 90:
        w, h = h, w
    fps = _rate(st.get("avg_frame_rate")) or _rate(st.get("r_frame_rate")) or 30.0
    dur = _rate(st.get("duration")) or _rate((info.get("format") or {}).get("duration"))
    if w <= 0 or h <= 0:
        raise ValueError(f"no picture size in {path}")
    return Probe(w, h, min(fps, FPS_CAP), dur)


@dataclasses.dataclass(frozen=True)
class DecodePlan:
    src: str
    t0: float               # source seconds of the first frame
    t1: float               # source seconds after the last frame
    fps: float
    crop: tuple[int, int, int, int]  # x, y, w, h in source pixels
    size: tuple[int, int]   # decoded w, h
    grade: str              # JSON of the grade spec ("null" = none)
    blur: float             # Gaussian sigma in decoded pixels

    def key(self) -> str:
        st = os.stat(self.src)
        blob = json.dumps([CACHE_VERSION, os.path.abspath(self.src), st.st_size, st.st_mtime_ns,
                           round(self.t0, 4), round(self.t1, 4), round(self.fps, 4), self.crop, self.size,
                           self.grade, round(self.blur, 3)])
        return hashlib.sha256(blob.encode()).hexdigest()[:20]


def cache_dir() -> str:
    return os.environ.get("KSTUDIO_CLIP_CACHE") or os.path.join(tempfile.gettempdir(), "kstudio-clips")


@contextlib.contextmanager
def _locked(path: str) -> Iterator[None]:
    """Exclusive inter-process lock on `path` (no-op where fcntl is unavailable)."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield
        return
    with open(path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _trim_cache(root: str, keep: str) -> None:
    """Drop the least recently used entries above $KSTUDIO_CLIP_CACHE_MB (default 1024)."""
    try:
        budget = float(os.environ.get("KSTUDIO_CLIP_CACHE_MB", "1024")) * 1e6
    except ValueError:
        budget = 1024e6
    entries = []
    for f in os.listdir(root):
        if f.endswith(".yuv") and not f.startswith(keep):
            p = os.path.join(root, f)
            with contextlib.suppress(OSError):
                st = os.stat(p)
                entries.append((st.st_atime, st.st_size, p))
    total = sum(e[1] for e in entries)
    with contextlib.suppress(OSError):
        total += os.path.getsize(os.path.join(root, keep + ".yuv"))
    for _, size, p in sorted(entries):
        if total <= budget:
            break
        for ext in (".yuv", ".json", ".lock"):
            with contextlib.suppress(OSError):
                os.remove(p[: -len(".yuv")] + ext)
        total -= size


def to_rgba(yuv: np.ndarray) -> np.ndarray:
    """One cached I420 frame (h * 3 / 2, w) -> RGBA (h, w, 4)."""
    import cv2
    return cv2.cvtColor(np.ascontiguousarray(yuv), cv2.COLOR_YUV2RGBA_I420)


def _look(frame: np.ndarray, grade: Any, blur: float) -> np.ndarray:
    """Apply the clip's own grade and blur to one I420 frame."""
    if grade is None and blur <= 0.05:
        return frame
    import cv2
    rgb = cv2.cvtColor(frame, cv2.COLOR_YUV2RGB_I420)
    if grade is not None:
        from ..grade import apply_display, resolve_grade
        rgb = (apply_display(resolve_grade(grade), rgb.astype(np.float32) / 255.0) * 255.0 + 0.5).astype(np.uint8)
    if blur > 0.05:
        rgb = cv2.GaussianBlur(rgb, (0, 0), blur)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2YUV_I420)


def _ffmpeg_frames(plan: DecodePlan) -> Iterator[np.ndarray]:
    w, h = plan.size
    cx, cy, cw, ch = plan.crop
    # I420 at BT.601 limited range, the layout and matrix OpenCV converts back (a third of RGBA's size)
    vf = (f"fps={plan.fps:.6f},crop={cw}:{ch}:{cx}:{cy},"
          f"scale={w}:{h}:flags=area:out_color_matrix=bt601:out_range=tv,format=yuv420p")
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{plan.t0:.6f}", "-i", plan.src,
           "-t", f"{max(plan.t1 - plan.t0, 1e-3):.6f}", "-an", "-sn", "-dn", "-filter_threads", "1", "-vf", vf,
           "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"]
    size = w * h * 3 // 2
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise ValueError("ffmpeg is not installed") from None
    assert proc.stdout is not None and proc.stderr is not None
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h * 3 // 2, w)
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode(errors="replace")
        proc.stderr.close()
        if proc.wait() != 0 and err:
            raise ValueError(f"ffmpeg could not decode {plan.src}: {err.strip()[:200]}")


def decode(plan: DecodePlan) -> np.ndarray:
    """I420 frames (n, h * 3 / 2, w) uint8 for a plan, from the disk cache or decoded into it."""
    grade = json.loads(plan.grade)
    w, h = plan.size
    try:
        root = cache_dir()
        os.makedirs(root, exist_ok=True)
        key = plan.key()
        base = os.path.join(root, key)
        with _locked(base + ".lock"):
            meta = None
            with contextlib.suppress(OSError, ValueError):
                with open(base + ".json") as fh:
                    meta = json.load(fh)
            if not meta or not os.path.isfile(base + ".yuv"):
                tmp = f"{base}.{os.getpid()}.part"
                n = 0
                try:
                    with open(tmp, "wb") as out:
                        for fr in _ffmpeg_frames(plan):
                            out.write(np.ascontiguousarray(_look(fr, grade, plan.blur)).tobytes())
                            n += 1
                    if n == 0:
                        raise ValueError(f"no frames decoded from {plan.src} between {plan.t0:.2f}s and "
                                         f"{plan.t1:.2f}s")
                except BaseException:
                    with contextlib.suppress(OSError):
                        os.remove(tmp)
                    raise
                os.replace(tmp, base + ".yuv")
                meta = {"n": n, "w": w, "h": h, "fps": plan.fps, "src": plan.src}
                with open(base + ".json.part", "w") as fh:
                    json.dump(meta, fh)
                os.replace(base + ".json.part", base + ".json")
                _trim_cache(root, key)
            n = int(meta["n"])
            with contextlib.suppress(OSError):
                os.utime(base + ".yuv")
        return np.memmap(base + ".yuv", np.uint8, "r", shape=(n, h * 3 // 2, w))
    except OSError:
        # cache not writable: decode into memory for this process only
        frames = [_look(fr, grade, plan.blur) for fr in _ffmpeg_frames(plan)]
        if not frames:
            raise ValueError(f"no frames decoded from {plan.src}") from None
        return np.stack(frames)


def frame_index(src_t: float, t0: float, fps: float, n: int) -> int:
    """Nearest decoded frame to source time `src_t` (frame k is shown at t0 + k / fps)."""
    return max(0, min(n - 1, int(math.floor((src_t - t0) * fps + 0.5))))


# --------------------------------------------------------------------------- the scene

@register("clip")
class Clip(Scene):
    """Footage from a local video file: trim with `in`/`out` (seconds or beats, snapped with the
    grid), `fit` cover (with a `focus` point and a slow `zoom`) or contain, an optional `grade`,
    `blur` and `opacity`, a `scrim` that keeps type legible, an optional picture-in-picture `box`,
    and `over`: a list of scenes (title, callout, karaoke, counter ...) drawn on top of the footage.
    `end` = hold (freeze the last frame) or loop. `source` = {"swap": {...}} plays footage from the
    optional swap backend (see docs/SWAP.md) and falls back to the input video without it."""
    defaults = {"src": "", "source": None, "in": 0.0, "out": None, "speed": 1.0, "end": "hold",
                "fit": "cover", "focus": [0.5, 0.5], "zoom": [1.0, 1.06], "ease": "move",
                "opacity": 1.0, "grade": None, "blur": 0.0, "scrim": 0.45, "box": None, "radius": 24,
                "backdrop": "blur", "sfx": None, "over": []}
    time_params = ("in", "out")

    def __init__(self, params: dict[str, Any], start: float, dur: float, tl: Any, mode_cfg: dict):
        super().__init__(params, start, dur, tl, mode_cfg)
        if self.p["fit"] not in ("cover", "contain"):
            raise SpecError(f'clip "fit" must be "cover" or "contain", got {self.p["fit"]!r}')
        if self.p["end"] not in ("hold", "loop"):
            raise SpecError(f'clip "end" must be "hold" or "loop", got {self.p["end"]!r}')
        self.swap = swap_source(self.p["source"])  # validates the shape early (paths resolve at draw time)
        self.children: list[tuple[Scene, float, bool]] = []  # (scene, offset, open-ended)
        over = self.p.get("over") or []
        if not isinstance(over, list):
            raise SpecError('clip "over" must be a list of scenes')
        for i, sc in enumerate(over):
            if not isinstance(sc, dict) or "type" not in sc:
                raise SpecError(f'clip "over" item #{i} must be a scene object with a "type"')
            if sc["type"] == "clip":
                raise SpecError('clip "over" cannot hold another clip')
            try:
                cls = get_scene_class(sc["type"])
            except KeyError as e:
                raise SpecError(str(e).strip("'\"")) from None
            at = max(0.0, self._dur(sc["at"])) if "at" in sc else 0.0
            open_end = "dur" not in sc
            cd = max(self.dur - at, 1e-3) if open_end else self.tl.resolve_dur(sc["dur"], start + at)
            cp = {k: v for k, v in sc.items() if k not in _RESERVED}
            cp.setdefault("id", f"{self.id}.over{i}_{sc['type']}")
            cp.setdefault("ground", self.ground)
            self.children.append((cls(cp, start + at, cd, tl, mode_cfg), at, open_end))
        self._frames: np.ndarray | None = None
        self._plan: DecodePlan | None = None
        self._err: str | None = None
        self._img: tuple[int, skia.Image] | None = None
        self._size: tuple[int, int] | None = None

    # -- timeline ---------------------------------------------------------
    def cues(self) -> list[Cue]:
        out = []
        if self.p["sfx"]:
            out.append(Cue("sfx", 0.0, data={"sound": self.p["sfx"]}))
        for ch, at, _ in self.children:
            out += [dataclasses.replace(c, at=c.at + at) for c in ch.cues()]
        return out

    def src_time(self, lt: float, t0: float, t1: float) -> float:
        """Source seconds shown at scene-local time `lt`."""
        d = max(lt, 0.0) * float(self.p["speed"])
        span = max(t1 - t0, 1e-6)
        if self.p["end"] == "loop":
            return t0 + math.fmod(d, span)
        return t0 + min(d, span)

    # -- footage ----------------------------------------------------------
    def _path(self, ctx: Ctx) -> str:
        if self.swap is not None:
            req = swap_source(self.p["source"], ctx.assets)
            assert req is not None
            from ..swap import resolve_clip_source
            return resolve_clip_source(req, ctx.assets)
        src = str(self.p["src"])
        return src if os.path.isabs(src) else os.path.join(ctx.assets, src)

    def _rect(self, ctx: Ctx) -> skia.Rect:
        box = self.p["box"]
        if box:
            x, y, w, h = (float(v) for v in box)
            return skia.Rect.MakeXYWH(x * ctx.W, y * ctx.H, w * ctx.W, h * ctx.H)
        bleed = 0.03 * max(ctx.W, ctx.H)  # covers camera drift at the frame edges
        return skia.Rect.MakeLTRB(-bleed, -bleed, ctx.W + bleed, ctx.H + bleed)

    def _zooms(self) -> tuple[float, float]:
        z = self.p["zoom"]
        z0, z1 = (float(z[0]), float(z[1])) if isinstance(z, (list, tuple)) else (float(z), float(z))
        return max(z0, 1.0), max(z1, 1.0)

    def plan(self, ctx: Ctx) -> DecodePlan:
        path = self._path(ctx)
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        pr = probe(path)
        rect = self._rect(ctx)
        rw, rh = rect.width(), rect.height()
        zmax = max(self._zooms())
        t0 = max(0.0, float(self.p["in"] or 0.0))
        t1 = float(self.p["out"]) if self.p["out"] is not None else (pr.duration or t0 + self.dur)
        if pr.duration:
            t1 = min(t1, pr.duration)
        if t1 <= t0:
            raise ValueError(f'clip "in" ({t0:.2f}s) is at or after "out" ({t1:.2f}s) of {self.p["src"]}')
        if self.p["end"] == "hold":  # decode only what the scene can show
            t1 = min(t1, t0 + max(self.dur, 0.0) * abs(float(self.p["speed"])) + 2.0 / pr.fps)
        if self.p["fit"] == "cover":
            k = max(rw / pr.width, rh / pr.height)
            cw, ch = min(pr.width, int(math.ceil(rw / k))), min(pr.height, int(math.ceil(rh / k)))
            fx, fy = (float(v) for v in self.p["focus"])
            cx, cy = int(round((pr.width - cw) * E.clamp(fx))), int(round((pr.height - ch) * E.clamp(fy)))
            s = min(1.0, k * zmax)
        else:
            cw, ch, cx, cy = pr.width, pr.height, 0, 0
            s = min(1.0, min(rw / pr.width, rh / pr.height) * zmax)
        w, h = max(2, int(round(cw * s / 2)) * 2), max(2, int(round(ch * s / 2)) * 2)
        blur = float(self.p["blur"] or 0.0) * ctx.s * (w / max(rw, 1.0))
        return DecodePlan(path, t0, t1, pr.fps, (cx, cy, cw, ch), (w, h),
                          json.dumps(self.p["grade"], sort_keys=True), blur)

    def frames(self, ctx: Ctx) -> np.ndarray | None:
        size = (ctx.W, ctx.H)
        if self._size != size:  # a new frame size (preview vs render) needs its own decode
            self._size, self._frames, self._err, self._img = size, None, None, None
        if self._frames is None and self._err is None:
            try:
                self._plan = self.plan(ctx)
                self._frames = decode(self._plan)
            except FileNotFoundError as e:
                self._err = f"missing footage: {os.path.basename(str(e))}"
            except (ValueError, OSError, subprocess.SubprocessError) as e:
                self._err = str(e)
                print(f"kstudio: clip {self.id}: {e}", file=sys.stderr)
        return self._frames

    def image_at(self, ctx: Ctx, lt: float) -> skia.Image | None:
        fr = self.frames(ctx)
        if fr is None or self._plan is None:
            return None
        pl = self._plan
        k = frame_index(self.src_time(lt, pl.t0, pl.t1), pl.t0, pl.fps, len(fr))
        if self._img is None or self._img[0] != k:
            self._img = (k, skia.Image.fromarray(to_rgba(fr[k]), colorType=skia.ColorType.kRGBA_8888_ColorType))
        return self._img[1]

    # -- drawing ----------------------------------------------------------
    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        rect = self._rect(ctx)
        img = self.image_at(ctx, ctx.lt)
        op = E.clamp(float(self.p["opacity"]))
        c.save()
        if self.p["box"]:
            r = float(self.p["radius"]) * ctx.s
            c.clipRRect(skia.RRect.MakeRectXY(rect, r, r), skia.ClipOp.kIntersect, True)
        else:
            c.clipRect(rect)
        if img is None:
            g = L.grid(ctx)
            D.draw_text(c, self._err or "missing footage", g.cx, ctx.cy, T["meta"].font(ctx), ctx.pal("neg"),
                        align="center")
        else:
            u = E.resolve(self.p["ease"])(E.clamp(ctx.lt / max(self.dur, 1e-3)))
            z0, z1 = self._zooms()
            self._draw_frame(ctx, img, rect, E.lerp(z0, z1, u), op)
        c.restore()
        if self.p["scrim"]:
            a = float(self.p["scrim"]) * op
            sh = D.linear_grad(0, rect.top(), 0, rect.bottom(),
                               [ctx.pal("bg", a * 0.7), ctx.pal("bg", a * 0.25), ctx.pal("bg", a)], [0.0, 0.45, 1.0])
            c.drawRect(rect, D.paint(0, shader=sh))
        for ch, at, open_end in self.children:
            if open_end:
                ch.dur = max(self.dur - at, 1e-3)
            lt = ctx.lt - at
            if lt < 0 or lt > ch.dur:
                continue
            cctx = dataclasses.replace(ctx, lt=lt, start=self.start + at, dur=ch.dur, scene=ch)
            c.save()
            ch.draw(cctx)
            c.restore()

    def _draw_frame(self, ctx: Ctx, img: skia.Image, rect: skia.Rect, zoom: float, op: float) -> None:
        c = ctx.c
        iw, ih = img.width(), img.height()
        rw, rh = rect.width(), rect.height()
        if self.p["fit"] == "cover":
            k = max(rw / iw, rh / ih) * zoom
            fx, fy = (E.clamp(float(v)) for v in self.p["focus"])
            ax, ay = rect.left() + rw * fx, rect.top() + rh * fy  # zoom about the focus point
            w, h = iw * k, ih * k
            dst = skia.Rect.MakeXYWH(ax - w * fx, ay - h * fy, w, h)
            c.drawImageRect(img, dst, D.SAMPLING, D.paint(0xFFFFFFFF, alpha=op))
            return
        if self.p["backdrop"] == "blur":
            k = max(rw / iw, rh / ih)
            w, h = iw * k, ih * k
            p = D.paint(0xFFFFFFFF, alpha=op * 0.85)
            p.setImageFilter(skia.ImageFilters.Blur(24 * ctx.s, 24 * ctx.s, skia.TileMode.kClamp))
            c.drawImageRect(img, skia.Rect.MakeXYWH(rect.centerX() - w / 2, rect.centerY() - h / 2, w, h),
                            D.SAMPLING, p)
            c.drawRect(rect, D.paint(ctx.pal("bg", 0.35 * op)))
        k = min(rw / iw, rh / ih) * zoom
        w, h = iw * k, ih * k
        c.drawImageRect(img, skia.Rect.MakeXYWH(rect.centerX() - w / 2, rect.centerY() - h / 2, w, h),
                        D.SAMPLING, D.paint(0xFFFFFFFF, alpha=op))
