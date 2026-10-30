"""Easing grammar, spring, stagger, linear-light motion blur, dither, grain and grade presets."""
from __future__ import annotations

import math

import numpy as np
import pytest
import skia

from kstudio import ease as E
from kstudio import grade as G
from kstudio.compositor import Compositor
from kstudio.presets import QUALITY, make_output


# ---------------------------------------------------------------- easing registry
@pytest.mark.parametrize("spec", ["out_expo", "outExpo", "expo-out", "ease_out_expo", "in_out_quint",
                                  "in_quart", "linear", "out_back", "in_out_sine", "in_out_quad"])
def test_registry_names_and_aliases(spec):
    f = E.resolve(spec)
    assert abs(f(0.0)) < 1e-6 and abs(f(1.0) - 1.0) < 1e-6


def test_registry_keeps_legacy_easings():
    for name in ("linear", "in_quad", "out_quad", "in_out_quad", "in_cubic", "out_cubic", "in_out_cubic",
                 "out_quart", "in_expo", "out_expo", "in_out_expo", "out_back", "out_elastic"):
        assert name in E.EASINGS, name
        assert callable(getattr(E, name)), name
    assert E.resolve("out_expo")(0.3) == pytest.approx(E.out_expo(0.3))
    assert 0.9 < E.spring(1.0) < 1.1  # legacy spring(t) signature still works


def test_cubic_bezier_string():
    f = E.resolve("cubic-bezier(0.2, 0.8, 0.2, 1)")
    assert f(0.0) == pytest.approx(0.0, abs=1e-6) and f(1.0) == pytest.approx(1.0, abs=1e-6)
    assert f(0.25) > 0.6  # strongly front-loaded
    lin = E.resolve("cubic-bezier(0.25,0.25,0.75,0.75)")
    for x in (0.1, 0.37, 0.8):
        assert lin(x) == pytest.approx(x, abs=1e-4)
    css = E.resolve("css.ease")
    assert css(0.5) == pytest.approx(E.cubic_bezier(0.25, 0.1, 0.25, 1.0)(0.5), abs=1e-9)


def test_back_overshoot_capped_at_four_percent():
    f = E.resolve("back(0.04)")
    peak = max(f(x / 2000) for x in range(2001))
    assert peak == pytest.approx(1.04, abs=2e-3)
    g = E.resolve("back(0.2)")  # opt-in overshoot is clamped to the house cap
    assert max(g(x / 2000) for x in range(2001)) <= 1.0 + E.MAX_OVERSHOOT + 2e-3


def test_roles_and_steps():
    assert E.resolve("enter")(0.4) == pytest.approx(E.out_expo(0.4))
    assert E.resolve("move")(0.3) == pytest.approx(E.in_out_quint(0.3))
    st = E.resolve("steps(4)")
    assert [st(x) for x in (0.1, 0.3, 0.6, 0.99)] == pytest.approx([0.0, 0.25, 0.5, 0.75])
    with pytest.raises((ValueError, KeyError)):
        E.resolve("no_such_ease")


# ---------------------------------------------------------------- spring
@pytest.mark.parametrize("dur,bounce", [(0.6, 0.25), (0.4, 0.0), (1.0, 0.5), (0.8, -0.3)])
def test_spring_settles_at_requested_duration(dur, bounce):
    sp = E.Spring.settling(dur, bounce)
    assert sp.settle_time() == pytest.approx(dur, rel=0.01)
    assert abs(sp.value(dur) - 1.0) <= 1.5e-3
    assert abs(sp.value(dur * 0.5) - 1.0) > 1e-3  # not settled half way
    f = E.resolve(f"spring({dur},{bounce})")
    assert f.duration == pytest.approx(dur, rel=0.01)
    assert f(0.0) == pytest.approx(0.0, abs=1e-9) and f(1.0) == pytest.approx(1.0, abs=2e-3)


def test_spring_physics():
    sp = E.Spring(stiffness=200, damping=10, mass=1)
    assert sp.omega0 == pytest.approx(math.sqrt(200))
    assert sp.zeta == pytest.approx(10 / (2 * math.sqrt(200)))
    # numerical check of x'' = -k (x - 1) - c x' at a few points
    for t in (0.05, 0.2, 0.4):
        h = 1e-4
        acc = (sp.value(t + h) - 2 * sp.value(t) + sp.value(t - h)) / h ** 2
        assert acc == pytest.approx(-200 * (sp.value(t) - 1) - 10 * sp.velocity_at(t), rel=1e-3, abs=1e-2)
    assert E.Spring.perceptual(0.5, 0.0).zeta == pytest.approx(1.0)
    kick = E.Spring(stiffness=100, damping=20, velocity=8.0)
    assert kick.velocity_at(0.0) == pytest.approx(8.0)


