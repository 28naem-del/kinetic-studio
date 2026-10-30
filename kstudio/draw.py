"""Drawing toolkit on top of skia: colour, text layout, shapes, paths, images, 3D."""
from __future__ import annotations

import math
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import skia

from . import fonts as F

# --------------------------------------------------------------------- colour

def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def argb(rgb: tuple[int, int, int], a: float = 1.0) -> int:
    a8 = max(0, min(255, int(round(a * 255))))
    return (a8 << 24) | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]


def mix_rgb(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


class Palette:
    """Named colours resolved from a theme dict; accepts '#hex' or a theme key."""

    def __init__(self, theme: dict):
        self.theme = theme

    def rgb(self, c: str | Sequence[int]) -> tuple[int, int, int]:
        if isinstance(c, (list, tuple)):
            return (int(c[0]), int(c[1]), int(c[2]))
        c = str(c)
        if c.startswith("#"):
            return hex_rgb(c)
        v = self.theme.get(c)
        if v is None:
            raise KeyError(f"unknown colour {c!r}")
        return self.rgb(v)

    def __call__(self, c: str | Sequence[int], a: float = 1.0) -> int:
        return argb(self.rgb(c), a)

    def on(self, ground: str) -> str:
        """Readable ink for a ground colour key."""
        r, g, b = self.rgb(ground)
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return self.theme.get("ink_dark", "#111111") if lum > 150 else self.theme.get("ink", "#ffffff")


def paint(color: int, stroke: float = 0.0, blur: float = 0.0, shader: skia.Shader | None = None,
          cap_round: bool = True) -> skia.Paint:
    p = skia.Paint(AntiAlias=True, Color=color)
    if stroke > 0:
        p.setStyle(skia.Paint.kStroke_Style)
        p.setStrokeWidth(stroke)
        if cap_round:
            p.setStrokeCap(skia.Paint.kRound_Cap)
            p.setStrokeJoin(skia.Paint.kRound_Join)
    if blur > 0:
        p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
    if shader is not None:
        p.setShader(shader)
    return p


def linear_grad(x0: float, y0: float, x1: float, y1: float, colors: list[int],
                pos: list[float] | None = None) -> skia.Shader:
    return skia.GradientShader.MakeLinear([skia.Point(x0, y0), skia.Point(x1, y1)], colors, pos)


def radial_grad(cx: float, cy: float, r: float, colors: list[int], pos: list[float] | None = None) -> skia.Shader:
    return skia.GradientShader.MakeRadial(skia.Point(cx, cy), max(r, 1e-3), colors, pos)


# --------------------------------------------------------------------- text
#
# Every measure and draw goes through one model: glyph advances + GPOS pair kerning (shaped and
# cached per typeface instance in fonts.py) + tracking in em between glyphs. Measuring, wrapping,
# per-glyph layout and drawing therefore always agree, with or without tracking.

def cap_height(font: skia.Font) -> float:
    m = font.getMetrics()
    return m.fCapHeight if m.fCapHeight > 0 else font.getSize() * 0.7


def _fkey(font: skia.Font) -> tuple:
    return (font.getTypeface().uniqueID(), font.getSize(), font.getScaleX(), font.getSkewX())


_RUNS: dict[tuple, tuple] = {}


def _run(font: skia.Font, s: str, tracking: float = 0.0, kern: bool = True) -> tuple:
    """(glyph ids, x offsets, advances, width) for one line; width excludes trailing tracking."""
    key = (_fkey(font), s, float(tracking), bool(kern))
    r = _RUNS.get(key)
    if r is None:
        glyphs = list(font.textToGlyphs(s)) if s else []
        if len(glyphs) != len(s):  # surrogate/odd input: fall back to Skia's own glyph mapping
            kern = False
        adv = list(font.getWidths(glyphs)) if glyphs else []
        size = font.getSize()
        kv = F.kerning(font.getTypeface(), s) if kern and len(glyphs) == len(s) else [0.0] * max(0, len(glyphs) - 1)
        tr = tracking * size
        xs, x = [], 0.0
        for i, a in enumerate(adv):
            xs.append(x)
            x += a + tr + (kv[i] * size if i < len(kv) else 0.0)
        width = (xs[-1] + adv[-1]) if adv else 0.0
        r = (glyphs, xs, adv, width)
        if len(_RUNS) > 20000:
            _RUNS.clear()
        _RUNS[key] = r
    return r


def text_w(font: skia.Font, s: str, tracking: float = 0.0, kern: bool = True) -> float:
    """Advance width of one line (kerned; tracking in em is added between glyphs)."""
    if not s:
        return 0.0
    return _run(font, s, tracking, kern)[3]


def glyph_layout(font: skia.Font, s: str, tracking: float = 0.0, kern: bool = True) -> list[tuple[str, float, float]]:
    """Per-character (char, x offset, advance) from the kerned run, for per-letter animation."""
    _g, xs, adv, _w = _run(font, s, tracking, kern)
    if len(xs) != len(s):
        out, x = [], 0.0
        for ch in s:
            w = font.measureText(ch)
            out.append((ch, x, w))
            x += w + tracking * font.getSize()
        return out
    return [(ch, xs[i], adv[i]) for i, ch in enumerate(s)]


def glyph_rects(font: skia.Font, s: str, x: float, y: float, tracking: float = 0.0,
                kern: bool = True) -> list[tuple[str, skia.Rect]]:
    """Per-character ink bounds at baseline (x, y), from the same kerned run as draw_text."""
    glyphs, xs, _adv, _w = _run(font, s, tracking, kern)
    bounds = font.getBounds(glyphs) if glyphs else []
    out = []
    for i, ch in enumerate(s[:len(glyphs)]):
        b = bounds[i]
        out.append((ch, skia.Rect(x + xs[i] + b.left(), y + b.top(), x + xs[i] + b.right(), y + b.bottom())))
    return out


def fit_font(family: str, text: str, max_w: float, max_size: float, weight: int = 800,
             width: float = 100.0, tracking: float = 0.0, min_size: float = 8.0, **kw) -> skia.Font:
    size = max_size
    f = F.font(family, size, weight, width, **kw)
    w = text_w(f, text, tracking)
    if w > max_w and w > 0:
        size = max(min_size, size * max_w / w)
        f = F.font(family, size, weight, width, **kw)
    return f


_WRAPS: dict[tuple, list[str]] = {}


def _wrap_para(font: skia.Font, words: list[str], max_w: float, tracking: float, style: str,
               max_rag: float) -> list[str]:
    m = len(words)
    if m <= 1:
        return [" ".join(words)]
    widths: dict[tuple[int, int], float] = {}

    def W(i: int, j: int) -> float:
        k = (i, j)
        if k not in widths:
            widths[k] = text_w(font, " ".join(words[i:j]), tracking)
        return widths[k]

    greedy, start = [], 0
    for j in range(1, m):
        if W(start, j + 1) > max_w:
            greedy.append((start, j))
            start = j
    greedy.append((start, m))
    n = len(greedy)
    if n == 1 or style == "greedy":
        return [" ".join(words[i:j]) for i, j in greedy]

    def cost(i: int, j: int, last: bool) -> float:
        w = W(i, j)
        if w > max_w * 1.0001 and j - i > 1:
            return math.inf
        slack = max(0.0, max_w - w) / max_w
        c = slack * slack * (0.15 if (last and style != "balance") else 1.0)
        if not last and slack > max_rag:
            c += 4.0 * (slack - max_rag)              # max rag: no short line mid-paragraph
        if last and j - i == 1 and m > n:
            c += 2.0                                   # widow: lone word on the last line
        if not last and j - i > 1 and len(words[j - 1].strip(".,;:!?\"'")) <= 2:
            c += 0.02                                  # avoid a dangling short word at a line end
        if i == 0 and j == 1 and m > n:
            c += 1.0                                   # orphan: lone word on the first line
        return c

    INF = math.inf
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    bk = [[0] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for k in range(1, n + 1):
        last = k == n
        for j in range(k, m + 1):
            if last and j != m:
                continue
            best, arg = INF, -1
            for i in range(k - 1, j):
                if dp[k - 1][i] == INF:
                    continue
                v = dp[k - 1][i] + cost(i, j, last)
                if v < best:
                    best, arg = v, i
            dp[k][j], bk[k][j] = best, arg
    if dp[n][m] == INF:
        return [" ".join(words[i:j]) for i, j in greedy]
    cuts, j = [], m
    for k in range(n, 0, -1):
        i = bk[k][j]
        cuts.append((i, j))
        j = i
    return [" ".join(words[i:j]) for i, j in reversed(cuts)]


def wrap(font: skia.Font, text: str, max_w: float, tracking: float = 0.0, style: str = "pretty",
         max_rag: float = 0.4) -> list[str]:
    """Break text into lines no wider than max_w (explicit '\\n' always breaks).

    Uses the same number of lines as a greedy fill, then chooses the breaks:
    - "pretty" (default): ragged-right with minimum raggedness, no line shorter than
      (1 - max_rag) of the measure except the last, no widow or orphan word where avoidable;
    - "balance": equal-length lines (headlines, captions);
    - "greedy": first-fit, as in v0.1."""
    key = (_fkey(font), text, round(float(max_w), 2), float(tracking), style, float(max_rag))
    out = _WRAPS.get(key)
    if out is None:
        out = []
        for para in text.split("\n"):
            out.extend(_wrap_para(font, para.split(), max_w, tracking, style, max_rag))
        if len(_WRAPS) > 4000:
            _WRAPS.clear()
        _WRAPS[key] = out
    return list(out)


def _blob(font: skia.Font, s: str, tracking: float, kern: bool) -> skia.TextBlob | None:
    glyphs, xs, _adv, _w = _run(font, s, tracking, kern)
    if not glyphs:
        return None
    b = skia.TextBlobBuilder()
    b.allocRunPosH(font, glyphs, xs, 0.0)
    return b.make()


def draw_text(c: skia.Canvas, s: str, x: float, y: float, font: skia.Font, color: int,
              align: str = "center", tracking: float = 0.0, stroke: float = 0.0,
              valign: str = "baseline", blur: float = 0.0, kern: bool = True) -> float:
    """Draw a single kerned line. y is the baseline (or the cap-centre with valign='middle', the
    cap top with valign='top'). Returns the drawn width."""
    w = text_w(font, s, tracking, kern)
    if align == "center":
        x -= w / 2
    elif align == "right":
        x -= w
    if valign == "middle":
        y += cap_height(font) / 2
    elif valign == "top":
        y += cap_height(font)
    blob = _blob(font, s, tracking, kern)
    if blob is not None:
        c.drawTextBlob(blob, x, y, paint(color, stroke=stroke, blur=blur))
    return w


def text_path(s: str, x: float, y: float, font: skia.Font, tracking: float = 0.0,
              kern: bool = True) -> skia.Path:
    """Outline of one kerned line with its baseline at (x, y)."""
    glyphs, xs, _adv, _w = _run(font, s, tracking, kern)
    path = skia.Path()
    for g, gx in zip(glyphs, xs):
        gp = font.getPath(g)
        if gp is not None:
            gp.offset(x + gx, y)
            path.addPath(gp)
    return path


# --------------------------------------------------------------------- line boxes (layout)

@dataclass(frozen=True)
class LineBox:
    """One set line. x is the left edge of the advance box, baseline the baseline y."""
    text: str
    x: float
    baseline: float
    w: float
    cap: float
    ascent: float
    descent: float

    @property
    def top(self) -> float:
        """Cap top: the optical top of the line, used for alignment to the grid."""
        return self.baseline - self.cap

    @property
    def bottom(self) -> float:
        return self.baseline + self.descent

    @property
    def rect(self) -> skia.Rect:
        return skia.Rect(self.x, self.baseline - self.ascent, self.x + self.w, self.baseline + self.descent)


@dataclass
class TextBlock:
    """Lines set with one font, leading and tracking. top = first cap top, h = cap top of the
    first line to the baseline of the last (the visual block height used for layout)."""
    lines: list[LineBox]
    font: skia.Font
    leading: float      # line pitch in px
    tracking: float
    align: str

    @property
    def top(self) -> float:
        return self.lines[0].top if self.lines else 0.0

    @property
    def baseline(self) -> float:
        """Baseline of the last line."""
        return self.lines[-1].baseline if self.lines else 0.0

    @property
    def bottom(self) -> float:
        return self.lines[-1].bottom if self.lines else 0.0

    @property
    def h(self) -> float:
        return self.baseline - self.top

    @property
    def w(self) -> float:
        return max((ln.w for ln in self.lines), default=0.0)

    @property
    def x(self) -> float:
        return min((ln.x for ln in self.lines), default=0.0)

    @property
    def rect(self) -> skia.Rect:
        return skia.Rect(self.x, self.top, self.x + self.w, self.baseline)

    def draw(self, c: skia.Canvas, color: int, **kw) -> None:
        for ln in self.lines:
            draw_text(c, ln.text, ln.x, ln.baseline, self.font, color, align="left", tracking=self.tracking, **kw)


def block_height(font: skia.Font, n_lines: int, leading: float = 1.2) -> float:
    """Visual height (first cap top to last baseline) of n lines at a leading multiple."""
    return cap_height(font) + max(0, n_lines - 1) * leading * font.getSize()


def set_lines(font: skia.Font, lines: Sequence[str], x: float, top: float, leading: float = 1.2,
              tracking: float = 0.0, align: str = "left") -> TextBlock:
    """Line boxes for already-broken lines. x is the left edge / centre / right edge per align;
    top is the cap top of the first line; leading is a multiple of the font size."""
    m = font.getMetrics()
    cap, asc, desc = cap_height(font), -m.fAscent, m.fDescent
    pitch = leading * font.getSize()
    out = []
    for i, s in enumerate(lines):
        w = text_w(font, s, tracking)
        lx = x - w / 2 if align == "center" else x - w if align == "right" else x
        out.append(LineBox(s, lx, top + cap + i * pitch, w, cap, asc, desc))
    return TextBlock(out, font, pitch, tracking, align)


def layout_text(font: skia.Font, text: str, x: float, top: float, max_w: float, leading: float = 1.2,
                tracking: float = 0.0, align: str = "left", style: str = "pretty",
                max_lines: int | None = None) -> TextBlock:
    """Wrap and set a paragraph; returns line boxes (see set_lines)."""
    lines = wrap(font, text, max_w, tracking, style=style)
    if max_lines is not None:
        lines = lines[:max_lines]
    return set_lines(font, lines, x, top, leading, tracking, align)


# --------------------------------------------------------------------- shapes & paths

def rrect(c: skia.Canvas, x: float, y: float, w: float, h: float, r: float, p: skia.Paint) -> None:
    c.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, h), r, r), p)


