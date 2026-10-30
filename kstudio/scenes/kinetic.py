"""Kinetic-type scenes: slam, stack, type3d, scramble, marquee, burst, countdown.

Timing is in beats. Every timing parameter below accepts beat expressions ("1b", "1/2b",
"1/3b") or seconds; defaults are beat units, so landings, SFX and camera hits share one grid.
Easing follows the house grammar: entries out_expo / out_quint, exits in_quart / in_expo,
moves in_out_quint; no springs or out_back on type.

Variable axes: headline words land by animating the Archivo axes, wdth 70 -> `width`
(default 112) and wght 600 -> `weight` (default 900), out_expo over `land` (default "1/3b").
The size is fitted to the final instance, so the word stretches out to full width on the hit.

Chrome (all kinetic scenes): when the mode sets "chrome": true (kinetic does) or a scene sets
"chrome": true, a small grid-aligned frame is drawn: a scene index, the tempo with a beat dot
and a running beat counter above a ticker strip at the bottom safe line. The ticker
scrolls on the beat grid across cuts and lists the film's own words (override with
"ticker": "TEXT"; "chrome": false hides it per scene).
"""
from __future__ import annotations

import math
import random

import skia

from .. import draw as D
from .. import ease as E
from .. import layout as L
from .base import Ctx, Cue, Scene, register

LAND = "1/3b"            # slam landing (0.156 s at 128 BPM): out_expo on scale + axes
AX_W0, AX_G0 = 70.0, 600.0  # landing starts condensed + semibold


def _words(p) -> list[str]:
    w = p.get("words", p.get("text", ""))
    return w if isinstance(w, list) else str(w).split()


def _max_type(ctx: Ctx) -> float:
    return (0.30 if ctx.portrait else 0.40) * min(ctx.H, ctx.W * 1.6)


def _hero_w(ctx: Ctx) -> float:
    """Width a full-bleed hero word is fitted to (90 % of the frame width)."""
    return ctx.W * 0.90


def _axfont(ctx: Ctx, role: str, size: float, wght: float, wdth: float) -> skia.Font:
    """Variable instance quantised (wght 10, wdth 1) so animated axes reuse cached typefaces."""
    return ctx.font(role, size, int(round(wght / 10.0) * 10), float(round(wdth)))


def _land_axes(u: float, land: float, weight: float, width: float, w0: float = AX_W0,
               g0: float = AX_G0) -> tuple[float, float, float]:
    """(eased progress, wght, wdth) of a landing `u` seconds after the hit."""
    e = E.out_expo(u / max(land, 1e-4)) if u >= 0 else 0.0
    return e, E.lerp(g0, weight, e), E.lerp(w0, width, e)


def _beat_pulse(ctx: Ctx, decay_beats: float = 0.5, since: float = 0.0) -> float:
    """1 on every beat of the scene, decaying out_expo to 0 over `decay_beats` (beat-locked)."""
    b = ctx.local_beat - since
    if b < 0:
        return 0.0
    f = b - math.floor(b)
    return 1.0 - E.out_expo(f / max(decay_beats, 1e-3))


def _ink_on(ctx: Ctx, ground: str) -> str:
    return ctx.pal.on(ground)


def _accent_on(ctx: Ctx, ground: str) -> str:
    return ctx.theme.get("ink_dark", "bg") if ground == "accent" else "accent"


