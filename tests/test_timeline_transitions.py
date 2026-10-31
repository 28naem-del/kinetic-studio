"""Beat units, grid snapping, transition windows/visibility, flashes, match-cut hand-off, camera, grounds."""
import numpy as np
import pytest
import skia

from kstudio import draw as D
from kstudio import transitions as TR
from kstudio.film import TRANSITIONS, Film
from kstudio.modes import THEMES
from kstudio.scenes.base import GROUND_STYLES, Ctx, Scene, draw_ground, register
from kstudio.timeline import Timeline, is_beat_expr

W, H = 216, 384
RED, BLUE = 0xFFFF0000, 0xFF0000FF


@register("_tt_red")
class _Red(Scene):
    """Big red subject in the middle; publishes it as 'logo'."""
    def draw(self, ctx):
        r = skia.Rect.MakeXYWH(ctx.W * 0.2, ctx.H * 0.3, ctx.W * 0.6, ctx.H * 0.4)
        ctx.c.drawRect(r, D.paint(RED))
        ctx.publish("logo", r)


SEEN = []


@register("_tt_blue")
class _Blue(Scene):
    """Big blue subject in the middle; receives 'logo' via ctx.handoff."""
    def draw(self, ctx):
        r = ctx.handoff("logo", (ctx.W * 0.3, ctx.H * 0.35, ctx.W * 0.4, ctx.H * 0.3), dur="1b")
        SEEN.append((round(ctx.lt, 4), r.left(), r.top(), r.width(), r.height()))
        ctx.c.drawRect(r, D.paint(BLUE))
        ctx.publish("logo", r)


def _film(tr="cut", bpm=120, **kw):
    spec = {"mode": "kinetic", "music": {"bpm": bpm},
            "scenes": [{"type": "_tt_red", "dur": "4b", "camera": False},
                       {"type": "_tt_blue", "dur": "4b", "transition": tr, "camera": False}], **kw}
    return Film(spec, W, H)


def _frame(film, t):
    s = skia.Surface(W, H)
    film.draw(s.getCanvas(), t)
    a = s.makeImageSnapshot().toarray(colorType=skia.ColorType.kRGBA_8888_ColorType)[..., :3].astype(int)
    return a[..., 0], a[..., 1], a[..., 2]


def _coverage(film, t):
    r, g, b = _frame(film, t)
    red = ((r - np.maximum(g, b)) > 80).mean()
    blue = ((b - np.maximum(r, g)) > 80).mean()
    return red, blue


# ---------------------------------------------------------------- beat units
def test_beat_expressions_and_fractions():
    tl = Timeline(120.0)
    assert tl.resolve_dur("1/2b") == pytest.approx(0.25)
    assert tl.resolve_dur("3/4b") == pytest.approx(0.375)
    assert tl.resolve_dur("1bar") == pytest.approx(2.0)
    assert tl.resolve_dur("2beats") == pytest.approx(1.0)
    assert tl.resolve_dur(0.4) == pytest.approx(0.4)
    assert tl.resolve_times(["1b", "1/2b", 0.3]) == pytest.approx([0.5, 0.25, 0.3])
    assert tl.resolve("drop-2b") if tl.named("drop") else True
    assert is_beat_expr("2b") and is_beat_expr("1/2b") and is_beat_expr("bar3") and is_beat_expr("1bar")
    assert not is_beat_expr("0.5") and not is_beat_expr(0.5) and not is_beat_expr("250ms")


def test_snapping_helpers():
    tl = Timeline(120.0)
    assert tl.snap(0.3) == pytest.approx(0.5)
    assert tl.snap(0.2) == pytest.approx(0.0)
    assert tl.snap(0.3, div=2) == pytest.approx(0.25)
    assert tl.snap(0.1, mode="ceil") == pytest.approx(0.5)
    assert tl.snap_bar(2.9) == pytest.approx(2.0)
    assert tl.grid_error(0.52) == pytest.approx(0.02)
    assert tl.on_grid(1.0005) and not tl.on_grid(1.01)
    assert tl.is_downbeat(4.0) and not tl.is_downbeat(4.5)
    assert tl.bar_of(4.1) == 3
    assert tl.beats_of(1.5, 0.0) == pytest.approx(3.0)
    assert tl.snap_dur(1.1, 0.0) == pytest.approx(1.0)
    assert tl.snap_dur(0.05, 0.0, min_units=1.0) == pytest.approx(0.5)


