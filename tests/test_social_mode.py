"""Social mode: beat-locked spec, karaoke on the 8th grid, quote card, layout anchors."""
import copy
import json
import os
import re
from types import SimpleNamespace

import numpy as np
import pytest
from conftest import EXAMPLES

from kstudio import draw as D
from kstudio import layout as L
from kstudio.compositor import Compositor
from kstudio.film import Film
from kstudio.presets import make_output
from kstudio.scenes import social as S
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
    assert abs(tl.spb - 60 / 128) < 1e-9
    assert [sc.type for sc in film.scenes] == ["hook", "quote", "karaoke", "follow"]
    lengths = [round(tl.beats_of(sc.dur, sc.start), 6) for sc in film.scenes]
    assert lengths == [8, 8, 8, 8]
    assert 8.0 <= film.duration <= 15.0
    assert film.scenes[-1].start + film.scenes[-1].dur == pytest.approx(film.duration, abs=1e-6)
    for sc in film.scenes[1:]:
        assert tl.on_grid(sc.start, 1.0, 0.001), sc.id
        assert tl.is_downbeat(sc.start, 0.001), sc.id
    assert [b.kind for b in film.boundaries] == ["cut", "cut", "cut"]
    # every cut has a riser ending exactly on it
    for sc in film.scenes[:-1]:
        ends = [c for c in sc.cues() if c.kind == "sfx" and c.data.get("sound") == "riser_short"]
        assert ends and ends[0].at == pytest.approx(sc.dur) and ends[0].data.get("align") == "end", sc.id
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
        # the first word's pill (side padding at the 1.08 pop) starts on x0, with the kicker rule
        assert min(b[1] for b in boxes) - f.getSize() * sc.PAD * sc.POP == pytest.approx(g.x0, abs=0.5)
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
            right = a[1] + a[3] / 2 + a[3] * sc.POP / 2 + pad * sc.POP  # popped about its centre
            assert right < b[1] - 1, (a[0], b[0])


def test_quote_measure_and_role(film):
    sc = film.scenes[1]
    assert sc.measure_ok
    f, _tk, boxes = sc.layout(_Probe(film, sc, 1.0))
    assert 95 - 0.5 <= f.getSize() <= 126 + 0.5
    g = L.grid(W, H)
    # the opening quote hangs into the margin, the text after it sits on x0
    assert boxes[0][0].startswith("“") and boxes[0][1] < g.x0
    used = max(b[1] + b[3] for b in boxes) - g.x0
    assert used >= 0.70 * g.content_w
    # the paper sheet is full-bleed by design: it runs past both frame edges
    rect = sc.card_rect(_Probe(film, sc, 1.0))
    assert rect.left() < 0 and rect.right() > W


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


def test_frame_time_is_one_value_per_delivered_frame():
    """Every motion-blur sub-sample of a frame reads the same clock (no double-exposed digits)."""
    for i in range(0, 120):
        want = i / 30.0 - 0.5
        for sub in (-0.45, -0.2, 0.0, 0.2, 0.45):
            ctx = SimpleNamespace(fps=30, start=0.5, lt=(i + sub) / 30.0 - 0.5)
            assert S.frame_time(ctx) == pytest.approx(want, abs=1e-9), (i, sub)


def test_hook_pulses_run_down_the_stack_on_the_beat(film):
    hook = film.scenes[0]
    pb = hook.pulse_beats()
    n = len(hook._lines())
    assert [i for _b, i in pb] == [k % n for k in range(len(pb))]
    assert pb and pb[-1][0] < round(film.tl.beats_of(hook.dur, hook.start))
    cue_ts = [round(c.at, 6) for c in hook.cues() if c.kind == "sfx"]
    for b, i in pb:
        t = hook.bt(b)
        assert round(t, 6) in cue_ts
        assert hook._nod(t + hook.beat_len / 6, i) == pytest.approx(1.0, abs=1e-6)
        assert hook._nod(t + hook.beat_len * 0.999, i) < 0.01
        assert hook._nod(t + hook.beat_len / 6, (i + 1) % n) == 0.0


def test_quote_first_word_is_on_the_cut(film):
    sc = film.scenes[1]
    assert sc.onsets()[0] == pytest.approx(0.0, abs=1e-9)