def _shade(rgb: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return (int(rgb[0] * k), int(rgb[1] * k), int(rgb[2] * k))


def _repeat_row(c: skia.Canvas, text: str, y: float, f: skia.Font, color: int, offset: float, W: float,
                stroke: float = 0.0, sep: str = "  ") -> None:
    """One row of `text` repeated across the frame, shifted by `offset` px (wraps seamlessly)."""
    unit = text + sep
    uw = max(D.text_w(f, unit), 1.0)
    x = -uw + (offset % uw) - W * 0.1
    while x < W * 1.1:
        D.draw_text(c, unit, x, y, f, color, align="left", stroke=stroke)
        x += uw


# ---------------------------------------------------------------------- chrome

_TICKER: dict[int, list[tuple[int, str]]] = {}


def _scene_words(sc: Scene) -> str:
    p = sc.p
    if isinstance(p.get("lines"), list) and sc.type == "stack":
        return " ".join(str(x) for x in p["lines"])
    if isinstance(p.get("words"), list):
        return " ".join(str(x) for x in p["words"])
    if isinstance(p.get("text"), str) and p.get("text"):
        return str(p["text"])
    return ""


def _ticker_segments(ctx: Ctx, scene: Scene) -> list[tuple[int, str]]:
    """(scene index, words) for every scene of the film with words, de-duplicated in order.
    A scene whose words repeat an earlier scene's shares that segment."""
    own = scene.p.get("ticker")
    if isinstance(own, str) and own.strip():
        return [(-1, own.upper())]
    film = ctx.film
    key = id(film)
    if key not in _TICKER:
        segs: list[tuple[int, str]] = []
        seen: dict[str, int] = {}
        for j, sc in enumerate(film.scenes if film is not None else [scene]):
            txt = _scene_words(sc).upper().strip()
            if txt and txt not in seen:
                seen[txt] = j
                segs.append((j, txt))
        _TICKER[key] = segs or [(-1, "KINETIC")]
        if len(_TICKER) > 32:
            _TICKER.pop(next(iter(_TICKER)))
    return _TICKER[key]


def _ticker_text(ctx: Ctx, scene: Scene) -> str:
    return "  •  ".join(t for _, t in _ticker_segments(ctx, scene))


def _chrome_on(scene: Scene) -> bool:
    v = scene.p.get("chrome")
    if v is None:
        v = scene.mode.get("chrome", False)
    return bool(v)


def content_floor(ctx: Ctx, scene: Scene) -> float:
    """Lowest y scene content may use: above the chrome labels when chrome is on."""
    if not _chrome_on(scene):
        return float(ctx.H)
    g = L.grid(ctx)
    fk = L.TYPE["kicker"].font(ctx, weight=700)
    fm = L.TYPE["meta"].font(ctx, weight=600)
    rule_y = g.safe_bottom - D.cap_height(fk) - g.bl(3)
    return rule_y - g.bl(2) - D.cap_height(fm) - g.bl(3)


def _chrome_rows(ctx: Ctx) -> tuple[float, float, float]:
    """(ticker baseline, hairline y, label baseline) of the chrome block on the bottom safe line."""
    g = L.grid(ctx)
    fk = L.TYPE["kicker"].font(ctx, weight=700)
    base = g.safe_bottom
    rule_y = base - D.cap_height(fk) - g.bl(3)
    return base, rule_y, rule_y - g.bl(2)


def draw_chrome(ctx: Ctx, scene: Scene, ground: str, plate: bool = False) -> None:
    """Chrome block on the bottom safe line, in three levels of emphasis.

    Label row (meta): scene number in the accent, the count and scene type quiet on the left;
    beat dot, tempo and a running beat counter on the right. Hairline: the scene's progress,
    filled in the accent one beat step at a time (out_expo over 1/4 beat). Ticker (kicker): the
    film's words scrolling on the beat clock; the current scene's words are at full ink, the
    rest recede, bullets in the accent. `plate` lays a ground-coloured band behind the block
    for full-bleed scenes (marquee). Kept off the top line, which belongs to the handle."""
    if not _chrome_on(scene):
        return
    g = L.grid(ctx)
    c = ctx.c
    ink = _ink_on(ctx, ground)
    acc = _accent_on(ctx, ground)
    meta = L.TYPE["meta"]
    fm = meta.font(ctx, weight=600)
    fmb = meta.font(ctx, weight=800)
    tk = meta.tk(ctx)
    pulse = _beat_pulse(ctx, 0.5)
    kick = L.TYPE["kicker"]
    fk = kick.font(ctx, weight=700)
    ktk = kick.tk(ctx)
    cap_k, cap_m = D.cap_height(fk), D.cap_height(fm)
    base, rule_y, lab_y = _chrome_rows(ctx)
    if plate:
        top = lab_y - cap_m - g.bl(4)
        c.drawRect(skia.Rect(0, top, ctx.W, base + g.bl(4)), D.paint(ctx.pal(ground, 0.9)))
    # hairline + beat-stepped progress of the scene
    hair = max(1.0, g.px(2))
    c.drawRect(skia.Rect(g.x0, rule_y, g.x1, rule_y + hair), D.paint(ctx.pal(ink, 0.22)))
    nb = max(1, int(round(ctx.dur / max(ctx.beat_len, 1e-6))))
    lb = max(0.0, ctx.local_beat)
    step = min(float(nb), math.floor(lb + 1e-6) + E.out_expo(min(1.0, (lb % 1.0) / 0.25)))
    c.drawRect(skia.Rect(g.x0, rule_y - hair, g.x0 + g.content_w * step / nb, rule_y + 2 * hair),
               D.paint(ctx.pal(acc, 0.9)))
    film = ctx.film
    idx, n = 1, 1
    if film is not None and scene in film.scenes:
        idx, n = film.scenes.index(scene) + 1, len(film.scenes)
    gb = int(math.floor(ctx.tl.time_to_beat(ctx.start + ctx.lt) + 1e-6)) + 1
    x = g.x0
    x += D.draw_text(c, f"{idx:02d}", x, lab_y, fmb, ctx.pal(acc), align="left", tracking=tk)
    x += D.draw_text(c, f"/{n:02d}", x, lab_y, fm, ctx.pal(ink, 0.42), align="left", tracking=tk)
    D.draw_text(c, scene.type.upper(), x + g.px(28), lab_y, fm, ctx.pal(ink, 0.72), align="left", tracking=tk)
    w = D.draw_text(c, f"{gb:03d}", g.x1, lab_y, fmb, ctx.pal(ink, 0.6 + 0.4 * pulse), align="right", tracking=tk)
    w += D.draw_text(c, f"{ctx.tl.bpm:.0f} BPM  BEAT ", g.x1 - w, lab_y, fm, ctx.pal(ink, 0.42),
                     align="right", tracking=tk)
    r = g.px(7) * (1 + 0.35 * pulse)  # beat dot, flashing on the beat
    c.drawCircle(g.x1 - w - g.px(20), lab_y - cap_m / 2, r, D.paint(ctx.pal(acc, 0.35 + 0.65 * pulse)))
    # ticker: one 16th-note step of travel per 16th (continuous across cuts: film beat clock)
    beat_abs = ctx.tl.time_to_beat(ctx.start + ctx.lt)
    off = -beat_abs * g.px(64)
    segs = _ticker_segments(ctx, scene)
    cur = _scene_words(scene).upper().strip()
    sep = "  •  "
    sep_w = D.text_w(fk, sep, tracking=ktk)
    dot_w = D.text_w(fk, "•", tracking=ktk)
    widths = [D.text_w(fk, t, tracking=ktk) for _, t in segs]
    uw = max(sum(widths) + sep_w * len(segs), 1.0)
    c.save()
    c.clipRect(skia.Rect(g.x0, base - cap_k - g.bl(1), g.x1, base + g.bl(2)))
    x = g.x0 - uw + (off % uw)
    while x < g.x1:
        for (_, t), tw in zip(segs, widths):
            if x + tw > g.x0 and x < g.x1:
                D.draw_text(c, t, x, base, fk, ctx.pal(ink, 0.95 if t == cur else 0.55), align="left", tracking=ktk)
            x += tw
            D.draw_text(c, "•", x + (sep_w - dot_w) / 2, base, fk, ctx.pal(acc, 0.85), align="left")
            x += sep_w
    c.restore()


# ---------------------------------------------------------------------- slam

@register("slam")
class Slam(Scene):
    """One word per beat, punched in at full width; grounds can flip per word.

    Each word lands in `land` ("1/3b"): scale 1.45 -> 1, a small roll, and the variable axes
    wdth 70 -> `width`, wght 600 -> `weight` (out_expo). `rows` outline echo rows cascade below
    the word (and `rows_above` above it), each 1/8 beat later, sliding sideways on the beat grid
    so the frame stays full and alive between hits. `echo` adds the scale-out ghost on the hit
    and `bar` an accent rule that snaps out under each word on its hit."""
    defaults = {"per": "1b", "flip": True, "grounds": ["bg", "accent", "paper"], "style": "fill",
                "accent_words": [], "weight": 900, "width": 112, "echo": True, "sfx": "hit", "first_sfx": "impact",
                "land": LAND, "rows": 4, "rows_above": 2, "row_stagger": "1/8b", "bar": True}
    time_params = ("land", "row_stagger")

    def _per(self) -> float:
        return self.tl.resolve_dur(self.p["per"], self.start)

    def _index(self, lt: float) -> int:
        n = len(_words(self.p))
        return max(0, min(n - 1, int((lt + 1e-6) / max(self._per(), 1e-3))))

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

    def _color(self, ctx: Ctx, word: str, ground: str) -> str:
        acc = self.p["accent_words"]
        if word in acc or word.strip(".,!?") in acc:
            return ctx.theme["accent"] if ground != "accent" else ctx.theme["ink_dark"]
        return ctx.pal.on(ground)

    def draw(self, ctx: Ctx) -> None:
        words = _words(self.p)
        if not words:
            return
        per = self._per()
        land = float(self.p["land"])
        i = self._index(ctx.lt)
        u = ctx.lt - i * per
        word = words[i]
        ground = self.ground_at(ctx.lt)
        color = self._color(ctx, word, ground)
        weight, width = float(self.p["weight"]), float(self.p["width"])
        c = ctx.c
        cy = ctx.cy

        def sized(wd: str) -> float:
            return ctx.fit("display", wd, _hero_w(ctx), _max_type(ctx), int(weight), width).getSize()

        size = sized(word)
        cap = D.cap_height(_axfont(ctx, "display", size, weight, width))
        pitch = cap * 1.24
        stag = float(self.p["row_stagger"])
        outline = self.p["style"] == "outline" or (self.p["style"] == "mix" and i % 2 == 1)
        sw = max(1.5, 2.6 * ctx.s)
        # echo rows: outline copies cascading away from the hero; a row keeps the previous word
        # until the cascade reaches it (a ripple on every hit)
        rows = [(-k, a) for k, a in zip(range(1, int(self.p["rows_above"]) + 1), (0.30, 0.16, 0.09))]
        rows += [(k, a) for k, a in zip(range(1, int(self.p["rows"]) + 1), (0.52, 0.34, 0.22, 0.14, 0.09, 0.06))]
        beat = ctx.local_beat
        c.save()
        c.clipRect(skia.Rect(0, L.grid(ctx).safe_top, ctx.W, content_floor(ctx, self)))
        for k, alpha in rows:
            d = abs(k)
            uk = u - d * stag
            wi, uu = i, uk
            if uk < 0:
                if i == 0:
                    continue
                wi, uu = i - 1, uk + per
            wd = words[wi]
            e, wg, wdh = _land_axes(uu, land, weight, width)
            f = _axfont(ctx, "display", sized(wd), wg, wdh)
            y = cy + k * pitch * (0.55 + 0.45 * e)
            side = 1 if (k % 2) else -1
            dx = side * g_px(ctx, 26) * d * (beat + 0.6 * e)
            col = ctx.pal(self._color(ctx, wd, ground), alpha * (0.35 + 0.65 * e))
            D.draw_text(c, wd, ctx.cx + dx, y, f, col, stroke=sw, valign="middle")
        c.restore()
        e, wg, wdh = _land_axes(u, land, weight, width)
        f = _axfont(ctx, "display", size, wg, wdh)
        sc = (1 + 0.45 * (1 - e)) * (1 + 0.03 * (u / max(per, 1e-3)))
        c.save()
        c.translate(ctx.cx, cy)
        c.rotate((1 - e) * (-6 if i % 2 else 6))
        if self.p["echo"]:
            ue = u / max(ctx.beats(0.5), 1e-3)
            if ue < 1:
                for gs, ga in ((1.16, 0.32), (1.36, 0.16)):
                    k_ = gs + 0.5 * E.out_quint(ue)
                    c.save(); c.scale(k_, k_)
                    D.draw_text(c, word, 0, 0, f, ctx.pal(color, ga * (1 - E.out_quint(ue))), stroke=sw, valign="middle")
                    c.restore()
        c.scale(sc, sc)
        D.draw_text(c, word, 0, 0, f, ctx.pal(color),  # hard cut in: full on the hit frame
                    stroke=(5 * ctx.s if outline else 0), valign="middle")
        c.restore()
        if self.p["bar"]:
            self._impact_bar(ctx, word, f, u, per, cap * sc, ground)
        draw_chrome(ctx, self, ground)

    def _impact_bar(self, ctx: Ctx, word: str, f: skia.Font, u: float, per: float, cap: float,
                    ground: str) -> None:
        """Accent rule under the hero: snaps out from the centre to the word's width on the hit
        (out_expo over 1/4 beat) and draws back to a point in the last 1/4 beat (in_quart), so
        every landing is underlined and the next word lands on a clean frame."""
        g = L.grid(ctx)
        q = max(ctx.beats(0.25), 1e-3)
        grow = E.out_expo(E.clamp(u / q))
        back = E.in_quart(E.clamp((u - (per - q)) / q))
        half = D.text_w(f, word) / 2 * max(0.0, grow - back)
        if half < 0.5:
            return
        y = ctx.cy + cap / 2 + g.bl(3)
        col = ctx.theme["ink_dark"] if ground == "accent" else ctx.theme["accent"]
        ctx.c.drawRect(skia.Rect(ctx.cx - half, y, ctx.cx + half, y + g.bl(1.5)), D.paint(ctx.pal(col)))


def g_px(ctx: Ctx, ref: float) -> float:
    return ref * ctx.s


# ---------------------------------------------------------------------- stack

@register("stack")
class Stack(Scene):
    """Lines that each fill the width, arriving one per `per` (default 1 beat).

    The whole phrase is set from the first frame as faint outline rows that scroll on the beat
    grid (`ghost`), so the frame is full before the words land. Each line then slides in
    (out_expo over `land`, default 1/2 beat) while its axes stretch wdth 62 -> 118 and
    wght 600 -> `weight`; landed lines pump their width on every following beat (`pump`).
    `gap` is the line gap as a fraction of half the frame height (kept for old specs)."""
    defaults = {"lines": ["MAKE", "IT", "MOVE"], "per": "1b", "accent_last": True, "weight": 900,
                "outline": [], "align": "center", "sfx": "whoosh", "gap": 0.06, "land": "1/2b",
                "ghost": True, "pump": True, "width": 118}
    time_params = ("land",)

    def cues(self) -> list[Cue]:
        per = self.tl.resolve_dur(self.p["per"], self.start)
        out = []
        n = len(self.p["lines"])
        for i in range(n):
            out.append(Cue("sfx", i * per, data={"sound": self.p["sfx"] if i < n - 1 else "hit"}))
            out.append(Cue("hit", i * per, data={"strength": 0.35 if i < n - 1 else 0.6}))
        return out

    def draw(self, ctx: Ctx) -> None:
        lines = [str(x) for x in self.p["lines"]]
        if not lines:
            return
        per = self.tl.resolve_dur(self.p["per"], self.start)
        land = float(self.p["land"])
        weight, width = float(self.p["weight"]), float(self.p["width"])
        sizes = [ctx.fit("display", ln, _hero_w(ctx), _max_type(ctx) * 0.85, int(weight), width).getSize()
                 for ln in lines]
        caps = [D.cap_height(_axfont(ctx, "display", s_, weight, width)) for s_ in sizes]
        gap_raw = self.p_raw.get("gap", 0.06)
        gap = (float(gap_raw) if isinstance(gap_raw, (int, float)) else 0.06) * ctx.H * 0.5
        total = sum(caps) + gap * (len(lines) - 1)
        k = min(1.0, ctx.H * 0.66 / max(total, 1.0))
        y = ctx.cy - total * k / 2
        ink = ctx.pal.on(self.ground)
        c = ctx.c
        beat = ctx.local_beat
        for i, ln in enumerate(lines):
            h = caps[i] * k
            base = y + h
            u = ctx.lt - i * per
            side = -1 if i % 2 else 1
            last = i == len(lines) - 1
            col = ctx.theme["accent"] if (last and self.p["accent_last"]) else ink
            if self.p["ghost"]:
                gf = _axfont(ctx, "display", sizes[i] * k, weight, width)
                ga = 0.2 if u < 0 else 0.09
                _repeat_row(c, ln, base, gf, ctx.pal(col if u >= 0 else ink, ga),
                            -side * beat * g_px(ctx, 60), ctx.W, stroke=max(1.2, 2.2 * ctx.s), sep="   ")
            if u >= 0:
                e = E.out_expo(u / max(land, 1e-4))
                _, wg, wdh = _land_axes(u, ctx.beats(1 / 3), weight, width, w0=62.0)
                if self.p["pump"] and u > per * 0.75:
                    wdh -= 9.0 * _beat_pulse(ctx, 0.5)
                f = _axfont(ctx, "display", sizes[i] * k, wg, wdh)
                x = ctx.cx + side * (1 - e) * ctx.W * 0.9
                outl = ln in self.p["outline"]
                D.draw_text(c, ln, x, base, f, ctx.pal(col), stroke=(4 * ctx.s if outl else 0))
            y += h + gap * k
        draw_chrome(ctx, self, self.ground)


# ---------------------------------------------------------------------- type3d

@register("type3d")
class Type3D(Scene):
    """Extruded 3D type: swings in flat, then extrudes on the `impact` beat.

    Enter: the face swings in from `swing` degrees (out_expo over the first beat). Impact
    (default "1b", on the beat): the extrusion punches out to `extrude` x cap height (default
    0.5, >= 8 %), with a shaded side in the accent at about 55 % luma (`side`, `side_luma`),
    an impact SFX, a camera hit and a music hit; `rows` outline echoes then cascade down from
    the word (1/8 beat apart) to fill the lower half. After the impact the word sways once per
    bar so the extrusion keeps reading. Sized so the word spans >= 85 % of the frame width.
    `depth` = number of extrusion slices (old specs: kept as is)."""
    defaults = {"text": "3D", "depth": 18, "weight": 900, "width": 110, "swing": 75, "sub": "", "color": "ink",
                "side": "accent", "side_luma": 0.55, "extrude": 0.5, "impact": "1b", "rows": 4,
                "row_stagger": "1/8b", "sfx": "whoosh_big", "impact_sfx": "impact", "y": 0.42}
    time_params = ("impact", "row_stagger")

    def cues(self) -> list[Cue]:
        imp = float(self.p["impact"])
        return [Cue("sfx", 0.0, data={"sound": self.p["sfx"]}),
                Cue("sfx", imp, data={"sound": self.p["impact_sfx"]}),
                Cue("hit", imp, data={"strength": 0.8}),
                Cue("cue", imp, data={"music": "hit"})]

    def shutter(self, lt: float) -> float:
        imp = float(self.p["impact"])
        return 2.0 if lt < imp + 0.12 else 1.0

    FILL = 0.90       # projected face width at the settled pose, x frame width (hero words)
    RX, RY, SWAY = -14.0, -16.0, 6.0  # settled tilt / yaw and the once-per-bar sway (degrees)

    def _angles(self, ctx: Ctx) -> tuple[float, float, float, float, float]:
        """(rx, ry, rz, scale, extrude 0..1) before levelling."""
        lt = ctx.lt
        imp = float(self.p["impact"])
        e_in = E.out_expo(lt / max(imp, 1e-3))
        ex = E.out_expo((lt - imp) / max(ctx.beats(1 / 3), 1e-3)) if lt >= imp else 0.0
        bar = max(ctx.local_beat - self.tl.beats_of(imp, self.start), 0.0) / 4.0
        sway = math.sin(2 * math.pi * bar) * (1 - math.exp(-6 * bar))
        ry = math.radians(self.p["swing"] * (1 - e_in) + self.RY * ex - self.SWAY * sway)
        rx = math.radians(self.RX * ex + 4 * math.sin(2 * math.pi * bar * 0.5))
        rz = math.radians(-4 * (1 - e_in))
        # impact squash: a 3 % kick on the hit, out_expo back over half a beat
        kick = 0.03 * (1 - E.out_expo((lt - imp) / max(ctx.beats(0.5), 1e-3))) if lt >= imp else 0.0
        scale = (0.82 + 0.18 * e_in) * (1 + kick)
        return rx, ry, rz, scale, ex

    @staticmethod
    def _level(ctx: Ctx, half_w: float, rx: float, ry: float, rz: float, scale: float) -> float:
        """Roll that keeps the projected cap midline level (yaw + tilt in perspective otherwise
        slant the word downhill); two fixed-point steps are within 0.05 degree."""
        cx, cy = ctx.cx, ctx.cy
        z = rz
        for _ in range(2):
            m = D.persp3d(cx, cy, rx, ry, z, dist=1400 * ctx.s, scale=scale)
            pts = []
            for x in (cx - half_w, cx + half_w):
                pw = m.rc(3, 0) * x + m.rc(3, 1) * cy + m.rc(3, 3)
                pts.append(((m.rc(0, 0) * x + m.rc(0, 1) * cy + m.rc(0, 3)) / pw,
                            (m.rc(1, 0) * x + m.rc(1, 1) * cy + m.rc(1, 3)) / pw))
            z -= math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0])
        return z

    def _pose(self, ctx: Ctx, f: skia.Font | None = None) -> tuple[float, float, float, float, float]:
        """(rx, ry, rz, scale, extrude 0..1) at the current time; the roll levels the word once
        it has swung in (blended in with the entry)."""
        rx, ry, rz, scale, ex = self._angles(ctx)
        f = f if f is not None else self.font_for(ctx)
        imp = float(self.p["impact"])
        e_in = E.out_expo(ctx.lt / max(imp, 1e-3))
        lev = self._level(ctx, D.text_w(f, str(self.p["text"])) / 2, rx, ry, rz, scale)
        return rx, ry, E.lerp(rz, lev, e_in), scale, ex

    def font_for(self, ctx: Ctx) -> skia.Font:
        """Face sized so its projected width at the settled pose (levelled, no sway) is FILL x W,
        the same fill as the flat hero words."""
        text = str(self.p["text"])
        key = (ctx.W, ctx.H, text, self.p["weight"], self.p["width"])
        cache = self.__dict__.setdefault("_fit_cache", {})
        if key not in cache:
            f0 = ctx.fit("display", text, _hero_w(ctx), _max_type(ctx) * 1.25, self.p["weight"], self.p["width"])
            rx, ry = math.radians(self.RX), math.radians(self.RY)
            hw = D.text_w(f0, text) / 2
            rz = self._level(ctx, hw, rx, ry, 0.0, 1.0)
            lo, hi = self._bounds(ctx, f0, (rx, ry, rz, 1.0), ctx.cx, ctx.cy)
            size = min(f0.getSize() * self.FILL * ctx.W / max(hi - lo, 1.0), _max_type(ctx) * 1.25)
            cache[key] = size
        return ctx.font("display", cache[key], int(self.p["weight"]), float(self.p["width"]))

    def draw(self, ctx: Ctx) -> None:
        text = str(self.p["text"])
        f = self.font_for(ctx)
        rx, ry, rz, scale, ex = self._pose(ctx, f)
        cap = D.cap_height(f)
        cx, cy = ctx.cx, ctx.H * float(self.p["y"])
        lo, hi = self._bounds(ctx, f, (rx, ry, rz, scale), cx, cy)
        c0 = ctx.c
        c0.save()
        c0.translate(ctx.cx - (lo + hi) / 2, 0)  # keep the projected face centred (perspective skews it)
        slices = max(2, int(self.p["depth"]))
        side_rgb = ctx.pal.rgb(self.p["side"])
        front_side = _shade(side_rgb, float(self.p["side_luma"]))
        c = ctx.c
        dist = 1400 * ctx.s
        # echo rows: flat outlines under the word, cascading out after the impact
        imp = float(self.p["impact"])
        pitch = cap * 1.3
        stag = float(self.p["row_stagger"])
        c.save()
        c.clipRect(skia.Rect(-ctx.W, 0, 2 * ctx.W, content_floor(ctx, self)))
        for k, a in zip(range(1, int(self.p["rows"]) + 1), (0.55, 0.36, 0.22, 0.13, 0.08)):
            uk = ctx.lt - imp - (k - 1) * stag
            if uk < 0:
                continue
            e = E.out_expo(uk / max(ctx.beats(0.5), 1e-3))
            c.save()
            c.concat(D.persp3d(cx, cy, rx, ry, rz, dist=dist, scale=scale, oz=0.0))
            D.draw_text(c, text, cx, cy + cap / 2 + k * pitch * e, f, ctx.pal(self.p["side"], a * e),
                        stroke=max(1.5, 2.6 * ctx.s))
            c.restore()
        c.restore()
        # extrusion: slices stepped along a screen-space depth vector that follows the pose (so
        # the side always reads), face projected in perspective on top
        depth_px = float(self.p["extrude"]) * cap * scale * ex
        vx, vy = 0.35 - 1.6 * math.sin(ry), 0.75 - 1.4 * math.sin(rx)
        vn = max(math.hypot(vx, vy), 1e-6)
        vx, vy = vx / vn, vy / vn
        slices = max(slices, int(depth_px / max(1.5, 2.0 * ctx.s))) if ex > 0.01 else 0
        for k in range(slices, -1, -1):
            c.save()
            d = depth_px * k / max(slices, 1)
            c.translate(vx * d, vy * d)
            c.concat(D.persp3d(cx, cy, rx, ry, rz, dist=dist, scale=scale))
            if k == 0:
                col = ctx.pal(self.p["color"])
            else:
                # front of the side at side_luma x accent, darkening toward the back
                col = D.argb(_shade(front_side, 1.0 - 0.45 * k / slices))
            D.draw_text(c, text, cx, cy + cap / 2, f, col)
            c.restore()
        if self.p["sub"]:
            r = L.TYPE["kicker"]
            sf = r.font(ctx, weight=700)
            a = E.clamp((ctx.lt - imp) / max(ctx.beats(0.5), 1e-3))
            D.draw_text(c, str(self.p["sub"]).upper(), cx, cy - cap / 2 - L.grid(ctx).bl(6), sf,
                        ctx.pal("muted", a), tracking=0.25)
        c0.restore()
        draw_chrome(ctx, self, self.ground)

    def projected_width(self, ctx: Ctx) -> float:
        """Screen width of the word's face at the current pose (for tests / layout checks)."""
        f = self.font_for(ctx)
        rx, ry, rz, scale, _ = self._pose(ctx, f)
        lo, hi = self._bounds(ctx, f, (rx, ry, rz, scale), ctx.cx, ctx.H * float(self.p["y"]))
        return hi - lo

    def _bounds(self, ctx: Ctx, f: skia.Font, pose: tuple, cx: float, cy: float) -> tuple[float, float]:
        """Projected x extent of the word's face."""
        rx, ry, rz, scale = pose
        w = D.text_w(f, str(self.p["text"]))
        cap = D.cap_height(f)
        m = D.persp3d(cx, cy, rx, ry, rz, dist=1400 * ctx.s, scale=scale)
        xs = []
        for x, y in ((cx - w / 2, cy - cap / 2), (cx + w / 2, cy - cap / 2), (cx - w / 2, cy + cap / 2),
                     (cx + w / 2, cy + cap / 2)):
            px = m.rc(0, 0) * x + m.rc(0, 1) * y + m.rc(0, 3)
            pw = m.rc(3, 0) * x + m.rc(3, 1) * y + m.rc(3, 3)
            xs.append(px / pw)
        return min(xs), max(xs)


