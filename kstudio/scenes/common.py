"""Scenes shared by every mode (title, text, image, logo, end card) + overlay painters."""
from __future__ import annotations

import os

import skia

from .. import draw as D
from .. import ease as E
from .base import Ctx, Cue, Scene, register


def reveal_line(ctx: Ctx, text: str, x: float, y: float, f: skia.Font, color: int, u: float,
                align: str = "left", tracking: float = 0.0) -> None:
    """Masked rise-in of one text line; u = 0..1 progress. y is the baseline."""
    cap = D.cap_height(f)
    e = E.out_expo(u)
    c = ctx.c
    c.save()
    pad = cap * 0.45
    c.clipRect(skia.Rect(-ctx.W, y - cap - pad, 2 * ctx.W, y + pad * 1.2))
    D.draw_text(c, text, x, y + (1 - e) * (cap + pad * 2), f, color, align=align, tracking=tracking)
    c.restore()


@register("title")
class Title(Scene):
    """Kicker + multi-line title (masked rise) + subtitle + growing rule."""
    defaults = {"kicker": "", "title": "Title", "subtitle": "", "align": "left", "size": 128,
                "title_role": "display", "weight": 600, "accent_line": -1, "sfx": "swish"}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.0, data={"sound": self.p["sfx"]}), Cue("sfx", 0.15, data={"sound": "chime", "gain": -6})]

    def draw(self, ctx: Ctx) -> None:
        c, lt, s = ctx.c, ctx.lt, ctx.s
        align = self.p["align"]
        x = ctx.margin * 1.2 if align == "left" else ctx.cx
        maxw = ctx.safe_w * 0.96
        size = self.p["size"] * s * (1.0 if ctx.portrait else 0.9)
        f = ctx.font(self.p["title_role"], size, self.p["weight"], 100)
        lines = D.wrap(f, self.p["title"], maxw)
        lh = size * 1.08
        sub_f = ctx.font("body", 40 * s, 400)
        sub_lines = D.wrap(sub_f, self.p["subtitle"], maxw * 0.92) if self.p["subtitle"] else []
        block = len(lines) * lh + (len(sub_lines) * 56 * s + 40 * s if sub_lines else 0) + 110 * s
        y = ctx.cy - block / 2 + 70 * s
        ink = ctx.pal.on(self.ground)
        if self.p["kicker"]:
            kf = ctx.font("body", 30 * s, 700)
            a = E.clamp(lt / 0.3)
            kx = x
            if align == "left":
                rule = 56 * s * E.out_cubic(lt / 0.5)
                c.drawRect(skia.Rect.MakeXYWH(kx, y - 12 * s, rule, 4 * s), D.paint(ctx.pal("accent")))
                kx += 76 * s
            D.draw_text(c, self.p["kicker"].upper(), kx, y, kf, ctx.pal("accent", a), align=align, tracking=0.22)
        y += 40 * s
        for i, ln in enumerate(lines):
            y += lh
            col = ctx.pal("accent") if i == self.p["accent_line"] or (self.p["accent_line"] == -2 and i == len(lines) - 1) else ctx.pal(ink)
            reveal_line(ctx, ln, x, y - lh * 0.22, f, col, (lt - 0.12 - i * 0.12) / 0.8, align)
        y += 30 * s
        for i, ln in enumerate(sub_lines):
            y += 56 * s
            a = E.clamp((lt - 0.55 - i * 0.08) / 0.5)
            D.draw_text(c, ln, x, y + (1 - a) * 14 * s, sub_f, ctx.pal("muted", a), align=align)


