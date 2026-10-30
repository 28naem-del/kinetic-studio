"""Transition grammar: beat-quantised windows between two scenes.

Every transition has a length in beats and an anchor (the fraction of the window that sits
before the cut). The cut is the incoming scene's start, so with beat-unit scene lengths the
cut, the window edges and the transition SFX all land on the grid.

Spec forms (on the incoming scene):
    "transition": "whip-left"                      kind-direction shorthand
    "transition": "wipe-circle"                    kind-shape shorthand (wipe)
    "transition": {"kind": "wipe", "shape": "type", "text": "GO", "dir": "up", "soft": 24,
                   "dur": "1b", "edge": "accent"}
    "transition_dur": "1/2b"                       beats (or seconds, quantised to 16ths)

Kinds (default beats): cut 0 | flash 0 (cut + 2-frame accent flash) | cross 1 (exit 0.5b
in_quart, enter 0.5b out_expo; "fade" is an alias) | dissolve 1 (plain alpha crossfade, opt-in)
| whip 1/2 | push 3/4 | zoom 3/4 | squash 1/2 | wipe 1 (rect, circle, diagonal, type) |
slice 1/4 (n bands) | split 3/4 (two halves open) | match 0 (element hand-off; "morph" beats).

Streak accents are hits, not passages: a slice window is capped at 1/4 beat or STREAK_FRAMES
frames, whichever is shorter (any "dur" is clamped), and the whip's speed lines are drawn only
inside the same span around the cut.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import skia

from . import draw as D
from . import ease as E
from . import fonts as F

if TYPE_CHECKING:  # pragma: no cover
    from .film import Boundary, Film

# name: beats (window length), anchor (fraction before the cut), shutter multiplier inside the
# window, sfx at the cut (peak-aligned), legacy "dur" in seconds at 120 BPM for old callers.
TRANSITIONS: dict[str, dict[str, Any]] = {
    "cut": {"beats": 0.0, "anchor": 0.5, "shutter": 1.0, "sfx": None},
    "flash": {"beats": 0.0, "anchor": 0.5, "shutter": 1.0, "sfx": "hit"},
    "cross": {"beats": 1.0, "anchor": 0.5, "shutter": 1.0, "sfx": None},
    "fade": {"beats": 1.0, "anchor": 0.5, "shutter": 1.0, "sfx": None, "alias": "cross"},
    "dissolve": {"beats": 1.0, "anchor": 0.5, "shutter": 1.0, "sfx": None},
    "whip": {"beats": 0.5, "anchor": 0.5, "shutter": 3.0, "sfx": "whoosh"},
    "push": {"beats": 0.75, "anchor": 0.5, "shutter": 1.25, "sfx": "swish"},
    "zoom": {"beats": 0.75, "anchor": 0.5, "shutter": 2.5, "sfx": "whoosh"},
    "squash": {"beats": 0.5, "anchor": 0.5, "shutter": 2.0, "sfx": "swish"},
    "wipe": {"beats": 1.0, "anchor": 0.5, "shutter": 1.5, "sfx": "swish"},
    "slice": {"beats": 0.25, "anchor": 0.5, "shutter": 2.0, "sfx": "swish", "max_beats": 0.25, "max_frames": 3},
    "split": {"beats": 0.75, "anchor": 0.5, "shutter": 2.0, "sfx": "swish"},
    "match": {"beats": 0.0, "anchor": 0.0, "shutter": 1.0, "sfx": None},
}
for _v in TRANSITIONS.values():
    _v["dur"] = _v["beats"] * 0.5
DIRS = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}
WIPE_SHAPES = ("rect", "circle", "diagonal", "type")
# kinds across which a published element is handed to the next scene (others: opt in with "handoff": true)
HANDOFF_KINDS = ("match", "cut", "flash", "cross", "fade", "dissolve")
FLASH_ALPHA = (0.74, 0.64)   # first / second frame: accent tint at 60-80 % over the frame
STREAK_FRAMES = 3            # longest streak accent (slice window, whip speed lines), in frames
STREAK_BEATS = 0.25          # ... and in beats; the shorter of the two applies


def _h(i: int, seed: int) -> float:
    """Deterministic hash in [0, 1)."""
    n = (i * 374761393 + seed * 668265263) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFFFF) / float(0x1000000)


def in_quart(u: float) -> float:
    return E.clamp(u) ** 4


def out_quint(u: float) -> float:
    return 1 - (1 - E.clamp(u)) ** 5


def lead(p: float, k: float = 0.75) -> float:
    """in_out_cubic on a time-warped clock: the incoming element leads (0.73 at p = 0.5)."""
    return E.in_out_cubic(E.clamp(p) ** k)


def parse(tr: Any, dur_override: Any = None) -> tuple[str, dict[str, Any]]:
    """Spec value -> (kind, params). Raises KeyError for an unknown kind."""
    if isinstance(tr, dict):
        params = dict(tr)
        kind = str(params.pop("kind", params.pop("type", "cut")))
    else:
        kind, _, rest = str(tr).partition("-")
        params = {}
        if rest:
            if kind == "wipe" and rest in WIPE_SHAPES:
                params["shape"] = rest
            elif kind == "wipe" and "-" in rest:
                a, _, b = rest.partition("-")
                params.update(shape=a if a in WIPE_SHAPES else "rect", dir=b if a in WIPE_SHAPES else a)
            else:
                params["dir"] = rest
    if kind not in TRANSITIONS:
        raise KeyError(kind)
    if dur_override is not None:
        params["dur"] = dur_override
    return kind, params


# ---------------------------------------------------------------------- drawing

def draw_boundary(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    fn = DRAW.get(TRANSITIONS[b.kind].get("alias", b.kind), _draw_cut)
    fn(film, c, b, p, t)


def _a(film: Film, c: skia.Canvas, b: Boundary, t: float, **kw: Any) -> None:
    film._draw_scene(c, b.a, t, lt_max=b.a.dur + (b.t1 - b.t), **kw)


def incoming_lt(b: Boundary, t: float) -> float:
    """Incoming scene clock inside a window: starts at the window start and catches up with
    film time at the window end (continuous, monotonic), so its subject is on screen before the cut."""
    t0, t1 = float(b.t0), float(b.t1)  # type: ignore[arg-type]
    if t1 - t0 <= 1e-6 or t >= t1:
        return max(t - b.b.start, 0.0)
    return max(t - t0, 0.0) * (t1 - b.b.start) / (t1 - t0)


def _b(film: Film, c: skia.Canvas, b: Boundary, t: float, **kw: Any) -> None:
    film._draw_scene(c, b.b, t, lt_min=0.0, lt_force=incoming_lt(b, t), **kw)


def _draw_cut(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    (_a if t < b.t else _b)(film, c, b, t)


def _draw_cross(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    """Exit then enter: no alpha crossfade of the two scenes' content, the ground cuts on the beat.
    The enter starts one frame before the cut so the cut frame is never empty (<= 2 frames of overlap)."""
    dx, dy = b.direction if b.params.get("dir") else (0, -1)
    H, W = film.H, film.W
    win = max(float(b.t1) - float(b.t0), 1e-6)  # type: ignore[arg-type]
    f = min(1.0 / (max(film.fps, 1) * win), 0.25)
    pc = E.clamp((float(b.t) - float(b.t0)) / win)  # type: ignore[arg-type]
    if p < pc:
        u = in_quart(E.clamp(p / max(pc, 1e-6)))
        _a(film, c, b, t, content_alpha=1 - u, content_offset=(dx * W * 0.04 * u, dy * H * 0.045 * u, 1 - 0.025 * u))
    pe = pc - f
    if p >= pe:
        v = E.out_expo(E.clamp((p - pe) / max(1 - pe, 1e-6)))
        _b(film, c, b, t, content_alpha=v, ground=p >= pc,
           content_offset=(-dx * W * 0.04 * (1 - v), -dy * H * 0.045 * (1 - v), 1 + 0.03 * (1 - v)))


def _draw_dissolve(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    _a(film, c, b, t)
    c.saveLayerAlpha(None, int(255 * E.in_out_quad(p)))
    _b(film, c, b, t)
    c.restore()


def streak_half(film: Film, cut: float) -> float:
    """Half the streak-accent span (s) around a cut: the whole span is at most STREAK_BEATS beats
    or STREAK_FRAMES frames, whichever is shorter."""
    tl = film.tl
    beat = tl.beat_to_time(tl.time_to_beat(cut) + STREAK_BEATS) - cut
    return 0.5 * min(beat, STREAK_FRAMES / max(float(film.fps), 1.0))


def _streaks(film: Film, c: skia.Canvas, b: Boundary, p: float, seam: float, horizontal: bool,
             t: float | None = None) -> None:
    """Directional speed streaks around the seam (deterministic per boundary), only within
    streak_half() of the cut, peaking on it."""
    W, H = film.W, film.H
    s = min(W, H) / 1080.0
    k = math.sin(math.pi * E.clamp(p))
    if t is not None:
        half = streak_half(film, float(b.t))
        k = math.cos(0.5 * math.pi * min(abs(t - float(b.t)) / max(half, 1e-6), 1.0))
    if k < 0.05:
        return
    cols = [film.pal("accent", 0.55 * k), film.pal("ink", 0.35 * k), film.pal("accent2", 0.4 * k)]
    n = 22
    for i in range(n):
        h1 = _h(i, int(b.t * 1000) + 17)
        h2 = _h(i, int(b.t * 1000) + 91)
        length = (0.25 + 0.6 * h2) * (W if horizontal else H) * k
        off = (h1 - 0.5) * 0.5 * (W if horizontal else H)
        th = (1.5 + 5 * h2) * s
        p_ = D.paint(cols[i % 3], stroke=th)
        if horizontal:
            y = H * (i + 0.5) / n + (h2 - 0.5) * H / n
            x = seam + off
            c.drawLine(x - length / 2, y, x + length / 2, y, p_)
        else:
            x = W * (i + 0.5) / n + (h2 - 0.5) * W / n
            y = seam + off
            c.drawLine(x, y - length / 2, x, y + length / 2, p_)


def _draw_whip(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    W, H = film.W, film.H
    dx, dy = b.direction
    ea = E.in_out_cubic(E.clamp(p) ** 1.33)     # outgoing lags
    eb = lead(p)                                  # incoming leads: on screen well before p = 0.5
    k = math.sin(math.pi * p)
    c.save()
    c.translate(W / 2, H / 2)
    c.rotate(3.0 * k * (dx or dy or 1))
    c.scale(1 + 0.04 * k, 1 + 0.04 * k)
    c.translate(-W / 2, -H / 2)
    c.save(); c.translate(dx * W * ea, dy * H * ea)
    _a(film, c, b, t)
    c.restore()
    c.save(); c.translate(-dx * W * (1 - eb), -dy * H * (1 - eb))
    _b(film, c, b, t, clip=True)
    c.restore()
    horizontal = dx != 0
    seam = (W / 2 + dx * W * (eb - 0.5)) if horizontal else (H / 2 + dy * H * (eb - 0.5))
    _streaks(film, c, b, p, seam, horizontal, t)
    c.restore()


def shadow_rgb(film: Film) -> tuple[int, int, int]:
    """Theme-tinted shadow: the ground colour taken most of the way to black (never pure #000)."""
    return D.mix_rgb(film.pal.rgb("bg"), (0, 0, 0), 0.72)


def _draw_push(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    """A decisive panel move: the incoming panel drives in on an in-out cubic that leads the cut
    while the outgoing scene is pushed out by the same edge with a parallax lag. The window's
    shutter is short (1.25x), so the move reads as one clean streak; there is no full-frame dim,
    so the frame never washes out mid-move."""
    W, H = film.W, film.H
    dx, dy = b.direction
    eb = lead(p, 0.8)                              # incoming panel position, leading the cut
    ea = 0.42 * eb                                 # outgoing pushed by the panel with a parallax lag
    c.save(); c.translate(dx * W * ea, dy * H * ea)
    _a(film, c, b, t)
    c.restore()
    ox, oy = -dx * W * (1 - eb), -dy * H * (1 - eb)
    s = min(W, H) / 1080.0
    # soft, theme-tinted shadow cast by the leading edge of the incoming panel
    k = math.sin(math.pi * E.clamp(eb))
    if k > 0.01:
        sh = skia.Rect(ox, oy, ox + W, oy + H)
        c.drawRect(sh, D.paint(D.argb(shadow_rgb(film), 0.38 * k), blur=36 * s))
    c.save(); c.translate(ox, oy)
    _b(film, c, b, t, clip=True)
    c.restore()


def _draw_zoom(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    W, H = film.W, film.H

    def scaled(sc: float) -> None:
        c.translate(W / 2, H / 2); c.scale(sc, sc); c.translate(-W / 2, -H / 2)

    if p < 0.5:
        u = E.in_cubic(p * 2)
        c.save(); scaled(1 + 1.6 * u)
        _a(film, c, b, t)
        c.restore()
        al = E.smoothstep(0.22, 0.5, p)
        if al > 0.002:
            c.save(); scaled(0.62 + 0.23 * al)
            c.saveLayerAlpha(None, int(255 * al))
            _b(film, c, b, t)
            c.restore(); c.restore()
    else:
        u = E.out_expo((p - 0.5) * 2)
        c.save(); scaled(1 + 1.6 + 2.0 * u)
        _a(film, c, b, t)
        c.restore()
        c.save(); scaled(0.85 + 0.15 * u)
        _b(film, c, b, t)
        c.restore()


def _draw_squash(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    W, H = film.W, film.H
    film._ground_only(c, b.b, t)
    ua = E.in_cubic(E.clamp(p / 0.5))
    if ua < 0.999:
        c.save(); c.translate(W / 2, H / 2); c.scale(1 + 0.15 * ua, max(1 - ua, 0.001)); c.translate(-W / 2, -H / 2)
        _a(film, c, b, t, clip=True)
        c.restore()
    ub = E.out_expo(E.clamp((p - 0.3) / 0.7))
    if ub > 0.001:
        c.save(); c.translate(W / 2, H / 2); c.scale(1.12 - 0.12 * ub, max(ub, 0.001)); c.translate(-W / 2, -H / 2)
        _b(film, c, b, t, clip=True)
        c.restore()


def _mask_path(film: Film, b: Boundary, e: float) -> tuple[skia.Path, float]:
    """Wipe mask for edge progress e in [0, 1]; returns (path, extra full-frame alpha)."""
    W, H = film.W, film.H
    shape = str(b.params.get("shape", "rect"))
    dx, dy = b.direction
    path = skia.Path()
    diag = math.hypot(W, H)
    if shape == "circle":
        at = b.params.get("at", [0.5, 0.5])
        cx, cy = W * float(at[0]), H * float(at[1])
        rmax = math.hypot(max(cx, W - cx), max(cy, H - cy)) * 1.08
        path.addCircle(cx, cy, max(rmax * e, 0.5))
        return path, 0.0
    if shape == "type":
        text = str(b.params.get("text") or b.b.p.get("text") or (b.b.p.get("words") or ["*"])[0])[:12] or "*"
        fam = film.theme.get("display", "Archivo")
        probe = F.font(fam, 100.0, 900, 100.0)
        size = min(H * 0.5, 100.0 * W * 0.8 / max(probe.measureText(text), 1.0))
        font = F.font(fam, size, 900, 100.0)
        tw = font.measureText(text)
        g = D.text_path(text, -tw / 2, 0, font)
        bb = g.computeTightBounds()
        g.offset(0, -(bb.top() + bb.bottom()) / 2)
        sc = 0.35 * math.exp(4.2 * e)            # glyphs grow until a counter swallows the frame
        m = skia.Matrix.Translate(W / 2, H / 2)
        m = skia.Matrix.Concat(m, skia.Matrix.Scale(sc, sc))
        g.transform(m)
        return g, E.smoothstep(0.62, 0.95, e)
    if shape == "diagonal":
        ang = math.radians(float(b.params.get("angle", 20)))
        horiz = dx != 0 or dy == 0
        sgn = (dx if horiz else dy) or 1
        # a big rectangle whose leading edge sweeps across, rotated about the centre
        lead_ = -diag / 2 + diag * e
        r = skia.Rect(-diag, -diag, lead_, diag) if horiz else skia.Rect(-diag, -diag, diag, lead_)
        path.addRect(r)
        m = skia.Matrix.Translate(W / 2, H / 2)
        m = skia.Matrix.Concat(m, skia.Matrix.RotateRad(ang))
        if sgn < 0:
            m = skia.Matrix.Concat(m, skia.Matrix.Scale(-1, 1) if horiz else skia.Matrix.Scale(1, -1))
        path.transform(m)
        return path, 0.0
    # rect: reveal along the direction of travel ("left" = edge travels right-to-left)
    if dx > 0:
        path.addRect(skia.Rect(0, 0, W * e, H))
    elif dx < 0:
        path.addRect(skia.Rect(W * (1 - e), 0, W, H))
    elif dy > 0:
        path.addRect(skia.Rect(0, 0, W, H * e))
    else:
        path.addRect(skia.Rect(0, H * (1 - e), W, H))
    return path, 0.0


def _draw_wipe(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    W, H = film.W, film.H
    s = min(W, H) / 1080.0
    e = E.in_out_cubic(p) if b.params.get("shape", "rect") != "type" else E.in_out_quad(p)
    soft = float(b.params.get("soft", 18)) * s
    _a(film, c, b, t)
    path, full = _mask_path(film, b, e)
    c.saveLayer(None, None)
    _b(film, c, b, t)
    c.saveLayer(None, skia.Paint(BlendMode=skia.BlendMode.kDstIn))
    mp = skia.Paint(AntiAlias=True, Color=0xFFFFFFFF)
    if soft > 0.5:
        mp.setMaskFilter(skia.MaskFilter.MakeBlur(skia.BlurStyle.kNormal_BlurStyle, soft / 2))
    c.drawPath(path, mp)
    if full > 0:
        c.drawRect(skia.Rect(0, 0, W, H), D.paint(D.argb((255, 255, 255), full)))
    c.restore()
    c.restore()
    edge = b.params.get("edge", "accent")
    if edge and e < 0.995:
        k = E.clamp(min(e, 1 - e) * 8) * (1 - full)
        ew = float(b.params.get("edge_w", 10)) * s
        _wipe_edge(film, c, b, path, e, D.paint(film.pal(str(edge), 0.95 * k), stroke=ew))


def _wipe_edge(film: Film, c: skia.Canvas, b: Boundary, path: skia.Path, e: float, paint: skia.Paint) -> None:
    """Stroke only the moving (leading) edge of a wipe mask, never the frame-border sides."""
    W, H = film.W, film.H
    shape = str(b.params.get("shape", "rect"))
    dx, dy = b.direction
    if shape == "rect":
        if dx > 0:
            c.drawLine(W * e, -H, W * e, 2 * H, paint)
        elif dx < 0:
            c.drawLine(W * (1 - e), -H, W * (1 - e), 2 * H, paint)
        elif dy > 0:
            c.drawLine(-W, H * e, 2 * W, H * e, paint)
        else:
            c.drawLine(-W, H * (1 - e), 2 * W, H * (1 - e), paint)
        return
    if shape == "diagonal":
        # the mask is a huge rect whose leading side is the only edge inside the frame: clip the
        # stroke to the frame so the far sides (outside it) never show
        c.save(); c.clipRect(skia.Rect(0, 0, W, H))
        c.drawPath(path, paint)
        c.restore()
        return
    c.drawPath(path, paint)  # circle / type: the whole outline is the leading edge


def _record(film: Film, sc_draw: Callable[[skia.Canvas], None]) -> skia.Picture:
    rec = skia.PictureRecorder()
    cv = rec.beginRecording(skia.Rect(-film.W, -film.H, 2 * film.W, 2 * film.H))
    sc_draw(cv)
    return rec.finishRecordingAsPicture()


def _draw_slice(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    W, H = film.W, film.H
    s = min(W, H) / 1080.0
    dx, dy = b.direction
    horiz = dx != 0
    n = max(2, int(b.params.get("n", 6)))
    st = float(b.params.get("stagger", 0.07))
    pa = _record(film, lambda cv: _a(film, cv, b, t, clip=True))
    pb = _record(film, lambda cv: _b(film, cv, b, t, clip=True))
    span = 1 - (n - 1) * st
    edge = film.pal(str(b.params.get("edge", "accent")), 0.9)
    for i in range(n):
        q = E.clamp((p - i * st) / span)
        sgn = (1 if i % 2 == 0 else -1) * ((dx or dy) or 1)
        ea, eb = E.in_cubic(q), lead(q, 0.7)
        band = skia.Rect(0, H * i / n, W, H * (i + 1) / n) if horiz else skia.Rect(W * i / n, 0, W * (i + 1) / n, H)
        c.save(); c.clipRect(band)
        c.save(); c.translate(sgn * W * ea, 0) if horiz else c.translate(0, sgn * H * ea)
        c.drawPicture(pa)
        c.restore()
        off = -sgn * (W if horiz else H) * (1 - eb)
        c.save(); c.translate(off, 0) if horiz else c.translate(0, off)
        c.drawPicture(pb)
        c.restore()
        if 0.01 < eb < 0.99:
            if horiz:
                x = off + (W if sgn < 0 else 0)
                c.drawRect(skia.Rect(x - 4 * s, band.top(), x + 4 * s, band.bottom()), D.paint(edge))
            else:
                y = off + (H if sgn < 0 else 0)
                c.drawRect(skia.Rect(band.left(), y - 4 * s, band.right(), y + 4 * s), D.paint(edge))
        c.restore()


def _draw_split(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    W, H = film.W, film.H
    dx, dy = b.direction
    vertical_seam = dx != 0          # left/right: halves part sideways
    e = E.in_out_cubic(p) ** 0.85
    sc = 1.08 - 0.08 * E.out_expo(p)
    c.save(); c.translate(W / 2, H / 2); c.scale(sc, sc); c.translate(-W / 2, -H / 2)
    _b(film, c, b, t)
    c.restore()
    pa = _record(film, lambda cv: _a(film, cv, b, t))
    for half in (0, 1):
        sgn = -1 if half == 0 else 1
        r = (skia.Rect(0, 0, W / 2, H) if half == 0 else skia.Rect(W / 2, 0, W, H)) if vertical_seam else \
            (skia.Rect(0, 0, W, H / 2) if half == 0 else skia.Rect(0, H / 2, W, H))
        c.save()
        if vertical_seam:
            c.translate(sgn * W * 0.55 * e, 0)
        else:
            c.translate(0, sgn * H * 0.55 * e)
        c.clipRect(r)
        c.drawPicture(pa)
        c.restore()


def _draw_match(film: Film, c: skia.Canvas, b: Boundary, p: float, t: float) -> None:
    """Whole-scene fit (opt-in "fit": true): the incoming scene starts transformed so its published
    element sits on the outgoing one, then relaxes to identity over the morph window."""
    if t < b.t:
        _a(film, c, b, t)
        return
    m = film.match_matrix(b, E.out_expo(p))
    if m is None:
        _b(film, c, b, t)
        return
    c.save(); c.concat(m)
    _b(film, c, b, t)
    c.restore()


DRAW: dict[str, Callable[..., None]] = {
    "cut": _draw_cut, "flash": _draw_cut, "match": _draw_match, "cross": _draw_cross, "dissolve": _draw_dissolve,
    "whip": _draw_whip, "push": _draw_push, "zoom": _draw_zoom, "squash": _draw_squash, "wipe": _draw_wipe,
    "slice": _draw_slice, "split": _draw_split,
}


def flash_first_frame(cut: float, fps: float) -> int:
    """Index of the first output frame at or after the cut (the flash's first frame)."""
    return int(math.ceil(cut * fps - 1e-6))


def flash_frame_alpha(cut: float, i: int, fps: float, frames: int = 2) -> float:
    """Accent flash opacity for whole output frame i: exactly `frames` frames starting at the first
    frame at or after the cut, 0 elsewhere. Evaluated once per delivered frame (the compositor
    applies it after motion blur), so a wide shutter can never smear it into a third frame."""
    k = int(i) - flash_first_frame(cut, fps)
    if 0 <= k < frames:
        return FLASH_ALPHA[min(k, len(FLASH_ALPHA) - 1)]
    return 0.0


def flash_alpha(cut: float, t: float, fps: float, frames: int = 2) -> float:
    """Accent flash opacity at time t, quantised to the output frame t falls on (frame i covers
    [i - 0.5, i + 0.5) / fps), so it is the same for every sub-sample of a frame."""
    return flash_frame_alpha(cut, int(math.floor(t * fps + 0.5 + 1e-9)), fps, frames)
