"""Data / stats scenes: counter, bars, line, candles, ticker, stat_grid."""
from __future__ import annotations

import math
import random

import skia

from .. import draw as D
from .. import ease as E
from .base import Ctx, Cue, Scene, register
from .common import reveal_line


def fmt(v: float, decimals: int = 0, prefix: str = "", suffix: str = "", commas: bool = True) -> str:
    body = f"{abs(v):,.{decimals}f}" if commas else f"{abs(v):.{decimals}f}"
    return f"{'-' if v < 0 else ''}{prefix}{body}{suffix}"


def series_from(spec, n_default: int = 48) -> list[float]:
    """A list of numbers, or {"seed", "n", "start", "drift", "vol"} for a seeded random walk."""
    if isinstance(spec, list):
        return [float(v) for v in spec]
    spec = spec or {}
    rnd = random.Random(int(spec.get("seed", 7)))
    n = int(spec.get("n", n_default))
    v = float(spec.get("start", 100.0))
    drift, vol = float(spec.get("drift", 0.004)), float(spec.get("vol", 0.03))
    out = []
    for _ in range(n):
        v *= 1 + drift + rnd.gauss(0, vol)
        out.append(v)
    return out


def _title(ctx: Ctx, kicker: str, title: str, lt: float, y: float | None = None) -> float:
    s = ctx.s
    y = y if y is not None else ctx.H * (0.15 if ctx.portrait else 0.12)
    if kicker:
        kf = ctx.font("mono", 28 * s, 600)
        D.draw_text(ctx.c, kicker.upper(), ctx.margin, y, kf, ctx.pal("accent", E.clamp(lt / 0.3)), align="left", tracking=0.18)
        y += 76 * s
    if title:
        tf = ctx.font("display", 70 * s, 700)
        for i, ln in enumerate(D.wrap(tf, title, ctx.safe_w)):
            reveal_line(ctx, ln, ctx.margin, y + i * 80 * s, tf, ctx.pal("ink"), (lt - 0.05 * i) / 0.7)
        y += 80 * s * len(D.wrap(tf, title, ctx.safe_w))
    return y


@register("counter")
class Counter(Scene):
    """A big number rolling up to its value (mono, tabular), label, delta pill, sparkline."""
    defaults = {"value": 1000, "from": 0, "decimals": 0, "prefix": "", "suffix": "", "label": "", "kicker": "",
                "delta": "", "count": 1.4, "delay": 0.2, "spark": True, "seed": 3, "color": "ink"}

    def cues(self) -> list[Cue]:
        d, cnt = self.p["delay"], self.p["count"]
        out = [Cue("sfx", d + k * cnt / 12 * (1 - k / 24), data={"sound": "tick", "gain": -6}) for k in range(12)]
        out += [Cue("sfx", d + cnt, data={"sound": "ding"}), Cue("hit", d + cnt, data={"strength": 0.35})]
        return out

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        u = E.out_expo((lt - self.p["delay"]) / self.p["count"])
        v = E.lerp(float(self.p["from"]), float(self.p["value"]), u)
        final = fmt(float(self.p["value"]), self.p["decimals"], self.p["prefix"], self.p["suffix"])
        f = ctx.fit("mono", final, ctx.safe_w, 240 * s, 800)
        text = fmt(v, self.p["decimals"], self.p["prefix"], self.p["suffix"])
        landed = lt >= self.p["delay"] + self.p["count"]
        pop = 1 + (0.06 * math.exp(-(lt - self.p["delay"] - self.p["count"]) * 8) if landed else 0)
        cy = ctx.cy - 40 * s
        if self.p["kicker"]:
            kf = ctx.font("mono", 32 * s, 600)
            D.draw_text(c, self.p["kicker"].upper(), ctx.cx, cy - D.cap_height(f) - 70 * s, kf,
                        ctx.pal("muted", E.clamp(lt / 0.3)), tracking=0.2)
        c.save(); c.translate(ctx.cx, cy); c.scale(pop, pop)
        D.draw_text(c, text, 0, 0, f, ctx.pal(self.p["color"] if not landed else "accent" if self.p["color"] == "accent" else self.p["color"]),
                    valign="middle")
        c.restore()
        cap = D.cap_height(f)
        if self.p["label"]:
            lf = ctx.font("body", 48 * s, 600)
            reveal_line(ctx, self.p["label"], ctx.cx, cy + cap / 2 + 90 * s, lf, ctx.pal("ink", 0.9), (lt - 0.3) / 0.7, "center")
        if self.p["delta"]:
            df = ctx.font("mono", 40 * s, 700)
            dtxt = str(self.p["delta"])
            neg = dtxt.strip().startswith("-")
            a = E.clamp((lt - self.p["delay"] - self.p["count"] + 0.1) / 0.3)
            if a > 0:
                w = D.text_w(df, dtxt) + 60 * s
                y = cy + cap / 2 + 150 * s
                e = E.out_back(a)
                c.save(); c.translate(ctx.cx, y + 30 * s); c.scale(e, e)
                D.rrect(c, -w / 2, -36 * s, w, 72 * s, 36 * s, D.paint(ctx.pal("neg" if neg else "pos", 0.18)))
                D.draw_text(c, dtxt, 0, 0, df,
                            ctx.pal("neg" if neg else "pos"), valign="middle")
                c.restore()
        if self.p["spark"]:
            data = series_from({"seed": self.p["seed"], "n": 40, "drift": 0.01, "vol": 0.04})
            lo, hi = min(data), max(data)
            w, h = ctx.safe_w * 0.8, 110 * s
            x0, y0 = ctx.cx - w / 2, ctx.H * 0.78
            pts = [(x0 + i / (len(data) - 1) * w, y0 + h - (d - lo) / (hi - lo + 1e-9) * h) for i, d in enumerate(data)]
            path = D.polyline(pts)
            c.drawPath(D.partial_path(path, u), D.paint(ctx.pal("accent", 0.8), stroke=4 * s))