@register("text")
class TextBlock(Scene):
    """A paragraph that fades in word by word, optional heading and highlighted terms."""
    defaults = {"heading": "", "body": "", "highlight": [], "size": 54, "align": "left", "rate": 0.06}

    def draw(self, ctx: Ctx) -> None:
        c, s = ctx.c, ctx.s
        f = ctx.font("body", self.p["size"] * s, 500)
        lines = D.wrap(f, self.p["body"], ctx.safe_w * 0.95)
        lh = self.p["size"] * s * 1.38
        hf = ctx.font("display", 74 * s, 600)
        top = ctx.cy - (len(lines) * lh + (110 * s if self.p["heading"] else 0)) / 2
        x0 = ctx.margin * 1.2 if self.p["align"] == "left" else None
        ink = ctx.pal.on(self.ground)
        if self.p["heading"]:
            reveal_line(ctx, self.p["heading"], x0 or ctx.cx, top + 74 * s, hf, ctx.pal(ink), ctx.lt / 0.7,
                        "left" if x0 else "center")
            top += 120 * s
        hl = {h.lower() for h in self.p["highlight"]}
        k = 0
        for li, ln in enumerate(lines):
            y = top + (li + 1) * lh
            words = ln.split()
            lw = D.text_w(f, ln)
            x = x0 if x0 is not None else ctx.cx - lw / 2
            sp = D.text_w(f, " ")
            for w in words:
                u = E.clamp((ctx.lt - 0.3 - k * self.p["rate"]) / 0.35)
                key = w.strip(".,;:!?()").lower()
                col = ctx.pal("accent", u) if key in hl else ctx.pal(ink, u * 0.92)
                D.draw_text(c, w, x, y + (1 - E.out_cubic(u)) * 18 * s, f, col, align="left")
                x += D.text_w(f, w) + sp
                k += 1


@register("image")
class ImageScene(Scene):
    """Full-bleed picture with a Ken Burns push, gradient scrim and optional caption."""
    defaults = {"src": "", "zoom": [1.0, 1.14], "pan": [[-0.3, 0.0], [0.3, 0.0]], "caption": "", "scrim": 0.55}

    def draw(self, ctx: Ctx) -> None:
        src = self.p["src"]
        path = src if os.path.isabs(src) else os.path.join(ctx.assets, src)
        u = E.in_out_quad(ctx.lt / max(self.dur, 1e-3))
        try:
            img = D.load_image(path)
            z = E.lerp(self.p["zoom"][0], self.p["zoom"][1], u)
            pa, pb = self.p["pan"]
            D.draw_cover(ctx.c, img, skia.Rect(0, 0, ctx.W, ctx.H), z,
                         (E.lerp(pa[0], pb[0], u), E.lerp(pa[1], pb[1], u)))
        except (FileNotFoundError, ValueError):
            D.draw_text(ctx.c, f"missing image: {src}", ctx.cx, ctx.cy, ctx.font("mono", 30 * ctx.s, 500),
                        ctx.pal("neg"))
        if self.p["scrim"]:
            sh = D.linear_grad(0, ctx.H * 0.45, 0, ctx.H, [ctx.pal("bg", 0), ctx.pal("bg", self.p["scrim"])])
            ctx.c.drawRect(skia.Rect(0, 0, ctx.W, ctx.H), D.paint(0, shader=sh))
        if self.p["caption"]:
            f = ctx.font("display", 72 * ctx.s, 700)
            for i, ln in enumerate(D.wrap(f, self.p["caption"], ctx.safe_w)):
                reveal_line(ctx, ln, ctx.margin, ctx.H * 0.66 + i * 84 * ctx.s, f, ctx.pal("ink"),
                            (ctx.lt - 0.2 - i * 0.1) / 0.7)


