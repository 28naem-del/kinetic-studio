"""Kinetic-type scenes: slam, stack, type3d, scramble, marquee, burst, countdown."""
from __future__ import annotations

import math
import random

import skia

from .. import draw as D
from .. import ease as E
from .base import Ctx, Cue, Scene, register


def _words(p) -> list[str]:
    w = p.get("words", p.get("text", ""))
    return w if isinstance(w, list) else str(w).split()


def _max_type(ctx: Ctx) -> float:
    return (0.30 if ctx.portrait else 0.40) * min(ctx.H, ctx.W * 1.6)


@register("slam")
class Slam(Scene):
    """One word per beat, punched in at full width; grounds can flip per word."""
    defaults = {"per": "1b", "flip": True, "grounds": ["bg", "accent", "paper"], "style": "fill",
                "accent_words": [], "weight": 900, "width": 112, "echo": True, "sfx": "hit", "first_sfx": "impact"}

    def _per(self) -> float:
        return self.tl.resolve_dur(self.p["per"], self.start)

    def _index(self, lt: float) -> int:
        n = len(_words(self.p))
        return max(0, min(n - 1, int(lt / max(self._per(), 1e-3))))

    def ground_at(self, lt: float) -> str:
        if not self.p["flip"]:
            return self.ground
        g = self.p["grounds"]
        return g[self._index(lt) % len(g)]

    def cues(self) -> list[Cue]:
        out = []
        for i, _ in enumerate(_words(self.p)):
            at = i * self._per()
            snd = self.p["first_sfx"] if i == 0 else self.p["sfx"]
            out += [Cue("sfx", at, data={"sound": snd}), Cue("hit", at, data={"strength": 0.9 if i == 0 else 0.55})]
        return out

    def draw(self, ctx: Ctx) -> None:
        words = _words(self.p)
        if not words:
            return
        per = self._per()
        i = self._index(ctx.lt)
        u = ctx.lt - i * per
        word = words[i]
        ground = self.ground_at(ctx.lt)
        is_acc = word in self.p["accent_words"] or word.strip(".,!?") in self.p["accent_words"]
        color = ctx.pal.on(ground)
        if is_acc:
            color = ctx.theme["accent"] if ground != "accent" else ctx.theme["ink_dark"]
        land = E.out_expo(u / 0.16)
        width = E.lerp(70, self.p["width"], E.out_cubic(u / 0.22))
        f = ctx.fit("display", word, ctx.safe_w * 0.98, _max_type(ctx), self.p["weight"], width)
        sc = (1 + 0.45 * (1 - land)) * (1 + 0.035 * u)
        c = ctx.c
        c.save()
        c.translate(ctx.cx, ctx.cy)
        c.rotate((1 - land) * (-6 if i % 2 else 6))
        c.scale(sc, sc)
        outline = self.p["style"] == "outline" or (self.p["style"] == "mix" and i % 2 == 1)
        if self.p["echo"] and u < 0.5:
            for _k, (gs, ga) in enumerate(((1.18, 0.35), (1.42, 0.18))):
                a = ga * (1 - E.clamp(u / 0.5))
                c.save(); c.scale(gs + u * 0.6, gs + u * 0.6)
                D.draw_text(c, word, 0, 0, f, ctx.pal(color, a), stroke=3 * ctx.s, valign="middle")
                c.restore()
        D.draw_text(c, word, 0, 0, f, ctx.pal(color, E.clamp(u / 0.04)),
                    stroke=(5 * ctx.s if outline else 0), valign="middle")
        c.restore()