def test_optical_margin_and_hanging_punctuation(film):
    hook = film.scenes[0]
    ctx = _Probe(film, hook, 1.0)
    f = S._serif(ctx, 224.0, 760, soft=0)
    for word in ("Two", "next", "Hat"):
        dx = S.optical_dx(f, word, 0.0)
        ink_left = D.glyph_rects(f, word, dx, 0.0, 0.0)[0][1].left()
        allow = 0.02 * 224 + 1e-3 if word[0] in S._DIAG + S._ROUND else 1e-3
        assert -allow <= ink_left <= 1e-3, word
    # the opening quote hangs: the N after it lands on the edge, the quote mark sits left of it
    dx = S.optical_dx(f, "“Nobody", 0.0)
    rects = D.glyph_rects(f, "“Nobody", dx, 0.0, 0.0)
    assert rects[0][1].left() < -0.25 * 224 and rects[0][1].right() < rects[1][1].left() + 2.0
    assert rects[1][1].left() == pytest.approx(0.0, abs=1e-3)


def test_karaoke_pill_holds_one_whole_word(tmp_path):
    """Mid-slot, the pill covers the active word from ascender to descender ink and never
    reaches a neighbour on the same line (camera off so captures are in scene coordinates)."""
    spec = copy.deepcopy(load_spec(SPEC_DIR))
    for sc in spec["scenes"]:
        sc["camera"] = False
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec))
    film = Film(load_spec(str(p)), W, H)
    sc = film.scenes[2]
    words = sc._word_list()
    ctx = _Probe(film, sc, 1.0)
    timed = sc.timed()
    g = L.grid(W, H)
    for page in sc.pages():
        f, _tk, boxes = sc._page_layout(ctx, page, words)
        asc, desc = S.ink_extent(f)
        box_of = {page[k]: boxes[k] for k in range(len(boxes))}
        for i in page:
            t_end = timed[i + 1][1] if i + 1 < len(timed) else sc.dur
            for frac in (0.15, 0.5, 0.85):
                lt = timed[i][1] + (t_end - timed[i][1]) * min(frac, 0.5 * sc.beat_len / max(t_end - timed[i][1], 1e-6))
                got = film.capture(sc, sc.start + lt).get("pill")
                assert got is not None and got["data"]["word"] == timed[i][0]
                r = got["rect"]
                _w, x, base, ww, li = box_of[i]
                assert r.left() <= x + 0.5 and r.right() >= x + ww - 0.5, (timed[i][0], lt)
                assert r.top() <= base - asc + 0.5 and r.bottom() >= base + desc - 0.5, (timed[i][0], lt)
                assert g.x0 - 0.5 <= r.left() and r.right() <= g.x1 + 0.5, (timed[i][0], lt)
                for j in (i - 1, i + 1):
                    if j in box_of and box_of[j][4] == li:
                        _wj, xj, _bj, wwj, _lj = box_of[j]
                        assert r.right() < xj or r.left() > xj + wwj, (timed[i][0], timed[j][0], lt)


def test_follow_card_closes_the_film(film):
    sc = film.scenes[3]
    assert sc.type == "follow"
    tap = sc.tl.beats_of(float(sc.p["tap_at"]), sc.start)
    assert tap == pytest.approx(round(tap), abs=1e-6)
    assert sc.sheen_at(sc._at_beat(1.5) - 0.0) is not None
    assert sc.sheen_at(sc._at_beat(tap + 0.5)) is None
    assert sc.nod(sc._at_beat(tap + 1 + 1 / 6), 0, 2) == pytest.approx(1.0, abs=1e-6)


def test_karaoke_lit_word_is_one_value_per_frame():
    """Every shutter sub-sample of a delivered frame lights the same word, so the motion blur
    never sees the pill jump from word to word inside one frame (that reads as a smear)."""
    film = Film(load_spec(SPEC_DIR), W, H)
    sc = film.scenes[2]
    fps = 30.0
    reach = 0.5 / fps * 0.5  # a 180-degree shutter reaches a quarter frame either side
    for _w, on in sc.timed():
        i = round((sc.start + on) * fps)
        for k in (i - 1, i, i + 1):
            t = k / fps
            if not sc.start + reach < t < sc.end - reach:
                continue
            lit = {film.capture(sc, t + d).get("pill", {}).get("data", {}).get("word") for d in (-reach, 0.0, reach)}
            assert len(lit) == 1, (k, lit)
