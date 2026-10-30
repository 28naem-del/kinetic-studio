"""Social clip scenes: hook opener, quote card, karaoke captions and a call-to-follow end card.

All four share one editorial frame on the S6 grid (kstudio.layout), so cuts between them land
on the same lines:
- a creator header (avatar, name, handle / series, scene index) on the top safe line;
- a kicker (32 px caps, +12 %) whose cap top sits on the shared anchor (y = 560 on a reel);
- the text block in Fraunces with its optical size, SOFT and WONK axes set, flush left on x0,
  wrapped with a balanced rag (and hanging opening punctuation);
- bottom chrome on the safe line: story-style progress segments (one per scene), the scene
  label and a 4-dot bar counter that lights the current beat.

Timing is in beats. Hook lines land one per beat, quote words and karaoke words land on the
8th grid (`stagger` / `per` = 1/2 beat), karaoke pages turn on bar lines. Entries are
out_expo / out_quint, exits in_quart; springs only move physical objects (the karaoke pill, the
follow button). Words that are not spoken yet are dimmed to 45 % of the text colour mixed into
the ground (warm, not a grey alpha).

Ground: social scenes default to the "paper" ground (a fibre/noise field), with a warm light
pool and a floor gradient drawn on a deep parallax layer; film grain and the "warm" grade come
from the mode's post settings.

Creator data comes from the spec's top-level "creator" object
({"name", "handle", "series", "initials"}); without it the header falls back to the spec
"handle" (and is hidden when neither is set). A karaoke/quote "speaker" ({"name", "role",
"initials"}) falls back to the creator.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Any

import skia

from .. import draw as D
from .. import ease as E
from .. import fonts as F
from .. import layout as L
from .base import Ctx, Cue, Scene, register

DIM = 0.45                 # dimmed words: 45 % of the text colour over the ground
SOFT, WONK = 60.0, 1.0     # Fraunces soft corners + wonky alternates for the editorial voice
_PUNCT = ".,!?;:…\"'“”‘’"
_HANG = "“\"‘'«("          # opening punctuation that hangs into the left margin
PILL_BOUNCE = 0.22         # pill / button springs: about 2 % overshoot (S5 cap is 4 %)


# ---------------------------------------------------------------------- shared helpers

def _g(ctx: Ctx) -> L.Grid:
    return L.grid(ctx)


def _rgb_mix(ctx: Ctx, a: str, b: str, k: float) -> tuple[int, int, int]:
    return D.mix_rgb(ctx.pal.rgb(a), ctx.pal.rgb(b), k)


def dim_color(ctx: Ctx, key: str = "ink", k: float = DIM, alpha: float = 1.0, ground: str = "bg") -> int:
    """`key` at `k` of its strength over the ground (an opaque warm tone, not a grey alpha)."""
    return D.argb(_rgb_mix(ctx, ground, key, k), alpha)


def _serif(ctx: Ctx, px: float, weight: float = 650, soft: float = SOFT, wonk: float = WONK) -> skia.Font:
    """Display face (Fraunces in the warm theme) at frame px, with opsz/SOFT/WONK set."""
    g = _g(ctx)
    fam = ctx.theme.get("display", "Fraunces")
    kw: dict[str, Any] = {"soft": round(soft / 5.0) * 5.0, "wonk": wonk}
    oz = F.opsz_for(fam, px / max(g.u, 1e-6))
    if oz is not None:
        kw["opsz"] = round(oz)
    return F.font(fam, px, int(round(weight / 10.0) * 10), 100.0, **kw)


def _norm(w: str) -> str:
    return w.lower().strip(_PUNCT)


def _creator(ctx: Ctx) -> dict[str, str]:
    film = ctx.film
    if film is None:
        return {}
    cr = dict(film.spec.get("creator") or {})
    if not cr and getattr(film, "handle", ""):
        cr = {"handle": film.handle}
    if not cr:
        return {}
    name = str(cr.get("name") or cr.get("handle", "").lstrip("@"))
    initials = str(cr.get("initials") or "".join(w[0] for w in name.split()[:2]).upper() or "K")
    return {"name": name, "handle": str(cr.get("handle", "")), "series": str(cr.get("series", "")),
            "initials": initials}


def _person(ctx: Ctx, spec: Any) -> dict[str, str]:
    """A speaker from a scene param ({"name", "role", "initials"} or a plain name), else the creator."""
    if isinstance(spec, str) and spec:
        spec = {"name": spec}
    if isinstance(spec, dict) and spec.get("name"):
        name = str(spec["name"])
        return {"name": name, "role": str(spec.get("role", "")),
                "initials": str(spec.get("initials") or "".join(w[0] for w in name.split()[:2]).upper())}
    cr = _creator(ctx)
    if not cr:
        return {}
    return {"name": cr["name"], "role": cr.get("series", "") or cr.get("handle", ""), "initials": cr["initials"]}


def _scene_index(ctx: Ctx, scene: Scene) -> tuple[int, int]:
    film = ctx.film
    if film is not None and scene in film.scenes:
        return film.scenes.index(scene) + 1, len(film.scenes)
    return 1, 1


def _film_t(ctx: Ctx) -> float:
    return ctx.start + ctx.lt


def _beat_pulse(ctx: Ctx, every: float = 1.0, decay: float = 0.5) -> float:
    """1 on each `every`-beat grid line of the film, decaying out_expo over `decay` beats."""
    b = ctx.tl.time_to_beat(_film_t(ctx)) / every
    f = (b - math.floor(b)) * every
    return 1.0 - E.out_expo(E.clamp(f / max(decay, 1e-3)))


def _scale_about(c: skia.Canvas, cx: float, cy: float, k: float) -> None:
    c.translate(cx, cy)
    c.scale(k, k)
    c.translate(-cx, -cy)


def kicker_font(ctx: Ctx) -> tuple[skia.Font, float]:
    r = L.TYPE["kicker"]
    return r.font(ctx, weight=700), r.tk(ctx)


def draw_kicker(ctx: Ctx, text: str, x: float, top: float, color: int, rule: float = 0.0,
                right: str = "", right_color: int | None = None, x_right: float | None = None,
                rule_color: int | None = None) -> float:
    """Kicker (caps, tracked) with its cap top at `top`; an optional accent rule (0..1 drawn)
    before it and a right-aligned counterpart on the same line. Returns the baseline."""
    f, tk = kicker_font(ctx)
    g = _g(ctx)
    cap = D.cap_height(f)
    base = top + cap
    x0 = x
    if rule > 0:
        rw = g.px(56)
        ctx.c.drawRect(skia.Rect(x, base - cap / 2 - g.px(2), x + rw * E.clamp(rule), base - cap / 2 + g.px(2)),
                       D.paint(rule_color if rule_color is not None else ctx.pal("accent")))
        x0 = x + rw + g.px(20)
    if text:
        D.draw_text(ctx.c, text.upper(), x0, base, f, color, align="left", tracking=tk)
    if right:
        D.draw_text(ctx.c, right.upper(), g.x1 if x_right is None else x_right, base, f,
                    right_color if right_color is not None else color, align="right", tracking=tk)
    return base


def draw_header(ctx: Ctx, scene: Scene, alpha: float = 1.0) -> float:
    """Creator lockup on the top safe line: avatar disc, name over handle / series, and the
    scene index on the right. Returns the header bottom (y)."""
    cr = _creator(ctx)
    g = _g(ctx)
    if not cr or alpha <= 0.002:
        return g.safe_top
    c = ctx.c
    d = g.px(88)
    top = g.safe_top
    cx, cy = g.x0 + d / 2, top + d / 2
    c.drawCircle(cx, cy, d / 2, D.paint(ctx.pal("accent", alpha)))
    c.drawCircle(cx, cy, d / 2 + g.px(7), D.paint(dim_color(ctx, "accent", 0.55, alpha), stroke=g.px(3)))
    fi = _serif(ctx, g.px(40), 800)
    D.draw_text(c, cr["initials"], cx, cy, fi, ctx.pal("ink_dark", alpha), valign="middle")
    cap_r = L.TYPE["caption"]
    fn = cap_r.font(ctx, weight=700)
    meta = L.TYPE["meta"]
    fm = meta.font(ctx, weight=500)
    tx = g.x0 + d + g.px(28)
    D.draw_text(c, cr["name"], tx, cy - g.px(6), fn, ctx.pal("ink", alpha), align="left", tracking=cap_r.tk(ctx))
    sub = "  ·  ".join(x for x in (cr["handle"], cr["series"]) if x)
    if sub:
        D.draw_text(c, sub, tx, cy + D.cap_height(fm) + g.px(16), fm, dim_color(ctx, "ink", 0.62, alpha),
                    align="left", tracking=meta.tk(ctx))
    i, n = _scene_index(ctx, scene)
    D.draw_text(c, f"{i:02d}/{n:02d}", g.x1, cy + D.cap_height(fm) / 2, fm, dim_color(ctx, "ink", 0.62, alpha),
                align="right", tracking=meta.tk(ctx))
    return top + d


def chrome_top(ctx: Ctx) -> float:
    """Top of the bottom chrome (labels above the progress segments on the safe line)."""
    g = _g(ctx)
    fm = L.TYPE["meta"].font(ctx, weight=600)
    return g.safe_bottom - g.bl(1) - g.bl(3) - D.cap_height(fm)


def draw_chrome(ctx: Ctx, scene: Scene, label: str = "") -> None:
    """Bottom chrome on the safe line: story progress segments (one per scene, filling with
    film time), the scene label on the left and a bar counter (4 dots, current beat lit) on
    the right."""
    g = _g(ctx)
    c = ctx.c
    t = _film_t(ctx)
    h = g.bl(1)
    y1 = g.safe_bottom
    film = ctx.film
    scenes = film.scenes if film is not None else [scene]
    total = sum(max(s.dur, 1e-6) for s in scenes)
    gap = g.px(10)
    avail = g.content_w - gap * (len(scenes) - 1)
    x = g.x0
    track = dim_color(ctx, "ink", 0.22)
    for s in scenes:
        w = avail * max(s.dur, 1e-6) / total
        D.rrect(c, x, y1 - h, w, h, h / 2, D.paint(track))
        u = E.clamp((t - s.start) / max(s.dur, 1e-6)) if film is not None else E.clamp(ctx.lt / max(ctx.dur, 1e-6))
        if u > 0:
            D.rrect(c, x, y1 - h, max(w * u, h), h, h / 2, D.paint(ctx.pal("accent")))
        x += w + gap
    meta = L.TYPE["meta"]
    fm = meta.font(ctx, weight=600)
    base = y1 - h - g.bl(3)
    i, n = _scene_index(ctx, scene)
    D.draw_text(c, f"{i:02d}  {label or scene.type}".upper(), g.x0, base, fm, dim_color(ctx, "ink", 0.7),
                align="left", tracking=meta.tk(ctx))
    # bar counter: 4 dots, the current beat lit and kicked on the hit
    beat = ctx.tl.time_to_beat(t)
    bpb = ctx.tl.beats_per_bar
    cur = int(math.floor(beat + 1e-6)) % bpb
    pulse = _beat_pulse(ctx, 1.0, 0.5)
    r = g.px(7)
    step = g.px(26)
    cy = base - D.cap_height(fm) / 2
    for k in range(bpb):
        xk = g.x1 - r - (bpb - 1 - k) * step
        on = k == cur
        rr = r * (1.0 + 0.45 * pulse) if on else r * 0.75
        c.drawCircle(xk, cy, rr, D.paint(ctx.pal("accent") if on else dim_color(ctx, "ink", 0.4)))
    wtxt = f"BAR {ctx.tl.bar_of(t):02d}"
    D.draw_text(c, wtxt, g.x1 - 2 * r - (bpb - 1) * step - g.px(22), base, fm, dim_color(ctx, "ink", 0.7),
                align="right", tracking=meta.tk(ctx))


def draw_backdrop(ctx: Ctx, strength: float = 1.0) -> None:
    """Warm light on the paper ground: a slow light pool from the upper left and a floor
    gradient towards bg2, on a deep parallax layer (moves less than the type)."""
    c, W, H = ctx.c, ctx.W, ctx.H
    t = _film_t(ctx)
    with ctx.layer(2.2, world=True):
        x = W * (0.22 + 0.05 * math.sin(t * 0.21))
        y = H * (0.30 + 0.03 * math.cos(t * 0.17))
        pool = D.radial_grad(x, y, max(W, H) * 0.62,
                             [ctx.pal("accent", 0.20 * strength), ctx.pal("accent2", 0.07 * strength),
                              ctx.pal("accent2", 0.0)], [0.0, 0.5, 1.0])
        c.drawRect(skia.Rect(-W, -H, 2 * W, 2 * H), D.paint(0x01000000, shader=pool))
        floor = D.linear_grad(0, H * 0.45, 0, H * 1.05, [ctx.pal("bg2", 0.0), ctx.pal("bg2", 0.55 * strength)])
        c.drawRect(skia.Rect(-W, -H, 2 * W, 2 * H), D.paint(0x01000000, shader=floor))


def text_font(ctx: Ctx, words: list[str], size: Any, max_lines: int, weight: float = 650,
              min_ref: float = 95.0) -> tuple[skia.Font, list[str], float]:
    """Title-role Fraunces (126 px; or `size` ref px / role name) wrapped to the full content
    width with balanced rag; shrinks towards `min_ref` px only if the text needs more than
    `max_lines` lines. Returns (font, lines, tracking em)."""
    g = _g(ctx)
    role = _role(size)
    ref = role.size
    text = " ".join(words)
    lo = min(min_ref, ref)
    while True:
        px = ref * g.u * g.P.type_k
        f = _serif(ctx, px, weight)
        tk = F.track(ref, False, ctx.theme.get("display"))
        lines = D.wrap(f, text, g.content_w, tk, style="balance")
        if len(lines) <= max_lines or ref <= lo + 1e-6:
            return f, lines, tk
        ref = max(lo, ref * 0.94)


def _role(size: Any) -> L.Role:
    if isinstance(size, str) and size in L.TYPE:
        return L.TYPE[size]
    if isinstance(size, (int, float)) and not isinstance(size, bool):
        return L.TYPE.role(float(size)).with_(size=float(size), leading=L.TYPE["title"].leading)
    return L.TYPE["title"]


@lru_cache(maxsize=256)
def _balance(widths: tuple[float, ...], gap: float, max_w: float) -> tuple[int, ...]:
    """Line-break indices for words of `widths` separated by `gap`: the fewest lines that fit
    `max_w`, then the narrowest width that keeps that line count (balanced rag, no widows)."""
    def greedy(lim: float) -> list[int]:
        breaks, w = [], 0.0
        for i, ww in enumerate(widths):
            if i and w + gap + ww > lim:
                breaks.append(i)
                w = ww
            else:
                w = ww if i == 0 else w + gap + ww
        return breaks
    if not widths:
        return ()
    n = len(greedy(max_w))
    lo, hi = max(widths), max(max_w, max(widths))
    for _ in range(24):
        mid = (lo + hi) / 2
        if len(greedy(mid)) <= n:
            hi = mid
        else:
            lo = mid
    return tuple(greedy(hi))


def set_words(ctx: Ctx, words: list[str], size: Any, max_w: float, max_lines: int, weight: float = 650,
              min_ref: float = 95.0, gap_em: float = 0.0, top: float = 0.0, x: float | None = None,
              soft: float = SOFT, hang: bool = True) -> tuple[skia.Font, float, list[tuple[str, float, float, float, int]]]:
    """Set `words` flush left from `x` (default x0) with the cap top of line 1 at `top`.

    The role size (default title 126) shrinks towards `min_ref` only when the text needs more
    than `max_lines`. `gap_em` widens the word space (room for a scaled active word and its
    pill). Opening punctuation at the start of a line hangs into the margin. Returns (font,
    tracking em, boxes) with boxes = (word, x, baseline, width, line)."""
    g = _g(ctx)
    role = _role(size)
    ref = role.size
    lo = min(min_ref, ref)
    x = g.x0 if x is None else x
    fam = ctx.theme.get("display")
    while True:
        px = ref * g.u * g.P.type_k
        f = _serif(ctx, px, weight, soft=soft)
        tk = F.track(ref, False, fam)
        gap = max(D.text_w(f, " ", tk), px * gap_em)
        widths = tuple(round(D.text_w(f, w, tk), 2) for w in words)
        brk = _balance(widths, round(gap, 2), round(max_w, 2))
        if len(brk) + 1 <= max_lines or ref <= lo + 1e-6:
            break
        ref = max(lo, ref * 0.94)
    cap = D.cap_height(f)
    pitch = px * role.leading
    boxes = []
    li, xx = 0, x
    bset = set(brk)
    for i, (w, ww) in enumerate(zip(words, widths)):
        if i in bset:
            li += 1
            xx = x
        wx = xx
        if hang and wx == x and w and w[0] in _HANG:
            wx -= D.text_w(f, w[0], tk)
        boxes.append((w, wx, top + cap + li * pitch, ww, li))
        xx = wx + ww + gap
    return f, tk, boxes


def word_boxes(f: skia.Font, lines: list[str], x: float, top: float, leading: float,
               tk: float) -> list[tuple[str, float, float, float, int]]:
    """(word, x, baseline, width, line) for each word of the set lines (cap top of line 1 at top)."""
    out = []
    cap = D.cap_height(f)
    pitch = f.getSize() * leading
    sp = D.text_w(f, " ", tk)
    for li, ln in enumerate(lines):
        base = top + cap + li * pitch
        xx = x
        for w in ln.split():
            ww = D.text_w(f, w, tk)
            out.append((w, xx, base, ww, li))
            xx += ww + sp
    return out


def draw_speaker(ctx: Ctx, person: dict[str, str], top: float, u: float, speaking: float = 1.0,
                 on_light: bool = False) -> float:
    """Speaker lower third at `top`: avatar disc with a ring that pulses on the 8ths while the
    speaker talks, name (caption 40/700) over role (meta), three level bars. The plate wipes
    in from the left with u (0..1, out_expo applied by the caller). Returns the bottom (y)."""
    if not person or u <= 0.002:
        return top
    g = _g(ctx)
    c = ctx.c
    h = g.px(128)
    d = g.px(84)
    cap_r, meta = L.TYPE["caption"], L.TYPE["meta"]
    fn = cap_r.font(ctx, weight=700)
    fm = meta.font(ctx, weight=500)
    tx = g.x0 + g.px(24) + d + g.px(28)
    tw = max(D.text_w(fn, person["name"], cap_r.tk(ctx)), D.text_w(fm, person.get("role", ""), meta.tk(ctx)))
    bars_w = g.px(60)
    w = min(g.content_w, tx - g.x0 + tw + g.px(40) + bars_w + g.px(28))
    c.save()
    c.clipRect(skia.Rect(g.x0 - g.px(4), top - g.px(30), g.x0 + w * u + g.px(4), top + h + g.px(30)))
    plate = D.mix_rgb(ctx.pal.rgb("bg"), ctx.pal.rgb("ink"), 0.09) if not on_light else ctx.pal.rgb("paper")
    D.rrect(c, g.x0, top, w, h, h / 2, D.paint(D.argb(plate)))
    cx, cy = g.x0 + g.px(24) + d / 2, top + h / 2
    e8 = _beat_pulse(ctx, 0.5, 0.4) * speaking
    c.drawCircle(cx, cy, d / 2 + g.px(6) + g.px(10) * e8, D.paint(ctx.pal("accent", 0.25 + 0.55 * e8), stroke=g.px(3)))
    c.drawCircle(cx, cy, d / 2, D.paint(ctx.pal("accent2")))
    fi = _serif(ctx, g.px(36), 800)
    D.draw_text(c, person.get("initials", ""), cx, cy, fi, ctx.pal("ink_dark"), valign="middle")
    D.draw_text(c, person["name"], tx, cy - g.px(6), fn, ctx.pal("ink"), align="left", tracking=cap_r.tk(ctx))
    if person.get("role"):
        D.draw_text(c, person["role"], tx, cy + D.cap_height(fm) + g.px(16), fm, dim_color(ctx, "ink", 0.62),
                    align="left", tracking=meta.tk(ctx))
    # level bars, driven by the 8th pulse with a per-bar phase
    bx = g.x0 + w - g.px(28) - bars_w
    t = _film_t(ctx)
    for k in range(3):
        lvl = 0.25 + 0.75 * speaking * (0.45 * e8 + 0.55 * (0.5 + 0.5 * E.noise1(t * 7.0 + k * 3.3, seed=5)))
        bh = g.px(52) * lvl
        D.rrect(c, bx + k * g.px(22), cy - bh / 2, g.px(12), bh, g.px(6), D.paint(ctx.pal("accent")))
    c.restore()
    return top + h


class SocialScene(Scene):
    """Shared base: social scenes default to the paper ground (scene param `ground_style`)."""
    defaults: dict[str, Any] = {"ground_style": "paper", "backdrop": True}

    def __init__(self, params: dict[str, Any], start: float, dur: float, tl: Any, mode_cfg: dict):
        super().__init__(params, start, dur, tl, mode_cfg)
        if self.ground_style is None:
            self.ground_style = self.p_raw.get("ground_style")

    def _bt0(self) -> float:
        return self.tl.time_to_beat(self.start)

    def _at_beat(self, b: float) -> float:
        return self.tl.beat_to_time(self._bt0() + b) - self.start

    def _beat_of(self, t: float) -> float:
        return self.tl.time_to_beat(self.start + t) - self._bt0()

    def _on_beat(self, t: float) -> bool:
        b = self._beat_of(t)
        return abs(b - round(b)) * self.beat_len < 0.002

    def shutter(self, lt: float) -> float:
        """Keep the motion-blur window inside the scene: a hard cut seldom falls on a frame
        boundary at 86 BPM, and a shutter that straddles it would smear the two scenes into one
        frame. The 180-degree window at 30 fps reaches 1/120 s either side of the frame."""
        reach = 120.0
        m = min(1.0, max(self.dur - lt, 0.0) * reach)
        if self.start > 0:
            m = min(m, max(lt, 0.0) * reach)
        return m

    def backdrop(self, ctx: Ctx) -> None:
        if self.p.get("backdrop", True):
            draw_backdrop(ctx)


# ---------------------------------------------------------------------- hook

@register("hook")
class Hook(SocialScene):
    """Hook opener: a stacked headline that lands one line per beat.

    `lines`: strings; `roles`: one role per line (display 224 / headline 168 / title 126;
    "auto", the default, picks the largest role that fits the content width). Each line rises out of a mask
    (out_expo over 1/3 beat) while its weight axis opens from 280 to `weight` and the SOFT
    axis closes, so the word lands heavy on the kick. Emphasis words are set in the accent and
    get a hand-drawn swash underline drawn on the 8th after they land. The first line is
    already on screen at frame 0. `timer` shows a running seconds readout on the kicker line.
    SFX: an impact + hit on the first line, a thud (display) or soft pop (smaller roles) on
    every other line, all on the beat."""
    defaults = {**SocialScene.defaults, "lines": ["Two", "seconds.", "That is all", "you get."], "kicker": "",
                "roles": [], "emphasis": [], "stagger": "1b", "first": "0b", "weight": 760, "timer": True, "label": "hook",
                "mark": 2.0}

    def _meter(self, ctx: Ctx) -> None:
        """Watch-time meter in the lower third: a large mono seconds readout over a ruler with
        a tick on every 8th of the scene; the fill runs with the clock and the `mark` (seconds)
        is flagged in accent2. It fills the bottom half while the headline builds."""
        g = _g(ctx)
        c, lt = ctx.c, ctx.lt
        meta = L.TYPE["meta"]
        fm = meta.font(ctx, weight=600)
        y_rule = chrome_top(ctx) - g.bl(9)
        y_num = y_rule - g.bl(5)
        rows = self.layout(ctx)
        span = max(self.dur, 1e-6)
        mark = float(self.p["mark"])
        w = g.content_w
        prog = E.clamp(lt / span)
        # running readout, right-aligned on the first line's baseline where the line is short
        if rows:
            r0 = rows[0]
            f0 = _serif(ctx, r0["px"], float(self.p["weight"]), soft=0)
            big = meta.font(ctx, weight=500, size=g.px(96))
            room = g.content_w - D.text_w(f0, r0["text"], r0["tk"]) - g.px(48)
            if D.text_w(big, "0.00s", -0.02) <= room:
                D.draw_text(c, f"{lt:0.2f}s", g.x1, r0["base"], big,
                            ctx.pal("ink") if lt < mark else ctx.pal("accent2"), align="right", tracking=-0.02)
        D.draw_text(c, "SECONDS WATCHED", g.x0, y_num, fm, dim_color(ctx, "ink", 0.7), align="left",
                    tracking=meta.tk(ctx))
        c.drawRect(skia.Rect(g.x0, y_rule - g.px(2), g.x1, y_rule + g.px(2)), D.paint(dim_color(ctx, "ink", 0.25)))
        c.drawRect(skia.Rect(g.x0, y_rule - g.px(4), g.x0 + w * prog, y_rule + g.px(4)), D.paint(ctx.pal("accent")))
        n8 = int(round(self.tl.beats_of(span, self.start) * 2))
        e8 = _beat_pulse(ctx, 0.5, 0.35)
        for k in range(n8 + 1):
            x = g.x0 + w * k / max(n8, 1)
            past = k / max(n8, 1) <= prog + 1e-6
            hh = g.px(18 if k % 2 == 0 else 10)
            if past and abs(k / max(n8, 1) - prog) < 0.5 / max(n8, 1):
                hh *= 1.0 + 0.8 * e8
            c.drawRect(skia.Rect(x - g.px(1.5), y_rule + g.px(8), x + g.px(1.5), y_rule + g.px(8) + hh),
                       D.paint(ctx.pal("accent") if past else dim_color(ctx, "ink", 0.4)))
        xm = g.x0 + w * E.clamp(mark / span)
        c.drawRect(skia.Rect(xm - g.px(2), y_rule - g.bl(3), xm + g.px(2), y_rule + g.bl(3)),
                   D.paint(ctx.pal("accent2")))
        D.draw_text(c, f"{mark:g} S", xm, y_rule - g.bl(4), fm, ctx.pal("accent2"), align="center",
                    tracking=meta.tk(ctx))

    def _lines(self) -> list[dict[str, str]]:
        out = []
        src = self.p["lines"] or str(self.p.get("text", "")).split("\n")
        roles = list(self.p.get("roles") or [])
        for i, ln in enumerate(src):
            if isinstance(ln, dict):
                out.append({"text": str(ln.get("text", "")), "role": str(ln.get("role", "auto"))})
            else:
                out.append({"text": str(ln), "role": str(roles[i]) if i < len(roles) else "auto"})
        return [x for x in out if x["text"]]

    def onsets(self) -> list[float]:
        st, first = float(self.p["stagger"]), float(self.p["first"])
        return [first + i * st for i in range(len(self._lines()))]

    def cues(self) -> list[Cue]:
        out = []
        roles = self._roles_ref()
        for i, t in enumerate(self.onsets()):
            if t >= self.dur - 1e-6:
                break
            if i == 0:
                out += [Cue("sfx", t, data={"sound": "impact", "gain": -9.0}),
                        Cue("hit", t, data={"strength": 0.5, "flash": 0.0})]
            elif roles[i] >= 168:
                out.append(Cue("sfx", t, data={"sound": "thud", "gain": -6.0}))
            else:
                out.append(Cue("sfx", t, data={"sound": "pop_soft", "gain": -7.0}))
        return out

    def _roles_ref(self) -> list[float]:
        """Reference px per line (auto roles assume a 888 px measure at the final weight)."""
        out = []
        for ln in self._lines():
            r = ln["role"]
            out.append(L.TYPE[r].size if r in L.TYPE else 0.0)
        return out

    def layout(self, ctx: Ctx) -> list[dict[str, Any]]:
        g = _g(ctx)
        fk, _ = kicker_font(ctx)
        y = g.anchor_y + D.cap_height(fk) + g.bl(7)
        w1 = float(self.p["weight"])
        fam = ctx.theme.get("display")
        rows = []
        base = None
        for ln in self._lines():
            role = ln["role"]
            if role not in L.TYPE:
                for cand in ("display", "headline", "title"):
                    r = L.TYPE[cand]
                    f = _serif(ctx, r.px(g), w1, soft=0)
                    if D.text_w(f, ln["text"], F.track(r.size, False, fam)) <= g.content_w or cand == "title":
                        role = cand
                        break
            r = L.TYPE[role]
            px = r.px(g)
            f = _serif(ctx, px, w1, soft=0)
            tk = F.track(r.size, False, fam)
            wmax = D.text_w(f, ln["text"], tk)
            if wmax > g.content_w:  # still too wide at title: fit
                px *= g.content_w / wmax
                f = _serif(ctx, px, w1, soft=0)
            cap = D.cap_height(f)
            base = (y + cap) if base is None else base + px * r.leading
            rows.append({"text": ln["text"], "px": px, "ref": r.size, "base": base, "cap": cap, "tk": tk,
                         "leading": r.leading})
        return rows

    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        g = _g(ctx)
        spb = self.beat_len
        self.backdrop(ctx)
        draw_header(ctx, self)
        kin = E.out_expo(E.clamp((lt + spb * 0.5) / (spb * 0.5)))
        draw_kicker(ctx, self.p["kicker"], g.x0, g.anchor_y, ctx.pal("accent", kin), rule=kin,
                    right=f"{self.p['mark']:g} s rule" if self.p["timer"] else "",
                    right_color=dim_color(ctx, "ink", 0.7))
        if self.p["timer"]:
            self._meter(ctx)
        emph = {_norm(e) for e in self.p["emphasis"]}
        land = spb / 3
        w0, w1 = 280.0, float(self.p["weight"])
        for i, (row, t0) in enumerate(zip(self.layout(ctx), self.onsets())):
            u = (lt - t0) / land + (0.55 if i == 0 else 0.0)  # the first line is already rising at frame 0
            if u <= 0:
                continue
            e = E.out_expo(E.clamp(u))
            ax = E.out_expo(E.clamp((lt - t0) / (spb * 0.5) + (0.35 if i == 0 else 0.0)))
            wt = round((w0 + (w1 - w0) * ax) / 20.0) * 20.0
            f = _serif(ctx, row["px"], wt, soft=100.0 * (1 - ax))
            base, px = row["base"], row["px"]
            asc = px * 1.0
            c.save()
            c.clipRect(skia.Rect(g.x0 - px * 0.3, base - asc, ctx.W + px, base + px * 0.32))
            y = base + (1 - e) * px * 1.05
            xx = g.x0
            sp = D.text_w(f, " ", row["tk"])
            words = row["text"].split()
            for w in words:
                ww = D.text_w(f, w, row["tk"])
                col = ctx.pal("accent") if _norm(w) in emph else ctx.pal("ink")
                if xx == g.x0 and w[0] in _HANG:
                    xx -= D.text_w(f, w[0], row["tk"])
                D.draw_text(c, w, xx, y, f, col, align="left", tracking=row["tk"])
                xx += ww + sp
            c.restore()
            # swash under emphasis words: drawn on the 8th after the landing
            xx = g.x0
            fw = _serif(ctx, px, w1, soft=0)
            sp1 = D.text_w(fw, " ", row["tk"])
            for w in words:
                ww = D.text_w(fw, w, row["tk"])
                if _norm(w) in emph:
                    su = E.out_quint(E.clamp((lt - t0 - spb * 0.5) / (spb * 0.5)))
                    if su > 0:
                        x0, x1 = xx + px * 0.02, xx + ww * 0.98
                        yy = base + px * 0.11
                        p = skia.Path()
                        p.moveTo(x0, yy + px * 0.03)
                        p.cubicTo(x0 + (x1 - x0) * 0.3, yy - px * 0.05, x0 + (x1 - x0) * 0.62, yy + px * 0.07, x1, yy - px * 0.02)
                        p.cubicTo(x1 - (x1 - x0) * 0.25, yy + px * 0.02, x1 - (x1 - x0) * 0.55, yy + px * 0.10,
                                  x1 - (x1 - x0) * 0.72, yy + px * 0.09)
                        c.drawPath(D.partial_path(p, su), D.paint(ctx.pal("accent2"), stroke=px * 0.055))
                xx += ww + sp1
        draw_chrome(ctx, self, self.p.get("label") or "hook")


# ---------------------------------------------------------------------- quote

@register("quote")
class Quote(SocialScene):
    """Pull-quote card revealed word by word on the 8th grid.

    A paper card (bleeding to half the margin) rises in out_expo over 1/2 beat with a 2 degree
    settle. Inside, the kicker sits on the shared anchor and the quote is set in Fraunces at the
    title role (126 px, down to 95 px for long quotes; light weight, optical size from the px
    size) flush left on x0 with the opening quote mark hanging into the margin. A large ghost
    quote mark sits top right, behind the text. Each word mask-rises from below its line over one
    `stagger` (default 1/2 beat, out_cubic), its weight and SOFT axis settling as it lands. On the
    beat after the last word the byline (speaker avatar, name, role) lands on the card's bottom
    edge with a hairline; from the next 8th the emphasis words get a highlighter fill, one per 8th,
    and the card pushes in (up to 7 %, accelerating) under the riser into the outgoing cut. Pull
    quotes read best at 45-70 characters (`measure_ok`).

    `card: false` sets the quote straight on the ground (legacy look). Legacy fields stay:
    `author`, `role`, `size` (ref px or role name), `stagger` (seconds or beats), `exit`."""
    defaults = {**SocialScene.defaults, "text": "", "author": "", "role": "", "size": "title", "stagger": "1/2b",
                "first": "1/2b", "kicker": "", "emphasis": [], "weight": 440, "max_lines": 5, "exit": False,
                "ticks": True, "riser": True, "label": "quote", "card": True, "meta": "", "speaker": None}

    def _words(self) -> list[str]:
        return str(self.p["text"]).split()

    @property
    def measure_ok(self) -> bool:
        n = len(str(self.p["text"]).strip().strip("“”\"'‘’"))
        return 45 <= n <= 70

    def onsets(self) -> list[float]:
        st = float(self.p["stagger"])
        first = float(self.p["first"])
        return [first + i * st for i in range(len(self._words()))]

    def _attr_at(self) -> float:
        ons = self.onsets()
        last = ons[-1] if ons else 0.0
        b = self._beat_of(last)
        return self.bt(math.floor(b + 1e-6) + 1.0)

    def _mark_times(self) -> dict[int, float]:
        """Box index -> scene time of each emphasis word's highlighter sweep (one per 8th after
        the byline's 8th)."""
        emph = {_norm(e) for e in self.p["emphasis"]}
        t = self._attr_at() + self.beat_len * 0.5
        out = {}
        for k, w in enumerate(self._words()):
            if _norm(w) in emph:
                out[k] = t
                t += self.beat_len * 0.5
        return out

    def _push_at(self) -> float:
        """Scene time the card starts its push-in: the 8th after the byline lands, with the mark-up."""
        return self._attr_at() + self.beat_len * 0.5

    def _exit_at(self) -> float:
        return self.dur - self.beat_len * 0.5

    def cues(self) -> list[Cue]:
        out = []
        spb = self.beat_len
        emph = {_norm(e) for e in self.p["emphasis"]}
        if self.p["ticks"]:
            for w, t in zip(self._words(), self.onsets()):
                if t > 1e-6 and t < self.dur and self._on_beat(t):
                    snd = "pop_soft" if _norm(w) in emph else "tick"
                    out.append(Cue("sfx", t, data={"sound": snd, "gain": -9.0 if snd == "tick" else -7.0,
                                                    "velocity": 0.5}))
        at = self._attr_at()
        if at < self.dur:
            out.append(Cue("sfx", at, data={"sound": "chime", "gain": -8.0}))
        if self.p["riser"] and self.dur >= spb * 4:
            out.append(Cue("sfx", self.dur, data={"sound": "riser_short", "gain": -9.0, "align": "end"}))
        return out

    def card_rect(self, ctx: Ctx) -> skia.Rect:
        g = _g(ctx)
        bleed = g.margin / 2
        return skia.Rect(bleed, g.anchor_y - g.bl(9), ctx.W - bleed, chrome_top(ctx) - g.bl(7))

    def layout(self, ctx: Ctx) -> tuple[skia.Font, float, list]:
        g = _g(ctx)
        fk, _ = kicker_font(ctx)
        top = g.anchor_y + D.cap_height(fk) + g.bl(8)
        if self.p["card"]:
            r = self.card_rect(ctx)
            max_w = r.right() - g.px(64) - g.x0
        else:
            max_w = g.content_w
        f, tk, boxes = set_words(ctx, self._words(), self.p["size"], max_w, int(self.p["max_lines"]),
                                 float(self.p["weight"]), top=top, soft=40.0)
        return f, tk, boxes

    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        g = _g(ctx)
        spb = self.beat_len
        card = bool(self.p["card"])
        self.backdrop(ctx)
        draw_header(ctx, self)
        ground = "paper" if card else "bg"
        ink = ctx.pal("ink_dark") if card else ctx.pal("ink")
        ink_key = "ink_dark" if card else "ink"

        # card: rises out_expo over 1/2 beat with a small rotation settle (a physical sheet)
        cu = E.out_expo(E.clamp((lt + ctx.frame) / (spb * 0.5)))
        rect = self.card_rect(ctx)
        c.save()
        # once the quote is read and signed, the card pushes in towards the outgoing cut
        # (accelerating under the riser): up to 7 % about the card centre
        p0 = self._push_at()
        if lt > p0 and self.dur > p0:
            pu = E.clamp((lt - p0) / (self.dur - p0))
            _scale_about(c, rect.centerX(), rect.centerY(), 1.0 + 0.07 * (0.45 * pu + 0.55 * pu * pu))
        if card:
            c.translate(0, (1 - cu) * g.bl(30))
            piv = (rect.centerX(), rect.bottom())
            c.translate(*piv)
            c.rotate(-2.0 * (1 - cu))
            c.translate(-piv[0], -piv[1])
            rad = g.px(36)
            sh = skia.Rect(rect.left(), rect.top() + g.bl(3), rect.right(), rect.bottom() + g.bl(4))
            c.drawRRect(skia.RRect.MakeRectXY(sh, rad, rad), D.paint(D.argb((0, 0, 0), 0.5), blur=g.px(28)))
            c.drawRRect(skia.RRect.MakeRectXY(rect, rad, rad), D.paint(ctx.pal("paper")))
            c.save()
            c.clipRRect(skia.RRect.MakeRectXY(rect, rad, rad), doAntiAlias=True)
            # ghost quote mark, top right behind the text (lands on beat 0)
            qf = _serif(ctx, g.px(560), 800, soft=100)
            qa = E.out_expo(E.clamp((lt + ctx.frame) / (spb * 0.75)))
            qx, qy = rect.right() - g.px(40), rect.top() + g.px(400)
            c.save()
            c.translate(qx, qy)
            c.rotate(8.0 * (1 - qa))
            c.scale(0.9 + 0.1 * qa, 0.9 + 0.1 * qa)
            D.draw_text(c, "”", 0, 0, qf, dim_color(ctx, "accent", 0.42, 1.0, ground="paper"), align="right")
            c.restore()
            c.restore()

        # kicker on the anchor (accent2 rule); on the card the meta goes to the byline row
        if self.p["kicker"] or self.p["meta"]:
            draw_kicker(ctx, self.p["kicker"], g.x0, g.anchor_y,
                        ctx.pal("accent2") if card else dim_color(ctx, "ink", 0.7), rule=1.0,
                        rule_color=ctx.pal("accent2"), right="" if card else self.p["meta"],
                        right_color=dim_color(ctx, ink_key, 0.55, ground=ground))

        f, tk, boxes = self.layout(ctx)
        cap = D.cap_height(f)
        size = f.getSize()
        ons = self.onsets()
        emph = {_norm(e) for e in self.p["emphasis"]}
        ex0 = self._exit_at()

        def out_u(li: int) -> float:
            if not self.p["exit"]:
                return 0.0
            return E.in_quart(E.clamp((lt - ex0 - li * spb / 16) / (spb * 0.375)))

        # highlighter fill behind emphasis words: the editor's mark-up once the quote has been read,
        # one emphasis word per 8th from the 8th after the byline, each sweeping left -> right
        marks = self._mark_times()
        for k, (w, x, base, ww, li) in enumerate(boxes):
            if k not in marks:
                continue
            hu = E.out_cubic(E.clamp((lt - marks[k]) / (spb * 0.5)))
            if hu <= 0:
                continue
            a = 1 - out_u(li)
            dy = -out_u(li) * g.bl(14)
            lx = x - size * 0.05
            rx = lx + (ww + size * 0.10) * hu
            c.drawRect(skia.Rect(lx, base - cap * 0.52 + dy, rx, base + size * 0.13 + dy),
                       D.paint(ctx.pal("accent", 0.92 * a)))
        # words: mask-rise from below their own line over one stagger (out_cubic), the axes
        # morphing from a light, soft cut to the set weight as they land
        w0, w1 = 300.0, float(self.p["weight"])
        for k, (w, x, base, ww, li) in enumerate(boxes):
            u = E.clamp((lt - ons[k] + ctx.frame * 0.5) / max(float(self.p["stagger"]), spb * 0.25))
            if u <= 0:
                continue
            e = E.out_cubic(u)
            ou = out_u(li)
            a = min(1.0, e * 3.0) * (1 - ou)
            if a <= 0.002:
                continue
            wt = round((w0 + (w1 - w0) * e) / 20.0) * 20.0
            fw = f if wt >= w1 else _serif(ctx, size, wt, soft=40.0 + 60.0 * (1 - e))
            dy = (1 - e) * size * 0.9 - ou * g.bl(14)
            col = D.argb(D.mix_rgb(ctx.pal.rgb(ground), ctx.pal.rgb(ink_key), a))
            c.save()
            c.clipRect(skia.Rect(x - size * 0.6, base - size * 1.0, x + ww + size * 0.6, base + size * 0.32))
            D.draw_text(c, w, x, base + dy, fw, col, align="left", tracking=tk)
            c.restore()

        # byline on the card's bottom edge: hairline, speaker avatar, name, role
        at = self._attr_at()
        au = E.out_expo(E.clamp((lt - at) / (spb * 0.5)))
        person = _person(ctx, self.p["speaker"] or ({"name": self.p["author"], "role": self.p["role"]}
                                                    if self.p["author"] else None))
        if au > 0 and person:
            if card:
                d = g.px(96)
                yb = rect.bottom() - g.px(48) - d
                c.drawRect(skia.Rect(g.x0, yb - g.bl(5) - g.px(1), g.x0 + (rect.right() - g.px(48) - g.x0) * au,
                                     yb - g.bl(5) + g.px(1)), D.paint(dim_color(ctx, "ink_dark", 0.25, ground="paper")))
                c.save()
                c.translate(0, (1 - au) * g.bl(4))
                cx, cy = g.x0 + d / 2, yb + d / 2
                c.drawCircle(cx, cy, d / 2 * (0.6 + 0.4 * au), D.paint(ctx.pal("accent2", au)))
                D.draw_text(c, person["initials"], cx, cy, _serif(ctx, g.px(38), 800), ctx.pal("paper", au),
                            valign="middle")
                cap_r, meta = L.TYPE["caption"], L.TYPE["meta"]
                fn, fm = cap_r.font(ctx, weight=700), meta.font(ctx, weight=500)
                tx = g.x0 + d + g.px(28)
                D.draw_text(c, f"— {person['name']}", tx, cy - g.px(6), fn, ctx.pal("ink_dark", au), align="left",
                            tracking=cap_r.tk(ctx))
                if person.get("role"):
                    D.draw_text(c, person["role"], tx, cy + D.cap_height(fm) + g.px(16), fm,
                                dim_color(ctx, "ink_dark", 0.6, au, ground="paper"), align="left",
                                tracking=meta.tk(ctx))
                if self.p["meta"]:
                    D.draw_text(c, str(self.p["meta"]).upper(), rect.right() - g.px(48), cy + D.cap_height(fm) / 2,
                                fm, dim_color(ctx, "ink_dark", 0.6, au, ground="paper"), align="right",
                                tracking=meta.tk(ctx))
                c.restore()
            else:
                block_bottom = (boxes[-1][2] + size * 0.25) if boxes else g.anchor_y
                draw_speaker(ctx, person, block_bottom + g.bl(8), au)
        c.restore()
        draw_chrome(ctx, self, self.p.get("label") or "quote")


