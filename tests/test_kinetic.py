"""Kinetic mode: beat-locked cues, variable-axis landings, frame fill and old-spec compatibility."""
import inspect
import os

import numpy as np
import pytest
import skia
from conftest import EXAMPLES

from kstudio import draw as D
from kstudio.compositor import Compositor
from kstudio.film import Film
from kstudio.presets import make_output
from kstudio.scenes import kinetic as K
from kstudio.spec import load_spec


def _film(spec=None, w=1080, h=1920):
    return Film(spec or load_spec(os.path.join(EXAMPLES, "kinetic")), w, h)


def _ctx(film, sc, lt):
    surf = skia.Surface(film.W, film.H)
    ctx = film._ctx(surf.getCanvas(), sc.start + lt)
    ctx.lt, ctx.dur, ctx.start, ctx.scene = lt, sc.dur, sc.start, sc
    return ctx


def _scene(film, kind):
    return next(s for s in film.scenes if s.type == kind)


def test_cues_sit_on_the_grid():
    film = _film()
    beat = 60.0 / film.tl.bpm
    sfx = [m for m in film.tl.markers if m.kind == "sfx"]
    on_beat = 0
    for m in sfx:
        b = film.tl.time_to_beat(m.t)
        sixteenth = abs(b * 4 - round(b * 4)) * beat / 4
        assert sixteenth < 1e-3, f"sfx {m.data} at {m.t:.4f}s is off the 16th grid"
        on_beat += abs(b - round(b)) * beat < 0.0334
    assert on_beat / len(sfx) >= 0.8
    for sc in film.scenes:  # every cut on a whole beat
        b = film.tl.time_to_beat(sc.start)
        assert abs(b - round(b)) * beat < 1e-3


def test_landing_axes_animate_out_expo():
    e0, g0, w0 = K._land_axes(0.0, 0.156, 900, 112)
    assert (e0, g0, w0) == (0.0, 600.0, 70.0)
    e1, g1, w1 = K._land_axes(0.156, 0.156, 900, 112)
    assert e1 > 0.99 and g1 > 897 and w1 > 111.5
    _, gm, wm = K._land_axes(0.039, 0.156, 900, 112)  # a quarter in: out_expo is past halfway
    assert gm > 750 and wm > 91


def test_slam_land_is_a_third_of_a_beat():
    film = _film()
    sc = _scene(film, "slam")
    assert sc.p["land"] == pytest.approx(film.tl.spb / 3, abs=1e-6)
    assert sc.p["weight"] >= 900 and sc.p["width"] >= 112


def test_type3d_fills_width_and_extrudes():
    film = _film()
    sc = _scene(film, "type3d")
    imp = sc.p["impact"]
    assert imp == pytest.approx(film.tl.spb, abs=1e-6)  # "1b"
    for lt in np.linspace(imp, sc.dur - 1e-3, 9):
        ctx = _ctx(film, sc, float(lt))
        assert sc.projected_width(ctx) >= 0.85 * film.W
        assert sc.projected_width(ctx) <= 0.98 * film.W
    assert sc.p["extrude"] >= 0.08
    assert sc.p["side_luma"] == pytest.approx(0.55)
    cues = {(c.kind, round(c.at, 6)) for c in sc.cues()}
    assert ("cue", round(imp, 6)) in cues and ("hit", round(imp, 6)) in cues


def test_scramble_fills_width_and_resolves_on_beats():
    film = _film()
    sc = _scene(film, "scramble")
    ctx = _ctx(film, sc, 1.0)
    w = D.text_w(sc.font_for(ctx), str(sc.p["text"]))
    assert w >= 0.85 * film.W
    beat = film.tl.spb
    for c in sc.cues():
        b = c.at / beat
        assert abs(b * 2 - round(b * 2)) < 1e-3, f"{c.kind} {c.data} at {c.at}"
    hit = [c for c in sc.cues() if c.kind == "hit"][0]
    assert abs(hit.at / beat - round(hit.at / beat)) < 1e-3
    assert hit.at >= sc._locks()[-1]


def test_house_easing_in_kinetic():
    src = inspect.getsource(K)
    assert "E.out_back" not in src and "E.out_elastic" not in src
    assert "E.spring" not in src and "E.Spring" not in src


def test_old_kinetic_specs_still_render():
    """v0.1 field forms: numeric stagger, seconds, depth, gap, odd scene lengths, snap."""
    spec = {"mode": "kinetic", "music": {"style": "drive", "bpm": 128}, "snap": True, "scenes": [
        {"type": "slam", "words": ["STOP", "SCROLLING", "NOW"], "dur": "3b", "accent_words": ["NOW"]},
        {"type": "stack", "lines": ["ONE IDEA", "PER", "BEAT"], "dur": "4b", "gap": 0.06},
        {"type": "marquee", "text": "MOTION", "dur": "4b", "speed": 420, "transition": "zoom"},
        {"type": "type3d", "text": "KINETIC", "dur": "4b", "depth": 18, "swing": 75},
        {"type": "scramble", "text": "KINETIC", "label": "STUDIO", "stagger": 0.05, "dur": "4b"},
        {"type": "countdown", "from": 3, "dur": "3b", "transition": "squash"},
        {"type": "burst", "text": "GO", "dur": "4b", "transition": "flash", "chrome": False},
    ]}
    out = make_output("reel", quality="draft", scale=0.25)
    film = Film(spec, out.width, out.height)
    stack = _scene(film, "stack")
    assert stack.p_raw["gap"] == 0.06
    comp = Compositor(film, out)
    for sc in film.scenes:
        img = comp.frame(int((sc.start + sc.dur * 0.6) * out.fps))
        assert img[..., :3].std() > 3.0, f"{sc.type} blank"


def test_chrome_flag():
    film = _film()
    sc = _scene(film, "slam")
    assert K._chrome_on(sc)
    assert K.content_floor(_ctx(film, sc, 0.1), sc) < film.H * 0.8
    sc.p["chrome"] = False
    assert not K._chrome_on(sc)
    assert K.content_floor(_ctx(film, sc, 0.1), sc) == film.H
