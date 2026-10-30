"""Engine fixes: 2-frame accent flash, per-frame seeded grain, continuous smears, denoised motion
audit, delivery true peak / VBV, end-card contrast and the 4 % overshoot cap."""
from __future__ import annotations

import inspect
import math
import re
from pathlib import Path

import numpy as np
import pytest
import skia

from kstudio import audit as A
from kstudio import ease as E
from kstudio import render as RD
from kstudio import transitions as TR
from kstudio.audio.mix import mix_film
from kstudio.compositor import Compositor, spec_seed
from kstudio.film import Film
from kstudio.modes import MODES, THEMES
from kstudio.presets import make_output
from kstudio.scenes.common import contrast_ratio, cta_colors, ground_samples, readable
from kstudio.spec import load_spec
from kstudio import draw as D

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- flash
@pytest.mark.parametrize("cut", [7.5, 7.0, 7.0123, 3.3333333, 11.25])
def test_flash_lights_exactly_two_frames(cut):
    fps = 30.0
    lit = [i for i in range(int(cut * fps) - 5, int(cut * fps) + 6) if TR.flash_frame_alpha(cut, i, fps) > 0]
    assert len(lit) == 2 and lit[1] == lit[0] + 1
    assert lit[0] == TR.flash_first_frame(cut, fps) and lit[0] / fps >= cut - 1e-6
    for i in lit:
        assert 0.6 <= TR.flash_frame_alpha(cut, i, fps) <= 0.8


def test_kinetic_film_flashes_are_two_accent_frames():
    film = Film(load_spec(str(ROOT / "examples" / "kinetic")), 270, 480)
    cuts = [b.t for b in film.boundaries if b.kind == "flash"]
    assert cuts
    accent = film.pal.rgb("accent")
    for cut in cuts:
        i0 = TR.flash_first_frame(cut, film.fps)
        lit = [i for i in range(i0 - 4, i0 + 5) if film.flash_at(i)[1] > 0.002]
        assert lit == [i0, i0 + 1]
        for i in lit:
            rgb, a = film.flash_at(i)
            assert rgb == accent and 0.6 <= a <= 0.8  # an accent tint, never a white frame


# ---------------------------------------------------------------- grain
class _Still:
    def __init__(self, spec):
        self.duration, self.cfg, self.spec, self.theme = 2.0, {}, spec, {}

    def draw(self, c, t):
        c.drawRect(skia.Rect.MakeXYWH(0, 0, 192, 108), skia.Paint(Color=skia.Color(70, 70, 70)))

    def shutter_mult(self, t):
        return 1.0


def _out():
    o = make_output("landscape", fps=30, quality="draft")
    o.width, o.height = 192, 108
    return o


_GRAIN = {"bloom": 0.0, "chroma": 0.0, "vignette": 0.0, "grain": 0.06}


def test_grain_is_deterministic_per_frame_and_spec():
    spec = {"theme": "ink", "scenes": [{"type": "title"}]}
    a = Compositor(_Still(spec), _out(), post=_GRAIN, grade="none")
    b = Compositor(_Still(dict(spec)), _out(), post=_GRAIN, grade="none")
    assert a.seed == b.seed == spec_seed(_Still(spec))
    for i in (0, 5, 17):
        assert np.array_equal(a.frame(i), b.frame(i))
    # re-seeded every frame: a static picture still gets fresh grain
    assert not np.array_equal(a.frame(3), a.frame(4))
    # and a different spec gets a different seed
    other = Compositor(_Still({"theme": "ink", "scenes": [{"type": "text"}]}), _out(), post=_GRAIN, grade="none")
    assert other.seed != a.seed


# ---------------------------------------------------------------- smear
def test_whip_and_zoom_frames_leave_nothing_unresolved():
    spec = load_spec(str(ROOT / "examples" / "kinetic"))
    out = make_output("reel", 30, "standard", scale=0.25)
    spec = dict(spec); spec["_fps"] = out.fps
    film = Film(spec, out.width, out.height)
    comp = Compositor(film, out)
    for i in (56, 112, 169):  # mid whip-left, zoom, whip-up
        comp.frame(i)
    assert comp.stats["unresolved"] == 0
    assert comp.stats["max_samples"] >= 8


