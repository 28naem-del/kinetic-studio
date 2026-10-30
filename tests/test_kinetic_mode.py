"""Kinetic mode layout: countdown on the grid, type3d hero fill, chrome ticker, burst rays, slam bar."""
import math
import os
import re

import numpy as np
import pytest
import skia
from conftest import EXAMPLES

from kstudio import draw as D
from kstudio import layout as L
from kstudio.film import Film
from kstudio.scenes import kinetic as K
from kstudio.spec import load_spec

W, H = 1080, 1920
SPEC_DIR = os.path.join(EXAMPLES, "kinetic")


@pytest.fixture(scope="module")
def film():
    return Film(load_spec(SPEC_DIR), W, H)


def _ctx(film, sc, lt):
    surf = skia.Surface(film.W, film.H)
    ctx = film._ctx(surf.getCanvas(), sc.start + lt)
    ctx.lt, ctx.dur, ctx.start, ctx.scene = lt, sc.dur, sc.start, sc
    return ctx, surf


def _scene(film, kind):
    return next(s for s in film.scenes if s.type == kind)


def _timing_values(node, key=None):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _timing_values(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _timing_values(v, key)
    elif key in ("dur", "at", "stagger", "per", "land", "impact", "row_stagger", "transition_dur"):
        yield key, node


def test_spec_has_no_seconds_timings():
    vals = list(_timing_values(load_spec(SPEC_DIR)))
    assert vals
    for k, v in vals:
        if k == "at" and (v == 0 or (isinstance(v, str) and not re.search(r"\d", v))):
            continue  # film start, or a marker anchored to a scene id
        assert isinstance(v, str) and v.endswith("b"), (k, v)


def test_countdown_ring_and_pips_clear_the_chrome(film):
    sc = _scene(film, "countdown")
    ctx, _ = _ctx(film, sc, sc.dur / 2)
    g = L.grid(ctx)
    lay = sc.layout(ctx)
    floor = K.content_floor(ctx, sc)
    assert lay["floor"] == pytest.approx(min(floor, g.safe_bottom))
    # pips end at least 4 baseline units above the chrome labels
    assert lay["pip_y"] + lay["pip_h"] <= floor - g.bl(4) + 1e-6
    # the long ticks stay inside the side margins and below the safe top
    reach = lay["rad"] * sc.RING_OUT
    assert lay["cx"] - reach >= g.x0 - 1e-6 and lay["cx"] + reach <= g.x1 + 1e-6
    assert lay["cy"] - reach >= g.safe_top
    # and the pip row is centred under the ring
    assert lay["pip_y"] > lay["cy"] + reach


def test_countdown_draws_nothing_between_pips_and_chrome(film):
    sc = _scene(film, "countdown")
    ctx, surf = _ctx(film, sc, film.tl.spb * 2.5)
    ctx.c.clear(skia.ColorTRANSPARENT)
    sc.draw(ctx)
    img = surf.makeImageSnapshot().toarray()
    lay = sc.layout(ctx)
    g = L.grid(ctx)
    floor = K.content_floor(ctx, sc)
    y0 = int(math.ceil(lay["pip_y"] + lay["pip_h"])) + 2
    band = img[y0:int(floor) - 1, int(g.x0):int(g.x1), 3]
    assert band.size and int(band.max()) == 0


def test_type3d_hero_fills_ninety_percent_and_sits_level(film):
    sc = _scene(film, "type3d")
    imp_b = film.tl.beats_of(sc.p["impact"], sc.start)
    # two beats after the impact the sway crosses zero: the settled, levelled pose
    ctx, _ = _ctx(film, sc, sc.tl.beat_to_time(film.tl.time_to_beat(sc.start) + imp_b + 2) - sc.start)
    assert sc.projected_width(ctx) / W == pytest.approx(K.Type3D.FILL, abs=0.02)
    f = sc.font_for(ctx)
    rx, ry, rz, scale, _ = sc._pose(ctx, f)
    hw = D.text_w(f, str(sc.p["text"])) / 2
    assert abs(K.Type3D._level(ctx, hw, rx, ry, rz, scale) - rz) < math.radians(0.1)
    # through the bar (with the sway) the face stays within the hero band
    for lt in np.linspace(sc.p["impact"], sc.dur - 1e-3, 7):
        c2, _ = _ctx(film, sc, float(lt))
        assert 0.84 * W <= sc.projected_width(c2) <= 0.96 * W


def test_ticker_segments_are_unique_and_ordered(film):
    sc = _scene(film, "burst")
    ctx, _ = _ctx(film, sc, 0.1)
    segs = K._ticker_segments(ctx, sc)
    texts = [t for _, t in segs]
    assert len(texts) == len(set(texts))
    assert [j for j, _ in segs] == sorted(j for j, _ in segs)
    assert texts[0].startswith("STOP SCROLLING") and texts[-1] == "GO"
    # type3d and scramble both say KINETIC: one segment, owned by the first
    assert sum("KINETIC" == t for t in texts) == 1
    assert K._ticker_text(ctx, sc) == "  •  ".join(texts)


def test_ticker_override_param(film):
    sc = _scene(film, "slam")
    ctx, _ = _ctx(film, sc, 0.1)
    old = sc.p.get("ticker")
    try:
        sc.p["ticker"] = "custom line"
        assert K._ticker_segments(ctx, sc) == [(-1, "CUSTOM LINE")]
    finally:
        if old is None:
            sc.p.pop("ticker", None)
        else:
            sc.p["ticker"] = old


def test_chrome_rows_sit_on_the_safe_line(film):
    sc = _scene(film, "slam")
    ctx, _ = _ctx(film, sc, 0.1)
    base, rule_y, lab_y = K._chrome_rows(ctx)
    g = L.grid(ctx)
    assert base == pytest.approx(g.safe_bottom)
    assert lab_y < rule_y < base
    assert K.content_floor(ctx, sc) < lab_y


def _chrome_ink_box(film, sc, lt, cam=True):
    ctx, surf = _ctx(film, sc, lt)
    ctx.c.clear(skia.ColorTRANSPARENT)
    if cam:
        sc.camera(ctx)
        # exaggerate the dolly, bump, roll and shake far past the mode's range
        st = ctx.cam
        st.z, st.bump, st.roll, st.sx, st.sy = 0.2, 0.08, 1.5, 30.0, -25.0
        ctx.c.resetMatrix()
        st.apply(ctx.c, 1.0)
    K.draw_chrome(ctx, sc, sc.ground_at(lt))
    a = surf.makeImageSnapshot().toarray()[..., 3]
    ys, xs = np.nonzero(a > 8)
    return ctx, (int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()))


