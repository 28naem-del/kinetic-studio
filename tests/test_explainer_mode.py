"""Explainer mode, round 2: the page-turn push, reel-sized captions, the end card inside a beat,
the cover figure plate and beat-only timings in the example spec."""
import json
import os
import re

import numpy as np
import pytest
from conftest import EXAMPLES

from kstudio import layout as L
from kstudio.compositor import Compositor
from kstudio.film import Film
from kstudio.presets import make_output
from kstudio.scenes import explainer as X
from kstudio.spec import load_spec

W, H = 1080, 1920
TIMING_KEYS = {"dur", "at", "first", "stagger", "step", "delay", "offset", "hold", "start", "end"}


@pytest.fixture(scope="module")
def film():
    return Film(load_spec(os.path.join(EXAMPLES, "explainer")), W, H)


def _spy_rise(monkeypatch):
    seen = []
    orig = X._rise

    def spy(ctx, text, x, baseline, f, color, u, *a, **k):
        seen.append((text, round(f.getSize(), 1), u))
        return orig(ctx, text, x, baseline, f, color, u, *a, **k)

    monkeypatch.setattr(X, "_rise", spy)
    return seen


def _frame(film, f, scale=1.0):
    """Frame f drawn through the compositor; the film is laid out at the output size."""
    out = make_output("reel", quality="draft", scale=scale)
    if scale != 1.0:
        film = Film(load_spec(os.path.join(EXAMPLES, "explainer")), out.width, out.height)
    return Compositor(film, out).frame(f)


def _push(film):
    return next(b for b in film.boundaries if b.kind == "push")


def test_push_is_a_horizontal_page_turn(film):
    b = _push(film)
    assert b.b.type == "bullets"
    assert b.direction[1] == 0 and b.direction[0] != 0                  # moves along x, not y
    assert film.tl.beats_of(b.dur, b.t - b.dur / 2) == pytest.approx(0.5, abs=1e-6)


def test_pushed_scene_arrives_composed(film, monkeypatch):
    """By mid-push (p <= 0.5) the incoming page already carries its heading and standfirst at rest."""
    b = _push(film)
    seen = _spy_rise(monkeypatch)
    f = int(np.ceil((b.t - b.dur / 2) * 30)) + 2                         # about a third into the push
    assert (f / 30 - (b.t - b.dur / 2)) / b.dur <= 0.5
    _frame(film, f)
    got = {s[0]: s[2] for s in seen}
    assert got.get("Locality") == pytest.approx(1.0)
    assert got.get(b.b.p["caption"]) == pytest.approx(1.0)
    assert X.carried(type("C", (), {"film": film})(), b.b)               # the scene knows it is carried
    assert not X.carried(type("C", (), {"film": film})(), film.scenes[2])  # a match cut is not


def test_explainer_text_is_reel_sized(film, monkeypatch):
    """Nothing the explainer scenes set is below the 53 px body step, apart from kicker/meta chrome."""
    seen = _spy_rise(monkeypatch)
    for f in (45, 120, 230, 330):
        _frame(film, f)
    sizes = {s[1] for s in seen}
    assert 53.0 in sizes
    chrome = {X.T["kicker"].px(L.grid(W, H)), X.T["meta"].px(L.grid(W, H))}
    small = {s for s in sizes if s < 53.0 - 0.5}
    assert small <= {round(px, 1) for px in chrome}, small
    assert X.SMALL.px(L.grid(W, H)) >= 53.0


def test_end_card_is_full_inside_one_beat(film):
    ec = film.scenes[-1]
    spb = film.tl.spb
    f1 = int(np.ceil((ec.start + spb) * 30))
    fe = int((ec.start + ec.dur) * 30) - 2

    def ink(img):
        g = img[..., :3].astype(float).mean(axis=2)
        bg = np.median(g)
        rows = (np.abs(g - bg) > 40)[int(0.1 * g.shape[0]):int(0.95 * g.shape[0])]
        return int(rows.sum())

    a, z = ink(_frame(film, f1, 0.25)), ink(_frame(film, fe, 0.25))
    assert z > 0 and a / z >= 0.85, (a, z)


def test_cover_plate_uses_the_lower_half(film, monkeypatch):
    got = []
    orig = X.Cover._plate

    def spy(self, ctx, g, bars, top, floor, settled):
        got.append(self.plate_rects(g, bars, top, floor))
        return orig(self, ctx, g, bars, top, floor, settled)

    monkeypatch.setattr(X.Cover, "_plate", spy)
    _frame(film, 50)
    assert got
    g = L.grid(W, H)
    rects = got[-1]
    assert len(rects) == len(film.scenes[0].p["bars"])
    assert min(r.top() for r in rects) >= H / 2
    assert max(r.bottom() for r in rects) <= g.safe_bottom + 0.5
    assert min(r.left() for r in rects) >= g.x0 - 0.5
    assert max(r.right() for r in rects) <= g.x1 + 0.5
    assert len({round(r.bottom(), 3) for r in rects}) == 1                # one floor


def _timings(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _timings(v, f"{path}.{k}") if isinstance(v, (dict, list)) else [(f"{path}.{k}", k, v)]
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _timings(v, f"{path}[{i}]")


def test_spec_timings_are_in_beats():
    with open(os.path.join(EXAMPLES, "explainer", "spec.json"), encoding="utf-8") as fh:
        raw = re.sub(r"(?m)^\s*//.*$", "", fh.read())
    spec = json.loads(raw)
    bad = []
    for path, key, v in _timings(spec):
        if key not in TIMING_KEYS:
            continue
        if isinstance(v, (int, float)) and v != 0:
            bad.append((path, v))
        elif isinstance(v, str) and re.fullmatch(r"[\d.]+\s*(s|ms)?", v.strip()) and float(re.sub(r"[^\d.]", "", v)):
            bad.append((path, v))
    assert not bad, bad
    assert spec["scenes"][-1]["step"] == "1/8b"