@register("stack")
class Stack(Scene):
    """Lines that each fill the width, arriving one per `per`, width-axis stretching in."""
    defaults = {"lines": ["MAKE", "IT", "MOVE"], "per": "1b", "accent_last": True, "weight": 900,
                "outline": [], "align": "center", "sfx": "whoosh", "gap": 0.06}

    def cues(self) -> list[Cue]:
        per = self.tl.resolve_dur(self.p["per"], self.start)
        out = []
        for i in range(len(self.p["lines"])):
            out.append(Cue("sfx", i * per, data={"sound": self.p["sfx"] if i < len(self.p["lines"]) - 1 else "hit"}))
            out.append(Cue("hit", i * per, data={"strength": 0.35}))
        return out

    def draw(self, ctx: Ctx) -> None:
        lines = self.p["lines"]
        per = self.tl.resolve_dur(self.p["per"], self.start)
        fonts = [ctx.fit("display", ln, ctx.safe_w, _max_type(ctx) * 0.85, self.p["weight"], 118) for ln in lines]
        caps = [D.cap_height(f) for f in fonts]
        gap = self.p["gap"] * ctx.H * 0.5
        total = sum(caps) + gap * (len(lines) - 1)
        lim = ctx.H * 0.78
        k = min(1.0, lim / total)
        y = ctx.cy - total * k / 2
        ink = ctx.pal.on(self.ground)
        for i, ln in enumerate(lines):
            u = ctx.lt - i * per
            h = caps[i] * k
            if u >= 0:
                e = E.out_expo(u / 0.35)
                width = E.lerp(62, 118, E.out_cubic(u / 0.4))
                f = ctx.fit("display", ln, ctx.safe_w, _max_type(ctx) * 0.85 * k, self.p["weight"], width)
                side = -1 if i % 2 else 1
                x = ctx.cx + side * (1 - e) * ctx.W * 0.9
                drift = side * 22 * ctx.s * ctx.lt
                last = i == len(lines) - 1
                col = ctx.theme["accent"] if (last and self.p["accent_last"]) else ink
                outl = ln in self.p["outline"]
                D.draw_text(ctx.c, ln, x + drift, y + h, f, ctx.pal(col), stroke=(4 * ctx.s if outl else 0))
            y += h + gap * k


@register("type3d")
class Type3D(Scene):
    """Extruded 3D type swinging in on a spring, then breathing in perspective."""
    defaults = {"text": "3D", "depth": 18, "weight": 900, "width": 110, "swing": 75, "sub": "", "color": "ink",
                "side": "accent", "sfx": "whoosh_big"}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.0, data={"sound": self.p["sfx"]}), Cue("sfx", 0.32, data={"sound": "impact"}),
                Cue("hit", 0.32, data={"strength": 0.8})]

    def shutter(self, lt: float) -> float:
        return 2.0 if lt < 0.35 else 1.0

    def draw(self, ctx: Ctx) -> None:
        text = str(self.p["text"])
        lt = ctx.lt
        f = ctx.fit("display", text, ctx.safe_w * 0.86, _max_type(ctx) * 1.05, self.p["weight"], self.p["width"])
        sp = E.spring(lt, 1.6, 5.5)
        ry = math.radians(self.p["swing"] * (1 - sp) + 16 * math.sin(lt * 1.3))
        rx = math.radians(10 * math.sin(lt * 0.9 + 0.5) - 6)
        rz = math.radians(-3 * (1 - sp))
        scale = 0.6 + 0.4 * E.out_back(lt / 0.45)
        depth = int(self.p["depth"])
        dz = 2.2 * ctx.s
        side_rgb = ctx.pal.rgb(self.p["side"])
        bg_rgb = ctx.pal.rgb(self.ground)
        c = ctx.c
        cap = D.cap_height(f)
        for k in range(depth, -1, -1):
            c.save()
            c.concat(D.persp3d(ctx.cx, ctx.cy, rx, ry, rz, dist=1400 * ctx.s, scale=scale, oz=k * dz))
            if k == 0:
                col = ctx.pal(self.p["color"])
            else:
                col = D.argb(D.mix_rgb(side_rgb, bg_rgb, 0.15 + 0.6 * k / depth))
            D.draw_text(c, text, ctx.cx, ctx.cy + cap / 2, f, col)
            c.restore()
        if self.p["sub"]:
            sf = ctx.font("body", 38 * ctx.s, 600)
            a = E.clamp((lt - 0.5) / 0.4)
            D.draw_text(c, self.p["sub"].upper(), ctx.cx, ctx.cy + cap * 0.9 + 70 * ctx.s, sf,
                        ctx.pal("muted", a), tracking=0.25)