# ---------------------------------------------------------------------- scramble

GLYPHS = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#$%&*+=<>/\\"


@register("scramble")
class Scramble(Scene):
    """Glyph scramble that resolves letter by letter (code / reveal feel).

    Glyphs re-roll on 32nd notes. Letters lock from `first` (default "1b") every `stagger`
    (default "1/8b"; seconds still work), each with a 1/4-beat pop; key clicks fall on the half
    beats of the resolve and the hit (SFX, camera, music) on the next whole beat after the last
    letter. The word is set across >= 85 % of the width. Below it a block of `rows` mono lines
    keeps scrambling and resolves one line per beat to `lines` (optional list of strings) to
    fill the lower half; `label` is the kicker above the word."""
    defaults = {"text": "DECRYPTED", "label": "", "stagger": "1/8b", "first": "1b", "role": "mono", "weight": 800,
                "caret": True, "lines": [], "rows": 5}

    def _locks(self) -> list[float]:
        text = str(self.p["text"])
        first, st = float(self.p["first"]), float(self.p["stagger"])
        return [first + i * st for i in range(len(text))]

    def _hit_at(self) -> float:
        locks = self._locks()
        last = locks[-1] if locks else float(self.p["first"])
        b = self.tl.beats_of(last, self.start)
        return self.bt(math.floor(b + 1e-6) + 1.0)

    def cues(self) -> list[Cue]:
        out = [Cue("sfx", 0.0, data={"sound": "glitch"})]
        seen = set()
        for t in self._locks():
            b = self.tl.beats_of(t, self.start)
            hb = round(b * 2) / 2
            if abs(b - hb) * self.beat_len < 0.012 and hb not in seen:
                seen.add(hb)
                out.append(Cue("sfx", self.bt(hb), data={"sound": "key", "gain": -4}))
        hit = self._hit_at()
        out.append(Cue("sfx", hit, data={"sound": "hit"}))
        out.append(Cue("hit", hit, data={"strength": 0.5}))
        out.append(Cue("cue", hit, data={"music": "hit"}))
        return out

    def font_for(self, ctx: Ctx) -> skia.Font:
        """Word font: the word alone spans ~86 % of the frame width (caret beside it)."""
        return ctx.fit(self.p["role"], str(self.p["text"]), _hero_w(ctx) * 0.96, ctx.H * 0.16 / 0.73,
                       self.p["weight"])

    def draw(self, ctx: Ctx) -> None:
        text = str(self.p["text"])
        g = L.grid(ctx)
        f = self.font_for(ctx)
        lay = D.glyph_layout(f, text)
        total = lay[-1][1] + lay[-1][2] if lay else 0
        cap = D.cap_height(f)
        caret_w = (g.px(10) + cap * 0.09) if self.p["caret"] else 0.0
        x0 = ctx.cx - (total + caret_w) / 2  # word + caret centred as one block
        cy = ctx.H * 0.40
        ink = ctx.pal.on(self.ground)
        bucket = int(math.floor(ctx.local_beat * 8))
        rnd = random.Random(bucket * 7919 + sum(map(ord, text)))
        locks = self._locks()
        pop_d = max(ctx.beats(0.25), 1e-3)
        c = ctx.c
        for i, (ch, ox, w) in enumerate(lay):
            tr = locks[i]
            if ctx.lt >= tr:
                g_, col = ch, ink
                pop = 1 + 0.25 * (1 - E.out_expo((ctx.lt - tr) / pop_d))
            else:
                g_ = rnd.choice(GLYPHS) if ch != " " else " "
                col, pop = ctx.theme["accent"], 1.0
            c.save()
            c.translate(x0 + ox + w / 2, cy)
            c.scale(pop, pop)
            D.draw_text(c, g_, 0, cap / 2, f, ctx.pal(col))
            c.restore()
        done_t = locks[-1] if locks else 0.0
        if self.p["caret"]:
            if ctx.lt < done_t or int(ctx.local_beat * 2) % 2 == 0:
                cx = x0 + total + g.px(10)
                c.drawRect(skia.Rect.MakeXYWH(cx, cy - cap / 2, cap * 0.09, cap), D.paint(ctx.pal("accent")))
        if self.p["label"]:
            r = L.TYPE["kicker"]
            lf = r.font(ctx, weight=700)
            D.draw_text(c, str(self.p["label"]).upper(), ctx.cx, cy - cap / 2 - g.bl(7), lf, ctx.pal("muted"),
                        tracking=0.3)
        self._matrix(ctx, g, cy + cap / 2 + g.bl(12), ink)
        draw_chrome(ctx, self, self.ground)

    def _matrix(self, ctx: Ctx, g: L.Grid, top: float, ink: str) -> None:
        """Mono block under the word: scrambling rows that resolve one per beat to `lines`."""
        n = int(self.p["rows"])
        if n <= 0:
            return
        lines = [str(x).upper() for x in (self.p["lines"] or [])]
        r = L.TYPE["subhead"]
        cols = 22
        f = ctx.fit("mono", "M" * cols, g.content_w, r.px(ctx) * 1.2, 600)
        pitch = D.cap_height(f) * 1.9
        bottom = g.safe_bottom - g.bl(16)
        n = max(1, min(n, int((bottom - top) / pitch)))
        rnd = random.Random(int(math.floor(ctx.local_beat * 8)) * 131 + 7)
        first_beat = self.tl.beats_of(float(self.p["first"]), self.start)
        c = ctx.c
        for j in range(n):
            y = top + D.cap_height(f) + j * pitch
            target = lines[j] if j < len(lines) else ""
            lock_b = first_beat + j  # one line per beat from the first lock
            u = ctx.local_beat - lock_b
            appear = E.out_expo(E.clamp((ctx.local_beat + 0.25 - j * 0.125) / 0.5))
            if appear <= 0.01:
                continue
            if target and u >= 0:
                s = target[:cols]
                col = ctx.pal(ink, 0.9 if j == min(len(lines), n) - 1 else 0.7)
            else:
                m = cols if not target else max(len(target), 8)
                s = "".join(rnd.choice(GLYPHS) for _ in range(m))
                if target and u > -1:  # resolving left to right over the beat before the lock
                    k = int(len(target) * E.clamp(u + 1))
                    s = target[:k] + s[k:len(target)]
                col = ctx.pal("accent" if j % 2 == 0 else ink, 0.32 * appear)
            D.draw_text(c, s, g.x0, y, f, col, align="left")


