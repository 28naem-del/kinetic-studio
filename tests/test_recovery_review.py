"""Regression coverage for independent review R1-R4; no renderer or network required."""
import builtins
import io
import os
import tempfile
import unittest
import urllib.error
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from kstudio import assets_hash, swap
from kstudio.assets_hash import asset_fingerprints
from kstudio.exitcodes import BackendUnreachable, RenderFailed
from kstudio.spec import swap_key, swap_requests


class ReviewRegressions(unittest.TestCase):
    def test_unreferenced_outputs_never_feed_back_into_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "v.mp4").write_bytes(b"input")
            spec = {"scenes": [{"type": "clip", "src": "v.mp4"}]}
            before = asset_fingerprints(tmp, spec)
            for relative in ("movie.mp4", "deliverables/movie.mp4", "review/contact.png", "music.wav"):
                output = root / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"rendered")
                self.assertEqual(before, asset_fingerprints(tmp, spec), relative)
                output.write_bytes(b"new render")
                self.assertEqual(before, asset_fingerprints(tmp, spec), relative)

    def test_font_paths_follow_cwd_and_bundled_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project, bundle = root / "project", root / "fonts"
            project.mkdir()
            bundle.mkdir()
            external = root / "custom.ttf"
            external.write_bytes(b"AAAA")
            bundled = bundle / "Bundled.ttf"
            bundled.write_bytes(b"FFFF")
            # Resolve a relative face against cwd, even with a different spec folder.
            relative = os.path.relpath(external, Path.cwd())
            for spec in ({"theme_overrides": {"display": str(external)}},
                         {"scenes": [{"type": "title", "face": relative}]}):
                with self.subTest(spec=spec), patch.object(assets_hash, "BUNDLED_FONT_DIR", str(bundle)):
                    before = asset_fingerprints(str(project), spec)
                    external.write_bytes(external.read_bytes() + b"X")
                    after = asset_fingerprints(str(project), spec)
                    self.assertNotEqual(before, after)
                    bundled.write_bytes(bundled.read_bytes() + b"Y")
                    self.assertNotEqual(after, asset_fingerprints(str(project), spec))
                    self.assertIn(".kstudio-fonts/Bundled.ttf", dict(after))

    def test_repeated_swap_inputs_read_once_per_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video, character = root / "v.mp4", root / "c.png"
            video.write_bytes(b"video")
            character.write_bytes(b"character")
            scene = {"type": "clip", "source": {"swap": {"video": "v.mp4", "character": "c.png"}}}
            spec = {"scenes": [scene] * 10}
            request = swap_requests({**spec, "_dir": tmp})[0]
            output = root / "out" / "swap" / (swap_key(request) + ".mp4")
            output.parent.mkdir(parents=True)
            output.write_bytes(b"result")
            counts = {str(video): 0, str(character): 0}
            original = builtins.open

            def counting(path, *args, **kwargs):
                name = os.fspath(path)
                if name in counts:
                    counts[name] += 1
                return original(path, *args, **kwargs)

            with patch.dict(os.environ, {"KSTUDIO_SWAP_DIR": str(output.parent)}),                     patch("builtins.open", side_effect=counting):
                first = asset_fingerprints(tmp, spec)
            self.assertEqual(counts, {str(video): 1, str(character): 1})
            output.write_bytes(b"updated")
            with patch.dict(os.environ, {"KSTUDIO_SWAP_DIR": str(output.parent)}):
                self.assertNotEqual(first, asset_fingerprints(tmp, spec))
            # No persistent metadata cache: changing input bytes is noticed on the next call.
            video.write_bytes(b"VIDEO")
            self.assertNotEqual(first, asset_fingerprints(tmp, spec))

    def _backend_case(self, history, queue):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            video, image = root / "v.mp4", root / "c.png"
            video.write_bytes(b"video")
            image.write_bytes(b"image")
            for name, result in (("missing_nodes", []), ("upload", "file"), ("submit", "pid")):
                stack.enter_context(patch.object(swap.Comfy, name, return_value=result))

            def reply(path):
                if path.startswith("/history"):
                    return history
                if isinstance(queue, Exception):
                    raise queue
                return queue

            stack.enter_context(patch.object(swap.Comfy, "get_json", side_effect=reply))
            with self.assertRaises((BackendUnreachable, RenderFailed)) as error:
                swap.run_swap(str(video), str(image), out=str(root / "result.mp4"),
                              url="http://offline.invalid", timeout=-1)
            self.assertFalse((root / "result.mp4").exists())
            self.assertFalse(list(root.glob("*.part")))
            return error.exception

    def test_malformed_queue_is_optional_progress_not_internal_error(self):
        for pending in (1, "bad", {"wrong": 1}):
            with self.subTest(pending=pending):
                error = self._backend_case({}, {"queue_pending": pending})
                self.assertEqual(error.code, "SWAP_TIMEOUT")

    def test_malformed_history_messages_classified(self):
        for messages in (1, "bad", {"wrong": 1}):
            with self.subTest(messages=messages):
                error = self._backend_case({"pid": {"status": {"status_str": "error", "messages": messages},
                                                    "outputs": {}}}, {})
                self.assertEqual(error.code, "BACKEND_UNREACHABLE")
                self.assertEqual(error.details["reason"], "not_comfyui")

    def test_null_execution_message_is_reported_as_workflow_failure(self):
        history = {"pid": {"status": {"status_str": "error", "messages": [
            ["execution_error", {"exception_message": None}]]}, "outputs": {}}}
        error = self._backend_case(history, {})
        self.assertEqual(error.code, "SWAP_FAILED")
        self.assertIn("the workflow failed", error.message)

    def test_http_errors_close_in_queue_upload_and_download(self):
        body = io.BytesIO(b"error")
        error = urllib.error.HTTPError("http://offline.invalid/queue", 404, "missing", None, body)
        self.assertEqual(self._backend_case({}, error).code, "SWAP_TIMEOUT")
        self.assertTrue(body.closed)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "v.mp4"
            source.write_bytes(b"video")
            api = swap.Comfy("http://offline.invalid")
            for operation in (lambda: api.upload(str(source)),
                              lambda: api.download({"filename": "x.mp4"}, str(Path(tmp) / "x.mp4"))):
                body = io.BytesIO(b"error")
                error = urllib.error.HTTPError(api.url, 404, "missing", None, body)
                with patch.object(api, "_open", side_effect=error), self.assertRaises(RenderFailed):
                    operation()
                self.assertTrue(body.closed)


if __name__ == "__main__":
    unittest.main()
