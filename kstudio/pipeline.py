"""End-to-end build: spec -> (beat analysis) -> parallel chunked video -> soundtrack mix ->
mux -> audit -> report.json. Everything lands in the output folder; intermediate chunks live in
<out>/.kstudio_work/<name>/ so an interrupted render resumes where it stopped."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Any

from . import __version__
from .audio.mix import mix_film
from .audit import audit, probe
from .beats import BeatGrid, analyse_file
from .film import Film
from .presets import make_output
from .render import RenderLog, mux, render_video, which_ffmpeg
from .resources import auto_workers


def beat_grid_for(spec: dict[str, Any]) -> BeatGrid | None:
    m = spec.get("music") or {}
    f = m.get("file")
    if not f or m.get("sync", True) is False or "bpm" in m:
        return None
    path = f if os.path.isabs(f) else os.path.join(spec.get("_dir", "."), f)
    return analyse_file(path)


def add_mux_checks(a: dict[str, Any], mx: dict[str, Any]) -> None:
    """Fold the mux verification (delivered fps/frame count vs the video track, post-AAC true peak
    vs the mux ceiling) into an audit result as two extra checks and recompute its pass flag."""
    vi, vo = mx["video_in"], mx["video_out"]
    a["checks"]["mux_fps"] = {"ok": bool(mx["fps_ok"]),
                              "value": f"{vo.get('frames')} fr r {vo.get('r_fps')} / n/d {vo.get('measured_fps')}",
                              "want": f"{vi.get('frames')} fr @ {vi.get('r_fps')}"}
    a["checks"]["mux_true_peak"] = {"ok": bool(mx["true_peak_ok"]), "value": mx["loudness"].get("true_peak"),
                                    "want": f"<= {mx['ceiling_dbtp']} dBTP after {len(mx['attempts'])} encode(s)"}
    a["pass"] = all(c["ok"] for c in a["checks"].values())


def delivery_summary(mx: dict[str, Any], p: dict[str, Any], a: dict[str, Any] | None) -> dict[str, Any]:
    """Post-mux numbers of the delivered file in one place: loudness, true peak, rates, A/V gap."""
    lo = (a or {}).get("loudness") or mx["loudness"]
    vo = mx["video_out"]
    ad = (p.get("audio") or {}).get("duration")
    vd = p.get("video_duration")
    return {"lufs": lo.get("lufs"), "true_peak": lo.get("true_peak"), "true_peak_ok": mx["true_peak_ok"],
            "mux_encodes": len(mx["attempts"]), "mux_gain_db": mx["gain_db"],
            "r_fps": vo.get("r_fps"), "avg_fps": vo.get("avg_fps"), "measured_fps": vo.get("measured_fps"),
            "frames": vo.get("frames"), "fps_ok": mx["fps_ok"],
            "av_gap": round(ad - vd, 4) if ad is not None and vd is not None else None,
            "black_frames": (a or {}).get("motion", {}).get("black_frames")}


def build(spec: dict[str, Any], out_path: str, preset: str = "reel", fps: int = 30, quality: str = "standard",
          k4: bool = False, workers: str | int = "auto", fresh: bool = False, do_audit: bool = True,
          chunk_seconds: float = 2.0, on_progress: Callable[[dict], None] | None = None,
          say: Callable[[str], None] = print, snap: bool | str | None = None) -> dict[str, Any]:
    """Render a spec. ``snap`` (True/"beat"/"8th"/"16th"/"bar") turns on beat auto-snap for scene
    lengths, starts and seconds-valued timings; off-grid scenes are reported by the pre-render lint."""
    which_ffmpeg()
    t_start = time.time()
    started = datetime.now().astimezone()
    out = make_output(preset, fps, quality, k4)
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    name = os.path.splitext(os.path.basename(out_path))[0]
    work = os.path.join(out_dir, ".kstudio_work", name)
    os.makedirs(work, exist_ok=True)
    if fresh:
        for f in os.listdir(work):
            os.remove(os.path.join(work, f))
    log = RenderLog(os.path.join(out_dir, f"{name}.render.log"))
    grid = beat_grid_for(spec)
    if grid:
        say(f"beat grid from music file: {grid.bpm:.2f} BPM, offset {grid.offset:.3f}s, confidence {grid.confidence:.2f}")
        log.write("beats", **grid.as_dict())
    spec = dict(spec)
    spec["_fps"] = out.fps  # transition flashes and windows are sized in frames of the output rate
    if snap:
        spec["snap"] = snap
    film = Film(spec, out.width, out.height, beat_grid=grid)
    lint = film.lint()
    for w in lint:
        if w["level"] == "warn":
            say(f"lint: {w['where']}: {w['msg']}")
    if lint:
        log.write("lint", items=lint)
    n_frames = int(round(film.duration * out.fps))
    chunks = -(-n_frames // int(chunk_seconds * out.fps))
    if workers == "auto":
        n_workers, winfo = auto_workers(out.width, out.height, out.samples, jobs=chunks)
    else:
        n_workers, winfo = int(workers), {"workers": int(workers), "requested": True}
    say(f"{film.mode}: {film.duration:.2f}s, {n_frames} frames @ {out.width}x{out.height} {out.fps}fps "
        f"[{out.quality}], {chunks} chunks, {n_workers} workers ({winfo})")
    log.write("plan", version=__version__, mode=film.mode, duration=film.duration, frames=n_frames,
              output=asdict(out), workers=winfo, started=started.isoformat(timespec="seconds"))
    video = os.path.join(work, "video.mp4")
    fp = film.fingerprint([asdict(out), chunk_seconds])
    t0 = time.time()
    rv = render_video(spec, out, video, work, fp, n_frames, n_workers,
                      grid=asdict(grid) if grid else None, chunk_seconds=chunk_seconds, log=log, on_progress=on_progress)
    video_s = time.time() - t0
    t1 = time.time()
    wav = os.path.join(work, "mix.wav")
    target = float(film.cfg.get("audio", {}).get("lufs", spec.get("lufs", -14.0)))
    am = mix_film(film, wav, target_lufs=target)
    audio_s = time.time() - t1
    log.write("audio_done", seconds=round(audio_s, 2), final=am["final"], gain_db=am["gain_db"], sfx=am["sfx_count"],
              music=am["music"])
    mx = mux(video, wav, out_path)
    log.write("mux", attempts=mx["attempts"], gain_db=mx["gain_db"], true_peak_ok=mx["true_peak_ok"],
              fps_ok=mx["fps_ok"], loudness=mx["loudness"])
    if not mx["fps_ok"]:
        say(f"mux: delivered frame rate/count drifted: {mx['video_out']} (video track {mx['video_in']})")
    if not mx["true_peak_ok"]:
        say(f"mux: true peak {mx['loudness'].get('true_peak')} dBTP over the {mx['ceiling_dbtp']} dBTP ceiling "
            f"after {len(mx['attempts'])} encodes")
    total = time.time() - t_start
    finished = datetime.now().astimezone()
    report: dict[str, Any] = {
        "output": os.path.abspath(out_path), "version": __version__, "mode": film.mode,
        "duration": round(film.duration, 3), "frames": n_frames, "size": [out.width, out.height], "fps": out.fps,
        "quality": out.quality, "workers": winfo, "render": rv, "video_seconds": round(video_s, 2),
        "audio_seconds": round(audio_s, 2), "total_seconds": round(total, 2),
        "started": started.isoformat(timespec="seconds"), "finished": finished.isoformat(timespec="seconds"),
        "audio": {k: v for k, v in am.items() if k != "sfx"}, "timeline": film.summary(),
        "lint": lint, "mux": {k: v for k, v in mx.items() if k != "out"},
    }
    if do_audit:
        th = dict(film.cfg.get("audit", {}))
        th.setdefault("lufs", target)
        a = audit(out_path, {"duration": film.duration, "width": out.width, "height": out.height, "fps": out.fps,
                             "level": out.level}, th)
        add_mux_checks(a, mx)
        report["audit"] = a
        log.write("audit", passed=a["pass"], checks={k: v["ok"] for k, v in a["checks"].items()})
        p = a["probe"]
    else:
        p = probe(out_path)
    report["delivery"] = delivery_summary(mx, p, report.get("audit"))
    report["pass"] = report["audit"]["pass"] if do_audit else bool(mx["fps_ok"] and mx["true_peak_ok"])
    with open(os.path.join(out_dir, f"{name}.report.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    log.write("done", seconds=round(total, 2), output=os.path.abspath(out_path))
    return report