def partial_path(path: skia.Path, t: float) -> skia.Path:
    """The first t (0..1) of a path's total length, across all contours."""
    t = max(0.0, min(1.0, t))
    out = skia.Path()
    if t <= 0:
        return out
    pm = skia.PathMeasure(path, False)
    lengths = []
    while True:
        lengths.append(pm.getLength())
        if not pm.nextContour():
            break
    remaining = sum(lengths) * t
    pm = skia.PathMeasure(path, False)
    for L in lengths:
        if remaining <= 0:
            break
        pm.getSegment(0, min(L, remaining), out, True)
        remaining -= L
        if not pm.nextContour():
            break
    return out


def polyline(points: Iterable[tuple[float, float]]) -> skia.Path:
    p = skia.Path()
    for i, (x, y) in enumerate(points):
        if i == 0:
            p.moveTo(x, y)
        else:
            p.lineTo(x, y)
    return p


def arrow_head(c: skia.Canvas, x: float, y: float, ang: float, size: float, color: int) -> None:
    p = skia.Path()
    p.moveTo(x, y)
    p.lineTo(x - size * math.cos(ang - 0.45), y - size * math.sin(ang - 0.45))
    p.lineTo(x - size * math.cos(ang + 0.45), y - size * math.sin(ang + 0.45))
    p.close()
    c.drawPath(p, paint(color))


