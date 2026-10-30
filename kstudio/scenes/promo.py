"""Product launch scenes: a 3D-tilted device with callouts, full-bleed features, a beat-pulsing
CTA and a brand end card.

Everything is set on the layout grid (kstudio.layout: 12 columns, safe zones, type scale) and
timed in beats: scene timing parameters accept "Nb" and resolve on the scene's own grid.
Motion grammar: entries out_expo / out_quint, exits in_quart / in_expo, moves in_out_quint;
springs only for physical objects (the device and the pill-shaped callouts / button).

Scenes
    device    phone mockup at ~70 % of the frame height, yaw tilt (in_out_quint over `tilt`
              beats), bezel highlight that follows the tilt, soft contact + ambient shadow,
              glass sheen; kicker + headline on the grid; callouts with leader lines pinned
              to points on the screen (they track the 3D tilt) and kept inside the safe area.
    features  one feature per `per` beats (default 2b): a full-bleed colour panel with a large
              icon, the label at the title role (126 px) and a body line; progress segments.
              The v0.1 card grid is still available with "layout": "grid" (or "columns").
    cta       headline + full-width pill button that pulses on every beat (spring landing one
              frame after the kick), a touch that presses it on the downbeat (`tap_at`, default
              the first bar line >= 2 beats in) with a click SFX, optional `done` label morph.
    brand     end card: app icon, wordmark at the display role (224 px, wght 700) revealed by
              a travelling mask bar, tagline, URL and store badges on the bottom safe line.
"""
from __future__ import annotations

import math
import os
from functools import lru_cache
from typing import Any

import numpy as np
import skia

from .. import draw as D
from .. import ease as E
from .. import layout as L
from .base import Ctx, Cue, Scene, register
from .common import reveal_line

T = L.TYPE
OPAQUE = 0xFF000000  # paint colour for shader fills (opaque alpha so the shader is never muted)


def _shade(shader: skia.Shader, blur: float = 0.0) -> skia.Paint:
    return D.paint(OPAQUE, shader=shader, blur=blur)


@lru_cache(maxsize=64)
def _spring(settle: float, bounce: float) -> E.Spring:
    return E.Spring.settling(max(settle, 0.05), bounce)


def _m44_np(m: skia.M44) -> np.ndarray:
    return np.array([[m.rc(r, c) for c in range(4)] for r in range(4)], float)


def _proj(M: np.ndarray, x: float, y: float) -> tuple[float, float]:
    v = M @ np.array([x, y, 0.0, 1.0])
    w = v[3] if abs(v[3]) > 1e-9 else 1e-9
    return float(v[0] / w), float(v[1] / w)


def _on(ctx: Ctx, key: str) -> str:
    """Readable ink key for a colour key or hex."""
    r, g, b = ctx.pal.rgb(key)
    return "ink_dark" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 150 else "ink"


def _mask_line(ctx: Ctx, text: str, x: float, y: float, f: skia.Font, color: int, u_in: float,
               u_out: float = 0.0, align: str = "left", tracking: float = 0.0) -> None:
    """Masked line: rises in (out_expo) and leaves upward (in_quart) through its own mask. y = baseline."""
    if u_in <= 0 or u_out >= 1:
        return
    cap = D.cap_height(f)
    pad = cap * 0.45
    h = cap + pad * 2
    off = (1 - E.out_expo(E.clamp(u_in))) * h - E.in_quart(E.clamp(u_out)) * h
    c = ctx.c
    c.save()
    c.clipRect(skia.Rect(-ctx.W, y - cap - pad, 2 * ctx.W, y + pad * 1.2))
    D.draw_text(c, text, x, y + off, f, color, align=align, tracking=tracking)
    c.restore()


# ---------------------------------------------------------------------- device


def _procedural_ui(ctx: Ctx, rect: skia.Rect, ui: dict, lt: float, scroll: float) -> None:
    """A generic app home screen drawn from a tiny description (used when there is no screenshot).
    Fixed header + hero card, a scrolling list clipped above an opaque tab bar (no overlap)."""
    c = ctx.c
    s = rect.width() / 560.0
    x0, y0, w, h = rect.left(), rect.top(), rect.width(), rect.height()
    bg = ui.get("bg", "#120f2e")
    c.drawRect(rect, D.paint(ctx.pal(bg)))
    c.drawRect(rect, _shade(D.radial_grad(x0 + w * 0.2, y0 + h * 0.05, w * 1.1,
                                          [ctx.pal("accent", 0.28), ctx.pal("accent", 0.0)])))
    disp, body = ctx.theme.get("display", "SpaceGrotesk"), ctx.theme.get("body", "Inter")
    from .. import fonts as F
    ff = lambda fam, px, wt: F.font(fam, px * s, wt)  # noqa: E731
    # status bar
    D.draw_text(c, "9:41", x0 + 44 * s, y0 + 58 * s, ff(body, 24, 650), ctx.pal("ink"), align="left")
    D.rrect(c, x0 + w - 92 * s, y0 + 40 * s, 44 * s, 20 * s, 6 * s, D.paint(ctx.pal("ink", 0.85), stroke=2.2 * s))
    D.rrect(c, x0 + w - 88 * s, y0 + 44 * s, 30 * s, 12 * s, 3 * s, D.paint(ctx.pal("ink", 0.85)))
    for k in range(3):
        bh = (8 + 5 * k) * s
        D.rrect(c, x0 + w - 150 * s + k * 12 * s, y0 + 60 * s - bh, 7 * s, bh, 2 * s, D.paint(ctx.pal("ink", 0.85)))
    D.draw_text(c, ui.get("subtitle", "Good morning"), x0 + 40 * s, y0 + 150 * s, ff(body, 26, 500), ctx.pal("muted"),
                align="left")
    D.draw_text(c, ui.get("title", "Today"), x0 + 38 * s, y0 + 222 * s, ff(disp, 64, 700), ctx.pal("ink"), align="left")
    # hero card with progress ring
    cy = y0 + 258 * s
    ch = 286 * s
    D.rrect(c, x0 + 28 * s, cy, w - 56 * s, ch, 38 * s,
            _shade(D.linear_grad(x0, cy, x0 + w, cy + ch, [ctx.pal("accent"), ctx.pal("accent2")])))
    D.rrect(c, x0 + 28 * s, cy, w - 56 * s, ch * 0.5, 38 * s,
            _shade(D.linear_grad(0, cy, 0, cy + ch * 0.5, [ctx.pal("#ffffff", 0.18), ctx.pal("#ffffff", 0.0)])))
    pr = float(ui.get("progress", 0.82)) * E.out_quint(E.clamp(lt / 1.6))
    rc = skia.Rect.MakeXYWH(x0 + 64 * s, cy + 46 * s, 194 * s, 194 * s)
    c.drawArc(rc, 0, 360, False, D.paint(ctx.pal("#ffffff", 0.25), stroke=22 * s))
    c.drawArc(rc, -90, 360 * pr, False, D.paint(ctx.pal("#ffffff"), stroke=22 * s))
    D.draw_text(c, f"{int(round(pr * 100))}%", rc.centerX(), rc.centerY(), ff(disp, 52, 700), ctx.pal("#ffffff"),
                valign="middle")
    D.draw_text(c, ui.get("card_title", "Daily goal"), x0 + 296 * s, cy + 128 * s, ff(body, 30, 700), ctx.pal("#ffffff"),
                align="left")
    D.draw_text(c, ui.get("card_sub", "on track"), x0 + 296 * s, cy + 170 * s, ff(body, 24, 500),
                ctx.pal("#ffffff", 0.8), align="left")
    # list: clipped between the hero card and the tab bar, scrolls underneath the bar
    tab_h = 132 * s
    tb = rect.bottom() - tab_h
    top = cy + ch + 34 * s
    rows = ui.get("rows", [["Focus session", "25 min"], ["Hydration", "6 / 8"], ["Walk", "4.2 km"],
                           ["Read", "18 pages"], ["Sleep", "7h 40m"], ["Stretch", "done"], ["Journal", "2 notes"]])
    icons = ["clock", "heart", "globe", "book", "star", "check", "spark"]
    c.save()
    c.clipRect(skia.Rect(rect.left(), top - 8 * s, rect.right(), tb))
    ry = top - scroll
    lf, sf = ff(body, 28, 650), ff(body, 24, 500)
    for i, (a, b) in enumerate(rows):
        if ry > tb:
            break
        if ry + 104 * s > top - 8 * s:
            D.rrect(c, x0 + 28 * s, ry, w - 56 * s, 104 * s, 28 * s, D.paint(ctx.pal("#ffffff", 0.07)))
            c.drawCircle(x0 + 86 * s, ry + 52 * s, 30 * s, D.paint(ctx.pal("accent", 0.25)))
            D.icon(c, icons[i % len(icons)], x0 + 86 * s, ry + 52 * s, 30 * s, ctx.pal("accent"))
            D.draw_text(c, a, x0 + 138 * s, ry + 62 * s, lf, ctx.pal("ink"), align="left")
            D.draw_text(c, b, x0 + w - 58 * s, ry + 62 * s, sf, ctx.pal("muted"), align="right")
        ry += 118 * s
    c.restore()
    # tab bar: opaque, hairline on top, home indicator
    c.drawRect(skia.Rect(rect.left(), tb, rect.right(), rect.bottom()), D.paint(ctx.pal(bg)))
    c.drawRect(skia.Rect(rect.left(), tb, rect.right(), rect.bottom()), D.paint(ctx.pal("#ffffff", 0.05)))
    c.drawRect(skia.Rect(rect.left(), tb, rect.right(), tb + 1.5 * s), D.paint(ctx.pal("#ffffff", 0.12)))
    for k, ic in enumerate(["star", "chart", "bell", "user"]):
        D.icon(c, ic, rect.left() + w * (k + 0.5) / 4, tb + 48 * s, 36 * s, ctx.pal("accent" if k == 0 else "muted"))
    D.rrect(c, rect.centerX() - 70 * s, rect.bottom() - 22 * s, 140 * s, 8 * s, 4 * s, D.paint(ctx.pal("ink", 0.7)))