# ---------------------------------------------------------------------- karaoke

@register("karaoke")
class Karaoke(SocialScene):
    """Spoken-caption karaoke on the 8th grid, one sentence per page.

    timing "beat" (default): word i lands `per` beats after the previous one (default 1/2 beat,
    the 8th grid); with `page_on` "bar" every sentence starts on the next bar line, so phrases
    breathe with the music. timing "even" spreads the words over the scene and quantises each
    onset to the 8th grid; explicit "words": [{"w": "word", "t": 1.25}, ...] (scene seconds or
    "2b") are used as given.

    Highlight: an accent pill is the "now" marker. On each word's onset its leading edge sweeps
    across the word (out_cubic over 85 % of the word's slot), filling it left to right (the
    text inside the pill turns dark), while its tail springs off the previous word (spring, about
    2 % overshoot). The active word scales 1.0 -> 1.08 (out_expo, 1/4 beat) and settles back
    when the next word lands. Spoken words keep the text colour; upcoming words are dimmed to
    45 % of it; spoken emphasis words turn accent and pulse on every `pulse` (default 1/2 beat).
    Pages enter out_expo and leave in_quart in the last 1/4 beat before the next page.

    Below the text: an audiogram strip that pulses on the 8ths and fills as the clip plays, and
    the speaker lower third (`speaker`, default the creator) that wipes in on the first 8th.
    `ticks` puts a soft tick on every word that lands on a beat (emphasis words get a pop);
    the bed's hats carry the off-beat 8ths.

    Legacy fields stay: `lines_per_page` (pages by lines instead of sentences, continuous
    timing), `size` (ref px or a role name), `lead`, `tail`, `kicker`, `emphasis`."""
    defaults = {**SocialScene.defaults, "text": "", "words": None, "timing": "beat", "per": 0.5,
                "lines_per_page": None, "size": "title", "lead": 0.0, "tail": 0.6, "emphasis": [], "kicker": "",
                "page_on": "bar", "max_lines": 3, "audiogram": True, "ticks": True, "label": "caption",
                "weight": 650, "riser": True, "speaker": None, "pulse": "1/2b", "gap": 0.0}
    beat_params = ("pulse",)
    GAP_EM = 0.30          # word space wide enough for a 1.08 x active word and its pill
    PAD = 0.09             # pill padding (em)

    # -- timing ---------------------------------------------------------------
    def _word_list(self) -> list[str]:
        if self.p["words"]:
            return [w["w"] if isinstance(w, dict) else str(w) for w in self.p["words"]]
        return str(self.p["text"]).split()

    def pages(self) -> list[list[int]]:
        """Word indices per page (a sentence each, or the whole text when `page_on` is "none"
        or legacy `lines_per_page` is set)."""
        words = self._word_list()
        if self.p.get("lines_per_page") or self.p.get("page_on") in (None, "none", False):
            return [list(range(len(words)))]
        out: list[list[int]] = []
        cur: list[int] = []
        for i, w in enumerate(words):
            cur.append(i)
            if w.rstrip("\"'”’").endswith((".", "!", "?")):
                out.append(cur)
                cur = []
        if cur:
            out.append(cur)
        return out or [[]]

    def timed(self) -> list[tuple[str, float]]:
        """(word, scene-local onset seconds) for every word."""
        words = self._word_list()
        if self.p["words"] and all(isinstance(w, dict) and "t" in w for w in self.p["words"]):
            return [(w["w"], float(w["t"])) for w in self.p["words"]]
        n = max(len(words), 1)
        lead_b = self._beat_of(float(self.p["lead"]))
        if self.p["timing"] == "even":
            span_b = max(self._beat_of(max(self.dur - float(self.p["tail"]), 0.5)) - lead_b, 0.5)
            out, last = [], -1.0
            for i, w in enumerate(words):
                b = round((lead_b + span_b * i / n) * 2) / 2
                if b <= last:
                    b = last + 0.5
                last = b
                out.append((w, self._at_beat(b)))
            return out
        per = float(self.p["per"]) or 0.5
        bpb = self.tl.beats_per_bar
        on_bar = self.p.get("page_on") == "bar" and not self.p.get("lines_per_page")
        b = lead_b
        out = []
        for pi, page in enumerate(self.pages()):
            if pi and on_bar:
                b = math.ceil((self._bt0() + b) / bpb - 1e-6) * bpb - self._bt0()
            for i in page:
                out.append((words[i], self._at_beat(b)))
                b += per
        return out

    def page_spans(self) -> list[tuple[float, float]]:
        """(start, end) scene seconds of each page (start = first word, end = next page)."""
        tm = self.timed()
        pages = self.pages()
        starts = [tm[p[0]][1] if p else 0.0 for p in pages]
        starts[0] = min(starts[0], 0.0)
        ends = starts[1:] + [self.dur]
        return list(zip(starts, ends))

    def cues(self) -> list[Cue]:
        out = []
        emph = {_norm(e) for e in self.p["emphasis"]}
        for w, t in self.timed():
            out.append(Cue("cue", t, data={"word": w}))
            if self.p["ticks"] and t <= self.dur - 1e-6 and self._on_beat(t):
                if _norm(w) in emph:
                    out.append(Cue("sfx", t, data={"sound": "pop_soft", "gain": -6.0, "velocity": 0.6}))
                else:
                    out.append(Cue("sfx", t, data={"sound": "tick", "gain": -9.0, "velocity": 0.5}))
        if self.p["riser"] and self.dur >= self.beat_len * 4:
            out.append(Cue("sfx", self.dur, data={"sound": "riser_short", "gain": -9.0, "align": "end"}))
        return out

    # -- layout -----------------------------------------------------------------
    def _block_top(self, ctx: Ctx) -> float:
        g = _g(ctx)
        fk, _ = kicker_font(ctx)
        return g.anchor_y + D.cap_height(fk) + g.bl(8)

    def _page_layout(self, ctx: Ctx, page: list[int], words: list[str]) -> tuple[skia.Font, float, list]:
        g = _g(ctx)
        gap = float(self.p["gap"]) or self.GAP_EM
        return set_words(ctx, [words[i] for i in page], self.p["size"], g.content_w, int(self.p["max_lines"]),
                         float(self.p["weight"]), gap_em=gap, top=self._block_top(ctx))

    def _zones(self, ctx: Ctx, pages: list[list[int]], words: list[str]) -> tuple[float, float, float]:
        """(audiogram top, audiogram bottom, speaker top): fixed for the whole scene, below the
        deepest page."""
        g = _g(ctx)
        deepest = self._block_top(ctx)
        for page in pages:
            f, _tk, boxes = self._page_layout(ctx, page, words)
            if boxes:
                deepest = max(deepest, boxes[-1][2] + f.getSize() * 0.25)
        sp_top = chrome_top(ctx) - g.bl(7) - g.px(128)
        a0 = max(deepest + g.bl(8), g.anchor_y + g.safe_h * 0.40)
        a1 = sp_top - g.bl(7)
        return a0, a1, sp_top

    # -- draw -------------------------------------------------------------------
    def draw(self, ctx: Ctx) -> None:
        # word logic runs half a frame ahead so the frame that follows an onset always shows
        # the pill on the new word (the sound lands inside that frame's shutter)
        c, lt = ctx.c, ctx.lt + ctx.frame * 0.5
        g = _g(ctx)
        self.backdrop(ctx)
        words = self._word_list()
        timed = self.timed()
        if not words:
            return
        pages = self.pages()
        if self.p.get("lines_per_page"):
            pages = self._line_pages(ctx, words, int(self.p["lines_per_page"]))
        starts = [timed[p[0]][1] if p else 0.0 for p in pages]
        pi = max([k for k, s in enumerate(starts) if s <= lt + 1e-9] or [0])
        page = pages[pi]
        p_start = 0.0 if pi == 0 else starts[pi]
        p_end = starts[pi + 1] if pi + 1 < len(pages) else self.dur + 10.0
        active = max((i for i, (_, t) in enumerate(timed) if t <= lt + 1e-9), default=-1)
        spb = self.beat_len
        emph = {_norm(e) for e in self.p["emphasis"]}

        draw_header(ctx, self)
        kin = E.out_expo(E.clamp((lt + (spb if pi == 0 else 0)) / (spb * 0.5)))
        draw_kicker(ctx, self.p["kicker"] or "", g.x0, g.anchor_y, ctx.pal("accent", kin), rule=kin,
                    right=f"{pi + 1}/{len(pages)}" if len(pages) > 1 else "",
                    right_color=dim_color(ctx, "ink", 0.7, kin))

        f, tk, boxes = self._page_layout(ctx, page, words)
        cap = D.cap_height(f)
        size = f.getSize()
        enter_d, exit_d = spb * 0.25, spb * 0.25
        nlines = (boxes[-1][4] + 1) if boxes else 1

        def line_shift(li: int) -> tuple[float, float]:
            u_in = 1.0 if pi == 0 else E.out_expo(E.clamp((lt - p_start - li * spb / 16) / enter_d))
            u_out = E.in_quart(E.clamp((lt - (p_end - exit_d) - (nlines - 1 - li) * spb / 32) / exit_d)) \
                if pi + 1 < len(pages) else 0.0
            # a page turn is a cut on the bar line: the lines rise in, but are opaque within a frame
            return (1 - u_in) * g.bl(9) - u_out * g.bl(12), min(1.0, u_in * 4.0) * (1 - u_out)

        box_of = {page[k]: boxes[k] for k in range(min(len(page), len(boxes)))}
        pad = size * self.PAD
        pt, pb = size * 0.20, size * 0.26
        rise = spb * 0.25
        per_s = max((timed[active + 1][1] - timed[active][1]) if 0 <= active < len(timed) - 1 else spb * 0.5,
                    ctx.frame)
        per_s = min(per_s, spb * 0.5)

        def scale_of(i: int) -> float:
            k = 1.0
            if i == active:
                k += 0.08 * E.out_expo(E.clamp((lt - timed[i][1]) / rise))
            elif i == active - 1 and active < len(timed):
                k += 0.08 * (1 - E.out_expo(E.clamp((lt - timed[active][1]) / rise)))
            if i < active and _norm(timed[i][0]) in emph:
                k += 0.05 * _beat_pulse(ctx, float(self.p["pulse"]) or 0.5, 0.35)
            return k

        def wrect(i: int) -> skia.Rect:
            w, x, base, ww, li = box_of[i]
            dy, _a = line_shift(li)
            k = scale_of(i)
            cxw = x + ww / 2
            return skia.Rect(cxw - ww * k / 2 - pad, base - cap * k - pt + dy - (k - 1) * cap * 0.0,
                             cxw + ww * k / 2 + pad, base + pb + dy)

        # -- pill: tail springs off the previous word, head sweeps across the active word
        pill = None
        if active in box_of:
            u = max(lt - timed[active][1], 0.0)
            tgt = wrect(active)
            head = tgt.left() + (tgt.right() - tgt.left()) * (0.18 + 0.82 * E.out_cubic(E.clamp(u / (per_s * 0.85))))
            tail = tgt.left()
            if active - 1 in box_of and box_of[active - 1][4] == box_of[active][4]:
                src = wrect(active - 1)
                k = E.Spring.settling(spb * 0.25, PILL_BOUNCE).value(u)
                tail = src.left() + (tgt.left() - src.left()) * k
            head = max(head, tail + tgt.height() * 0.5)
            pill = skia.Rect(min(tail, head - tgt.height() * 0.5), tgt.top(), head, tgt.bottom())
            _dy, a_line = line_shift(box_of[active][4])
            is_e = _norm(timed[active][0]) in emph
            col = ctx.pal("accent2" if is_e else "accent", a_line)
            rad = min(pill.height() * 0.3, g.px(26))
            c.drawRRect(skia.RRect.MakeRectXY(pill, rad, rad), D.paint(col))
            ctx.publish("pill", tgt, word=timed[active][0])

        def draw_word(i: int, color_fn) -> None:
            w, x, base, ww, li = box_of[i]
            dy, a = line_shift(li)
            if a <= 0.002:
                return
            k = scale_of(i)
            c.save()
            _scale_about(c, x + ww / 2, base - cap / 2 + dy, k)
            D.draw_text(c, w, x, base + dy, f, color_fn(i, a), align="left", tracking=tk)
            c.restore()

        def base_color(i: int, a: float) -> int:
            if i < active:
                return ctx.pal("accent" if _norm(timed[i][0]) in emph else "ink", a)
            if i == active:  # the part not yet swept stays dimmed until the pill fills it
                return dim_color(ctx, "ink", DIM, a)
            return dim_color(ctx, "ink", DIM, a)

        for i in page:
            if i in box_of:
                draw_word(i, base_color)
        if pill is not None:
            rad = min(pill.height() * 0.3, g.px(26))
            c.save()
            c.clipRRect(skia.RRect.MakeRectXY(pill, rad, rad), doAntiAlias=True)
            for i in (active - 1, active):
                if i in box_of:
                    draw_word(i, lambda _i, a: ctx.pal("ink_dark", a))
            c.restore()

        a0, a1, sp_top = self._zones(ctx, pages, words)
        if self.p["audiogram"]:
            self._audiogram(ctx, a0, a1)
        su = E.out_expo(E.clamp((lt - spb * 0.5) / (spb * 0.5)))
        draw_speaker(ctx, _person(ctx, self.p["speaker"]), sp_top, su, speaking=1.0)
        draw_chrome(ctx, self, self.p.get("label") or "caption")

    def _line_pages(self, ctx: Ctx, words: list[str], lpp: int) -> list[list[int]]:
        f, lines, tk = text_font(ctx, words, self.p["size"], 99, self.p["weight"])
        out, idx = [], 0
        for k in range(0, len(lines), max(lpp, 1)):
            n = sum(len(ln.split()) for ln in lines[k:k + lpp])
            out.append(list(range(idx, idx + n)))
            idx += n
        return out or [list(range(len(words)))]

    def _audiogram(self, ctx: Ctx, y0: float, y1: float) -> None:
        """Waveform scrubber: bars pulse on the 8ths (beat-locked envelope x per-bar noise); the
        part already played is in the accent, the rest dimmed."""
        g = _g(ctx)
        if y1 - y0 < g.bl(6):
            return
        c = ctx.c
        n = 44
        gap = g.px(9)
        bw = (g.content_w - gap * (n - 1)) / n
        mid = (y0 + y1) / 2
        hmax = (y1 - y0) / 2
        t = _film_t(ctx)
        beat = ctx.tl.time_to_beat(t)
        e8 = (1.0 - E.clamp((beat * 2 - math.floor(beat * 2)) / 0.95)) ** 1.5
        e1 = (1.0 - E.clamp((beat - math.floor(beat)) / 0.95)) ** 1.5
        prog = E.clamp(ctx.lt / max(self.dur, 1e-6))
        played = dim_color(ctx, "accent", 1.0)
        rest = dim_color(ctx, "accent", 0.32)
        with ctx.layer(1.15):
            for k in range(n):
                u = k / (n - 1)
                shape = 0.35 + 0.65 * math.sin(math.pi * (0.08 + 0.84 * u)) ** 0.7
                nz = 0.5 + 0.5 * E.noise1(k * 1.7 + math.floor(beat * 2) * 3.1, seed=11)
                amp = shape * (0.18 + 0.52 * nz * e8 + 0.30 * e1 * (0.5 + 0.5 * math.cos(u * 9.0 + beat)))
                hh = max(bw * 0.5, hmax * min(1.0, amp))
                x = g.x0 + k * (bw + gap)
                D.rrect(c, x, mid - hh, bw, 2 * hh, bw / 2, D.paint(played if u <= prog else rest))


