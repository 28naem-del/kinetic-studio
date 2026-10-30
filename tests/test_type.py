"""Type and layout system: variable axes, kerning, wrapping, line boxes, grid and type scale."""
import numpy as np
import pytest
import skia

from kstudio import draw as D
from kstudio import fonts as F
from kstudio import layout as L


def ink(font: skia.Font, s: str, w: int = 900, h: int = 220) -> np.ndarray:
    """Rasterise s in black on white; returns a bool mask of dark pixels."""
    surf = skia.Surface(w, h)
    c = surf.getCanvas()
    c.clear(skia.ColorWHITE)
    D.draw_text(c, s, 20, h * 0.75, font, D.argb((0, 0, 0)), align="left")
    a = surf.makeImageSnapshot().toarray()[..., :3].mean(-1)
    return a < 128


# ---------------------------------------------------------------- S1: variable axes take effect

def test_archivo_width_axis_changes_advance():
    narrow = F.font("Archivo", 100, 700, 62).measureText("MOTION")
    wide = F.font("Archivo", 100, 700, 125).measureText("MOTION")
    assert (wide - narrow) / narrow >= 0.15, (narrow, wide)


def test_archivo_weight_axis_changes_advance():
    light = F.font("Archivo", 100, 300, 100).measureText("MOTION")
    black = F.font("Archivo", 100, 900, 100).measureText("MOTION")
    assert abs(black - light) > 1.0, (light, black)


@pytest.mark.parametrize("family", ["SpaceGrotesk", "Fraunces", "Archivo", "Inter"])
def test_weight_axis_renders_bolder(family):
    lo = ink(F.font(family, 100, 300), "MOTION").sum()
    hi = ink(F.font(family, 100, 700), "MOTION").sum()
    assert hi > lo * 1.25, (family, int(lo), int(hi))


def test_axes_clamped_and_listed():
    assert F.axes("Archivo")["wdth"][0] == 62 and F.axes("Archivo")["wdth"][2] == 125
    assert F.clamp_axis("SpaceGrotesk", "wght", 900) == 700
    assert F.opsz_for("Fraunces", 224) == 144 and F.opsz_for("Archivo", 224) is None


# ---------------------------------------------------------------- kerning survives tracking

def test_kerning_applied_and_matches_shaper():
    f = F.font("Archivo", 100, 700)
    plain = sum(f.getWidths(f.textToGlyphs("AVATAR")))
    kerned = D.text_w(f, "AVATAR")
    assert kerned < plain - 5
    shaped = F._shaped_width(f.getTypeface(), "AVATAR") * f.getSize() / F._REF
    assert abs(kerned - shaped) < 0.5
    assert D.text_w(f, "AVATAR", kern=False) == pytest.approx(plain, abs=0.01)


def test_kerning_kept_with_tracking():
    f = F.font("Inter", 80, 600)
    s = "AVATAR Type"
    base = D.text_w(f, s)
    tr = 0.08
    assert D.text_w(f, s, tr) == pytest.approx(base + tr * 80 * (len(s) - 1), abs=0.01)
    gl = D.glyph_layout(f, s, tr)
    # A->V gap is the A advance + tracking + the (negative) AV kern
    av = gl[1][1] - gl[0][1]
    assert av < gl[0][2] + tr * 80 - 1.0
    # the tracked, kerned line is narrower on screen than the tracked, unkerned one
    w_k = np.ptp(ink_tracked(f, "AVATAR", tr, True))
    w_n = np.ptp(ink_tracked(f, "AVATAR", tr, False))
    assert w_k < w_n - 5


def ink_tracked(f, s, tr, kern):
    surf = skia.Surface(1000, 200)
    c = surf.getCanvas()
    c.clear(skia.ColorWHITE)
    D.draw_text(c, s, 20, 150, f, D.argb((0, 0, 0)), align="left", tracking=tr, kern=kern)
    return (surf.makeImageSnapshot().toarray()[..., :3].mean(-1) < 128).any(0).nonzero()[0]


def test_text_path_and_rects_follow_run():
    f = F.font("Archivo", 100, 800)
    p = D.text_path("AVA", 0, 100, f, tracking=0.1)
    rects = D.glyph_rects(f, "AVA", 0, 100, tracking=0.1)
    assert len(rects) == 3
    assert p.getBounds().right() == pytest.approx(rects[-1][1].right(), abs=1.5)


# ---------------------------------------------------------------- wrapping

