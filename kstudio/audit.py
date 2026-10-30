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

STILL_THRESHOLD = 0.6     # mean abs luma diff (0-255) at 1/8 scale below which a frame counts as "still"
BLACK_THRESHOLD = 10.0    # mean luma below which a frame counts as black


def probe(path: str) -> dict[str, Any]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    v: dict[str, Any] = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    num, _, den = v.get("avg_frame_rate", "0/1").partition("/")
    lvl = v.get("level")
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
    w, h = max(16, width // 8) // 2 * 2, max(16, height // 8) // 2 * 2
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={w}:{h}:flags=area,format=gray",
                             "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    diffs, lum = [], []
    prev = None
    fsz = w * h
    while True:
        buf = proc.stdout.read(fsz)  # type: ignore[union-attr]
        if len(buf) < fsz:
            break
        f = np.frombuffer(buf, np.uint8).astype(np.float32)
        lum.append(float(f.mean()))
        if prev is not None:
            diffs.append(float(np.abs(f - prev).mean()))
        prev = f
    proc.wait()
    d = np.array(diffs) if diffs else np.zeros(1)
    still = d < STILL_THRESHOLD
    longest = run = 0
    for s in still:
        run = run + 1 if s else 0
        longest = max(longest, run)
    lum_a = np.array(lum) if lum else np.zeros(1)
    return {"frames_decoded": len(lum), "motion_ratio": round(float(1 - still.mean()), 3),
            "still_ratio": round(float(still.mean()), 3), "longest_hold_s": round(longest / fps, 2) if fps else None,
            "mean_motion": round(float(d.mean()), 2), "black_frames": int((lum_a < BLACK_THRESHOLD).sum()),
            "first_frame_black": bool(lum_a[0] < BLACK_THRESHOLD)}


def audit(path: str, expect: dict[str, Any] | None = None, thresholds: dict[str, Any] | None = None) -> dict[str, Any]:
    from .audio.mix import measure
    expect = expect or {}
    th = {"min_motion": 0.6, "max_hold": 2.0, "lufs": -14.0, "lufs_tol": 1.0, "max_true_peak": -1.0,
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
    if r.get("loudness"):
        lo = r["loudness"]
        lines.append(f"  loudness {lo['lufs']} LUFS, true peak {lo['true_peak']} dBTP, LRA {lo['lra']} LU "
                     f"(measured on the delivered file, {lo.get('meter', 'loudnorm')})")
    if p.get("r_fps") is not None:
        lines.append(f"  video stream: {p['video_frames']} frames / {p['video_duration']} s; r_frame_rate {p['r_fps']}, "
                     f"avg_frame_rate {p['avg_fps']}, frames/duration {p['measured_fps']}")
    return "\n".join(lines)
