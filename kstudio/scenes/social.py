"""Social clip scenes: karaoke captions, pull-quotes and a call-to-follow end card.

All three share one editorial frame on the S6 grid (kstudio.layout), so cuts between them land
on the same lines:
- a creator header (avatar, name, handle / series, scene index) on the top safe line;
- a kicker (32 px caps, +12 %) whose cap top sits on the shared anchor (y = 560 on a reel);
- the text block: Fraunces at the title role (126 px, fitted down to 95 px only if needed),
  flush left on x0, wrapped with balanced rag, block centre at or above 45 % of the height;
- bottom chrome on the safe line: story-style progress segments (one per scene), the scene
  label and a 4-dot bar counter that lights the current beat.

Timing is in beats. Word onsets sit on the 8th grid by default (karaoke `per` = 1/2 beat),
page turns land on bar lines, entries are out_expo / out_quint, exits in_quart / in_expo; the
only spring is the karaoke / follow pill (a physical object). Dimmed (not yet spoken) words use
45 % of the text colour mixed into the ground, so they stay warm instead of turning grey.

Creator data comes from the spec's top-level "creator" object
({"name", "handle", "series", "initials"}); without it the header falls back to the spec
"handle" (and is hidden when neither is set).
"""
from __future__ import annotations

import math
import re
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


# ---------------------------------------------------------------------- shared helpers

def _g(ctx: Ctx) -> L.Grid:
    return L.grid(ctx)


def _rgb_mix(ctx: Ctx, a: str, b: str, k: float) -> tuple[int, int, int]:
    return D.mix_rgb(ctx.pal.rgb(a), ctx.pal.rgb(b), k)


def dim_color(ctx: Ctx, key: str = "ink", k: float = DIM, alpha: float = 1.0) -> int:
    """`key` at `k` of its strength over the ground (an opaque warm tone, not a grey alpha)."""
    return D.argb(_rgb_mix(ctx, "bg", key, k), alpha)


def _serif(ctx: Ctx, px: float, weight: float = 650, soft: float = SOFT, wonk: float = WONK) -> skia.Font:
    """Display face (Fraunces in the warm theme) at frame px, with opsz/SOFT/WONK set."""
    g = _g(ctx)
    fam = ctx.theme.get("display", "Fraunces")
    kw: dict[str, Any] = {"soft": soft, "wonk": wonk}
    oz = F.opsz_for(fam, px / max(g.u, 1e-6))
    if oz is not None:
        kw["opsz"] = oz
    return F.font(fam, px, int(round(weight / 10.0) * 10), 100.0, **kw)


def _norm(w: str) -> str:
    return w.lower().strip(_PUNCT)


def _sentences(text: str) -> list[list[str]]:
    """Split running text into sentences (pages) of words."""
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
    return [p.split() for p in parts] or [[]]


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
    return 1.0 - E.out_expo(f / max(decay, 1e-3))


def kicker_font(ctx: Ctx) -> tuple[skia.Font, float]:
    r = L.TYPE["kicker"]
    return r.font(ctx, weight=700), r.tk(ctx)


def draw_kicker(ctx: Ctx, text: str, x: float, top: float, color: int, rule: float = 0.0,
                right: str = "", right_color: int | None = None) -> float:
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
                       D.paint(ctx.pal("accent")))
        x0 = x + rw + g.px(20)
    D.draw_text(ctx.c, text.upper(), x0, base, f, color, align="left", tracking=tk)
    if right:
        D.draw_text(ctx.c, right.upper(), g.x1, base, f, right_color if right_color is not None else color,
                    align="right", tracking=tk)
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


