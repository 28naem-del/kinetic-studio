"""Scenes shared by every mode (title, text, image, logo, end card) + overlay painters.

Every size comes from the type scale (layout.TYPE roles) and every position from the frame's
Grid (columns, safe zones, the shared top anchor, baseline units g.bl(n)); there are no pixel
literals here, so the scenes hold at any resolution and at 9:16, 16:9 and 1:1."""
from __future__ import annotations

import os

import skia

from .. import draw as D
from .. import ease as E
from .. import layout as L
from .base import Ctx, Cue, Scene, _lift, register

T = L.TYPE


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


def _role(p: dict, style_key: str, size_key: str = "size") -> L.Role:
    """The role named by p[style_key]; an explicit p[size_key] (reference px) overrides its size."""
    base = T[p[style_key]] if p.get(style_key) in T else T["headline"]
    return base.with_(size=float(p[size_key])) if p.get(size_key) else base


def _anchor_x(g: L.Grid, align: str) -> float:
    return g.cx if align == "center" else g.x1 if align == "right" else g.x0


@register("title")
class Title(Scene):
    """Editorial title card: tracked kicker on the shared top anchor, a balanced headline under
    it, and the subtitle set as a standfirst on the bottom safe line under a growing hairline.
    `size` (reference px) overrides the `style` role size; `title_role` picks the theme face."""
    defaults = {"kicker": "", "title": "Title", "subtitle": "", "align": "left", "size": None,
                "style": "headline", "title_role": "display", "weight": None, "accent_line": -1,
                "sfx": "swish"}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.0, data={"sound": self.p["sfx"]}), Cue("sfx", 0.0, data={"sound": "chime", "gain": -6})]

    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        g = L.grid(ctx)
        align = self.p["align"]
        x = _anchor_x(g, align)
        r = _role(self.p, "style")
        f = L.fit_role(ctx, r, self.p["title"], g.head_w, weight=self.p["weight"], face=self.p["title_role"])
        rt = r.tk(ctx, self.p["title_role"])
        lines = D.wrap(f, self.p["title"], g.head_w, rt, style="balance")
        ink = ctx.pal.on(self.ground)

        kr, sr = T["kicker"], T["body"]
        kf = kr.font(ctx)
        kick = kr.text(self.p["kicker"])
        sub_f = sr.font(ctx, weight=400)
        sub_lines = D.wrap(sub_f, self.p["subtitle"], g.measure, sr.tk(ctx)) if self.p["subtitle"] else []

        gap = g.bl(6)
        head_h = D.block_height(f, len(lines), r.leading)
        kick_h = D.cap_height(kf) + gap if kick else 0.0
        sub_h = D.block_height(sub_f, len(sub_lines), sr.leading) if sub_lines else 0.0
        top = g.place(kick_h + head_h + (sub_h + 2 * gap if sub_lines else 0.0), "top")

        if kick:
            a = E.clamp(lt / 0.3)
            kx = x
            if align == "left":
                rule = g.col_w * E.out_cubic(lt / 0.5)
                c.drawRect(skia.Rect.MakeXYWH(kx, top + D.cap_height(kf) / 2 - g.bl(0.25), rule, g.bl(0.5)),
                           D.paint(ctx.pal("accent")))
                kx += g.col_w + g.gutter
            D.draw_text(c, kick, kx, top, kf, ctx.pal("accent", a), align=align, tracking=kr.tk(ctx), valign="top")
        blk = D.set_lines(f, lines, x, top + kick_h, r.leading, rt, align)
        n = len(lines)
        for i, ln in enumerate(blk.lines):
            hot = i == self.p["accent_line"] or (self.p["accent_line"] == -2 and i == n - 1)
            col = ctx.pal("accent") if hot else ctx.pal(ink)
            reveal_line(ctx, ln.text, ln.x, ln.baseline, f, col, (lt - 0.12 - i * 0.12) / 0.8, "left", rt)
        if sub_lines:
            sub_top = max(blk.baseline + 2 * gap, g.safe_bottom - sub_h)
            u = E.out_cubic(E.clamp((lt - 0.45) / 0.6))
            hair = g.content_w if align == "left" else g.span_w(6)
            hx = g.x0 if align == "left" else x - hair / 2 if align == "center" else x - hair
            c.drawRect(skia.Rect.MakeXYWH(hx, sub_top - gap, hair * u, max(1.0, g.bl(0.25))),
                       D.paint(ctx.pal(ink, 0.35)))
            sb = D.set_lines(sub_f, sub_lines, x, sub_top, sr.leading, sr.tk(ctx), align)
            for i, ln in enumerate(sb.lines):
                a = E.clamp((lt - 0.55 - i * 0.08) / 0.5)
                D.draw_text(c, ln.text, ln.x, ln.baseline + (1 - a) * g.bl(2), sub_f, ctx.pal("muted", a),
                            align="left", tracking=sr.tk(ctx))


