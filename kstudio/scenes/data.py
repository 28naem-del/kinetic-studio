"""Data / stats scenes: counter, line, candles, bars, stat_grid, ticker.

An editorial data-desk system on the layout grid (layout.grid / layout.TYPE), timed in beats:

* Chrome on every scene, in screen space (the camera never moves it): a running head on the
  safe top line (scene index in mono, the scene `kicker`, a hairline, a LIVE dot that pulses on
  the beat) and a market tape on the bottom safe line that scrolls on film time, so it runs
  continuously through every cut. The tape lists the film's watchlist (the first `ticker`
  scene's items, or a scene's own `tape` list; `tape: false` hides it) with a disclaimer under it.
* Headings are the 126 px title role (display face, wght 700) with the cap top on a shared line
  under the running head; charts own the plot band below it, the full content width
  (gridlines x0..x1, data x0..x1 minus a value-label gutter) from that line to the bottom safe
  zone. Text-led scenes (counter) set the hero figure's cap top on the 560 anchor.
* Figures are JetBrains Mono (tabular by construction). Counting values step on 16th notes and
  roll digit by digit like an odometer; leading zeros of the final width are set dim so the
  figure never reflows. The counter's weight axis climbs 300 -> 800 as it counts.
* Colour carries meaning only: the accent is "up / the subject", `neg` is "down"; every other
  mark is ink or muted.
* Motion grammar: entries out_expo / out_quint, moves in_out_quint, no overshoot except the delta
  pill (pop = back 4 %). Every reveal, count landing, candle open/close and callout sits on the
  beat grid and has an SFX on that beat.
* Match cuts: the counter publishes its sparkline as "spark" (the line chart grows out of it),
  the line chart publishes its plot frame as "plot" (the candles keep the axes and roll the
  axis labels to the new scale), and the first stat card publishes "card0" (the ticker's
  first row is born from it). Use transition "match" (or any kind with "handoff": true).
* Parallax: gridlines sit on a far layer, data on the scene plane, callouts in front.

Scene types and spec fields (all optional unless stated; times accept "Nb" beat units):

counter    kicker, heading (title role), value (None = last value of `spark`), from, decimals,
           prefix, suffix, label, delta (None = change of `spark` in %), delta_note,
           count ("2b": the count lands on the downbeat after it), first, spark (series or
           seed dict; true = random; false = none), color.
line       kicker, heading ("{change}" and "{last}" are filled in), series, prefix, suffix,
           decimals, first (cursor start), draw (cursor travel), events [{i, text}],
           x_labels, change (big change figure at the end of the read), dek.
candles    kicker, heading, seed, n, start, end, vol, level (resistance the last full candle
           closes above), ohlc (explicit [[o, h, l, c], ...]), ma, prefix, decimals,
           live (number of candles that form on the beat, one per beat, default 3), callout.
bars       kicker, heading, items [{label, value, prev, color}], prefix, suffix, decimals,
           highlight (label), first, stagger, grow, callout (text inside the highlighted bar).
stat_grid  kicker, heading, items [{label, value, prefix, suffix, decimals, delta, spark, gauge}],
           first, stagger, count.
ticker     kicker, heading, items [{sym, name, price, change, decimals, prefix}], jitter,
           breadth (advancing/declining bar, default true).

Every scene keeps the v0.1 fields of the same name (title -> heading, seconds-valued timings).
"""
from __future__ import annotations

import contextlib
import math
import random
from collections.abc import Iterator
from typing import Any

import skia

from .. import draw as D
from .. import ease as E
from .. import layout as L
from .base import Ctx, Cue, Scene, register

T = L.TYPE
ENTER = E.out_expo        # house entry
SETTLE = E.out_quint      # long settles (rules, bars, gridlines)
MOVE = E.in_out_quint     # cursor / morph moves
POP = E.resolve("pop")    # back(0.04): the delta pill only
MINUS = "−"
UP, DOWN = "▲", "▼"


# ---------------------------------------------------------------------- numbers

def fmt(v: float, decimals: int = 0, prefix: str = "", suffix: str = "", commas: bool = True,
        sign: bool = False) -> str:
    """Formatted figure: thousands commas, a true minus sign, optional explicit plus."""
    body = f"{abs(v):,.{decimals}f}" if commas else f"{abs(v):.{decimals}f}"
    s = MINUS if v < 0 and float(body.replace(",", "") or 0) != 0 else ("+" if sign and v > 0 else "")
    return f"{s}{prefix}{body}{suffix}"


def padded(v: float, final: float, decimals: int = 0, prefix: str = "", suffix: str = "") -> tuple[str, int, int]:
    """`v` set to the width of `final` with leading zeros (tabular, never reflows).
    Returns (text, dim_start, dim_end): the leading pad characters to draw dim."""
    tgt_int = f"{abs(final):,.{decimals}f}".split(".")[0]
    q = round(abs(v), decimals)
    ip = format(int(q), f"0{len(tgt_int)},d")
    body = ip + (("." + f"{q:.{decimals}f}".split(".")[1]) if decimals > 0 else "")
    n = 0
    while n < len(ip) - 1 and ip[n] in "0,":
        n += 1
    sign = MINUS if v < 0 and q > 0 else ""
    return f"{sign}{prefix}{body}{suffix}", len(sign) + len(prefix), len(sign) + len(prefix) + n


def series_from(spec: Any, n_default: int = 48) -> list[float]:
    """A list of numbers, or {"seed", "n", "start", "drift", "vol"} for a seeded random walk."""
    if isinstance(spec, list):
        return [float(v) for v in spec]
    spec = spec if isinstance(spec, dict) else {}
    rnd = random.Random(int(spec.get("seed", 7)))
    n = int(spec.get("n", n_default))
    v = float(spec.get("start", 100.0))
    drift, vol = float(spec.get("drift", 0.004)), float(spec.get("vol", 0.03))
    out = []
    for _ in range(n):
        v *= 1 + drift + rnd.gauss(0, vol)
        out.append(v)
    return out


def change_pct(data: list[float]) -> float:
    return (data[-1] / data[0] - 1.0) * 100.0 if data and data[0] else 0.0


def nice_step(span: float, n: int = 4) -> float:
    raw = max(span, 1e-9) / n
    k = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * k:
            return m * k
    return 10 * k


def axis(lo: float, hi: float, pad_lo: float = 0.08, pad_hi: float = 0.04, n: int = 4) -> tuple[float, float, float]:
    """A 'nice' axis of n equal steps covering lo..hi with fractional padding: (min, max, step)."""
    span = max(hi - lo, abs(hi) * 1e-3, 1e-9)
    a, b = lo - span * pad_lo, hi + span * pad_hi
    raw = (b - a) / n
    k = 10 ** math.floor(math.log10(raw))
    ladder = [m * k * 10 ** j for j in range(4) for m in (1, 2, 2.5, 5)]
    for step in ladder:
        if step < raw * 0.999:
            continue
        lo_n = math.floor(a / step) * step
        if lo_n + step * n >= b:
            return lo_n, lo_n + step * n, step
    return a, b, (b - a) / n


# ---------------------------------------------------------------------- drawing helpers

def _bu(ctx: Ctx, at: float, dur: float = 0.5) -> float:
    """0..1 progress of a window starting `at` beats into the scene and lasting `dur` beats."""
    return E.clamp((ctx.local_beat - at) / max(dur, 1e-6))


def _sfx(at: float, sound: str, gain: float = -4.0, **kw: Any) -> Cue:
    return Cue("sfx", at, data={"sound": sound, "gain": gain, **kw})


def mono(ctx: Ctx, px: float, weight: int = 600) -> skia.Font:
    return ctx.font("mono", px, weight)


def _rise(ctx: Ctx, text: str, x: float, baseline: float, f: skia.Font, color: int, u: float,
          tracking: float = 0.0, align: str = "left", ease=None) -> float:
    """Masked rise of one line (u = 0..1 window progress). Returns the line width."""
    w = D.text_w(f, text, tracking)
    if u <= 0 or not text:
        return w
    e = (ease or ENTER)(u)
    cap = D.cap_height(f)
    pad = cap * 0.45
    c = ctx.c
    c.save()
    c.clipRect(skia.Rect(-ctx.W, baseline - cap - pad, 2 * ctx.W, baseline + pad * 1.4))
    D.draw_text(c, text, x, baseline + (1 - e) * (cap + pad * 2), f, color, align=align, tracking=tracking)
    c.restore()
    return w


def odometer(ctx: Ctx, a: str, b: str, u: float, x: float, baseline: float, f: skia.Font,
             color: int, dim: int | None = None, dim_range: tuple[int, int] = (0, 0),
             align: str = "left") -> float:
    """Mono figure rolling from string `a` to `b` (u = 0..1): changed characters slide up out of
    a clipped slot while the new ones rise in. Strings are right-aligned to each other.
    Returns the drawn width."""
    n = max(len(a), len(b))
    a, b = a.rjust(n), b.rjust(n)
    adv = D.text_w(f, "0")
    w = adv * n
    x0 = x - w if align == "right" else x - w / 2 if align == "center" else x
    cap = D.cap_height(f)
    slot = cap * 1.4
    c = ctx.c
    e = ENTER(E.clamp(u))
    c.save()
    c.clipRect(skia.Rect(x0 - adv, baseline - cap * 1.1, x0 + w + adv, baseline + cap * 0.25))
    for i in range(n):
        col = dim if dim is not None and dim_range[0] <= i < dim_range[1] else color
        xi = x0 + i * adv
        if a[i] == b[i] or e >= 1.0:
            if b[i] != " ":
                D.draw_text(c, b[i], xi, baseline, f, col, align="left", kern=False)
            continue
        if a[i] != " " and e < 1:
            D.draw_text(c, a[i], xi, baseline - slot * e, f, _fade(col, (1 - e) ** 2), align="left", kern=False)
        if b[i] != " " and e > 0:
            D.draw_text(c, b[i], xi, baseline + slot * (1 - e), f, _fade(col, min(1.0, e * 1.5)), align="left",
                        kern=False)
    c.restore()
    return w