@pytest.mark.parametrize("kind", ["slam", "countdown", "burst"])
def test_chrome_stays_on_the_grid_under_the_camera(film, kind):
    sc = _scene(film, kind)
    lt = film.tl.spb * 2.5
    ctx, box = _chrome_ink_box(film, sc, lt)
    _, flat = _chrome_ink_box(film, sc, lt, cam=False)
    g = L.grid(ctx)
    x0, x1, y0, y1 = box
    assert x0 >= g.x0 - 1 and x1 <= g.x1 + 1
    assert y1 <= g.safe_bottom + g.bl(2) + 1  # ticker clip ends bl(2) under the baseline
    assert y0 >= g.safe_top
    # screen space: the camera does not move the chrome at all
    assert all(abs(a - b) <= 1 for a, b in zip(box, flat))


def _alpha_outside_word(film, sc, lt, **params):
    old = {k: sc.p[k] for k in params}
    sc.p.update(params)
    try:
        ctx, surf = _ctx(film, sc, lt)
        ctx.c.clear(skia.ColorTRANSPARENT)
        sc.draw(ctx)
        img = surf.makeImageSnapshot().toarray()
    finally:
        sc.p.update(old)
    g = L.grid(ctx)
    return img[int(g.safe_top):int(g.safe_top) + 160, int(g.x0):int(g.x0) + 200].astype(np.int32)


def test_burst_rays_fill_the_ground_and_turn(film):
    sc = _scene(film, "burst")
    assert sc.p["rays"] > 0 and sc.p["spin"] > 0
    lt = film.tl.spb * 1.5
    on = _alpha_outside_word(film, sc, lt)
    off = _alpha_outside_word(film, sc, lt, rays=0)
    assert np.abs(on - off).sum() > 0
    later = _alpha_outside_word(film, sc, lt + film.tl.spb * 0.5)
    assert np.abs(later - on).sum() > 0


def test_slam_bar_snaps_out_and_back(film):
    sc = _scene(film, "slam")
    assert sc.p["bar"] is True
    per = sc._per()
    g_ctx, _ = _ctx(film, sc, per * 0.5)
    f = g_ctx.fit("display", "LOOK", K._hero_w(g_ctx), K._max_type(g_ctx), 900, 112)
    widths = []
    for u in (0.0, per * 0.3, per * 0.5, per - 1e-4):
        ctx, surf = _ctx(film, sc, per * 0.5)
        ctx.c.clear(skia.ColorTRANSPARENT)
        sc._impact_bar(ctx, "LOOK", f, u, per, D.cap_height(f), "bg")
        a = surf.makeImageSnapshot().toarray()[..., 3]
        cols = np.where(a.max(axis=0) > 0)[0]
        widths.append(0 if cols.size == 0 else int(cols[-1] - cols[0]))
    assert widths[0] == 0 and widths[-1] < widths[2] * 0.1
    assert widths[2] > 0.5 * D.text_w(f, "LOOK")