def test_scene_time_params_resolve_to_seconds():
    spec = {"mode": "kinetic", "music": {"bpm": 120},
            "markers": [{"kind": "cue", "at": "2b", "dur": "1b", "name": "ov"}],
            "scenes": [{"type": "_tt_red", "dur": "8b", "stagger": "1/2b", "per": "1b", "times": ["1b", "2b", 0.3],
                        "cues": [{"at": "1b", "dur": "1/2b"}]},
                       {"type": "karaoke", "dur": "4b", "text": "a b c", "per": "2b"}]}
    film = Film(spec, W, H)
    p = film.scenes[0].p
    assert p["stagger"] == pytest.approx(0.25) and p["per"] == pytest.approx(0.5)
    assert p["times"] == pytest.approx([0.5, 1.0, 0.3])
    assert p["cues"][0]["at"] == pytest.approx(0.5) and p["cues"][0]["dur"] == pytest.approx(0.25)
    assert film.scenes[0].p_raw["stagger"] == "1/2b"
    assert film.scenes[1].p["per"] == pytest.approx(2.0)          # karaoke per is a beat count
    ov = film.tl.named("ov")
    assert ov.t == pytest.approx(1.0) and ov.dur == pytest.approx(0.5)
    ctx = film._ctx(skia.Surface(8, 8).getCanvas(), 0.0)
    assert ctx.beat_len == pytest.approx(0.5) and ctx.beats(2) == pytest.approx(1.0)


def test_auto_snap_and_lint():
    spec = {"mode": "kinetic", "music": {"bpm": 120},
            "scenes": [{"type": "_tt_red", "dur": 1.1, "stagger": 0.07},
                       {"type": "_tt_blue", "dur": 1.3, "transition": "whip"}]}
    loose = Film(spec, W, H)
    warns = [w for w in loose.lint() if w["level"] == "warn"]
    assert any("whole number of beats" in w["msg"] for w in warns)
    assert any("off the beat" in w["msg"] for w in warns)
    snapped = Film({**spec, "snap": True}, W, H)
    assert [s.dur for s in snapped.scenes] == pytest.approx([1.0, 1.5])
    assert snapped.scenes[0].p["stagger"] == pytest.approx(0.125)   # 16th grid, never 0
    assert not [w for w in snapped.lint() if w["level"] == "warn"]
    for b in snapped.boundaries:
        assert abs(snapped.tl.grid_error(b.t)) < 1e-3


# ---------------------------------------------------------------- transition timing
@pytest.mark.parametrize("kind", sorted(TRANSITIONS))
def test_every_cut_and_window_edge_on_grid(kind):
    film = _film(kind, bpm=128)
    b = film.boundaries[0]
    tl = film.tl
    assert abs(tl.grid_error(b.t)) < 1e-3                      # cut on a beat
    if TRANSITIONS[kind].get("max_frames"):
        # streak accents (slice) are frame-capped: <= min(1/4 beat, max_frames) centred on the cut, so
        # below 150 BPM their edges sit off the 32nd grid by design
        quarter = tl.beat_to_time(tl.time_to_beat(b.t) + 0.25) - b.t
        assert b.dur <= min(quarter, TRANSITIONS[kind]["max_frames"] / film.fps) + 1e-6
        assert b.t - b.t0 == pytest.approx(b.t1 - b.t)
    else:
        assert abs(tl.grid_error(b.t0, 8)) < 1e-3 and abs(tl.grid_error(b.t1, 8)) < 1e-3   # 32nd grid
    assert b.t0 <= b.t <= b.t1
    assert b.dur == pytest.approx(b.t1 - b.t0)


def test_transition_dur_in_beats_and_seconds_quantised():
    f = _film({"kind": "push", "dur": "1b"})
    assert f.boundaries[0].beats == pytest.approx(1.0) and f.boundaries[0].dur == pytest.approx(0.5)
    g = _film({"kind": "push", "dur": 0.33})
    assert g.boundaries[0].beats == pytest.approx(0.75)
    assert TRANSITIONS["whip"]["beats"] == 0.5 and "dur" in TRANSITIONS["whip"]   # legacy key kept


def test_no_mode_defaults_to_plain_fade():
    from kstudio.modes import MODES
    for name, cfg in MODES.items():
        assert cfg.get("transition", "cut") not in ("fade", "dissolve"), name


def test_parse_names():
    assert TR.parse("whip-left") == ("whip", {"dir": "left"})
    assert TR.parse("wipe-circle") == ("wipe", {"shape": "circle"})
    assert TR.parse("wipe-circle-up") == ("wipe", {"shape": "circle", "dir": "up"})
    assert TR.parse({"kind": "slice", "n": 4}, "1b") == ("slice", {"n": 4, "dur": "1b"})
    with pytest.raises(KeyError):
        TR.parse("spin")


@pytest.mark.parametrize("kind", ["push", "whip", "zoom", "wipe", "slice", "split", "squash"])
def test_no_empty_midframe(kind):
    film = _film(kind)
    b = film.boundaries[0]
    mid = b.t0 + 0.5 * (b.t1 - b.t0)
    red, blue = _coverage(film, mid)
    assert red + blue > 0.05, (kind, red, blue)
    if kind in ("push", "whip", "wipe", "slice", "split"):
        assert red > 0.01 and blue > 0.01, (kind, red, blue)  # both scenes on screen at the midpoint
    # incoming subject visible by p <= 0.5
    seen = max(_coverage(film, b.t0 + f * (b.t1 - b.t0))[1] for f in (0.3, 0.4, 0.5))
    assert seen > 0.01, (kind, seen)


