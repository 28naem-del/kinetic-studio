"""Data mode: beat-locked spec, on-beat SFX, full-width charts, hand-offs and legacy specs."""
import os

import numpy as np
import pytest
import skia
from conftest import EXAMPLES, ROOT

from kstudio import layout as L
from kstudio.film import Film
from kstudio.modes import MODES
from kstudio.scenes import data as DS
from kstudio.spec import load_spec

W, H = 1080, 1920
SPEC_DIR = os.path.join(EXAMPLES, "data")


@pytest.fixture(scope="module")
def film():
    return Film(load_spec(SPEC_DIR), W, H)


def _frame(film, t):
    s = skia.Surface(W, H)
    film.draw(s.getCanvas(), t)
    return s.makeImageSnapshot().toarray()[..., :3].astype(np.int16)


def test_scene_lengths_and_cuts_on_the_beat(film):
    tl = film.tl
    assert abs(tl.spb - 0.5) < 1e-9
    lengths = [round(tl.beats_of(sc.dur, sc.start), 6) for sc in film.scenes]
    assert lengths == [4, 4, 6, 4, 4, 4, 4]
    for sc in film.scenes[1:]:
        assert tl.on_grid(sc.start, 1.0, 0.001), sc.id
    assert 8.0 <= film.duration <= 15.0
    assert [w for w in film.lint() if w.get("level") == "warn"] == []


def test_spec_has_no_seconds_timings():
    raw = open(os.path.join(SPEC_DIR, "spec.json")).read()
    spec = load_spec(SPEC_DIR)
    keys = {"dur", "at", "first", "count", "stagger", "grow", "draw", "delay", "transition_dur"}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys and isinstance(v, (int, float)) and not (k == "at" and v == 0):
                    raise AssertionError(f"{k}={v} is in seconds")
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(spec)
    assert "zoom" not in raw


def test_sfx_land_on_the_beat(film):
    sfx = film.tl.of("sfx")
    assert len(sfx) >= 15
    on = sum(1 for m in sfx if film.tl.on_grid(m.t, 1.0, 0.0334))
    assert on / len(sfx) >= 0.8


def test_charts_fill_the_safe_width(film):
    g = L.grid(W, H)
    for sc in film.scenes:
        if sc.type not in ("line", "candles", "bars"):
            continue
        img = _frame(film, sc.end - 0.02)
        plot = DS.DataScene.plot_rect(g)
        band = img[int(plot.top()):int(plot.bottom())]
        bg = np.median(img[int(plot.top()):int(plot.bottom()), :8].reshape(-1, 3), axis=0)
        ink = (np.abs(band - bg).max(axis=2) > 40).sum(axis=0) >= 3
        cols = np.nonzero(ink)[0]
        assert cols.size, sc.id
        extent = cols[-1] - cols[0] + 1
        assert extent >= 0.8 * g.content_w, (sc.id, extent)


def test_no_overshoot_default_and_no_out_back():
    src = open(os.path.join(ROOT, "kstudio", "scenes", "data.py")).read()
    assert "out_back" not in src
    assert MODES["data"]["transition"] != "zoom"


def test_match_handoffs_resolve(film):
    by = {sc.type: sc for sc in film.scenes}
    assert film.handoff_source(by["line"], "spark") is not None
    assert film.handoff_source(by["candles"], "plot") is not None
    assert film.handoff_source(by["ticker"], "card0") is not None


def test_no_blank_frames_around_cuts(film):
    for sc in film.scenes[1:]:
        for t in (sc.start - 0.017, sc.start, sc.start + 0.034):
            img = _frame(film, t)
            assert img.std() > 6, (sc.id, t)


def test_counter_lands_on_the_downbeat(film):
    sc = film.scenes[0]
    final = DS.fmt(sc._value(), 0, "$")
    assert final == "$3,573"
    hits = [m.t for m in film.tl.of("sfx") if m.data.get("sound") == "ding" and m.t < sc.end]
    assert hits and all(film.tl.on_grid(t, 1.0, 0.001) for t in hits)


def test_breakout_candle_closes_above_level(film):
    sc = next(s for s in film.scenes if s.type == "candles")
    o = sc._ohlc()
    hist, live, _, _ = sc._plan()
    lvl = float(sc.p["level"])
    assert o[hist + live - 1][3] > lvl
    assert all(c[3] < lvl for c in o[:hist + live - 1])
    assert all(lo <= min(op, cl) and hi >= max(op, cl) for op, hi, lo, cl in o)


