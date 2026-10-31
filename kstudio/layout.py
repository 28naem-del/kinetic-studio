"""Layout and type system: a 12-column grid with safe zones and a modular type scale.

All reference values are px on the 1080 x 1920 reel (short edge 1080). A Grid scales them to
any resolution (unit u = short edge / 1080) and swaps in a profile for 16:9 and 1:1 frames.

    g = layout.grid(ctx)                 # or Grid(W, H)
    x, w = g.span(0, 9)                  # columns 1-9 (0-based start, count)
    role = layout.TYPE["headline"]       # 168 px, leading 0.98, optical tracking
    f = role.font(ctx)                   # theme face for the role, sized for this frame
    blk = layout.text(ctx, "headline", "How does a CPU cache work?", g.x0, g.anchor_y, w)
    g.coverage(blk.top, g.safe_bottom) >= 0.6
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

import skia

from . import draw as D
from . import fonts as F

# ---------------------------------------------------------------------- grid

REF = 1080.0        # reference short edge
BASELINE = 8.0      # baseline unit (reference px)


@dataclass(frozen=True)
class Profile:
    """Reference px at a 1080 px short edge; vertical zones are fractions of the frame height
    so they hold for any frame of the same aspect."""
    margin: float
    gutter: float
    cols: int
    safe_top: float      # fraction of H kept clear at the top (platform UI)
    safe_bottom: float   # fraction of H kept clear at the bottom (captions, buttons)
    anchor: float        # fraction of H: shared top anchor for text-led scenes
    caption: float       # fraction of H: centre line of burned-in captions
    type_k: float        # type scale multiplier for this aspect
    head_cols: int = 12  # columns a headline may span
    text_cols: int = 11  # columns for running text (keeps the measure at ~25-45 characters)


PROFILES: dict[str, Profile] = {
    # 1080 x 1920: margins 96, safe top 220 / bottom 380, anchor y=560, captions at y=1240
    "portrait": Profile(96.0, 24.0, 12, 220 / 1920, 380 / 1920, 560 / 1920, 1240 / 1920, 1.0),
    # 1920 x 1080: margins 120, title-safe 96 top / 120 bottom, anchor y=312, captions y=864
    "landscape": Profile(120.0, 24.0, 12, 96 / 1080, 120 / 1080, 312 / 1080, 864 / 1080, 0.9, 9, 7),
    # 1080 x 1080: margins 96, top 120 / bottom 180, anchor y=304, captions y=820
    "square": Profile(96.0, 24.0, 12, 120 / 1080, 180 / 1080, 304 / 1080, 820 / 1080, 0.9),
}


def aspect_class(W: float, H: float) -> str:
    if H > W * 1.15:
        return "portrait"
    if W > H * 1.15:
        return "landscape"
    return "square"


@dataclass(frozen=True)
class Grid:
    W: int
    H: int
    profile: str = ""

    def __post_init__(self) -> None:
        if not self.profile:
            object.__setattr__(self, "profile", aspect_class(self.W, self.H))

    # -- scale ------------------------------------------------------------
    @property
    def P(self) -> Profile:
        return PROFILES[self.profile]

    @property
    def u(self) -> float:
        """Reference px -> frame px."""
        return min(self.W, self.H) / REF

    def px(self, ref_px: float) -> float:
        return ref_px * self.u

    def bl(self, n: float = 1.0) -> float:
        """n baseline units (8 reference px each)."""
        return n * BASELINE * self.u

    # -- columns ----------------------------------------------------------
    @property
    def margin(self) -> float:
        return self.P.margin * self.u

    @property
    def gutter(self) -> float:
        return self.P.gutter * self.u

    @property
    def cols(self) -> int:
        return self.P.cols

    @property
    def x0(self) -> float:
        return self.margin

    @property
    def x1(self) -> float:
        return self.W - self.margin

    @property
    def content_w(self) -> float:
        return self.x1 - self.x0

    @property
    def col_w(self) -> float:
        return (self.content_w - (self.cols - 1) * self.gutter) / self.cols

    @property
    def cx(self) -> float:
        return self.W / 2

    def col(self, i: int) -> float:
        """Left edge of column i (0-based; negative counts from the right)."""
        i = i % self.cols if i < 0 else i
        return self.x0 + i * (self.col_w + self.gutter)

    def col_end(self, i: int) -> float:
        return self.col(i) + self.col_w

    def span(self, i: int, n: int) -> tuple[float, float]:
        """(x, width) of n columns starting at column i, gutters included."""
        n = max(1, min(n, self.cols - (i % self.cols)))
        return self.col(i), n * self.col_w + (n - 1) * self.gutter

    def span_w(self, n: int) -> float:
        return self.span(0, n)[1]

    @property
    def head_w(self) -> float:
        """Measure for headlines (12 columns on 9:16 and 1:1, 9 on 16:9)."""
        return self.span_w(self.P.head_cols)

    @property
    def measure(self) -> float:
        """Measure for running text (11 columns on 9:16 and 1:1, 7 on 16:9)."""
        return self.span_w(self.P.text_cols)

    # -- vertical zones ---------------------------------------------------
    @property
    def safe_top(self) -> float:
        return self.P.safe_top * self.H

    @property
    def safe_bottom(self) -> float:
        """y of the bottom safe line."""
        return self.H - self.P.safe_bottom * self.H

    @property
    def safe_h(self) -> float:
        return self.safe_bottom - self.safe_top

    @property
    def safe(self) -> skia.Rect:
        return skia.Rect(self.x0, self.safe_top, self.x1, self.safe_bottom)

    @property
    def anchor_y(self) -> float:
        """Shared top anchor (cap top of the first line) for text-led scenes."""
        return self.P.anchor * self.H

    @property
    def caption_y(self) -> float:
        return self.P.caption * self.H

    @property
    def chrome_y(self) -> float:
        """Baseline for corner chrome (handle, index) inside the top margin box."""
        return self.margin + self.bl(4)

    def snap(self, y: float) -> float:
        """Snap y to the baseline grid."""
        b = self.bl(1)
        return round(y / b) * b

    def row(self, j: float, n: int = 8) -> float:
        """Top of band j of n equal bands inside the safe zone."""
        return self.safe_top + self.safe_h * j / n

    # -- block placement --------------------------------------------------
    def place(self, h: float, anchor: str = "top") -> float:
        """Top y for a block of visual height h.
        top: at the shared anchor (moved up if it would cross the bottom safe line);
        center: optical centre of the safe zone (centre sits at 46 %, slightly high);
        bottom: block ends on the bottom safe line."""
        if anchor == "bottom":
            return self.safe_bottom - h
        if anchor == "center":
            y = self.safe_top + self.safe_h * 0.46 - h / 2
        else:
            y = self.anchor_y
        return max(self.safe_top, min(y, self.safe_bottom - h))

    def coverage(self, top: float, bottom: float) -> float:
        """Fraction of the frame height spanned by a content block."""
        return max(0.0, min(bottom, self.H) - max(top, 0.0)) / self.H

    def fills(self, top: float, bottom: float, min_frac: float = 0.6) -> bool:
        """The S6 rule: the content block spans >= min_frac of the frame height."""
        return self.coverage(top, bottom) >= min_frac - 1e-9

    def inside(self, r: skia.Rect) -> bool:
        """True if rect r lies inside the safe area."""
        s = self.safe
        eps = 0.5
        return r.left() >= s.left() - eps and r.right() <= s.right() + eps and \
            r.top() >= s.top() - eps and r.bottom() <= s.bottom() + eps


@lru_cache(maxsize=32)
def _grid(W: int, H: int) -> Grid:
    return Grid(W, H)


def grid(ctx_or_w: Any, H: int | None = None) -> Grid:
    """Grid for a Ctx (anything with W/H) or for explicit W, H."""
    if H is None:
        return _grid(int(ctx_or_w.W), int(ctx_or_w.H))
    return _grid(int(ctx_or_w), int(H))


# ---------------------------------------------------------------------- type scale

@dataclass(frozen=True)
class Role:
    """A typographic role. size in reference px; leading is a multiple of the size; tracking in em
    (None = optical default for the size, see fonts.track); face is a theme font key."""
    name: str
    size: float
    leading: float
    weight: int
    face: str
    tracking: float | None = None
    caps: bool = False
    width: float = 100.0

    @property
    def track(self) -> float:
        """Tracking (em), face-independent."""
        return F.track(self.size, self.caps) if self.tracking is None else self.tracking

    def tk(self, ctx: Any = None, face: str | None = None) -> float:
        """Tracking (em) for this role in ctx's theme face (optical-size faces track looser)."""
        if self.tracking is not None:
            return self.tracking
        fam = None
        if ctx is not None:
            fam = ctx.theme.get(face, face) if face else self.family(ctx.theme)
        return F.track(self.size, self.caps, fam)

    def px(self, g: Grid | Any) -> float:
        g = g if isinstance(g, Grid) else grid(g)
        return self.size * g.u * g.P.type_k

    def pitch(self, g: Grid | Any) -> float:
        """Line pitch in frame px."""
        return self.px(g) * self.leading

    def family(self, theme: dict) -> str:
        return theme.get(self.face, self.face)

    def font(self, ctx: Any, weight: int | None = None, width: float | None = None,
             size: float | None = None, face: str | None = None) -> skia.Font:
        """Font for this role in ctx's theme. size overrides the frame px size."""
        fam = ctx.theme.get(face, face) if face else self.family(ctx.theme)
        px = self.px(ctx) if size is None else size
        kw = {}
        oz = F.opsz_for(fam, self.size)
        if oz is not None:
            kw["opsz"] = oz
        return F.font(fam, px, weight if weight is not None else self.weight,
                      width if width is not None else self.width, **kw)

    def text(self, s: str) -> str:
        return s.upper() if self.caps else s

    def with_(self, **kw) -> Role:
        return replace(self, **kw)


