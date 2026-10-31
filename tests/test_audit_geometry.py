"""Bounded normalized-audit geometry; native pipeline controls live in candidate2/native_probe.py."""
import json
import random
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kstudio import audit as A
from kstudio.exitcodes import AuditFailed, classify


class AuditGeometryTests(unittest.TestCase):
    def test_standard_geometry(self):
        for shape, expected in [((720, 1280), (134, 240)), ((1080, 1920), (134, 240)),
                                ((2160, 3840), (134, 240)), ((1280, 720), (240, 134)),
                                ((1920, 1080), (240, 134)), ((3840, 2160), (240, 134)),
                                ((720, 720), (134, 134)), ((1080, 1080), (134, 134)),
                                ((2160, 2160), (134, 134)), ((864, 1080), (134, 168)),
                                ((1080, 1350), (134, 168)), ((270, 480), (134, 240)),
                                ((3840, 1080), (480, 134)), ((144, 256), (134, 240)),
                                ((320, 240), (180, 134))]:
            with self.subTest(shape=shape):
                self.assertEqual(A.motion_analysis_size(*shape), expected)

    def test_invalid_and_unsupported_fail_closed_before_decode(self):
        for shape in [(0, 1080), (-1, 1080), (True, 1080), (720.0, 1280), (None, 1280),
                      (1, 1), (16, 16), (100, 100), (142, 256), (2, 8192), (8192, 2),
                      (270, 4096), (4096, 270), (1080, 4096), (300, 4097), (4096, 4096),
                      (2 ** 100, 1080)]:
            with self.subTest(shape=shape), patch.object(A.subprocess, "Popen") as popen:
                with self.assertRaises(AuditFailed) as raised:
                    A.motion_stats("must-not-open.mp4", *shape, 30)
                popen.assert_not_called()
                self.assertEqual(int(raised.exception.exit), 5)
                self.assertEqual(raised.exception.details["reason"], "unsupported_geometry")

    def test_probe_rejects_before_video_timing_or_decode(self):
        metadata = {"streams": [{"codec_type": "video", "width": 2, "height": 8192}]}
        with patch.object(A.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(metadata))) as run:
            with patch.object(A.subprocess, "Popen") as popen:
                with self.assertRaises(AuditFailed) as raised:
                    A.probe("metadata-only.mp4")
                popen.assert_not_called()
                self.assertEqual(run.call_count, 1)
                self.assertIs(classify(raised.exception, "render"), raised.exception)
                self.assertEqual(raised.exception.code, "AUDIT_FAILED")

    def test_all_supported_results_obey_resource_bounds(self):
        rng = random.Random(3202)
        for _ in range(12000):
            width, height = rng.randrange(1, 6000), rng.randrange(1, 6000)
            try:
                w, h = A.motion_analysis_size(width, height)
            except AuditFailed:
                continue
            self.assertGreaterEqual(min(width, height), 144)
            self.assertLessEqual(max(width, height), 4096)
            self.assertLessEqual(width * height, 9_000_000)
            self.assertEqual((w % 2, h % 2), (0, 0))
            self.assertGreaterEqual(min(w, h), 16)
            self.assertLessEqual(max(w, h), 512)
            self.assertLessEqual(w * h, 65_536)
            self.assertLessEqual(w, width)
            self.assertLessEqual(h, height)

    def test_reference_geometry_preserved_within_supported_policy(self):
        for long_edge in range(1080, 4097):
            for width, height in [(1080, long_edge), (long_edge, 1080)]:
                try:
                    actual = A.motion_analysis_size(width, height)
                except AuditFailed:
                    continue  # unsupported is asserted separately, never accepted as a video PASS
                self.assertEqual(actual, (max(16, width // 8) // 2 * 2, max(16, height // 8) // 2 * 2))

    def test_display_metadata_policy(self):
        base = {"width": 720, "height": 1280}
        for sar in [None, "N/A", "0:1", "1:1"]:
            A._validate_audit_video({**base, "sample_aspect_ratio": sar})
        for extra in [{"sample_aspect_ratio": "2:1"}, {"sample_aspect_ratio": "4:3"},
                      {"tags": {"rotate": "90"}}, {"side_data_list": [{"rotation": -90}]},
                      {"tags": {"rotate": "nan"}}, {"tags": {"rotate": "bad"}},
                      {"tags": "bad"}, {"tags": ""}, {"side_data_list": ["bad"]},
                      {"side_data_list": 3}, {"side_data_list": 0}]:
            with self.subTest(extra=extra), self.assertRaises(AuditFailed) as raised:
                A._validate_audit_video({**base, **extra})
            self.assertEqual(raised.exception.details["reason"], "unsupported_display_geometry")
        A._validate_audit_video({**base, "tags": {"rotate": "360"}})


if __name__ == "__main__":
    unittest.main()
