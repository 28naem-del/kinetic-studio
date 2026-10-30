"""Explainer / education scenes: bullets, diagram, definition, keyfact."""
from __future__ import annotations

import math

import skia

from .. import draw as D
from .. import ease as E
from .base import Ctx, Cue, Scene, register
from .common import reveal_line


def _heading(ctx: Ctx, text: str, y: float, lt: float, kicker: str = "") -> float:
    s = ctx.s
    if kicker:
        kf = ctx.font("body", 28 * s, 700)
        D.draw_text(ctx.c, kicker.upper(), ctx.margin * 1.2, y, kf, ctx.pal("accent", E.clamp(lt / 0.4)),
                    align="left", tracking=0.22)
        y += 70 * s
    if text:
        hf = ctx.font("display", 76 * s, 600)
        for i, ln in enumerate(D.wrap(hf, text, ctx.safe_w * 0.95)):
            reveal_line(ctx, ln, ctx.margin * 1.2, y + i * 84 * s, hf, ctx.pal(ctx.pal.on("bg")), (lt - i * 0.1) / 0.8)
            y_last = y + i * 84 * s
        y = y_last + 40 * s
    return y


@register("bullets")
class Bullets(Scene):
    """Heading + numbered points revealed one by one; the newest point is emphasised."""
    defaults = {"kicker": "", "heading": "", "items": [], "stagger": 0.9, "first": 0.6, "numbered": True}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", self.p["first"] + i * self.p["stagger"], data={"sound": "pop", "gain": -3})
                for i in range(len(self.p["items"]))]

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        y = ctx.H * (0.32 if ctx.portrait else 0.18)
        y = _heading(ctx, self.p["heading"], y, lt, self.p["kicker"]) + 60 * s
        f = ctx.font("body", 52 * s, 500)
        x = ctx.margin * 1.2
        ink = ctx.pal.on(self.ground)
        n = len(self.p["items"])
        for i, item in enumerate(self.p["items"]):
            text = item if isinstance(item, str) else item.get("text", "")
            icon = None if isinstance(item, str) else item.get("icon")
            t0 = self.p["first"] + i * self.p["stagger"]
            u = E.clamp((lt - t0) / 0.5)
            lines = D.wrap(f, text, ctx.safe_w - 140 * s)
            if u <= 0:
                y += len(lines) * 68 * s + 64 * s
                continue
            latest = i == n - 1 or lt < t0 + self.p["stagger"]
            dim = 1.0 if latest else 0.62
            e = E.out_back(u)
            r = 34 * s
            cy = y + r
            c.drawCircle(x + r, cy, r * e, D.paint(ctx.pal("accent", 0.16 * dim)))
            c.drawCircle(x + r, cy, r * e, D.paint(ctx.pal("accent", dim), stroke=3 * s))
            if icon:
                D.icon(c, icon, x + r, cy, 32 * s * e, ctx.pal("accent", dim))
            elif self.p["numbered"]:
                nf = ctx.font("mono", 30 * s * max(e, 0.01), 700)
                D.draw_text(c, str(i + 1), x + r, cy, nf, ctx.pal("accent", dim), valign="middle")
            else:
                D.icon(c, "check", x + r, cy, 30 * s * e, ctx.pal("accent", dim))
            for li, ln in enumerate(lines):
                D.draw_text(c, ln, x + 2 * r + 34 * s + (1 - E.out_cubic(u)) * 40 * s, y + 52 * s + li * 66 * s, f,
                            ctx.pal(ink, u * dim), align="left")
            y += len(lines) * 68 * s + 64 * s