def _fade(col: int, k: float) -> int:
    return skia.ColorSetA(col, int(skia.ColorGetA(col) * E.clamp(k)))


def stepped(lb: float, start: float, dur: float, div: float = 4.0) -> tuple[float, float, float]:
    """A count that advances on a beat subdivision (16ths by default): returns (from, to, u)
    progress values (0..1, out_expo shaped) of the current step and the roll progress within it."""
    if lb <= start:
        return 0.0, 0.0, 1.0
    steps = max(1, int(round(dur * div)))
    k = (lb - start) * div
    i = int(math.floor(k))
    if i >= steps:
        return 1.0, 1.0, 1.0

    def at(j: int) -> float:
        return 1.0 if j >= steps else ENTER(j / steps)
    return at(i), at(i + 1), E.clamp((k - i) / 0.7)


@contextlib.contextmanager
def screen(ctx: Ctx) -> Iterator[None]:
    """Draw in screen space: undo the scene camera (chrome stays put while the content moves)."""
    cam = ctx.cam
    if cam is None:
        yield
        return
    inv = skia.Matrix()
    if not cam.matrix(1.0).invert(inv):
        yield
        return
    ctx.c.save()
    ctx.c.concat(inv)
    try:
        yield
    finally:
        ctx.c.restore()


def tri(c: skia.Canvas, x: float, y: float, size: float, up: bool, color: int) -> None:
    """Small up/down triangle with its centre at (x, y)."""
    p = skia.Path()
    h = size * 0.86
    if up:
        p.moveTo(x - size / 2, y + h / 2); p.lineTo(x + size / 2, y + h / 2); p.lineTo(x, y - h / 2)
    else:
        p.moveTo(x - size / 2, y - h / 2); p.lineTo(x + size / 2, y - h / 2); p.lineTo(x, y + h / 2)
    p.close()
    c.drawPath(p, D.paint(color))


def spark_path(data: list[float], r: skia.Rect, lo: float | None = None, hi: float | None = None,
               upto: float | None = None) -> tuple[skia.Path, list[tuple[float, float]]]:
    """Polyline of `data` fitted into r (optionally only up to fractional index `upto`)."""
    n = len(data)
    lo = min(data) if lo is None else lo
    hi = max(data) if hi is None else hi
    span = max(hi - lo, 1e-9)
    X = lambda i: r.left() + r.width() * i / max(n - 1, 1)  # noqa: E731
    Y = lambda v: r.bottom() - r.height() * (v - lo) / span  # noqa: E731
    pts = [(X(i), Y(v)) for i, v in enumerate(data)]
    if upto is not None and upto < n - 1:
        k = int(math.floor(max(upto, 0.0)))
        fr = max(upto, 0.0) - k
        pts = pts[:k + 1]
        if k + 1 < n:
            pts.append((X(k + fr), Y(E.lerp(data[k], data[k + 1], fr))))
    return D.polyline(pts), pts


def area_under(pts: list[tuple[float, float]], base_y: float) -> skia.Path:
    p = D.polyline(pts)
    if pts:
        p.lineTo(pts[-1][0], base_y)
        p.lineTo(pts[0][0], base_y)
        p.close()
    return p


# ---------------------------------------------------------------------- base scene

class DataScene(Scene):
    """Shared grid, chrome and heading for the data scenes."""
    time_params = ("draw", "reveal")

    # -- timing ----------------------------------------------------------
    def shutter(self, lt: float) -> float:
        """Closed shutter on the frame that lands on the cut: a centred shutter there would average
        the outgoing and incoming scenes into one smeared frame; the cut frame is the new scene, sharp."""
        return 0.0 if self.start > 0 and lt < 1 / 120 else 1.0

    @property
    def cut_in(self) -> bool:
        """Entered on a cut: the new heading and running head are the cut, set on its first frame."""
        return self.start > 0

    def b(self, key: str, default: float) -> float:
        """A timing parameter in beats (spec values are beat units or seconds)."""
        v = self.p.get(key)
        if v is None:
            return default
        return float(v) / max(self.beat_len, 1e-6)

    # -- geometry --------------------------------------------------------
    @staticmethod
    def head_top(g: L.Grid) -> float:
        return g.safe_top + g.bl(12)

    @staticmethod
    def plot_rect(g: L.Grid) -> skia.Rect:
        """The chart band: full content width, from under the heading to above the x labels."""
        return skia.Rect(g.x0, g.safe_top + g.bl(33), g.x1, g.safe_bottom - g.bl(10))

    @staticmethod
    def gutter_w(g: L.Grid) -> float:
        return g.span_w(2)

    # -- chrome ----------------------------------------------------------
    def _index(self, ctx: Ctx) -> tuple[str, str]:
        film = ctx.film
        if film is None or self not in getattr(film, "scenes", []):
            return "01", ""
        i = film.scenes.index(self)
        return f"{i + 1:02d}", f"{len(film.scenes):02d}"

    def tape_items(self, ctx: Ctx) -> list[dict]:
        own = self.p.get("tape")
        if own is False:
            return []
        if isinstance(own, list):
            return own
        film = ctx.film
        for sc in getattr(film, "scenes", []) or []:
            if sc.type == "ticker" and sc.p.get("items"):
                return list(sc.p["items"])
        return []

    def chrome(self, ctx: Ctx, g: L.Grid) -> None:
        with screen(ctx):
            self._folio(ctx, g)
            items = self.tape_items(ctx)
            if items:
                draw_tape(ctx, g, items)

    def _folio(self, ctx: Ctx, g: L.Grid) -> None:
        c = ctx.c
        kr, mr = T["kicker"], T["meta"]
        kf, mf = kr.font(ctx), mr.font(ctx, weight=600)
        base = g.safe_top + g.bl(2) + D.cap_height(kf)
        ink = ctx.pal.on("bg")
        u = 1.0 if self.cut_in else _bu(ctx, 0.0, 0.5)
        idx, count = self._index(ctx)
        x = g.x0
        D.draw_text(c, idx, x, base, mf, ctx.pal("accent"), align="left", tracking=mr.tk(ctx))
        x += g.span_w(1) + g.gutter
        label = kr.text(str(self.p.get("kicker") or ""))
        if label:
            _rise(ctx, label, x, base, kf, ctx.pal(ink), u, kr.tk(ctx))
            x += D.text_w(kf, label, kr.tk(ctx)) + g.gutter
        live = "LIVE"
        lw = D.text_w(mf, live, mr.tk(ctx))
        right = g.x1 - lw - g.bl(4)
        if count:
            cw = D.text_w(mf, f"{idx}/{count}", mr.tk(ctx))
            right -= cw + g.bl(3)
            D.draw_text(c, f"{idx}/{count}", right + g.bl(1.5), base, mf, ctx.pal("muted"), align="left",
                        tracking=mr.tk(ctx))
            right -= g.bl(1.5)
        if right > x:
            y = base - D.cap_height(kf) / 2
            c.drawRect(skia.Rect(x, y - g.bl(0.125), x + (right - x - g.gutter) * SETTLE(u), y + g.bl(0.125)),
                       D.paint(ctx.pal(ink, 0.24)))
        # LIVE + a dot that pulses on every beat
        fr = ctx.local_beat % 1.0
        pulse = math.exp(-fr * 5.0)
        dy = base - D.cap_height(mf) / 2
        dx = g.x1 - lw - g.bl(2)
        c.drawCircle(dx, dy, g.bl(0.75) * (1 + 0.6 * pulse), D.paint(ctx.pal("accent", 0.25 + 0.35 * pulse)))
        c.drawCircle(dx, dy, g.bl(0.6), D.paint(ctx.pal("accent")))
        D.draw_text(c, live, g.x1, base, mf, ctx.pal(ink), align="right", tracking=mr.tk(ctx))

    # -- heading ---------------------------------------------------------
    def heading_text(self) -> str:
        return str(self.p.get("heading") or self.p.get("title") or "")

    def heading(self, ctx: Ctx, g: L.Grid, text: str, at: float = 0.0, top: float | None = None,
                max_lines: int = 2) -> float:
        """Title-role heading (126 px, display face, wght 700) rising line by line; returns the
        last baseline (or the top when there is no heading)."""
        top = self.head_top(g) if top is None else top
        if not text:
            return top
        r = T["title"]
        tk = r.tk(ctx, "display")
        f = r.font(ctx, weight=700)
        lines = D.wrap(f, text, g.content_w, tk, style="balance")
        if len(lines) > max_lines:
            if max_lines == 1:  # one line: set the role a touch smaller rather than break it
                k = g.content_w / max(D.text_w(f, text, tk), 1.0)
                f = r.font(ctx, weight=700, size=f.getSize() * k * 0.995)
                lines = [text]
            else:
                f = L.fit_role(ctx, r, text, g.content_w, weight=700)
                lines = D.wrap(f, text, g.content_w, tk, style="balance")[:max_lines]
        cap = D.cap_height(f)
        pitch = f.getSize() * r.leading
        ink = ctx.pal(ctx.pal.on("bg"))
        base = top + cap
        for i, ln in enumerate(lines):
            u = 1.0 if self.cut_in and at <= 0 else _bu(ctx, at + i / 16, 0.5)
            _rise(ctx, ln, g.x0, base + i * pitch, f, ink, u, tk)
        return base + (len(lines) - 1) * pitch

    # -- shared chart frame ----------------------------------------------
    def grid_lines(self, ctx: Ctx, g: L.Grid, r: skia.Rect, u: float, labels: list[str],
                   old: list[str] | None = None, roll: float = 1.0) -> None:
        """Five horizontal gridlines over the full content width (far layer) with right-aligned
        mono labels above them; labels roll from `old` (axis hand-off) or rise in."""
        c = ctx.c
        mr = T["meta"]
        mf = mr.font(ctx, weight=500)
        with ctx.layer(1.25):
            for k in range(5):
                y = r.top() + r.height() * k / 4
                w = (g.x1 - g.x0) * SETTLE(E.clamp(u * 1.4 - k * 0.1))
                if w > 0.5:
                    col = ctx.pal("ink", 0.22 if k == 4 else 0.1)
                    c.drawRect(skia.Rect(g.x0, y - g.bl(0.125), g.x0 + w, y + g.bl(0.125)), D.paint(col))
                if k < len(labels):
                    lab = labels[k]
                    base = y - g.bl(1.5)
                    if old is not None and k < len(old):
                        odometer(ctx, old[k], lab, roll, g.x1, base, mf, ctx.pal("muted"), align="right")
                    else:
                        _rise(ctx, lab, g.x1, base, mf, ctx.pal("muted"), E.clamp(u * 1.5 - k * 0.08),
                              align="right")