@register("logo")
class Logo(Scene):
    """Logo sting: a line draws, the wordmark rises through it, tracking tightens, tagline."""
    defaults = {"text": "BRAND", "tagline": "", "mark": "", "weight": 800, "role": "display",
                "stop_music": True, "line": True, "hit_at": 0.32, "url": ""}

    def cues(self) -> list[Cue]:
        h = float(self.p["hit_at"])
        out = [Cue("sfx", 0.0, data={"sound": "riser_short"}), Cue("sfx", h, data={"sound": "logo_hit"}),
               Cue("hit", h, data={"strength": 0.9, "flash": 0.35})]
        if self.p["stop_music"]:
            out.append(Cue("cue", h, name="music_stop"))
        return out

    def draw(self, ctx: Ctx) -> None:
        c, lt, s = ctx.c, ctx.lt, ctx.s
        h = float(self.p["hit_at"])
        text = str(self.p["text"])
        f = ctx.fit(self.p["role"], text, ctx.safe_w * 0.82, 210 * s, self.p["weight"], 100)
        cap = D.cap_height(f)
        tr = E.lerp(0.35, 0.02, E.out_expo((lt - h) / 1.2)) if lt > h else 0.35
        tw = D.text_w(f, text, tr)
        ink = ctx.pal.on(self.ground)
        y = ctx.cy + cap / 2
        mark = self.p["mark"]
        if mark:
            mr = 70 * s
            my = ctx.cy - cap - 60 * s
            e = E.out_back(E.clamp((lt - h) / 0.45))
            if e > 0:
                if mark.endswith((".png", ".jpg", ".webp")):
                    path = mark if os.path.isabs(mark) else os.path.join(ctx.assets, mark)
                    try:
                        img = D.load_image(path)
                        c.save(); c.translate(ctx.cx, my); c.scale(e, e)
                        D.draw_cover(c, img, skia.Rect(-mr, -mr, mr, mr))
                        c.restore()
                    except (FileNotFoundError, ValueError):
                        pass
                else:
                    c.drawCircle(ctx.cx, my, mr * e, D.paint(ctx.pal("accent")))
                    mf = ctx.font(self.p["role"], mr * 1.1, 900)
                    D.draw_text(c, mark[:2], ctx.cx, my, mf, ctx.pal("ink_dark" if ctx.pal.on("accent") != ctx.theme["ink"] else "ink", e),
                                valign="middle")
        if self.p["line"]:
            lu = E.in_out_expo(lt / max(h, 0.05))
            lw = (tw + 120 * s) * lu
            fade = 1 - E.clamp((lt - h - 0.4) / 0.5)
            if fade > 0:
                c.drawRect(skia.Rect.MakeXYWH(ctx.cx - lw / 2, y + 26 * s, lw, 5 * s), D.paint(ctx.pal("accent", fade)))
        x = ctx.cx - tw / 2
        for i, (ch, ox, _w) in enumerate(D.glyph_layout(f, text, tr)):
            u = (lt - h * 0.55 - i * 0.025) / 0.5
            c.save()
            c.clipRect(skia.Rect(-ctx.W, y - cap * 1.6, 2 * ctx.W, y + 24 * s))
            D.draw_text(c, ch, x + ox, y + (1 - E.out_expo(u)) * cap * 1.3, f, ctx.pal(ink), align="left")
            c.restore()
        tag = self.p["tagline"]
        if tag:
            tf = ctx.font("body", 40 * s, 500)
            a = E.clamp((lt - h - 0.35) / 0.6)
            D.draw_text(c, tag, ctx.cx, y + 110 * s + (1 - a) * 16 * s, tf, ctx.pal("muted", a), tracking=0.04)
        if self.p["url"]:
            uf = ctx.font("mono", 30 * s, 500)
            a = E.clamp((lt - h - 0.7) / 0.6)
            D.draw_text(c, self.p["url"], ctx.cx, y + 180 * s, uf, ctx.pal("accent", a), tracking=0.08)


@register("end_card")
class EndCard(Scene):
    """Calm closing card: icon badge, statement, small print, handle. Use for disclaimers."""
    defaults = {"icon": "book", "title": "For learning only.", "body": "Not medical advice.",
                "small": "", "handle": "", "stop_music": False, "sfx": "chime"}

    def cues(self) -> list[Cue]:
        out = [Cue("sfx", 0.05, data={"sound": self.p["sfx"]})]
        if self.p["stop_music"]:
            out.append(Cue("cue", 0.05, name="music_stop"))
        return out

    def draw(self, ctx: Ctx) -> None:
        c, lt, s = ctx.c, ctx.lt, ctx.s
        ink = ctx.pal.on(self.ground)
        y = ctx.cy - 160 * s
        e = E.out_back(E.clamp(lt / 0.5))
        r = 74 * s * e
        if r > 0:
            c.drawCircle(ctx.cx, y, r, D.paint(ctx.pal("accent", 0.14)))
            c.drawCircle(ctx.cx, y, r, D.paint(ctx.pal("accent", 0.9), stroke=3 * s))
            D.icon(c, self.p["icon"], ctx.cx, y, 64 * s * e, ctx.pal("accent"))
        tf = ctx.fit("display", self.p["title"], ctx.safe_w, 92 * s, 600)
        reveal_line(ctx, self.p["title"], ctx.cx, y + 210 * s, tf, ctx.pal(ink), (lt - 0.2) / 0.8, "center")
        bf = ctx.fit("body", self.p["body"], ctx.safe_w, 54 * s, 600)
        reveal_line(ctx, self.p["body"], ctx.cx, y + 300 * s, bf, ctx.pal("accent"), (lt - 0.4) / 0.8, "center")
        if self.p["small"]:
            sf = ctx.font("body", 30 * s, 400)
            a = E.clamp((lt - 0.8) / 0.6)
            for i, ln in enumerate(D.wrap(sf, self.p["small"], ctx.safe_w * 0.86)):
                D.draw_text(c, ln, ctx.cx, y + 390 * s + i * 44 * s, sf, ctx.pal("muted", a))
        if self.p["handle"]:
            hf = ctx.font("mono", 34 * s, 600)
            a = E.clamp((lt - 1.0) / 0.6)
            D.draw_text(c, self.p["handle"], ctx.cx, ctx.H * 0.78, hf, ctx.pal(ink, a), tracking=0.06)