TEXT = ("Every load that misses the cache waits for main memory, which is about a hundred times "
        "slower than the core that asked for it")


def test_wrap_respects_measure_and_line_count():
    f = F.font("Inter", 53, 500)
    for mw in (420, 600, 812):
        g = D.wrap(f, TEXT, mw, style="greedy")
        p = D.wrap(f, TEXT, mw)
        b = D.wrap(f, TEXT, mw, style="balance")
        assert len(p) == len(g) == len(b)
        assert all(D.text_w(f, ln) <= mw + 0.01 for ln in p + b)
        assert " ".join(p).split() == TEXT.split() == " ".join(b).split()


def test_wrap_balance_evens_lines():
    f = F.font("Fraunces", 168, 700)
    s = "How does a CPU cache work today"
    g = D.wrap(f, s, 888, style="greedy")
    b = D.wrap(f, s, 888, style="balance")
    assert len(g) == len(b) >= 2
    spread = lambda ls: max(D.text_w(f, x) for x in ls) - min(D.text_w(f, x) for x in ls)  # noqa: E731
    assert spread(b) <= spread(g)


def test_wrap_avoids_widow():
    f = F.font("Inter", 53, 500)
    found = 0
    for mw in range(300, 900, 7):
        g = D.wrap(f, TEXT, mw, style="greedy")
        if len(g) > 1 and len(g[-1].split()) == 1:
            found += 1
            p = D.wrap(f, TEXT, mw)
            assert len(p[-1].split()) >= 2, (mw, p)
    assert found, "no greedy widow case found to test"


def test_wrap_keeps_explicit_breaks():
    f = F.font("Inter", 53, 500)
    assert D.wrap(f, "How does a\nCPU cache work?", 2000) == ["How does a", "CPU cache work?"]


def test_line_boxes():
    f = F.font("Inter", 53, 500)
    blk = D.layout_text(f, TEXT, 96, 560, 812, leading=1.36)
    assert blk.top == pytest.approx(560)
    assert len(blk.lines) >= 3
    pitch = [b.baseline - a.baseline for a, b in zip(blk.lines, blk.lines[1:])]
    assert all(p == pytest.approx(1.36 * 53) for p in pitch)
    assert blk.h == pytest.approx(D.block_height(f, len(blk.lines), 1.36))
    c = D.set_lines(f, ["ab", "abcdef"], 540, 0, align="center")
    assert c.lines[0].x + c.lines[0].w / 2 == pytest.approx(540)


# ---------------------------------------------------------------- grid + type scale

def test_reel_grid():
    g = L.Grid(1080, 1920)
    assert g.profile == "portrait" and g.margin == 96 and g.cols == 12 and g.gutter == 24
    assert g.col_w == pytest.approx(52) and g.span(0, 12) == pytest.approx((96, 888))
    assert g.col(11) + g.col_w == pytest.approx(984) and g.col(-1) == g.col(11)
    assert (g.safe_top, g.safe_bottom, g.anchor_y) == pytest.approx((220, 1540, 560))
    assert g.bl(1) == 8 and g.snap(563) == 560
    assert g.place(400) == pytest.approx(560) and g.place(1200) == pytest.approx(340)
    assert g.place(100, "bottom") == pytest.approx(1440)
    assert g.fills(220, 1540) and not g.fills(560, 1100)
    assert g.coverage(0, 960) == pytest.approx(0.5)


def test_grid_scales_and_presets():
    g2 = L.Grid(2160, 3840)
    assert g2.margin == 192 and g2.anchor_y == 1120 and g2.col_w == pytest.approx(104)
    land = L.Grid(1920, 1080)
    assert land.profile == "landscape" and land.margin == 120 and land.col_w == pytest.approx(118)
    assert land.measure < land.head_w < land.content_w
    sq = L.Grid(1080, 1080)
    assert sq.profile == "square" and sq.safe_top < sq.anchor_y < sq.safe_bottom
    for g in (L.Grid(1080, 1920), land, sq, L.Grid(540, 960)):
        assert g.span(0, g.cols)[1] == pytest.approx(g.content_w)


