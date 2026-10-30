"""Social clip scenes: karaoke word-highlight captions and pull-quotes."""
from __future__ import annotations

import skia

from .. import draw as D
from .. import ease as E
from .base import Ctx, Cue, Scene, register


@register("karaoke")
class Karaoke(Scene):
    """Big captions paged a few lines at a time; the active word gets a springy pill.
    Word timing: "even" over the scene, "beat" (one word per `per` beats) or explicit
    [{"w": "word", "t": 1.25}, ...] in scene-local seconds."""
    defaults = {"text": "", "words": None, "timing": "even", "per": 0.5, "lines_per_page": 3,
                "size": 92, "lead": 0.25, "tail": 0.6, "emphasis": [], "kicker": ""}

    def _timed(self) -> list[tuple[str, float]]:
        if self.p["words"]:
            out = []
            for w in self.p["words"]:
                out.append((w["w"], float(w["t"])) if isinstance(w, dict) else (str(w), -1.0))
            if all(t >= 0 for _, t in out):
                return out
            words = [w for w, _ in out]
        else:
            words = self.p["text"].split()
        n = max(len(words), 1)
        if self.p["timing"] == "beat":
            return [(w, self.p["lead"] + self.bt(i * float(self.p["per"]))) for i, w in enumerate(words)]
        span = max(self.dur - self.p["lead"] - self.p["tail"], 0.5)
        return [(w, self.p["lead"] + span * i / n) for i, w in enumerate(words)]

    def cues(self) -> list[Cue]:
        return [Cue("cue", t, data={"word": w}) for w, t in self._timed()]

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        timed = self._timed()
        f = ctx.font("display", self.p["size"] * s, 800)
        maxw = ctx.safe_w
        # layout lines greedily
        lines: list[list[int]] = [[]]
        cur_w = 0.0
        sp = D.text_w(f, " ")
        for i, (w, _) in enumerate(timed):
            ww = D.text_w(f, w)
            if lines[-1] and cur_w + sp + ww > maxw:
                lines.append([])
                cur_w = 0.0
            cur_w += (sp if lines[-1] else 0) + ww
            lines[-1].append(i)
        active = max((i for i, (_, t) in enumerate(timed) if t <= lt), default=-1)
        lpp = int(self.p["lines_per_page"])
        line_of = {i: li for li, ln in enumerate(lines) for i in ln}
        page = line_of.get(max(active, 0), 0) // lpp
        page_lines = lines[page * lpp:(page + 1) * lpp]
        page_start_t = timed[page_lines[0][0]][1] if page_lines and page_lines[0] else 0.0
        pin = E.out_expo((lt - page_start_t + 0.15) / 0.35) if page > 0 else E.out_expo(lt / 0.35)
        lh = self.p["size"] * s * 1.22
        y0 = ctx.cy - len(page_lines) * lh / 2 + lh * 0.8
        if self.p["kicker"]:
            kf = ctx.font("mono", 30 * s, 600)
            D.draw_text(c, self.p["kicker"].upper(), ctx.cx, y0 - lh * 1.2, kf, ctx.pal("accent"), tracking=0.2)
        emph = {e.lower().strip(".,!?") for e in self.p["emphasis"]}
        for li, ln in enumerate(page_lines):
            words = [timed[i][0] for i in ln]
            lw = D.text_w(f, " ".join(words))
            x = ctx.cx - lw / 2
            y = y0 + li * lh + (1 - pin) * 40 * s
            for i in ln:
                w, t0 = timed[i]
                ww = D.text_w(f, w)
                is_e = w.lower().strip(".,!?") in emph
                if i == active:
                    k = E.spring(lt - t0, 3.0, 9.0)
                    padx, h = 18 * s, lh * 0.92
                    c.save(); c.translate(x + ww / 2, y - D.cap_height(f) / 2); c.scale(0.9 + 0.1 * k, 0.9 + 0.1 * k)
                    D.rrect(c, -ww / 2 - padx, -h / 2, ww + 2 * padx, h, 20 * s, D.paint(ctx.pal("accent2" if is_e else "accent")))
                    c.rotate(-2.0 * (1 - k))
                    D.draw_text(c, w, 0, 0, f, ctx.pal("ink_dark"), valign="middle")
                    c.restore()
                else:
                    said = i < active
                    col = ctx.pal("accent" if (is_e and said) else "ink", (1.0 if said else 0.35) * pin)
                    D.draw_text(c, w, x + 4 * s, y + 6 * s, f, ctx.pal("bg", 0.5 * pin), align="left", blur=5 * s)
                    D.draw_text(c, w, x, y, f, col, align="left")
                x += ww + sp


@register("quote")
class Quote(Scene):
    """Pull-quote: giant quote mark, words fading in on a stagger, author with rule."""
    defaults = {"text": "", "author": "", "role": "", "size": 84, "stagger": 0.07}

    def cues(self) -> list[Cue]:
        n = len(self.p["text"].split())
        return [Cue("sfx", 0.1, data={"sound": "swish", "gain": -6}),
                Cue("sfx", 0.4 + n * self.p["stagger"], data={"sound": "chime", "gain": -6})]

    def draw(self, ctx: Ctx) -> None:
        c, s, lt = ctx.c, ctx.s, ctx.lt
        f = ctx.font("serif", self.p["size"] * s, 500)
        qf = ctx.font("serif", 420 * s, 800)
        lines = D.wrap(f, self.p["text"], ctx.safe_w)
        lh = self.p["size"] * s * 1.25
        y0 = ctx.cy - len(lines) * lh / 2
        qa = E.out_expo(lt / 0.8)
        D.draw_text(c, "\u201c", ctx.margin - 10 * s, y0 + 170 * s - (1 - qa) * 60 * s, qf, ctx.pal("accent", 0.9 * qa), align="left")
        k = 0
        sp = D.text_w(f, " ")
        for li, ln in enumerate(lines):
            x = ctx.margin
            y = y0 + (li + 1) * lh + 80 * s
            for w in ln.split():
                u = E.clamp((lt - 0.3 - k * self.p["stagger"]) / 0.5)
                D.draw_text(c, w, x, y + (1 - E.out_cubic(u)) * 24 * s, f, ctx.pal("ink", u), align="left", blur=(1 - u) * 6 * s)
                x += D.text_w(f, w) + sp
                k += 1
        done = 0.4 + k * self.p["stagger"]
        u = E.out_expo((lt - done) / 0.6)
        ya = y0 + (len(lines) + 1) * lh + 130 * s
        c.drawRect(skia.Rect(ctx.margin, ya - 14 * s, ctx.margin + 90 * s * u, ya - 8 * s), D.paint(ctx.pal("accent")))
        af = ctx.font("body", 40 * s, 700)
        rf = ctx.font("body", 30 * s, 500)
        D.draw_text(c, self.p["author"], ctx.margin + 110 * s, ya, af, ctx.pal("ink", u), align="left")
        if self.p["role"]:
            D.draw_text(c, self.p["role"], ctx.margin + 110 * s, ya + 44 * s, rf, ctx.pal("muted", u), align="left")
