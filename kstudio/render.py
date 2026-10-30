"""Chunked, parallel, resumable renderer.

    frames [0, N) are split into fixed-size chunks (default 2 s). Each worker process
    rebuilds the Film from the spec (deterministic), renders its chunk frame by frame,
    and pipes raw RGB into its own ffmpeg/libx264 encoder, writing chunk_XXXX.ts.partial,
    renamed to .ts only after ffmpeg exits cleanly and the frame count is verified.

    A manifest (spec fingerprint + output settings) lives next to the chunks; re-running
    the same render skips every finished chunk, so an interrupted render resumes where
    it stopped. Chunks are concatenated losslessly (stream copy) at the end.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import queue
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import numpy as np

from .presets import Output


def ffprobe_frames(path: str) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                        "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return int(next(x for x in r.stdout.split() if x.strip()).split(",")[0])
    except (ValueError, StopIteration):
        return -1


# Rate control for chunked encodes. Every 2 s chunk is a separate x264 run, and x264 starts each run
# with the VBV buffer `vbv-init` full; with the old bufsize = 2 x maxrate and the default init of 0.9
# every chunk could burst ~1.8 s of maxrate into its first second, so the concatenated stream ran at
# 1.74 x maxrate over 1 s windows. A half-second buffer that starts 30% full, against a ceiling 5%
# under the level's maxrate, keeps the joined stream inside maxrate x 1.1 (measured 1.06-1.09).
VBV_RATE = 0.95      # x264 -maxrate as a fraction of Output.maxrate_kbps
VBV_BUFFER_S = 0.5   # -bufsize in seconds of the nominal maxrate
VBV_INIT = 0.3       # initial buffer fill of each chunk


def encoder_cmd(out: Output, path: str, threads: int = 2) -> list[str]:
    rate = int(out.maxrate_kbps * VBV_RATE)
    buf = int(out.maxrate_kbps * VBV_BUFFER_S)
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out.width}x{out.height}", "-r", str(out.fps), "-i", "-",
            "-vf", "scale=out_color_matrix=bt709:out_range=tv",
            "-c:v", "libx264", "-preset", out.x264_preset, "-crf", str(out.crf),
            "-maxrate", f"{rate}k", "-bufsize", f"{buf}k", "-x264-params", f"vbv-init={VBV_INIT}",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level:v", out.level,
            "-g", str(out.fps * 2), "-bf", "2", "-threads", str(threads),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            "-f", "mpegts", path]


def plan_chunks(n_frames: int, fps: int, chunk_seconds: float = 2.0) -> list[tuple[int, int]]:
    size = max(1, int(round(fps * chunk_seconds)))
    return [(a, min(a + size, n_frames)) for a in range(0, n_frames, size)]


# ---------------------------------------------------------------------- worker

def _worker(wid: int, spec: dict, out_d: dict, grid: dict | None, post: dict | None, work_dir: str,
            tasks: mp.Queue, results: mp.Queue) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        from .beats import BeatGrid
        from .compositor import Compositor
        from .film import Film
        out = Output(**out_d)
        bg = BeatGrid(**grid) if grid else None
        film = Film(spec, out.width, out.height, beat_grid=bg)
        comp = Compositor(film, out, post)
        threads = max(1, min(4, (os.cpu_count() or 2) // max(1, int(os.environ.get("KSTUDIO_WORKERS", "1")))))
    except Exception as e:  # noqa: BLE001
        import traceback
        results.put({"type": "error", "worker": wid, "error": f"{e}\n{traceback.format_exc()}"})
        return
    while True:
        try:
            job = tasks.get(timeout=0.5)
        except queue.Empty:
            continue
        if job is None:
            break
        k, a, b = job
        final = os.path.join(work_dir, f"chunk_{k:04d}.ts")
        part = final + ".partial"
        t0 = time.time()
        proc = subprocess.Popen(encoder_cmd(out, part, threads), stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for i in range(a, b):
                proc.stdin.write(comp.frame(i).tobytes())  # type: ignore[union-attr]
                if (i - a) % 6 == 5 or i == b - 1:
                    results.put({"type": "progress", "worker": wid, "chunk": k, "frame": i})
            proc.stdin.close()  # type: ignore[union-attr]
            err = proc.stderr.read().decode(errors="replace")  # type: ignore[union-attr]
            rc = proc.wait()
            if rc != 0:
                raise RuntimeError(f"ffmpeg exited {rc}: {err[-800:]}")
            got = ffprobe_frames(part)
            if got != b - a:
                raise RuntimeError(f"chunk {k}: expected {b - a} frames, encoder wrote {got}")
            os.replace(part, final)
            results.put({"type": "chunk_done", "worker": wid, "chunk": k, "frames": b - a,
                         "seconds": round(time.time() - t0, 3), "stats": dict(comp.stats)})
        except Exception as e:  # noqa: BLE001
            proc.kill()
            import traceback
            results.put({"type": "error", "worker": wid, "chunk": k, "error": f"{e}\n{traceback.format_exc()}"})
            break


# ---------------------------------------------------------------------- driver

class RenderLog:
    def __init__(self, path: str, echo: bool = True):
        self.fh = open(path, "a", encoding="utf-8")
        self.echo = echo

    def write(self, ev: str, **kw: Any) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": ev, **kw}
        self.fh.write(json.dumps(rec) + "\n")
        self.fh.flush()

    def close(self) -> None:
        self.fh.close()


def render_video(spec: dict, out: Output, out_path: str, work_dir: str, fingerprint: str, n_frames: int,
                 workers: int, grid: dict | None = None, post: dict | None = None, chunk_seconds: float = 2.0,
                 log: RenderLog | None = None, on_progress: Callable[[dict], None] | None = None) -> dict:
    """Render frames [0, n_frames) to out_path (video only, H.264 MP4). Resumable."""
    os.makedirs(work_dir, exist_ok=True)
    man_path = os.path.join(work_dir, "manifest.json")
    chunks = plan_chunks(n_frames, out.fps, chunk_seconds)
    manifest = {"fingerprint": fingerprint, "output": asdict(out), "frames": n_frames, "chunks": chunks}
    if os.path.exists(man_path):
        try:
            old = json.load(open(man_path))
        except json.JSONDecodeError:
            old = {}
        if {k: old.get(k) for k in manifest} != json.loads(json.dumps(manifest)):
            for f in os.listdir(work_dir):
                if f.startswith("chunk_"):
                    os.remove(os.path.join(work_dir, f))
            if log:
                log.write("manifest_changed", note="spec or settings changed; previous chunks discarded")
    json.dump(manifest, open(man_path, "w"), indent=1)
    todo, done = [], []
    for k, (a, b) in enumerate(chunks):
        p = os.path.join(work_dir, f"chunk_{k:04d}.ts")
        if os.path.exists(p) and ffprobe_frames(p) == b - a:
            done.append(k)
        else:
            todo.append((k, a, b))
    for f in os.listdir(work_dir):
        if f.endswith(".partial"):
            os.remove(os.path.join(work_dir, f))
    workers = max(1, min(workers, len(todo))) if todo else 0
    if log:
        log.write("render_start", frames=n_frames, chunks=len(chunks), resumed_chunks=len(done), todo=len(todo),
                  workers=workers, size=[out.width, out.height], fps=out.fps, quality=out.quality)
    t0 = time.time()
    frames_done = sum(chunks[k][1] - chunks[k][0] for k in done)
    if todo:
        ctx = mp.get_context("spawn")
        tasks: mp.Queue = ctx.Queue()
        results: mp.Queue = ctx.Queue()
        for job in todo:
            tasks.put(job)
        for _ in range(workers):
            tasks.put(None)
        os.environ["KSTUDIO_WORKERS"] = str(workers)
        procs = [ctx.Process(target=_worker, args=(w, spec, asdict(out), grid, post, work_dir, tasks, results),
                             daemon=True) for w in range(workers)]
        for proc in procs:
            proc.start()
        progress: dict[int, int] = {}
        remaining = len(todo)
        error = None
        last_print = 0.0
        try:
            while remaining > 0:
                try:
                    msg = results.get(timeout=1.0)
                except queue.Empty:
                    if not any(proc.is_alive() for proc in procs):
                        error = error or "all workers exited unexpectedly"
                        break
                    continue
                if msg["type"] == "progress":
                    progress[msg["chunk"]] = msg["frame"] - chunks[msg["chunk"]][0] + 1
                elif msg["type"] == "chunk_done":
                    progress.pop(msg["chunk"], None)
                    frames_done += msg["frames"]
                    remaining -= 1
                    if log:
                        log.write("chunk_done", chunk=msg["chunk"], worker=msg["worker"], frames=msg["frames"],
                                  seconds=msg["seconds"])
                elif msg["type"] == "error":
                    error = msg["error"]
                    if log:
                        log.write("error", **{k: v for k, v in msg.items() if k != "type"})
                    break
                cur = frames_done + sum(progress.values())
                el = time.time() - t0
                info: dict[str, float | None] = {"frames": cur, "total": n_frames, "elapsed": round(el, 1),
                        "fps": round(cur / el, 2) if el > 0 else 0.0}
                rate = (cur - (n_frames - sum(b - a for _, a, b in todo))) / el if el > 0 else 0
                info["eta"] = round((n_frames - cur) / rate, 1) if rate > 0 else None
                if on_progress and time.time() - last_print > 0.5:
                    on_progress(info)
                    last_print = time.time()
        except KeyboardInterrupt:
            error = "interrupted (finished chunks are kept; re-run the same command to resume)"
        finally:
            for proc in procs:
                if error:
                    proc.terminate()
                proc.join(timeout=5 if not error else 1)
        if error:
            if log:
                log.write("render_failed", error=error[:2000])
            raise RuntimeError(error)
    # concat
    lst = os.path.join(work_dir, "concat.txt")
    with open(lst, "w") as fh:
        for k in range(len(chunks)):
            fh.write(f"file '{os.path.join(work_dir, f'chunk_{k:04d}.ts')}'\n")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                        "-c", "copy", "-movflags", "+faststart", out_path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"concat failed: {r.stderr[-800:]}")
    got = ffprobe_frames(out_path)
    el = time.time() - t0
    if log:
        log.write("render_done", frames=got, expected=n_frames, seconds=round(el, 2), workers=workers)
    if got != n_frames:
        raise RuntimeError(f"final video has {got} frames, expected {n_frames}")
    return {"frames": got, "seconds": round(el, 2), "workers": workers, "resumed_chunks": len(done),
            "rendered_chunks": len(todo)}


def _rate(r: str | None) -> float:
    num, _, den = (r or "0/1").partition("/")
    try:
        return float(num) / float(den or 1) if float(den or 1) else 0.0
    except ValueError:
        return 0.0


def video_timing(path: str) -> dict[str, Any]:
    """Frame-exact timing of a file's first video stream: r_frame_rate, avg_frame_rate, packet count
    and the stream duration, plus nb_frames/duration (the rate a player actually sees)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=r_frame_rate,avg_frame_rate,nb_frames,nb_read_packets,duration:format=duration",
                        "-of", "json", path], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        d = {}
    v = (d.get("streams") or [{}])[0]
    frames = int(v.get("nb_read_packets") or v.get("nb_frames") or 0)
    dur = float(v.get("duration") or (d.get("format") or {}).get("duration") or 0.0)
    return {"r_frame_rate": v.get("r_frame_rate"), "avg_frame_rate": v.get("avg_frame_rate"),
            "r_fps": round(_rate(v.get("r_frame_rate")), 4), "avg_fps": round(_rate(v.get("avg_frame_rate")), 4),
            "frames": frames, "video_duration": round(dur, 6),
            "measured_fps": round(frames / dur, 4) if dur > 0 else 0.0}


