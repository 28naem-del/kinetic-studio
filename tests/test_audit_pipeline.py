"""Actual FFmpeg resampling/decoding controls; no renderer, network or optional skips."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from kstudio import audit as A


def _run(command, payload=None):
    child = subprocess.Popen(command, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = child.communicate(payload, timeout=45)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=3)
        raise
    if child.returncode:
        raise AssertionError((command, child.returncode, stderr.decode(errors="replace")[-3000:]))
    return stdout


def _measure_fixture(folder, width, height, kind):
    # The same normalized picture at each resolution; correlated grain stresses
    # the changed resampler instead of cancelling all noise through huge downscales.
    bw, bh, count = 270, 480, 24
    rng = np.random.default_rng(3202)
    frames = []
    for index in range(count):
        frame = np.full((bh, bw), 128, dtype=np.uint8)
        if kind in ("static", "grain"):
            frame[bh//4:3*bh//4, bw//4:3*bw//4] = 60
            if kind == "grain":
                frame = np.clip(np.rint(frame.astype(np.float32) + rng.normal(0, 6, frame.shape)), 0, 255).astype(np.uint8)
        elif kind == "moving":
            x = bw//10 + 2*index
            frame[bh//4:3*bh//4, x:x+bw//5] = 245
        elif kind in ("black", "near9", "near10", "near11"):
            frame.fill(0 if kind == "black" else int(kind[4:]))
        elif kind in ("first_black", "later_black") and index == (0 if kind == "first_black" else 12):
            frame.fill(0)
        elif kind in ("patch_below", "patch_above"):
            frame.fill(0)
            frame[:bh//2, :bw//10] = 180 if kind == "patch_below" else 220
        frames.append(frame.tobytes())
    file = folder / f"{kind}-{width}-{height}.mkv"
    _run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pixel_format", "gray", "-video_size", f"{bw}x{bh}",
          "-framerate", "30", "-i", "pipe:0", "-an", "-filter_threads", "1", "-vf",
          f"scale={width}:{height}:flags=neighbor,setsar=1", "-frames:v", str(count),
          "-c:v", "ffv1", "-threads", "1", "-pix_fmt", "gray", str(file)], b"".join(frames))
    # Own process group bounds the real production decoder, not a replacement equation.
    code = ("import json,sys;sys.path.insert(0,sys.argv[1]);from kstudio.audit import motion_stats;"
            "print(json.dumps(motion_stats(sys.argv[2],int(sys.argv[3]),int(sys.argv[4]),30)))")
    raw = _run([sys.executable, "-c", code, str(Path(A.__file__).resolve().parents[1]), str(file), str(width), str(height)])
    data = json.loads(raw)
    file.unlink()
    assert data["frames_decoded"] == count
    assert data["motion_analysis_size"] == list(A.motion_analysis_size(width, height))
    return data


class AuditPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("native audit pipeline regressions require ffmpeg and ffprobe; no silent skip")

    def test_pipeline_static_grain_and_moving(self):
        with tempfile.TemporaryDirectory() as tmp:
            for width, height in [(144, 256), (720, 1280), (1080, 1920), (2160, 3840),
                                  (1280, 720), (720, 720), (864, 1080), (320, 240)]:
                for kind in ("static", "grain", "moving"):
                    with self.subTest(width=width, height=height, kind=kind):
                        data = _measure_fixture(Path(tmp), width, height, kind)
                        if kind == "static":
                            self.assertEqual(data["motion_ratio"], 0)
                        elif kind == "grain":
                            self.assertLess(data["motion_ratio"], .1)
                        else:
                            self.assertGreater(data["motion_ratio"], .9)
                        self.assertEqual(data["black_frames"], 0)
                        self.assertFalse(data["first_frame_black"])

    def test_pipeline_black_boundaries_and_first_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            for width, height in [(720, 1280), (1080, 1920), (2160, 3840)]:
                for kind in ("black", "near9", "near10", "near11", "patch_below", "patch_above", "first_black", "later_black"):
                    with self.subTest(width=width, height=height, kind=kind):
                        data = _measure_fixture(Path(tmp), width, height, kind)
                        black = 24 if kind in ("black", "near9", "patch_below") else 1 if kind in ("first_black", "later_black") else 0
                        self.assertEqual(data["black_frames"], black)
                        self.assertEqual(data["first_frame_black"], kind in ("black", "near9", "patch_below", "first_black"))


if __name__ == "__main__":
    unittest.main()