# --------------------------------------------------------------------- images

@lru_cache(maxsize=64)
def load_image(path: str) -> skia.Image:
    import cv2
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    arr = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if arr is None:
        raise ValueError(f"cannot decode image {path}")
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGBA)
    elif arr.shape[2] == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGBA)
    else:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGBA)
    return skia.Image.fromarray(np.ascontiguousarray(arr), colorType=skia.ColorType.kRGBA_8888_ColorType)


SAMPLING = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear)


def draw_cover(c: skia.Canvas, img: skia.Image, rect: skia.Rect, zoom: float = 1.0,
               pan: tuple[float, float] = (0.0, 0.0), alpha: float = 1.0) -> None:
    """Draw img covering rect (object-fit: cover) with zoom and normalised pan."""
    iw, ih = img.width(), img.height()
    s = max(rect.width() / iw, rect.height() / ih) * zoom
    dw, dh = iw * s, ih * s
    cx = rect.centerX() + pan[0] * (dw - rect.width()) / 2
    cy = rect.centerY() + pan[1] * (dh - rect.height()) / 2
    c.save()
    c.clipRect(rect, skia.ClipOp.kIntersect, True)
    p = skia.Paint(AntiAlias=True)
    p.setAlphaf(alpha)
    c.drawImageRect(img, skia.Rect.MakeXYWH(cx - dw / 2, cy - dh / 2, dw, dh), SAMPLING, p)
    c.restore()