def test_cross_exit_enter_overlap_at_most_two_frames():
    film = _film("cross")
    b = film.boundaries[0]
    fps = film.fps
    both = 0
    for i in range(int(b.t0 * fps) - 1, int(b.t1 * fps) + 2):
        red, blue = _coverage(film, i / fps)
        both += red > 0.002 and blue > 0.002
    assert both <= 2
    assert sum(_coverage(film, b.t)) > 0.002            # cut frame is not empty


def test_flash_is_two_tinted_frames():
    film = _film("flash")
    cut, fps = film.boundaries[0].t, film.fps
    al = [TR.flash_alpha(cut, i / fps, fps) for i in range(int(cut * fps) - 5, int(cut * fps) + 6)]
    lit = [a for a in al if a > 0]
    assert len(lit) == 2 and all(0.6 <= a <= 0.8 for a in lit)
    def corner(t):
        r, g, b = _frame(film, t)
        return np.array([r[:40, :40].mean(), g[:40, :40].mean(), b[:40, :40].mean()])
    lit_px, base_px = corner(cut), corner(cut + 6 / fps)
    acc = np.array(film.pal.rgb(film.theme.get("flash", "accent")), float)
    assert lit_px.min() < 235 and not np.allclose(lit_px, film.pal.rgb("paper"), atol=20)  # never white/paper
    assert np.dot(lit_px - base_px, acc - base_px) > 0       # moved toward the accent
    assert np.linalg.norm(lit_px - acc) < np.linalg.norm(base_px - acc)
    assert sum(TR.flash_alpha(cut, cut + k / fps, fps) > 0 for k in range(3, 8)) == 0


# ---------------------------------------------------------------- match cut
def test_match_cut_handoff_registry():
    SEEN.clear()
    film = _film("match")
    b = film.boundaries[0]
    src = film.handoff_source(b.b, "logo")
    assert src is not None
    r = src["rect"]
    assert r.left() == pytest.approx(W * 0.2, abs=0.5) and r.width() == pytest.approx(W * 0.6, abs=0.5)
    _frame(film, b.b.start + 1e-4)
    lt, x, y, w, h = SEEN[-1]
    assert x == pytest.approx(W * 0.2, abs=1.0) and w == pytest.approx(W * 0.6, abs=1.0)  # starts on the old rect
    _frame(film, b.b.start + 0.6)
    lt, x, y, w, h = SEEN[-1]
    assert x == pytest.approx(W * 0.3, abs=0.5) and w == pytest.approx(W * 0.4, abs=0.5)  # landed after 1 beat
    m = film.match_matrix(b, 0.0)
    assert m is not None
    whip = _film("whip")
    assert whip.handoff_source(whip.scenes[1], "logo") is None          # only match-like cuts hand off
    opt_in = _film({"kind": "whip", "handoff": True})
    assert opt_in.handoff_source(opt_in.scenes[1], "logo") is not None


# ---------------------------------------------------------------- camera
def test_camera_continuity_and_parallax():
    spec = {"mode": "kinetic", "music": {"bpm": 120},
            "scenes": [{"type": "_tt_red", "dur": "4b", "camera": {"push": 0.08}},
                       {"type": "_tt_blue", "dur": "4b", "camera": {"push": 0.08, "carry": "full"}}]}
    film = Film(spec, W, H)
    a, b = film.scenes
    eps = 1e-3

    def state(sc, t):
        ctx = film._ctx(skia.Surface(8, 8).getCanvas(), t)
        ctx.lt, ctx.dur, ctx.start, ctx.scene = t - sc.start, sc.dur, sc.start, sc
        return sc.camera_state(ctx)

    s0, s1 = state(a, b.start - eps), state(b, b.start + eps)
    # full carry: no jump in the dolly (the beat bump is a deliberate accent, so compare without it)
    assert s0.scale_at(1.0, False) - s0.bump == pytest.approx(s1.scale_at(1.0, False) - s1.bump, abs=5e-3)
    assert s0.scale_at(3.0, True) - s0.bump == pytest.approx(s1.scale_at(3.0, True) - s1.bump, abs=5e-3)
    assert s1.scale_at(1.0, False) - s1.bump > 1.06                     # the second shot keeps pushing in
    st = state(a, 1.5)
    near, far = st.relative(0.5, False), st.relative(3.0, False)
    assert not near.isIdentity() or not far.isIdentity()


# ---------------------------------------------------------------- grounds
@pytest.mark.parametrize("style", [s for s in GROUND_STYLES if s not in ("solid", "gradient")])
def test_ground_patterns_read(style):
    th = THEMES["ink"]
    pal = D.Palette(th)
    s = skia.Surface(540, 960)
    ctx = Ctx(c=s.getCanvas(), W=540, H=960, pal=pal, theme=th, tl=Timeline(120.0), t=2.0, lt=2.0, dur=4.0)
    draw_ground(ctx, "bg", style)
    a = s.makeImageSnapshot().toarray()[..., :3].astype(float)
    luma = (0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]) / 255
    base = np.dot(np.array(pal.rgb("bg"), float) / 255, [0.2126, 0.7152, 0.0722])
    assert np.abs(luma - base).max() >= 0.06, style