def draw_tape(ctx: Ctx, g: L.Grid, items: list[dict], big: bool = False) -> None:
    """Market tape on the bottom safe line, scrolling on film time (continuous across cuts)."""
    c = ctx.c
    mr = T["meta"]
    f = mono(ctx, mr.px(g) * (1.6 if big else 1.0), 600)
    top = g.safe_bottom + g.bl(1)
    h = g.bl(10 if big else 8)
    mid = top + h / 2
    base = mid + D.cap_height(f) / 2
    c.drawRect(skia.Rect(-ctx.W, top, 2 * ctx.W, top + h), D.paint(ctx.pal("bg2", 0.85)))
    c.drawRect(skia.Rect(-ctx.W, top, 2 * ctx.W, top + g.bl(0.25)), D.paint(ctx.pal("ink", 0.14)))
    c.drawRect(skia.Rect(-ctx.W, top + h - g.bl(0.25), 2 * ctx.W, top + h), D.paint(ctx.pal("ink", 0.14)))
    tk = 0.02
    gap = g.bl(6)
    tri_s = D.cap_height(f) * 0.8
    cells = []
    for it in items:
        sym = str(it.get("sym", "???"))
        price = fmt(float(it.get("price", 0)), int(it.get("decimals", 2)), it.get("prefix", ""))
        ch = float(it.get("change", 0))
        pct = f"{abs(ch):.2f}%"
        cells.append((sym, price, ch >= 0, pct))
    widths = [D.text_w(f, s, tk) + g.bl(1.5) + D.text_w(f, p, tk) + g.bl(2) + tri_s + g.bl(1) + D.text_w(f, q, tk)
              for s, p, _, q in cells]
    unit = sum(widths) + gap * len(cells)
    if unit <= 0:
        return
    speed = g.px(150)                       # px per second of film time
    x = -((ctx.t * speed) % unit) - unit
    while x < ctx.W + unit:
        for (s, p, up, q), w in zip(cells, widths):
            if -w < x < ctx.W:
                xx = x
                xx += D.draw_text(c, s, xx, base, f, ctx.pal("ink"), align="left", tracking=tk) + g.bl(1.5)
                xx += D.draw_text(c, p, xx, base, f, ctx.pal("ink", 0.62), align="left", tracking=tk) + g.bl(2)
                col = ctx.pal("accent" if up else "neg")
                tri(c, xx + tri_s / 2, mid, tri_s, up, col)
                xx += tri_s + g.bl(1)
                D.draw_text(c, q, xx, base, f, col, align="left", tracking=tk)
            x += w + gap
    nf = T["meta"].font(ctx, weight=500)
    D.draw_text(c, "ILLUSTRATIVE DATA · NOT FINANCIAL ADVICE", g.x0, top + h + g.bl(5), nf,
                ctx.pal("muted", 0.9), align="left", tracking=T["meta"].tk(ctx))


# ---------------------------------------------------------------------- counter

@register("counter")
class Counter(DataScene):
    """Hero figure on the anchor counting up on 16ths over `count` (odometer roll, weight 300 ->
    800) and landing on the downbeat after it; label, a delta pill on the landing and a
    full-width sparkline (published as "spark" for a match cut into a line chart)."""
    defaults = {"value": None, "from": 0, "decimals": 0, "prefix": "", "suffix": "", "label": "", "kicker": "",
                "heading": "", "delta": None, "delta_note": "", "count": "2b", "first": "0b", "spark": True,
                "seed": 3, "color": "ink", "delay": None}

    def _spark(self) -> list[float]:
        sp = self.p["spark"]
        if sp is False or sp is None:
            return []
        if sp is True:
            return series_from({"seed": self.p["seed"], "n": 30, "drift": 0.01, "vol": 0.04})
        return series_from(sp, 30)

    def _value(self) -> float:
        if self.p["value"] is not None:
            return float(self.p["value"])
        data = self._spark()
        return data[-1] if data else 1000.0

    def _delta(self) -> str:
        d = self.p["delta"]
        if d is None:
            data = self._spark()
            return fmt(change_pct(data), 1, "", "%", sign=True) if data else ""
        return str(d)

    def _times(self) -> tuple[float, float]:
        first = self.p["delay"] if self.p.get("delay") is not None else self.p["first"]
        f_b = float(first) / max(self.beat_len, 1e-6)
        c_b = self.b("count", 2.0)
        return f_b, c_b

    def cues(self) -> list[Cue]:
        f_b, c_b = self._times()
        land = math.ceil(f_b + c_b - 1e-6)
        out = [_sfx(self.bt(k), "tick_hi" if k == math.floor(f_b) else "tick", -7.0)
               for k in range(int(math.floor(f_b)), land)]
        out += [_sfx(self.bt(land), "ding", -3.0), Cue("hit", self.bt(land), data={"strength": 0.4}),
                Cue("cue", self.bt(land), data={"music": "hit"})]
        if self._spark():
            out.append(_sfx(self.bt(land + 1), "blip_up", -6.0))
        return out

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        lb = ctx.local_beat
        f_b, c_b = self._times()
        land = math.ceil(f_b + c_b - 1e-6)
        self.heading(ctx, g, self.heading_text(), max_lines=1)
        final = self._value()
        v0 = float(self.p["from"])
        dec, pre, suf = int(self.p["decimals"]), str(self.p["prefix"]), str(self.p["suffix"])
        a, b, ur = stepped(lb, f_b, c_b)
        sa, d0, d1 = padded(E.lerp(v0, final, a), final, dec, pre, suf)
        sb, _, _ = padded(E.lerp(v0, final, b), final, dec, pre, suf)
        n = len(padded(final, final, dec, pre, suf)[0])
        adv_em = 0.6
        size = min(g.px(300), g.content_w / (n * adv_em))
        prog = E.clamp((lb - f_b) / max(c_b, 1e-6))
        wght = int(300 + 500 * ENTER(prog))
        f = mono(ctx, size, wght)
        cap = D.cap_height(f)
        base = g.anchor_y + cap
        landed = lb >= land
        col = ctx.pal(self.p["color"]) if self.p["color"] in ctx.theme else ctx.pal("ink")
        odometer(ctx, sa, sb, ur, g.x0, base, f, col, dim=ctx.pal("muted", 0.45), dim_range=(d0, d1))
        # landing underline: the figure's width, accent, drawn on the downbeat
        fw = D.text_w(f, "0") * len(sb)
        ul = _bu(ctx, land, 0.5)
        if ul > 0:
            c.drawRect(skia.Rect(g.x0, base + g.bl(3), g.x0 + fw * SETTLE(ul), base + g.bl(3.75)),
                       D.paint(ctx.pal("accent")))
        y = base + g.bl(13)
        if self.p["label"]:
            bf = T["body"].font(ctx)
            _rise(ctx, str(self.p["label"]), g.x0, y, bf, ctx.pal("ink", 0.72), _bu(ctx, 0.25, 0.5),
                  T["body"].tk(ctx))
        # delta pill: pops on the landing beat (back 4 %), sign colour, >= 40 px figures
        delta = self._delta()
        pill_top = y + g.bl(5)
        pill_h = g.bl(10)
        if delta:
            neg = delta.strip().startswith(("-", MINUS))
            key = "neg" if neg else "accent"
            df = mono(ctx, T["caption"].px(g) * 1.05, 700)
            txt = delta.replace("-", MINUS)
            tw = D.text_w(df, txt)
            pw = tw + g.bl(4) + D.cap_height(df) + g.bl(1.5)
            u = _bu(ctx, land, 0.5)
            if u > 0:
                e = POP(u)
                c.save()
                c.translate(g.x0, pill_top + pill_h / 2)
                c.scale(max(e, 0.0), max(e, 0.0))
                D.rrect(c, 0, -pill_h / 2, pw, pill_h, pill_h / 2, D.paint(ctx.pal(key)))
                tri(c, g.bl(2) + D.cap_height(df) / 2, 0, D.cap_height(df) * 0.9, not neg, ctx.pal("ink_dark"))
                D.draw_text(c, txt, g.bl(2) + D.cap_height(df) + g.bl(1.5), D.cap_height(df) / 2, df,
                            ctx.pal("ink_dark"), align="left")
                c.restore()
            if self.p["delta_note"]:
                mf = T["meta"].font(ctx, weight=500)
                _rise(ctx, T["meta"].text(str(self.p["delta_note"]).upper()), g.x0 + pw + g.gutter,
                      pill_top + pill_h / 2 + D.cap_height(mf) / 2, mf, ctx.pal("muted"), _bu(ctx, land + 0.25, 0.5),
                      T["meta"].tk(ctx))
        # sparkline: full content width from the delta row to the bottom of the plot band
        data = self._spark()
        if data:
            r = skia.Rect(g.x0, pill_top + pill_h + g.bl(8), g.x1, self.plot_rect(g).bottom())
            self.draw_spark(ctx, g, data, r, prog, lb >= land)
            ctx.publish("spark", r, kind="line")
        self.chrome(ctx, g)

    def draw_spark(self, ctx: Ctx, g: L.Grid, data: list[float], r: skia.Rect, prog: float, landed: bool) -> None:
        c = ctx.c
        with ctx.layer(1.25):
            c.drawRect(skia.Rect(r.left(), r.bottom() - g.bl(0.125), r.right(), r.bottom() + g.bl(0.125)),
                       D.paint(ctx.pal("ink", 0.22)))
        upto = SETTLE(E.clamp(prog * 1.05 + 0.08)) * (len(data) - 1)
        path, pts = spark_path(data, r, upto=upto)
        if len(pts) < 2:
            return
        sh = D.linear_grad(0, r.top(), 0, r.bottom(), [ctx.pal("accent", 0.30), ctx.pal("accent", 0.0)])
        c.drawPath(area_under(pts, r.bottom()), D.paint(0, shader=sh))
        c.drawPath(path, D.paint(ctx.pal("accent", 0.35), stroke=g.bl(1.5), blur=g.bl(1)))
        c.drawPath(path, D.paint(ctx.pal("accent"), stroke=g.bl(0.6)))
        hx, hy = pts[-1]
        c.drawCircle(hx, hy, g.bl(1.6), D.paint(ctx.pal("accent", 0.35)))
        c.drawCircle(hx, hy, g.bl(0.9), D.paint(ctx.pal("ink")))
        mf = T["meta"].font(ctx, weight=500)
        lo, hi = min(data), max(data)
        dec, pre = int(self.p["decimals"]), str(self.p["prefix"])
        u = E.clamp(prog * 2)
        _rise(ctx, f"30D HIGH {fmt(hi, dec, pre)}", r.left(), r.top() - g.bl(2), mf, ctx.pal("muted"), u,
              T["meta"].tk(ctx))
        _rise(ctx, f"LOW {fmt(lo, dec, pre)}", r.right(), r.top() - g.bl(2), mf, ctx.pal("muted"), u,
              T["meta"].tk(ctx), align="right")


