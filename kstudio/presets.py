"""Output presets and quality levels."""
from __future__ import annotations

from dataclasses import dataclass

PRESETS: dict[str, tuple[int, int]] = {
    "reel": (1080, 1920),       # 9:16 Reels / TikTok / Shorts
    "story": (1080, 1920),
    "landscape": (1920, 1080),  # 16:9 1080p
    "youtube": (1920, 1080),
    "square": (1080, 1080),     # 1:1
    "portrait": (1080, 1350),   # 4:5 feed
}

# quality -> (motion-blur subsamples, shutter angle in degrees, x264 crf, x264 preset)
QUALITY: dict[str, tuple[int, float, int, str]] = {
    "draft": (1, 0.0, 26, "veryfast"),
    "standard": (3, 180.0, 19, "medium"),
    "high": (5, 216.0, 16, "slow"),
}


@dataclass
class Output:
    width: int
    height: int
    fps: int
    samples: int
    shutter: float
    crf: int
    x264_preset: str
    preset: str
    quality: str

    @property
    def aspect(self) -> str:
        from math import gcd
        g = gcd(self.width, self.height)
        return f"{self.width // g}:{self.height // g}"

    @property
    def maxrate_kbps(self) -> int:
        """VBV cap: ~16 Mbps at 1080x1920@30, scaled by pixel rate (keeps uploads platform-friendly)."""
        return int(16000 * (self.width * self.height * self.fps) / (1080 * 1920 * 30))

    @property
    def level(self) -> str:
        px = self.width * self.height * self.fps
        if px <= 1920 * 1080 * 30:
            return "4.0"
        if px <= 1920 * 1080 * 60:
            return "4.2"
        if px <= 3840 * 2160 * 30:
            return "5.1"
        return "5.2"


def make_output(preset: str = "reel", fps: int = 30, quality: str = "standard",
                k4: bool = False, scale: float = 1.0) -> Output:
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
    if quality not in QUALITY:
        raise ValueError(f"unknown quality {quality!r}; choose from {sorted(QUALITY)}")
    if fps not in (24, 25, 30, 50, 60):
        raise ValueError("fps must be one of 24, 25, 30, 50, 60")
    w, h = PRESETS[preset]
    if k4:
        w, h = w * 2, h * 2
    if scale != 1.0:
        w, h = int(round(w * scale / 2) * 2), int(round(h * scale / 2) * 2)
    s, sh, crf, xp = QUALITY[quality]
    return Output(w, h, fps, s, sh, crf, xp, preset, quality)