@register("bars")
class Bars(Scene):
    """Bar chart growing on a stagger with counting value labels and a highlighted bar."""
    defaults = {"kicker": "", "title": "", "items": [], "prefix": "", "suffix": "", "decimals": 0,
                "highlight": "", "horizontal": None, "stagger": 0.12, "first": 0.4, "grow": 0.9}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", self.p["first"] + i * self.p["stagger"], data={"sound": "blip", "gain": -3})
                for i in range(len(self.p["items"]))]

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        top = _title(ctx, self.p["kicker"], self.p["title"], lt) + 40 * s
        items = self.p["items"]
        if not items:
            return
        vmax = max(float(i["value"]) for i in items) or 1.0
        horiz = self.p["horizontal"] if self.p["horizontal"] is not None else (ctx.portrait and len(items) > 4)
        bottom = ctx.H * (0.80 if ctx.portrait else 0.86)
        vf = ctx.font("mono", 32 * s, 700)
        lf = ctx.font("body", 30 * s, 600)
        for i, it in enumerate(items):
            u = E.out_expo((lt - self.p["first"] - i * self.p["stagger"]) / self.p["grow"])
            hl = it.get("label") == self.p["highlight"]
            col = ctx.pal(it.get("color", "accent" if hl else "muted"), 1.0 if hl else 0.75)
            val = fmt(float(it["value"]) * u, self.p["decimals"], self.p["prefix"], self.p["suffix"])
            if horiz:
                rowh = (bottom - top) / len(items)
                bh = rowh * 0.55
                y = top + i * rowh
                x0 = ctx.margin
                maxw = ctx.safe_w * 0.72
                w = maxw * float(it["value"]) / vmax * u
                D.draw_text(c, it.get("label", ""), x0, y + 4 * s, lf, ctx.pal("ink", 0.9), align="left")
                D.rrect(c, x0, y + 20 * s, max(w, 2), bh, bh * 0.2, D.paint(col))
                if u > 0.02:
                    D.draw_text(c, val, x0 + w + 20 * s, y + 20 * s + bh * 0.7, vf, ctx.pal("ink", min(1, u * 2)), align="left")
            else:
                n = len(items)
                slot = ctx.safe_w / n
                bw = slot * 0.62
                x = ctx.margin + slot * i + (slot - bw) / 2
                maxh = bottom - top - 120 * s
                h = maxh * float(it["value"]) / vmax * u
                D.rrect(c, x, bottom - h, bw, h, min(bw * 0.12, h / 2), D.paint(col))
                if hl and u > 0.9:
                    c.drawRect(skia.Rect(x - 6 * s, bottom - h - 6 * s, x + bw + 6 * s, bottom),
                               D.paint(ctx.pal("accent", 0.25), blur=20 * s))
                if u > 0.02:
                    D.draw_text(c, val, x + bw / 2, bottom - h - 22 * s, vf, ctx.pal("ink", min(1, u * 2)))
                D.draw_text(c, it.get("label", ""), x + bw / 2, bottom + 50 * s, lf, ctx.pal("muted"))
        c.drawRect(skia.Rect(ctx.margin, bottom, ctx.W - ctx.margin, bottom + 2 * s), D.paint(ctx.pal("muted", 0.6)))


