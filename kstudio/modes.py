"""Modes = a theme + music style + camera/post feel + audit thresholds + a starter spec.

A mode is only a set of defaults: any scene can be used in any mode, and every value
can be overridden in the spec ("theme", "music", "post", "camera", "audit").
"""
from __future__ import annotations

import copy
from typing import Any

THEMES: dict[str, dict[str, Any]] = {
    "ink": {  # kinetic type: black / bone / signal red
        "bg": "#0b0b0f", "bg2": "#17171f", "paper": "#f2eee5", "accent": "#ff3b2e", "accent2": "#ffd23f",
        "ink": "#f2eee5", "ink_dark": "#0b0b0f", "muted": "#6e6e7a", "pos": "#3ee08f", "neg": "#ff3b2e",
        "display": "Archivo", "body": "Inter", "mono": "JetBrainsMono", "ground_style": "solid",
        "grade": "ink",
    },
    "lab": {  # explainer: deep navy, clinical teal, warm amber for emphasis
        "bg": "#0a1520", "bg2": "#10273a", "paper": "#edf3f6", "accent": "#38d6c4", "accent2": "#ffb547",
        "ink": "#edf3f6", "ink_dark": "#0a1520", "muted": "#7f95a8", "pos": "#38d6c4", "neg": "#ff7a6b",
        "display": "Fraunces", "body": "Inter", "mono": "JetBrainsMono", "ground_style": "grid",
    },
    "studio": {  # product promo: ink violet gradient, electric violet + cyan
        "bg": "#0c0a1c", "bg2": "#241866", "paper": "#ffffff", "accent": "#8b6cff", "accent2": "#22d3ee",
        "ink": "#ffffff", "ink_dark": "#0c0a1c", "muted": "#a6a1c9", "pos": "#34d399", "neg": "#fb7185",
        "display": "SpaceGrotesk", "body": "Inter", "mono": "JetBrainsMono", "ground_style": "blobs",
    },
    "terminal": {  # data / trading: near-black, neon green up, hot red down, gold
        "bg": "#06080b", "bg2": "#0d141b", "paper": "#e9f1f3", "accent": "#19e68c", "accent2": "#f5c542",
        "ink": "#e9f1f3", "ink_dark": "#06080b", "muted": "#5b6a74", "pos": "#19e68c", "neg": "#ff4d5e",
        "display": "SpaceGrotesk", "body": "Inter", "mono": "JetBrainsMono", "ground_style": "dots",
    },
    "warm": {  # social / quote: espresso, amber, tangerine
        "bg": "#17110d", "bg2": "#3b2216", "paper": "#fff5e6", "accent": "#ffb703", "accent2": "#fb5607",
        "ink": "#fff5e6", "ink_dark": "#17110d", "muted": "#a8907c", "pos": "#8ac926", "neg": "#fb5607",
        "display": "Fraunces", "body": "Inter", "mono": "JetBrainsMono", "ground_style": "blobs",
    },
}

MODES: dict[str, dict[str, Any]] = {
    "kinetic": {
        "description": "Kinetic type teaser: huge beat-synced words, whips, 3D type, flashes.",
        "theme": "ink",
        "music": {"style": "drive", "bpm": 128},
        "camera": {"push": 0.06, "roll": 0.012, "shake": 1.0, "bump": 0.035, "drift": 6, "drift_hz": 0.45,
                   "carry": "world", "ground_depth": 3.0},
        "post": {"bloom": 0.55, "grain": 0.055, "vignette": 0.35, "chroma": 1.0},
        "transition": "whip",
        "chrome": True,
        "audit": {"max_hold": 0.75, "min_motion": 0.85},
    },
    "explainer": {
        "description": "Explainer / education: calm pacing, title cards, labelled diagrams, sources, disclaimer.",
        "theme": "lab",
        "music": {"style": "calm", "bpm": 84},
        "camera": {"push": 0.05, "roll": 0.0, "shake": 0.15, "bump": 0.0, "drift": 14, "drift_hz": 0.3,
                   "carry": "world", "ground_depth": 3.0},
        "post": {"bloom": 0.18, "grain": 0.03, "vignette": 0.3, "chroma": 0.0},
        "transition": "cross",
        "audit": {"max_hold": 3.5, "min_motion": 0.45},
    },
    "promo": {
        "description": "Product / app promo: device mockups with screenshots, feature callouts, logo sting.",
        "theme": "studio",
        "music": {"style": "bright", "bpm": 112},
        "camera": {"push": 0.06, "roll": 0.004, "shake": 0.5, "bump": 0.01, "drift": 18, "drift_hz": 0.35,
                   "carry": "world", "ground_depth": 3.0},
        "post": {"bloom": 0.35, "grain": 0.03, "vignette": 0.25, "chroma": 0.4},
        "transition": "push",
        "audit": {"max_hold": 1.5, "min_motion": 0.7},
    },
    "data": {
        "description": "Data / stats story: counters, bar/line/candle charts, tickers.",
        "theme": "terminal",
        "music": {"style": "pulse", "bpm": 120},
        "camera": {"push": 0.03, "roll": 0.003, "shake": 0.5, "bump": 0.012, "drift": 6, "drift_hz": 0.35,
                   "carry": "world", "ground_depth": 3.0},
        "post": {"bloom": 0.45, "grain": 0.04, "vignette": 0.35, "chroma": 0.5},
        "transition": "zoom",
        "audit": {"max_hold": 1.5, "min_motion": 0.7},
    },
    "social": {
        "description": "Quote / social clip: karaoke captions with word highlight, quote cards.",
        "theme": "warm",
        "music": {"style": "lofi", "bpm": 86},
        "camera": {"push": 0.03, "roll": 0.0, "shake": 0.3, "bump": 0.006, "drift": 8, "drift_hz": 0.3,
                   "carry": "world", "ground_depth": 3.0},
        "post": {"bloom": 0.25, "grain": 0.05, "vignette": 0.4, "chroma": 0.0},
        "transition": "cross",
        "audit": {"max_hold": 2.0, "min_motion": 0.6},
    },
}


def mode_defaults(mode: str) -> dict[str, Any]:
    if mode not in MODES:
        raise KeyError(f"unknown mode {mode!r}; choose from {sorted(MODES)}")
    return copy.deepcopy(MODES[mode])


def resolve_theme(name_or_dict: Any, overrides: dict | None = None) -> dict[str, Any]:
    if isinstance(name_or_dict, dict):
        base = copy.deepcopy(THEMES[name_or_dict.get("base", "ink")])
        base.update({k: v for k, v in name_or_dict.items() if k != "base"})
    else:
        base = copy.deepcopy(THEMES[name_or_dict])
    if overrides:
        base.update(overrides)
    return base
