"""Product / app promo scenes: device mockup (+ callouts), features grid, CTA."""
from __future__ import annotations

import math
import os

import skia

from .. import draw as D
from .. import ease as E
from .base import Ctx, Cue, Scene, register
from .common import reveal_line


def _procedural_ui(ctx: Ctx, rect: skia.Rect, ui: dict, lt: float, scroll: float) -> None:
    """A clean generic app screen drawn from a tiny description (used when no screenshot)."""
    c, s = ctx.c, rect.width() / 560.0
    x0, y0, w = rect.left(), rect.top() - scroll, rect.width()
    c.drawRect(rect, D.paint(ctx.pal(ui.get("bg", "#0f0d24"))))
    hf = ctx.font("display", 46 * s, 700)
    sf = ctx.font("body", 22 * s, 500)
    D.draw_text(c, ui.get("subtitle", "Good morning"), x0 + 36 * s, y0 + 120 * s, sf, ctx.pal("muted"), align="left")
    D.draw_text(c, ui.get("title", "Today"), x0 + 36 * s, y0 + 170 * s, hf, ctx.pal("ink"), align="left")
    # hero card with ring
    cy = y0 + 200 * s
    D.rrect(c, x0 + 28 * s, cy, w - 56 * s, 300 * s, 34 * s,
            D.paint(0, shader=D.linear_grad(x0, cy, x0 + w, cy + 300 * s, [ctx.pal("accent"), ctx.pal("accent2")])))
    pr = float(ui.get("progress", 0.82)) * E.out_cubic(lt / 1.6)
    rc = skia.Rect.MakeXYWH(x0 + 60 * s, cy + 50 * s, 200 * s, 200 * s)
    c.drawArc(rc, 0, 360, False, D.paint(ctx.pal("#ffffff", 0.25), stroke=22 * s))
    c.drawArc(rc, -90, 360 * pr, False, D.paint(ctx.pal("#ffffff"), stroke=22 * s))
    vf = ctx.font("display", 54 * s, 700)
    D.draw_text(c, f"{int(round(pr * 100))}%", rc.centerX(), rc.centerY(), vf, ctx.pal("#ffffff"), valign="middle")
    lf = ctx.font("body", 26 * s, 600)
    D.draw_text(c, ui.get("card_title", "Daily goal"), x0 + 300 * s, cy + 120 * s, lf, ctx.pal("#ffffff"), align="left")
    D.draw_text(c, ui.get("card_sub", "on track"), x0 + 300 * s, cy + 160 * s, sf, ctx.pal("#ffffff", 0.75), align="left")
    # rows
    rows = ui.get("rows", [["Focus session", "25 min"], ["Hydration", "6 / 8"], ["Walk", "4.2 km"], ["Read", "18 pages"],
                           ["Sleep", "7h 40m"], ["Stretch", "done"]])
    icons = ["clock", "heart", "globe", "book", "star", "check"]
    ry = cy + 330 * s
    for i, (a, b) in enumerate(rows):
        D.rrect(c, x0 + 28 * s, ry, w - 56 * s, 104 * s, 26 * s, D.paint(ctx.pal("#ffffff", 0.06)))
        c.drawCircle(x0 + 84 * s, ry + 52 * s, 30 * s, D.paint(ctx.pal("accent", 0.22)))
        D.icon(c, icons[i % len(icons)], x0 + 84 * s, ry + 52 * s, 30 * s, ctx.pal("accent"))
        D.draw_text(c, a, x0 + 136 * s, ry + 62 * s, lf, ctx.pal("ink"), align="left")
        D.draw_text(c, b, x0 + w - 56 * s, ry + 62 * s, sf, ctx.pal("muted"), align="right")
        ry += 120 * s
    # tab bar (fixed, not scrolled)
    tb = rect.bottom() - 120 * s
    c.drawRect(skia.Rect(rect.left(), tb, rect.right(), rect.bottom()), D.paint(ctx.pal("#000000", 0.35)))
    for k, ic in enumerate(["star", "chart", "bell", "user"]):
        D.icon(c, ic, rect.left() + w * (k + 0.5) / 4, tb + 50 * s, 34 * s, ctx.pal("accent" if k == 0 else "muted"))