# ---------------------------------------------------------------- stagger
def test_stagger_linear_and_centre():
    assert E.stagger(4, 0.1) == pytest.approx([0.0, 0.1, 0.2, 0.3])
    assert E.stagger(4, 0.1, start=0.5, order="last") == pytest.approx([0.8, 0.7, 0.6, 0.5])
    assert E.stagger(5, 0.1, order="centre") == pytest.approx([0.2, 0.1, 0.0, 0.1, 0.2])
    assert E.stagger(5, 0.1, order="edges") == pytest.approx([0.0, 0.1, 0.2, 0.1, 0.0])


def test_stagger_max_total_and_ease():
    d = E.stagger(11, 0.1, max_total=0.5)
    assert d[-1] == pytest.approx(0.5) and d[0] == 0.0
    eased = E.stagger(11, 0.1, ease="in_quad")
    assert eased[-1] == pytest.approx(1.0) and eased[5] == pytest.approx(0.25)
    shuffled = E.stagger(6, 0.1, order="random", seed=3)
    assert sorted(shuffled) == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])


def test_stagger_grid():
    g = E.stagger_grid(3, 3, 0.1)  # from the centre, euclidean distance
    assert len(g) == 9 and g[4] == pytest.approx(0.0)
    assert g[1] == pytest.approx(0.1) and g[0] == pytest.approx(0.1 * math.sqrt(2))
    gx = E.stagger_grid(3, 2, 0.1, origin="first", axis="x")
    assert gx == pytest.approx([0.0, 0.1, 0.2, 0.0, 0.1, 0.2])


def test_stagger_progress():
    delays = E.stagger(3, 0.2)
    assert E.stagger_progress(0.2, 1, delays, 0.4, ease="linear") == pytest.approx(0.0)
    assert E.stagger_progress(0.4, 1, delays, 0.4, ease="linear") == pytest.approx(0.5)
    assert E.stagger_progress(0.6, 1, delays, 0.4, ease="linear") == pytest.approx(1.0)


# ---------------------------------------------------------------- compositor
class _Film:
    """Minimal film: draws a callable; constant shutter."""

    def __init__(self, draw, duration=2.0, theme="ink"):
        self._draw, self.duration = draw, duration
        self.cfg, self.spec, self.theme = {}, {"theme": theme}, {}

    def draw(self, canvas, t):
        self._draw(canvas, t)

    def shutter_mult(self, t):
        return 1.0


_FLAT = {"bloom": 0.0, "chroma": 0.0, "vignette": 0.0, "grain": 0.0}


def _out(w=192, h=108, quality="standard", fps=30):
    o = make_output("landscape", fps=fps, quality=quality)
    o.width, o.height = w, h
    return o


def test_quality_samples_and_shutter():
    assert QUALITY["standard"][0] >= 8 and QUALITY["high"][0] >= 16 and QUALITY["draft"][0] == 1
    assert QUALITY["standard"][1] == 180.0 and QUALITY["high"][1] == 180.0


def test_linear_light_blur_brighter_than_gamma_average():
    # a white bar sweeping fast over black: covered pixels see white ~half the shutter
    def draw(c, t):
        x = (t - 0.5) * 30 * 120 + 96  # 120 px per frame at 30 fps
        c.drawRect(skia.Rect.MakeXYWH(x - 30, 0, 60, 108), skia.Paint(Color=skia.ColorWHITE))
    comp = Compositor(_Film(draw), _out(), post=dict(_FLAT, dither=False), grade="none")
    lin = comp.frame_linear(15).copy()  # t = 0.5: bar centred, sweeping 60 px over the shutter
    row = lin[54]
    cover = (row[:, 1] > 1e-4) & (row[:, 1] < 0.999)
    assert cover.sum() > 20  # a smear, not a hard edge
    smear = row[cover, 1]
    disp_linear = G.linear_to_srgb(smear)
    disp_gamma = smear  # gamma-space averaging of 0/1 gives the coverage fraction itself
    assert float(disp_linear.mean()) > float(disp_gamma.mean()) + 0.1
    # light energy is preserved: the smear integrates to the 60 px bar
    assert float(row[:, 1].sum()) == pytest.approx(60.0, rel=0.05)


def test_linear_average_of_pattern():
    black_white = np.array([0.0, 1.0], np.float32)
    lin_mean = G.linear_to_srgb(G.srgb_to_linear(black_white).mean())
    assert float(lin_mean) == pytest.approx(0.735, abs=0.01)
    assert float(lin_mean) > float(black_white.mean())


def test_motion_blur_is_continuous_at_high_speed():
    # a 6 px stroke moving 120 px inside the shutter: flow alone under-reads this (the stroke
    # moves further than its own width between strata); the sampler must still close the gaps
    def draw(c, t):
        x = (t - 0.5) * 30 * 240 + 240
        c.drawRect(skia.Rect.MakeXYWH(x - 3, 0, 6, 108), skia.Paint(Color=skia.ColorWHITE))
    comp = Compositor(_Film(draw), _out(w=480), post=dict(_FLAT, dither=False), grade="none")
    row = comp.frame_linear(15)[54, :, 1]
    seg = row[190:290] > 1e-4
    assert seg.mean() > 0.85  # one streak, not a few copies
    runs, cur = [], 0
    for lit in seg:
        cur = 0 if lit else cur + 1
        runs.append(cur)
    assert max(runs) <= 4
    assert comp.stats["unresolved"] >= 1 and comp.stats["max_samples"] >= 24
    assert float(row.sum()) == pytest.approx(6.0, rel=0.08)  # energy preserved