@register("line")
class LineChart(Scene):
    """Line/area chart drawing left to right with a glowing comet head, live value badge,
    grid with axis labels and annotated events."""
    defaults = {"kicker": "", "title": "", "series": {"seed": 11, "n": 60}, "prefix": "$", "suffix": "", "decimals": 0,
                "draw": 2.6, "first": 0.3, "events": [], "x_labels": [], "change": True}

    def cues(self) -> list[Cue]:
        out = [Cue("sfx", self.p["first"], data={"sound": "riser_short", "gain": -6})]
        n = len(series_from(self.p["series"]))
        for ev in self.p["events"]:
            out.append(Cue("sfx", self.p["first"] + self.p["draw"] * ev.get("i", 0) / max(n - 1, 1), data={"sound": "pop"}))
        out.append(Cue("sfx", self.p["first"] + self.p["draw"], data={"sound": "ding"}))
        out.append(Cue("hit", self.p["first"] + self.p["draw"], data={"strength": 0.4}))
        return out

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        top = _title(ctx, self.p["kicker"], self.p["title"], lt) + 60 * s
        data = series_from(self.p["series"])
        n = len(data)
        lo, hi = min(data), max(data)
        pad = (hi - lo) * 0.12 + 1e-9
        lo, hi = lo - pad, hi + pad
        x0, x1 = ctx.margin, ctx.W - ctx.margin - 40 * s
        y0, y1 = top, ctx.H * (0.78 if ctx.portrait else 0.86)
        X = lambda i: x0 + (x1 - x0) * i / max(n - 1, 1)  # noqa: E731
        Y = lambda v: y1 - (y1 - y0) * (v - lo) / (hi - lo)  # noqa: E731
        gf = ctx.font("mono", 24 * s, 500)
        for k in range(5):
            v = lo + (hi - lo) * k / 4
            y = Y(v)
            c.drawLine(x0, y, x1, y, D.paint(ctx.pal("muted", 0.18), stroke=1.2 * s, cap_round=False))
            D.draw_text(c, fmt(v, self.p["decimals"], self.p["prefix"], self.p["suffix"]), x1 + 30 * s, y - 8 * s, gf,
                        ctx.pal("muted", 0.8), align="right")
        for i, lab in enumerate(self.p["x_labels"]):
            xx = x0 + (x1 - x0) * i / max(len(self.p["x_labels"]) - 1, 1)
            D.draw_text(c, lab, xx, y1 + 46 * s, gf, ctx.pal("muted"), align="center")
        u = E.in_out_cubic((lt - self.p["first"]) / self.p["draw"])
        if u <= 0:
            return
        fi = u * (n - 1)
        k = int(fi)
        pts = [(X(i), Y(data[i])) for i in range(k + 1)]
        if k < n - 1:
            fr = fi - k
            pts.append((X(fi), Y(E.lerp(data[k], data[k + 1], fr))))
        hx, hy = pts[-1]
        hv = (y1 - hy) / (y1 - y0) * (hi - lo) + lo
        up = hv >= data[0]
        col = "pos" if up else "neg"
        area = D.polyline(pts)
        area.lineTo(hx, y1); area.lineTo(pts[0][0], y1); area.close()
        c.drawPath(area, D.paint(0, shader=D.linear_grad(0, y0, 0, y1, [ctx.pal(col, 0.35), ctx.pal(col, 0.0)])))
        line = D.polyline(pts)
        c.drawPath(line, D.paint(ctx.pal(col, 0.35), stroke=14 * s, blur=8 * s))
        c.drawPath(line, D.paint(ctx.pal(col), stroke=5 * s))
        c.drawCircle(hx, hy, 22 * s, D.paint(ctx.pal(col, 0.5), blur=14 * s))
        c.drawCircle(hx, hy, 9 * s, D.paint(ctx.pal("paper")))
        c.drawLine(hx, hy, x1, hy, D.paint(ctx.pal(col, 0.5), stroke=2 * s))
        bf = ctx.font("mono", 34 * s, 700)
        txt = fmt(hv, self.p["decimals"], self.p["prefix"], self.p["suffix"])
        bw = D.text_w(bf, txt) + 36 * s
        bx = min(hx + 24 * s, ctx.W - ctx.margin * 0.3 - bw)
        by = hy - 90 * s if hy > y0 + 120 * s else hy + 30 * s
        D.rrect(c, bx, by, bw, 58 * s, 12 * s, D.paint(ctx.pal(col)))
        D.draw_text(c, txt, bx + bw / 2, by + 41 * s, bf, ctx.pal("ink_dark"))
        ef = ctx.font("body", 28 * s, 700)
        for ev in self.p["events"]:
            i = int(ev.get("i", 0))
            if i > fi:
                continue
            a = E.clamp((fi - i) / 3)
            ex, ey = X(i), Y(data[i])
            c.drawCircle(ex, ey, 10 * s, D.paint(ctx.pal("accent2", a)))
            c.drawLine(ex, ey - 14 * s, ex, ey - 80 * s, D.paint(ctx.pal("accent2", a), stroke=2 * s))
            D.draw_text(c, ev.get("text", ""), ex, ey - 96 * s, ef, ctx.pal("accent2", a))
        if self.p["change"] and u >= 1:
            ch = (data[-1] / data[0] - 1) * 100
            a = E.clamp((lt - self.p["first"] - self.p["draw"]) / 0.4)
            cf = ctx.font("mono", 60 * s, 800)
            D.draw_text(c, f"{'+' if ch >= 0 else ''}{ch:.1f}%", ctx.W - ctx.margin, y0 - 30 * s, cf,
                        ctx.pal(col, a), align="right")


