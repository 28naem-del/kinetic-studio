"""Execute the production matrix method without native deps; inspect text sent to drawing.

Geometry/fonts are stand-ins, not pixel or visual approval. Native Film coverage lives
in test_kinetic.py. No duplicated resolve algorithm or random-seed lucky-frame assertion.
"""
import ast
import math
import random
import unittest
from pathlib import Path
from types import SimpleNamespace

from kstudio import ease as E
from kstudio.spec import load_spec
from kstudio.timeline import Timeline

ROOT = Path(__file__).resolve().parents[1]


def matrix_method(path=None):
    path = path or ROOT / "kstudio/scenes/kinetic.py"
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Scramble")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_matrix")
    glyphs = next(n for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "GLYPHS" for t in n.targets))
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                             glyphs, method], type_ignores=[])
    calls = []
    namespace = {"math": math, "random": random, "E": E,
                 "D": SimpleNamespace(cap_height=lambda f: 20,
                                      draw_text=lambda c, text, x, y, f, color, **kw: calls.append((text, color))),
                 "L": SimpleNamespace(TYPE={"subhead": SimpleNamespace(px=lambda ctx: 20)})}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["_matrix"], calls


def fixture(bpm=128, beats=4, lines=None, first=1, rows=5, tl=None):
    spec = load_spec(str(ROOT / "examples/kinetic"))
    params = next(s for s in spec["scenes"] if s["type"] == "scramble")
    tl = tl or Timeline(bpm)
    start = tl.beat_to_time(16)
    dur = tl.beat_to_time(16 + beats) - start
    scene = SimpleNamespace(tl=tl, start=start, dur=dur,
                            p={"first": tl.beat_to_time(16 + first) - start, "rows": rows,
                               "lines": params["lines"] if lines is None else lines})
    grid = SimpleNamespace(content_w=800, safe_bottom=1000, bl=lambda n: n, x0=80)
    return scene, grid


def sample(method, calls, scene, grid, lt):
    calls.clear()
    ctx = SimpleNamespace(local_beat=scene.tl.beats_of(lt, scene.start), c=None,
                          fit=lambda *args: None, pal=lambda ink, alpha=1: (ink, alpha))
    method(scene, ctx, grid, 0, "ink")
    return list(calls)


class ScrambleResolveTests(unittest.TestCase):
    def setUp(self):
        self.method, self.calls = matrix_method()

    def assert_hold(self, scene, grid, fps):
        expected = [s.upper() for s in scene.p["lines"]]
        # Use the conservative START of the default half-beat whip, not the later
        # visibly-obscured frame. Require >= 35% of scene duration clear of it.
        end_beat = scene.tl.time_to_beat(scene.start + scene.dur)
        transition_start = scene.tl.beat_to_time(end_beat - 0.25)
        first_frame = math.ceil((scene.start + scene.dur * 0.5) * fps - 1e-9)
        stop_frame = math.ceil(transition_start * fps - 1e-9)
        self.assertGreaterEqual((stop_frame - first_frame) / fps, scene.dur * 0.35)
        for frame in range(first_frame, math.ceil((scene.start + scene.dur) * fps) + 4):
            rows = sample(self.method, self.calls, scene, grid, frame / fps - scene.start)
            self.assertEqual([s for s, _ in rows[:len(expected)]], expected, f"frame {frame}")
            self.assertTrue(all(color[1] >= 0.7 for _, color in rows[:len(expected)]))
        # Subframe samples and out-of-order calls must not reintroduce scrambling.
        for fraction in (0.99, 0.51, 1.03, 0.75, 0.5):
            rows = sample(self.method, self.calls, scene, grid, fraction * scene.dur)
            self.assertEqual([s for s, _ in rows[:len(expected)]], expected)
        return first_frame, stop_frame

    def test_exact_delivery_phrase_has_readable_hold_and_no_late_scrambling(self):
        scene, grid = fixture()
        self.assertEqual(scene.p["lines"][-1], "FRAMES > LOCAL")
        self.assertEqual(self.assert_hold(scene, grid, 30), (254, 278))

    def test_duration_bpm_fps_and_line_count(self):
        for bpm in (90, 128, 180):
            for beats in (2, 4, 8):
                for fps in (24, 30, 60):
                    for count in (1, 4, 5):
                        with self.subTest(bpm=bpm, beats=beats, fps=fps, count=count):
                            scene, grid = fixture(bpm, beats, [f"ROW {i} > DONE" for i in range(count)])
                            self.assert_hold(scene, grid, fps)

    def test_variable_tempo_uses_elapsed_time_for_reading_half(self):
        beat_times = [i * 0.5 for i in range(17)] + [8.3, 8.7, 9.2, 9.8, 10.4]
        scene, grid = fixture(tl=Timeline(120, beat_times=beat_times))
        self.assert_hold(scene, grid, 30)

    def test_already_early_schedule_is_not_accelerated(self):
        scene, grid = fixture(beats=12)
        for row, lock_beat in enumerate((1, 2, 3, 4)):
            lt = scene.tl.beat_to_time(16 + lock_beat) - scene.start
            before = sample(self.method, self.calls, scene, grid, lt - 0.0001)
            after = sample(self.method, self.calls, scene, grid, lt)
            self.assertLess(before[row][1][1], 0.7)
            self.assertGreaterEqual(after[row][1][1], 0.7)
            self.assertEqual(after[row][0], scene.p["lines"][row])

    def test_reveal_still_animates_and_decorative_rows_stay_animated(self):
        scene, grid = fixture()
        early = [sample(self.method, self.calls, scene, grid, t)
                 for t in (0.1, 0.2, 0.3, 0.4)]
        self.assertGreater(len({rows[3][0] for rows in early}), 1)
        late = [sample(self.method, self.calls, scene, grid, t)
                for t in (1.0, 1.2, 1.4, 1.6)]
        self.assertGreater(len({rows[4][0] for rows in late}), 1)
        for lines, rows in (([], 5), ([], 0), (["DONE"], 1)):
            scene, grid = fixture(lines=lines, rows=rows, first=0)
            sample(self.method, self.calls, scene, grid, scene.dur)


if __name__ == "__main__":
    unittest.main()