@register("diagram")
class Diagram(Scene):
    """Labelled node/edge diagram that builds itself: nodes pop, edges draw with a comet,
    callouts attach with leader lines. Layouts: chain, radial, manual (x,y in 0..1)."""
    defaults = {"kicker": "", "heading": "", "nodes": [], "edges": None, "layout": "chain",
                "callouts": [], "stagger": 0.45, "first": 0.5, "node_r": 0, "caption": ""}

    def _layout(self, ctx: Ctx) -> tuple[list[tuple[float, float]], float]:
        nodes = self.p["nodes"]
        n = max(1, len(nodes))
        top = ctx.H * (0.42 if ctx.portrait else 0.36)
        bot = ctx.H * (0.70 if ctx.portrait else 0.80)
        left, right = ctx.margin * 1.2, ctx.W - ctx.margin * 1.2
        r = self.p["node_r"] * ctx.s or min((right - left) / n * 0.36, 110 * ctx.s)
        lay = self.p["layout"]
        if lay == "radial":
            cx, cy = ctx.cx, (top + bot) / 2
            R = min(right - left, bot - top) * 0.42
            pts = [(cx, cy)] + [(cx + R * math.cos(-math.pi / 2 + k * 2 * math.pi / (n - 1)),
                                 cy + R * math.sin(-math.pi / 2 + k * 2 * math.pi / (n - 1))) for k in range(n - 1)]
            return pts, min(r, R * 0.32)
        if lay == "manual":
            return [(left + nd.get("x", 0.5) * (right - left), top + nd.get("y", 0.5) * (bot - top)) for nd in nodes], r
        # chain: one row, zig-zag slightly for rhythm
        step = (right - left) / n
        cy = (top + bot) / 2
        return [(left + step * (k + 0.5), cy + (0 if n < 3 else (-1) ** k * r * 0.35)) for k in range(n)], r

    def _edges(self) -> list[dict]:
        e = self.p["edges"]
        if e is None:
            ids = [nd.get("id", str(i)) for i, nd in enumerate(self.p["nodes"])]
            if self.p["layout"] == "radial":
                return [{"from": ids[0], "to": x} for x in ids[1:]]
            return [{"from": a, "to": b} for a, b in zip(ids, ids[1:])]
        return e

    def cues(self) -> list[Cue]:
        out = []
        for i in range(len(self.p["nodes"])):
            out.append(Cue("sfx", self.p["first"] + i * self.p["stagger"], data={"sound": "pop", "gain": -2}))
        n = len(self.p["nodes"])
        for j, _ in enumerate(self.p["callouts"]):
            out.append(Cue("sfx", self.p["first"] + n * self.p["stagger"] + 0.3 + j * 0.6, data={"sound": "tick"}))
        return out

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        _heading(ctx, self.p["heading"], ctx.H * (0.25 if ctx.portrait else 0.14), lt, self.p["kicker"])
        pts, r = self._layout(ctx)
        nodes = self.p["nodes"]
        ids = {nd.get("id", str(i)): i for i, nd in enumerate(nodes)}
        ink = ctx.pal.on(self.ground)
        appear = [self.p["first"] + i * self.p["stagger"] for i in range(len(nodes))]
        # edges under nodes
        for edge in self._edges():
            ia, ib = ids.get(edge["from"]), ids.get(edge["to"])
            if ia is None or ib is None:
                continue
            t0 = max(appear[ia], appear[ib]) + 0.05
            u = E.in_out_cubic((lt - t0) / 0.45)
            if u <= 0:
                continue
            (x0, y0), (x1, y1) = pts[ia], pts[ib]
            ang = math.atan2(y1 - y0, x1 - x0)
            sx, sy = x0 + math.cos(ang) * r, y0 + math.sin(ang) * r
            ex, ey = x1 - math.cos(ang) * r, y1 - math.sin(ang) * r
            px, py = E.lerp(sx, ex, u), E.lerp(sy, ey, u)
            p = D.paint(ctx.pal("accent", 0.85), stroke=5 * s)
            if edge.get("style") == "dashed":
                p.setPathEffect(skia.DashPathEffect.Make([14 * s, 10 * s], 0))
            c.drawLine(sx, sy, px, py, p)
            if u < 1:
                c.drawCircle(px, py, 10 * s, D.paint(ctx.pal("accent2", 0.6), blur=10 * s))
                c.drawCircle(px, py, 5 * s, D.paint(ctx.pal("paper")))
            elif edge.get("style") == "arrow":
                D.arrow_head(c, ex, ey, ang, 20 * s, ctx.pal("accent"))
            if edge.get("label") and u >= 1:
                lf = ctx.font("mono", 24 * s, 600)
                a2 = E.clamp((lt - t0 - 0.45) / 0.4)
                D.draw_text(c, edge["label"], (sx + ex) / 2, (sy + ey) / 2 - 18 * s, lf, ctx.pal("accent2", a2))
        # nodes
        for i, nd in enumerate(nodes):
            u = (lt - appear[i]) / 0.55
            if u <= 0:
                continue
            x, y = pts[i]
            pop = E.out_back(u, 2.2)
            col = nd.get("color", "accent")
            ring = E.clamp(u * 1.2)
            if ring < 1:
                c.drawCircle(x, y, r * (1 + 0.8 * ring), D.paint(ctx.pal(col, 0.5 * (1 - ring)), stroke=4 * s))
            breathe = 1 + 0.02 * math.sin(lt * 2 + i)
            c.drawCircle(x, y, r * pop * breathe, D.paint(ctx.pal("bg2")))
            c.drawCircle(x, y, r * pop * breathe, D.paint(ctx.pal(col), stroke=5 * s))
            label = str(nd.get("label", ""))
            lf = D.fit_font(ctx.theme["display"], label, r * 1.55, r * 0.62, 600)
            D.draw_text(c, label, x, y, lf, ctx.pal(ink, E.clamp(u * 1.5)), valign="middle")
            if nd.get("sub"):
                sf = ctx.font("body", min(30 * s, r * 0.36), 500)
                D.draw_text(c, nd["sub"], x, y + r + 50 * s, sf, ctx.pal("muted", E.clamp((u - 0.3) * 2)))
        # callouts
        base = self.p["first"] + len(nodes) * self.p["stagger"] + 0.3
        cf = ctx.font("body", 34 * s, 600)
        for j, co in enumerate(self.p["callouts"]):
            ni = ids.get(co.get("node", ""), 0)
            u = (lt - base - j * 0.6) / 0.6
            if u <= 0 or not pts:
                continue
            x, y = pts[ni]
            side = co.get("side", "up" if j % 2 == 0 else "down")
            dy = -1 if side == "up" else 1
            ly = y + dy * (r + 150 * s)
            grow = E.out_cubic(u)
            c.drawLine(x, y + dy * r, x, E.lerp(y + dy * r, ly, grow), D.paint(ctx.pal("accent2"), stroke=3 * s))
            c.drawCircle(x, y + dy * r, 7 * s, D.paint(ctx.pal("accent2")))
            if grow > 0.6:
                a = E.clamp((u - 0.6) / 0.5)
                txt = co.get("text", "")
                tw = D.text_w(cf, txt) + 40 * s
                bx = min(max(x - tw / 2, ctx.margin * 0.6), ctx.W - ctx.margin * 0.6 - tw)
                by = ly - (60 * s if dy < 0 else 0)
                D.rrect(c, bx, by, tw, 60 * s, 14 * s, D.paint(ctx.pal("accent2", 0.95 * a)))
                D.draw_text(c, txt, bx + tw / 2, by + 41 * s, cf, ctx.pal("ink_dark", a))
        if self.p["caption"]:
            f = ctx.font("body", 40 * s, 500)
            a = E.clamp((lt - base - 0.2) / 0.6)
            for k, ln in enumerate(D.wrap(f, self.p["caption"], ctx.safe_w * 0.9)):
                D.draw_text(c, ln, ctx.cx, ctx.H * (0.76 if ctx.portrait else 0.9) + k * 54 * s, f, ctx.pal("muted", a))