# ---------------------------------------------------------------------- line

@register("line")
class LineChart(DataScene):
    """Line chart on the full plot band. With a "spark" hand-off the counter's sparkline grows
    into the plot; otherwise the line draws in over the first beat. A read cursor then travels
    the series (in_out_quint over `draw`), lighting the area behind it; event callouts land on
    the beat after the cursor passes them; the change figure lands when the read ends."""
    defaults = {"kicker": "", "title": "", "heading": "", "series": {"seed": 11, "n": 60}, "prefix": "$",
                "suffix": "", "decimals": 0, "draw": "3b", "first": "1b", "events": [], "x_labels": [],
                "change": True, "dek": ""}

    def _data(self) -> list[float]:
        return series_from(self.p["series"])

    def _range(self, data: list[float]) -> tuple[float, float, list[float]]:
        lo_n, hi_n, step = axis(min(data), max(data), 0.08, 0.04)
        return lo_n, hi_n, [hi_n - step * k for k in range(5)]

    def _cursor(self, lb: float) -> float:
        """Fraction 0..1 of the series read at local beat lb."""
        return MOVE(E.clamp((lb - self.b("first", 1.0)) / max(self.b("draw", 3.0), 1e-6)))

    def _pass_beat(self, i: int, n: int) -> float:
        """Whole beat on which event i's callout lands (the beat after the cursor reaches it)."""
        f0, d = self.b("first", 1.0), self.b("draw", 3.0)
        target = i / max(n - 1, 1)
        lo, hi = 0.0, 1.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if MOVE(mid) < target:
                lo = mid
            else:
                hi = mid
        return float(math.ceil(f0 + hi * d - 1e-6))

    def heading_text(self) -> str:
        text = super().heading_text()
        data = self._data()
        if "{" in text and data:
            text = text.replace("{change}", fmt(change_pct(data), 1, "", "%", sign=True))
            text = text.replace("{last}", fmt(data[-1], int(self.p["decimals"]), str(self.p["prefix"])))
        return text

    def cues(self) -> list[Cue]:
        data = self._data()
        n = len(data)
        f0, d = self.b("first", 1.0), self.b("draw", 3.0)
        out = [_sfx(0.0, "snap", -6.0)] + ([_sfx(self.bt(math.ceil(f0)), "data_blip", -5.0)] if math.ceil(f0) > 0 else [])
        seen = set()
        for ev in self.p["events"]:
            bb = self._pass_beat(int(ev.get("i", 0)), n)
            if bb not in seen:
                seen.add(bb)
                out.append(_sfx(self.bt(bb), "pop", -4.0))
        end = math.ceil(f0 + d - 1e-6)
        if self.p["change"]:
            out += [_sfx(self.bt(end), "blip_up", -3.0), Cue("hit", self.bt(end), data={"strength": 0.35})]
        return out

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        lb = ctx.local_beat
        self.heading(ctx, g, self.heading_text(), max_lines=1)
        data = self._data()
        n = len(data)
        plot = self.plot_rect(g)
        lo, hi, ticks = self._range(data)
        dec, pre, suf = int(self.p["decimals"]), str(self.p["prefix"]), str(self.p["suffix"])
        labels = [fmt(v, dec, "", suf) for v in ticks]
        self.grid_lines(ctx, g, plot, _bu(ctx, 0.0, 1.0), labels)
        ctx.publish("plot", plot, labels=labels)
        dr = skia.Rect(plot.left(), plot.top(), plot.right() - self.gutter_w(g), plot.bottom())
        handed = ctx.handed_off("spark")
        if handed:
            r = ctx.handoff("spark", dr, dur="1b", ease=MOVE)
            # the counter drew the spark fitted to its own min..max; morph that fit to the axis range
            k = MOVE(_bu(ctx, 0.0, 1.0))
            lo_k, hi_k = E.lerp(min(data), lo, k), E.lerp(max(data), hi, k)
            upto = None
        else:
            r = dr
            lo_k, hi_k = lo, hi
            upto = SETTLE(_bu(ctx, 0.0, 1.0)) * (n - 1)
        path, pts = spark_path(data, r, lo_k, hi_k, upto)
        # x labels
        xf = T["meta"].font(ctx, weight=500)
        xl = list(self.p["x_labels"])
        for i, lab in enumerate(xl):
            xx = dr.left() + dr.width() * i / max(len(xl) - 1, 1)
            al = "left" if i == 0 else "right" if i == len(xl) - 1 else "center"
            _rise(ctx, str(lab).upper(), xx, plot.bottom() + g.bl(5), xf, ctx.pal("muted"), _bu(ctx, 0.25 + i / 16, 0.5),
                  T["meta"].tk(ctx), align=al)
        if len(pts) < 2:
            self.chrome(ctx, g)
            return
        cur = self._cursor(lb)
        fi = cur * (n - 1)
        # area: dim under the whole line, lit behind the cursor
        sh = D.linear_grad(0, r.top(), 0, r.bottom(), [ctx.pal("accent", 0.12), ctx.pal("accent", 0.0)])
        c.drawPath(area_under(pts, r.bottom()), D.paint(0, shader=sh))
        if cur > 0:
            _, lit = spark_path(data, r, lo_k, hi_k, fi)
            if len(lit) > 1:
                sh2 = D.linear_grad(0, r.top(), 0, r.bottom(), [ctx.pal("accent", 0.38), ctx.pal("accent", 0.02)])
                c.drawPath(area_under(lit, r.bottom()), D.paint(0, shader=sh2))
        c.drawPath(path, D.paint(ctx.pal("accent", 0.3), stroke=g.bl(1.6), blur=g.bl(1)))
        c.drawPath(path, D.paint(ctx.pal("accent"), stroke=g.bl(0.65)))
        # events: point ring + leader + label pill, in front
        ev_list = [ev for ev in self.p["events"] if 0 <= int(ev.get("i", 0)) < n]
        X = lambda i: r.left() + r.width() * i / max(n - 1, 1)  # noqa: E731
        Y = lambda v: r.bottom() - r.height() * (v - lo_k) / max(hi_k - lo_k, 1e-9)  # noqa: E731
        with ctx.layer(0.85):
            for ev in ev_list:
                i = int(ev["i"])
                u = _bu(ctx, self._pass_beat(i, n), 0.5)
                if u <= 0:
                    continue
                self._callout(ctx, g, X(i), Y(data[i]), str(ev.get("text", "")), u, r)
        # cursor
        if 0 < cur:
            hx = X(fi)
            k = min(int(fi), n - 2)
            hv = E.lerp(data[k], data[k + 1], fi - k)
            hy = Y(hv)
            a = 1.0 - E.in_quart(E.clamp((lb - math.ceil(self.b("first", 1.0) + self.b("draw", 3.0) - 1e-6)) / 0.5))
            c.drawRect(skia.Rect(hx - g.bl(0.125), r.top(), hx + g.bl(0.125), r.bottom()), D.paint(ctx.pal("ink", 0.35 * a)))
            c.drawCircle(hx, hy, g.bl(2.2), D.paint(ctx.pal("accent", 0.3)))
            c.drawCircle(hx, hy, g.bl(1.0), D.paint(ctx.pal("ink")))
            bf = mono(ctx, T["caption"].px(g), 700)
            txt = fmt(hv, dec, pre, suf)
            bw = D.text_w(bf, txt) + g.bl(3)
            bh = g.bl(7)
            bx = hx + g.bl(2) if hx + g.bl(2) + bw < g.x1 else hx - g.bl(2) - bw
            by = hy - g.bl(2) - bh if hy - bh - g.bl(2) > r.top() else hy + g.bl(2)
            if a > 0.01:
                D.rrect(c, bx, by, bw, bh, g.bl(1), D.paint(ctx.pal("accent", a)))
                D.draw_text(c, txt, bx + bw / 2, by + bh / 2, bf, ctx.pal("ink_dark", a), valign="middle")
        # the change figure lands when the read ends
        end = math.ceil(self.b("first", 1.0) + self.b("draw", 3.0) - 1e-6)
        if self.p["change"]:
            u = _bu(ctx, end, 0.5)
            if u > 0:
                ch = change_pct(data)
                key = "accent" if ch >= 0 else "neg"
                cf = mono(ctx, T["title"].px(g), 800)
                mf = T["meta"].font(ctx, weight=600)
                y0 = plot.top() + g.bl(6)
                _rise(ctx, f"{len(data)}-DAY CHANGE", plot.left(), y0, mf, ctx.pal("muted"), u, T["meta"].tk(ctx))
                _rise(ctx, fmt(ch, 1, "", "%", sign=True), plot.left(), y0 + g.bl(3) + D.cap_height(cf), cf,
                      ctx.pal(key), u)
        if self.p["dek"]:
            mf = T["meta"].font(ctx, weight=500)
            _rise(ctx, str(self.p["dek"]).upper(), plot.left(), plot.top() - g.bl(2), mf, ctx.pal("muted"),
                  _bu(ctx, 0.0, 0.5), T["meta"].tk(ctx))
        self.chrome(ctx, g)

    def _callout(self, ctx: Ctx, g: L.Grid, px: float, py: float, text: str, u: float, r: skia.Rect) -> None:
        c = ctx.c
        e = ENTER(u)
        c.drawCircle(px, py, g.bl(2.6) * e, D.paint(ctx.pal("ink", 0.9), stroke=g.bl(0.4)))
        cf = T["caption"].font(ctx, weight=600, face="display")
        tw = D.text_w(cf, text)
        pw, ph = tw + g.bl(4), g.bl(8)
        above = py - r.top() > r.height() * 0.45
        ly = py - g.bl(16) - ph if above else py + g.bl(16)
        ly = min(max(ly, r.top()), r.bottom() - ph)
        lx = min(max(px - pw / 2, g.x0), g.x1 - pw)
        y_from = py - g.bl(2.8) if above else py + g.bl(2.8)
        y_to = ly + ph if above else ly
        c.drawLine(px, y_from, px, E.lerp(y_from, y_to, e), D.paint(ctx.pal("ink", 0.7), stroke=g.bl(0.3)))
        w = pw * ENTER(E.clamp(u * 1.6 - 0.3))
        if w > 1:
            c.save()
            c.clipRect(skia.Rect(lx, ly, lx + w, ly + ph))
            D.rrect(c, lx, ly, pw, ph, g.bl(1), D.paint(ctx.pal("ink")))
            D.draw_text(c, text, lx + g.bl(2), ly + ph / 2, cf, ctx.pal("ink_dark"), align="left", valign="middle")
            c.restore()


