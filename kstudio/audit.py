"""Delivery audit for a finished video: motion/still ratio, longest hold, black frames,
loudness (LUFS / true peak / LRA), codec profile & level, pixel format, duration, size.

Loudness and true peak are measured on the delivered file itself (the decoded AAC track, ffmpeg
EBU R128), so codec overshoot is caught. The frame rate is checked three ways (r_frame_rate,
avg_frame_rate and frames / stream duration) and must match the requested rate exactly; without
an expected rate the three must agree with each other."""
from __future__ import annotations

import json
import subprocess
from typing import Any

import numpy as np

from .exitcodes import AuditFailed

STILL_THRESHOLD = 0.6     # raw metric: mean abs luma diff (0-255) at 1/8 scale below which a frame is "still"
# Motion is judged on a grain-free basis. Film grain is re-seeded every frame, so on the delivered file
# every frame differs from the last by ~2-3 levels at 1/8 scale (well over STILL_THRESHOLD) and the raw
# ratio reads 1.0 for any graded render. Grain is independent from frame to frame, so it adds the same
# 2 sigma^2 to the squared 1-frame and 2-frame differences, while a steady move adds m^2 and 4 m^2:
# m = sqrt((D2^2 - (D1^2 + D0^2) / 2) / 3) is the motion with the noise cancelled, without knowing sigma.
# MOTION_GATE is the m (RMS, 0-255 luma at 1/8 scale) below which a frame counts as still. It was
# calibrated against the designed motion (grain off, pre-encode, the raw metric): kinetic 0.982 vs
# 0.987, explainer 0.493 vs 0.502 and 0.542 vs 0.528 (grain-free encode), promo 0.601 vs 0.646;
# 1.5 over-reads the explainer by 0.05-0.08 and 2.0 under-reads it by 0.07-0.09.
MOTION_GATE = 1.75
BLACK_THRESHOLD = 10.0    # mean luma below which a frame counts as black

# Reference-v2 preserves existing default-preset/master grids, not a claim of
# archived universal perceptual calibration. Unsupported geometry fails closed.
MOTION_REFERENCE_SHORT_EDGE = 1080
AUDIT_MIN_SHORT_EDGE = 144
AUDIT_MAX_INPUT_EDGE = 4096
AUDIT_MAX_INPUT_PIXELS = 9_000_000
AUDIT_MAX_ANALYSIS_EDGE = 512
AUDIT_MAX_ANALYSIS_PIXELS = 65_536
MOTION_BASIS_VERSION = "reference-1080-v2"


