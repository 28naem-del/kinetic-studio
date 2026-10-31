"""Exercise the real chunk-resume path with custom delivery filenames and mocked encoding."""
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from kstudio import render
from kstudio.film import Film
from kstudio.presets import make_output


def test_custom_delivery_does_not_discard_finished_chunks(tmp_path, monkeypatch):
    spec = {"_dir": str(tmp_path), "scenes": [{"type": "title", "title": "Reuse", "dur": 1}]}
    output = make_output("reel", 30, "draft", scale=0.1)
    film = Film(spec, output.width, output.height)
    original = film.fingerprint()
    work = tmp_path / ".kstudio_work" / "movie"
    work.mkdir(parents=True)
    chunks = render.plan_chunks(30, 30)
    (work / "manifest.json").write_text(json.dumps({"fingerprint": original, "output": asdict(output),
                                                   "frames": 30, "chunks": chunks}))
    chunk = work / "chunk_0000.ts"
    chunk.write_bytes(b"previously verified chunk")
    destination = tmp_path / "deliverables" / "movie.mp4"
    destination.parent.mkdir()

    def concat(command, **kwargs):
        Path(command[-1]).write_bytes(b"new delivered video")
        return subprocess.CompletedProcess(command, 0, "", "")

    def no_workers(*args, **kwargs):
        raise AssertionError("finished chunks must not spawn new render workers")

    monkeypatch.setattr(render.subprocess, "run", concat)
    monkeypatch.setattr(render, "ffprobe_frames", lambda path: 30)
    monkeypatch.setattr(render, "video_timing", lambda path: {"avg_fps": 30, "video_duration": 1})
    monkeypatch.setattr(render.mp, "get_context", no_workers)
    for _ in range(2):
        result = render.render_video(spec, output, str(destination), str(work), film.fingerprint(), 30, workers=1)
        assert result["resumed_chunks"] == 1 and result["rendered_chunks"] == 0
        assert chunk.read_bytes() == b"previously verified chunk"
        assert film.fingerprint() == original
