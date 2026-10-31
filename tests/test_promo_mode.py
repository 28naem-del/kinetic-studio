"""Promo example: the frame stays on the grid under the camera, the CTA reads and leaves ahead of the\ncircle wipe, the end card holds inside the safe lines."""
import os

import numpy as np
import pytest
import skia
from conftest import EXAMPLES

from kstudio import draw as D
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


def _cta_layout_at(film, t):
    """(scene, ctx, layout) of the CTA as drawn at film time t (through the compositor and its camera)."""
    sc = film.scenes[2]
    comp = Compositor(film, make_output("reel", quality="draft"))
    got = []
    orig = sc.layout

    def spy(ctx):
        lay = orig(ctx)
        got.append((ctx, lay))
        return lay

    sc.layout = spy
    try:
        comp.frame(int(round(t * 30)))
    finally:
        del sc.layout
    assert got
    return (sc, *got[-1])


def test_cta_block_spans_the_safe_area_with_the_button_in_the_thumb_zone(film):
    sc = film.scenes[2]
    sc, ctx, lay = _cta_layout_at(film, sc.start + sc.bt(3))
    g, b, reach = lay["g"], lay["button"], lay["reach"]
    assert lay["proof_base"] + reach < b.top()                                   # proof row clears the ring wave
    assert b.bottom() + reach < lay["note_base"] - D.cap_height(lay["nf"])      # and so does the note
    assert lay["rule_y"] <= g.safe_bottom
    assert b.centerY() > 0.55 * g.H                                             # the button sits low, in thumb reach
    cx, cy = (float(v) for v in film.boundaries[2].params["at"])
    assert b.left() < cx * W < b.right() and b.top() < cy * H < b.bottom()      # the end card opens from the button


def test_cta_copy_leaves_line_by_line_ahead_of_the_circle_wipe_edge(film):
    wipe = film.boundaries[2]
    assert wipe.kind == "wipe" and wipe.a is film.scenes[2]
    sc, ctx, lay = _cta_layout_at(film, wipe.t0 - 0.05)
    g, bl, hf = lay["g"], sc.beat_len, lay["hf"]
    pieces = [skia.Rect(g.x0, ln.baseline - D.cap_height(hf), g.x1, ln.baseline) for ln in lay["blk"].lines]
    pieces += [skia.Rect(g.x0, lay["proof_base"] - g.bl(6), g.x1, lay["proof_base"]),
               skia.Rect(g.x0, lay["note_base"] - D.cap_height(lay["nf"]), g.x1, lay["note_base"])]
    covers = [sc.covered_at(ctx, g, r) for r in pieces]
    for cv in covers:
        assert cv is not None
        assert wipe.t0 - sc.start - 1e-6 <= cv <= wipe.t1 - sc.start + 1e-6
        assert sc._exit(cv - 0.125 * bl, bl, 0, cv) == pytest.approx(1.0)   # gone before the edge arrives
        assert sc._exit(sc.tap() + bl, bl, 0, cv) == 0.0                    # the copy holds through the tap
    assert len({round(cv, 3) for cv in covers}) >= 3                        # line by line, not all at once
    assert covers[-1] < covers[0]                                           # the note (nearest the button) first


def test_end_card_rings_never_cross_the_top_safe_line(film, monkeypatch):
    sc = film.scenes[3]
    rings, grids = [], []
    orig_rrect, orig_layout = D.rrect, sc.layout

    def rrect(c, x, y, w, h, r, p):
        if p.getStyle() == skia.Paint.kStroke_Style and p.getAlpha() > 0:
            rings.append((y, p.getStrokeWidth()))
        orig_rrect(c, x, y, w, h, r, p)

    def layout(ctx):
        lay = orig_layout(ctx)
        grids.append(lay["g"])
        return lay

    monkeypatch.setattr(D, "rrect", rrect)
    monkeypatch.setattr(sc, "layout", layout)
    canvas = skia.Canvas(np.zeros((8, 8, 4), np.uint8))
    n = 0
    for k in range(8, 32):   # every 8th of the hold, against that frame's (camera-pinned) safe line
        rings.clear()
        grids.clear()
        film.draw(canvas, sc.start + sc.bt(k / 8))
        g = grids[-1]
        tops = [y - sw / 2 for y, sw in rings if sw > g.bl(1)]
        n += len(tops)
        assert all(top >= g.safe_top - 0.5 for top in tops), (k, min(tops), g.safe_top)
    assert n > 0


def test_end_card_url_ink_reads(film):
    ctx = film._ctx(skia.Canvas(np.zeros((8, 8, 4), np.uint8)), film.scenes[3].start + 1.0)
    for ground in ("bg", "bg2"):
        assert contrast(ctx.pal.rgb("accent2"), ctx.pal.rgb(ground)) >= 4.5, ground


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
