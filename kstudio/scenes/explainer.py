"""Explainer / education scenes: cover, definition, diagram, bullets, keyfact.

An editorial system on the layout grid (layout.grid / layout.TYPE), with no pixel literals:

* Running head ("folio") on the safe top line: section index in mono, the section label as a
  tracked kicker, a hairline to the right margin and the page count. It carries the scene's
  `kicker`, so headings never need a second label.
* Text-led scenes (cover, definition, bullets, keyfact) set their first line's cap top on the
  shared anchor (g.anchor_y). The diagram owns the anchor itself: its heading sits in the
  running-head band and the figure fills the anchor -> bottom safe line.
* Type comes from the scale: headline 168 (cover), display 224 (definition term), title 126
  (headings, numerals, figures), subhead 71 for list items, body 53 for running text and every
  caption-like line (bar subs, connector labels, callouts, bullet details, pronunciation),
  kicker 32 and meta 30 for the running head only. Nothing a viewer has to read is set below
  53 px on the 1080 x 1920 reel.
* Timing is in beats. Every reveal starts on the grid: bullets every 1b, diagram nodes every
  1/2b with the connecting edge drawn on the off-beat 16th before the node lands, and a tick
  SFX on each entry. Entries use out_expo / out_quint, exits in_quart; no overshoot easing.
* Match cut: the definition publishes its highlighted term as "term"; the diagram's first
  node (and keyfact's value) picks it up with ctx.handoff("term", ...), so the highlighter
  block becomes the L1 bar across a "match" transition.
* Parallax: diagram edges sit on a far layer, bars on the scene plane, callouts in front;
  any camera move (truck, drift, push) separates them.
* Carried entrances: a scene that enters on a moving panel transition (push, whip, slice,
  split, zoom, squash) arrives composed. Its beat-0 structure (running head, heading, rules,
  tracks) is drawn settled, because the panel move is its entrance; later beats reveal as usual.
  The incoming panel therefore carries its subject from the first frame of the window.

Scene types and spec fields (all optional unless stated):

cover       index, kicker (running head), title (\\n for line breaks), accent (word of the title
            set on the accent highlighter), subtitle (standfirst on the bottom safe line), sfx,
            bars [{label, accent, h}]: a figure plate in the lower half, columns standing on the
            standfirst rule (height grows left to right, or `h` 0..1); `accent` columns fill with
            the highlighter on beat 1, so the figure lights up with the headline word.
definition  index, kicker, term, phonetic, pos, definition, note, stats [{label, value}],
            highlight (bool, default true: the term gets the accent highlighter on beat 1).
diagram     index, kicker, heading, caption (standfirst under the heading), nodes [{id, label,
            sub, value, color, w}], edges (None = chain; {from, to, style: line|dashed|arrow,
            label}), layout ladder|chain|radial|manual (manual nodes take x, y in 0..1),
            callouts [{node, text, at}], first, stagger, node_r (circle layouts), trace (ladder: a
            time, e.g. "3b"; a miss drops through the connectors a 16th apart, each label lit
            with the accent2 highlighter, and lands as a rule on the last level).
            ladder: one bar per node, flush left, width growing down the list (or `w`, a
            fraction of the content width; > 1 bleeds off the right edge); label + sub left,
            value right. The other layouts draw circles of at least 100 px radius.
bullets     index, kicker, heading, caption (standfirst under the heading), items [str | {text,
            detail, icon}], first, stagger, numbered (large display numerals; false = check marks).
keyfact     index, kicker, value, label, detail, color.

Every scene keeps the fields of the v0.1 scene of the same name.
"""
from __future__ import annotations

import math
import re

import skia

from .. import draw as D
from .. import ease as E
from .. import layout as L
from .base import Ctx, Cue, Scene, register

T = L.TYPE
ENTER = E.out_expo      # house entry
SETTLE = E.out_quint    # longer settles (bars, rules)
EXIT = E.in_quart       # house exit
SMALL = T["body"]       # caption-like lines: the 53 px step, never the 40 px caption on a reel
# transitions that move the incoming scene in as a panel: its beat-0 structure arrives with it
CARRY_KINDS = ("push", "whip", "slice", "split", "zoom", "squash")


# ---------------------------------------------------------------------- helpers

def _bu(ctx: Ctx, at: float, dur: float = 0.5) -> float:
    """0..1 progress of a window that starts `at` beats into the scene and lasts `dur` beats."""
    return E.clamp((ctx.local_beat - at) / max(dur, 1e-6))


def _su(ctx: Ctx, at_s: float, dur_b: float = 0.5) -> float:
    """Like _bu, with the start in scene seconds (resolved spec times) and the length in beats."""
    return E.clamp((ctx.lt - at_s) / max(dur_b * ctx.beat_len, 1e-6))


def carried(ctx: Ctx, scene: Scene) -> bool:
    """True when `scene` enters on a moving panel transition (see CARRY_KINDS)."""
    film = getattr(ctx, "film", None)
    for b in getattr(film, "boundaries", None) or ():
        if b.b is scene and b.kind in CARRY_KINDS and b.dur > 0:
            return True
    return False


def _bu0(ctx: Ctx, settled: bool, at: float, dur: float = 0.5) -> float:
    """_bu for beat-0 structure: 1 (already set) when the scene is carried in by its transition."""
    return 1.0 if settled else _bu(ctx, at, dur)