def text_font(ctx: Ctx, words: list[str], size: Any, max_lines: int, weight: float = 650,
              min_ref: float = 95.0) -> tuple[skia.Font, list[str], float]:
    """Title-role Fraunces (126 px; or `size` ref px / role name) wrapped to the full content
    width with balanced rag; shrinks towards `min_ref` px only if the text needs more than
    `max_lines` lines. Returns (font, lines, tracking em)."""
    g = _g(ctx)
    role = L.TYPE["title"]
    if isinstance(size, str) and size in L.TYPE:
        role = L.TYPE[size]
    elif isinstance(size, (int, float)) and not isinstance(size, bool):
        role = L.TYPE.role(float(size)).with_(leading=L.TYPE["title"].leading)
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


# ---------------------------------------------------------------------- karaoke

@register("karaoke")
class Karaoke(Scene):
    """Spoken-caption karaoke on the 8th grid, one sentence per page.

    timing "beat" (default): word i lands `per` beats after the previous one (default 1/2 beat,
    the 8th grid); with `page_on` "bar" every sentence starts on the next bar line, so phrases
    breathe with the music. timing "even" spreads the words over the scene and quantises each
    onset to the 8th grid; explicit "words": [{"w": "word", "t": 1.25}, ...] (scene seconds or
    "2b") are used as given.

    Look: Fraunces at the title role (126 px) flush left from the anchor; spoken words in the
    text colour, upcoming words dimmed to 45 % of it, emphasis words in the accent once spoken.
    The active word scales 1.0 -> 1.08 (out_expo, 1/4 beat) on an accent pill that springs from
    the previous word to the new one on the hit. Pages enter out_expo and leave in_quart in the
    last 1/4 beat before the next page. An audiogram strip under the block pulses on the 8ths
    and fills as the clip plays. `ticks` puts a soft tick on every word that lands on a beat
    (emphasis words get a pop); the bed's hats carry the off-beat 8ths.

    Legacy fields stay: `lines_per_page` (pages by lines instead of sentences, continuous
    timing), `size` (ref px or a role name), `lead`, `tail`, `kicker`, `emphasis`."""
    defaults = {"text": "", "words": None, "timing": "beat", "per": 0.5, "lines_per_page": None,
                "size": "title", "lead": 0.0, "tail": 0.6, "emphasis": [], "kicker": "", "page_on": "bar",
                "max_lines": 4, "audiogram": True, "ticks": True, "label": "caption", "weight": 650,
                "riser": True}

    # -- timing ---------------------------------------------------------------
    def _bt0(self) -> float:
        return self.tl.time_to_beat(self.start)

    def _at_beat(self, b: float) -> float:
        return self.tl.beat_to_time(self._bt0() + b) - self.start

    def _beat_of(self, t: float) -> float:
        return self.tl.time_to_beat(self.start + t) - self._bt0()

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
        spb = self.beat_len
        for w, t in self.timed():
            out.append(Cue("cue", t, data={"word": w}))
            if self.p["ticks"] and t <= self.dur - 1e-6:
                b = self._beat_of(t)
                if abs(b - round(b)) * spb < 0.002:
                    if _norm(w) in emph:
                        out.append(Cue("sfx", t, data={"sound": "pop_soft", "gain": -6.0, "velocity": 0.6}))
                    else:
                        out.append(Cue("sfx", t, data={"sound": "tick", "gain": -9.0, "velocity": 0.5}))
        if self.p["riser"] and self.dur >= self.beat_len * 4:
            out.append(Cue("sfx", self.dur, data={"sound": "riser_short", "gain": -9.0, "align": "end"}))
        return out

    # -- layout -----------------------------------------------------------------
    def _page_layout(self, ctx: Ctx, page: list[int], words: list[str]) -> tuple[skia.Font, list, float]:
        g = _g(ctx)
        ptxt = [words[i] for i in page]
        f, lines, tk = text_font(ctx, ptxt, self.p["size"], int(self.p["max_lines"]), self.p["weight"])
        lpp = self.p.get("lines_per_page")
        if lpp:
            lines = lines  # legacy paging by lines happens in draw()
        top = self._block_top(ctx)
        boxes = word_boxes(f, lines, g.x0, top, L.TYPE["title"].leading, tk)
        return f, boxes, tk

    def _block_top(self, ctx: Ctx) -> float:
        g = _g(ctx)
        fk, _ = kicker_font(ctx)
        return g.anchor_y + D.cap_height(fk) + g.bl(7)

    # -- draw -------------------------------------------------------------------
    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        g = _g(ctx)
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
        kick_top = g.anchor_y
        kin = E.out_expo(E.clamp((lt + (spb if pi == 0 else 0)) / (spb * 0.5)))
        kicker = self.p["kicker"] or ""
        draw_kicker(ctx, kicker, g.x0, kick_top, ctx.pal("accent", kin), rule=kin,
                    right=f"{pi + 1}/{len(pages)}" if len(pages) > 1 else "",
                    right_color=dim_color(ctx, "ink", 0.7, kin))

        f, boxes, tk = self._page_layout(ctx, page, words)
        cap = D.cap_height(f)
        size = f.getSize()
        # page entry (out_expo, lines staggered 1/16 b) and exit (in_quart, last 1/4 b)
        enter_d, exit_d = spb * 0.25, spb * 0.25
        nlines = (boxes[-1][4] + 1) if boxes else 1

        def line_shift(li: int) -> tuple[float, float]:
            if pi == 0:
                u_in = 1.0
            else:
                u_in = E.out_expo(E.clamp((lt - p_start - li * spb / 16) / enter_d))
            u_out = E.in_quart(E.clamp((lt - (p_end - exit_d) - (nlines - 1 - li) * spb / 32) / exit_d)) \
                if pi + 1 < len(pages) else 0.0
            dy = (1 - u_in) * g.bl(9) - u_out * g.bl(12)
            return dy, u_in * (1 - u_out)

        # pill: springs from the previous word's box to the active one on the hit
        page_set = set(page)
        act_box = None
        prev_box = None
        box_of = {page[k]: boxes[k] for k in range(len(boxes))}
        if active in page_set:
            act_box = box_of[active]
            if active - 1 in page_set:
                prev_box = box_of[active - 1]
        pad_x, pad_t, pad_b = size * 0.16, size * 0.20, size * 0.26

        def rect_of(bx: tuple, k: float = 1.0) -> skia.Rect:
            _, x, base, w, li = bx
            dy, _a = line_shift(li)
            cxw = x + w / 2
            ww = w * k
            return skia.Rect(cxw - ww / 2 - pad_x, base - cap - pad_t + dy, cxw + ww / 2 + pad_x, base + pad_b + dy)

        word_scale = 1.0
        if act_box is not None:
            t0 = timed[active][1]
            u = lt - t0
            word_scale = 1.0 + 0.08 * E.out_expo(E.clamp(u / (spb * 0.25)))
            tgt = rect_of(act_box, word_scale)
            sp = E.Spring.settling(spb * 0.5, 0.28)
            k = sp.value(max(u, 0.0))
            if prev_box is not None:
                src = rect_of(prev_box, 1.08)
            else:
                cxr, cyr = tgt.centerX(), tgt.centerY()
                src = skia.Rect(cxr - tgt.width() * 0.3, cyr - tgt.height() * 0.3, cxr + tgt.width() * 0.3,
                                cyr + tgt.height() * 0.3)
            r = skia.Rect(E.lerp(src.left(), tgt.left(), k), E.lerp(src.top(), tgt.top(), k),
                          E.lerp(src.right(), tgt.right(), k), E.lerp(src.bottom(), tgt.bottom(), k))
            _dy, a_line = line_shift(act_box[4])
            is_e = _norm(timed[active][0]) in emph
            col = ctx.pal("accent2" if is_e else "accent", a_line if pi else 1.0)
            D.rrect(c, r.left(), r.top(), r.width(), r.height(), min(r.height() * 0.3, g.px(26)), D.paint(col))
            ctx.publish("pill", r, word=timed[active][0])

        for k, i in enumerate(page):
            if k >= len(boxes):
                break
            w, x, base, ww, li = boxes[k]
            dy, a = line_shift(li)
            if a <= 0.002:
                continue
            is_e = _norm(w) in emph
            if i == active:
                cxw = x + ww / 2
                c.save()
                c.translate(cxw, base - cap / 2 + dy)
                c.scale(word_scale, word_scale)
                D.draw_text(c, w, -ww / 2, cap / 2, f, ctx.pal("ink_dark", a), align="left", tracking=tk)
                c.restore()
                continue
            if i < active:
                col = ctx.pal("accent" if is_e else "ink", a)
            else:
                col = dim_color(ctx, "ink", DIM, a)
            D.draw_text(c, w, x, base + dy, f, col, align="left", tracking=tk)

        block_bottom = (boxes[-1][2] + size * 0.25) if boxes else g.anchor_y
        if self.p["audiogram"]:
            self._audiogram(ctx, max(block_bottom, g.anchor_y + g.safe_h * 0.42) + g.bl(8), chrome_top(ctx) - g.bl(6))
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
        e8 = 1.0 - E.out_quint(E.clamp((beat * 2 - math.floor(beat * 2)) / 0.9))
        e1 = 1.0 - E.out_quint(E.clamp((beat - math.floor(beat)) / 0.9))
        prog = E.clamp(ctx.lt / max(self.dur, 1e-6))
        played = dim_color(ctx, "accent", 1.0)
        rest = dim_color(ctx, "accent", 0.32)
        for k in range(n):
            u = k / (n - 1)
            shape = 0.35 + 0.65 * math.sin(math.pi * (0.08 + 0.84 * u)) ** 0.7
            nz = 0.5 + 0.5 * E.noise1(k * 1.7 + math.floor(beat * 2) * 3.1, seed=11)
            amp = shape * (0.18 + 0.52 * nz * e8 + 0.30 * e1 * (0.5 + 0.5 * math.cos(u * 9.0 + beat)))
            hh = max(bw * 0.5, hmax * min(1.0, amp))
            x = g.x0 + k * (bw + gap)
            D.rrect(c, x, mid - hh, bw, 2 * hh, bw / 2, D.paint(played if u <= prog else rest))


