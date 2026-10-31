"""Pure regression tests for selected extensionless fonts and bundled-name collisions."""
import os
import tempfile
import unittest
from pathlib import Path

from kstudio.assets_hash import asset_fingerprints
from kstudio.font_paths import FONT_DIR, resolve_font_path
from kstudio.modes import resolve_theme
from kstudio.spec import spec_hash


class FontDependencyTests(unittest.TestCase):
    def test_direct_role_and_inline_alias_without_font_extension(self):
        original = (Path(FONT_DIR) / "Inter.ttf").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            for name in ("font.bin", "extensionless"):
                font = project / name
                for selection in ("direct", "inline", "override"):
                    with self.subTest(name=name, selection=selection):
                        font.write_bytes(original)
                        spec = {"scenes": [{"type": "logo", "role": str(font)}]}
                        if selection != "direct":
                            spec["scenes"][0]["role"] = "brand"
                            spec["theme"] = {"base": "ink", "brand": str(font)}
                            if selection == "override":
                                spec["theme"]["brand"] = "Archivo"
                                spec["theme_overrides"] = {"brand": str(font)}
                        theme = resolve_theme(spec.get("theme", "ink"), spec.get("theme_overrides"))
                        role = spec["scenes"][0]["role"]
                        self.assertEqual(resolve_font_path(theme.get(role, role)), str(font))
                        before = asset_fingerprints(str(project), spec)
                        self.assertIn(name, dict(before))
                        stat = font.stat()
                        changed = bytearray(original)
                        changed[-1] ^= 1
                        font.write_bytes(changed)
                        os.utime(font, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                        after = asset_fingerprints(str(project), spec)
                        self.assertNotEqual(before, after)
                        self.assertNotEqual(spec_hash(spec, before), spec_hash(spec, after))

    def test_relative_extensionless_role_uses_process_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            font = root / "custom"
            font.write_bytes(b"font bytes")
            previous = os.getcwd()
            try:
                os.chdir(root)
                spec = {"scenes": [{"type": "logo", "role": "custom"}]}
                before = asset_fingerprints(str(project), spec)
                self.assertIn("../custom", dict(before))
                font.write_bytes(b"new bytes")
                self.assertNotEqual(before, asset_fingerprints(str(project), spec))
            finally:
                os.chdir(previous)

    def test_builtin_families_and_aliases_ignore_same_named_cwd_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            previous = os.getcwd()
            try:
                os.chdir(root)
                for family in ("Archivo", "display", "sans"):
                    with self.subTest(family=family):
                        collision = root / family
                        collision.write_bytes(b"unrelated")
                        spec = {"theme_overrides": {"display": family},
                                "scenes": [{"type": "logo", "role": "display"}]}
                        selected = Path(resolve_font_path(family)).resolve()
                        self.assertEqual(selected.parent, Path(FONT_DIR).resolve())
                        before = asset_fingerprints(str(project), spec)
                        collision.write_bytes(b"unrelated changed")
                        self.assertEqual(before, asset_fingerprints(str(project), spec))
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
