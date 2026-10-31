"""Recovery regressions: stdlib-only subset also runs without the renderer installed."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kstudio.assets_hash import asset_fingerprints
from kstudio.exitcodes import BackendUnreachable
from kstudio.swap import Comfy, find_video, load_workflow

ROOT = Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    def test_generic_json_is_not_comfyui(self):
        api = Comfy("http://localhost:8188")
        for value in ({"status": "ok"}, {}, {"LoadImage": "ok"}, []):
            with self.subTest(value=value), patch.object(api, "get_json", return_value=value):
                with self.assertRaises(BackendUnreachable) as error:
                    api.missing_nodes(["LoadImage", "CustomNode"])
                self.assertEqual(error.exception.details["reason"], "not_comfyui")
        with patch.object(api, "get_json", return_value={"LoadImage": {"input": {}}}):
            self.assertEqual(api.missing_nodes(["LoadImage", "CustomNode"]), ["CustomNode"])

    def test_workflows_present(self):
        for mode in ("simple", "object"):
            self.assertTrue(load_workflow(mode))

    def test_malformed_output_is_not_attribute_error(self):
        self.assertIsNone(find_video({"1": ["bad"]}, {}))

    def test_asset_bytes_invalidate_even_with_identical_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "outdoors").mkdir()
            video = root / "outdoors" / "video.mp4"
            video.write_bytes(b"AAAA")
            st = video.stat()
            spec = {"scenes": [{"type": "clip", "src": "outdoors/video.mp4"}]}
            before = asset_fingerprints(tmp, spec)
            video.write_bytes(b"BBBB")
            os.utime(video, ns=(st.st_atime_ns, st.st_mtime_ns))
            self.assertNotEqual(before, asset_fingerprints(tmp, spec))
            self.assertIn("outdoors/video.mp4", dict(before))

    def test_portable_order_and_explicit_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / "a", root / "b"
            (a / "out").mkdir(parents=True)
            (a / "x.mp4").write_bytes(b"video")
            (a / "out" / "swap.mp4").write_bytes(b"swap")
            shutil.copytree(a, b)
            spec = {"scenes": [{"type": "clip", "src": "out/swap.mp4"}, {"type": "clip", "src": "x.mp4"}]}
            self.assertEqual(asset_fingerprints(str(a), spec), asset_fingerprints(str(b), spec))
            bundled = asset_fingerprints(str(a), {})
            self.assertEqual(len(asset_fingerprints(str(a), spec)), len(bundled) + 2)
            self.assertNotIn("x.mp4", dict(bundled))

    def test_lock_serializes_and_preserves_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker = root / "worker.py"
            worker.write_text(
                "import sys,time\nfrom pathlib import Path\n"
                "p=Path(sys.argv[1]); p.mkdir(); time.sleep(.15); p.rmdir()\n"
            )
            command = [sys.executable, str(ROOT / "scripts/render_locked.py"),
                       "--lock", str(root / "render.lock"), "--", sys.executable,
                       str(worker), str(root / "active")]
            jobs = [subprocess.Popen(command) for _ in range(3)]
            self.assertEqual([job.wait(timeout=10) for job in jobs], [0, 0, 0])
            result = subprocess.run(command[:-2] + ["-c", "raise SystemExit(7)"], check=False)
            self.assertEqual(result.returncode, 7)


if __name__ == "__main__":
    unittest.main()
