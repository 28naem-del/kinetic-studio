"""CLI wiring (render --snap, audit --fps/--frames, info lint) and mux results in the build report."""
import json
import os
import shutil
import subprocess

import pytest
from conftest import EXAMPLES

from kstudio import cli, pipeline

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _mux(fps_ok=True, tp_ok=True):
    vi = {"frames": 60, "r_fps": 30.0, "avg_fps": 30.0, "measured_fps": 30.0, "video_duration": 2.0}
    vo = dict(vi, frames=60 if fps_ok else 61, measured_fps=30.0 if fps_ok else 30.09)
    return {"out": "x.mp4", "attempts": [{"gain_db": 0.0, "lufs": -14.0, "true_peak": -1.2 if tp_ok else -0.4}],
            "gain_db": 0.0, "loudness": {"lufs": -14.0, "true_peak": -1.2 if tp_ok else -0.4},
            "true_peak_ok": tp_ok, "ceiling_dbtp": -1.0, "video_in": vi, "video_out": vo, "fps_ok": fps_ok}


@pytest.mark.parametrize("fps_ok,tp_ok", [(True, True), (False, True), (True, False)])
def test_mux_checks_drive_audit_pass(fps_ok, tp_ok):
    a = {"checks": {"codec": {"ok": True, "value": "h264 High", "want": "h264 High"}}, "pass": True,
         "loudness": {"lufs": -14.0, "true_peak": -1.2}, "motion": {"black_frames": 0}}
    mx = _mux(fps_ok, tp_ok)
    pipeline.add_mux_checks(a, mx)
    assert a["checks"]["mux_fps"]["ok"] is fps_ok and a["checks"]["mux_true_peak"]["ok"] is tp_ok
    assert a["pass"] is (fps_ok and tp_ok)
    d = pipeline.delivery_summary(mx, {"audio": {"duration": 2.01}, "video_duration": 2.0}, a)
    assert d["fps_ok"] is fps_ok and d["av_gap"] == 0.01 and d["black_frames"] == 0 and d["mux_encodes"] == 1


def test_render_passes_snap_and_honours_pass(monkeypatch, tmp_path):
    seen = {}

    def fake_build(spec, out, *args, snap=None, **kw):
        seen["snap"] = snap
        return {"output": out, "duration": 1.0, "video_seconds": 0.1, "audio_seconds": 0.1, "total_seconds": 0.2,
                "render": {"workers": 1, "resumed_chunks": 0},
                "audio": {"final": {"lufs": -14.0, "true_peak": -1.5}}, "pass": seen.get("want", True)}

    monkeypatch.setattr(pipeline, "build", fake_build)
    spec = os.path.join(EXAMPLES, "kinetic")
    out = str(tmp_path / "k.mp4")
    assert cli.main(["render", spec, "-o", out, "--no-audit", "-q"]) == 0 and seen["snap"] is None
    assert cli.main(["render", spec, "-o", out, "--no-audit", "-q", "--snap"]) == 0 and seen["snap"] == "beat"
    seen["want"] = False
    assert cli.main(["render", spec, "-o", out, "--no-audit", "-q", "--snap", "8th"]) == 5 and seen["snap"] == "8th"
    with pytest.raises(SystemExit):
        cli.main(["render", spec, "--snap", "quarter"])


def test_info_prints_lint(capsys):
    assert cli.main(["info", os.path.join(EXAMPLES, "kinetic"), "-v"]) == 0
    assert "lint:" in capsys.readouterr().out


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_audit_cli_fps_and_frames(tmp_path, capsys):
    v = str(tmp_path / "v.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=2", "-c:v",
                    "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", v], check=True)
    cli.main(["audit", v, "--fps", "30", "--frames", "60", "--json"])
    r = json.loads(capsys.readouterr().out)
    assert r["checks"]["fps"]["ok"] and r["checks"]["frames"]["ok"]
    cli.main(["audit", v, "--fps", "25", "--frames", "50", "--json"])
    r = json.loads(capsys.readouterr().out)
    assert not r["checks"]["fps"]["ok"] and not r["checks"]["frames"]["ok"]


def test_interrupted_render_loop_reraises_the_interrupt(monkeypatch, tmp_path):
    """Ctrl-C in the worker loop stops the workers and leaves as KeyboardInterrupt (exit 130), not RuntimeError."""
    import queue

    from kstudio import render
    from kstudio.presets import make_output

    stopped = []

    class Results(queue.Queue):
        def get(self, *a, **k):
            raise KeyboardInterrupt

    class Proc:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def is_alive(self):
            return True

        def terminate(self):
            stopped.append("terminate")

        def join(self, timeout=None):
            stopped.append("join")

    queues = iter([queue.Queue(), Results()])

    class Ctx:
        def Queue(self):
            return next(queues)

        Process = Proc

    monkeypatch.setattr(render.mp, "get_context", lambda kind: Ctx())
    for k in ("KSTUDIO_WORKERS", "KSTUDIO_CV_THREADS_RUN"):  # render_video sets these; restore them afterwards
        monkeypatch.setenv(k, os.environ.get(k, "1"))
    log = render.RenderLog(str(tmp_path / "render.log"), echo=False)
    with pytest.raises(KeyboardInterrupt):
        render.render_video({}, make_output("reel", 30, "draft", scale=0.25), str(tmp_path / "v.mp4"),
                            str(tmp_path / "work"), "fp", 60, 1, log=log)
    log.close()
    assert stopped == ["terminate", "join"]
    assert '"render_failed"' in open(tmp_path / "render.log").read()