# --------------------------------------------------------------------- 3D

def m44(mat: np.ndarray) -> skia.M44:
    m = skia.M44()
    for r in range(4):
        for col in range(4):
            m.setRC(r, col, float(mat[r, col]))
    return m


def persp3d(cx: float, cy: float, rx: float = 0.0, ry: float = 0.0, rz: float = 0.0,
            dist: float = 1600.0, tz: float = 0.0, scale: float = 1.0, oz: float = 0.0) -> skia.M44:
    """Matrix that rotates content about (cx, cy) in 3D and projects it with a camera at
    `dist` px. Angles in radians. tz pushes content away (+) or toward (-) the camera."""
    def rot_x(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]], float)

    def rot_y(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]], float)

    def rot_z(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float)

    T1 = np.eye(4); T1[0, 3], T1[1, 3] = -cx, -cy
    O = np.eye(4); O[2, 3] = oz  # object-space depth (extrusion layers)
    S = np.diag([scale, scale, scale, 1.0])
    Z = np.eye(4); Z[2, 3] = tz
    P = np.eye(4); P[3, 2] = 1.0 / dist  # w = 1 + z/d
    T2 = np.eye(4); T2[0, 3], T2[1, 3] = cx, cy
    M = T2 @ P @ Z @ rot_z(rz) @ rot_x(rx) @ rot_y(ry) @ O @ S @ T1
    return m44(M)