# ---------------------------------------------------------------------- candles

@register("candles")
class Candles(DataScene):
    """Candlestick chart on the plot band. History sweeps in over the first beat; the last `live`
    candles then form one per beat with real OHLC motion (open, a path through the low/high to the
    close, wick tracking the extremes so far). The last full candle closes above `level` on its
    beat (impact, music hit, the level flips to the accent); the next candle keeps forming."""
    defaults = {"kicker": "", "title": "", "heading": "", "seed": 21, "n": 30, "start": 100.0, "end": None,
                "vol": 0.025, "drift": 0.003, "ohlc": None, "level": None, "first": "0b", "live": None,
                "ma": 7, "prefix": "$", "decimals": 2, "callout": "", "reveal": None}

    # -- data ------------------------------------------------------------
    def _ohlc(self) -> list[tuple[float, float, float, float]]:
        if self.p["ohlc"]:
            return [tuple(map(float, r)) for r in self.p["ohlc"]]  # type: ignore[misc]
        rnd = random.Random(int(self.p["seed"]))
        n = max(4, int(self.p["n"]))
        start = float(self.p["start"])
        vol = float(self.p["vol"])
        end = self.p["end"]
        if end is None:  # v0.1 behaviour: a free random walk with drift
            v, closes = start, [start]
            for _ in range(n):
                v *= 1 + float(self.p["drift"]) + rnd.gauss(0, vol)
                closes.append(v)
        else:            # a Brownian bridge from start to end
            w = [0.0]
            for _ in range(n):
                w.append(w[-1] + rnd.gauss(0, 1))
            a, z = math.log(start), math.log(float(end))
            closes = [math.exp(a + (z - a) * k / n + vol * (w[k] - w[n] * k / n)) for k in range(n + 1)]
        level = self.p["level"]
        if level is not None and end is not None and float(end) > float(level):
            cap = float(level) * 0.994
            closes = [min(v, cap) if k < n - 1 else v for k, v in enumerate(closes)]
            # the last full candle (n - 2) closes through the level; the forming one runs on to `end`
            closes[n - 1] = max(closes[n - 1], float(level) + (float(end) - float(level)) * 0.6)
        out = []
        for i in range(n):
            o, cl = closes[i], closes[i + 1]
            h = max(o, cl) * (1 + abs(rnd.gauss(0, vol * 0.45)))
            lo = min(o, cl) * (1 - abs(rnd.gauss(0, vol * 0.45)))
            if level is not None and end is not None and i < n - 2:
                h = min(h, float(level) * 0.997)
            out.append((o, h, lo, cl))
        return out

    def _live(self) -> int:
        v = self.p.get("live")
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 3

    def _plan(self) -> tuple[int, int, float, float]:
        """(n history, n live, first beat, sweep beats)."""
        data = self._ohlc()
        n = len(data)
        live = min(self._live(), max(n - 2, 0))
        has_forming = 1 if n - live - 1 > 0 else 0
        hist = n - live - has_forming
        return hist, live, self.b("first", 0.0), 1.0

    def cues(self) -> list[Cue]:
        hist, live, f0, sweep = self._plan()
        out = [_sfx(self.bt(f0), "data_blip", -5.0)]
        for k in range(live):
            out.append(_sfx(self.bt(f0 + sweep + k), "tick", -5.0))
        brk = f0 + sweep + live
        out += [_sfx(self.bt(brk), "impact", -2.0), Cue("hit", self.bt(brk), data={"strength": 0.6}),
                Cue("cue", self.bt(brk), data={"music": "hit"})]
        return out

    @staticmethod
    def _path_price(o: float, h: float, lo: float, cl: float, u: float, seed: int) -> tuple[float, float, float]:
        """Price at formation progress u (open -> first extreme -> second extreme -> close), and the
        low/high reached so far."""
        up = cl >= o
        first, second = (lo, h) if up else (h, lo)
        knots = [(0.0, o), (0.3, first), (0.72, second), (1.0, cl)]
        u = E.clamp(u)

        def price(x: float) -> float:
            for (x0, v0), (x1, v1) in zip(knots, knots[1:]):
                if x <= x1:
                    k = (x - x0) / max(x1 - x0, 1e-9)
                    base = E.lerp(v0, v1, E.in_out_sine(k))
                    wob = E.noise1(x * 9.0, seed) * 0.5
                    return min(max(base + wob * (h - lo) * 0.18 * math.sin(math.pi * x), lo), h)
            return cl
        p = price(u)
        seen = [price(u * j / 12) for j in range(13)]
        return p, min(seen), max(seen)

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        lb = ctx.local_beat
        self.heading(ctx, g, self.heading_text(), max_lines=1)
        data = self._ohlc()
        n = len(data)
        hist, live, f0, sweep = self._plan()
        brk = f0 + sweep + live
        dec, pre = int(self.p["decimals"]), str(self.p["prefix"])
        lo_all = min(r_[2] for r_ in data)
        hi_all = max(r_[1] for r_ in data)
        lo, hi, step = axis(lo_all, hi_all, 0.06, 0.02)
        labels = [fmt(hi - step * k, 0 if step >= 1 else dec) for k in range(5)]
        plot = self.plot_rect(g)
        # axis hand-off: the previous chart's plot frame and labels
        old = None
        src = ctx.film.handoff_source(self, "plot") if ctx.film is not None and ctx.scene is self else None
        if src is not None:
            old = list(src.get("data", {}).get("labels", [])) or None
            plot = ctx.handoff("plot", plot, dur="1/2b", ease=MOVE)
        # on a cut the old axis is already half-rolled, so the cut frame never shows stale prices
        roll = _bu(ctx, 0.0, 0.5)
        if self.cut_in:
            roll = 0.5 + 0.5 * roll
        self.grid_lines(ctx, g, plot, 1.0 if old else _bu(ctx, 0.0, 1.0), labels, old, roll)
        dr = skia.Rect(plot.left(), plot.top(), plot.right() - self.gutter_w(g), plot.bottom())
        slot = dr.width() / n
        Y = lambda v: dr.bottom() - dr.height() * (v - lo) / max(hi - lo, 1e-9)  # noqa: E731
        # resistance level
        level = self.p["level"]
        broke = lb >= brk
        if level is not None:
            ly = Y(float(level))
            u = _bu(ctx, f0 + 0.5, 1.0)
            if u > 0:
                col = ctx.pal("accent" if broke else "ink", 0.9 if broke else 0.45)
                p = D.paint(col, stroke=g.bl(0.3), cap_round=False)
                p.setPathEffect(skia.DashPathEffect.Make([g.bl(1.5), g.bl(1)], 0))
                c.drawLine(g.x0, ly, g.x0 + (g.x1 - g.x0) * SETTLE(u), ly, p)
                mf = T["meta"].font(ctx, weight=700)
                tag = (str(self.p["callout"]) or f"CLOSES ABOVE {fmt(float(level), 0)}") if broke else \
                    f"RESISTANCE {fmt(float(level), 0)}"
                _rise(ctx, tag.upper(), g.x0, ly - g.bl(1.5), mf, ctx.pal("accent" if broke else "muted"),
                      _bu(ctx, brk, 0.5) if broke else u, T["meta"].tk(ctx))
        # volume (bottom band of the plot, far layer)
        vb = dr.height() * 0.11
        with ctx.layer(1.12):
            for i, (o, h, l_, cl) in enumerate(data):
                vis = self._visible(i, hist, live, f0, sweep, lb)
                if vis <= 0:
                    continue
                x = dr.left() + slot * (i + 0.5)
                vh = vb * (0.25 + 0.75 * (0.5 + E.noise1(i * 1.37, int(self.p["seed"])) * 0.5))
                if i == hist + live - 1:
                    vh = vb * 1.25
                vh *= ENTER(E.clamp(vis))
                col = ctx.pal("accent" if cl >= o else "neg", 0.28)
                c.drawRect(skia.Rect(x - slot * 0.32, dr.bottom() - vh, x + slot * 0.32, dr.bottom()), D.paint(col))
        # candles
        last = None
        closes: list[float] = []
        for i, (o, h, l_, cl) in enumerate(data):
            vis = self._visible(i, hist, live, f0, sweep, lb)
            if vis <= 0:
                break
            x = dr.left() + slot * (i + 0.5)
            if i < hist:
                e = ENTER(E.clamp(vis))
                mid = (o + cl) / 2
                po, ph, pl, pc = mid + (o - mid) * e, mid + (h - mid) * e, mid + (l_ - mid) * e, mid + (cl - mid) * e
            else:
                pc, pl, ph = self._path_price(o, h, l_, cl, vis, int(self.p["seed"]) + i)
                po = o
            up = pc >= po
            col = ctx.pal("accent" if up else "neg")
            c.drawRect(skia.Rect(x - g.bl(0.2), Y(ph), x + g.bl(0.2), Y(pl)), D.paint(col))
            tb, bb = Y(max(po, pc)), Y(min(po, pc))
            c.drawRect(skia.Rect(x - slot * 0.34, tb, x + slot * 0.34, max(bb, tb + g.bl(0.3))), D.paint(col))
            last = (x, pc, po)
            closes.append(pc)
        # moving average over the shown closes
        m = int(self.p["ma"])
        if m > 1 and len(closes) > m:
            pts = []
            for i in range(m - 1, len(closes)):
                avg = sum(closes[i - m + 1:i + 1]) / m
                pts.append((dr.left() + slot * (i + 0.5), Y(avg)))
            c.drawPath(D.polyline(pts), D.paint(ctx.pal("ink", 0.5), stroke=g.bl(0.4)))
        # breakout ring
        if level is not None and broke and last is not None:
            u = _bu(ctx, brk, 1.0)
            bx = dr.left() + slot * (hist + live - 0.5)
            by = Y(data[hist + live - 1][3])
            rr = g.bl(2) + g.bl(10) * ENTER(u)
            c.drawCircle(bx, by, rr, D.paint(ctx.pal("accent", 0.8 * (1 - u)), stroke=g.bl(0.5)))
        # last price line + badge on the axis
        if last is not None:
            x, pc, _po = last
            y = Y(pc)
            up = pc >= data[0][0]
            p = D.paint(ctx.pal("ink", 0.5), stroke=g.bl(0.25), cap_round=False)
            p.setPathEffect(skia.DashPathEffect.Make([g.bl(1), g.bl(1)], 0))
            c.drawLine(x + slot * 0.5, y, g.x1, y, p)
            bf = mono(ctx, T["meta"].px(g) * 1.1, 700)
            txt = fmt(pc, 0 if pc >= 1000 else dec, pre)
            bw, bh = D.text_w(bf, txt) + g.bl(2.5), g.bl(6)
            with ctx.layer(0.9):
                D.rrect(c, g.x1 - bw, y - bh / 2, bw, bh, g.bl(0.75), D.paint(ctx.pal("accent" if up else "neg")))
                D.draw_text(c, txt, g.x1 - bw / 2, y, bf, ctx.pal("ink_dark"), valign="middle")
        ctx.publish("plot", plot, labels=labels)
        self.chrome(ctx, g)

    @staticmethod
    def _visible(i: int, hist: int, live: int, f0: float, sweep: float, lb: float) -> float:
        """Formation progress of candle i at local beat lb (history: 1/4-beat grow on a sweep;
        live: one beat each; the forming candle: half of its body over two beats)."""
        if i < hist:  # history is on the cut frame: the axis hand-off carries it in
            return 1.0
        k = i - hist
        if k < live:
            return E.clamp(lb - (f0 + sweep + k)) if lb >= f0 + sweep + k else -1.0
        at = f0 + sweep + live
        return E.clamp((lb - at) / 4.0) if lb >= at else -1.0