@register("candles")
class Candles(Scene):
    """Candlestick chart revealing candle by candle with a moving average and last-price line."""
    defaults = {"kicker": "", "title": "", "seed": 21, "n": 36, "start": 100.0, "vol": 0.025, "drift": 0.003,
                "ohlc": None, "reveal": 2.4, "first": 0.3, "ma": 7, "prefix": "$", "decimals": 2}

    def _ohlc(self) -> list[tuple[float, float, float, float]]:
        if self.p["ohlc"]:
            return [tuple(map(float, r)) for r in self.p["ohlc"]]  # type: ignore[misc]
        rnd = random.Random(int(self.p["seed"]))
        v = float(self.p["start"])
        out = []
        for _ in range(int(self.p["n"])):
            o = v
            cl = o * (1 + float(self.p["drift"]) + rnd.gauss(0, float(self.p["vol"])))
            h = max(o, cl) * (1 + abs(rnd.gauss(0, float(self.p["vol"]) * 0.5)))
            lo = min(o, cl) * (1 - abs(rnd.gauss(0, float(self.p["vol"]) * 0.5)))
            out.append((o, h, lo, cl))
            v = cl
        return out

    def cues(self) -> list[Cue]:
        n = len(self._ohlc())
        return [Cue("sfx", self.p["first"] + self.p["reveal"] * i / n, data={"sound": "tick", "gain": -9})
                for i in range(0, n, 3)] + [Cue("sfx", self.p["first"] + self.p["reveal"], data={"sound": "hit"})]

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        top = _title(ctx, self.p["kicker"], self.p["title"], lt) + 60 * s
        data = self._ohlc()
        n = len(data)
        lo = min(r[2] for r in data); hi = max(r[1] for r in data)
        pad = (hi - lo) * 0.08
        lo -= pad; hi += pad
        x0, x1 = ctx.margin, ctx.W - ctx.margin - 120 * s
        y0, y1 = top, ctx.H * (0.8 if ctx.portrait else 0.86)
        Y = lambda v: y1 - (y1 - y0) * (v - lo) / (hi - lo)  # noqa: E731
        slot = (x1 - x0) / n
        shown = (lt - self.p["first"]) / self.p["reveal"] * n
        for k in range(5):
            y = y0 + (y1 - y0) * k / 4
            c.drawLine(x0, y, ctx.W - ctx.margin, y, D.paint(ctx.pal("muted", 0.15), stroke=1.2 * s, cap_round=False))
        last = None
        for i, (o, h, l_, cl) in enumerate(data):
            u = E.clamp(shown - i)
            if u <= 0:
                break
            up = cl >= o
            col = ctx.pal("pos" if up else "neg")
            x = x0 + slot * (i + 0.5)
            mid = (o + cl) / 2
            ee = E.out_back(u)
            c.drawLine(x, Y(mid + (h - mid) * ee), x, Y(mid + (l_ - mid) * ee), D.paint(col, stroke=2.4 * s))
            top_b, bot_b = Y(mid + (max(o, cl) - mid) * ee), Y(mid + (min(o, cl) - mid) * ee)
            c.drawRect(skia.Rect(x - slot * 0.32, top_b, x + slot * 0.32, max(bot_b, top_b + 2 * s)), D.paint(col))
            last = cl
        m = int(self.p["ma"])
        pts = []
        for i in range(m - 1, min(n, int(shown) + 1)):
            avg = sum(r[3] for r in data[i - m + 1:i + 1]) / m
            pts.append((x0 + slot * (i + 0.5), Y(avg)))
        if len(pts) > 1:
            c.drawPath(D.polyline(pts), D.paint(ctx.pal("accent2", 0.9), stroke=3.5 * s))
        if last is not None:
            y = Y(last)
            p = D.paint(ctx.pal("ink", 0.6), stroke=2 * s)
            p.setPathEffect(skia.DashPathEffect.Make([10 * s, 8 * s], 0))
            c.drawLine(x0, y, x1 + 10 * s, y, p)
            bf = ctx.font("mono", 26 * s, 700)
            txt = fmt(last, self.p["decimals"], self.p["prefix"])
            bw = D.text_w(bf, txt) + 24 * s
            D.rrect(c, ctx.W - ctx.margin - bw, y - 24 * s, bw, 48 * s, 8 * s,
                    D.paint(ctx.pal("pos" if last >= data[0][0] else "neg")))
            D.draw_text(c, txt, ctx.W - ctx.margin - bw / 2, y + 10 * s, bf, ctx.pal("ink_dark"))