# ---------------------------------------------------------------------- marquee

@register("marquee")
class Marquee(Scene):
    """Tilted rows of repeating type scrolling in opposite directions, centre row hero.

    `speed` is px/s at 1080 (old specs) or, when `speed_beats` is set, px per beat; the hero
    row pumps on every beat (scale + wdth axis)."""
    defaults = {"text": "MOTION", "rows": 7, "tilt": -8, "speed": 420, "hero": True, "weight": 900,
                "speed_beats": None}

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.0, data={"sound": "whoosh"})]

    def draw(self, ctx: Ctx) -> None:
        text = str(self.p["text"]).upper()
        rows = int(self.p["rows"])
        rh = ctx.H * 1.25 / rows
        weight = int(self.p["weight"])
        f = ctx.font("display", rh * 0.92, weight, 112)
        unit = text + "  •  "
        uw = D.text_w(f, unit)
        c = ctx.c
        ink = ctx.pal.on(self.ground)
        pulse = _beat_pulse(ctx, 0.5)
        if self.p.get("speed_beats"):
            travel = ctx.local_beat * float(self.p["speed_beats"]) * ctx.s
        else:
            travel = ctx.lt * float(self.p["speed"]) * ctx.s
        c.save()
        c.translate(ctx.cx, ctx.cy)
        c.rotate(self.p["tilt"])
        mid = rows // 2
        for r in range(rows):
            y = (r - mid) * rh + D.cap_height(f) / 2
            direction = 1 if r % 2 else -1
            off = (travel * direction * (1.0 + 0.15 * abs(r - mid))) % uw
            x = -ctx.W * 1.2 - off
            hero = self.p["hero"] and r == mid
            col = ctx.pal("accent") if hero else ctx.pal(ink, 0.9 if r % 2 == 0 else 0.5)
            stroke = 0 if (hero or r % 2 == 0) else 3 * ctx.s
            fr = _axfont(ctx, "display", rh * 0.92, weight, 112 - 14 * pulse) if hero else f
            uwr = D.text_w(fr, unit) if hero else uw
            if hero:
                x = -ctx.W * 1.2 - (travel * direction) % uwr
                c.save(); c.translate(0, y - D.cap_height(f) / 2); c.scale(1 + 0.05 * pulse, 1 + 0.05 * pulse)
                c.translate(0, -(y - D.cap_height(f) / 2))
            while x < ctx.W * 1.2:
                D.draw_text(c, unit, x, y, fr, col, align="left", stroke=stroke)
                x += uwr
            if hero:
                c.restore()
        c.restore()
        draw_chrome(ctx, self, self.ground, plate=True)