def draw_phone(ctx: Ctx, cx: float, cy: float, pw: float, screen: str, ui: dict, lt: float,
               scroll: float, gloss: float) -> skia.Rect:
    c, s = ctx.c, ctx.s
    ph = pw * 2.08
    r = pw * 0.15
    x, y = cx - pw / 2, cy - ph / 2
    D.rrect(c, x + 10 * s, y + 40 * s, pw, ph, r, D.paint(ctx.pal("#000000", 0.55), blur=40 * s))
    D.rrect(c, x, y, pw, ph, r, D.paint(0, shader=D.linear_grad(x, y, x + pw, y + ph, [ctx.pal("#3a3a46"), ctx.pal("#15151c")])))
    for by, bh in ((0.2, 0.06), (0.29, 0.1)):
        D.rrect(c, x - 5 * s, y + ph * by, 7 * s, ph * bh, 3 * s, D.paint(ctx.pal("#2a2a33")))
    D.rrect(c, x + pw - 2 * s, y + ph * 0.26, 7 * s, ph * 0.12, 3 * s, D.paint(ctx.pal("#2a2a33")))
    inset = pw * 0.035
    sr = skia.Rect(x + inset, y + inset, x + pw - inset, y + ph - inset)
    c.save()
    c.clipRRect(skia.RRect.MakeRectXY(sr, r - inset, r - inset), skia.ClipOp.kIntersect, True)
    path = screen if (not screen or os.path.isabs(screen)) else os.path.join(ctx.assets, screen)
    drawn = False
    if screen:
        try:
            img = D.load_image(path)
            iw, ih = img.width(), img.height()
            k = sr.width() / iw
            dh = ih * k
            off = min(scroll, max(0.0, dh - sr.height()))
            c.drawImageRect(img, skia.Rect.MakeXYWH(sr.left(), sr.top() - off, sr.width(), dh), D.SAMPLING)
            drawn = True
        except (FileNotFoundError, ValueError):
            drawn = False
    if not drawn:
        _procedural_ui(ctx, sr, ui, lt, scroll)
    # gloss sweep
    gx = sr.left() + (gloss * 2.2 - 0.6) * sr.width()
    sh = D.linear_grad(gx - pw * 0.3, sr.top(), gx + pw * 0.3, sr.top() + pw * 0.6,
                       [ctx.pal("#ffffff", 0), ctx.pal("#ffffff", 0.14), ctx.pal("#ffffff", 0)])
    c.drawRect(sr, D.paint(0, shader=sh))
    c.restore()
    D.rrect(c, cx - pw * 0.15, y + inset + pw * 0.035, pw * 0.3, pw * 0.085, pw * 0.0425, D.paint(ctx.pal("#000000")))
    return sr