# ---------------------------------------------------------------------- bars

@register("bars")
class Bars(DataScene):
    """Horizontal bars over the full content width: tracks and labels on the cut, bars grow
    (out_expo over `grow`) on a `stagger` from `first` with their figures counting on 16ths;
    the previous-period marks and deltas land after the last bar, then the callout inside the
    highlighted bar."""
    defaults = {"kicker": "", "title": "", "heading": "", "items": [], "prefix": "", "suffix": "", "decimals": 0,
                "highlight": "", "horizontal": None, "stagger": "1/2b", "first": "1b", "grow": "1b", "callout": ""}

    def _t(self) -> tuple[float, float, float]:
        return self.b("first", 1.0), self.b("stagger", 0.5), self.b("grow", 1.0)

    def cues(self) -> list[Cue]:
        f0, st, gr = self._t()
        items = self.p["items"]
        out = []
        for i, it in enumerate(items):
            hl = it.get("label") == self.p["highlight"]
            out.append(_sfx(self.bt(f0 + i * st), "blip_up" if hl else "data_blip", -4.0 if hl else -7.0))
        if items:
            done = math.ceil(f0 + (len(items) - 1) * st + gr - 1e-6)
            if any("prev" in it for it in items):
                out.append(_sfx(self.bt(done), "tick_hi", -5.0))
            if self.p["callout"]:
                out += [_sfx(self.bt(done + 1), "pop", -4.0), Cue("hit", self.bt(done + 1), data={"strength": 0.3})]
        return out

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        lb = ctx.local_beat
        self.heading(ctx, g, self.heading_text(), max_lines=1)
        items = self.p["items"]
        if not items:
            self.chrome(ctx, g)
            return
        f0, st, gr = self._t()
        plot = self.plot_rect(g)
        n = len(items)
        pitch = plot.height() / n
        bh = min(pitch * 0.5, g.bl(13))
        vmax = max(float(i["value"]) for i in items) or 1.0
        dec, pre, suf = int(self.p["decimals"]), str(self.p["prefix"]), str(self.p["suffix"])
        lf = T["body"].font(ctx, weight=600, face="display")
        vf_px = T["body"].px(g)
        mf = T["meta"].font(ctx, weight=600)
        done = math.ceil(f0 + (n - 1) * st + gr - 1e-6)
        call_rect = None
        for i, it in enumerate(items):
            top = plot.top() + i * pitch + (pitch - bh - g.bl(8)) / 2
            bar_y = top + g.bl(8)
            lab_base = bar_y - g.bl(2)
            hl = it.get("label") == self.p["highlight"]
            key = it.get("color") or ("accent" if hl else "muted")
            # track on the cut (far layer), label rises in the first half beat
            with ctx.layer(1.12):
                c.drawRect(skia.Rect(g.x0, bar_y, g.x1, bar_y + bh), D.paint(ctx.pal("ink", 0.06)))
            D.draw_text(c, str(it.get("label", "")), g.x0, lab_base, lf, ctx.pal("ink"), align="left",
                        tracking=T["body"].tk(ctx, "display"))
            at = f0 + i * st
            u = E.clamp((lb - at) / max(gr, 1e-6))
            val = float(it["value"])
            w = g.content_w * val / vmax * ENTER(u)
            if w > 0.5:
                c.drawRect(skia.Rect(g.x0, bar_y, g.x0 + w, bar_y + bh), D.paint(ctx.pal(key)))
            # figure: counts on 16ths with the bar, right-aligned on the grid edge
            a, b, ur = stepped(lb, at, gr)
            wght = int(400 + 400 * ENTER(u))
            vf = mono(ctx, vf_px, wght)
            sa = fmt(val * a, dec, pre, suf)
            sb = fmt(val * b, dec, pre, suf)
            if lb >= at - 0.02:
                odometer(ctx, sa, sb, ur, g.x1, lab_base, vf, ctx.pal("ink" if not hl else "accent"), align="right")
            # previous period: marker on the bar + signed delta after the label
            if "prev" in it:
                pu = _bu(ctx, done, 0.5)
                if pu > 0:
                    pv = float(it["prev"])
                    px = g.x0 + g.content_w * pv / vmax
                    mk = ENTER(pu)
                    c.drawRect(skia.Rect(px - g.bl(0.3), bar_y - g.bl(1.2) * mk, px + g.bl(0.3), bar_y + bh + g.bl(1.2) * mk),
                               D.paint(ctx.pal("ink", 0.85)))
                    dv = val - pv
                    dtxt = fmt(dv, 1, "", "", sign=True)
                    lw = D.text_w(lf, str(it.get("label", "")), T["body"].tk(ctx, "display"))
                    dx = g.x0 + lw + g.bl(3)
                    tri(c, dx + g.bl(1), lab_base - D.cap_height(mf) / 2, D.cap_height(mf) * 0.85 * mk, dv >= 0,
                        ctx.pal("accent" if dv >= 0 else "neg"))
                    _rise(ctx, dtxt + " PTS", dx + g.bl(3), lab_base, mf, ctx.pal("accent" if dv >= 0 else "neg"), pu,
                          T["meta"].tk(ctx))
            if hl:
                call_rect = skia.Rect(g.x0, bar_y, g.x0 + g.content_w * val / vmax, bar_y + bh)
        # legend for the previous-period marks, top right of the band
        if any("prev" in it for it in items):
            pu = _bu(ctx, done, 0.5)
            _rise(ctx, "│ = 30 DAYS AGO", g.x1, plot.top() - g.bl(2), mf, ctx.pal("muted"), pu,
                  T["meta"].tk(ctx), align="right")
        # callout inside the highlighted bar
        if self.p["callout"] and call_rect is not None:
            u = _bu(ctx, done + 1, 0.5)
            if u > 0:
                cf = T["caption"].font(ctx, weight=700, face="display")
                txt = str(self.p["callout"])
                c.save()
                c.clipRect(skia.Rect(call_rect.left(), call_rect.top(), call_rect.left() + call_rect.width() * ENTER(u),
                                     call_rect.bottom()))
                D.draw_text(c, txt, call_rect.left() + g.bl(3), call_rect.centerY(), cf, ctx.pal("ink_dark"),
                            align="left", valign="middle")
                c.restore()
        ctx.publish("plot", plot)
        self.chrome(ctx, g)