class TypeScale:
    """Modular scale: step k -> round(base * ratio**k). Default 1.333 from 40 px:
    40 / 53 / 71 / 95 / 126 / 168 / 224 (steps 0..6)."""

    def __init__(self, base: float = 40.0, ratio: float = 1.333, roles: dict[str, Role] | None = None):
        self.base, self.ratio = base, ratio
        self.roles: dict[str, Role] = roles if roles is not None else self._default_roles()

    def size(self, step: float) -> float:
        return float(round(self.base * self.ratio ** step))

    @property
    def sizes(self) -> list[float]:
        return [self.size(k) for k in range(7)]

    def _default_roles(self) -> dict[str, Role]:
        S = self.size
        return {
            "display": Role("display", S(6), 0.92, 800, "display"),
            "headline": Role("headline", S(5), 0.98, 700, "display"),
            "title": Role("title", S(4), 1.04, 650, "display"),
            "subhead": Role("subhead", S(2), 1.16, 500, "body"),
            "body": Role("body", S(1), 1.36, 500, "body"),
            "caption": Role("caption", S(0), 1.32, 500, "body"),
            "kicker": Role("kicker", 32.0, 1.2, 700, "body", caps=True),   # S6: kicker 32 px, +12 %
            "meta": Role("meta", 30.0, 1.25, 500, "mono", tracking=0.06),
        }

    def __getitem__(self, name: str) -> Role:
        return self.roles[name]

    def __contains__(self, name: object) -> bool:
        return name in self.roles

    def role(self, name_or_size: str | float) -> Role:
        """A named role, or the role whose size is nearest to a px size (for spec overrides)."""
        if isinstance(name_or_size, str):
            return self.roles[name_or_size]
        px = float(name_or_size)
        best = min(self.roles.values(), key=lambda r: abs(r.size - px))
        return best.with_(size=px)


TYPE = TypeScale()


def font(ctx: Any, role: str, **kw) -> skia.Font:
    return TYPE[role].font(ctx, **kw)


def text(ctx: Any, role: str | Role, s: str, x: float, top: float, max_w: float, align: str = "left",
         style: str = "pretty", font: skia.Font | None = None, max_lines: int | None = None) -> D.TextBlock:
    """Wrap and set s in a role at (x, top=cap top); returns line boxes."""
    r = TYPE[role] if isinstance(role, str) else role
    f = font or r.font(ctx)
    return D.layout_text(f, r.text(s), x, top, max_w, r.leading, r.tk(ctx), align, style, max_lines)


def fit_role(ctx: Any, role: str | Role, s: str, max_w: float, **kw) -> skia.Font:
    """Role font, shrunk only if the longest word would not fit max_w."""
    r = TYPE[role] if isinstance(role, str) else role
    f = r.font(ctx, **kw)
    tr = r.tk(ctx, kw.get("face"))
    longest = max(r.text(s).split() or [""], key=lambda w: D.text_w(f, w, tr))
    w = D.text_w(f, longest, tr)
    if w > max_w > 0:
        f = r.font(ctx, size=f.getSize() * max_w / w, **kw)
    return f