@register("ticker")
class Ticker(Scene):
    """Watchlist with live-flickering prices and change pills, plus a scrolling tape."""
    defaults = {"kicker": "", "title": "", "items": [], "tape": True, "speed": 160, "jitter": 0.004}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.25 + i * 0.12, data={"sound": "blip", "gain": -6}) for i in range(len(self.p["items"]))]

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        top = _title(ctx, self.p["kicker"], self.p["title"], lt) + 30 * s
        items = self.p["items"]
        rowh = 128 * s
        sf = ctx.font("mono", 44 * s, 800)
        nf = ctx.font("body", 26 * s, 500)
        pf = ctx.font("mono", 40 * s, 600)
        cf = ctx.font("mono", 30 * s, 700)
        bucket = int(lt * 4)
        for i, it in enumerate(items):
            u = E.out_expo((lt - 0.25 - i * 0.12) / 0.5)
            if u <= 0:
                continue
            y = top + i * (rowh + 16 * s)
            x = ctx.margin + (1 - u) * 120 * s
            rnd = random.Random(bucket * 131 + i)
            price = float(it.get("price", 100)) * (1 + rnd.uniform(-1, 1) * self.p["jitter"] * min(1, lt))
            chg = float(it.get("change", 0)) + (price / float(it.get("price", 100)) - 1) * 100
            up = chg >= 0
            flash = 1 if (int(lt * 4) % 3 == i % 3 and lt > 1) else 0
            D.rrect(c, x, y, ctx.safe_w, rowh, 24 * s, D.paint(ctx.pal("#ffffff", (0.05 + 0.05 * flash) * u)))
            D.draw_text(c, it.get("sym", "???"), x + 36 * s, y + 62 * s, sf, ctx.pal("ink", u), align="left")
            D.draw_text(c, it.get("name", ""), x + 36 * s, y + 100 * s, nf, ctx.pal("muted", u), align="left")
            dec = int(it.get("decimals", 2))
            D.draw_text(c, fmt(price, dec, it.get("prefix", "$")), x + ctx.safe_w * 0.68, y + 78 * s, pf,
                        ctx.pal("ink", u), align="right")
            txt = f"{'+' if up else ''}{chg:.2f}%"
            pw = 190 * s
            D.rrect(c, x + ctx.safe_w - pw - 24 * s, y + 30 * s, pw, 68 * s, 16 * s, D.paint(ctx.pal("pos" if up else "neg", u)))
            D.draw_text(c, txt, x + ctx.safe_w - pw / 2 - 24 * s, y + 75 * s, cf, ctx.pal("ink_dark", u))
        if self.p["tape"] and items:
            ty = ctx.H * (0.84 if ctx.portrait else 0.9)
            c.drawRect(skia.Rect(0, ty, ctx.W, ty + 70 * s), D.paint(ctx.pal("accent", 0.12)))
            tf = ctx.font("mono", 30 * s, 700)
            unit = "   ".join(f"{it.get('sym')} {'▲' if float(it.get('change', 0)) >= 0 else '▼'}{abs(float(it.get('change', 0))):.2f}%"
                               for it in items) + "   "
            uw = D.text_w(tf, unit)
            x = -((lt * self.p["speed"] * s) % uw)
            while x < ctx.W:
                D.draw_text(c, unit, x, ty + 46 * s, tf, ctx.pal("accent"), align="left")
                x += uw