@register("device")
class Device(Scene):
    """Phone (or browser) mockup with screenshot / procedural UI, 3D swing-in, float, scroll,
    gloss sweep, optional headline and feature callouts with leader lines."""
    defaults = {"frame": "phone", "screen": "", "ui": {}, "headline": "", "sub": "", "callouts": [],
                "scroll": 0, "swing": 28, "first_callout": 0.9, "callout_gap": 0.7}

    def cues(self) -> list[Cue]:
        out = [Cue("sfx", 0.0, data={"sound": "whoosh"}), Cue("sfx", 0.45, data={"sound": "thud", "gain": -4})]
        for i, _ in enumerate(self.p["callouts"]):
            out.append(Cue("sfx", self.p["first_callout"] + i * self.p["callout_gap"], data={"sound": "pop"}))
        return out

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        has_head = bool(self.p["headline"])
        if ctx.portrait:
            pw = ctx.W * (0.5 if self.p["callouts"] else 0.56)
            cx, cy = ctx.cx, ctx.H * (0.56 if has_head else 0.5)
        else:
            pw = ctx.H * 0.36
            cx, cy = ctx.W * (0.68 if has_head else 0.5), ctx.cy + 20 * s
        sp = E.spring(lt, 1.1, 4.5)
        ry = math.radians(self.p["swing"] * (1 - sp) + 6 * math.sin(lt * 0.8))
        rx = math.radians(5 * math.sin(lt * 0.6 + 1))
        fy = 12 * s * math.sin(lt * 1.4)
        enter = E.out_expo(lt / 0.6)
        c.save()
        c.concat(D.persp3d(cx, cy + (1 - enter) * ctx.H * 0.6 + fy, rx, ry, 0, dist=1800 * s, scale=0.92 + 0.08 * enter))
        scroll = float(self.p["scroll"]) * s * E.in_out_cubic((lt - 0.8) / max(self.dur - 1.2, 0.5))
        draw_phone(ctx, cx, cy + (1 - enter) * ctx.H * 0.6 + fy, pw, self.p["screen"], self.p["ui"], lt, scroll,
                   E.clamp((lt - 0.3) / 1.4))
        c.restore()
        if has_head:
            hf = ctx.font("display", (84 if ctx.portrait else 96) * s, 700)
            maxw = ctx.safe_w if ctx.portrait else ctx.W * 0.42
            hx = ctx.cx if ctx.portrait else ctx.margin * 1.4
            al = "center" if ctx.portrait else "left"
            hy = ctx.H * 0.12 if ctx.portrait else ctx.H * 0.38
            for i, ln in enumerate(D.wrap(hf, self.p["headline"], maxw)):
                reveal_line(ctx, ln, hx, hy + (i + 1) * 92 * s, hf, ctx.pal("ink"), (lt - 0.2 - i * 0.1) / 0.8, al)
            if self.p["sub"]:
                sf = ctx.font("body", 38 * s, 500)
                a = E.clamp((lt - 0.6) / 0.5)
                yy = hy + (len(D.wrap(hf, self.p["headline"], maxw)) + 1) * 92 * s
                D.draw_text(c, self.p["sub"], hx, yy, sf, ctx.pal("muted", a), align=al)
        ph = pw * 2.08
        for i, co in enumerate(self.p["callouts"]):
            t0 = self.p["first_callout"] + i * self.p["callout_gap"]
            u = (lt - t0) / 0.5
            if u <= 0:
                continue
            side = co.get("side", "left" if i % 2 == 0 else "right")
            yy = cy - ph / 2 + ph * float(co.get("y", 0.25 + 0.2 * i)) + fy
            e = E.out_back(E.clamp(u), 1.6)
            cf = ctx.font("body", 34 * s, 700)
            text = co.get("text", "")
            tw = D.text_w(cf, text) + 110 * s
            bh = 84 * s
            edge = cx - pw / 2 if side == "left" else cx + pw / 2
            bx = edge - tw + 50 * s if side == "left" else edge - 50 * s
            bx = min(max(bx, ctx.margin * 0.4), ctx.W - ctx.margin * 0.4 - tw)
            c.save()
            c.translate(bx + tw / 2, yy)
            c.scale(e, e)
            D.rrect(c, -tw / 2 + 6 * s, -bh / 2 + 14 * s, tw, bh, bh / 2, D.paint(ctx.pal("#000000", 0.4), blur=16 * s))
            D.rrect(c, -tw / 2, -bh / 2, tw, bh, bh / 2, D.paint(ctx.pal("paper")))
            c.drawCircle(-tw / 2 + 44 * s, 0, 26 * s, D.paint(ctx.pal(co.get("color", "accent"))))
            D.icon(c, co.get("icon", "check"), -tw / 2 + 44 * s, 0, 26 * s, ctx.pal("#ffffff"))
            D.draw_text(c, text, -tw / 2 + 84 * s, 12 * s, cf, ctx.pal("ink_dark"), align="left")
            c.restore()


@register("features")
class Features(Scene):
    """Grid of feature cards (icon, title, line) popping in on a stagger."""
    defaults = {"heading": "", "items": [], "stagger": 0.22, "first": 0.3, "columns": 0}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", self.p["first"] + i * self.p["stagger"], data={"sound": "pop", "gain": -2})
                for i in range(len(self.p["items"]))]

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        items = self.p["items"]
        cols = self.p["columns"] or (2 if ctx.portrait else min(4, max(1, len(items))))
        rows = max(1, math.ceil(len(items) / cols))
        gap = 32 * s
        top = ctx.H * (0.26 if ctx.portrait else 0.32)
        if self.p["heading"]:
            hf = ctx.font("display", 80 * s, 700)
            for i, ln in enumerate(D.wrap(hf, self.p["heading"], ctx.safe_w)):
                reveal_line(ctx, ln, ctx.cx, top - 90 * s + i * 90 * s, hf, ctx.pal("ink"), lt / 0.8, "center")
            top += 60 * s
        cw = (ctx.safe_w - gap * (cols - 1)) / cols
        ch = min(cw * 1.05, (ctx.H * 0.6 - gap * (rows - 1)) / rows)
        for i, it in enumerate(items):
            r_, k = divmod(i, cols)
            x = ctx.margin + k * (cw + gap)
            y = top + r_ * (ch + gap)
            u = E.clamp((lt - self.p["first"] - i * self.p["stagger"]) / 0.55)
            if u <= 0:
                continue
            e = E.out_back(u, 1.4)
            c.save()
            c.translate(x + cw / 2, y + ch / 2 + (1 - E.out_cubic(u)) * 60 * s)
            c.scale(0.85 + 0.15 * e, 0.85 + 0.15 * e)
            D.rrect(c, -cw / 2, -ch / 2, cw, ch, 36 * s, D.paint(ctx.pal("#ffffff", 0.07 * u)))
            D.rrect(c, -cw / 2, -ch / 2, cw, ch, 36 * s, D.paint(ctx.pal("#ffffff", 0.14 * u), stroke=2 * s))
            ic = 54 * s
            c.drawCircle(-cw / 2 + 40 * s + ic, -ch / 2 + 40 * s + ic, ic,
                         D.paint(0, shader=D.linear_grad(-cw / 2, -ch / 2, -cw / 2 + 2 * ic, -ch / 2 + 2 * ic,
                                                         [ctx.pal("accent"), ctx.pal("accent2")])))
            D.icon(c, it.get("icon", "star"), -cw / 2 + 40 * s + ic, -ch / 2 + 40 * s + ic, ic * 0.95, ctx.pal("#ffffff"))
            tf = D.fit_font(ctx.theme["display"], it.get("title", ""), cw - 70 * s, 46 * s, 700)
            D.draw_text(c, it.get("title", ""), -cw / 2 + 40 * s, -ch / 2 + 2 * ic + 110 * s, tf, ctx.pal("ink", u), align="left")
            bf = ctx.font("body", 28 * s, 500)
            for li, ln in enumerate(D.wrap(bf, it.get("text", ""), cw - 80 * s)[:3]):
                D.draw_text(c, ln, -cw / 2 + 40 * s, -ch / 2 + 2 * ic + 160 * s + li * 38 * s, bf, ctx.pal("muted", u), align="left")
            c.restore()


