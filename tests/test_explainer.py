"""Explainer mode: beat-locked timing, editorial type roles, grid geometry and the match cut."""
import os
import re

import numpy as np
import pytest
from conftest import EXAMPLES, ROOT

from kstudio import layout as L
from kstudio.compositor import Compositor
from kstudio.film import Film
from kstudio.presets import make_output
from kstudio.scenes import explainer as X
from kstudio.spec import load_spec

W, H = 1080, 1920


@pytest.fixture(scope="module")
def film():
    return Film(load_spec(os.path.join(EXAMPLES, "explainer")), W, H)


def _spy_rise(monkeypatch):
    seen = []
    orig = X._rise

    def spy(ctx, text, x, baseline, f, color, u, *a, **k):
        seen.append((text, round(f.getSize(), 1), x, baseline, f.getTypeface().fontStyle().weight()))
        return orig(ctx, text, x, baseline, f, color, u, *a, **k)

    monkeypatch.setattr(X, "_rise", spy)
    return seen


def _draw_at(film, t, scale=0.25):
    out = make_output("reel", quality="draft", scale=scale)
    return Compositor(film, out).frame(int(round(t * out.fps)))


def test_scene_lengths_and_cuts_are_whole_beats(film):
    tl = film.tl
    assert abs(tl.spb - 60 / 84) < 1e-9
    assert [sc.type for sc in film.scenes] == ["cover", "definition", "diagram", "bullets", "end_card"]
    lengths = [round(tl.beats_of(sc.dur, sc.start), 6) for sc in film.scenes]
    assert lengths == [3, 4, 5, 4, 4]
    for sc in film.scenes[1:]:
        assert tl.on_grid(sc.start, 1.0, 0.001), sc.id
    assert tl.is_downbeat(film.scenes[-1].start, 0.001)                  # end card on the bar line
    kinds = [(b.kind, round(tl.beats_of(b.dur, b.t - b.dur / 2), 3) if b.dur else 0) for b in film.boundaries]
    assert kinds == [("wipe", 1.0), ("match", 0), ("push", 0.5), ("cut", 0)]
    assert not [w for w in film.lint() if w["level"] == "warn"]


def test_sfx_on_the_beat(film):
    tl = film.tl
    marks = tl.of("sfx")
    assert len(marks) >= 12
    assert all(tl.on_grid(m.t, 4, 0.0334) for m in marks)                # every tick on a 16th
    on = [m for m in marks if tl.on_grid(m.t, 1, 0.0334)]
    assert len(on) / len(marks) >= 0.8                                   # and most on the beat


def test_ticks_follow_nodes_and_bullets(film):
    dg, bl = film.scenes[2], film.scenes[3]
    ap = dg._appear()
    assert ap == pytest.approx([dg.bt(0.5 * i) for i in range(len(dg.p["nodes"]))], abs=1e-6)
    node_cues = sorted(c.at for c in dg.cues() if c.data.get("sound", "").startswith("tick"))
    assert node_cues == pytest.approx(ap, abs=1e-6)
    item_cues = sorted(c.at for c in bl.cues())
    assert item_cues == pytest.approx([bl.bt(1 + i) for i in range(len(bl.p["items"]))], abs=1e-6)


def test_type_roles_and_anchor(film, monkeypatch):
    seen = _spy_rise(monkeypatch)
    g = L.grid(W, H)
    _draw_at(film, 1.5, scale=1.0)                                       # cover, fully on
    head = [s for s in seen if s[1] >= 150]
    assert head and all(abs(s[1] - 168) < 0.5 for s in head)
    assert all(s[2] == pytest.approx(g.x0) for s in head)               # left-aligned on the grid
    seen.clear()
    bl = film.scenes[3]
    _draw_at(film, bl.start + bl.beat_len * 3.9, scale=1.0)
    sizes = {s[1] for s in seen}
    assert 126.0 in sizes and 53.0 in sizes
    assert not [s for s in seen if s[1] < 30]


def test_text_led_scenes_start_on_the_anchor(film, monkeypatch):
    from kstudio import draw as D
    seen = _spy_rise(monkeypatch)
    g = L.grid(W, H)
    bl = film.scenes[3]
    _draw_at(film, bl.start + bl.beat_len * 3.5, scale=1.0)
    heading = next(s for s in seen if s[0] == "Locality")
    assert heading[1] == 126.0
    cap = heading[3] - g.anchor_y
    assert 0.6 * 126 < cap < 0.8 * 126                                   # cap top sits on 560
    assert heading[2] == pytest.approx(g.x0)
    del D


def test_ladder_fills_the_frame(film):
    dg = film.scenes[2]
    g = L.grid(W, H)
    rects = dg._ladder(None, g)
    assert len(rects) == 4
    left = min(r.left() for r in rects)
    right = max(r.right() for r in rects)
    assert right - left >= 900
    assert min(r.width() for r in rects) >= 200 and min(r.height() for r in rects) >= 200 * 0.9
    top, bot = min(r.top() for r in rects), max(r.bottom() for r in rects)
    assert top == pytest.approx(g.anchor_y) and bot == pytest.approx(g.safe_bottom)
    assert (bot - top) / H >= 0.5


def test_term_hands_off_into_the_first_node(film):
    dg = film.scenes[2]
    src = film.handoff_source(dg, "term")
    assert src is not None
    assert src["data"]["text"] == "Cache"
    r = src["rect"]
    g = L.grid(W, H)
    assert r.width() > 200 and r.left() >= g.x0 - g.bl(2) - 1 and abs(r.top() - g.anchor_y) < 80