def test_formatting():
    assert DS.fmt(-1234.5, 1, "$") == "−$1,234.5"
    assert DS.fmt(18.4, 1, "", "%", sign=True) == "+18.4%"
    s, d0, d1 = DS.padded(42, 70840, 0, "$")
    assert s == "$00,042" and (d0, d1) == (1, 5)
    lo, hi, step = DS.axis(2881, 3573)
    assert lo <= 2881 and hi >= 3573 and (hi - lo) / step == 4


def test_legacy_seconds_spec_still_renders():
    spec = {"mode": "data", "scenes": [
        {"type": "counter", "kicker": "BTC", "value": 48.2, "decimals": 1, "prefix": "$", "suffix": "B",
         "delta": "+12.4%", "dur": 2.4},
        {"type": "candles", "title": "1H", "seed": 21, "n": 36, "start": 64000, "decimals": 0, "dur": 3.0},
        {"type": "bars", "title": "Dominance", "items": [{"label": "BTC", "value": 54}], "dur": 2.5},
        {"type": "line", "title": "Trend", "series": {"seed": 7, "n": 30}, "events": [{"i": 18, "text": "x"}],
         "dur": 2.6},
        {"type": "ticker", "items": [{"sym": "BTC", "price": 1, "change": -1}], "dur": 2.6}]}
    f = Film(spec, 270, 480)
    s = skia.Surface(270, 480)
    for sc in f.scenes:
        f.draw(s.getCanvas(), sc.start + sc.dur * 0.7)
    assert f.duration > 10


def _bg(img):
    return np.median(img[:, :8].reshape(-1, 3), axis=0)


def test_content_stays_inside_the_margins(film):
    """The camera is locked on the content plane: nothing but the ground sits in the 96 px side
    margins between the safe lines, on any scene, early or late."""
    spec = load_spec(SPEC_DIR)
    for sc_spec in spec["scenes"]:
        sc_spec["ground_style"] = "solid"  # the dotted ground lives in the margins by design
    plain = Film(spec, W, H)
    g = L.grid(W, H)
    top, bot = int(g.safe_top), int(g.safe_bottom)
    for sc in plain.scenes:
        for t in (sc.start + 0.25, sc.start + sc.dur * 0.6, sc.end - 0.02):
            img = _frame(plain, t)
            band = np.abs(img[top:bot] - _bg(img)).max(axis=2) > 40
            left, right = band[:, :int(g.x0) - 1], band[:, int(g.x1) + 1:]
            assert left.sum() == 0 and right.sum() == 0, (sc.id, t, left.sum(), right.sum())


def test_film_opens_on_the_title(film):
    g = L.grid(W, H)
    img = _frame(film, 0.0)
    head = img[int(DS.DataScene.head_top(g)):int(DS.DataScene.head_top(g) + g.bl(16)), int(g.x0):int(g.x1)]
    assert (np.abs(head - _bg(img)).max(axis=2) > 120).sum() > 2000


def test_counter_counts_from_the_series_start(film):
    sc = film.scenes[0]
    assert abs(sc._from() - sc._spark()[0]) < 1e-9
    closer = film.scenes[-1]
    assert closer.type == "counter" and closer._from() == 0.0
    assert DS.fmt(closer._value(), 1, "+", "%") == "+10.3%"


def test_delta_false_hides_the_pill():
    f = Film({"mode": "data", "scenes": [{"type": "counter", "value": 5, "delta": False, "dur": "4b"}]}, W, H)
    assert f.scenes[0]._delta() == ""


def test_breadth_bar_has_no_slivers_before_it_grows(film):
    sc = next(s for s in film.scenes if s.type == "ticker")
    g = L.grid(W, H)
    plot = DS.DataScene.plot_rect(g)
    band_top = int(plot.top() + plot.height() * 0.8)
    for t in (sc.start + 0.05, sc.start + 0.3):
        img = _frame(film, t)
        rows = img[band_top:int(plot.bottom()) + 1, int(g.x0):int(g.x1)]
        red = (rows[..., 0] > 180) & (rows[..., 1] < 120)
        green = (rows[..., 1] > 180) & (rows[..., 0] < 120)
        assert red.sum() == 0 and green.sum() == 0, t


def test_candle_read_out_never_covers_the_axis(film):
    """The last price is read out top-left of the plot; the axis gutter holds only its labels."""
    sc = next(s for s in film.scenes if s.type == "candles")
    g = L.grid(W, H)
    plot = DS.DataScene.plot_rect(g)
    gx = int(g.x1 - DS.DataScene.gutter_w(g)) + 4
    for t in np.arange(sc.start + 0.5, sc.end, 0.25):
        img = _frame(film, float(t))
        gut = img[int(plot.top()):int(plot.bottom()), gx:int(g.x1)]
        green = (gut[..., 1] > 180) & (gut[..., 0] < 120) & (gut[..., 2] < 200)
        assert green.sum() < 40, (float(t), int(green.sum()))