# ---------------------------------------------------------------------- follow

@register("follow")
class Follow(SocialScene):
    """Call-to-follow end card. The pill of the previous karaoke scene is handed over on the
    cut (match on "pill") and springs out into a full-width follow button; the headline rises
    in line by line on the 8ths; the button is pressed on `tap_at` (default beat 2) with a
    click + chime, its label morphs to `done`, and it kicks on every beat after (spring).
    `stats` ([{"value": "312", "label": "clips"}, ...]) sit in a row above the chrome, rising
    on the 8ths after the press; numeric values count up (out_quint over 1 beat)."""
    defaults = {**SocialScene.defaults, "kicker": "Follow for more", "text": "One idea a day, set to the beat.",
                "button": "Follow", "done": "Following", "tap_at": "2b", "note": "", "label": "follow",
                "weight": 650, "stats": []}

    def cues(self) -> list[Cue]:
        tap = float(self.p["tap_at"])
        out = [Cue("sfx", 0.0, data={"sound": "pop", "gain": -6.0})]
        if tap < self.dur:
            out += [Cue("sfx", tap, data={"sound": "ui_click", "gain": -4.0}),
                    Cue("sfx", tap, data={"sound": "chime", "gain": -8.0}),
                    Cue("hit", tap, data={"strength": 0.4, "flash": 0.0})]
        return out

    def button_rect(self, ctx: Ctx, top: float) -> skia.Rect:
        g = _g(ctx)
        return skia.Rect(g.x0, top, g.x1, top + g.px(152))

    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        g = _g(ctx)
        spb = self.beat_len
        cr = _creator(ctx)
        self.backdrop(ctx)
        draw_header(ctx, self)
        draw_kicker(ctx, self.p["kicker"], g.x0, g.anchor_y, ctx.pal("accent"), rule=1.0)
        fk, _ = kicker_font(ctx)
        top = g.anchor_y + D.cap_height(fk) + g.bl(8)
        f, tk, boxes = set_words(ctx, str(self.p["text"]).split(), "title", g.content_w, 3,
                                 float(self.p["weight"]), top=top)
        cap = D.cap_height(f)
        for w, x, base, ww, li in boxes:
            u = E.out_expo(E.clamp((lt - li * spb * 0.5 + ctx.frame * 0.5) / (spb * 0.25)))
            if u <= 0:
                continue
            D.draw_text(c, w, x, base + (1 - u) * g.bl(7), f, ctx.pal("ink", u), align="left", tracking=tk)
        block_bottom = boxes[-1][2] + f.getSize() * 0.25 if boxes else top + cap
        # handle row
        hy = block_bottom + g.bl(6)
        sub = L.TYPE["subhead"]
        fh = sub.font(ctx, weight=600)
        hu = E.out_expo(E.clamp((lt - spb) / (spb * 0.25)))
        if cr.get("handle") and hu > 0:
            D.draw_text(c, cr["handle"], g.x0, hy + D.cap_height(fh) + (1 - hu) * g.bl(4), fh,
                        dim_color(ctx, "ink", 0.8, hu), align="left", tracking=sub.tk(ctx))
        # the button: handed off from the karaoke pill, springs to full width
        btop = hy + D.cap_height(fh) + g.bl(8)
        tgt = self.button_rect(ctx, btop)
        sp = E.Spring.settling(1.0, PILL_BOUNCE)
        if ctx.handed_off("pill"):
            r = ctx.handoff("pill", tgt, dur="1/2b", ease=sp.ease())
        else:
            k = E.Spring.settling(spb * 0.5, PILL_BOUNCE).value(max(lt, 0.0))
            cx, cy = tgt.centerX(), tgt.centerY()
            r = skia.Rect(cx - tgt.width() / 2 * k, cy - tgt.height() / 2 * k, cx + tgt.width() / 2 * k,
                          cy + tgt.height() / 2 * k)
        tap = float(self.p["tap_at"])
        pressed = lt >= tap
        # beat kick after the press (spring 1 frame after the beat), press dip on the tap
        if pressed:
            since = lt - tap
            dip = 1.0 - 0.05 * (1 - E.Spring.settling(spb * 0.6, 0.35).value(since))
            kick = 0.0
            b = ctx.local_beat
            if b >= self.tl.beats_of(tap, self.start) + 1 - 1e-6:
                fb = (b - math.floor(b)) * spb
                kick = 0.025 * max(0.0, 1 - E.Spring.settling(spb * 0.7, 0.3).value(max(fb - ctx.frame, 0.0)))
            k = dip + kick
            cx, cy = r.centerX(), r.centerY()
            r = skia.Rect(cx - r.width() / 2 * k, cy - r.height() / 2 * k, cx + r.width() / 2 * k,
                          cy + r.height() / 2 * k)
        col = ctx.pal("ink") if pressed else ctx.pal("accent")
        D.rrect(c, r.left(), r.top(), r.width(), r.height(), r.height() / 2, D.paint(col))
        if pressed:  # ripple from the tap point
            ru = E.out_quint(E.clamp((lt - tap) / spb))
            c.save()
            c.clipRRect(skia.RRect.MakeRectXY(r, r.height() / 2, r.height() / 2), doAntiAlias=True)
            c.drawCircle(r.left() + r.width() * 0.72, r.centerY(), r.width() * 0.9 * ru,
                         D.paint(ctx.pal("accent", 0.35 * (1 - ru))))
            c.restore()
        la = E.clamp((r.width() - tgt.width() * 0.88) / (tgt.width() * 0.1))
        if la > 0:
            body = L.TYPE["body"]
            fb_ = _serif(ctx, g.px(64), 700)
            label = (self.p["done"] if pressed else self.p["button"])
            if cr.get("handle") and not pressed:
                label = f"{label}  {cr['handle']}"
            lw = D.text_w(fb_, label, body.tk(ctx))
            x = r.centerX() - lw / 2
            if pressed:
                x += g.px(28)
                ck = r.centerX() - lw / 2 - g.px(28)
                D.icon(c, "check", ck, r.centerY(), g.px(44), ctx.pal("ink_dark", la))
            D.draw_text(c, label, x, r.centerY(), fb_, ctx.pal("ink_dark", la), align="left", valign="middle",
                        tracking=body.tk(ctx))
        y = tgt.bottom() + g.bl(6)
        if self.p["note"]:
            nu = E.out_expo(E.clamp((lt - tap - spb * 0.5) / (spb * 0.25)))
            if nu > 0:
                draw_kicker(ctx, self.p["note"], g.x0, y, dim_color(ctx, "ink", 0.7, nu))
            y += g.bl(8)
        self._stats(ctx, tap, y)
        draw_chrome(ctx, self, self.p.get("label") or "follow")

    def _stats(self, ctx: Ctx, tap: float, y_min: float) -> None:
        stats = [s for s in (self.p["stats"] or []) if isinstance(s, dict)]
        if not stats:
            return
        g = _g(ctx)
        c, lt, spb = ctx.c, ctx.lt, self.beat_len
        n = len(stats)
        colw = (g.content_w - g.gutter * (n - 1)) / n
        vr, lr = L.TYPE["subhead"], L.TYPE["meta"]
        fv = _serif(ctx, L.TYPE["headline"].px(g) * 0.62, 700)
        fl = lr.font(ctx, weight=600)
        bottom = chrome_top(ctx) - g.bl(7)
        top = max(y_min, bottom - D.cap_height(fv) - g.bl(4) - D.cap_height(fl) - g.bl(3))
        c.drawRect(skia.Rect(g.x0, top - g.bl(4), g.x1, top - g.bl(4) + g.px(2)), D.paint(dim_color(ctx, "ink", 0.22)))
        for k, s in enumerate(stats):
            t0 = tap + spb * 0.5 * k
            u = E.out_expo(E.clamp((lt - t0) / (spb * 0.25)))
            x = g.x0 + k * (colw + g.gutter)
            val = str(s.get("value", ""))
            m = re.fullmatch(r"(\D*)(\d+)(\D*)", val)
            if m and u > 0:
                cu = E.out_quint(E.clamp((lt - t0) / spb))
                val = f"{m.group(1)}{int(round(int(m.group(2)) * cu))}{m.group(3)}"
            a = 0.35 + 0.65 * u
            yb = top + D.cap_height(fv) + (1 - u) * g.bl(4)
            D.draw_text(c, val, x, yb, fv, ctx.pal("accent" if u > 0 else "ink", a) if u > 0 else
                        dim_color(ctx, "ink", 0.35), align="left", tracking=vr.tk(ctx, "display"))
            D.draw_text(c, str(s.get("label", "")).upper(), x, yb + g.bl(3) + D.cap_height(fl), fl,
                        dim_color(ctx, "ink", 0.7, a), align="left", tracking=lr.tk(ctx))