# ---------------------------------------------------------------------- stat grid

@register("stat_grid")
class StatGrid(DataScene):
    """2 x 2 (or 1 x N) cards filling the plot band: cards cascade in on 16ths from `first`,
    figures count on 16ths and land together on the downbeat after `count`; each card carries a
    signed delta and a mini spark or gauge. The first card is published as "card0"."""
    defaults = {"kicker": "", "title": "", "heading": "", "items": [], "count": "2b", "stagger": "1/4b",
                "first": "0b"}

    def cues(self) -> list[Cue]:
        items = self.p["items"]
        if not items:
            return []
        f0, cnt = self.b("first", 0.0), self.b("count", 2.0)
        land = math.ceil(f0 + cnt - 1e-6)
        return [_sfx(self.bt(f0), "data_blip", -5.0), _sfx(self.bt(land), "ding", -4.0),
                Cue("hit", self.bt(land), data={"strength": 0.3})]

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        lb = ctx.local_beat
        self.heading(ctx, g, self.heading_text(), max_lines=1)
        items = self.p["items"]
        if not items:
            self.chrome(ctx, g)
            return
        f0, st, cnt = self.b("first", 0.0), self.b("stagger", 0.25), self.b("count", 2.0)
        land = math.ceil(f0 + cnt - 1e-6)
        plot = self.plot_rect(g)
        cols = 2 if (ctx.portrait or len(items) == 4) else len(items)
        rows = math.ceil(len(items) / cols)
        gap = g.gutter
        cw = (g.content_w - gap * (cols - 1)) / cols
        ch = (plot.height() + g.bl(5) - gap * (rows - 1)) / rows
        kr, mr = T["kicker"], T["meta"]
        kf = kr.font(ctx)
        for i, it in enumerate(items):
            r_, k = divmod(i, cols)
            x = g.x0 + k * (cw + gap)
            y = plot.top() + r_ * (ch + gap)
            at = f0 + i * st
            u = E.clamp((lb - at) / 0.5)
            if u <= 0:
                continue
            e = ENTER(u)
            rect = skia.Rect(x, y, x + cw, y + ch)
            if i == 0:
                rect = ctx.handoff("card0", rect) if ctx.handed_off("card0") else rect
                ctx.publish("card0", skia.Rect(x, y, x + cw, y + ch))
            yy = rect.top() + (1 - e) * g.bl(6)
            card = skia.Rect(rect.left(), yy, rect.right(), yy + rect.height())
            D.rrect(c, card.left(), card.top(), card.width(), card.height(), g.bl(2), D.paint(ctx.pal("bg2", 0.9 * e)))
            # border draws around from the top-left
            bp = skia.Path()
            bp.addRRect(skia.RRect.MakeRectXY(card, g.bl(2), g.bl(2)))
            c.drawPath(D.partial_path(bp, SETTLE(u)), D.paint(ctx.pal("ink", 0.16), stroke=g.bl(0.25)))
            pad = g.bl(4)
            lab = kr.text(str(it.get("label", "")))
            _rise(ctx, lab, card.left() + pad, card.top() + pad + D.cap_height(kf), kf, ctx.pal("muted"), u, kr.tk(ctx))
            val = float(it.get("value", 0))
            dec, pre, suf = int(it.get("decimals", 0)), str(it.get("prefix", "")), str(it.get("suffix", ""))
            final = fmt(val, dec, pre, suf)
            size = min(T["title"].px(g), (cw - 2 * pad) / (max(len(final), 3) * 0.6))
            a, b, ur = stepped(lb, at + 0.25, max(land - at - 0.25, 0.25))
            wght = int(400 + 400 * ENTER(E.clamp((lb - at) / max(land - at, 1e-6))))
            vf = mono(ctx, size, wght)
            vbase = card.top() + pad + D.cap_height(kf) + g.bl(5) + D.cap_height(vf)
            sa, d0, d1 = padded(val * a, val, dec, pre, suf)
            sb, _, _ = padded(val * b, val, dec, pre, suf)
            odometer(ctx, sa, sb, ur, card.left() + pad, vbase, vf, ctx.pal("ink"), dim=ctx.pal("muted", 0.4),
                     dim_range=(d0, d1))
            if it.get("delta"):
                d = str(it["delta"])
                neg = d.strip().startswith(("-", MINUS))
                key = "neg" if neg else "accent"
                df = mono(ctx, T["caption"].px(g), 700)
                du = _bu(ctx, land, 0.5)
                dy = vbase + g.bl(8)
                tri(c, card.left() + pad + D.cap_height(df) / 2, dy - D.cap_height(df) / 2, D.cap_height(df) * 0.9 * ENTER(du),
                    not neg, ctx.pal(key))
                _rise(ctx, d.replace("-", MINUS), card.left() + pad + D.cap_height(df) + g.bl(1.5), dy, df, ctx.pal(key), du)
            # mini viz on the card floor
            vr = skia.Rect(card.left() + pad, card.bottom() - pad - ch * 0.26, card.right() - pad, card.bottom() - pad)
            vu = E.clamp((lb - at - 0.25) / max(land - at, 0.5))
            if it.get("gauge") is not None:
                gmin, gmax = (it["gauge"] if isinstance(it["gauge"], list) else (0, 100))
                fr_ = E.clamp((val - gmin) / max(gmax - gmin, 1e-9))
                gy = vr.bottom() - g.bl(2)
                c.drawRect(skia.Rect(vr.left(), gy - g.bl(1), vr.right(), gy + g.bl(1)), D.paint(ctx.pal("ink", 0.1)))
                gw = vr.width() * fr_ * ENTER(vu)
                c.drawRect(skia.Rect(vr.left(), gy - g.bl(1), vr.left() + gw, gy + g.bl(1)), D.paint(ctx.pal("accent")))
                c.drawCircle(vr.left() + gw, gy, g.bl(1.8), D.paint(ctx.pal("ink")))
                mf = mr.font(ctx, weight=500)
                D.draw_text(c, str(int(gmin)), vr.left(), gy - g.bl(3), mf, ctx.pal("muted", e), align="left")
                D.draw_text(c, str(int(gmax)), vr.right(), gy - g.bl(3), mf, ctx.pal("muted", e), align="right")
            elif it.get("spark") is not None:
                data = series_from(it["spark"], 24)
                up = data[-1] >= data[0]
                path, pts = spark_path(data, vr, upto=SETTLE(vu) * (len(data) - 1))
                if len(pts) > 1:
                    key = "accent" if up else "neg"
                    sh = D.linear_grad(0, vr.top(), 0, vr.bottom(), [ctx.pal(key, 0.25), ctx.pal(key, 0.0)])
                    c.drawPath(area_under(pts, vr.bottom()), D.paint(0, shader=sh))
                    c.drawPath(path, D.paint(ctx.pal(key), stroke=g.bl(0.5)))
        self.chrome(ctx, g)