MUX_CEILING_DBTP = -1.3   # delivery true-peak ceiling measured on the encoded AAC track (-1.0 spec, 0.3 dB margin)
MUX_MAX_TRIES = 6         # encodes: up to 2 local trims, then whole-file gain steps


def _local_trim(src: np.ndarray, decoded: np.ndarray, ceiling_db: float, margin_db: float = 0.2,
                block: int = 1024) -> tuple[np.ndarray, list[dict[str, float]]]:
    """Pull the pre-encode audio down only where the *encoded* audio overshoots: per AAC frame
    (1024 samples) whose decoded true peak exceeds the ceiling, a raised-cosine dip of
    (overshoot + margin) dB spanning the frame and its neighbours. Loudness is untouched."""
    from .audio import dsp as A
    n = min(len(src), len(decoded))
    env = A.true_peak_env(decoded[:n])
    ceil = 10 ** (ceiling_db / 20)
    g = np.ones(len(src), np.float32)
    fixes = []
    for k in range(0, n, block):
        pk = float(env[k:k + block].max())
        if pk <= ceil:
            continue
        over = float(20 * np.log10(pk / ceil)) + margin_db
        a, b = max(0, k - 2 * block), min(len(src), k + 3 * block)
        w = np.hanning(b - a + 2)[1:-1].astype(np.float32)
        w = np.minimum(1.0, w * 2.0)  # flat-topped dip
        g[a:b] = np.minimum(g[a:b], 1 - (1 - 10 ** (-over / 20)) * w)
        fixes.append({"t": round(k / A.SR, 4), "over_db": round(over - margin_db, 2)})
    return (src * g[:, None]).astype(np.float32), fixes