@register("cta")
class CTA(Scene):
    """Call to action: headline, pulsing button, simulated tap, sub-line."""
    defaults = {"headline": "Try it free", "button": "Get the app", "sub": "", "tap_at": 1.1}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.0, data={"sound": "swish"}), Cue("sfx", 0.45, data={"sound": "pop"}),
                Cue("sfx", self.p["tap_at"], data={"sound": "click"}), Cue("sfx", self.p["tap_at"] + 0.08, data={"sound": "ding"}),
                Cue("hit", self.p["tap_at"], data={"strength": 0.3})]

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        hf = ctx.fit("display", self.p["headline"], ctx.safe_w, 120 * s, 700)
        reveal_line(ctx, self.p["headline"], ctx.cx, ctx.cy - 80 * s, hf, ctx.pal("ink"), lt / 0.8, "center")
        bf = ctx.font("body", 46 * s, 800)
        bw = D.text_w(bf, self.p["button"]) + 140 * s
        bh = 120 * s
        u = E.clamp((lt - 0.45) / 0.5)
        tap = float(self.p["tap_at"])
        press = 1 - 0.08 * math.exp(-((lt - tap) * 12) ** 2)
        e = E.out_back(u) * press
        by = ctx.cy + 90 * s
        if u > 0:
            pulse = (lt * 0.9) % 1.0
            c.save(); c.translate(ctx.cx, by); c.scale(e, e)
            D.rrect(c, -bw / 2 - 18 * s * pulse, -bh / 2 - 18 * s * pulse, bw + 36 * s * pulse, bh + 36 * s * pulse,
                    bh / 2 + 18 * s * pulse, D.paint(ctx.pal("accent", 0.5 * (1 - pulse)), stroke=4 * s))
            D.rrect(c, -bw / 2, -bh / 2, bw, bh, bh / 2,
                    D.paint(0, shader=D.linear_grad(-bw / 2, 0, bw / 2, 0, [ctx.pal("accent"), ctx.pal("accent2")])))
            D.draw_text(c, self.p["button"], 0, 0, bf, ctx.pal("#ffffff"), valign="middle")
            c.restore()
        if lt > tap - 0.5:
            k = E.clamp((lt - tap + 0.5) / 0.5)
            fx = ctx.cx + bw * 0.25 + (1 - E.out_cubic(k)) * 200 * s
            fy = by + 30 * s + (1 - E.out_cubic(k)) * 260 * s
            if lt > tap:
                rr = E.out_cubic((lt - tap) / 0.5)
                c.drawCircle(fx, fy, 70 * s * rr, D.paint(ctx.pal("#ffffff", 0.5 * (1 - rr)), stroke=4 * s))
            c.drawCircle(fx, fy, 26 * s * (0.85 if abs(lt - tap) < 0.1 else 1), D.paint(ctx.pal("#ffffff", 0.85)))
        if self.p["sub"]:
            sf = ctx.font("body", 36 * s, 500)
            a = E.clamp((lt - 0.8) / 0.5)
            D.draw_text(c, self.p["sub"], ctx.cx, by + 150 * s, sf, ctx.pal("muted", a))