def _luma(rgb: tuple[int, int, int]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _on(ctx: Ctx, fill: str) -> str:
    """Readable text colour key on a fill colour key (the darker or lighter of the theme inks)."""
    if _luma(ctx.pal.rgb(fill)) > 150:
        return "ink_dark"
    light = max(("paper", "ink"), key=lambda k: _luma(ctx.pal.rgb(k)) if k in ctx.theme else -1)
    return light


def _tick(at: float, pitch: float = 0.0, gain: float = -4.0, sound: str = "tick") -> Cue:
    return Cue("sfx", at, data={"sound": sound, "gain": gain, "pitch": pitch})


def _rise(ctx: Ctx, text: str, x: float, baseline: float, f: skia.Font, color: int, u: float,
          tracking: float = 0.0, ease=None, floor: float | None = None) -> None:
    """Masked rise of one line, left-aligned at x (u = 0..1 window progress; out_quint by default,
    a long settle; pass ENTER for the snappier out_expo). `floor` caps the mask (a line set on the
    bottom safe line rises out of that line instead of through the space below it)."""
    if u <= 0 or not text:
        return
    e = (ease or SETTLE)(u)
    cap = D.cap_height(f)
    pad = cap * 0.45
    c = ctx.c
    c.save()
    bottom = baseline + pad * 1.2 if floor is None else min(baseline + pad * 1.2, floor)
    c.clipRect(skia.Rect(-ctx.W, baseline - cap - pad, 2 * ctx.W, bottom))
    D.draw_text(c, text, x, baseline + (1 - e) * (cap + pad * 2), f, color, align="left", tracking=tracking)
    c.restore()


def folio(ctx: Ctx, g: L.Grid, index: str, label: str, count: str = "", settled: bool = False,
          at: float = 0.0) -> float:
    """Running head on the safe top line; returns its baseline. The hairline draws in over the
    first half beat from beat `at` (negative: already under way on the scene's first frame); the
    label rises with it (already set when the scene is carried in)."""
    c = ctx.c
    kr, mr = T["kicker"], T["meta"]
    kf, mf = kr.font(ctx), mr.font(ctx, weight=600)
    top = g.safe_top + g.bl(2)
    base = top + D.cap_height(kf)
    ink = ctx.pal.on("bg")
    u = _bu0(ctx, settled, at, 0.75)
    x = g.x0
    if index:
        _rise(ctx, index, x, base, mf, ctx.pal("accent"), u, mr.tk(ctx))
        x += g.span_w(1) + g.gutter
    if label:
        lab = kr.text(label)
        _rise(ctx, lab, x, base, kf, ctx.pal(ink), u, kr.tk(ctx))
        x += D.text_w(kf, lab, kr.tk(ctx)) + g.gutter
    right = g.x1
    if count:
        cw = D.text_w(mf, count, mr.tk(ctx))
        right = g.x1 - cw - g.gutter
        _rise(ctx, count, g.x1 - cw, base, mf, ctx.pal("muted"), u, mr.tk(ctx))
    if right > x:
        y = base - D.cap_height(kf) / 2
        c.drawRect(skia.Rect(x, y - g.bl(0.125), x + (right - x) * SETTLE(u), y + g.bl(0.125)),
                   D.paint(ctx.pal(ink, 0.28)))
    return base


def _count(p: dict) -> str:
    return str(p.get("page") or "")


def highlight(ctx: Ctx, rect: skia.Rect, u: float, fill: str = "accent") -> skia.Rect:
    """Accent highlighter block wiped in from the left (u 0..1); returns the drawn rect."""
    w = rect.width() * ENTER(u)
    r = skia.Rect.MakeXYWH(rect.left(), rect.top(), w, rect.height())
    if w > 0.5:
        ctx.c.drawRect(r, D.paint(ctx.pal(fill)))
    return r


def _two_tone(ctx: Ctx, text: str, x: float, baseline: float, f: skia.Font, base: int, over: int,
              clip: skia.Rect, tracking: float = 0.0) -> None:
    """Text in `base`, re-drawn in `over` where it sits on the highlighter."""
    D.draw_text(ctx.c, text, x, baseline, f, base, align="left", tracking=tracking)
    if clip.width() > 0.5:
        ctx.c.save()
        ctx.c.clipRect(clip)
        D.draw_text(ctx.c, text, x, baseline, f, over, align="left", tracking=tracking)
        ctx.c.restore()


PRE = 1 / 8                # cover pre-roll (beats): its entrance is under way on frame 0
PAD_T, PAD_B = 0.2, 0.18   # highlighter above the cap top / below the baseline (em)


def _pad_rect(f: skia.Font, x: float, baseline: float, w: float, g: L.Grid) -> skia.Rect:
    """Highlighter box around a word: from a little above the cap top to below the baseline."""
    size = f.getSize()
    cap = D.cap_height(f)
    return skia.Rect(x - g.bl(2), baseline - cap - PAD_T * size, x + w + g.bl(2), baseline + PAD_B * size)


# ---------------------------------------------------------------------- cover

@register("cover")
class Cover(Scene):
    """Explainer cover: running head, a 168 px headline on the anchor (visible from frame 0:
    lines rise together over half a beat) and the standfirst rule on beat 0, one word of the
    headline on the accent highlighter on beat 1, the standfirst on the bottom safe line on beat 2.
    With `bars`, the lower half carries a figure plate: columns grow up from the standfirst rule
    on beat 0 (a 16th apart) and the `accent` ones fill with the highlighter on beat 1."""
    defaults = {"index": "01", "kicker": "", "title": "Title", "accent": "", "subtitle": "", "sfx": "tick",
                "page": "", "bars": []}

    def cues(self) -> list[Cue]:
        out = []
        if self.p["accent"] or any(isinstance(b, dict) and b.get("accent") for b in self.p["bars"] or []):
            out.append(_tick(self.bt(1), 3.0, sound=self.p["sfx"]))
        if self.p["subtitle"]:
            out.append(_tick(self.bt(2), 0.0, sound=self.p["sfx"]))
        return out

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        settled = carried(ctx, self)
        # the opening frame is the thumbnail: the headline and running head are already rising on
        # frame 0 (a 1/8 beat pre-roll), so the poster frame carries the title, not a blank page
        folio(ctx, g, self.p["index"], self.p["kicker"], _count(self.p), settled, -PRE)
        r = T["headline"]
        f = L.fit_role(ctx, r, self.p["title"].replace("\n", " "), g.content_w, face="display")
        tk = r.tk(ctx, "display")
        lines = D.wrap(f, self.p["title"], g.content_w, tk, style="balance")
        blk = D.set_lines(f, lines, g.x0, g.anchor_y, r.leading, tk, "left")
        ink = ctx.pal(ctx.pal.on(self.ground))
        acc = self.p["accent"].strip().lower()
        hl = skia.Rect.MakeEmpty()
        for i, ln in enumerate(blk.lines):
            u = _bu0(ctx, settled, i / 32 - PRE, 0.5)
            pos = ln.text.lower().find(acc) if acc else -1
            if pos >= 0 and ln.text[pos:pos + len(acc)]:
                x0 = ln.x + D.text_w(f, ln.text[:pos], tk) if pos else ln.x
                w = D.text_w(f, ln.text[pos:pos + len(acc)], tk)
                hl = highlight(ctx, _pad_rect(f, x0, ln.baseline, w, g), _bu(ctx, 1.0, 0.75))
                ctx.publish("term", hl, text=ln.text[pos:pos + len(acc)], em=hl.height() / f.getSize())
            if u >= 1 and hl.width() > 0.5 and pos >= 0:
                _two_tone(ctx, ln.text, ln.x, ln.baseline, f, ink, ctx.pal(_on(ctx, "accent")), hl, tk)
            else:
                _rise(ctx, ln.text, ln.x, ln.baseline, f, ink, u, tk, ENTER)
        floor = g.safe_bottom
        if self.p["subtitle"]:
            sr = SMALL
            sf = sr.font(ctx, weight=500)
            sl = D.wrap(sf, self.p["subtitle"], g.measure, sr.tk(ctx))
            sh = D.block_height(sf, len(sl), sr.leading)
            desc = sf.getMetrics().fDescent                      # descenders stay above the safe line
            top = max(blk.baseline + g.bl(8), g.safe_bottom - sh - desc)
            floor = top - g.bl(5)
            u = _bu0(ctx, settled, 1 / 8, 0.75)
            c.drawRect(skia.Rect(g.x0, floor, g.x0 + g.content_w * SETTLE(u), floor + g.bl(0.25)),
                       D.paint(ctx.pal(ctx.pal.on(self.ground), 0.4)))
            sb = D.set_lines(sf, sl, g.x0, top, sr.leading, sr.tk(ctx))
            for i, ln in enumerate(sb.lines):
                _rise(ctx, ln.text, ln.x, ln.baseline, sf, ctx.pal("muted"), _bu(ctx, 2.0 + i / 16, 0.75), sr.tk(ctx),
                      floor=g.safe_bottom)
        bars = [b if isinstance(b, dict) else {"label": str(b)} for b in self.p["bars"] or []]
        if bars:
            self._plate(ctx, g, bars, blk.baseline + g.bl(12), floor, settled)

    def plate_rects(self, g: L.Grid, n_or_bars: list[dict], top: float, floor: float) -> list[skia.Rect]:
        """Final column rects of the figure plate: equal columns across the content width,
        standing on `floor`, heights growing left to right (or each bar's `h`, 0..1)."""
        n = len(n_or_bars)
        gap = g.gutter
        w = (g.content_w - gap * (n - 1)) / max(n, 1)
        lo = 0.36
        out = []
        for i, bar in enumerate(n_or_bars):
            h = bar.get("h")
            frac = float(h) if h is not None else (lo + (1 - lo) * i / max(n - 1, 1) if n > 1 else 1.0)
            hh = (floor - top) * E.clamp(frac)
            x = g.x0 + i * (w + gap)
            out.append(skia.Rect(x, floor - hh, x + w, floor))
        return out

    def _plate(self, ctx: Ctx, g: L.Grid, bars: list[dict], top: float, floor: float, settled: bool) -> None:
        c = ctx.c
        ink_k = ctx.pal.on(self.ground)
        lf = SMALL.font(ctx, weight=650)
        ltk = SMALL.tk(ctx)
        cap = D.cap_height(lf)
        pad = g.bl(3)
        acc_txt = ctx.pal(_on(ctx, "accent"))
        k_acc = 0
        for i, (bar, full) in enumerate(zip(bars, self.plate_rects(g, bars, top, floor))):
            ug = SETTLE(_bu0(ctx, settled, i / 16, 0.75))
            if ug <= 0:
                continue
            r = skia.Rect(full.left(), floor - full.height() * ug, full.right(), floor)
            solid = bool(bar.get("solid"))
            base_fill = ctx.pal(ink_k) if solid else ctx.pal(ink_k, 0.08)
            c.drawRect(r, D.paint(base_fill))
            txt = ctx.pal(_on(ctx, ink_k)) if solid else ctx.pal(ink_k)
            fill_r = skia.Rect.MakeEmpty()
            if bar.get("accent"):
                ua = ENTER(_bu(ctx, 1.0 + k_acc / 16, 0.75))
                k_acc += 1
                if ua > 0:
                    fill_r = skia.Rect(r.left(), floor - r.height() * ua, r.right(), floor)
                    c.drawRect(fill_r, D.paint(ctx.pal("accent")))
            label = str(bar.get("label", ""))
            if label:
                c.save()
                c.clipRect(r)
                _two_tone(ctx, label, r.left() + pad, r.top() + pad + cap, lf, txt, acc_txt, fill_r, ltk)
                c.restore()


# ---------------------------------------------------------------------- definition

@register("definition")
class Definition(Scene):
    """Dictionary entry on the anchor. Beat 0: the term in display 224 with the page rules.
    Beat 1/2: pronunciation / part of speech in mono, the definition in body 53 and the optional
    `note`. Beat 1: the accent highlighter behind the term (published as "term" for a match cut).
    Beats 2, 3, ...: one figure of `stats` per beat on the bottom safe line; numeric figures
    after the first count up from the first one (a slow miss reads slow)."""
    defaults = {"index": "", "kicker": "", "term": "Term", "phonetic": "", "pos": "noun", "definition": "",
                "note": "", "stats": [], "highlight": True, "page": ""}

    def cues(self) -> list[Cue]:
        out = [_tick(self.bt(1), 3.0)]
        for j, _ in enumerate(list(self.p["stats"] or [])[:3]):
            out.append(_tick(self.bt(2 + j), -2.0 * j))
        return out

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        settled = carried(ctx, self)
        if self.p["index"] or self.p["kicker"]:
            folio(ctx, g, self.p["index"], self.p["kicker"], _count(self.p), settled)
        ink_k = ctx.pal.on(self.ground)
        ink = ctx.pal(ink_k)
        rule = D.paint(ctx.pal(ink_k, 0.35))
        tr = T["display"]
        term = str(self.p["term"])
        tf = L.fit_role(ctx, tr, term, g.content_w - g.bl(4), weight=700, face="display")
        tk = tr.tk(ctx, "display")
        x = g.x0 + (g.bl(2) if self.p["highlight"] else 0.0)
        base = g.anchor_y + D.cap_height(tf)
        tw = D.text_w(tf, term, tk)
        box = _pad_rect(tf, x, base, tw, g)
        hl = highlight(ctx, box, _bu(ctx, 1.0, 0.75)) if self.p["highlight"] else skia.Rect.MakeEmpty()
        ctx.publish("term", box, text=term, em=box.height() / tf.getSize())
        u = _bu0(ctx, settled, 0.0, 0.75)
        if u >= 1:
            _two_tone(ctx, term, x, base, tf, ink, ctx.pal(_on(ctx, "accent")), hl, tk)
        else:
            _rise(ctx, term, x, base, tf, ink, u, tk)
        y = base + 0.18 * tf.getSize() + g.bl(8)
        mf = T["meta"].font(ctx, weight=500, size=SMALL.px(g))
        meta = "  ·  ".join(v for v in (self.p["phonetic"], self.p["pos"]) if v)
        if meta:
            _rise(ctx, meta, g.x0, y, mf, ctx.pal("accent"), _bu(ctx, 0.5, 0.75), 0.02)
            y += g.bl(6)
        c.drawRect(skia.Rect(g.x0, y, g.x0 + g.content_w * SETTLE(_bu0(ctx, settled, 0.0, 0.75)), y + g.bl(0.25)),
                   rule)
        y += g.bl(7)
        br = T["body"]
        bf = br.font(ctx)
        if self.p["definition"]:
            bl = D.wrap(bf, self.p["definition"], g.measure, br.tk(ctx))
            bb = D.set_lines(bf, bl, g.x0, y, br.leading, br.tk(ctx))
            for i, ln in enumerate(bb.lines):
                _rise(ctx, ln.text, ln.x, ln.baseline, bf, ink, _bu(ctx, 0.5 + (i + 1) / 16, 0.75), br.tk(ctx))
            y = bb.baseline + g.bl(6)
        if self.p["note"]:
            nr = SMALL
            nf = nr.font(ctx)
            nl = D.wrap(nf, self.p["note"], g.measure, nr.tk(ctx))
            nb = D.set_lines(nf, nl, g.x0, y, nr.leading, nr.tk(ctx))
            for i, ln in enumerate(nb.lines):
                _rise(ctx, ln.text, ln.x, ln.baseline, nf, ctx.pal("muted"), _bu(ctx, 0.75 + i / 16, 0.75), nr.tk(ctx))
        stats = list(self.p["stats"] or [])[:3]
        if not stats:
            return
        vr, kr = T["title"], T["kicker"]
        kf = kr.font(ctx)
        cols = 12 // len(stats)
        vfs = [L.fit_role(ctx, vr, str(st.get("value", "")), g.span_w(cols) - g.gutter, weight=600, face="display")
               for st in stats]
        vf = min(vfs, key=lambda f: f.getSize())
        vbase = g.safe_bottom
        ktop = vbase - D.cap_height(vf) - g.bl(4) - D.cap_height(kf)
        hy = ktop - g.bl(4)
        c.drawRect(skia.Rect(g.x0, hy, g.x0 + g.content_w * SETTLE(_bu0(ctx, settled, 1 / 8, 0.75)),
                             hy + g.bl(0.25)), rule)
        first = _num(str(stats[0].get("value", "")))
        for j, st in enumerate(stats):
            sx = g.span(j * cols, cols)[0]
            uj = _bu(ctx, 2.0 + j, 0.75)
            _rise(ctx, kr.text(str(st.get("label", ""))), sx, ktop + D.cap_height(kf), kf, ctx.pal("muted"), uj,
                  kr.tk(ctx))
            val = str(st.get("value", ""))
            num = _num(val)
            if j > 0 and num and first:
                k = SETTLE(_bu(ctx, 2.0 + j, 0.875))
                v = first[1] + (num[1] - first[1]) * k
                val = f"{num[0]}{v:.{num[3]}f}{num[2]}"
            col = ctx.pal("accent") if j == 0 else ink
            _rise(ctx, val, sx, vbase, vf, col, uj, vr.tk(ctx, "display"), floor=g.safe_bottom + g.bl(0.5))


def _num(s: str) -> tuple[str, float, str, int] | None:
    """Split '80 ns' -> ('', 80.0, ' ns', 0 decimals); None when there is no number."""
    m = re.match(r"^(\D*?)(\d+(?:\.(\d+))?)(.*)$", s)
    if not m:
        return None
    return m.group(1), float(m.group(2)), m.group(4), len(m.group(3) or "")


# ---------------------------------------------------------------------- diagram

@register("diagram")
class Diagram(Scene):
    """Labelled diagram that builds on the beat: nodes every `stagger` (default 1/2b), each
    edge drawn on the off-beat 16th before the node it lands on, callouts on the following
    whole beats; a tick on every entry. The heading sits in the running-head band and the
    figure fills the anchor -> bottom safe line. The first node takes over a published "term"
    (match cut). Layouts: ladder (bars, default for portrait explainer specs), chain, radial,
    manual."""
    defaults = {"index": "", "kicker": "", "heading": "", "nodes": [], "edges": None, "layout": "chain",
                "callouts": [], "stagger": "1/2b", "first": 0.0, "node_r": 0, "caption": "", "page": "",
                "trace": None}
    time_params = ("trace",)

    # -- timing ------------------------------------------------------------
    def _appear(self) -> list[float]:
        return [self.p["first"] + i * self.p["stagger"] for i in range(len(self.p["nodes"]))]

    def _callout_times(self) -> list[float]:
        ap = self._appear()
        end = (ap[-1] if ap else 0.0) + self.p["stagger"]
        b0 = math.ceil(self.tl.beats_of(end, self.start) - 1e-6)
        out = []
        for j, co in enumerate(self.p["callouts"]):
            out.append(self.time(co["at"]) if co.get("at") is not None else self.bt(b0 + j))
        return out

    def cues(self) -> list[Cue]:
        out = []
        n = len(self.p["nodes"])
        for i, t in enumerate(self._appear()):
            if i == 0 and t <= 1e-6:
                out.append(_tick(t, 5.0, sound="tick_hi"))
            else:
                out.append(_tick(t, 5.0 - 7.0 * i / max(n - 1, 1)))
        for t in self._callout_times():
            out.append(Cue("sfx", t, data={"sound": "pop_soft", "gain": -5}))
        if self.p["trace"] is not None and self.p["layout"] == "ladder":
            out.append(_tick(float(self.p["trace"]), 0.0, gain=-4.0, sound="blip_down"))  # the miss drops
        return out

    # -- geometry ----------------------------------------------------------
    def _area(self, ctx: Ctx, g: L.Grid) -> skia.Rect:
        return skia.Rect(g.x0, g.anchor_y, g.x1, g.safe_bottom)

    def _ladder(self, ctx: Ctx, g: L.Grid) -> list[skia.Rect]:
        nodes = self.p["nodes"]
        n = max(1, len(nodes))
        a = self._area(ctx, g)
        gap = g.bl(8)
        h = (a.height() - gap * (n - 1)) / n
        out = []
        for i, nd in enumerate(nodes):
            w = nd.get("w")
            frac = float(w) if w is not None else (0.45 + (1.12 - 0.45) * i / max(n - 1, 1) if n > 1 else 1.0)
            y = a.top() + i * (h + gap)
            out.append(skia.Rect.MakeXYWH(g.x0, y, g.content_w * frac, h))
        return out

    def _circles(self, ctx: Ctx, g: L.Grid) -> tuple[list[tuple[float, float]], float]:
        nodes = self.p["nodes"]
        n = max(1, len(nodes))
        a = self._area(ctx, g)
        rmin = g.px(100)
        lay = self.p["layout"]
        if lay == "radial":
            cx, cy = a.centerX(), a.centerY()
            R = min(a.width(), a.height()) / 2
            r = self.p["node_r"] * ctx.s or max(rmin, R * 0.26)
            R -= r
            pts = [(cx, cy)] + [(cx + R * math.cos(-math.pi / 2 + k * 2 * math.pi / max(n - 1, 1)),
                                 cy + R * math.sin(-math.pi / 2 + k * 2 * math.pi / max(n - 1, 1))) for k in range(n - 1)]
            return pts, r
        if lay == "manual":
            r = self.p["node_r"] * ctx.s or rmin
            return [(a.left() + r + nd.get("x", 0.5) * (a.width() - 2 * r),
                     a.top() + r + nd.get("y", 0.5) * (a.height() - 2 * r)) for nd in nodes], r
        # chain: zig-zag down the column (portrait) or across (landscape)
        if ctx.portrait:
            r = self.p["node_r"] * ctx.s or max(rmin, min(a.width() / 4.2, a.height() / (2.2 * n)))
            step = (a.height() - 2 * r) / max(n - 1, 1)
            return [(a.left() + r if k % 2 == 0 else a.right() - r, a.top() + r + k * step) for k in range(n)], r
        r = self.p["node_r"] * ctx.s or max(rmin, min(a.width() / (2.4 * n), a.height() / 3))
        step = (a.width() - 2 * r) / max(n - 1, 1)
        return [(a.left() + r + k * step, a.centerY() + (0 if n < 3 else (-1) ** k * r * 0.35)) for k in range(n)], r

    def _edges(self) -> list[dict]:
        e = self.p["edges"]
        if e is None:
            ids = [nd.get("id", str(i)) for i, nd in enumerate(self.p["nodes"])]
            if self.p["layout"] == "radial":
                return [{"from": ids[0], "to": x} for x in ids[1:]]
            return [{"from": a, "to": b} for a, b in zip(ids, ids[1:])]
        return e

    def _fill(self, i: int, nd: dict) -> str:
        if nd.get("color"):
            return str(nd["color"])
        return "accent" if i == 0 else "ink"

    # -- drawing -----------------------------------------------------------
    def draw(self, ctx: Ctx) -> None:
        g = L.grid(ctx)
        settled = carried(ctx, self)
        fb = folio(ctx, g, self.p["index"], self.p["kicker"], _count(self.p), settled)
        ink_k = ctx.pal.on(self.ground)
        if self.p["heading"]:
            hr = T["title"]
            hf = L.fit_role(ctx, hr, self.p["heading"], g.content_w, weight=650, face="display")
            hl = D.wrap(hf, self.p["heading"], g.content_w, hr.tk(ctx, "display"), style="balance")[:1]
            hb = D.set_lines(hf, hl, g.x0, fb + g.bl(5), hr.leading, hr.tk(ctx, "display"))
            for ln in hb.lines:
                _rise(ctx, ln.text, ln.x, ln.baseline, hf, ctx.pal(ink_k), _bu0(ctx, settled, 0.0, 0.75),
                      hr.tk(ctx, "display"))
            if self.p["caption"]:
                cr = SMALL
                cf = cr.font(ctx, weight=400)
                cl = D.wrap(cf, self.p["caption"], g.content_w, cr.tk(ctx))[:1]
                for ln in D.set_lines(cf, cl, g.x0, hb.baseline + g.bl(4), cr.leading, cr.tk(ctx)).lines:
                    _rise(ctx, ln.text, ln.x, ln.baseline, cf, ctx.pal("muted"), _bu0(ctx, settled, 1 / 4, 0.75),
                          cr.tk(ctx))
        if self.p["layout"] == "ladder":
            self._draw_ladder(ctx, g, settled)
        else:
            self._draw_circles(ctx, g)

    def _edge_u(self, ctx: Ctx, ap: list[float], ia: int, ib: int) -> float:
        """Edge into node ib draws over the off-beat half of the step before ib lands."""
        t1 = max(ap[ia], ap[ib])
        half = self.p["stagger"] / 2 if self.p["stagger"] > 0 else ctx.beat_len / 4
        return SETTLE(E.clamp((ctx.lt - (t1 - half)) / max(half, 1e-6)))

    def _comet(self, ctx: Ctx, x: float, y: float, g: L.Grid) -> None:
        ctx.c.drawCircle(x, y, g.bl(1.5), D.paint(ctx.pal("accent2", 0.35), blur=g.bl(1.5)))
        ctx.c.drawCircle(x, y, g.bl(0.75), D.paint(ctx.pal("accent2")))

    def _draw_ladder(self, ctx: Ctx, g: L.Grid, settled: bool = False) -> None:
        c = ctx.c
        nodes = self.p["nodes"]
        rects = self._ladder(ctx, g)
        ap = self._appear()
        ids = {nd.get("id", str(i)): i for i, nd in enumerate(nodes)}
        lr, sr, vr = T["title"].with_(size=95.0), SMALL, T["subhead"].with_(face="display")
        pad = g.bl(4)
        edge_c = ctx.pal("accent2")
        # edges: vertical connectors in the gaps at the bars' left column, on the scene plane so they
        # stay locked to the bars under the camera; each label is centred on its x-height in the gap
        lf = T["meta"].font(ctx, weight=600, size=sr.px(g))
        xh = lf.getMetrics().fXHeight or D.cap_height(lf) * 0.72
        ex = g.x0 + g.bl(6)
        trace = self.p["trace"]
        for k, edge in enumerate(self._edges()):
            ia, ib = ids.get(edge["from"]), ids.get(edge["to"])
            if ia is None or ib is None:
                continue
            u = self._edge_u(ctx, ap, ia, ib)
            if u <= 0:
                continue
            ra, rb = rects[ia], rects[ib]
            y0, y1 = (ra.bottom(), rb.top()) if rb.top() >= ra.bottom() else (ra.top(), rb.bottom())
            yy = E.lerp(y0, y1, u)
            # the traced miss: the request drops through each gap a 16th apart and leaves it lit
            ut = _su(ctx, float(trace) + k * ctx.beat_len / 4, 0.25) if trace is not None else 0.0
            p = D.paint(edge_c, stroke=g.bl(0.5 + 0.5 * SETTLE(ut)))
            if edge.get("style") == "dashed" and ut <= 0:
                p.setPathEffect(skia.DashPathEffect.Make([g.bl(1.5), g.bl(1)], 0))
            c.drawLine(ex, y0, ex, yy, p)
            if u < 1:
                self._comet(ctx, ex, yy, g)
            elif edge.get("style", "arrow") == "arrow":
                D.arrow_head(c, ex, y1, math.pi / 2 if y1 > y0 else -math.pi / 2, g.bl(2 + 0.75 * SETTLE(ut)), edge_c)
            if 0 < ut < 1:
                self._comet(ctx, ex, E.lerp(y0, y1, E.in_out_cubic(ut)), g)
            if edge.get("label"):
                lab, lx, cy = str(edge["label"]), ex + g.bl(3), (y0 + y1) / 2
                if u >= 1 and ut > 0:
                    # the house highlighter, in accent2, marks each level the miss falls through
                    lw = D.text_w(lf, lab, T["meta"].tk(ctx))
                    box = skia.Rect(lx - g.bl(1), cy - xh / 2 - g.bl(1.25), lx + lw + g.bl(1), cy + xh / 2 + g.bl(1.25))
                    hl = highlight(ctx, box, ut, "accent2")
                    _two_tone(ctx, lab, lx, cy + xh / 2, lf, edge_c, ctx.pal(_on(ctx, "accent2")), hl, T["meta"].tk(ctx))
                else:
                    _rise(ctx, lab, lx, cy + xh / 2, lf, edge_c, u, T["meta"].tk(ctx))
        # scene plane: the empty tracks (beat 0, a 16th apart), then the bars
        n = len(nodes)
        for i, rt in enumerate(rects):
            ut = SETTLE(_bu0(ctx, settled, i / 16, 0.75))
            if ut > 0:
                c.drawRect(skia.Rect(rt.left(), rt.top(), rt.left() + rt.width() * ut, rt.bottom()),
                           D.paint(ctx.pal(ctx.pal.on(self.ground), 0.06)))
        for i, nd in enumerate(nodes):
            target = rects[i]
            fill = self._fill(i, nd)
            txt = ctx.pal(_on(ctx, fill))
            handed = i == 0 and ctx.handed_off("term")
            if handed:
                r = ctx.handoff("term", target, dur="1/2b", ease=ENTER)
                u = _bu(ctx, 0.0, 0.75)
            else:
                u = _su(ctx, ap[i], 0.75)
                if u <= 0:
                    continue
                r = skia.Rect.MakeXYWH(target.left(), target.top(), target.width() * SETTLE(u), target.height())
            c.drawRect(r, D.paint(ctx.pal(fill)))
            c.save()
            c.clipRect(r)
            lf = lr.font(ctx, weight=650)
            lbase = target.top() + pad + D.cap_height(lf)
            if handed and u < 1:
                # the published word leaves through the top of the morphing block (in_quart)
                out = E.clamp(u * 2)
                word, em = self._handed_word(ctx)
                if word and out < 1:
                    # shrink with the block on both axes so the whole word stays inside it
                    tk = T["display"].tk(ctx, "display")
                    w100 = D.text_w(T["display"].font(ctx, weight=700, size=100.0, face="display"), word, tk) / 100
                    size = min(r.height() / em, (r.width() - 2 * g.bl(2)) / max(w100, 1e-3))
                    wf = T["display"].font(ctx, weight=700, size=max(size, 1.0), face="display")
                    wy = r.bottom() - PAD_B * size - EXIT(out) * r.height()
                    D.draw_text(c, word, r.left() + g.bl(2), wy, wf, txt, align="left",
                                tracking=T["display"].tk(ctx, "display"))
            lu = _bu(ctx, 0.25, 0.75) if handed else u
            label = str(nd.get("label", ""))
            vx_right = min(target.right(), g.x1) - pad
            _rise(ctx, label, target.left() + pad, lbase, lf, txt, lu, lr.tk(ctx, "display"))
            if nd.get("value"):
                vf = vr.font(ctx, weight=600)
                vw = D.text_w(vf, str(nd["value"]), vr.tk(ctx, "display"))
                _rise(ctx, str(nd["value"]), vx_right - vw, lbase, vf, txt, _su(ctx, ap[i] + ctx.beat_len / 8, 0.75)
                      if not handed else _bu(ctx, 0.375, 0.75), vr.tk(ctx, "display"))
            if nd.get("sub"):
                sf = T["meta"].font(ctx, weight=500, size=sr.px(g))
                _rise(ctx, str(nd["sub"]), target.left() + pad, target.bottom() - pad, sf, ctx.pal(_on(ctx, fill), 0.78),
                      _su(ctx, ap[i] + ctx.beat_len / 4, 0.75) if not handed else _bu(ctx, 0.5, 0.75), T["meta"].tk(ctx))
            c.restore()
        if trace is not None and nodes:
            # the traced miss lands: an accent2 rule wipes along the top of the last level
            n_e = sum(1 for e in self._edges() if e.get("from") in ids and e.get("to") in ids)
            ua = ENTER(_su(ctx, float(trace) + n_e * ctx.beat_len / 4, 0.5))
            if ua > 0:
                last = rects[-1]
                right = min(last.right(), float(ctx.W))
                c.drawRect(skia.Rect(last.left(), last.top(), last.left() + (right - last.left()) * ua,
                                     last.top() + g.bl(1)), D.paint(ctx.pal("accent2")))
        # near layer: callouts
        times = self._callout_times()
        with ctx.layer(0.85):
            cr = SMALL
            cf = cr.font(ctx, weight=650)
            for j, co in enumerate(self.p["callouts"]):
                i = ids.get(co.get("node", ""), 0)
                if i >= n:
                    continue
                u = _su(ctx, times[j], 0.75)
                if u <= 0:
                    continue
                ctext = str(co.get("text", ""))
                rt = rects[i]
                tw = D.text_w(cf, ctext, cr.tk(ctx))
                pw, ph = tw + 2 * g.bl(3), D.cap_height(cf) + 2 * g.bl(2.5)
                if rt.right() + g.gutter + pw <= g.x1:
                    bx, by = rt.right() + g.gutter, rt.centerY() - ph / 2
                else:
                    sub_w = D.text_w(T["meta"].font(ctx, size=sr.px(g)), str(nodes[i].get("sub", "")), T["meta"].tk(ctx))
                    bx, by = rt.left() + pad + sub_w + g.gutter * 2, rt.bottom() - pad - ph / 2 - D.cap_height(cf) / 2
                w = pw * ENTER(u)
                c.save()
                c.clipRect(skia.Rect.MakeXYWH(bx, by, w, ph))
                D.rrect(c, bx, by, pw, ph, ph / 2, D.paint(ctx.pal("accent2")))
                D.draw_text(c, ctext, bx + g.bl(3), by + ph / 2 + D.cap_height(cf) / 2, cf, ctx.pal(_on(ctx, "accent2")),
                            align="left", tracking=cr.tk(ctx))
                c.restore()

    def _handed_word(self, ctx: Ctx) -> tuple[str, float]:
        """Text and box height (em) of the element handed over as "term"."""
        film = ctx.film
        src = film.handoff_source(self, "term") if film is not None else None
        data = (src or {}).get("data", {})
        return str(data.get("text", "")), float(data.get("em") or 1.08)

    def _draw_circles(self, ctx: Ctx, g: L.Grid) -> None:
        c = ctx.c
        pts, r = self._circles(ctx, g)
        nodes = self.p["nodes"]
        ids = {nd.get("id", str(i)): i for i, nd in enumerate(nodes)}
        ink_k = ctx.pal.on(self.ground)
        ap = self._appear()
        with ctx.layer(1.6):
            for edge in self._edges():
                ia, ib = ids.get(edge["from"]), ids.get(edge["to"])
                if ia is None or ib is None:
                    continue
                u = self._edge_u(ctx, ap, ia, ib)
                if u <= 0:
                    continue
                (x0, y0), (x1, y1) = pts[ia], pts[ib]
                ang = math.atan2(y1 - y0, x1 - x0)
                sx, sy = x0 + math.cos(ang) * r, y0 + math.sin(ang) * r
                ex, ey = x1 - math.cos(ang) * r, y1 - math.sin(ang) * r
                px, py = E.lerp(sx, ex, u), E.lerp(sy, ey, u)
                p = D.paint(ctx.pal("accent2"), stroke=g.bl(0.5))
                if edge.get("style") == "dashed":
                    p.setPathEffect(skia.DashPathEffect.Make([g.bl(1.5), g.bl(1)], 0))
                c.drawLine(sx, sy, px, py, p)
                if u < 1:
                    self._comet(ctx, px, py, g)
                elif edge.get("style") == "arrow":
                    D.arrow_head(c, ex, ey, ang, g.bl(2.5), ctx.pal("accent2"))
                if edge.get("label"):
                    lf = T["meta"].font(ctx, weight=600, size=SMALL.px(g))
                    _rise(ctx, str(edge["label"]), (sx + ex) / 2 + g.bl(2), (sy + ey) / 2 - g.bl(2), lf,
                          ctx.pal("accent2"), u, T["meta"].tk(ctx))
        for i, nd in enumerate(nodes):
            x, y = pts[i]
            rr = r
            if i == 0 and ctx.handed_off("term"):
                hr = ctx.handoff("term", skia.Rect(x - r, y - r, x + r, y + r), dur="1/2b", ease=ENTER)
                x, y, rr = hr.centerX(), hr.centerY(), min(hr.width(), hr.height()) / 2
                u = 1.0
            else:
                u = _su(ctx, ap[i], 0.75)
                if u <= 0:
                    continue
            e = ENTER(u)
            fill = self._fill(i, nd)
            c.drawCircle(x, y, rr * e, D.paint(ctx.pal(fill)))
            label = str(nd.get("label", ""))
            lf = D.fit_font(ctx.theme["display"], label, rr * 1.5, max(rr * 0.62, SMALL.px(g)), 650)
            D.draw_text(c, label, x, y, lf, ctx.pal(_on(ctx, fill), E.clamp(u * 2)), valign="middle")
            if nd.get("sub"):
                sf = T["meta"].font(ctx, weight=500, size=SMALL.px(g))
                _rise(ctx, str(nd["sub"]), x - D.text_w(sf, str(nd["sub"])) / 2, y + rr + g.bl(7), sf,
                      ctx.pal(ink_k, 0.7), _su(ctx, ap[i] + ctx.beat_len / 4, 0.75))
        times = self._callout_times()
        with ctx.layer(0.85):
            cf = SMALL.font(ctx, weight=650)
            for j, co in enumerate(self.p["callouts"]):
                ni = ids.get(co.get("node", ""), 0)
                u = _su(ctx, times[j], 0.75)
                if u <= 0 or ni >= len(pts):
                    continue
                x, y = pts[ni]
                txt = str(co.get("text", ""))
                tw = D.text_w(cf, txt) + 2 * g.bl(3)
                ph = D.cap_height(cf) + 2 * g.bl(2.5)
                bx = min(max(x - tw / 2, g.x0), g.x1 - tw)
                by = y - r - g.bl(3) - ph
                c.save()
                c.clipRect(skia.Rect.MakeXYWH(bx, by, tw * ENTER(u), ph))
                D.rrect(c, bx, by, tw, ph, ph / 2, D.paint(ctx.pal("accent2")))
                D.draw_text(c, txt, bx + g.bl(3), by + ph / 2 + D.cap_height(cf) / 2, cf, ctx.pal(_on(ctx, "accent2")),
                            align="left")
                c.restore()


# ---------------------------------------------------------------------- bullets

@register("bullets")
class Bullets(Scene):
    """Numbered list with large display numerals (headline 168): the heading (title 126) and the
    list's rules and ghosted numerals on beat 0, then one item per `stagger`
    (default 1b) from `first` (default 1b) with a tick: its numeral and accent rule come in,
    its text rises, and the previous item steps back to muted. Items are strings or
    {text, detail, icon}; numbered false sets check marks.
    The heading's cap top is on the anchor with the standfirst under it; the rows share the rest
    down to the bottom safe line. Item lines are subhead 71, details body 53.
    Carried in by a push, the heading, rules and ghost numerals arrive already set."""
    defaults = {"index": "", "kicker": "", "heading": "", "caption": "", "items": [], "stagger": "1b",
                "first": "1b", "numbered": True, "page": ""}

    def _t(self, i: int) -> float:
        return self.p["first"] + i * self.p["stagger"]

    def cues(self) -> list[Cue]:
        return [_tick(self._t(i), 2.0 * i) for i in range(len(self.p["items"]))]

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        settled = carried(ctx, self)
        folio(ctx, g, self.p["index"], self.p["kicker"], _count(self.p), settled)
        ink_k = ctx.pal.on(self.ground)
        top = g.anchor_y
        if self.p["heading"]:
            # heading cap top on the anchor, standfirst under it; the list owns the rest down to
            # the bottom safe line (one item line + a two-line detail per row)
            hr = T["title"]
            hf = L.fit_role(ctx, hr, self.p["heading"], g.content_w, weight=650, face="display")
            hl = D.wrap(hf, self.p["heading"], g.content_w, hr.tk(ctx, "display"), style="balance")[:1]
            hb = D.set_lines(hf, hl, g.x0, top, hr.leading, hr.tk(ctx, "display"))
            for i, ln in enumerate(hb.lines):
                _rise(ctx, ln.text, ln.x, ln.baseline, hf, ctx.pal(ink_k), _bu0(ctx, settled, i / 16, 0.75),
                      hr.tk(ctx, "display"))
            top = max(top, hb.baseline + g.bl(8))
            if self.p["caption"]:
                cf = SMALL.font(ctx, weight=400)
                cl = D.wrap(cf, self.p["caption"], g.content_w, SMALL.tk(ctx))[:1]
                cb = D.set_lines(cf, cl, g.x0, hb.baseline + g.bl(4), SMALL.leading, SMALL.tk(ctx))
                for ln in cb.lines:
                    _rise(ctx, ln.text, ln.x, ln.baseline, cf, ctx.pal("muted"), _bu0(ctx, settled, 1 / 8, 0.75),
                          SMALL.tk(ctx))
                top = max(top, cb.baseline + g.bl(6))
        items = self.p["items"]
        n = len(items)
        if not n:
            return
        row_h = (g.safe_bottom - top) / n
        nr, br, dr = T["headline"], T["subhead"], SMALL
        nf = nr.font(ctx, weight=500, face="display")
        bf, df = br.font(ctx, weight=620), dr.font(ctx, weight=400)
        ntk = nr.tk(ctx, "display")
        tx = g.col(2)
        tw = g.x1 - tx
        hair = g.bl(0.25)
        for i, item in enumerate(items):
            text = item if isinstance(item, str) else str(item.get("text", ""))
            detail = "" if isinstance(item, str) else str(item.get("detail", ""))
            icon = None if isinstance(item, str) else item.get("icon")
            ry = top + i * row_h
            # beat 0: the list's structure (rule + ghost numeral), staggered by a 16th
            u0 = _bu0(ctx, settled, (i + 1) / 16, 0.75)
            c.drawRect(skia.Rect(g.x0, ry, g.x0 + g.content_w * SETTLE(u0), ry + hair), D.paint(ctx.pal(ink_k, 0.25)))
            ctop = ry + g.bl(4)
            nbase = ctop + D.cap_height(nf)
            num = f"{i + 1}"
            mark = bool(icon) or not self.p["numbered"]
            if not mark:
                _rise(ctx, num, g.x0, nbase, nf, ctx.pal(ink_k, 0.1), u0, ntk)
            t0 = self._t(i)
            u = _su(ctx, t0, 0.75)
            if u <= 0:
                continue
            nxt = self._t(i + 1) if i < n - 1 else None
            back = 0.0 if nxt is None else EXIT(_su(ctx, nxt, 0.25))
            k = 1.0 - 0.45 * back
            # accent rule: grows in with the item, hands over to the next item
            w_in = ENTER(u) * g.span_w(2)
            x_out = g.x0 + g.span_w(2) * back
            if x_out < g.x0 + w_in:
                c.drawRect(skia.Rect(x_out, ry - g.bl(0.25), g.x0 + w_in, ry + g.bl(0.5)), D.paint(ctx.pal("accent")))
            if mark:
                sz = D.cap_height(nf)
                c.save()
                c.clipRect(skia.Rect(g.x0 - g.bl(2), ctop - g.bl(2), g.col(2), ctop + sz + g.bl(2)))
                c.translate(0, (1 - ENTER(u)) * sz * 1.4)
                D.icon(c, icon or "check", g.x0 + sz / 2, ctop + sz / 2, sz, ctx.pal("accent", k))
                c.restore()
            else:
                col = D.argb(D.mix_rgb(ctx.pal.rgb("accent"), ctx.pal.rgb("muted"), back))
                _rise(ctx, num, g.x0, nbase, nf, col, u, ntk)
            lines = D.wrap(bf, text, tw, br.tk(ctx))
            bb = D.set_lines(bf, lines, tx, ctop, br.leading, br.tk(ctx))
            for li, ln in enumerate(bb.lines):
                _rise(ctx, ln.text, ln.x, ln.baseline, bf, ctx.pal(ink_k, k), _su(ctx, t0 + li * ctx.beat_len / 16, 0.75),
                      br.tk(ctx))
            if detail:
                dl = D.wrap(df, detail, tw, dr.tk(ctx))
                db = D.set_lines(df, dl, tx, bb.baseline + g.bl(4), dr.leading, dr.tk(ctx))
                for li, ln in enumerate(db.lines):
                    _rise(ctx, ln.text, ln.x, ln.baseline, df, ctx.pal("muted", 0.6 + 0.4 * k),
                          _su(ctx, t0 + (li + 1) * ctx.beat_len / 8, 0.75), dr.tk(ctx))


# ---------------------------------------------------------------------- keyfact

@register("keyfact")
class KeyFact(Scene):
    """One big figure or short phrase, flush left on the anchor (display role, scaled up to fill
    the content width), a title-size label on beat 1 and a body detail on beat 2. The value
    takes over a published "term" when it follows a match cut."""
    defaults = {"index": "", "kicker": "", "value": "4", "label": "", "detail": "", "color": "accent", "page": ""}

    def cues(self) -> list[Cue]:
        out = [Cue("sfx", 0.0, data={"sound": "hit", "gain": -6}), Cue("hit", 0.0, data={"strength": 0.3})]
        if self.p["label"]:
            out.append(_tick(self.bt(1)))
        if self.p["detail"]:
            out.append(_tick(self.bt(2), -2.0))
        return out

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        settled = carried(ctx, self)
        if self.p["index"] or self.p["kicker"]:
            folio(ctx, g, self.p["index"], self.p["kicker"], _count(self.p), settled)
        ink_k = ctx.pal.on(self.ground)
        val = str(self.p["value"])
        vr = T["display"]
        vf = D.fit_font(ctx.theme["display"], val, g.content_w, vr.px(g) * 2.0, 700)
        cap = D.cap_height(vf)
        base = g.anchor_y + cap
        vw = D.text_w(vf, val)
        if ctx.handed_off("term"):
            r = ctx.handoff("term", skia.Rect(g.x0, g.anchor_y, g.x0 + vw, base), dur="1/2b", ease=ENTER)
            k = r.height() / max(cap, 1.0)
            c.save()
            c.translate(r.left(), r.bottom())
            c.scale(k, k)
            D.draw_text(c, val, 0, 0, vf, ctx.pal(self.p["color"]), align="left")
            c.restore()
        else:
            _rise(ctx, val, g.x0, base, vf, ctx.pal(self.p["color"]), _bu0(ctx, settled, 0.0, 0.75))
        y = base + g.bl(10)
        if self.p["label"]:
            lr = T["title"]
            lf = L.fit_role(ctx, lr, self.p["label"], g.content_w, weight=600, face="display")
            ll = D.wrap(lf, self.p["label"], g.content_w, lr.tk(ctx, "display"), style="balance")
            lb = D.set_lines(lf, ll, g.x0, y, lr.leading, lr.tk(ctx, "display"))
            for i, ln in enumerate(lb.lines):
                _rise(ctx, ln.text, ln.x, ln.baseline, lf, ctx.pal(ink_k), _bu(ctx, 1.0 + i / 16, 0.75),
                      lr.tk(ctx, "display"))
            y = lb.baseline + g.bl(8)
        if self.p["detail"]:
            dr = T["body"]
            df = dr.font(ctx, weight=400)
            dl = D.wrap(df, self.p["detail"], g.measure, dr.tk(ctx))
            top = max(y, g.safe_bottom - D.block_height(df, len(dl), dr.leading))
            for i, ln in enumerate(D.set_lines(df, dl, g.x0, top, dr.leading, dr.tk(ctx)).lines):
                _rise(ctx, ln.text, ln.x, ln.baseline, df, ctx.pal("muted"), _bu(ctx, 2.0 + i / 16, 0.75), dr.tk(ctx))