# ---------------------------------------------------------------------- quote

@register("quote")
class Quote(Scene):
    """Pull-quote revealed word by word on the 8th grid.

    A big accent quote mark lands on beat 0 above the kicker; the quote is set in Fraunces at
    the title role (126 px, down to 95 px for long quotes) flush left across the full content
    width; each word rises in (out_expo over 1/4 beat) one `stagger` after the previous
    (default 1/2 beat). Emphasis words turn accent and get a highlighter stroke drawn after
    the last word, then the attribution lands in the kicker role with a rule. In the last
    1/2 beat the lines leave upwards (in_quart, staggered) so the hard cut lands on a clean
    downbeat; a riser SFX ends on the cut.

    Legacy fields stay: `author`, `role`, `size` (ref px or role name), `stagger` (seconds or
    beats)."""
    defaults = {"text": "", "author": "", "role": "", "size": "title", "stagger": "1/2b", "first": "0b",
                "kicker": "", "emphasis": [], "weight": 600, "max_lines": 5, "exit": True, "ticks": True,
                "riser": True, "label": "quote"}

    def _words(self) -> list[str]:
        return str(self.p["text"]).split()

    def onsets(self) -> list[float]:
        st = float(self.p["stagger"])
        first = float(self.p["first"])
        return [first + i * st for i in range(len(self._words()))]

    def _attr_at(self) -> float:
        ons = self.onsets()
        last = ons[-1] if ons else 0.0
        b = self.tl.time_to_beat(self.start + last) - self.tl.time_to_beat(self.start)
        return self.bt(math.floor(b + 1e-6) + 1.0)

    def _exit_at(self) -> float:
        return self.dur - self.beat_len * 0.5

    def cues(self) -> list[Cue]:
        out = [Cue("sfx", 0.0, data={"sound": "swish", "gain": -6.0})]
        spb = self.beat_len
        b0 = self.tl.time_to_beat(self.start)
        emph = {_norm(e) for e in self.p["emphasis"]}
        if self.p["ticks"]:
            for w, t in zip(self._words(), self.onsets()):
                b = self.tl.time_to_beat(self.start + t) - b0
                if t > 1e-6 and abs(b - round(b)) * spb < 0.002:
                    snd = "pop_soft" if _norm(w) in emph else "tick"
                    out.append(Cue("sfx", t, data={"sound": snd, "gain": -9.0 if snd == "tick" else -7.0,
                                                    "velocity": 0.5}))
        at = self._attr_at()
        if at < self.dur:
            out.append(Cue("sfx", at, data={"sound": "chime", "gain": -8.0}))
        hl = at + spb
        if emph and hl < self.dur - 1e-6:
            out.append(Cue("sfx", hl, data={"sound": "swish", "gain": -10.0}))
        if self.p["riser"] and self.dur >= spb * 4:
            out.append(Cue("sfx", self.dur, data={"sound": "riser_short", "gain": -9.0, "align": "end"}))
        return out

    def layout(self, ctx: Ctx) -> tuple[skia.Font, list, float]:
        g = _g(ctx)
        f, lines, tk = text_font(ctx, self._words(), self.p["size"], int(self.p["max_lines"]), self.p["weight"])
        fk, _ = kicker_font(ctx)
        top = g.anchor_y + D.cap_height(fk) + g.bl(7)
        return f, word_boxes(f, lines, g.x0, top, L.TYPE["title"].leading, tk), tk

    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        g = _g(ctx)
        spb = self.beat_len
        draw_header(ctx, self)
        f, boxes, tk = self.layout(ctx)
        cap = D.cap_height(f)
        size = f.getSize()
        ons = self.onsets()
        emph = {_norm(e) for e in self.p["emphasis"]}
        nlines = (boxes[-1][4] + 1) if boxes else 1
        ex0 = self._exit_at()

        def out_u(li: int) -> float:
            if not self.p["exit"]:
                return 0.0
            return E.in_quart(E.clamp((lt - ex0 - li * spb / 16) / (spb * 0.375)))

        # quote mark: lands on beat 0 (out_expo scale + rise), sits between header and kicker
        qa = E.out_expo(E.clamp((lt + ctx.frame) / (spb * 0.5)))
        qf = _serif(ctx, g.px(420), 800, soft=100)
        qy = g.anchor_y - g.bl(4)
        ou0 = out_u(0)
        c.save()
        c.translate(g.x0, qy - (1 - qa) * g.bl(6) - ou0 * g.bl(14))
        k = 0.82 + 0.18 * qa
        c.scale(k, k)
        D.draw_text(c, "“", -g.px(14), g.px(210), qf, ctx.pal("accent", qa * (1 - ou0)), align="left")
        c.restore()
        if self.p["kicker"]:
            draw_kicker(ctx, self.p["kicker"], g.x0 + g.px(170), g.anchor_y - g.bl(13),
                        dim_color(ctx, "ink", 0.7, qa * (1 - ou0)))

        # highlighter strokes behind emphasis words (after the last word, out_expo 1/2 b)
        at = self._attr_at()
        hu = E.out_expo(E.clamp((lt - at - spb) / (spb * 0.5)))
        if hu > 0:
            for k, (w, x, base, ww, li) in enumerate(boxes):
                if _norm(w) in emph:
                    a = 1 - out_u(li)
                    dy = -out_u(li) * g.bl(14)
                    hx1 = x - size * 0.06 + (ww + size * 0.12) * hu
                    c.drawRect(skia.Rect(x - size * 0.06, base - cap * 0.42 + dy, hx1, base + size * 0.12 + dy),
                               D.paint(ctx.pal("accent2", 0.85 * a)))
        for k, (w, x, base, ww, li) in enumerate(boxes):
            u = E.clamp((lt - ons[k] + ctx.frame * 0.5) / (spb * 0.25))
            if u <= 0:
                continue
            e = E.out_expo(u)
            ou = out_u(li)
            a = e * (1 - ou)
            if a <= 0.002:
                continue
            dy = (1 - e) * g.bl(7) - ou * g.bl(14)
            col = ctx.pal("accent" if (_norm(w) in emph and hu <= 0) else "ink", a)
            D.draw_text(c, w, x, base + dy, f, col, align="left", tracking=tk)

        # attribution: rule + kicker-role name, role underneath (body face, dimmed)
        block_bottom = (boxes[-1][2] + size * 0.25) if boxes else g.anchor_y
        au = E.out_expo(E.clamp((lt - at) / (spb * 0.5)))
        if au > 0 and (self.p["author"] or self.p["role"]):
            ou = out_u(nlines)
            a = au * (1 - ou)
            ya = block_bottom + g.bl(7) - (1 - au) * g.bl(3) - ou * g.bl(14)
            base = draw_kicker(ctx, f"—  {self.p['author']}" if self.p["author"] else "", g.x0, ya,
                               ctx.pal("accent", a))
            if self.p["role"]:
                cr = L.TYPE["caption"]
                fr = cr.font(ctx, weight=500)
                D.draw_text(c, self.p["role"], g.x0, base + g.bl(3) + D.cap_height(fr), fr,
                            dim_color(ctx, "ink", 0.62, a), align="left", tracking=cr.tk(ctx))
        draw_chrome(ctx, self, self.p.get("label") or "quote")