def layer_z(dz: float) -> skia.M44:
    T = np.eye(4); T[2, 3] = dz
    return m44(T)


# --------------------------------------------------------------------- icons (original vector set)

def icon(c: skia.Canvas, name: str, cx: float, cy: float, size: float, color: int, sw: float | None = None) -> None:
    s = size / 2
    sw = sw or size * 0.09
    st = paint(color, stroke=sw)
    fl = paint(color)
    p = skia.Path()
    if name == "bolt":
        pts = [(0.15, -1), (-0.55, 0.12), (-0.02, 0.12), (-0.18, 1), (0.55, -0.15), (0.02, -0.15)]
        p = polyline([(cx + x * s, cy + y * s) for x, y in pts]); p.close(); c.drawPath(p, fl)
    elif name == "check":
        c.drawPath(polyline([(cx - 0.6 * s, cy), (cx - 0.15 * s, cy + 0.45 * s), (cx + 0.65 * s, cy - 0.5 * s)]), st)
    elif name == "lock":
        rrect(c, cx - 0.6 * s, cy - 0.1 * s, 1.2 * s, 0.95 * s, 0.15 * s, fl)
        c.drawArc(skia.Rect(cx - 0.38 * s, cy - 0.75 * s, cx + 0.38 * s, cy + 0.05 * s), 180, 180, False, st)
        c.drawLine(cx - 0.38 * s, cy - 0.35 * s, cx - 0.38 * s, cy - 0.1 * s, st)
        c.drawLine(cx + 0.38 * s, cy - 0.35 * s, cx + 0.38 * s, cy - 0.1 * s, st)
    elif name == "chart":
        for i, h in enumerate((0.5, 0.95, 0.7, 1.35)):
            x = cx - 0.75 * s + i * 0.45 * s
            rrect(c, x, cy + 0.7 * s - h * s, 0.3 * s, h * s, 0.06 * s, fl)
    elif name == "star":
        pts = []
        for i in range(10):
            r = s if i % 2 == 0 else s * 0.42
            a = -math.pi / 2 + i * math.pi / 5
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        p = polyline(pts); p.close(); c.drawPath(p, fl)
    elif name == "shield":
        p.moveTo(cx, cy - s); p.lineTo(cx + 0.8 * s, cy - 0.65 * s); p.lineTo(cx + 0.7 * s, cy + 0.2 * s)
        p.quadTo(cx + 0.45 * s, cy + 0.75 * s, cx, cy + s); p.quadTo(cx - 0.45 * s, cy + 0.75 * s, cx - 0.7 * s, cy + 0.2 * s)
        p.lineTo(cx - 0.8 * s, cy - 0.65 * s); p.close(); c.drawPath(p, st)
    elif name == "clock":
        c.drawCircle(cx, cy, 0.85 * s, st)
        c.drawPath(polyline([(cx, cy - 0.5 * s), (cx, cy), (cx + 0.4 * s, cy + 0.25 * s)]), st)
    elif name == "heart":
        p.moveTo(cx, cy + 0.85 * s)
        p.cubicTo(cx - 1.3 * s, cy - 0.1 * s, cx - 0.6 * s, cy - 1.1 * s, cx, cy - 0.4 * s)
        p.cubicTo(cx + 0.6 * s, cy - 1.1 * s, cx + 1.3 * s, cy - 0.1 * s, cx, cy + 0.85 * s)
        c.drawPath(p, fl)
    elif name == "spark":
        for k in range(4):
            a = k * math.pi / 2
            p2 = skia.Path(); p2.moveTo(cx, cy)
            p2.quadTo(cx + 0.18 * s * math.cos(a + 0.8), cy + 0.18 * s * math.sin(a + 0.8), cx + s * math.cos(a), cy + s * math.sin(a))
            p2.quadTo(cx + 0.18 * s * math.cos(a - 0.8), cy + 0.18 * s * math.sin(a - 0.8), cx, cy)
            c.drawPath(p2, fl)
    elif name == "globe":
        c.drawCircle(cx, cy, 0.85 * s, st)
        c.drawOval(skia.Rect(cx - 0.38 * s, cy - 0.85 * s, cx + 0.38 * s, cy + 0.85 * s), st)
        c.drawLine(cx - 0.85 * s, cy, cx + 0.85 * s, cy, st)
    elif name == "bell":
        p.moveTo(cx - 0.7 * s, cy + 0.45 * s); p.quadTo(cx - 0.55 * s, cy + 0.2 * s, cx - 0.55 * s, cy - 0.15 * s)
        p.quadTo(cx - 0.5 * s, cy - 0.8 * s, cx, cy - 0.8 * s); p.quadTo(cx + 0.5 * s, cy - 0.8 * s, cx + 0.55 * s, cy - 0.15 * s)
        p.quadTo(cx + 0.55 * s, cy + 0.2 * s, cx + 0.7 * s, cy + 0.45 * s); p.close(); c.drawPath(p, fl)
        c.drawCircle(cx, cy + 0.68 * s, 0.17 * s, fl)
    elif name == "user":
        c.drawCircle(cx, cy - 0.35 * s, 0.38 * s, fl)
        c.drawArc(skia.Rect(cx - 0.75 * s, cy + 0.1 * s, cx + 0.75 * s, cy + 1.3 * s), 180, 180, True, fl)
    elif name == "flask":
        p.moveTo(cx - 0.25 * s, cy - 0.9 * s); p.lineTo(cx - 0.25 * s, cy - 0.25 * s); p.lineTo(cx - 0.75 * s, cy + 0.75 * s)
        p.lineTo(cx + 0.75 * s, cy + 0.75 * s); p.lineTo(cx + 0.25 * s, cy - 0.25 * s); p.lineTo(cx + 0.25 * s, cy - 0.9 * s)
        c.drawPath(p, st); c.drawLine(cx - 0.42 * s, cy - 0.9 * s, cx + 0.42 * s, cy - 0.9 * s, st)
        q = skia.Path(); q.moveTo(cx - 0.55 * s, cy + 0.35 * s); q.lineTo(cx + 0.55 * s, cy + 0.35 * s); q.lineTo(cx + 0.75 * s, cy + 0.75 * s); q.lineTo(cx - 0.75 * s, cy + 0.75 * s); q.close(); c.drawPath(q, fl)
    elif name == "book":
        p.moveTo(cx, cy - 0.55 * s); p.quadTo(cx - 0.45 * s, cy - 0.85 * s, cx - 0.9 * s, cy - 0.65 * s); p.lineTo(cx - 0.9 * s, cy + 0.7 * s)
        p.quadTo(cx - 0.45 * s, cy + 0.5 * s, cx, cy + 0.8 * s); p.quadTo(cx + 0.45 * s, cy + 0.5 * s, cx + 0.9 * s, cy + 0.7 * s)
        p.lineTo(cx + 0.9 * s, cy - 0.65 * s); p.quadTo(cx + 0.45 * s, cy - 0.85 * s, cx, cy - 0.55 * s); p.lineTo(cx, cy + 0.8 * s)
        c.drawPath(p, st)
    elif name == "arrow":
        c.drawLine(cx - 0.7 * s, cy, cx + 0.6 * s, cy, st)
        c.drawPath(polyline([(cx + 0.15 * s, cy - 0.5 * s), (cx + 0.7 * s, cy), (cx + 0.15 * s, cy + 0.5 * s)]), st)
    else:  # dot fallback
        c.drawCircle(cx, cy, 0.5 * s, fl)


ICONS = ("bolt", "check", "lock", "chart", "star", "shield", "clock", "heart", "spark", "globe",
         "bell", "user", "flask", "book", "arrow")