# ---------------------------------------------------------------------- overlays

def draw_lower_third(ctx: Ctx, tag: str, text: str, u_in: float, u_out: float) -> None:
    """Source / citation lower-third. u_in, u_out: 0..1 progress of the in/out moves."""
    c, s = ctx.c, ctx.s
    e = E.out_expo(u_in) * (1 - E.in_cubic(u_out))
    if e <= 0.001:
        return
    tf = ctx.font("body", 24 * s, 800)
    bf = ctx.font("body", 31 * s, 500)
    maxw = ctx.safe_w * 0.95
    lines = D.wrap(bf, text, maxw - 60 * s)[:3]
    h = 66 * s + len(lines) * 42 * s
    y = ctx.H * (0.735 if ctx.portrait else 0.76) - h / 2
    x = ctx.margin - (1 - e) * (maxw + ctx.margin)
    w = min(maxw, max(D.text_w(bf, ln) for ln in lines) + 60 * s)
    D.rrect(c, x, y, w, h, 18 * s, D.paint(ctx.pal("bg", 0.82)))
    D.rrect(c, x, y, w, h, 18 * s, D.paint(ctx.pal("accent", 0.5), stroke=2 * s))
    c.drawRect(skia.Rect.MakeXYWH(x, y + 16 * s, 6 * s, h - 32 * s), D.paint(ctx.pal("accent")))
    D.draw_text(c, tag.upper(), x + 30 * s, y + 40 * s, tf, ctx.pal("accent"), align="left", tracking=0.2)
    for i, ln in enumerate(lines):
        D.draw_text(c, ln, x + 30 * s, y + 84 * s + i * 42 * s, bf, ctx.pal("ink", 0.95), align="left")


def draw_caption(ctx: Ctx, text: str, u: float, local: float, dur: float, karaoke: bool = True) -> None:
    """Burned-in caption box; with karaoke, the word under the playhead is highlighted."""
    c, s = ctx.c, ctx.s
    f = ctx.font("body", 50 * s, 800)
    words = text.split()
    lines = D.wrap(f, text, ctx.safe_w * 0.9)
    lh = 66 * s
    y0 = ctx.H * (0.64 if ctx.portrait else 0.80) - len(lines) * lh / 2
    a = E.clamp(u)
    k = 0
    cur = int(local / max(dur, 1e-3) * len(words)) if karaoke else -1
    for li, ln in enumerate(lines):
        lw = D.text_w(f, ln)
        x = ctx.cx - lw / 2
        y = y0 + (li + 1) * lh
        sp = D.text_w(f, " ")
        for w in ln.split():
            ww = D.text_w(f, w)
            if k == cur:
                D.rrect(c, x - 10 * s, y - 50 * s, ww + 20 * s, 64 * s, 12 * s, D.paint(ctx.pal("accent", a)))
                D.draw_text(c, w, x, y, f, ctx.pal("ink_dark", a), align="left")
            else:
                D.draw_text(c, w, x + 3 * s, y + 4 * s, f, ctx.pal("bg", 0.6 * a), align="left", blur=3 * s)
                D.draw_text(c, w, x, y, f, ctx.pal("ink", a * (1.0 if k < cur or cur < 0 else 0.78)), align="left")
            x += ww + sp
            k += 1


def draw_handle(ctx: Ctx, handle: str, alpha: float = 0.85) -> None:
    f = ctx.font("mono", 28 * ctx.s, 600)
    y = ctx.H * (0.075 if ctx.portrait else 0.08)
    D.draw_text(ctx.c, handle, ctx.margin, y, f, ctx.pal("ink", alpha * 0.8), align="left", tracking=0.05)


def draw_progress(ctx: Ctx, u: float) -> None:
    h = 6 * ctx.s
    ctx.c.drawRect(skia.Rect(0, ctx.H - h, ctx.W * u, ctx.H), D.paint(ctx.pal("accent", 0.85)))