# ---------------------------------------------------------------- audit motion
def _sq(frames):
    sq = [float(np.mean((frames[k + 1] - frames[k]) ** 2)) for k in range(len(frames) - 1)]
    sq2 = [float(np.mean((frames[k + 2] - frames[k]) ** 2)) for k in range(len(frames) - 2)]
    return sq, sq2


def test_denoised_motion_ignores_grain_and_keeps_moves():
    rng = np.random.default_rng(1)
    h, w, n = 60, 34, 24
    base = np.tile(np.linspace(0, 255, w, dtype=np.float32), (h, 1))
    grainy = [base + rng.normal(0, 6.0, base.shape).astype(np.float32) for _ in range(n)]
    sq, sq2 = _sq(grainy)
    raw = np.sqrt(sq)
    m = A.denoised_motion(sq, sq2)
    assert raw.min() > A.MOTION_GATE          # the raw metric calls static grain "motion"
    assert (m < A.MOTION_GATE).mean() > 0.9   # the denoised one does not
    moving = [np.roll(base, 2 * k, axis=1) + rng.normal(0, 6.0, base.shape).astype(np.float32) for k in range(n)]
    sq, sq2 = _sq(moving)
    assert (A.denoised_motion(sq, sq2) >= A.MOTION_GATE).mean() > 0.9


# ---------------------------------------------------------------- delivery
def test_true_peak_ceiling_defaults():
    assert inspect.signature(mix_film).parameters["ceiling_dbtp"].default <= -1.3
    assert RD.MUX_CEILING_DBTP <= -1.3
    src = inspect.getsource(A.audit)
    assert re.search(r'"max_true_peak":\s*-1\.3', src)


def test_encoder_vbv_flags_stay_under_maxrate():
    out = make_output("reel", 30, "standard")
    cmd = RD.encoder_cmd(out, "x.ts")
    rate = int(cmd[cmd.index("-maxrate") + 1].rstrip("k"))
    buf = int(cmd[cmd.index("-bufsize") + 1].rstrip("k"))
    assert rate <= out.maxrate_kbps
    assert buf <= out.maxrate_kbps  # <= 1 s of buffer: each 2 s chunk can't bank a long burst
    assert "vbv-init=" in cmd[cmd.index("-x264-params") + 1]


# ---------------------------------------------------------------- end card
@pytest.mark.parametrize("theme", sorted(THEMES))
def test_end_card_handle_and_cta_contrast(theme):
    pal = D.Palette(THEMES[theme])
    grounds = ground_samples(pal, "bg", THEMES[theme].get("ground_style", "solid"))
    rgb, a = readable(pal, grounds, 4.5, 0.92)
    assert min(contrast_ratio(D.mix_rgb(g, rgb, a), g) for g in grounds) >= 4.5
    for mode in MODES.values():
        fill, label, _ = cta_colors(pal, "bg", float(mode["post"].get("bloom", 0.0)))
        assert contrast_ratio(label, fill) >= 4.5


def test_end_card_owns_the_handle():
    film = Film({"mode": "explainer", "handle": "@x", "scenes": [{"type": "end_card", "dur": "4b"}]}, 270, 480)
    assert getattr(film.scenes[0], "owns_handle", False)


# ---------------------------------------------------------------- overshoot cap
def _peak(f, n=400):
    return max(f(i / n) for i in range(n + 1))


def test_registry_roles_overshoot_at_most_4_percent():
    for role in E.ROLES:
        f = E.resolve(role)
        assert _peak(f) <= 1.04 + 1e-6, role


def test_scene_defaults_do_not_use_out_back():
    for name in ("kinetic", "explainer", "promo", "common", "base"):
        src = (ROOT / "kstudio" / "scenes" / f"{name}.py").read_text()
        code = re.sub(r'"""[\s\S]*?"""', "", src)
        assert "out_back" not in code, name


def test_promo_beat_springs_under_cap():
    from kstudio.scenes import promo
    s = E.Spring.settling(0.4, promo.BEAT_BOUNCE)
    assert _peak(lambda u: s.value(u * 2.0)) <= 1.04
