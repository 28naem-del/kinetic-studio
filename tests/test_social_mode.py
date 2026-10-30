"""Social mode: beat-locked spec, karaoke on the 8th grid, quote card, layout anchors."""
import json
import os
import re

import numpy as np
import pytest
from conftest import EXAMPLES

from kstudio import draw as D
from kstudio import layout as L
from kstudio.compositor import Compositor
from kstudio.film import Film
from kstudio.presets import make_output
from kstudio.spec import load_spec

W, H = 1080, 1920
SPEC_DIR = os.path.join(EXAMPLES, "social")


@pytest.fixture(scope="module")
def film():
    return Film(load_spec(SPEC_DIR), W, H)


class _Probe:
    """Minimal frame context for layout queries (no drawing)."""

    def __init__(self, film, sc, lt):
        import skia
        self._ctx = film._ctx(skia.Canvas(np.zeros((8, 8, 4), np.uint8)), sc.start + lt)

    def __getattr__(self, k):
        return getattr(self._ctx, k)


def _timing_values(node, key=None):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _timing_values(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from _timing_values(v, key)
    elif key in ("dur", "at", "stagger", "first", "per", "tap_at", "lead", "tail", "pulse", "transition_dur"):
        yield key, node


def test_spec_has_no_seconds_timings():
    spec = load_spec(SPEC_DIR)
    vals = list(_timing_values(spec))
    assert vals
    for k, v in vals:
        if k == "at" and isinstance(v, str) and not re.search(r"\d", v):
            continue  # marker anchored to a scene id
        assert isinstance(v, str) and v.endswith("b"), (k, v)


def test_scene_lengths_and_cuts_are_whole_beats(film):
    tl = film.tl
    assert abs(tl.spb - 60 / 86) < 1e-9
    assert [sc.type for sc in film.scenes] == ["hook", "quote", "karaoke"]
    lengths = [round(tl.beats_of(sc.dur, sc.start), 6) for sc in film.scenes]
    assert lengths == [4, 8, 8]
    assert 8.0 <= film.duration <= 15.0
    for sc in film.scenes[1:]:
        assert tl.on_grid(sc.start, 1.0, 0.001), sc.id
        assert tl.is_downbeat(sc.start, 0.001), sc.id
    assert [b.kind for b in film.boundaries] == ["wipe", "cut"]
    assert not [w for w in film.lint() if w["level"] == "warn"]


def test_karaoke_words_on_the_8th_grid_pages_on_bars(film):
    sc = film.scenes[2]
    tm = sc.timed()
    assert len(tm) == len(sc._word_list())
    for _w, t in tm:
        assert film.tl.on_grid(sc.start + t, 2.0, 0.001), t
    for p in sc.pages():
        assert film.tl.is_downbeat(sc.start + tm[p[0]][1], 0.001)
    # strictly increasing, one word per 8th inside a page
    ts = [t for _w, t in tm]
    assert all(b > a for a, b in zip(ts, ts[1:]))


def test_sfx_on_beats_and_one_per_bar(film):
    tl = film.tl
    marks = tl.of("sfx") + tl.of("hit")
    on = [m for m in marks if tl.on_grid(m.t, 1.0, 0.0334)]
    assert len(on) / len(marks) >= 0.8
    bars = {tl.bar_of(m.t + 1e-4) for m in tl.of("sfx")}
    nbars = int(round(tl.beats_of(film.duration, 0.0) / tl.beats_per_bar))
    assert set(range(1, nbars + 1)) <= bars


def test_kicker_on_anchor_and_block_in_upper_half(film):
    g = L.grid(W, H)
    assert g.anchor_y == pytest.approx(560, abs=1)
    sc = film.scenes[2]
    ctx = _Probe(film, sc, 1.0)
    words = sc._word_list()
    for page in sc.pages():
        f, _tk, boxes = sc._page_layout(ctx, page, words)
        assert f.getSize() >= 95 - 0.5
        assert boxes[0][1] == pytest.approx(g.x0)
        top = boxes[0][2] - D.cap_height(f)
        bottom = boxes[-1][2]
        assert (top + bottom) / 2 <= 0.45 * H


def test_karaoke_word_gap_leaves_room_for_the_pill(film):
    """The active word at 1.08 x plus pill padding never reaches the next word on its line."""
    sc = film.scenes[2]
    ctx = _Probe(film, sc, 1.0)
    words = sc._word_list()
    for page in sc.pages():
        f, _tk, boxes = sc._page_layout(ctx, page, words)
        pad = f.getSize() * sc.PAD
        for a, b in zip(boxes, boxes[1:]):
            if a[4] != b[4]:
                continue
            right = a[1] + a[3] / 2 + a[3] * 1.08 / 2 + pad
            assert right < b[1] - 1, (a[0], b[0])


def test_quote_measure_and_role(film):
    sc = film.scenes[1]
    assert sc.measure_ok
    f, _tk, boxes = sc.layout(_Probe(film, sc, 1.0))
    assert 95 - 0.5 <= f.getSize() <= 126 + 0.5
    g = L.grid(W, H)
    # the opening quote hangs into the margin, the text after it sits on x0
    assert boxes[0][0].startswith("“") and boxes[0][1] < g.x0
    rect = sc.card_rect(_Probe(film, sc, 1.0))
    used = max(b[1] + b[3] for b in boxes) - g.x0
    assert used >= 0.70 * (rect.right() - g.x0)


def test_follow_takes_the_pill(tmp_path):
    """A cut from karaoke into the follow card hands the pill over (it becomes the button)."""
    spec = {"mode": "social", "creator": {"name": "A B", "handle": "@ab"}, "scenes": [
        {"type": "karaoke", "text": "One two three four.", "per": "1/2b", "dur": "4b"},
        {"type": "follow", "transition": {"kind": "cut", "on": "downbeat"}, "tap_at": "2b", "dur": "4b",
         "stats": [{"value": "12", "label": "clips"}]}]}
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec))
    film = Film(load_spec(str(p)), W, H)
    follow = film.scenes[1]
    assert film.handoff_source(follow, "pill") is not None
    out = make_output("reel", quality="draft", scale=0.25)
    comp = Compositor(film, out)
    img = comp.frame(int((follow.start + follow.beat_len * 3) * out.fps))
    assert img.std() > 6


def test_first_and_cut_frames_are_inked(film):
    out = make_output("reel", quality="draft", scale=0.25)
    comp = Compositor(film, out)
    frames = [0] + [int(round(sc.start * out.fps)) + d for sc in film.scenes[1:] for d in (-1, 0, 1)]
    for f in frames:
        img = comp.frame(f)[..., :3].astype(np.float32)
        luma = (0.2126 * img[..., 2] + 0.7152 * img[..., 1] + 0.0722 * img[..., 0]).mean()
        assert luma >= 14 and img.std() > 6, (f, luma)


def test_legacy_social_spec_still_builds(tmp_path):
    spec = {"mode": "social", "handle": "@old", "scenes": [
        {"type": "quote", "text": "Small steps every day beat big plans.", "author": "Someone", "dur": 3.0,
         "stagger": 0.1},
        {"type": "karaoke", "text": "one two three four five six", "timing": "even", "lines_per_page": 1,
         "dur": 3.0},
        {"type": "follow", "dur": 2.0}]}
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec))
    film = Film(load_spec(str(p)), 540, 960)
    out = make_output("reel", quality="draft", scale=0.25)
    comp = Compositor(film, out)
    for t in (0.5, 4.0, 7.0):
        img = comp.frame(int(t * out.fps))
        assert img.std() > 4