def test_title_half_visible_by_frame_3(film):
    out = make_output("reel", quality="draft", scale=0.25)
    comp = Compositor(film, out)
    bg = comp.frame(0)[..., :3].astype(np.float32)
    full = comp.frame(int(1.9 * out.fps))[..., :3].astype(np.float32)
    f3 = comp.frame(3)[..., :3].astype(np.float32)
    y0, y1 = int(0.28 * f3.shape[0]), int(0.55 * f3.shape[0])
    ref = np.median(full[y0:y1], axis=(0, 1))
    ink = lambda im: (np.abs(im[y0:y1] - ref).sum(-1) > 120).sum()     # noqa: E731
    assert ink(f3) >= 0.5 * ink(full)
    del bg


def test_eases_are_editorial():
    src = open(os.path.join(ROOT, "kstudio", "scenes", "explainer.py")).read()
    assert "out_back" not in src and "in_out_back" not in src
    assert X.ENTER.__name__ in ("out_expo", "out_quint") and X.EXIT.__name__ in ("in_quart", "in_expo")


def test_no_blank_frames_around_cuts(film):
    out = make_output("reel", quality="draft", scale=0.25)
    comp = Compositor(film, out)
    for sc in film.scenes[1:]:
        f0 = int(round(sc.start * out.fps))
        for f in (f0 - 1, f0, f0 + 1):
            img = comp.frame(f)[..., :3].astype(np.float32)
            luma = (0.2126 * img[..., 2] + 0.7152 * img[..., 1] + 0.0722 * img[..., 0]).mean()
            assert luma >= 14 and img.std() > 6, (sc.id, f, luma)


def test_every_scene_type_and_layout_draws():
    nodes = [{"label": "Input", "sub": "1 KB", "value": "2 ns"}, {"label": "Model"}, {"label": "Output"}]
    spec = {"mode": "explainer", "music": {"bpm": 84}, "scenes": [
        {"type": "cover", "title": "What is a\nhash table?", "accent": "hash", "subtitle": "Primer", "dur": "2b"},
        {"type": "definition", "term": "Hash", "definition": "A short fixed-size digest of any input.",
         "stats": [{"label": "Lookup", "value": "O(1)"}], "dur": "2b"},
        {"type": "diagram", "heading": "Chain", "nodes": nodes, "layout": "chain", "dur": "2b",
         "transition": {"kind": "match", "element": "term"}},
        {"type": "diagram", "heading": "Radial", "nodes": nodes + [{"label": "Cache"}], "layout": "radial",
         "callouts": [{"node": 0, "text": "start"}], "dur": "2b"},
        {"type": "diagram", "heading": "Manual", "layout": "manual", "dur": "2b",
         "nodes": [{"label": "A", "x": 0.3, "y": 0.4}, {"label": "B", "x": 0.7, "y": 0.6}]},
        {"type": "diagram", "heading": "Ladder", "nodes": nodes, "layout": "ladder", "edges": [{"from": "0", "to": "1", "style": "arrow", "label": "next"}, {"from": "1", "to": "2"}], "dur": "2b"},
        {"type": "bullets", "heading": "Icons", "numbered": False,
         "items": [{"text": "Fast", "icon": "bolt"}, "Plain string"], "dur": "2b"},
        {"type": "keyfact", "value": "42%", "label": "of lookups", "detail": "in the benchmark", "dur": "2b",
         "transition": {"kind": "match", "element": "term"}},
        {"type": "end_card", "title": "Thanks", "dur": "2b"}]}
    out = make_output("reel", quality="draft", scale=0.25)
    film = Film(spec, out.width, out.height)
    comp = Compositor(film, out)
    for sc in film.scenes:
        img = comp.frame(int((sc.start + sc.dur * 0.8) * out.fps))
        assert img[..., :3].std() > 3.0, sc.type


def test_legacy_explainer_fields_still_work():
    spec = {"mode": "explainer", "scenes": [
        {"type": "title", "title": "How does a CPU cache work?", "subtitle": "A 15-second primer", "dur": 2.0},
        {"type": "definition", "term": "Cache", "phonetic": "/kash/", "pos": "noun",
         "definition": "A small, fast memory.", "dur": 2.0},
        {"type": "diagram", "nodes": [{"label": "L1", "sub": "1 ns"}, {"label": "RAM", "sub": "80 ns"}],
         "layout": "chain", "stagger": 0.3, "first": 0.2, "dur": 2.5},
        {"type": "bullets", "heading": "Why", "items": ["One", "Two"], "stagger": 0.4, "dur": 2.0},
        {"type": "end_card", "title": "Keep data close.", "dur": 2.0}]}
    out = make_output("reel", quality="draft", scale=0.25)
    film = Film(spec, out.width, out.height)
    assert film.scenes[2]._appear() == pytest.approx([0.2, 0.5])
    comp = Compositor(film, out)
    for t in (1.0, 3.0, 5.5, 7.5, 9.5):
        assert comp.frame(int(t * out.fps))[..., :3].std() > 3.0, t


def test_spec_copy_is_safe():
    src = open(os.path.join(EXAMPLES, "explainer", "spec.json")).read().lower()
    for w in ("mg", "dose", "dosage", "inject", "cure", "treat", "prevents", "reverses"):
        assert not re.search(rf"\b{w}\b", src), w