# ---------------------------------------------------------------------- burst

@register("burst")
class Burst(Scene):
    """The drop: a word zooms from deep space with speed lines, rings and a flash.

    The word lands in `land` (1/3 beat) with the axes stretching (wdth 70 -> `width`,
    wght 600 -> `weight`), then pumps on every beat with a new shock ring (beat-locked).
    `rows` outline copies stack above and below the word, cascading out 1/8 beat apart after
    the landing and sliding sideways on the beat grid, flashing brighter on every beat. Behind
    it a sunburst of `rays` accent wedges opens on the drop, steps on every beat and turns
    `spin` degrees per beat."""
    defaults = {"text": "NOW", "weight": 900, "width": 125, "lines": 70, "color": "accent", "land": LAND,
                "rows": 3, "row_stagger": "1/8b", "rays": 18, "spin": 24.0}
    time_params = ("land", "row_stagger")

    def cues(self) -> list[Cue]:
        return [Cue("sfx", 0.0, data={"sound": "impact_big"}), Cue("hit", 0.0, data={"strength": 1.6, "flash": 0.9})]

    def shutter(self, lt: float) -> float:
        return 2.5 if lt < 0.25 else 1.0

    def _rays(self, ctx: Ctx, R: float, pulse: float) -> None:
        """Sunburst behind the word: `rays` wedges in the accent, fading in from the centre,
        thrown open on the drop (out_expo over 1/2 beat) and stepping 1/(2 x rays) of a turn on
        every beat (out_expo over 0.4 beat) on top of a steady `spin` (degrees per beat), so the
        ground itself counts the bar and never holds still."""
        n = int(self.p["rays"])
        if n <= 0:
            return
        c = ctx.c
        lb = max(0.0, ctx.local_beat)
        open_ = E.out_expo(E.clamp(lb / 0.5))
        step = math.floor(lb + 1e-6) + E.out_expo((lb % 1.0) / 0.4)
        rot = 7.0 + step * 180.0 / n + lb * float(self.p["spin"])
        half = 180.0 / n * 0.5 * open_
        col = ctx.pal.rgb(self.p["color"])
        a_out = int(255 * (0.13 + 0.05 * pulse))
        shader = skia.GradientShader.MakeRadial(
            skia.Point(ctx.cx, ctx.cy), R * 0.62,
            [skia.Color(*col, 0), skia.Color(*col, int(a_out * 0.45)), skia.Color(*col, a_out)],
            [0.0, 0.35, 1.0])
        paint = skia.Paint(AntiAlias=True, Shader=shader)
        path = skia.Path()
        for k in range(n):
            a0 = math.radians(rot + k * 360.0 / n - half)
            a1 = math.radians(rot + k * 360.0 / n + half)
            path.moveTo(ctx.cx, ctx.cy)
            path.lineTo(ctx.cx + math.cos(a0) * R, ctx.cy + math.sin(a0) * R)
            path.lineTo(ctx.cx + math.cos(a1) * R, ctx.cy + math.sin(a1) * R)
            path.close()
        c.drawPath(path, paint)

    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        rnd = random.Random(42)
        R = math.hypot(ctx.W, ctx.H)
        self._rays(ctx, R, _beat_pulse(ctx, 0.5))
        speed = 1 - E.out_quint(lt / max(self.dur, 0.1))
        ink = ctx.pal.on(self.ground)
        flow = 0.35 + 0.65 * speed
        for _i in range(int(self.p["lines"])):
            a = rnd.uniform(0, 2 * math.pi)
            r0 = rnd.uniform(0.15, 1.0) * R * 0.5
            v = rnd.uniform(0.8, 1.6)
            r = (r0 + lt * R * 1.1 * v * flow) % (R * 0.75) + R * 0.08
            ln = R * 0.12 * (0.4 + speed)
            p = D.paint(ctx.pal(ink, 0.22 + 0.35 * speed), stroke=rnd.uniform(1.5, 5) * ctx.s)
            c.drawLine(ctx.cx + math.cos(a) * r, ctx.cy + math.sin(a) * r,
                       ctx.cx + math.cos(a) * (r + ln), ctx.cy + math.sin(a) * (r + ln), p)
        # one shock ring per beat (the first is the drop itself)
        nb = int(math.floor(ctx.local_beat + 1e-6))
        for k in range(max(0, nb - 1), nb + 1):
            ur = (ctx.local_beat - k) / 1.25
            if 0 <= ur < 1:
                ring = E.out_quint(ur)
                col = "accent2" if k == 0 else "accent"
                c.drawCircle(ctx.cx, ctx.cy, R * (0.55 if k == 0 else 0.42) * ring,
                             D.paint(ctx.pal(col, 0.8 * (1 - ring)), stroke=(26 if k == 0 else 12) * ctx.s * (1 - ring) + 2))
        land = float(self.p["land"])
        text = str(self.p["text"])
        size = ctx.fit("display", text, ctx.safe_w, _max_type(ctx) * 1.1, self.p["weight"], self.p["width"]).getSize()
        e, wg, wdh = _land_axes(lt, land, float(self.p["weight"]), float(self.p["width"]))
        f = _axfont(ctx, "display", size, wg, wdh)
        z = 1 + 5 * (1 - E.out_expo(lt / max(ctx.beats(0.5), 1e-3)))
        pulse = _beat_pulse(ctx, 0.5)
        pump = 0.05 * pulse * (ctx.local_beat >= 1)
        k_ = z * (1 + 0.04 * lt) * (1 + pump)
        cap = D.cap_height(f) * k_
        stag = float(self.p["row_stagger"])
        sw = max(1.5, 2.8 * ctx.s)
        c.save()
        c.clipRect(skia.Rect(0, L.grid(ctx).safe_top, ctx.W, content_floor(ctx, self)))
        for k in range(1, int(self.p["rows"]) + 1):
            uk = lt - ctx.beats(0.25) - (k - 1) * stag
            if uk < 0:
                continue
            er = E.out_expo(uk / max(ctx.beats(0.5), 1e-3))
            a = (0.5 / k) * er * (0.6 + 0.4 * pulse)
            for sgn in (-1, 1):
                y = ctx.cy + sgn * k * cap * 1.32 * er
                # rows slide sideways on the beat grid, alternating, like the slam cascade
                dx = (1 if (k + (sgn > 0)) % 2 else -1) * g_px(ctx, 34) * k * ctx.local_beat
                c.save(); c.translate(ctx.cx + dx, y); c.scale(k_, k_)
                D.draw_text(c, text, 0, 0, f, ctx.pal(self.p["color"], a), stroke=sw / k_, valign="middle")
                c.restore()
        c.restore()
        c.save()
        c.translate(ctx.cx, ctx.cy)
        c.scale(k_, k_)
        D.draw_text(c, text, 0, 0, f, ctx.pal(self.p["color"]), valign="middle")
        c.restore()
        draw_chrome(ctx, self, self.ground)