# ---------------------------------------------------------------------- follow

@register("follow")
class Follow(Scene):
    """Call-to-follow end card. The pill of the previous karaoke scene is handed over on the
    cut (match on "pill") and springs out into a full-width follow button; the headline rises
    in line by line on the 8ths; the button is pressed on `tap_at` (default beat 2) with a
    click + chime, its label morphs to `done`, and it kicks on every beat after (spring)."""
    defaults = {"kicker": "Follow for more", "text": "One idea a day, set to the beat.", "button": "Follow",
                "done": "Following", "tap_at": "2b", "note": "", "label": "follow", "weight": 650}

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
        draw_header(ctx, self)
        draw_kicker(ctx, self.p["kicker"], g.x0, g.anchor_y, ctx.pal("accent"), rule=1.0)
        f, lines, tk = text_font(ctx, str(self.p["text"]).split(), "title", 3, self.p["weight"])
        fk, _ = kicker_font(ctx)
        top = g.anchor_y + D.cap_height(fk) + g.bl(7)
        boxes = word_boxes(f, lines, g.x0, top, L.TYPE["title"].leading, tk)
        cap = D.cap_height(f)
        for w, x, base, ww, li in boxes:
            u = E.out_expo(E.clamp((lt - li * spb * 0.5 + ctx.frame * 0.5) / (spb * 0.25)))
            if u <= 0:
                continue
            col = ctx.pal("ink", u)
            D.draw_text(c, w, x, base + (1 - u) * g.bl(7), f, col, align="left", tracking=tk)
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
        sp = E.Spring.settling(1.0, 0.22)
        if ctx.handed_off("pill"):
            r = ctx.handoff("pill", tgt, dur="1/2b", ease=sp.ease())
        else:
            k = E.Spring.settling(spb * 0.5, 0.22).value(max(lt, 0.0))
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
        la = E.clamp((r.width() - tgt.width() * 0.5) / (tgt.width() * 0.4))
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
        if self.p["note"]:
            nu = E.out_expo(E.clamp((lt - tap - spb * 0.5) / (spb * 0.25)))
            if nu > 0:
                draw_kicker(ctx, self.p["note"], g.x0, tgt.bottom() + g.bl(6), dim_color(ctx, "ink", 0.7, nu))
        draw_chrome(ctx, self, self.p.get("label") or "follow")