@register("definition")
class Definition(Scene):
    """Dictionary-style card: term, pronunciation, part of speech, definition, note."""
    defaults = {"term": "Term", "phonetic": "", "pos": "noun", "definition": "", "note": ""}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.0, data={"sound": "swish"}), Cue("sfx", 0.6, data={"sound": "key", "gain": -6})]

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        x = ctx.margin * 1.2
        y = ctx.H * (0.42 if ctx.portrait else 0.3)
        tf = ctx.fit("display", self.p["term"], ctx.safe_w, 150 * s, 600)
        reveal_line(ctx, self.p["term"], x, y, tf, ctx.pal(ctx.pal.on(self.ground)), lt / 0.8)
        y += 80 * s
        mf = ctx.font("mono", 32 * s, 500)
        a = E.clamp((lt - 0.4) / 0.5)
        meta = "  ·  ".join(v for v in (self.p["phonetic"], self.p["pos"]) if v)
        D.draw_text(c, meta, x, y, mf, ctx.pal("accent", a), align="left")
        y += 40 * s
        c.drawRect(skia.Rect.MakeXYWH(x, y, ctx.safe_w * 0.95 * E.out_expo((lt - 0.5) / 0.9), 2 * s),
                   D.paint(ctx.pal("muted", 0.6)))
        y += 30 * s
        bf = ctx.font("body", 48 * s, 500)
        for i, ln in enumerate(D.wrap(bf, self.p["definition"], ctx.safe_w * 0.95)):
            y += 66 * s
            a = E.clamp((lt - 0.8 - i * 0.12) / 0.5)
            D.draw_text(c, ln, x, y + (1 - a) * 12 * s, bf, ctx.pal(ctx.pal.on(self.ground), a), align="left")
        if self.p["note"]:
            nf = ctx.font("body", 34 * s, 500)
            y += 40 * s
            for i, ln in enumerate(D.wrap(nf, self.p["note"], ctx.safe_w * 0.9)):
                y += 48 * s
                a = E.clamp((lt - 1.5 - i * 0.1) / 0.5)
                D.draw_text(c, ln, x, y, nf, ctx.pal("muted", a), align="left")


@register("keyfact")
class KeyFact(Scene):
    """One big figure or short phrase with a label: e.g. '4 amino acids'."""
    defaults = {"value": "4", "label": "", "detail": "", "color": "accent"}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.1, data={"sound": "hit", "gain": -4}), Cue("hit", 0.1, data={"strength": 0.3})]

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        vf = ctx.fit("display", str(self.p["value"]), ctx.safe_w, 360 * s, 700)
        e = E.out_back(E.clamp((lt - 0.1) / 0.6))
        c.save(); c.translate(ctx.cx, ctx.cy - 60 * s); c.scale(0.6 + 0.4 * e, 0.6 + 0.4 * e)
        D.draw_text(c, str(self.p["value"]), 0, 0, vf, ctx.pal(self.p["color"], E.clamp(lt / 0.3)), valign="middle")
        c.restore()
        cap = D.cap_height(vf)
        lf = ctx.font("body", 56 * s, 700)
        reveal_line(ctx, self.p["label"], ctx.cx, ctx.cy + cap / 2 + 60 * s, lf, ctx.pal(ctx.pal.on(self.ground)),
                    (lt - 0.4) / 0.7, "center")
        if self.p["detail"]:
            df = ctx.font("body", 36 * s, 400)
            a = E.clamp((lt - 0.8) / 0.6)
            for i, ln in enumerate(D.wrap(df, self.p["detail"], ctx.safe_w * 0.85)):
                D.draw_text(c, ln, ctx.cx, ctx.cy + cap / 2 + 140 * s + i * 50 * s, df, ctx.pal("muted", a))