@register("text")
class TextBlock(Scene):
    """A paragraph that fades in word by word, optional heading and highlighted terms. Set on the
    grid from the shared top anchor; `size` (reference px) overrides the body role size."""
    defaults = {"heading": "", "body": "", "highlight": [], "size": None, "align": "left", "rate": 0.06,
                "style": "body", "heading_style": "title"}

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        align = self.p["align"] if self.p["align"] in ("left", "center", "right") else "left"
        x = _anchor_x(g, align)
        br = _role(self.p, "style")
        hr = T[self.p["heading_style"]]
        f = br.font(ctx)
        lines = D.wrap(f, self.p["body"], g.measure, br.tk(ctx))
        hf = L.fit_role(ctx, hr, self.p["heading"], g.head_w, weight=600) if self.p["heading"] else None
        hlines = D.wrap(hf, self.p["heading"], g.head_w, hr.tk(ctx), style="balance") if hf else []
        gap = g.bl(7)
        h = D.block_height(f, len(lines), br.leading)
        hh = D.block_height(hf, len(hlines), hr.leading) + gap if hf else 0.0
        top = g.place(hh + h, "top")
        ink = ctx.pal.on(self.ground)
        if hf:
            hb = D.set_lines(hf, hlines, x, top, hr.leading, hr.tk(ctx), align)
            for i, ln in enumerate(hb.lines):
                reveal_line(ctx, ln.text, ln.x, ln.baseline, hf, ctx.pal(ink), (ctx.lt - i * 0.1) / 0.7, "left", hr.tk(ctx))
        hl = {h.lower() for h in self.p["highlight"]}
        blk = D.set_lines(f, lines, x, top + hh, br.leading, br.tk(ctx), align)
        rise = g.bl(2)
        k = 0
        for ln in blk.lines:
            gl = D.glyph_layout(f, ln.text, br.tk(ctx))
            idx = 0
            for w in ln.text.split():
                idx = ln.text.index(w, idx)
                wx = ln.x + gl[idx][1]
                idx += len(w)
                u = E.clamp((ctx.lt - 0.3 - k * self.p["rate"]) / 0.35)
                key = w.strip(".,;:!?()").lower()
                col = ctx.pal("accent", u) if key in hl else ctx.pal(ink, u * 0.92)
                D.draw_text(c, w, wx, ln.baseline + (1 - E.out_cubic(u)) * rise, f, col, align="left", tracking=br.tk(ctx))
                k += 1


