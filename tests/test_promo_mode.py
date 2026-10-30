"""Promo example: the frame stays on the grid under the camera, the CTA reads, the end card holds."""
import os

import numpy as np
import pytest
import skia
from conftest import EXAMPLES

from kstudio import layout as L
from kstudio.compositor import Compositor
from kstudio.film import Film
from kstudio.presets import make_output
from kstudio.scenes.promo import _pin, contrast, label_ink
from kstudio.spec import load_spec

W, H = 1080, 1920


@pytest.fixture(scope="module")
def film():
    return Film(load_spec(os.path.join(EXAMPLES, "promo")), W, H)


def _screen(cam, r):
    """Rect r (world) as drawn on screen through the camera."""
    assert cam.tilt() is None
    return cam.matrix(1.0).mapRect(r)


def test_callouts_land_inside_margin_and_safe_lines_on_screen(film):
    sc = film.scenes[0]
    comp = Compositor(film, make_output("reel", quality="draft"))
    seen = []
    orig = sc._callout

    def spy(*a):
        r = orig(*a)
        if r is not None:
            seen.append(_screen(a[0].cam, r))
        return r

    sc._callout = spy
    try:  # settled pills, across the camera push and the beat bumps of the hold
        for b in (5.0, 6.0, 6.5, 7.0, 7.75):
            comp.frame(int(round((sc.start + sc.bt(b)) * 30)))
    finally:
        del sc._callout
    g = L.grid(W, H)
    assert len(seen) >= 10
    for r in seen:
        assert g.x0 - 0.5 <= r.left() and r.right() <= g.x1 + 0.5, (r.left(), r.right())
        assert g.safe_top - 0.5 <= r.top() and r.bottom() <= g.safe_bottom + 0.5, (r.top(), r.bottom())


def test_pinned_grid_maps_back_to_the_screen_lines(film):
    sc = film.scenes[3]
    comp = Compositor(film, make_output("reel", quality="draft", scale=0.25))
    grids = []
    orig = sc.layout

    def spy(ctx):
        lay = orig(ctx)
        grids.append((ctx.cam, lay["g"]))
        return lay

    sc.layout = spy
    try:
        comp.frame(int(round((sc.start + sc.bt(2.0)) * 30)))
    finally:
        del sc.layout
    assert grids
    cam, g = grids[-1]
    s = L.grid(g.W, g.H)
    r = _screen(cam, skia.Rect(g.x0, g.safe_top, g.x1, g.safe_bottom))
    assert r.left() >= s.x0 - 0.01 and r.right() <= s.x1 + 0.01
    assert r.top() >= s.safe_top - 0.01 and r.bottom() <= s.safe_bottom + 0.01


def test_pin_is_the_static_grid_without_a_camera(film):
    sc = film.scenes[0]
    ctx = film._ctx(skia.Canvas(np.zeros((8, 8, 4), np.uint8)), sc.start + 1.0)
    assert ctx.cam is None
    g, s = _pin(ctx), L.grid(ctx)
    assert (g.x0, g.x1, g.safe_top, g.safe_bottom, g.anchor_y) == (s.x0, s.x1, s.safe_top, s.safe_bottom, s.anchor_y)


def test_cta_label_contrast_is_at_least_4_5(film):
    sc = film.scenes[2]
    ctx = film._ctx(skia.Canvas(np.zeros((8, 8, 4), np.uint8)), sc.start + 1.0)
    ink = label_ink(ctx, ("accent", "accent2"))
    for key in ("accent", "accent2"):
        assert contrast(ctx.pal.rgb(ink), ctx.pal.rgb(key)) >= 4.5, key


def test_cta_copy_is_gone_before_the_circle_wipe_opens(film):
    cta = film.scenes[2]
    wipe = film.boundaries[2]
    assert wipe.kind == "wipe" and wipe.a is cta
    lt = wipe.t0 - cta.start
    for order in range(5):
        assert cta._exit(lt, cta.beat_len, order) == pytest.approx(1.0), order
    assert cta._exit(cta.tap() + cta.beat_len, cta.beat_len, 4) == 0.0   # the copy holds through the tap


def test_end_card_is_full_within_one_beat():
    out = make_output("reel", quality="draft", scale=0.25)
    film = Film(load_spec(os.path.join(EXAMPLES, "promo")), out.width, out.height)
    sc = film.scenes[3]
    comp = Compositor(film, out)
    g = L.grid(out.width, out.height)
    for b in (0.9, 1.1, 3.5):   # either side of the hit flash, and late in the hold
        img = comp.frame(int(round((sc.start + sc.bt(b)) * out.fps)))[..., :3].astype(np.float32)
        luma = 0.2126 * img[..., 2] + 0.7152 * img[..., 1] + 0.0722 * img[..., 0]
        badges = luma[int(g.safe_bottom - g.bl(14)):int(g.safe_bottom), int(g.x0):int(g.x1)]
        words = luma[int(g.H * 0.3):int(g.H * 0.6), int(g.x0):int(g.x1)]
        assert badges.max() - np.median(badges) > 120, b    # badge copy on screen
        assert words.max() - np.median(words) > 150, b      # icon / wordmark / tagline


def test_spec_uses_mode_audit_defaults_and_beat_timings():
    raw = load_spec(os.path.join(EXAMPLES, "promo"))
    assert "audit" not in raw
    film = Film(load_spec(os.path.join(EXAMPLES, "promo")), W, H)
    assert film.cfg["audit"]["min_motion"] == pytest.approx(0.7)
    timed = ("dur", "tilt", "land", "first_callout", "callout_gap", "per", "tap_at", "hit_at", "enter")
    for sc in raw["scenes"]:
        for k in timed:
            if k in sc:
                assert isinstance(sc[k], str) and sc[k].endswith("b"), (sc.get("id"), k, sc[k])