# ---------------------------------------------------------------------- countdown

@register("countdown")
class Countdown(Scene):
    """Big numbers counting down on the beat (3, 2, 1) with a ring sweep.

    Each number lands with the axes (wdth 70 -> 100, wght 600 -> 900, out_expo over 1/3 beat);
    an outer tick ring steps round on every beat and pips under the ring count the beats (the
    current pip fills out_expo over 1/4 beat). Layout on the grid: the tick ring stays inside
    the side margins, and the ring and pips sit above the chrome block (`content_floor`), so
    the pips never touch the chrome labels."""
    defaults = {"from": 3, "per": "1b", "label": ""}

    def cues(self) -> list[Cue]:
        per = self.tl.resolve_dur(self.p["per"], self.start)
        return [Cue("sfx", i * per, data={"sound": "tick" if i < int(self.p["from"]) - 1 else "hit"})
                for i in range(int(self.p["from"]))]

    RING_OUT = 1.29   # outer edge of the long ticks, in ring radii
    PIP_H = 12.0      # reference px

    def layout(self, ctx: Ctx) -> dict[str, float]:
        """Ring centre / radius and the pip row, fitted between the safe top and the chrome."""
        g = L.grid(ctx)
        floor = min(content_floor(ctx, self), g.safe_bottom)
        rad = min(min(ctx.W, ctx.H) * 0.32, (g.content_w / 2 - g.bl(1)) / self.RING_OUT)
        ph = g.px(self.PIP_H)
        pip_gap = g.bl(6)
        cy = min(ctx.cy, floor - g.bl(4) - ph - pip_gap - rad * self.RING_OUT)
        cy = max(cy, g.safe_top + rad * self.RING_OUT + g.bl(2))
        pip_y = cy + rad * self.RING_OUT + pip_gap
        return {"cx": ctx.cx, "cy": cy, "rad": rad, "pip_y": pip_y, "pip_h": ph, "floor": floor}

    def draw(self, ctx: Ctx) -> None:
        per = self.tl.resolve_dur(self.p["per"], self.start)
        n = int(self.p["from"])
        i = min(n - 1, int((ctx.lt + 1e-6) / per))
        ul = ctx.lt - i * per
        u = ul / per
        num = str(n - i)
        g = L.grid(ctx)
        lay = self.layout(ctx)
        rad, cx, cy = lay["rad"], lay["cx"], lay["cy"]
        size = min(_max_type(ctx) * 1.2, rad * 1.8)
        e, wg, wdh = _land_axes(ul, ctx.beats(1 / 3), 900, 100)
        f = _axfont(ctx, "display", size, wg, wdh)
        c = ctx.c
        ink = ctx.pal.on(self.ground)
        c.drawArc(skia.Rect(cx - rad, cy - rad, cx + rad, cy + rad), -90, 360 * (1 - u), False,
                  D.paint(ctx.pal("accent"), stroke=14 * ctx.s))
        # outer tick ring: 48 ticks, steps 1/16 turn per beat (out_expo), long tick every 4th
        step = math.floor(ctx.local_beat + 1e-6) + E.out_expo((ctx.local_beat % 1.0) / 0.4)
        rot = step * 360 / 16
        r1, r2 = rad * 1.16, rad * 1.24
        tp = D.paint(ctx.pal(ink, 0.45), stroke=max(1.0, 3 * ctx.s))
        for k in range(48):
            a = math.radians(rot + k * 7.5 - 90)
            rr = r2 + (rad * (self.RING_OUT - 1.24) if k % 4 == 0 else 0)
            c.drawLine(cx + math.cos(a) * r1, cy + math.sin(a) * r1, cx + math.cos(a) * rr, cy + math.sin(a) * rr, tp)
        sc = 1 + 0.3 * (1 - e)
        c.save(); c.translate(cx, cy); c.scale(sc, sc)
        D.draw_text(c, num, 0, 0, f, ctx.pal(ink), valign="middle")
        c.restore()
        # beat pips under the ring: the current one fills from the left on its beat
        pw, ph, gap = g.px(84), lay["pip_h"], g.px(16)
        x = cx - (n * pw + (n - 1) * gap) / 2
        y = lay["pip_y"]
        fill = E.out_expo(E.clamp(ul / max(ctx.beats(0.25), 1e-3)))
        for k in range(n):
            c.drawRect(skia.Rect.MakeXYWH(x, y, pw, ph), D.paint(ctx.pal(ink, 0.22)))
            if k <= i:
                wk = pw * (fill if k == i else 1.0)
                c.drawRect(skia.Rect.MakeXYWH(x, y, wk, ph), D.paint(ctx.pal("accent")))
            x += pw + gap
        if self.p["label"]:
            r = L.TYPE["kicker"]
            D.draw_text(c, str(self.p["label"]).upper(), cx, cy - rad * self.RING_OUT - g.bl(4),
                        r.font(ctx, weight=700), ctx.pal("muted"), tracking=0.3)
        draw_chrome(ctx, self, self.ground)