def test_type_scale():
    assert L.TYPE.sizes == [40, 53, 71, 95, 126, 168, 224]
    T = L.TYPE
    for name in ("display", "headline", "title", "body", "kicker", "caption"):
        r = T[name]
        assert r.size > 0 and 0.8 < r.leading < 1.5
    assert T["display"].size == 224 and T["headline"].size == 168 and T["title"].size == 126
    assert T["body"].size == 53 and T["caption"].size == 40
    assert T["kicker"].size == 32 and T["kicker"].caps and T["kicker"].track == pytest.approx(0.12)
    assert T["display"].track < T["headline"].track < 0 < T["kicker"].track
    assert T["display"].leading < T["title"].leading < T["body"].leading
    assert T.role(130).size == 130 and T.role(130).name == "title"


def test_optical_tracking_curve():
    assert F.track(12) > F.track(24) > F.track(48)
    assert F.track(48) == pytest.approx(-0.0223, abs=1e-3)
    assert F.track(224) == pytest.approx(-0.045, abs=1e-3)
    assert F.track(224, family="Fraunces") > F.track(224)
    assert F.track(32, caps=True) == pytest.approx(0.12) and F.track(168, caps=True) == pytest.approx(0.02)


# ---------------------------------------------------------------- common scenes on every aspect

@pytest.mark.parametrize("preset", ["reel", "landscape", "square"])
def test_common_scenes_draw_on_every_aspect(preset):
    from kstudio.compositor import Compositor
    from kstudio.film import Film
    from kstudio.presets import make_output
    spec = {"mode": "explainer", "handle": "@x", "progress": True,
            "captions": [{"at": 0.5, "dur": 4.0, "text": "Say one idea and show it big."}],
            "sources": [{"at": 0.5, "dur": 4.0, "tag": "Source", "text": "A book, 2nd ed."}],
            "scenes": [{"type": "title", "kicker": "K", "title": "How does a CPU cache work?", "subtitle": "Sub", "dur": 1.0},
                       {"type": "text", "heading": "Why", "body": TEXT, "highlight": ["cache"], "dur": 1.0},
                       {"type": "logo", "text": "flowly", "tagline": "t", "url": "u.app", "dur": 1.0},
                       {"type": "end_card", "small": "s", "handle": "@x", "dur": 1.0},
                       {"type": "title", "title": "Sized", "size": 128, "weight": 600, "dur": 1.0}]}
    out = make_output(preset, quality="draft", scale=0.25)
    comp = Compositor(Film(spec, out.width, out.height), out)
    for i in (20, 50, 80, 110, 140):
        img = comp.frame(i)
        assert img[..., :3].std() > 3.0, (preset, i)


def _fill(p: skia.Paint) -> int:
    surf = skia.Surface(4, 4)
    c = surf.getCanvas()
    c.clear(0xFF000000)
    c.drawRect(skia.Rect(0, 0, 4, 4), p)
    return int(surf.makeImageSnapshot().toarray()[1, 1, 0])


def test_paint_shader_full_alpha_unless_given():
    white = lambda: D.linear_grad(0, 0, 4, 0, [0xFFFFFFFF, 0xFFFFFFFF])  # noqa: E731
    assert _fill(D.paint(0, shader=white())) == 255            # colour 0 + shader is opaque
    assert abs(_fill(D.paint(0x80000000, shader=white())) - 128) <= 1  # explicit alpha byte
    assert abs(_fill(D.paint(0, shader=white(), alpha=0.25)) - 64) <= 1  # explicit alpha kwarg
    assert _fill(D.paint(0)) == 0                               # no shader: colour 0 stays clear


def test_lower_third_stacks_above_image_caption():
    from kstudio.film import Film
    from kstudio.scenes.common import bottom_reserved
    spec = {"mode": "explainer", "_dir": ".",
            "sources": [{"at": 0.0, "dur": 3.0, "text": "Hennessy & Patterson, Computer Architecture"}],
            "scenes": [{"type": "title", "title": "Intro", "dur": 2.0},
                       {"type": "image", "src": "none.jpg", "caption": "Closer memory is faster memory", "dur": 3.0}]}
    for W, H in ((1080, 1920), (1920, 1080), (1080, 1080)):
        film = Film(spec, W, H)
        img = film.scenes[1]
        surf = skia.Surface(W, H)
        ctx = film._ctx(surf.getCanvas(), img.start + 1.0)
        g = L.grid(ctx)
        top = img.caption_block(ctx)[3]
        lt_bottom = g.safe_bottom - bottom_reserved(ctx)
        assert lt_bottom <= top - g.bl(3)                       # stacked: lower third ends above the caption
        assert bottom_reserved(film._ctx(surf.getCanvas(), 0.5)) == 0.0  # no caption on screen: on the safe line