GLYPHS = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#$%&*+=<>/\\"


@register("scramble")
class Scramble(Scene):
    """Glyph scramble that resolves letter by letter (code / reveal feel)."""
    defaults = {"text": "DECRYPTED", "label": "", "stagger": 0.05, "role": "mono", "weight": 800, "caret": True}

    def cues(self) -> list[Cue]:
        n = len(str(self.p["text"]))
        out = [Cue("sfx", 0.0, data={"sound": "glitch"})]
        for i in range(n):
            if i % max(1, n // 10) == 0:
                out.append(Cue("sfx", 0.18 + i * self.p["stagger"], data={"sound": "key", "gain": -4}))
        out.append(Cue("sfx", 0.2 + n * self.p["stagger"], data={"sound": "hit"}))
        out.append(Cue("hit", 0.2 + n * self.p["stagger"], data={"strength": 0.5}))
        return out

    def draw(self, ctx: Ctx) -> None:
        text = str(self.p["text"])
        f = ctx.fit(self.p["role"], text, ctx.safe_w, _max_type(ctx) * 0.6, self.p["weight"])
        lay = D.glyph_layout(f, text)
        total = lay[-1][1] + lay[-1][2] if lay else 0
        x0 = ctx.cx - total / 2
        cap = D.cap_height(f)
        ink = ctx.pal.on(self.ground)
        bucket = int(ctx.t * 20)
        rnd = random.Random(bucket * 7919 + sum(map(ord, text)))
        for i, (ch, ox, w) in enumerate(lay):
            tr = 0.18 + i * self.p["stagger"]
            if ctx.lt >= tr:
                g, col = ch, ink
                pop = 1 + 0.25 * (1 - E.out_cubic((ctx.lt - tr) / 0.12))
            else:
                g = rnd.choice(GLYPHS) if ch != " " else " "
                col, pop = ctx.theme["accent"], 1.0
                if ctx.lt < i * 0.012:
                    continue
            c = ctx.c
            c.save()
            c.translate(x0 + ox + w / 2, ctx.cy)
            c.scale(pop, pop)
            D.draw_text(c, g, 0, cap / 2, f, ctx.pal(col))
            c.restore()
        if self.p["caret"]:
            done = ctx.lt > 0.2 + len(text) * self.p["stagger"]
            if not done or int(ctx.lt * 2.5) % 2 == 0:
                cx = x0 + total + 14 * ctx.s
                ctx.c.drawRect(skia.Rect.MakeXYWH(cx, ctx.cy - cap / 2, cap * 0.09, cap), D.paint(ctx.pal("accent")))
        if self.p["label"]:
            lf = ctx.font("mono", 30 * ctx.s, 500)
            D.draw_text(ctx.c, self.p["label"], ctx.cx, ctx.cy - cap / 2 - 50 * ctx.s, lf, ctx.pal("muted"), tracking=0.15)


@register("marquee")
class Marquee(Scene):
    """Tilted rows of repeating type scrolling in opposite directions, centre row hero."""
    defaults = {"text": "MOTION", "rows": 7, "tilt": -8, "speed": 420, "hero": True, "weight": 900}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.0, data={"sound": "whoosh"})]

    def draw(self, ctx: Ctx) -> None:
        text = str(self.p["text"]).upper()
        rows = int(self.p["rows"])
        rh = ctx.H * 1.25 / rows
        f = ctx.font("display", rh * 0.92, self.p["weight"], 112)
        unit = text + "  •  "
        uw = D.text_w(f, unit)
        c = ctx.c
        ink = ctx.pal.on(self.ground)
        c.save()
        c.translate(ctx.cx, ctx.cy)
        c.rotate(self.p["tilt"])
        mid = rows // 2
        for r in range(rows):
            y = (r - mid) * rh + D.cap_height(f) / 2
            direction = 1 if r % 2 else -1
            off = (ctx.lt * self.p["speed"] * ctx.s * direction * (1.0 + 0.15 * abs(r - mid))) % uw
            x = -ctx.W * 1.2 - off
            hero = self.p["hero"] and r == mid
            col = ctx.pal("accent") if hero else ctx.pal(ink, 0.9 if r % 2 == 0 else 0.5)
            stroke = 0 if (hero or r % 2 == 0) else 3 * ctx.s
            while x < ctx.W * 1.2:
                D.draw_text(c, unit, x, y, f, col, align="left", stroke=stroke)
                x += uw
        c.restore()