def draw_phone(ctx: Ctx, cx: float, cy: float, pw: float, screen: str, ui: dict, lt: float,
               scroll: float, gloss: float, light: float = 0.0) -> skia.Rect:
    """Phone at (cx, cy) with width pw in the current (possibly 3D) canvas space. `light` (-1..1)
    moves the bezel highlight and the glass sheen with the tilt. Returns the screen rect."""
    c = ctx.c
    ph = pw * 2.08
    r = pw * 0.16
    x, y = cx - pw / 2, cy - ph / 2
    # side buttons (behind the body)
    btn = D.paint(OPAQUE, shader=D.linear_grad(x - pw * 0.02, 0, x, 0, [ctx.pal("#1c1c24"), ctx.pal("#5a5a6a")]))
    for by, bh in ((0.17, 0.045), (0.24, 0.085), (0.34, 0.085)):
        D.rrect(c, x - pw * 0.012, y + ph * by, pw * 0.02, ph * bh, pw * 0.008, btn)
    D.rrect(c, x + pw - pw * 0.008, y + ph * 0.27, pw * 0.02, ph * 0.12, pw * 0.008, btn)
    # titanium frame
    D.rrect(c, x, y, pw, ph, r, _shade(D.linear_grad(x, y, x + pw, y + ph,
                                                     [ctx.pal("#5b5b6c"), ctx.pal("#24242e"), ctx.pal("#14141a"),
                                                      ctx.pal("#3c3c4a")], [0.0, 0.35, 0.7, 1.0])))
    # bezel highlight: a bright rim whose hot spot slides with the light direction
    hx = x + pw * (0.5 + 0.55 * light)
    rim = D.linear_grad(hx - pw * 0.9, y, hx + pw * 0.9, y + ph * 0.6,
                        [ctx.pal("#ffffff", 0.0), ctx.pal("#ffffff", 0.85), ctx.pal("#ffffff", 0.0)], [0.15, 0.5, 0.85])
    p = _shade(rim)
    p.setStyle(skia.Paint.kStroke_Style)
    p.setStrokeWidth(max(1.0, pw * 0.006))
    D.rrect(c, x + pw * 0.004, y + pw * 0.004, pw - pw * 0.008, ph - pw * 0.008, r - pw * 0.004, p)
    # black glass bezel
    b_in = pw * 0.022
    D.rrect(c, x + b_in, y + b_in, pw - 2 * b_in, ph - 2 * b_in, r - b_in, D.paint(ctx.pal("#040406")))
    inset = pw * 0.045
    sr = skia.Rect(x + inset, y + inset, x + pw - inset, y + ph - inset)
    rr = skia.RRect.MakeRectXY(sr, r - inset, r - inset)
    c.save()
    c.clipRRect(rr, skia.ClipOp.kIntersect, True)
    path = screen if (not screen or os.path.isabs(screen)) else os.path.join(ctx.assets, screen)
    drawn = False
    if screen:
        try:
            img = D.load_image(path)
            k = sr.width() / img.width()
            dh = img.height() * k
            off = min(scroll, max(0.0, dh - sr.height()))
            c.drawImageRect(img, skia.Rect.MakeXYWH(sr.left(), sr.top() - off, sr.width(), dh), D.SAMPLING)
            drawn = True
        except (FileNotFoundError, ValueError):
            drawn = False
    if not drawn:
        _procedural_ui(ctx, sr, ui, lt, scroll)
    # glass: a broad soft sheen that follows the light plus the entry sweep
    gx = sr.left() + sr.width() * (0.35 - 0.6 * light)
    sheen = D.linear_grad(gx - pw * 0.6, sr.top(), gx + pw * 0.6, sr.top() + pw * 1.2,
                          [ctx.pal("#ffffff", 0.0), ctx.pal("#ffffff", 0.07), ctx.pal("#ffffff", 0.0)])
    c.drawRect(sr, _shade(sheen))
    if 0.0 < gloss < 1.0:
        sx = sr.left() + (gloss * 2.4 - 0.7) * sr.width()
        sw = D.linear_grad(sx - pw * 0.25, sr.top(), sx + pw * 0.25, sr.top() + pw * 0.5,
                           [ctx.pal("#ffffff", 0.0), ctx.pal("#ffffff", 0.16), ctx.pal("#ffffff", 0.0)])
        c.drawRect(sr, _shade(sw))
    c.restore()
    # dynamic island
    D.rrect(c, cx - pw * 0.15, y + inset + pw * 0.03, pw * 0.3, pw * 0.085, pw * 0.0425, D.paint(ctx.pal("#000000")))
    c.drawCircle(cx + pw * 0.1, y + inset + pw * 0.0725, pw * 0.016, D.paint(ctx.pal("#1d2140")))
    return sr