def motion_analysis_size(width: int, height: int) -> tuple[int, int]:
    """Bounded, no-upscale reference grid; reject unsupported media, never waive it.

    Supported coded dimensions: short edge >=144, long edge <=4096, <=9M pixels.
    The normalized even grid must fit the source, <=512 per axis and <=65536 pixels.
    Limits deliberately reject extreme ratios rather than changing the motion bar.
    """
    if (isinstance(width, bool) or isinstance(height, bool)
            or not isinstance(width, int) or not isinstance(height, int)
            or min(width, height) < AUDIT_MIN_SHORT_EDGE
            or max(width, height) > AUDIT_MAX_INPUT_EDGE
            or width * height > AUDIT_MAX_INPUT_PIXELS):
        raise AuditFailed("unsupported audit input dimensions", reason="unsupported_geometry",
                          width=width, height=height, basis=MOTION_BASIS_VERSION)
    short = min(width, height)
    w = (width * MOTION_REFERENCE_SHORT_EDGE // short // 8) // 2 * 2
    h = (height * MOTION_REFERENCE_SHORT_EDGE // short // 8) // 2 * 2
    if (min(w, h) < 16 or w > width or h > height
            or max(w, h) > AUDIT_MAX_ANALYSIS_EDGE
            or w * h > AUDIT_MAX_ANALYSIS_PIXELS):
        raise AuditFailed("unsupported audit aspect ratio or analysis allocation", reason="unsupported_geometry",
                          width=width, height=height, analysis_size=[w, h], basis=MOTION_BASIS_VERSION)
    return w, h


def _validate_audit_video(video: dict[str, Any]) -> None:
    """Reject non-square-pixel or rotated inputs before decoder/timing work.

    Absent/N/A/0:1 SAR is treated as unspecified square pixels, as FFmpeg does.
    Explicit non-unit SAR and nonidentity rotation need separate normalization.
    """
    motion_analysis_size(video.get("width", 0), video.get("height", 0))
    if video.get("sample_aspect_ratio") not in (None, "N/A", "0:1", "1:1"):
        raise AuditFailed("unsupported audit sample aspect ratio", reason="unsupported_display_geometry",
                          sample_aspect_ratio=video.get("sample_aspect_ratio"), basis=MOTION_BASIS_VERSION)
    tags = video.get("tags", {})
    side_data = video.get("side_data_list", [])
    if tags is None:
        tags = {}
    if side_data is None:
        side_data = []
    if (not isinstance(tags, dict) or not isinstance(side_data, list)
            or any(not isinstance(item, dict) for item in side_data)):
        raise AuditFailed("unsupported audit display metadata", reason="unsupported_display_geometry",
                          basis=MOTION_BASIS_VERSION)
    rotations = [tags.get("rotate", 0)]
    rotations.extend(item.get("rotation", 0) for item in side_data)
    try:
        identity = all(float(rotation) % 360 == 0 for rotation in rotations)
    except (TypeError, ValueError):
        identity = False
    if not identity:
        raise AuditFailed("unsupported audit rotation", reason="unsupported_display_geometry",
                          rotations=rotations, basis=MOTION_BASIS_VERSION)


def probe(path: str) -> dict[str, Any]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    v: dict[str, Any] = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    num, _, den = v.get("avg_frame_rate", "0/1").partition("/")
    lvl = v.get("level")
    _validate_audit_video(v)
    from .render import video_timing
    vt = video_timing(path) if v else {}
    return {
        "width": v.get("width"), "height": v.get("height"),
        "r_fps": vt.get("r_fps"), "avg_fps": vt.get("avg_fps"), "measured_fps": vt.get("measured_fps"),
        "video_frames": vt.get("frames"), "video_duration": vt.get("video_duration"),
        "fps": round(float(num) / float(den or 1), 3) if float(den or 1) else 0.0,
        "codec": v.get("codec_name"), "profile": v.get("profile"),
        "level": f"{lvl // 10}.{lvl % 10}" if isinstance(lvl, int) else None,
        "pix_fmt": v.get("pix_fmt"), "frames": int(v.get("nb_frames", 0) or 0),
        "duration": round(float(d["format"].get("duration", 0.0)), 3),
        "size_mb": round(int(d["format"].get("size", 0)) / 1e6, 2),
        "bitrate_kbps": round(int(d["format"].get("bit_rate", 0)) / 1e3),
        "audio": None if a is None else {"codec": a.get("codec_name"), "sample_rate": int(a.get("sample_rate", 0)),
                                         "channels": a.get("channels"),
                                         "duration": round(float(a.get("duration") or 0.0), 4)},
    }


def motion_stats(path: str, width: int, height: int, fps: float) -> dict[str, Any]:
    """Per-frame motion on the bounded reference grid (see MOTION_GATE): the denoised motion ratio and
    longest hold, plus the raw grain-inclusive ratio, mean luma diff and black frames."""
    w, h = motion_analysis_size(width, height)
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-noautorotate", "-i", path, "-vf", f"scale={w}:{h}:flags=area,format=gray",
                             "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    diffs: list[float] = []
    sq: list[float] = []    # mean squared 1-frame difference, frame k-1 -> k
    sq2: list[float] = []   # mean squared 2-frame difference, frame k-2 -> k
    lum: list[float] = []
    win: list[np.ndarray] = []
    fsz = w * h
    while True:
        buf = proc.stdout.read(fsz)  # type: ignore[union-attr]
        if len(buf) < fsz:
            break
        f = np.frombuffer(buf, np.uint8).astype(np.float32)
        lum.append(float(f.mean()))
        if win:
            d = f - win[-1]
            diffs.append(float(np.abs(d).mean()))
            sq.append(float(np.dot(d, d)) / fsz)
        if len(win) == 2:
            d2 = f - win[0]
            sq2.append(float(np.dot(d2, d2)) / fsz)
        win = (win + [f])[-2:]
    returncode = proc.wait()
    if returncode != 0 or len(lum) < 3 or buf:
        raise AuditFailed("audit decode failed, incomplete, or shorter than three frames",
                          reason="invalid_decode", returncode=returncode, frames_decoded=len(lum))
    d = np.array(diffs) if diffs else np.zeros(1)
    m = denoised_motion(sq, sq2) if diffs else np.zeros(1)
    still = m < MOTION_GATE

    def longest_run(flags: np.ndarray) -> int:
        longest = run = 0
        for s in flags:
            run = run + 1 if s else 0
            longest = max(longest, run)
        return longest

    raw_still = d < STILL_THRESHOLD
    lum_a = np.array(lum) if lum else np.zeros(1)
    return {"frames_decoded": len(lum), "motion_ratio": round(float(1 - still.mean()), 3),
            "still_ratio": round(float(still.mean()), 3),
            "longest_hold_s": round(longest_run(still) / fps, 2) if fps else None,
            "motion_basis": f"denoised (two-lag, gate {MOTION_GATE}, {MOTION_BASIS_VERSION})",
            "motion_analysis_size": [w, h],
            "black_basis": f"mean luma < {BLACK_THRESHOLD} on {MOTION_BASIS_VERSION}",
            "motion_ratio_raw": round(float(1 - raw_still.mean()), 3),
            "longest_hold_raw_s": round(longest_run(raw_still) / fps, 2) if fps else None,
            "mean_motion": round(float(d.mean()), 2), "black_frames": int((lum_a < BLACK_THRESHOLD).sum()),
            "first_frame_black": bool(lum_a[0] < BLACK_THRESHOLD)}


def denoised_motion(sq: list[float], sq2: list[float]) -> np.ndarray:
    """Noise-cancelled motion per frame step. ``sq[k]`` = mean squared difference of frames k -> k+1,
    ``sq2[k]`` = frames k -> k+2. Step k uses the triple (k-1, k, k+1), the first step (0, 1, 2)."""
    n = len(sq)
    if n < 2 or not sq2:
        return np.sqrt(np.asarray(sq, np.float64)) if n else np.zeros(1)
    out = np.empty(n)
    for k in range(n):
        j = min(max(k - 1, 0), len(sq2) - 1)       # 2-frame span j -> j+2 covering step k
        e = (sq2[j] - 0.5 * (sq[j] + sq[j + 1])) / 3.0
        out[k] = np.sqrt(max(e, 0.0))
    return out


def audit(path: str, expect: dict[str, Any] | None = None, thresholds: dict[str, Any] | None = None) -> dict[str, Any]:
    from .audio.mix import measure
    expect = expect or {}
    th = {"min_motion": 0.6, "max_hold": 2.0, "lufs": -14.0, "lufs_tol": 1.0, "max_true_peak": -1.3,
          "max_black_frames": 0, "fps_tol": 0.01}
    th.update(thresholds or {})
    p = probe(path)
    m = motion_stats(path, p["width"], p["height"], p["fps"])
    loud = measure(path) if p["audio"] else None
    checks: dict[str, dict[str, Any]] = {}

    def check(name: str, ok: bool, value: Any, want: Any) -> None:
        checks[name] = {"ok": bool(ok), "value": value, "want": want}

    check("motion_ratio", m["motion_ratio"] >= th["min_motion"], m["motion_ratio"], f">= {th['min_motion']}")
    check("longest_hold", (m["longest_hold_s"] or 0) <= th["max_hold"], m["longest_hold_s"], f"<= {th['max_hold']} s")
    check("first_frame", not m["first_frame_black"], "black" if m["first_frame_black"] else "content", "not black")
    check("codec", p["codec"] == "h264" and p["profile"] == "High", f"{p['codec']} {p['profile']}", "h264 High")
    check("pix_fmt", p["pix_fmt"] == "yuv420p", p["pix_fmt"], "yuv420p")
    if p["audio"]:
        check("audio", p["audio"]["codec"] == "aac" and p["audio"]["sample_rate"] == 48000,
              f"{p['audio']['codec']} {p['audio']['sample_rate']}", "aac 48000")
    else:
        check("audio", False, None, "aac 48000")
    if loud:
        check("loudness", abs(loud["lufs"] - th["lufs"]) <= th["lufs_tol"], loud["lufs"], f"{th['lufs']} ±{th['lufs_tol']} LUFS")
        check("true_peak", loud["true_peak"] <= th["max_true_peak"], loud["true_peak"], f"<= {th['max_true_peak']} dBTP")
    check("black_frames", m["black_frames"] <= th["max_black_frames"], m["black_frames"], f"<= {th['max_black_frames']}")
    if "duration" in expect:
        check("duration", abs(p["duration"] - expect["duration"]) <= 1.5 / max(p["fps"], 1) + 0.05, p["duration"],
              f"{round(expect['duration'], 3)} s")
    if "width" in expect:
        check("resolution", (p["width"], p["height"]) == (expect["width"], expect["height"]), f"{p['width']}x{p['height']}",
              f"{expect['width']}x{expect['height']}")
    if p["audio"] and p["audio"].get("duration") and p.get("video_duration"):
        gap = round(p["audio"]["duration"] - p["video_duration"], 4)
        tol = max(0.03, 1.5 / max(p["r_fps"] or 30.0, 1.0))
        check("av_length", abs(gap) <= tol, f"audio-video {gap:+.3f} s", f"|gap| <= {tol:.3f} s")
    rates = [p["r_fps"] or 0.0, p["avg_fps"] or 0.0, p["measured_fps"] or 0.0]
    shown = f"r {rates[0]:g} / avg {rates[1]:g} / n/d {rates[2]:g}"
    if "fps" in expect:
        want = float(expect["fps"])
        check("fps", all(abs(r - want) < th["fps_tol"] for r in rates), shown, want)
        if "duration" in expect and "frames" not in expect:
            n_want = int(round(float(expect["duration"]) * want))
            check("frames", abs((p["video_frames"] or 0) - n_want) <= 1, p["video_frames"], n_want)
    else:
        check("fps", rates[0] > 0 and all(abs(r - rates[0]) < th["fps_tol"] for r in rates), shown, "r = avg = n/d")
    if "frames" in expect:
        n_want = int(expect["frames"])
        check("frames", abs((p["video_frames"] or 0) - n_want) <= 1, p["video_frames"], n_want)
    if "level" in expect:
        check("level", p["level"] == expect["level"], p["level"], expect["level"])
    return {"file": path, "probe": p, "motion": m, "loudness": loud, "checks": checks,
            "pass": all(c["ok"] for c in checks.values())}


def format_report(r: dict[str, Any]) -> str:
    lines = [f"audit: {r['file']}  ->  {'PASS' if r['pass'] else 'FAIL'}"]
    for k, c in r["checks"].items():
        lines.append(f"  [{'ok' if c['ok'] else '!!'}] {k:<13} {str(c['value']):<18} want {c['want']}")
    p, m = r["probe"], r["motion"]
    lines.append(f"  {p['width']}x{p['height']} @ {p['fps']} fps, {p['duration']} s, {p['size_mb']} MB, "
                 f"level {p['level']}, {p['bitrate_kbps']} kbps; mean motion {m['mean_motion']}, black frames {m['black_frames']}")
    if "motion_ratio_raw" in m:
        lines.append(f"  motion {m['motion_basis']}: {m['motion_ratio']} (raw, grain included: {m['motion_ratio_raw']}, "
                     f"longest raw hold {m['longest_hold_raw_s']} s)")
    if r.get("loudness"):
        lo = r["loudness"]
        lines.append(f"  loudness {lo['lufs']} LUFS, true peak {lo['true_peak']} dBTP, LRA {lo['lra']} LU "
                     f"(measured on the delivered file, {lo.get('meter', 'loudnorm')})")
    if p.get("r_fps") is not None:
        lines.append(f"  video stream: {p['video_frames']} frames / {p['video_duration']} s; r_frame_rate {p['r_fps']}, "
                     f"avg_frame_rate {p['avg_fps']}, frames/duration {p['measured_fps']}")
    return "\n".join(lines)