@register("burst")
class Burst(Scene):
    """The drop: a word zooms from deep space with speed lines, ring and flash."""
    defaults = {"text": "NOW", "weight": 900, "width": 125, "lines": 70, "color": "accent"}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.0, data={"sound": "impact_big"}), Cue("hit", 0.0, data={"strength": 1.6, "flash": 0.9})]

    def shutter(self, lt: float) -> float:
        return 2.5 if lt < 0.25 else 1.0

    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        rnd = random.Random(42)
        R = math.hypot(ctx.W, ctx.H)
        speed = 1 - E.out_cubic(lt / max(self.dur, 0.1))
        ink = ctx.pal.on(self.ground)
        for _i in range(int(self.p["lines"])):
            a = rnd.uniform(0, 2 * math.pi)
            r0 = rnd.uniform(0.15, 1.0) * R * 0.5
            v = rnd.uniform(0.8, 1.6)
            r = (r0 + lt * R * 1.4 * v) % (R * 0.75) + R * 0.08
            ln = R * 0.12 * (0.4 + speed)
            p = D.paint(ctx.pal(ink, 0.18 + 0.35 * speed), stroke=rnd.uniform(1.5, 5) * ctx.s)
            c.drawLine(ctx.cx + math.cos(a) * r, ctx.cy + math.sin(a) * r,
                       ctx.cx + math.cos(a) * (r + ln), ctx.cy + math.sin(a) * (r + ln), p)
        ring = E.out_cubic(lt / 0.6)
        if ring < 1:
            c.drawCircle(ctx.cx, ctx.cy, R * 0.55 * ring,
                         D.paint(ctx.pal("accent2", 0.8 * (1 - ring)), stroke=26 * ctx.s * (1 - ring) + 2))
        f = ctx.fit("display", str(self.p["text"]), ctx.safe_w, _max_type(ctx) * 1.1, self.p["weight"], self.p["width"])
        z = 1 + 5 * (1 - E.out_expo(lt / 0.22))
        c.save()
        c.translate(ctx.cx, ctx.cy)
        c.scale(z * (1 + 0.04 * lt), z * (1 + 0.04 * lt))
        D.draw_text(c, str(self.p["text"]), 0, 0, f, ctx.pal(self.p["color"]), valign="middle")
        c.restore()


@register("countdown")
class Countdown(Scene):
    """Big numbers counting down on the beat (3, 2, 1) with a ring sweep."""
    defaults = {"from": 3, "per": "1b", "label": ""}

    def cues(self) -> list[Cue]:
        per = self.tl.resolve_dur(self.p["per"], self.start)
        return [Cue("sfx", i * per, data={"sound": "tick" if i < int(self.p["from"]) - 1 else "hit"})
                for i in range(int(self.p["from"]))]

    def draw(self, ctx: Ctx) -> None:
        per = self.tl.resolve_dur(self.p["per"], self.start)
        n = int(self.p["from"])
        i = min(n - 1, int(ctx.lt / per))
        u = (ctx.lt - i * per) / per
        num = str(n - i)
        f = ctx.font("display", _max_type(ctx) * 1.2, 900, 100)
        c = ctx.c
        rad = min(ctx.W, ctx.H) * 0.32
        c.drawArc(skia.Rect(ctx.cx - rad, ctx.cy - rad, ctx.cx + rad, ctx.cy + rad), -90, 360 * (1 - u), False,
                  D.paint(ctx.pal("accent"), stroke=14 * ctx.s))
        sc = 1 + 0.3 * (1 - E.out_expo(u * 3))
        c.save(); c.translate(ctx.cx, ctx.cy); c.scale(sc, sc)
        D.draw_text(c, num, 0, 0, f, ctx.pal(ctx.pal.on(self.ground)), valign="middle")
        c.restore()