def mux(video: str, audio: str, out_path: str, ceiling_dbtp: float = MUX_CEILING_DBTP,
        max_tries: int = MUX_MAX_TRIES) -> dict[str, Any]:
    """Mux video (stream copy) + WAV -> AAC 192k MP4, then verify the delivered file.

    * The output runs exactly as long as the video: audio is padded with silence (apad) and the
      output is cut at the video duration (frames / rate). `-shortest` is not used, because when the
      audio is a few ms short it trims the video track and the delivered rate/frame count drifts
      (e.g. 330 frames over 10.967 s = 30.091 fps).
    * True peak and integrated loudness are measured on the final file (ffmpeg EBU R128, after the
      AAC encode). If the codec pushed the true peak over `ceiling_dbtp`, the pre-encode audio is
      lowered by the overshoot (+0.2 dB) where it happens (the AAC frames that went over, found by
      decoding the result), and re-encoded; if two local trims do not converge, the whole pre-encode
      gain is lowered by the overshoot (+0.2 dB) per encode instead. At most `max_tries` encodes.
    Returns the measurements; raises if the mux itself fails."""
    from .audio import dsp as A
    from .audio.mix import measure
    vt = video_timing(video)
    fps = vt["r_fps"] or vt["avg_fps"]
    dur = vt["frames"] / fps if fps and vt["frames"] else vt["video_duration"]
    gain_db = 0.0
    attempts: list[dict[str, Any]] = []
    loud: dict[str, Any] = {}
    src_path, tmp = audio, out_path + ".trim.wav"
    src: np.ndarray | None = None
    try:
        for i in range(max(1, max_tries)):
            af = (f"volume={gain_db:.2f}dB," if gain_db else "") + "apad"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video, "-i", src_path,
                   "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "192k",
                   "-ar", "48000"]
            if dur > 0:
                cmd += ["-t", f"{dur:.6f}"]
            r = subprocess.run(cmd + ["-movflags", "+faststart", out_path], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"mux failed: {r.stderr[-800:]}")
            loud = measure(out_path)
            att: dict[str, Any] = {"gain_db": round(gain_db, 2), "lufs": loud["lufs"], "true_peak": loud["true_peak"]}
            attempts.append(att)
            over = loud["true_peak"] - ceiling_dbtp
            if over <= 0 or i == max_tries - 1:
                break
            if i < min(2, max_tries - 2):  # local fix first
                if src is None:
                    src = A.read_audio(audio)
                src, fixes = _local_trim(src, A.read_audio(out_path), ceiling_dbtp - 0.1)
                att["local_trims"] = fixes
                if fixes:
                    A.write_wav(tmp, src)
                    src_path = tmp
                    continue
            gain_db -= over + 0.2
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    ot = video_timing(out_path)
    want = vt["r_fps"]
    fps_ok = bool(want and abs(ot["r_fps"] - want) < 1e-3 and abs(ot["avg_fps"] - want) < 1e-3
                  and abs(ot["measured_fps"] - want) < 0.01 and ot["frames"] == vt["frames"])
    return {"out": out_path, "attempts": attempts, "gain_db": round(gain_db, 2), "loudness": loud,
            "true_peak_ok": loud.get("true_peak", 0.0) <= ceiling_dbtp, "ceiling_dbtp": ceiling_dbtp,
            "video_in": vt, "video_out": ot, "fps_ok": fps_ok}


def which_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"kstudio: `{tool}` not found on PATH. Install ffmpeg (apt install ffmpeg / brew install ffmpeg).")