@register("image")
class ImageScene(Scene):
    """Full-bleed picture with a Ken Burns push, gradient scrim and an optional caption set
    flush-left on the bottom safe line."""
    defaults = {"src": "", "zoom": [1.0, 1.14], "pan": [[-0.3, 0.0], [0.3, 0.0]], "caption": "", "scrim": 0.55,
                "caption_style": "title"}

    def draw(self, ctx: Ctx) -> None:
        g = L.grid(ctx)
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
            D.draw_text(ctx.c, f"missing image: {src}", g.cx, ctx.cy, T["meta"].font(ctx), ctx.pal("neg"))
        if self.p["scrim"]:
            sh = D.linear_grad(0, ctx.H * 0.45, 0, ctx.H, [ctx.pal("bg", 0), ctx.pal("bg", self.p["scrim"])])
            ctx.c.drawRect(skia.Rect(0, 0, ctx.W, ctx.H), D.paint(0, shader=sh))
        cap = self.caption_block(ctx)
        if cap:
            f, r, lines, top = cap
            for i, ln in enumerate(D.set_lines(f, lines, g.x0, top, r.leading, r.tk(ctx)).lines):
                reveal_line(ctx, ln.text, ln.x, ln.baseline, f, ctx.pal("ink"), (ctx.lt - 0.2 - i * 0.1) / 0.7,
                            "left", r.tk(ctx))


    def caption_block(self, ctx: Ctx) -> tuple[skia.Font, L.Role, list[str], float] | None:
        """(font, role, lines, cap top) of the caption block standing on the bottom safe line."""
        if not self.p["caption"]:
            return None
        g = L.grid(ctx)
        r = T[self.p["caption_style"]]
        f = L.fit_role(ctx, r, self.p["caption"], g.head_w, weight=700)
        lines = D.wrap(f, self.p["caption"], g.head_w, r.tk(ctx), style="balance")
        return f, r, lines, g.place(D.block_height(f, len(lines), r.leading), "bottom")


@register("logo")
class Logo(Scene):
    """Logo sting: a line draws, the wordmark rises through it, tracking tightens, tagline.
    Centred on the optical centre of the safe zone (a sting is the one place centring belongs)."""
    defaults = {"text": "BRAND", "tagline": "", "mark": "", "weight": 800, "role": "display",
                "stop_music": True, "line": True, "hit_at": 0.32, "url": "", "style": "display"}

    def cues(self) -> list[Cue]:
        h = float(self.p["hit_at"])
        out = [Cue("sfx", 0.0, data={"sound": "riser_short"}), Cue("sfx", h, data={"sound": "logo_hit"}),
               Cue("hit", h, data={"strength": 0.9, "flash": 0.35})]
        if self.p["stop_music"]:
            out.append(Cue("cue", h, name="music_stop"))
        return out

    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        g = L.grid(ctx)
        h = float(self.p["hit_at"])
        text = str(self.p["text"])
        r = T[self.p["style"]]
        loose = 0.35
        f = ctx.fit(self.p["role"], text, g.span_w(10), r.px(g), self.p["weight"], 100, loose)
        cap = D.cap_height(f)
        tr = E.lerp(loose, r.tk(ctx, self.p["role"]), E.out_expo((lt - h) / 1.2)) if lt > h else loose
        tw = D.text_w(f, text, tr)
        ink = ctx.pal.on(self.ground)
        tag_r, url_r = T["body"], T["meta"]
        y = g.place(cap, "center") + cap  # wordmark baseline
        mark = self.p["mark"]
        if mark:
            mr = f.getSize() * 0.32
            my = y - cap - g.bl(8) - mr
            e = E.out_expo(E.clamp((lt - h) / 0.45))
            if e > 0:
                if mark.endswith((".png", ".jpg", ".webp")):
                    path = mark if os.path.isabs(mark) else os.path.join(ctx.assets, mark)
                    try:
                        img = D.load_image(path)
                        c.save(); c.translate(g.cx, my); c.scale(e, e)
                        D.draw_cover(c, img, skia.Rect(-mr, -mr, mr, mr))
                        c.restore()
                    except (FileNotFoundError, ValueError):
                        pass
                else:
                    c.drawCircle(g.cx, my, mr * e, D.paint(ctx.pal("accent")))
                    mf = ctx.font(self.p["role"], mr * 1.1, 900)
                    D.draw_text(c, mark[:2], g.cx, my, mf, ctx.pal("ink_dark" if ctx.pal.on("accent") != ctx.theme["ink"] else "ink", e),
                                valign="middle")
        if self.p["line"]:
            lu = E.in_out_expo(lt / max(h, 0.05))
            lw = (tw + 2 * g.col_w) * lu
            fade = 1 - E.clamp((lt - h - 0.4) / 0.5)
            if fade > 0:
                c.drawRect(skia.Rect.MakeXYWH(g.cx - lw / 2, y + g.bl(3), lw, g.bl(0.625)), D.paint(ctx.pal("accent", fade)))
        x = g.cx - tw / 2
        for i, (ch, ox, _w) in enumerate(D.glyph_layout(f, text, tr)):
            u = (lt - h * 0.55 - i * 0.025) / 0.5
            c.save()
            c.clipRect(skia.Rect(-ctx.W, y - cap * 1.6, 2 * ctx.W, y + g.bl(3)))
            D.draw_text(c, ch, x + ox, y + (1 - E.out_expo(u)) * cap * 1.3, f, ctx.pal(ink), align="left")
            c.restore()
        ty = y + g.bl(6) + tag_r.px(g)
        tag = self.p["tagline"]
        if tag:
            tf = tag_r.font(ctx)
            a = E.clamp((lt - h - 0.35) / 0.6)
            D.draw_text(c, tag, g.cx, ty + (1 - a) * g.bl(2), tf, ctx.pal("muted", a), tracking=tag_r.tk(ctx))
        if self.p["url"]:
            uf = url_r.font(ctx)
            a = E.clamp((lt - h - 0.7) / 0.6)
            uy = ty + (tag_r.pitch(g) + g.bl(2) if tag else 0.0)
            D.draw_text(c, self.p["url"], g.cx, uy, uf, ctx.pal("accent", a), tracking=url_r.tk(ctx))