@register("stat_grid")
class StatGrid(Scene):
    """2x2 (or 1xN) cards, each with a counting value, label and delta."""
    defaults = {"kicker": "", "title": "", "items": [], "count": 1.2, "stagger": 0.15}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.3 + i * self.p["stagger"], data={"sound": "pop", "gain": -4}) for i in range(len(self.p["items"]))]

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        top = _title(ctx, self.p["kicker"], self.p["title"], lt) + 40 * s
        items = self.p["items"]
        cols = 2 if ctx.portrait or len(items) == 4 else len(items)
        rows = math.ceil(len(items) / cols)
        gap = 28 * s
        cw = (ctx.safe_w - gap * (cols - 1)) / cols
        ch = min(340 * s, (ctx.H * 0.72 - top + ctx.H * 0.1 - gap * (rows - 1)) / max(rows, 1))
        for i, it in enumerate(items):
            r_, k = divmod(i, cols)
            x = ctx.margin + k * (cw + gap)
            y = top + r_ * (ch + gap)
            u = E.clamp((lt - 0.3 - i * self.p["stagger"]) / 0.5)
            if u <= 0:
                continue
            e = E.out_back(u)
            c.save(); c.translate(x + cw / 2, y + ch / 2); c.scale(0.9 + 0.1 * e, 0.9 + 0.1 * e)
            D.rrect(c, -cw / 2, -ch / 2, cw, ch, 28 * s, D.paint(ctx.pal("#ffffff", 0.06)))
            D.rrect(c, -cw / 2, -ch / 2, cw, ch, 28 * s, D.paint(ctx.pal("accent", 0.35 * u), stroke=2 * s))
            cu = E.out_expo((lt - 0.4 - i * self.p["stagger"]) / self.p["count"])
            val = fmt(float(it.get("value", 0)) * cu, int(it.get("decimals", 0)), it.get("prefix", ""), it.get("suffix", ""))
            vf = D.fit_font(ctx.theme["mono"], fmt(float(it.get("value", 0)), int(it.get("decimals", 0)), it.get("prefix", ""),
                                                   it.get("suffix", "")), cw - 60 * s, 88 * s, 800)
            D.draw_text(c, val, -cw / 2 + 30 * s, 10 * s, vf, ctx.pal("ink"), align="left")
            lf = ctx.font("body", 28 * s, 600)
            D.draw_text(c, it.get("label", ""), -cw / 2 + 30 * s, -ch / 2 + 60 * s, lf, ctx.pal("muted"), align="left")
            if it.get("delta"):
                d = str(it["delta"])
                neg = d.startswith("-")
                df = ctx.font("mono", 30 * s, 700)
                D.draw_text(c, d, -cw / 2 + 30 * s, ch / 2 - 40 * s, df, ctx.pal("neg" if neg else "pos", E.clamp(cu * 1.5)),
                            align="left")
            c.restore()