# ---------------------------------------------------------------------- ticker

@register("ticker")
class Ticker(DataScene):
    """Watchlist rows on the grid (symbol, name, price in mono, change pill), cascading in on
    32nds; prices re-quote on every beat with an odometer roll and a row flash in the sign colour.
    The first row can be born from the stat grid's "card0". A breadth bar (advancing vs
    declining) closes the band; the market tape runs underneath."""
    defaults = {"kicker": "", "title": "", "heading": "", "items": [], "tape": None, "speed": 160,
                "jitter": 0.003, "breadth": True}

    def cues(self) -> list[Cue]:
        items = self.p["items"]
        if not items:
            return []
        nb = int(round(self.dur / max(self.beat_len, 1e-6)))
        out = [_sfx(0.0, "snap", -6.0)]
        out += [_sfx(self.bt(k), "tick_hi", -8.0) for k in range(1, max(nb, 1))]
        return out

    def shutter(self, lt: float) -> float:
        """Re-quotes are appearance changes (flash, glyph swap), not motion: keep the shutter closed
        on the frames that straddle each beat so a centred shutter does not average the old and new
        quote into a smeared frame."""
        bl = max(self.beat_len, 1e-6)
        if lt >= bl - 1 / 60:
            ph = lt % bl
            if ph < 1 / 60 or ph > bl - 1 / 60:
                return 0.0
        return super().shutter(lt)

    def _quote(self, it: dict, i: int, k: int) -> tuple[float, float]:
        p0 = float(it.get("price", 100))
        if k <= 0:
            return p0, float(it.get("change", 0))
        rnd = random.Random(k * 131 + i * 7 + 3)
        drift = rnd.uniform(-1, 1) * float(self.p["jitter"])
        p = p0 * (1 + drift)
        return p, float(it.get("change", 0)) + drift * 100

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        lb = ctx.local_beat
        self.heading(ctx, g, self.heading_text(), max_lines=1)
        items = self.p["items"]
        if not items:
            self.chrome(ctx, g)
            return
        plot = self.plot_rect(g)
        n = len(items)
        breadth = bool(self.p["breadth"])
        band_h = plot.height() * (0.8 if breadth else 1.0)
        rh = band_h / n
        sf = T["body"].font(ctx, weight=700, face="display")
        nf = T["meta"].font(ctx, weight=500)
        pf_px = T["body"].px(g)
        cf = mono(ctx, T["caption"].px(g) * 0.9, 700)
        k = int(math.floor(lb))
        fr = lb - k
        pill_w = g.span_w(3)
        price_x = g.x1 - pill_w - g.gutter
        for i, it in enumerate(items):
            y = plot.top() + i * rh
            # on a cut the first row is already there (it is the hand-off card when there is one), so the
            # cut frame is never a bare heading; the rest cascade in behind it
            u = E.clamp((lb - i / 8) / 0.5)
            if i == 0 and self.cut_in:
                u = 1.0 if ctx.handed_off("card0") else max(u, 0.4)
            if u <= 0:
                continue
            e = ENTER(u)
            row = skia.Rect(g.x0, y, g.x1, y + rh - g.bl(1))
            if i == 0 and ctx.handed_off("card0"):
                row = ctx.handoff("card0", row, dur="1/2b")
            ox = (1 - e) * g.bl(8)
            p_old, _ = self._quote(it, i, k - 1)
            p_new, chg = self._quote(it, i, k)
            up = chg >= 0
            key = "accent" if up else "neg"
            flash = math.exp(-fr * 6) if k >= 1 else 0.0
            c.drawRect(row, D.paint(ctx.pal("bg2", 0.75 * e)))
            if flash > 0.01:
                c.drawRect(row, D.paint(ctx.pal("accent" if p_new >= p_old else "neg", 0.16 * flash)))
            c.drawRect(skia.Rect(row.left(), row.bottom() - g.bl(0.125), row.right(), row.bottom()),
                       D.paint(ctx.pal("ink", 0.12)))
            cy = row.centerY()
            pad = g.bl(3)
            dec = int(it.get("decimals", 2))
            pre = str(it.get("prefix", "$"))
            wght = int(500 + 300 * flash)
            pf = mono(ctx, pf_px, wght)
            ph = g.bl(7)
            # while row 0 is still the narrow hand-off card, stack symbol / price / pill like the card
            # it came from, and slide each piece into its row position as the card widens
            wt = E.clamp((row.width() / (g.x1 - g.x0) - 0.45) / 0.5)
            mix = lambda p0, p1: p0 + (p1 - p0) * wt  # noqa: E731
            p_txt = fmt(p_new, dec, pre)
            sym_y = mix(row.top() + pad + D.cap_height(sf), cy - g.bl(0.5))
            D.draw_text(c, str(it.get("sym", "???")), row.left() + pad + ox, sym_y, sf, ctx.pal("ink", e),
                        align="left")
            D.draw_text(c, str(it.get("name", "")).upper(), row.left() + pad + ox,
                        sym_y + g.bl(2) + D.cap_height(nf), nf, ctx.pal("muted", e), align="left",
                        tracking=T["meta"].tk(ctx))
            price_r = mix(row.left() + pad + D.text_w(pf, p_txt), min(price_x, row.right() - pill_w - g.gutter))
            price_y = mix(row.centerY() + g.bl(2) + D.cap_height(pf) / 2, cy + D.cap_height(pf) / 2)
            odometer(ctx, fmt(p_old, dec, pre), p_txt, E.clamp(fr / 0.5) if k >= 1 else 1.0,
                     price_r + ox * 0.5, price_y, pf, ctx.pal("ink", e), align="right")
            px0 = mix(row.left() + pad, row.right() - pill_w)
            pcy = mix(price_y + g.bl(4) + ph / 2, cy)
            D.rrect(c, px0, pcy - ph / 2, pill_w, ph, g.bl(1), D.paint(ctx.pal(key, e)))
            tri(c, px0 + g.bl(2.5), pcy, D.cap_height(cf) * 0.8, up, ctx.pal("ink_dark", e))
            D.draw_text(c, f"{abs(chg):.2f}%", px0 + pill_w - g.bl(2), pcy, cf, ctx.pal("ink_dark", e),
                        align="right", valign="middle")
            if i == 0:
                ctx.publish("row0", row)
        if breadth:
            adv = sum(1 for it in items if float(it.get("change", 0)) >= 0)
            dec_n = n - adv
            u = _bu(ctx, 1.0, 1.0)
            by = plot.top() + band_h + g.bl(6)
            mf = T["meta"].font(ctx, weight=700)
            _rise(ctx, f"ADVANCING {adv}", g.x0, by, mf, ctx.pal("accent"), u, T["meta"].tk(ctx))
            _rise(ctx, f"DECLINING {dec_n}", g.x1, by, mf, ctx.pal("neg"), u, T["meta"].tk(ctx), align="right")
            bt = by + g.bl(2)
            bh = g.bl(4)
            split = g.x0 + g.content_w * adv / max(n, 1)
            e = SETTLE(u)
            c.drawRect(skia.Rect(g.x0, bt, g.x0 + (split - g.x0) * e - g.bl(0.5), bt + bh), D.paint(ctx.pal("accent")))
            c.drawRect(skia.Rect(g.x1 - (g.x1 - split) * e + g.bl(0.5), bt, g.x1, bt + bh), D.paint(ctx.pal("neg")))
        self.chrome(ctx, g)