def test_static_frames_skip_samples():
    def draw(c, t):
        c.drawRect(skia.Rect.MakeXYWH(40, 20, 50, 50), skia.Paint(Color=skia.ColorWHITE))
    comp = Compositor(_Film(draw), _out(), post=_FLAT, grade="none")
    comp.frame(10)
    assert comp.stats["adaptive_skips"] == 1 and comp.stats["samples"] <= 2


def test_dither_gives_more_levels_in_dark_gradient():
    def draw(c, t):
        p = skia.Paint(Shader=skia.GradientShader.MakeLinear(
            [skia.Point(0, 0), skia.Point(480, 0)], [skia.Color(0, 0, 0), skia.Color(20, 20, 20)]))
        c.drawRect(skia.Rect.MakeWH(480, 64), p)
    film = _Film(draw)
    out = _out(w=480, h=64, quality="draft")
    plain = Compositor(film, out, post=dict(_FLAT, dither=False), grade="none").frame(3)[..., 1]
    dith = Compositor(film, out, post=_FLAT, grade="none").frame(3)[..., 1]
    # local averages (what the eye and the encoder see) resolve far more levels with dither
    lv_plain = len(np.unique(np.round(plain.astype(np.float32).mean(axis=0), 2)))
    lv_dith = len(np.unique(np.round(dith.astype(np.float32).mean(axis=0), 2)))
    assert lv_dith > 3 * lv_plain
    assert len(np.unique(dith)) > len(np.unique(plain))
    # and dithering does not shift the mean
    assert abs(float(dith.mean()) - float(plain.mean())) < 0.6


def test_grain_differs_between_consecutive_frames():
    def draw(c, t):
        c.clear(skia.Color(60, 60, 60))
    film = _Film(draw)
    comp = Compositor(film, _out(quality="draft"), post={"bloom": 0, "chroma": 0, "vignette": 0, "grain": 0.05})
    a = comp.frame(0).astype(np.int16)
    b = comp.frame(1).astype(np.int16)
    c2 = comp.frame(4).astype(np.int16)
    assert a.std() > 1.0  # grain is visible
    assert np.abs(a - b).mean() > 1.0  # and re-seeded each frame
    assert np.abs(a - c2).mean() > 1.0  # even when the field index wraps round


def test_grain_is_luminance_weighted():
    def draw(c, t):
        c.drawRect(skia.Rect.MakeWH(96, 108), skia.Paint(Color=skia.Color(40, 40, 40)))
        c.drawRect(skia.Rect.MakeXYWH(96, 0, 96, 108), skia.Paint(Color=skia.Color(245, 245, 245)))
    comp = Compositor(_Film(draw), _out(quality="draft"), post={"bloom": 0, "chroma": 0, "vignette": 0,
                                                              "grain": 0.05}, grade="none")
    f = comp.frame(2).astype(np.float32)[..., 1]
    assert f[:, 10:86].std() > 1.5 * f[:, 106:182].std()


# ---------------------------------------------------------------- grade
def test_grade_presets_resolve():
    for name in ("ink", "lab", "studio", "terminal", "warm"):
        g = G.resolve_grade(name)
        assert g is G.GRADES[name]
        assert 0.04 <= (g.grain or 0) <= 0.06
        lut = G.uint8_lut(g)
        assert lut.shape == (256, 3) and lut[0].max() < 40 and lut[255].min() > 200
    assert G.resolve_grade(None, theme="warm") is G.GRADES["warm"]
    assert G.resolve_grade(None, theme="unknown") == G.NEUTRAL
    assert G.resolve_grade(False).is_identity
    custom = G.resolve_grade({"base": "ink", "saturation": 0.8})
    assert custom.saturation == 0.8 and custom.lift == G.GRADES["ink"].lift
    with pytest.raises((ValueError, KeyError, TypeError)):
        G.resolve_grade({"nope": 1})


def test_identity_grade_round_trips():
    x = np.linspace(0, 1, 101, dtype=np.float32)
    assert G.apply_display(G.IDENTITY, x[:, None].repeat(3, 1)) == pytest.approx(x[:, None].repeat(3, 1), abs=1e-5)


def test_compositor_grade_selection():
    film = _Film(lambda c, t: c.clear(skia.Color(128, 128, 128)), theme="terminal")
    out = _out(quality="draft")
    assert Compositor(film, out).grade is G.GRADES["terminal"]
    assert Compositor(film, out, grade="warm").grade is G.GRADES["warm"]
    assert Compositor(film, out, post={"grade": "lab"}).grade is G.GRADES["lab"]
    a = Compositor(film, out, post=dict(_FLAT, dither=False), grade="none").frame(0)
    assert abs(int(a[..., 1].mean()) - 128) <= 1
