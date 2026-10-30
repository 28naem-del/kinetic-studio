"""Promo mode: beat-locked timing, grid geometry and the product-launch scenes."""
import os

import numpy as np
import pytest
from conftest import EXAMPLES

from kstudio import layout as L
from kstudio.compositor import Compositor
from kstudio.film import Film
from kstudio.presets import make_output
from kstudio.spec import load_spec

W, H = 1080, 1920


@pytest.fixture(scope="module")
def film():
    return Film(load_spec(os.path.join(EXAMPLES, "promo")), W, H)


class _Probe:
    """Minimal frame context for layout queries (no drawing)."""

    def __init__(self, film, sc, lt):
        import skia
        self._ctx = film._ctx(skia.Canvas(np.zeros((8, 8, 4), np.uint8)), sc.start + lt)

    def __getattr__(self, k):
        return getattr(self._ctx, k)


def test_scene_lengths_and_cuts_are_whole_beats(film):
    tl = film.tl
    assert abs(tl.spb - 60 / 112) < 1e-9
    lengths = [round(tl.beats_of(sc.dur, sc.start), 6) for sc in film.scenes]
    assert lengths == [8, 8, 8, 4]
    for sc in film.scenes[1:]:
        assert tl.on_grid(sc.start, 1.0, 0.001), sc.id          # cut within 1 ms of a beat
        assert tl.is_downbeat(sc.start, 0.001), sc.id
    assert [b.kind for b in film.boundaries] == ["push", "slice", "wipe"]
    assert not [w for w in film.lint() if w["level"] == "warn"]


def test_sfx_and_hits_sit_on_beats(film):
    tl = film.tl
    marks = tl.of("sfx") + tl.of("hit")
    assert marks
    on = [m for m in marks if tl.on_grid(m.t, 1.0, 0.0334)]
    assert len(on) / len(marks) >= 0.95


def test_device_is_70pc_tall_and_inside_frame(film):
    sc = film.scenes[0]
    assert sc.type == "device"
    for lt in (sc.beat_len * 2, sc.beat_len * 6):
        geo = sc.geometry(_Probe(film, sc, lt))
        assert geo["ph"] >= 0.69 * H
        assert geo["cy"] - geo["ph"] / 2 >= 0 and geo["cy"] + geo["ph"] / 2 <= H
        assert geo["cx"] - geo["pw"] / 2 >= 0 and geo["cx"] + geo["pw"] / 2 <= W
        hf, blk = geo["head"]
        assert abs(hf.getSize() - 168) < 0.5 and hf.getTypeface().fontStyle().weight() == 700
        assert blk.lines[0].x == pytest.approx(L.grid(W, H).x0)


def test_callouts_inside_safe_area(film):
    sc = film.scenes[0]
    out = make_output("reel", quality="draft")
    comp = Compositor(film, out)
    rects = []
    orig = sc._callout

    def spy(*a):
        r = orig(*a)
        if r is not None:
            rects.append(r)
        return r

    sc._callout = spy
    try:
        comp.frame(int((sc.start + sc.beat_len * 7) * out.fps))
    finally:
        del sc._callout
    assert len(rects) == 3
    g = L.grid(W, H)
    for r in rects:
        assert g.x0 - 0.5 <= r.left() and r.right() <= g.x1 + 0.5
        assert g.safe_top - 0.5 <= r.top() and r.bottom() <= g.safe_bottom + 0.5


def test_cta_headline_and_brand_wordmark_roles(film):
    cta, brand = film.scenes[2], film.scenes[3]
    lay = cta.layout(_Probe(film, cta, 1.0))
    assert abs(lay["hf"].getSize() - 168) < 0.5 and lay["hf"].getTypeface().fontStyle().weight() == 700
    assert lay["blk"].lines[0].x == pytest.approx(L.grid(W, H).x0)
    btn = lay["button"]
    g = L.grid(W, H)
    assert g.x0 - 0.5 <= btn.left() and btn.right() <= g.x1 + 0.5 and btn.bottom() <= g.safe_bottom
    # press lands on a bar line
    assert film.tl.is_downbeat(cta.start + cta.tap(), 0.001)
    bl = brand.layout(_Probe(film, brand, 1.0))
    assert abs(bl["f"].getSize() - 224) < 0.5 and bl["f"].getTypeface().fontStyle().weight() == 700


def test_features_one_per_two_beats(film):
    sc = film.scenes[1]
    assert sc._per() == pytest.approx(sc.bt(2), abs=1e-6)
    starts = sorted({round(c.at, 6) for c in sc.cues()})
    assert starts == pytest.approx([sc.bt(2 * i) for i in range(4)], abs=1e-6)


def test_no_blank_frames_around_cuts(film):
    out = make_output("reel", quality="draft", scale=0.25)
    comp = Compositor(film, out)
    for sc in film.scenes[1:]:
        f0 = int(round(sc.start * out.fps))
        for f in (f0 - 2, f0 - 1, f0, f0 + 1, f0 + 2):
            img = comp.frame(f)[..., :3].astype(np.float32)
            luma = (0.2126 * img[..., 2] + 0.7152 * img[..., 1] + 0.0722 * img[..., 0]).mean()
            assert luma >= 14 and img.std() > 6, (sc.id, f, luma)


def test_legacy_fields_still_work():
    spec = {"mode": "promo", "scenes": [
        {"type": "device", "headline": "Meet Flowly", "sub": "Your day, in focus", "swing": 20, "scroll": 0.4,
         "callouts": [{"text": "Smart focus timer", "icon": "clock", "side": "left"}], "dur": 2.0},
        {"type": "features", "heading": "Built for deep work", "columns": 2, "stagger": 0.22, "first": 0.3,
         "items": [{"icon": "bolt", "title": "Instant", "text": "One tap"}] * 4, "dur": 2.0},
        {"type": "cta", "headline": "Start free today", "button": "Get Flowly", "sub": "iOS", "tap_at": 1.1, "dur": 2.0},
        {"type": "brand", "text": "flowly", "dur": 2.0}]}
    out = make_output("reel", quality="draft", scale=0.25)
    film = Film(spec, out.width, out.height)
    assert film.scenes[0]._yaw() == (-10.0, 10.0)
    assert film.scenes[1]._grid_mode()
    assert film.scenes[2].tap() == pytest.approx(1.1)
    comp = Compositor(film, out)
    for t in (1.0, 3.0, 5.0, 7.0):
        assert comp.frame(int(t * out.fps))[..., :3].std() > 3.0


def test_text_led_blocks_fill_60pc_of_the_height(film):
    g = L.grid(W, H)
    cta, brand = film.scenes[2], film.scenes[3]
    lay = cta.layout(_Probe(film, cta, 1.0))
    assert cta.p["lockup"] and cta.p["sub"]
    assert g.fills(g.safe_top, g.safe_bottom)            # lockup on the top safe line .. sub on the bottom one
    from kstudio import draw as D
    assert lay["blk"].lines[0].baseline - D.cap_height(lay["hf"]) == pytest.approx(g.anchor_y, abs=1.0)  # cap top on 560
    bl = brand.layout(_Probe(film, brand, 1.0))
    assert g.fills(bl["mark_top"], g.safe_bottom)