@register("device")
class Device(Scene):
    """Hero device shot. A phone at `height` of the frame (0.70) springs up from below and turns
    in yaw from yaw[0] to yaw[1] (in_out_quint over `tilt`, default 4b), then eases back half
    way over the rest of the shot; the bezel highlight and glass sheen follow the tilt; a soft
    contact + ambient shadow sits under it. Above it: an optional tracked `kicker`, the
    `headline` at the headline role (168 px, 700) flush-left on the grid, and `sub` set as a
    right-aligned standfirst. `callouts` ({text, icon, side, y, x, dy, color}) pop in on the
    beat (`first_callout`, `callout_gap`): a pill (spring) inside the safe area with a leader
    line to the point (x, y) on the screen (fractions of the screen), tracked through the tilt.
    `screen` = screenshot path (9:19.5) or the procedural `ui`; `scroll` = list scroll (px@1080).
    Legacy `swing` (degrees) sets a symmetric yaw when `yaw` is not given."""
    defaults = {"frame": "phone", "screen": "", "ui": {}, "kicker": "", "headline": "", "sub": "", "callouts": [],
                "scroll": 0, "swing": None, "yaw": None, "pitch": [7, 2], "tilt": "4b", "height": 0.70,
                "first_callout": "3b", "callout_gap": "1b", "land": "1b"}
    time_params = ("first_callout", "callout_gap", "tilt", "land")

    def cues(self) -> list[Cue]:
        out = [Cue("sfx", 0.0, data={"sound": "whoosh_big", "gain": -4}),
               Cue("sfx", self.p["land"], data={"sound": "thud", "gain": -3}),
               Cue("hit", self.p["land"], data={"strength": 0.25})]
        for i, _ in enumerate(self.p["callouts"]):
            out.append(Cue("sfx", self.p["first_callout"] + i * self.p["callout_gap"],
                           data={"sound": "pop_soft", "gain": -2}))
        return out

    def _yaw(self) -> tuple[float, float]:
        y = self.p["yaw"]
        if isinstance(y, (list, tuple)) and len(y) >= 2:
            return float(y[0]), float(y[1])
        if isinstance(y, (int, float)):
            return -float(y), float(y)
        if self.p["swing"] is not None:
            a = min(abs(float(self.p["swing"])), 30.0) / 2
            return -a, a
        return -12.0, 12.0

    def geometry(self, ctx: Ctx) -> dict[str, Any]:
        """Layout of headline block and device for this frame (used by draw and by tests)."""
        g = L.grid(ctx)
        hr, kr = T["headline"], T["kicker"]
        out: dict[str, Any] = {"g": g}
        top = g.safe_top
        if ctx.landscape:
            col_x, col_w = g.x0, g.span_w(6)
        else:
            col_x, col_w = g.x0, g.head_w
        y = top
        if self.p["kicker"]:
            kf = kr.font(ctx)
            out["kicker"] = (kf, y)
            y += D.cap_height(kf) + g.bl(6)
        bottom = y
        if self.p["headline"]:
            hf = L.fit_role(ctx, hr, self.p["headline"], col_w)
            lines = D.wrap(hf, self.p["headline"], col_w, hr.tk(ctx), style="balance")
            blk = D.set_lines(hf, lines, col_x, y, hr.leading, hr.tk(ctx), "left")
            out["head"] = (hf, blk)
            bottom = blk.bottom
        if ctx.landscape:
            ph = min(float(self.p["height"]) * 1.12, 0.86) * ctx.H
            pcx, pcy = g.col(7) + g.span_w(5) / 2, ctx.H / 2
        else:
            gap = g.bl(3)
            dev_top = (bottom + gap) if (self.p["headline"] or self.p["kicker"]) else g.safe_top
            avail = ctx.H - g.bl(10) - dev_top
            ph = min(float(self.p["height"]) * ctx.H, avail)
            if not (self.p["headline"] or self.p["kicker"]):
                dev_top = (ctx.H - ph) / 2
            pcx, pcy = g.cx, dev_top + ph / 2
        out.update(ph=ph, pw=ph / 2.08, cx=pcx, cy=pcy, col_x=col_x, col_w=col_w)
        return out

    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        geo = self.geometry(ctx)
        g = geo["g"]
        ph, pw, cx, cy = geo["ph"], geo["pw"], geo["cx"], geo["cy"]
        bl = max(ctx.beat_len, 1e-3)
        # -- headline block (behind the device) ---------------------------------
        if "kicker" in geo:
            kf, ky = geo["kicker"]
            kr = T["kicker"]
            rule = g.col_w * E.out_expo(E.clamp(lt / (0.75 * bl)))
            c.drawRect(skia.Rect.MakeXYWH(geo["col_x"], ky + D.cap_height(kf) / 2 - g.bl(0.25), rule, g.bl(0.5)),
                       D.paint(ctx.pal("accent")))
            _mask_line(ctx, kr.text(self.p["kicker"]), geo["col_x"] + g.col_w + g.gutter, ky + D.cap_height(kf), kf,
                       ctx.pal("accent"), (lt + ctx.frame) / (0.75 * bl), 0.0, "left", kr.tk(ctx))
        if "head" in geo:
            hf, blk = geo["head"]
            hr = T["headline"]
            for i, ln in enumerate(blk.lines):
                _mask_line(ctx, ln.text, ln.x, ln.baseline, hf, ctx.pal("ink"), (lt + ctx.frame - i * 0.25 * bl) / bl,
                           0.0, "left", hr.tk(ctx))
            if self.p["sub"] and not ctx.landscape:
                sr_ = T["body"]
                sf = sr_.font(ctx, weight=500)
                slines = [s.strip() for s in str(self.p["sub"]).replace(", ", ",\n").split("\n")][:2]
                pitch = sr_.pitch(g)
                last = blk.lines[-1].baseline
                for j, sl in enumerate(slines):
                    yb = last - (len(slines) - 1 - j) * pitch
                    _mask_line(ctx, sl, g.x1, yb, sf, ctx.pal("muted"), (lt - (0.5 + 0.25 * j) * bl) / bl, 0.0, "right",
                               sr_.tk(ctx))
        # -- device -------------------------------------------------------------
        land = float(self.p["land"])
        sp = _spring(land * 1.9, 0.18)
        rise = 1.0 - sp.value(max(lt, 0.0))
        y_off = rise * ctx.H * 0.4
        y0, y1 = self._yaw()
        tilt = max(float(self.p["tilt"]), 1e-3)
        u1 = E.in_out_quint(E.clamp(lt / tilt))
        u2 = E.in_out_quint(E.clamp((lt - tilt) / max(self.dur - tilt, 1e-3)))
        yaw = y0 + (y1 - y0) * u1 - (y1 - y0) * 0.5 * u2
        pa = self.p["pitch"] if isinstance(self.p["pitch"], (list, tuple)) else [self.p["pitch"], self.p["pitch"]]
        pitch = float(pa[0]) + (float(pa[-1]) - float(pa[0])) * E.out_quint(E.clamp(lt / (2 * bl)))
        pitch += 1.2 * math.sin(lt * 1.3)
        fy = g.px(10) * math.sin(lt * 1.6)
        dcy = cy + y_off + fy
        scale = 0.9 + 0.1 * E.out_expo(E.clamp(lt / (1.5 * bl)))
        m = D.persp3d(cx, dcy, math.radians(pitch), math.radians(yaw), 0.0, dist=1500 * ctx.s, scale=scale)
        M = _m44_np(m)
        light = E.clamp(yaw / 14.0, -1.0, 1.0)
        # shadows (in 3D so they turn with the device): ambient + contact
        c.save()
        c.concat(m)
        r = pw * 0.16
        D.rrect(c, cx - pw / 2 + pw * 0.05, dcy - ph / 2 + ph * 0.05, pw, ph, r,
                D.paint(ctx.pal("#000000", 0.55), blur=pw * 0.1))
        D.rrect(c, cx - pw / 2 + pw * 0.015, dcy - ph / 2 + ph * 0.012, pw, ph, r,
                D.paint(ctx.pal("#000000", 0.45), blur=pw * 0.025))
        scroll = float(self.p["scroll"] or 0) * g.u * E.in_out_quint(E.clamp((lt - 2 * bl) / max(self.dur - 3 * bl, bl)))
        if isinstance(self.p["scroll"], (int, float)) and 0 < float(self.p["scroll"]) <= 1.0:
            scroll = float(self.p["scroll"]) * ph * 0.25 * E.in_out_quint(E.clamp((lt - 2 * bl) / max(self.dur - 3 * bl, bl)))
        sr = draw_phone(ctx, cx, dcy, pw, self.p["screen"], self.p["ui"], lt, scroll,
                        E.clamp((lt - 0.5 * bl) / (2.5 * bl)), light)
        c.restore()
        corners = [_proj(M, sx, sy) for sx, sy in ((cx - pw / 2, dcy - ph / 2), (cx + pw / 2, dcy - ph / 2),
                                                   (cx + pw / 2, dcy + ph / 2), (cx - pw / 2, dcy + ph / 2))]
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        ctx.publish("device", skia.Rect(min(xs), min(ys), max(xs), max(ys)))
        # -- callouts -------------------------------------------------------------
        for i, co in enumerate(self.p["callouts"]):
            t0 = float(self.p["first_callout"]) + i * float(self.p["callout_gap"])
            self._callout(ctx, g, co, i, lt - t0, bl, M, sr)

    def _callout(self, ctx: Ctx, g: L.Grid, co: dict, i: int, u_t: float, bl: float, M: np.ndarray,
                 sr: skia.Rect) -> skia.Rect | None:
        """Draw callout i at u_t seconds after its entry; returns the settled pill rect."""
        if u_t <= 0:
            return None
        c = ctx.c
        side = co.get("side", "left" if i % 2 == 0 else "right")
        tx = sr.left() + sr.width() * float(co.get("x", 0.72 if side == "left" else 0.28))
        ty = sr.top() + sr.height() * float(co.get("y", 0.3 + 0.2 * i))
        px_, py_ = _proj(M, tx, ty)
        cr = T["caption"]
        f = cr.font(ctx, weight=650)
        text = str(co.get("text", ""))
        bh = g.bl(11)
        ir = bh * 0.32
        tw = D.text_w(f, text, cr.tk(ctx))
        w = g.bl(2) + 2 * ir + g.bl(2) + tw + g.bl(3.5)
        dy = float(co.get("dy", -0.075)) * ctx.H
        by = py_ + dy - bh / 2
        bx = g.x0 if side == "left" else g.x1 - w
        bx = min(max(bx, g.x0), g.x1 - w)
        by = min(max(by, g.safe_top), g.safe_bottom - bh)
        # leader: from the pill's inner end to the target, drawn on (out_expo over 1/2 beat)
        ax = bx + w if side == "left" else bx
        ay = by + bh / 2
        lu = E.out_expo(E.clamp((u_t - 0.25 * bl) / (0.5 * bl)))  # draws once the pill is out
        col = co.get("color", "accent")
        lx, ly = ax + (px_ - ax) * lu, ay + (py_ - ay) * lu
        if lu > 0:
            c.drawLine(ax, ay, lx, ly, D.paint(ctx.pal("ink", 0.9), stroke=g.bl(0.4)))
        if lu > 0.6:
            k = E.out_expo(E.clamp((lu - 0.6) / 0.4))
            ring = (u_t / bl) % 1.0
            c.drawCircle(px_, py_, g.bl(1.6) * k, D.paint(ctx.pal("ink")))
            c.drawCircle(px_, py_, g.bl(1.6) * k * 0.55, D.paint(ctx.pal(col)))
            c.drawCircle(px_, py_, g.bl(1.6 + 3.5 * E.out_expo(ring)), D.paint(ctx.pal("ink", 0.5 * (1 - ring)),
                                                                              stroke=g.bl(0.3)))
        # pill: spring scale from its anchor end
        sp = _spring(0.9 * bl, 0.22)
        e = sp.value(u_t)
        ox = bx + w if side == "left" else bx
        c.save()
        c.translate(ox, ay)
        c.scale(max(e, 0.0), max(e, 0.0))
        c.translate(-ox, -ay)
        D.rrect(c, bx, by + g.bl(1.5), w, bh, bh / 2, D.paint(ctx.pal("#000000", 0.45), blur=g.bl(2.5)))
        D.rrect(c, bx, by, w, bh, bh / 2, D.paint(ctx.pal("paper")))
        icx = bx + g.bl(2) + ir
        c.drawCircle(icx, ay, ir, _shade(D.linear_grad(icx - ir, ay - ir, icx + ir, ay + ir,
                                                       [ctx.pal(col), ctx.pal("accent2" if col == "accent" else col)])))
        D.icon(c, co.get("icon", "check"), icx, ay, ir * 1.05, ctx.pal("#ffffff"))
        D.draw_text(c, text, icx + ir + g.bl(2), ay, f, ctx.pal("ink_dark"), align="left", tracking=cr.tk(ctx),
                    valign="middle")
        c.restore()
        return skia.Rect.MakeXYWH(bx, by, w, bh)