# ---------------------------------------------------------------------- contrast

def rel_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.x relative luminance of an sRGB colour."""
    def ch(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = rel_luminance(a), rel_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def ground_samples(pal: D.Palette, ground: str, style: str | None) -> list[tuple[int, int, int]]:
    """Colours a small overlay can land on over a ground key + pattern style: the base, plus the
    lifted pattern colours (grid lines, dots, blobs, mesh patches, the gradient's far end)."""
    base = pal.rgb(ground)
    out = [base]
    if ground == "bg" and style in ("gradient", "grid", "blobs", "mesh"):
        out.append(pal.rgb("bg2"))
    if style in ("blobs", "mesh", "dots", "grid") and ground == "bg":
        for key, d in (("accent", 0.22), ("accent2", 0.16), ("muted", 0.16)):
            try:
                out.append(_lift(pal, base, key, d))
            except KeyError:
                pass
    return out


def readable(pal: D.Palette, grounds: list[tuple[int, int, int]], min_ratio: float = 4.5,
             alpha: float = 0.85) -> tuple[tuple[int, int, int], float]:
    """(rgb, opacity) for small text over any of `grounds` with a WCAG contrast of at least
    `min_ratio` after blending: the theme ink or ink_dark (whichever reads better on the worst
    ground), at `alpha` raised as far as needed; pure white/black only as a last resort."""
    cands = [pal.rgb(k) for k in ("ink", "ink_dark") if k in pal.theme] + [(255, 255, 255), (0, 0, 0)]
    def worst(rgb: tuple[int, int, int], a: float) -> float:
        return min(contrast_ratio(D.mix_rgb(g, rgb, a), g) for g in grounds)
    best = max(cands[:2] or cands, key=lambda c: worst(c, 1.0))
    if worst(best, 1.0) < min_ratio:
        best = max(cands, key=lambda c: worst(c, 1.0))
    a = alpha
    while a < 1.0 and worst(best, a) < min_ratio:
        a = min(1.0, a + 0.025)
    return best, a


def _film_bloom(ctx: Ctx) -> float:
    film = getattr(ctx, "film", None)
    try:
        return float(film.cfg.get("post", {}).get("bloom", 0.0)) if film is not None else 0.0
    except (AttributeError, TypeError, ValueError):
        return 0.0


def cta_colors(pal: D.Palette, ground: str, bloom: float = 0.0
               ) -> tuple[tuple[int, int, int], tuple[int, int, int], bool]:
    """(fill, label, ring) for a CTA pill. Filled accent with the readable on-accent ink when that
    pair keeps >= 4.5:1 with headroom for the bloom pass (bloom spills the bright fill over dark
    glyphs: a 5.5:1 pair on the ink theme measures 2.4:1 after a 0.55 bloom); otherwise an accent
    ring on a dark accent-tinted fill with a light label, which bloom only brightens."""
    acc = pal.rgb("accent")
    on_acc = readable(pal, [acc], 4.5, 1.0)[0]
    if contrast_ratio(on_acc, acc) >= 4.5 * (1.0 + 2.0 * max(bloom, 0.0)):
        return acc, on_acc, False
    base = pal.rgb(ground) if ground in pal.theme else pal.rgb("bg")
    fill = D.mix_rgb(base, acc, 0.12)
    label = acc if contrast_ratio(acc, fill) >= 4.5 else readable(pal, [fill], 4.5, 1.0)[0]
    return fill, label, True


@register("end_card")
class EndCard(Scene):
    """Closing card on the grid, flush-left from the shared top anchor: a brand lockup (accent
    badge with an icon + wordmark on the subhead role), the statement (title role), an accent
    line (body), a CTA pill and the one handle, with small print standing on the bottom safe line.
    Elements enter one per half beat on the registry "enter" ease and the badge and CTA arrow pulse
    on every beat. The card owns the handle: the film's corner handle is hidden while it is on
    screen, and the handle colour is picked for >= 4.5:1 contrast on the card's ground."""
    defaults = {"icon": "book", "brand": "", "title": "For learning only.", "body": "Not medical advice.",
                "small": "", "handle": "", "cta": "", "stop_music": False, "sfx": "chime", "align": "left",
                "step": "1/2b"}
    owns_handle = True

    def cues(self) -> list[Cue]:
        out = [Cue("sfx", 0.0, data={"sound": self.p["sfx"]})]
        if self.p["stop_music"]:
            out.append(Cue("cue", 0.0, name="music_stop"))
        return out

    def _handle(self, ctx: Ctx) -> str:
        film = getattr(ctx, "film", None)
        return str(self.p["handle"] or (getattr(film, "handle", "") if film is not None else "") or "")

    def draw(self, ctx: Ctx) -> None:
        c = ctx.c
        g = L.grid(ctx)
        align = self.p["align"] if self.p["align"] in ("left", "center") else "left"
        x = _anchor_x(g, align)
        ink = ctx.pal.on(self.ground)
        style = self.ground_style or ctx.theme.get("ground_style", "solid")
        grounds = ground_samples(ctx.pal, self.ground, style)
        enter = E.resolve("enter")
        pop = E.resolve("pop")
        step = self.time(self.p["step"])
        beat = max(self.beats(1.0), 1e-3)

        def u(k: int, beats: float = 1.0) -> float:  # 0..1 entrance of element k
            return E.clamp((ctx.lt - k * step) / max(beats * beat, 1e-3))

        # beat pulse: a short decay after every beat from the second beat on
        lb = ctx.lt / beat
        pulse = (1.0 - (lb % 1.0)) ** 3 if lb >= 1.0 else 0.0

        sub, tr_, br, cr = T["subhead"], T["title"], T["body"], T["caption"]
        brand = str(self.p["brand"] or "")
        wf = sub.font(ctx, weight=700, face="display")
        wcap = D.cap_height(wf)
        R = wcap * 0.95                                       # badge radius on the wordmark's cap
        tf = L.fit_role(ctx, tr_, self.p["title"], g.head_w, weight=600)
        tlines = D.wrap(tf, self.p["title"], g.head_w, tr_.tk(ctx), style="balance")
        bf = br.font(ctx, weight=600)
        blines = D.wrap(bf, self.p["body"], g.measure, br.tk(ctx)) if self.p["body"] else []
        handle = self._handle(ctx)
        cta = str(self.p["cta"] or "")
        cf = br.font(ctx, weight=700)
        hf = br.font(ctx, weight=600)
        sf = cr.font(ctx, weight=400)
        slines = D.wrap(sf, self.p["small"], g.measure, cr.tk(ctx)) if self.p["small"] else []

        gap = g.bl(6)
        lock_h = 2 * R
        th = D.block_height(tf, len(tlines), tr_.leading)
        bh = D.block_height(bf, len(blines), br.leading) + gap if blines else 0.0
        pill_pad = g.bl(2.5)
        ch = (D.cap_height(cf) + 2 * pill_pad + 2 * gap) if cta else 0.0
        hh = (D.cap_height(hf) + gap) if handle else 0.0
        top = g.place(lock_h + 2 * gap + th + bh + ch + hh, "top")

        # 1. brand lockup: badge + wordmark, on beat 0
        e0 = u(0)
        if e0 > 0:
            k = pop(e0) * (1.0 + 0.05 * pulse)
            lock_w = 2 * R + (g.bl(3) + D.text_w(wf, brand, sub.tk(ctx, "display")) if brand else 0.0)
            bx = x + R if align == "left" else x - lock_w / 2 + R
            by = top + R
            c.drawCircle(bx, by, R * k, D.paint(ctx.pal("accent")))
            on_acc = readable(ctx.pal, [ctx.pal.rgb("accent")], 4.5, 1.0)[0]
            D.icon(c, self.p["icon"], bx, by, R * 1.05 * k, D.argb(on_acc, min(1.0, e0 * 1.5)))
            if brand:
                wx = bx + R + g.bl(3)
                wy = by + wcap / 2
                c.save()
                c.clipRect(skia.Rect(wx - g.bl(1), by - R - g.bl(2), ctx.W * 2, by + R + g.bl(2)))
                D.draw_text(c, brand, wx - (1 - enter(e0)) * g.bl(6), wy, wf, ctx.pal(ink, enter(e0)), align="left",
                            tracking=sub.tk(ctx, "display"))
                c.restore()
        y = top + lock_h + 2 * gap

        # 2. statement, line by line from the first half beat
        tb = D.set_lines(tf, tlines, x, y, tr_.leading, tr_.tk(ctx), align)
        for i, ln in enumerate(tb.lines):
            reveal_line(ctx, ln.text, ln.x, ln.baseline, tf, ctx.pal(ink), u(1) - i * 0.12, "left", tr_.tk(ctx))
        y = tb.baseline + gap
        if blines:
            bb = D.set_lines(bf, blines, x, y, br.leading, br.tk(ctx), align)
            for i, ln in enumerate(bb.lines):
                reveal_line(ctx, ln.text, ln.x, ln.baseline, bf, ctx.pal("accent"), u(2) - i * 0.12, "left", br.tk(ctx))
            y = bb.baseline + gap

        # 3. CTA pill (accent, on-accent ink) with an arrow that nudges on the beat
        if cta:
            e3 = enter(u(3))
            if e3 > 0:
                arrow = "\u2192"
                tw = D.text_w(cf, cta, br.tk(ctx))
                aw = D.text_w(cf, arrow, 0.0)
                cap = D.cap_height(cf)
                pw = tw + aw + g.bl(2) + 2 * pill_pad * 1.6
                ph = cap + 2 * pill_pad
                py = y + gap
                px = x if align == "left" else x - pw / 2
                fill, label, ring = cta_colors(ctx.pal, self.ground, _film_bloom(ctx))
                c.save()
                c.clipRect(skia.Rect(px - g.bl(1), py - g.bl(1), px + pw * e3 + g.bl(1), py + ph + g.bl(1)))
                D.rrect(c, px, py, pw, ph, ph / 2, D.paint(D.argb(fill)))
                if ring:
                    sw = max(1.5, g.bl(0.4))
                    D.rrect(c, px + sw / 2, py + sw / 2, pw - sw, ph - sw, (ph - sw) / 2,
                            D.paint(ctx.pal("accent"), stroke=sw))
                D.draw_text(c, cta, px + pill_pad * 1.6, py + pill_pad + cap, cf, D.argb(label), align="left",
                            tracking=br.tk(ctx))
                D.draw_text(c, arrow, px + pill_pad * 1.6 + tw + g.bl(2) + g.bl(1.5) * pulse, py + pill_pad + cap, cf,
                            D.argb(label), align="left")
                c.restore()
            y = y + ch

        # 4. the one handle, readable on every theme ground
        if handle:
            e4 = enter(u(4 if cta else 3))
            if e4 > 0:
                rgb, a = readable(ctx.pal, grounds, 4.5, 0.92)
                hy = y + (gap if not cta else 0.0) + D.cap_height(hf)
                D.draw_text(c, handle, x - (1 - e4) * g.bl(4), hy, hf, D.argb(rgb, a * e4), align=align,
                            tracking=br.tk(ctx))

        # 5. small print on the bottom safe line
        if slines:
            a = enter(u(5))
            sh = D.block_height(sf, len(slines), cr.leading)
            rgb, sa = readable(ctx.pal, grounds, 3.0, 0.62)
            for ln in D.set_lines(sf, slines, x, g.safe_bottom - sh, cr.leading, cr.tk(ctx), align).lines:
                D.draw_text(c, ln.text, ln.x, ln.baseline, sf, D.argb(rgb, sa * a), align="left", tracking=cr.tk(ctx))


# ---------------------------------------------------------------------- overlays

def bottom_reserved(ctx: Ctx) -> float:
    """Height taken on the bottom safe line by in-scene bottom-anchored blocks (image captions)
    at film time ctx.t, plus a gap, so overlays stack above them instead of overlapping. Eases
    in/out around the scene's start/end so a stacked overlay glides rather than jumps."""
    film = getattr(ctx, "film", None)
    if film is None:
        return 0.0
    g = L.grid(ctx)
    t, ramp, out = ctx.t, 0.45, 0.0
    for sc in getattr(film, "scenes", []):
        if not isinstance(sc, ImageScene) or not (sc.start - ramp <= t <= sc.start + sc.dur + ramp):
            continue
        cap = sc.caption_block(ctx)
        if not cap:
            continue
        w = E.in_out_cubic(E.clamp((t - sc.start + ramp) / ramp)) * E.in_out_cubic(E.clamp((sc.start + sc.dur + ramp - t) / ramp))
        out = max(out, w * (g.safe_bottom - cap[3] + g.bl(4)))
    return out


def draw_lower_third(ctx: Ctx, tag: str, text: str, u_in: float, u_out: float) -> None:
    """Source / citation lower-third sitting on the bottom safe line, stacked above an image
    caption that stands there (see bottom_reserved). u_in, u_out: 0..1 progress
    of the in/out moves."""
    c = ctx.c
    g = L.grid(ctx)
    e = E.out_expo(u_in) * (1 - E.in_cubic(u_out))
    if e <= 0.001:
        return
    kr, cr = T["kicker"], T["caption"]
    tf = kr.font(ctx, weight=800)
    bf = cr.font(ctx)
    pad = g.bl(4)
    maxw = min(g.content_w, g.measure + 2 * pad)
    lines = D.wrap(bf, text, maxw - 2 * pad, cr.tk(ctx))[:3]
    kcap = D.cap_height(tf)
    body = D.block_height(bf, len(lines), cr.leading) if lines else 0.0
    h = pad + kcap + g.bl(3) + body + pad
    y = g.safe_bottom - h - bottom_reserved(ctx)
    x = g.x0 - (1 - e) * (maxw + g.x0)
    w = min(maxw, max([D.text_w(bf, ln, cr.tk(ctx)) for ln in lines] + [D.text_w(tf, kr.text(tag), kr.tk(ctx))]) + 2 * pad)
    rad = g.bl(2)
    D.rrect(c, x, y, w, h, rad, D.paint(ctx.pal("bg", 0.82)))
    D.rrect(c, x, y, w, h, rad, D.paint(ctx.pal("accent", 0.5), stroke=g.bl(0.25)))
    c.drawRect(skia.Rect.MakeXYWH(x, y + rad, g.bl(0.75), h - 2 * rad), D.paint(ctx.pal("accent")))
    D.draw_text(c, kr.text(tag), x + pad, y + pad, tf, ctx.pal("accent"), align="left", tracking=kr.tk(ctx), valign="top")
    for ln in D.set_lines(bf, lines, x + pad, y + pad + kcap + g.bl(3), cr.leading, cr.tk(ctx)).lines:
        D.draw_text(c, ln.text, ln.x, ln.baseline, bf, ctx.pal("ink", 0.95), align="left", tracking=cr.tk(ctx))


def draw_caption(ctx: Ctx, text: str, u: float, local: float, dur: float, karaoke: bool = True) -> None:
    """Burned-in caption, centred on the grid's caption line; with karaoke, the word under the
    playhead sits on an accent pill."""
    c = ctx.c
    g = L.grid(ctx)
    r = T["body"]
    f = r.font(ctx, weight=800)
    tr = r.tk(ctx)
    words = text.split()
    lines = D.wrap(f, text, g.measure, tr, style="balance")
    blk = D.set_lines(f, lines, g.cx, 0.0, r.leading, tr, "center")
    blk = D.set_lines(f, lines, g.cx, g.caption_y - blk.h / 2, r.leading, tr, "center")
    a = E.clamp(u)
    k = 0
    cur = int(local / max(dur, 1e-3) * len(words)) if karaoke else -1
    cap = D.cap_height(f)
    pad = g.bl(1.25)
    sh = g.bl(0.375)
    for ln in blk.lines:
        gl = D.glyph_layout(f, ln.text, tr)
        idx = 0
        for w in ln.text.split():
            idx = ln.text.index(w, idx)
            x = ln.x + gl[idx][1]
            idx += len(w)
            ww = D.text_w(f, w, tr)
            y = ln.baseline
            if k == cur:
                D.rrect(c, x - pad, y - cap - pad * 1.4, ww + 2 * pad, cap + pad * 2.8, g.bl(1.5),
                        D.paint(ctx.pal("accent", a)))
                D.draw_text(c, w, x, y, f, ctx.pal("ink_dark", a), align="left", tracking=tr)
            else:
                D.draw_text(c, w, x + sh, y + sh, f, ctx.pal("bg", 0.6 * a), align="left", blur=sh, tracking=tr)
                D.draw_text(c, w, x, y, f, ctx.pal("ink", a * (1.0 if k < cur or cur < 0 else 0.78)), align="left",
                            tracking=tr)
            k += 1


def draw_handle(ctx: Ctx, handle: str, alpha: float = 0.85, ground: str | None = None) -> None:
    """Corner handle on the chrome line, in whichever theme ink reads at >= 4.5:1 on the ground
    under it (`ground` key, e.g. a slam scene's paper/accent flip; default the theme bg)."""
    g = L.grid(ctx)
    r = T["meta"]
    key = ground or "bg"
    try:
        grounds = ground_samples(ctx.pal, key, ctx.theme.get("ground_style", "solid"))
    except KeyError:
        grounds = [ctx.pal.rgb("bg")]
    rgb, a = readable(ctx.pal, grounds, 4.5, alpha * 0.8)
    D.draw_text(ctx.c, handle, g.x0, g.chrome_y, r.font(ctx, weight=600), D.argb(rgb, a),
                align="left", tracking=r.tk(ctx))


def draw_progress(ctx: Ctx, u: float) -> None:
    """Bottom progress bar, one baseline unit tall, with a bright accent tip."""
    g = L.grid(ctx)
    h = g.bl(1)
    x = ctx.W * E.clamp(u)
    ctx.c.drawRect(skia.Rect(0, ctx.H - h, x, ctx.H), D.paint(ctx.pal("accent", 0.6)))
    tip = min(x, g.bl(3))
    if tip > 0:
        ctx.c.drawRect(skia.Rect(x - tip, ctx.H - h, x, ctx.H), D.paint(ctx.pal("accent")))