# ---------------------------------------------------------------------- features

FEATURE_COLORS = ("accent", "accent2", "pos", "neg")


@register("features")
class Features(Scene):
    """Features, one at a time: every `per` beats (default 2b) a full-bleed colour panel wipes in
    from the right with a large icon, and the label (title role, 126 px) + body line rise in
    under it on the grid; the outgoing label leaves upward (in_quart). `heading` becomes the
    panel kicker with a 01/04 counter; progress segments sit on the bottom safe line.
    Items: {icon, title, text, color}. `layout: "grid"` (or `columns` > 0) keeps the v0.1 cards."""
    defaults = {"heading": "", "items": [], "per": "2b", "stagger": "1/2b", "first": 0.0, "columns": 0,
                "layout": "solo"}

    def _grid_mode(self) -> bool:
        return self.p["layout"] == "grid" or int(self.p["columns"] or 0) > 0

    def _per(self) -> float:
        n = max(1, len(self.p["items"]))
        per = float(self.p["per"])
        return min(per, self.dur / n) if per > 0 else self.dur / n

    def cues(self) -> list[Cue]:
        if self._grid_mode():
            return [Cue("sfx", float(self.p["first"]) + i * float(self.p["stagger"]), data={"sound": "pop", "gain": -2})
                    for i in range(len(self.p["items"]))]
        per = self._per()
        out = []
        for i in range(len(self.p["items"])):
            out.append(Cue("sfx", i * per, data={"sound": "blip_up" if i else "pop", "gain": -3}))
            if i:
                out.append(Cue("sfx", i * per, data={"sound": "swish", "gain": -8}))
        return out

    def draw(self, ctx: Ctx) -> None:
        if self._grid_mode():
            self._draw_grid(ctx)
            return
        items = self.p["items"]
        if not items:
            return
        g = L.grid(ctx)
        lt, bl = ctx.lt, max(ctx.beat_len, 1e-3)
        per = self._per()
        cur = min(int(lt // per) if lt >= 0 else 0, len(items) - 1)
        panel_h = ctx.H * (0.56 if ctx.portrait else 1.0)
        panel_w = ctx.W if ctx.portrait else ctx.W * 0.5
        # panels: previous one stays underneath while the current wipes in
        for i in (cur - 1, cur):
            if i < 0:
                continue
            u = (lt - i * per) / (0.5 * bl)
            reveal = 1.0 if i == 0 else E.out_expo(E.clamp(u))
            self._panel(ctx, g, items[i], i, len(items), lt - i * per, reveal, panel_w, panel_h, per, bl)
        # text block
        tr, br = T["title"], T["body"]
        for i in (cur - 1, cur, cur + 1):
            if not 0 <= i < len(items):
                continue
            li = lt - i * per
            u_in = (li + ctx.frame) / (0.75 * bl)
            u_out = 0.0 if i == len(items) - 1 else (li - (per - 0.25 * bl)) / (0.25 * bl)
            if u_in <= 0 or u_out >= 1:
                continue
            it = items[i]
            x = g.x0 if ctx.portrait else ctx.W * 0.5 + g.margin
            mw = g.content_w if ctx.portrait else ctx.W * 0.5 - 2 * g.margin
            top = panel_h + g.bl(12) if ctx.portrait else g.anchor_y
            tf = L.fit_role(ctx, tr, it.get("title", ""), mw, weight=700)
            blk = D.set_lines(tf, [it.get("title", "")], x, top, tr.leading, tr.tk(ctx), "left")
            ln = blk.lines[0]
            _mask_line(ctx, ln.text, ln.x, ln.baseline, tf, ctx.pal("ink"), u_in, u_out, "left", tr.tk(ctx))
            if it.get("text"):
                bf = br.font(ctx, weight=500)
                bls = D.wrap(bf, it["text"], mw, br.tk(ctx), style="balance")[:2]
                bb = D.set_lines(bf, bls, x, blk.bottom + g.bl(5), br.leading, br.tk(ctx), "left")
                for j, bln in enumerate(bb.lines):
                    _mask_line(ctx, bln.text, bln.x, bln.baseline, bf, ctx.pal("muted"),
                               (li - (0.25 + 0.125 * j) * bl) / (0.75 * bl), u_out, "left", br.tk(ctx))
        # progress segments on the bottom safe line
        n = len(items)
        gap = g.gutter
        sw = (g.content_w - gap * (n - 1)) / n
        h = g.bl(1)
        y = g.safe_bottom - h
        for i in range(n):
            x = g.x0 + i * (sw + gap)
            D.rrect(ctx.c, x, y, sw, h, h / 2, D.paint(ctx.pal("ink", 0.18)))
            f = E.clamp((lt - i * per) / per)
            if f > 0:
                D.rrect(ctx.c, x, y, sw * f, h, h / 2, D.paint(ctx.pal("ink" if f < 1 else "accent", 0.95)))

    def _panel(self, ctx: Ctx, g: L.Grid, it: dict, i: int, n: int, li: float, reveal: float, pw: float, ph: float,
               per: float, bl: float) -> None:
        c = ctx.c
        key = it.get("color", FEATURE_COLORS[i % len(FEATURE_COLORS)])
        rgb = ctx.pal.rgb(key)
        dark = D.mix_rgb(rgb, ctx.pal.rgb("bg"), 0.45)
        ink = _on(ctx, key)
        x_edge = pw * (1 - reveal)
        c.save()
        c.clipRect(skia.Rect(x_edge, 0, pw, ph))
        c.drawRect(skia.Rect(0, 0, pw, ph), _shade(D.linear_grad(0, 0, pw * 0.4, ph, [D.argb(rgb), D.argb(dark)])))
        # slow concentric rings (continuous motion behind the icon)
        icx, icy = pw / 2 + (1 - reveal) * pw * 0.25, ph * 0.56
        size = min(pw * 0.5, ph * 0.46)
        for k in range(4):
            rr = size * (0.75 + 0.42 * k) * (1 + 0.04 * math.sin(li * 1.4 + k))
            c.drawCircle(icx, icy, rr, D.paint(ctx.pal(ink, 0.10 - 0.02 * k), stroke=g.bl(0.5)))
        # icon: scales in (out_expo), drifts, leaves (in_expo) at the end of its window
        u_in = E.out_expo(E.clamp((li + ctx.frame) / (0.75 * bl)))
        u_out = 0.0 if i == n - 1 else E.in_expo(E.clamp((li - (per - 0.25 * bl)) / (0.25 * bl)))
        k = (0.7 + 0.3 * u_in) * (1 + 0.05 * E.clamp(li / per)) * (1 - 0.15 * u_out)
        rot = -10 * (1 - u_in) + 2.0 * math.sin(li * 1.1)
        c.save()
        c.translate(icx, icy)
        c.rotate(rot)
        c.scale(k, k)
        tile = size * 1.04
        D.rrect(c, -tile / 2, -tile / 2 + g.bl(4), tile, tile, tile * 0.26, D.paint(ctx.pal("#000000", 0.35),
                                                                                     blur=g.bl(5)))
        D.rrect(c, -tile / 2, -tile / 2, tile, tile, tile * 0.26,
                _shade(D.linear_grad(0, -tile / 2, 0, tile / 2, [ctx.pal(ink, 0.22), ctx.pal(ink, 0.08)])))
        D.rrect(c, -tile / 2, -tile / 2, tile, tile, tile * 0.26, D.paint(ctx.pal(ink, 0.35), stroke=g.bl(0.4)))
        D.icon(c, it.get("icon", "star"), 0, 0, size * 0.62, ctx.pal(ink, max(0.0, 1 - u_out)), sw=size * 0.05)
        c.restore()
        # panel kicker + counter on the top safe line
        kr = T["kicker"]
        kf = kr.font(ctx)
        ky = g.safe_top + D.cap_height(kf)
        if self.p["heading"]:
            D.draw_text(c, kr.text(self.p["heading"]), g.x0, ky, kf, ctx.pal(ink, 0.9), align="left", tracking=kr.tk(ctx))
        mr = T["meta"]
        D.draw_text(c, f"{i + 1:02d} / {n:02d}", pw - g.margin, ky, mr.font(ctx, weight=600), ctx.pal(ink, 0.9),
                    align="right", tracking=mr.tk(ctx))
        c.restore()
        if 0.002 < reveal < 0.998:  # leading edge
            c.drawRect(skia.Rect(x_edge - g.bl(0.5), 0, x_edge + g.bl(0.5), ph), D.paint(ctx.pal("ink", 0.9)))

    def _draw_grid(self, ctx: Ctx) -> None:
        """v0.1 card grid (opt-in), on the grid with entry easing only."""
        c, lt = ctx.c, ctx.lt
        g = L.grid(ctx)
        items = self.p["items"]
        cols = int(self.p["columns"] or 0) or (2 if ctx.portrait else min(4, max(1, len(items))))
        rows = max(1, math.ceil(len(items) / cols))
        gap = g.gutter
        top = g.anchor_y
        if self.p["heading"]:
            hr = T["title"]
            hf = L.fit_role(ctx, hr, self.p["heading"], g.head_w, weight=700)
            lines = D.wrap(hf, self.p["heading"], g.head_w, hr.tk(ctx), style="balance")
            blk = D.set_lines(hf, lines, g.x0, g.safe_top, hr.leading, hr.tk(ctx), "left")
            for i, ln in enumerate(blk.lines):
                reveal_line(ctx, ln.text, ln.x, ln.baseline, hf, ctx.pal("ink"), (lt - i * 0.1) / 0.7, "left", hr.tk(ctx))
            top = max(top, blk.bottom + g.bl(6))
        cw = (g.content_w - gap * (cols - 1)) / cols
        ch = min(cw * 1.05, (g.safe_bottom - top - gap * (rows - 1)) / rows)
        for i, it in enumerate(items):
            r_, k = divmod(i, cols)
            x = g.x0 + k * (cw + gap)
            y = top + r_ * (ch + gap)
            u = E.clamp((lt - float(self.p["first"]) - i * float(self.p["stagger"])) / 0.5)
            if u <= 0:
                continue
            e = E.out_expo(u)
            c.save()
            c.translate(x, y + (1 - e) * g.bl(8))
            D.rrect(c, 0, 0, cw, ch, g.bl(4), D.paint(ctx.pal("#ffffff", 0.08 * e)))
            D.rrect(c, 0, 0, cw, ch, g.bl(4), D.paint(ctx.pal("#ffffff", 0.16 * e), stroke=g.bl(0.25)))
            ic = g.bl(7)
            pad = g.bl(5)
            c.drawCircle(pad + ic, pad + ic, ic, _shade(D.linear_grad(pad, pad, pad + 2 * ic, pad + 2 * ic,
                                                                      [ctx.pal("accent"), ctx.pal("accent2")])))
            D.icon(c, it.get("icon", "star"), pad + ic, pad + ic, ic * 0.95, ctx.pal("#ffffff"))
            tr = T["subhead"]
            tf = D.fit_font(ctx.theme["display"], it.get("title", ""), cw - 2 * pad, tr.px(g), 700)
            D.draw_text(c, it.get("title", ""), pad, pad + 2 * ic + g.bl(10), tf, ctx.pal("ink", e), align="left")
            bf = T["caption"].font(ctx)
            for li, ln in enumerate(D.wrap(bf, it.get("text", ""), cw - 2 * pad)[:3]):
                D.draw_text(c, ln, pad, pad + 2 * ic + g.bl(17) + li * T["caption"].pitch(g), bf, ctx.pal("muted", e),
                            align="left")
            c.restore()


def _lockup(ctx: Ctx, g: L.Grid, name: str, mark: str, e: float) -> None:
    """Small app icon + product name on the top safe line, flush left (entry e in 0..1)."""
    if e <= 0:
        return
    c = ctx.c
    sz = g.bl(12)
    y = g.safe_top + (1 - e) * g.bl(4)
    rad = sz * 0.24
    D.rrect(c, g.x0, y, sz, sz, rad, _shade(D.linear_grad(g.x0, y, g.x0 + sz, y + sz,
                                                          [ctx.pal("accent", e), ctx.pal("accent2", e)])))
    D.icon(c, mark if mark in D.ICONS else "spark", g.x0 + sz / 2, y + sz / 2, sz * 0.56, ctx.pal("#ffffff", e))
    r = T["body"]
    f = r.font(ctx, weight=700, face="display")
    D.draw_text(c, name, g.x0 + sz + g.gutter, y + sz / 2, f, ctx.pal("ink", e), align="left",
                tracking=r.tk(ctx, "display"), valign="middle")


# ---------------------------------------------------------------------- CTA


@register("cta")
class CTA(Scene):
    """Call to action on the grid: tracked `kicker`, the `headline` (168 px, 700) flush-left on the
    shared anchor, a full-width pill button that springs in on beat 1 and pulses on every beat
    (spring landing one frame after the kick, with a ring wave), a touch that presses it on the
    downbeat `tap_at` (click SFX + ripple; the label morphs to `done` if set), a `note` under the
    button and the `sub` line (platforms) on the bottom safe line. `lockup` (product name) puts a
    small app icon (`mark`) + name on the top safe line so the block spans the frame."""
    defaults = {"kicker": "", "headline": "Try it free", "button": "Get the app", "sub": "", "note": "", "done": "",
                "tap_at": None, "enter": "1b", "pulse": 0.045, "lockup": "", "mark": "spark"}
    time_params = ("enter",)

    def tap(self) -> float:
        if self.p["tap_at"] is not None:
            return float(self.p["tap_at"])
        # first bar line at least 2 beats in (else beat 2)
        for b in range(2, 9):
            t = self.start + self.bt(b)
            if self.tl.is_downbeat(t, 0.002) and self.bt(b) < self.dur - 0.25:
                return self.bt(b)
        return self.bt(2)

    def cues(self) -> list[Cue]:
        tap = self.tap()
        return [Cue("sfx", self.p["enter"], data={"sound": "pop", "gain": -2}),
                Cue("sfx", tap, data={"sound": "ui_click", "gain": 2}),
                Cue("sfx", tap, data={"sound": "chime", "gain": -6}),
                Cue("hit", tap, data={"strength": 0.35})]

    def layout(self, ctx: Ctx) -> dict[str, Any]:
        g = L.grid(ctx)
        hr, kr = T["headline"], T["kicker"]
        hf = L.fit_role(ctx, hr, self.p["headline"], g.head_w)
        lines = D.wrap(hf, self.p["headline"], g.head_w, hr.tk(ctx), style="balance")
        kf = kr.font(ctx)
        kick_h = (D.cap_height(kf) + g.bl(6)) if self.p["kicker"] else 0.0
        top = g.anchor_y - kick_h
        blk = D.set_lines(hf, lines, g.x0, top + kick_h, hr.leading, hr.tk(ctx), "left")
        bh = g.bl(24)
        by = blk.bottom + g.bl(10)
        return {"g": g, "hf": hf, "blk": blk, "kf": kf, "top": top,
                "button": skia.Rect.MakeXYWH(g.x0, by, g.content_w, bh)}

    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        lay = self.layout(ctx)
        g, blk, br_ = lay["g"], lay["blk"], lay["button"]
        bl = max(ctx.beat_len, 1e-3)
        hr, kr = T["headline"], T["kicker"]
        if self.p["lockup"]:
            _lockup(ctx, g, str(self.p["lockup"]), str(self.p["mark"]), E.out_expo(E.clamp((lt + ctx.frame) / (0.75 * bl))))
        if self.p["kicker"]:
            kf = lay["kf"]
            ky = lay["top"] + D.cap_height(kf)
            rule = g.col_w * E.out_expo(E.clamp(lt / (0.75 * bl)))
            c.drawRect(skia.Rect.MakeXYWH(g.x0, ky - D.cap_height(kf) / 2 - g.bl(0.25), rule, g.bl(0.5)),
                       D.paint(ctx.pal("accent")))
            _mask_line(ctx, kr.text(self.p["kicker"]), g.x0 + g.col_w + g.gutter, ky, kf, ctx.pal("accent"),
                       (lt + ctx.frame) / (0.75 * bl), 0.0, "left", kr.tk(ctx))
        for i, ln in enumerate(blk.lines):
            _mask_line(ctx, ln.text, ln.x, ln.baseline, lay["hf"], ctx.pal("ink"), (lt + ctx.frame - i * 0.25 * bl) / bl,
                       0.0, "left", hr.tk(ctx))
        # button
        enter = float(self.p["enter"])
        tap = self.tap()
        e = _spring(1.2 * bl, 0.2).value(max(lt - enter + 0.5 * bl, 0.0)) if lt > enter - 0.5 * bl else 0.0
        if e > 0:
            # beat pulse: lands one frame after each kick, then springs back
            lb = ctx.local_beat
            kb = math.floor(lb + 1e-6)
            pulse = 0.0
            if lt > enter + 0.25 * bl:
                dt = lt - ctx.beats(kb)
                fr = ctx.frame
                if dt < fr:
                    pulse = E.clamp(dt / fr)
                else:
                    pulse = 1.0 - _spring(0.8 * bl, 0.3).value(dt - fr)
            press = 0.0
            if tap - 2 * ctx.frame <= lt:
                d = lt - tap
                press = E.clamp((d + 2 * ctx.frame) / (2 * ctx.frame)) if d < 0 else 1.0 - _spring(0.75 * bl, 0.3).value(d)
            k = e * (1 + float(self.p["pulse"]) * pulse) * (1 - 0.06 * press)
            bx, by, bw, bh = br_.left(), br_.top(), br_.width(), br_.height()
            bcx, bcy = br_.centerX(), br_.centerY()
            # ring wave on each beat
            if lt > enter + 0.25 * bl:
                ph = (lb - kb)
                a = 0.55 * (1 - E.out_quint(ph))
                grow = g.bl(5) * E.out_expo(ph)
                D.rrect(c, bx - grow, by - grow, bw + 2 * grow, bh + 2 * grow, bh / 2 + grow,
                        D.paint(ctx.pal("accent", a), stroke=g.bl(0.5)))
            c.save()
            c.translate(bcx, bcy)
            c.scale(k, k)
            c.translate(-bcx, -bcy)
            D.rrect(c, bx, by + g.bl(3), bw, bh, bh / 2, D.paint(ctx.pal("accent", 0.45), blur=g.bl(6)))
            D.rrect(c, bx, by, bw, bh, bh / 2, _shade(D.linear_grad(bx, by, bx + bw, by + bh,
                                                                     [ctx.pal("accent"), ctx.pal("accent2")])))
            D.rrect(c, bx, by, bw, bh * 0.5, bh / 2, _shade(D.linear_grad(0, by, 0, by + bh * 0.5,
                                                                           [ctx.pal("#ffffff", 0.25),
                                                                            ctx.pal("#ffffff", 0.0)])))
            sr_ = T["subhead"]
            lf = sr_.font(ctx, weight=700, face="display")
            done = self.p["done"] and lt >= tap
            label = self.p["done"] if done else self.p["button"]
            r_in = E.out_expo(E.clamp((lt - tap) / (0.5 * bl))) if done else 1.0
            ir = bh * 0.34
            icx = bx + bw - bh / 2
            c.save()
            c.clipRRect(skia.RRect.MakeRectXY(br_, bh / 2, bh / 2), skia.ClipOp.kIntersect, True)
            D.draw_text(c, label, bx + bh * 0.5, bcy + (1 - r_in) * bh * 0.6, lf, ctx.pal("#ffffff"), align="left",
                        tracking=sr_.tk(ctx, "display"), valign="middle")
            c.restore()
            c.drawCircle(icx, bcy, ir, D.paint(ctx.pal("#ffffff")))
            D.icon(c, "check" if done else "arrow", icx, bcy, ir * 1.1, ctx.pal("accent"), sw=ir * 0.14)
            c.restore()
            # touch: glides in over the beat before the tap, presses, ripples, lifts away
            if tap - bl <= lt <= tap + 1.5 * bl:
                tx, ty = bcx + bw * 0.18, bcy + bh * 0.1
                u = E.out_quint(E.clamp((lt - (tap - bl)) / (bl - ctx.frame)))
                v = E.in_quart(E.clamp((lt - tap - 0.5 * bl) / bl))
                fx = tx + (1 - u) * g.bl(30) + v * g.bl(18)
                fy = ty + (1 - u) * g.bl(50) + v * g.bl(40)
                rad = g.bl(4.5) * (1 - 0.18 * press)
                a = (1 - v) * E.clamp(u * 2)
                if lt >= tap:
                    rr = E.out_expo(E.clamp((lt - tap) / bl))
                    c.drawCircle(tx, ty, g.bl(4) + g.bl(16) * rr, D.paint(ctx.pal("#ffffff", 0.6 * (1 - rr)),
                                                                          stroke=g.bl(0.5)))
                c.drawCircle(fx, fy + g.bl(1), rad, D.paint(ctx.pal("#000000", 0.3 * a), blur=g.bl(1.5)))
                c.drawCircle(fx, fy, rad, D.paint(ctx.pal("#ffffff", 0.9 * a)))
        y = br_.bottom() + g.bl(8)
        if self.p["note"]:
            nr = T["body"]
            nf = nr.font(ctx, weight=500)
            yb = y + D.cap_height(nf)
            _mask_line(ctx, self.p["note"], g.x0, yb, nf, ctx.pal("muted"), (lt - enter - 0.5 * bl) / bl, 0.0, "left",
                       nr.tk(ctx))
        if self.p["sub"]:
            mr = T["meta"]
            mf = mr.font(ctx, weight=600)
            u = E.out_expo(E.clamp((lt - enter - bl) / bl))
            c.drawRect(skia.Rect.MakeXYWH(g.x0, g.safe_bottom - g.bl(6), g.content_w * u, max(1.0, g.bl(0.25))),
                       D.paint(ctx.pal("ink", 0.3)))
            _mask_line(ctx, self.p["sub"], g.x0, g.safe_bottom, mf, ctx.pal("ink", 0.85), (lt - enter - bl) / bl, 0.0,
                       "left", mr.tk(ctx))


# ---------------------------------------------------------------------- brand end card


@register("brand")
class Brand(Scene):
    """Brand end card: an app icon (gradient tile + `mark` icon name or image path) lands, then
    the wordmark (`text`, display role 224 px, `weight` 700) is revealed by an accent mask bar
    that travels across it (out_quint from the scene start) and lands on the beat `hit_at` (logo hit + music stop), a sheen
    sweeps the wordmark, `tagline` and `url` rise in, and `badges` (["App Store", ...]) sit
    on the bottom safe line from beat 2."""
    defaults = {"text": "brand", "tagline": "", "url": "", "mark": "spark", "weight": 700, "size": None,
                "badges": [], "badge_kicker": "Download on", "hit_at": "1b", "stop_music": True}
    time_params = ("hit_at",)

    def cues(self) -> list[Cue]:
        h = float(self.p["hit_at"])
        out = [Cue("sfx", 0.0, data={"sound": "riser_short", "gain": -4}), Cue("sfx", h, data={"sound": "logo_hit"}),
               Cue("hit", h, data={"strength": 0.8, "flash": 0.15})]
        if self.p["badges"]:
            out.append(Cue("sfx", self.bt(2), data={"sound": "pop_soft", "gain": -6}))
        if self.p["stop_music"]:
            out.append(Cue("cue", h, name="music_stop"))
        return out

    def layout(self, ctx: Ctx) -> dict[str, Any]:
        g = L.grid(ctx)
        r = T["display"]
        size = float(self.p["size"]) * g.u if self.p["size"] else r.px(g)
        f = L.fit_role(ctx, r.with_(size=size / g.u / g.P.type_k), str(self.p["text"]), g.content_w,
                       weight=int(self.p["weight"]))
        tr = r.tk(ctx)
        tw = D.text_w(f, str(self.p["text"]), tr)
        cap = D.cap_height(f)
        icon = g.bl(40) if self.p["badges"] else g.bl(32)
        mark_top = g.place(icon + g.bl(10) + cap, "center") - g.bl(8)
        if self.p["badges"]:  # icon .. badges spans >= 62 % of the frame height (S6 content block)
            mark_top = max(g.safe_top, min(mark_top, g.safe_bottom - 0.62 * ctx.H))
        base = mark_top + icon + g.bl(10) + cap
        return {"g": g, "f": f, "tr": tr, "tw": tw, "cap": cap, "icon": icon, "mark_top": mark_top, "base": base}

    def draw(self, ctx: Ctx) -> None:
        c, lt = ctx.c, ctx.lt
        lay = self.layout(ctx)
        g, f, tr, tw, cap = lay["g"], lay["f"], lay["tr"], lay["tw"], lay["cap"]
        bl = max(ctx.beat_len, 1e-3)
        h = float(self.p["hit_at"])
        text = str(self.p["text"])
        x = g.cx - tw / 2
        y = lay["base"]
        # app icon
        isz = lay["icon"]
        e = E.out_expo(E.clamp((lt + ctx.frame) / (0.75 * bl)))
        ix, iy = g.cx, lay["mark_top"] + isz / 2 + (1 - e) * g.bl(10)
        if e > 0:
            k = 0.6 + 0.4 * e
            c.save()
            c.translate(ix, iy)
            c.scale(k, k)
            c.rotate(-12 * (1 - e))
            rad = isz * 0.24
            D.rrect(c, -isz / 2, -isz / 2 + g.bl(3), isz, isz, rad, D.paint(ctx.pal("accent", 0.5 * e), blur=g.bl(6)))
            D.rrect(c, -isz / 2, -isz / 2, isz, isz, rad, _shade(D.linear_grad(-isz / 2, -isz / 2, isz / 2, isz / 2,
                                                                                [ctx.pal("accent"), ctx.pal("accent2")])))
            D.rrect(c, -isz / 2, -isz / 2, isz, isz * 0.5, rad, _shade(D.linear_grad(0, -isz / 2, 0, 0,
                                                                                      [ctx.pal("#ffffff", 0.28),
                                                                                       ctx.pal("#ffffff", 0.0)])))
            mark = str(self.p["mark"] or "")
            drawn = False
            if mark.endswith((".png", ".jpg", ".webp")):
                path = mark if os.path.isabs(mark) else os.path.join(ctx.assets, mark)
                try:
                    D.draw_cover(c, D.load_image(path), skia.Rect(-isz * 0.32, -isz * 0.32, isz * 0.32, isz * 0.32))
                    drawn = True
                except (FileNotFoundError, ValueError):
                    drawn = False
            if not drawn:
                D.icon(c, mark if mark in D.ICONS else "spark", 0, 0, isz * 0.56, ctx.pal("#ffffff"))
            c.restore()
        # wordmark: revealed left -> right behind a travelling accent bar that lands on the hit
        a0 = 0.0  # the incoming clock already starts inside the transition window
        u = E.out_quint(E.clamp((lt - a0) / max(h - a0, 1e-3)))
        top, bot = y - cap - g.bl(4), y + g.bl(7)
        xr = x - g.bl(4) + (tw + g.bl(8)) * u
        if u > 0:
            c.save()
            c.clipRect(skia.Rect(x - g.bl(6), top, xr, bot))
            for i, (ch, ox, _w) in enumerate(D.glyph_layout(f, text, tr)):
                gu = E.out_expo(E.clamp((lt + ctx.frame - a0 - i * 0.06 * bl) / (0.75 * bl)))
                D.draw_text(c, ch, x + ox - (1 - gu) * g.bl(6), y, f, ctx.pal("ink"), align="left")
            # sheen sweep after the hit
            su = (lt - h - 0.5 * bl) / (1.5 * bl)
            if 0 < su < 1:
                sx = x - tw * 0.3 + tw * 1.6 * E.in_out_quint(su)
                path = D.text_path(text, x, y, f, tr)
                c.save()
                c.clipPath(path, skia.ClipOp.kIntersect, True)
                c.drawRect(skia.Rect(x - g.bl(6), top, x + tw + g.bl(6), bot),
                           _shade(D.linear_grad(sx - tw * 0.18, top, sx + tw * 0.18, bot,
                                                [ctx.pal("accent2", 0.0), ctx.pal("accent2", 0.85),
                                                 ctx.pal("accent2", 0.0)])))
                c.restore()
            c.restore()
        bar_out = E.in_expo(E.clamp((lt - h) / (0.5 * bl)))
        if 0 < u and bar_out < 1:
            bw = g.bl(1.5) * (1 - bar_out)
            c.drawRect(skia.Rect(xr - bw, top + (bot - top) * bar_out, xr, bot), D.paint(ctx.pal("accent")))
        # underline lands from the bar
        if lt > h:
            lu = E.out_expo(E.clamp((lt - h) / bl))
            uw = tw * lu
            c.drawRect(skia.Rect.MakeXYWH(x + tw - uw, y + g.bl(5), uw, g.bl(0.75)), D.paint(ctx.pal("accent")))
        # tagline + url
        ty = y + g.bl(14) + T["body"].px(g) * 0.7
        if self.p["tagline"]:
            br = T["body"]
            _mask_line(ctx, self.p["tagline"], g.cx, ty, br.font(ctx, weight=500), ctx.pal("muted"),
                       (lt - h - 0.25 * bl) / bl, 0.0, "center", br.tk(ctx))
        if self.p["url"]:
            mr = T["meta"]
            _mask_line(ctx, self.p["url"], g.cx, ty + g.bl(9), mr.font(ctx, weight=600), ctx.pal("accent"),
                       (lt - h - 0.75 * bl) / bl, 0.0, "center", mr.tk(ctx))
        # store badges on the bottom safe line
        badges = list(self.p["badges"])[:2]
        if badges:
            bh = g.bl(14)
            n = len(badges)
            kr = T["kicker"]
            kf = kr.font(ctx, weight=600)
            nf = T["subhead"].with_(size=44.0).font(ctx, weight=700, face="display")
            ktxt = kr.text(str(self.p["badge_kicker"]))
            inner = max([D.text_w(kf, ktxt, kr.tk(ctx))] + [D.text_w(nf, str(nm)) for nm in badges])
            bw = min(g.bl(13) + inner + g.bl(4), (g.content_w - (n - 1) * g.gutter * 2) / n)
            total = n * bw + (n - 1) * g.gutter * 2
            bx0 = g.cx - total / 2
            for j, name in enumerate(badges):
                ub = E.out_quint(E.clamp((lt - self.bt(2) + ctx.frame - j * 0.25 * bl) / (0.75 * bl)))
                if ub <= 0:
                    continue
                bx = bx0 + j * (bw + g.gutter * 2)
                by = g.safe_bottom - bh + (1 - ub) * g.bl(6)
                D.rrect(c, bx, by, bw, bh, g.bl(3), D.paint(ctx.pal("ink", 0.08 * ub)))
                D.rrect(c, bx, by, bw, bh, g.bl(3), D.paint(ctx.pal("ink", 0.7 * ub), stroke=g.bl(0.3)))
                icx = bx + g.bl(8)
                D.icon(c, "star" if j == 0 else "bolt", icx, by + bh / 2, g.bl(5), ctx.pal("ink", ub))
                D.draw_text(c, ktxt, icx + g.bl(5), by + bh * 0.42, kf, ctx.pal("ink", 0.7 * ub), align="left",
                            tracking=kr.tk(ctx))
                D.draw_text(c, str(name), icx + g.bl(5), by + bh * 0.8, nf, ctx.pal("ink", ub), align="left")
