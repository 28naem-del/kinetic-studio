"""Brief -> spec prompt pack. No model call: the pack is plain text + JSON that any LLM (local or hosted,
any vendor) can answer with a spec, and `kinetic-studio validate --fix-hints` closes the repair loop.

The pack holds a system prompt, the user message (the brief plus the timing target), the spec JSON
Schema generated from the engine (narrowed to the mode's scene families unless all are asked for),
two complete example specs (the mode's own plus a contrasting one) and the mode's beat-unit rules,
which are derived from the mode table (BPM, default transition) and the bundled example (scene
lengths in beats, total bars).

Loop for an agent:
    pack = kinetic-studio brief --mode promo --text "..." --json
    spec = LLM(pack.system, pack.user)            # one JSON object
    kinetic-studio validate - --json --fix-hints  < spec   # exit 0 -> render; exit 3 -> send fix_prompt back
"""
from __future__ import annotations

import json
from typing import Any

from .presets import PRESETS

# Craft notes per mode (the design targets the bundled examples were built to).
MODE_NOTES: dict[str, list[str]] = {
    "kinetic": [
        "One idea per beat: huge words that fill the width, 1-4 words per line.",
        "Every scene is one bar (\"4b\"); land words on the beat with per \"1b\" or row staggers \"1/8b\".",
        "Alternate whip / zoom / flash transitions; save `burst` for the drop (last scene).",
    ],
    "explainer": [
        "Calm editorial pacing: a cover, a definition or key fact, a labelled diagram, numbered bullets, an end card.",
        "Scene lengths 3-5 beats at the slow BPM; bullets stagger \"1b\", diagram nodes \"1/2b\".",
        "Transitions: wipe, match (hand a term to the next scene), push \"1/2b\", and a hard cut on the downbeat "
        "into the end card.",
    ],
    "promo": [
        "Product story: device hero shot (8b), features one per 2 beats (\"per\": \"2b\"), CTA (4b), brand card (4b).",
        "Cuts on downbeats; the CTA pulses on every beat and the button press lands on a downbeat.",
        "Keep feature labels short (2-4 words); the headline 3-6 words.",
    ],
    "data": [
        "Stats story: a counter, a chart (bars / line / candles), a stat grid or ticker; one number per scene.",
        "Scene lengths 4-6 beats at 120 BPM so every cut lands on the kick; counters tick on 16ths.",
        "Give numbers units and a source line; highlight one bar or event.",
    ],
    "social": [
        "Spoken-caption clip: karaoke (words on 8ths), a pull quote revealed word by word, a follow card.",
        "Scene lengths 8 or 16 beats; a hard cut on the bar line between scenes.",
        "Write captions as the creator would say them: short sentences, 6-14 words each.",
    ],
}

GENERAL_RULES: list[str] = [
    "Return exactly one JSON object that validates against the schema. No prose, no markdown fences.",
    "Times and lengths are seconds or beat units at the music BPM: \"4b\" = 4 beats, \"1/2b\" = half a beat, "
    "\"1bar\" = 4 beats. Prefer beat units everywhere (dur, per, stagger, first, transition_dur, marker at).",
    "Scene lengths are whole beats, preferably 2, 4 or 8, so every cut sits on the beat grid.",
    "Staggers and per-item timings in beat fractions (\"1b\", \"1/2b\", \"1/4b\", \"1/8b\"), never in raw seconds.",
    "Use only scene types and fields that appear in the schema; any scene type works in any mode.",
    "Markers: sections with energy 1-3 shape the music; an `sfx` marker (\"sound\") sits on a beat "
    "(\"drop-2b\" = two beats before the scene with id \"drop\").",
    "Text: one idea per scene, short lines (<= 24 characters per line for display type), no emoji.",
    "Set \"id\" on scenes you want to reference from markers (\"at\": \"drop\") or hand an element across a "
    "match cut.",
]


def _mode_example(mode: str) -> dict[str, Any] | None:
    from .spec import example_spec, load_spec
    p = example_spec(mode)
    if p is None:
        return None
    spec = load_spec(str(p))
    return {k: v for k, v in spec.items() if not k.startswith("_")}


def _contrast_mode(mode: str, available: list[str]) -> str | None:
    order = ["kinetic", "promo", "explainer", "social", "data"]
    for m in order:
        if m != mode and m in available:
            return m
    return next((m for m in available if m != mode), None)


def mode_rules(mode: str, duration: float | None = None) -> dict[str, Any]:
    """Beat-unit rules for a mode, from the mode table and its bundled example."""
    from .modes import MODES
    from .timeline import Timeline
    cfg = MODES[mode]
    bpm = float(cfg["music"]["bpm"])
    spb = 60.0 / bpm
    tl = Timeline(bpm)
    ex = _mode_example(mode) or {}
    lengths: list[dict[str, Any]] = []
    total_beats = 0.0
    for sc in ex.get("scenes", []):
        d = sc.get("dur", 2.0)
        try:
            beats = tl.beats_of(tl.resolve_dur(d, 0.0), 0.0)
        except Exception:  # noqa: BLE001 - informative only
            beats = None
        if beats is not None:
            total_beats += beats
        lengths.append({"type": sc.get("type"), "dur": d,
                        "beats": round(beats, 3) if beats is not None else None,
                        **({"transition": sc["transition"]} if "transition" in sc else {})})
    target: dict[str, Any] | None = None
    if duration:
        bars = max(2, round(duration / (4 * spb)))
        target = {"seconds": round(bars * 4 * spb, 3), "beats": bars * 4, "bars": bars,
                  "note": f"{bars} bars of 4 beats at {bpm:g} BPM; distribute them over the scenes in whole beats"}
    return {
        "mode": mode, "bpm": bpm, "seconds_per_beat": round(spb, 4), "theme": cfg["theme"],
        "music_style": cfg["music"]["style"], "default_transition": cfg["transition"],
        "example_rhythm": lengths, "example_total_beats": round(total_beats, 3),
        "notes": MODE_NOTES.get(mode, []), "target": target,
    }


def mode_scene_types(mode: str) -> list[str]:
    """Scene types designed for a mode (its own scene module plus the common scenes)."""
    from .scenes import REGISTRY
    return sorted(n for n, c in REGISTRY.items() if c.__module__.rsplit(".", 1)[-1] in (mode, "common"))


def prompt_pack(text: str, mode: str = "kinetic", duration: float | None = None, preset: str = "reel",
                all_scenes: bool = False) -> dict[str, Any]:
    """Everything an LLM needs to turn `text` into a valid spec for `mode`."""
    from .modes import MODES
    from .schema import spec_schema
    from .spec import examples_dir
    if mode not in MODES:
        raise KeyError(f"unknown mode {mode!r}; choose from {sorted(MODES)}")
    if preset not in PRESETS:
        raise KeyError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
    available = sorted(p.name for p in examples_dir().iterdir() if p.is_dir()) if examples_dir().is_dir() else []
    examples: list[dict[str, Any]] = []
    own = _mode_example(mode)
    if own is not None:
        examples.append({"mode": mode, "spec": own})
    other = _contrast_mode(mode, available)
    if other:
        ex = _mode_example(other)
        if ex is not None:
            examples.append({"mode": other, "spec": ex})
    types = None if all_scenes else sorted(set(mode_scene_types(mode)) |
                                            {s.get("type") for e in examples for s in e["spec"].get("scenes", [])})
    schema = spec_schema(types)
    rules = mode_rules(mode, duration)
    w, h = PRESETS[preset]
    system = "\n".join([
        "You write Kinetic Studio specs: JSON documents that a local renderer turns into beat-synced "
        "motion-graphics videos (kinetic type, explainers, product promos, data stories, social clips).",
        "",
        "Output contract:",
        *[f"- {r}" for r in GENERAL_RULES],
        "",
        f"Mode \"{mode}\": {MODES[mode]['description']}",
        f"- Music {rules['music_style']} at {rules['bpm']:g} BPM (1 beat = {rules['seconds_per_beat']} s); "
        f"theme {rules['theme']}; default transition {rules['default_transition']}.",
        *[f"- {n}" for n in rules["notes"]],
        "- Example rhythm: " + ", ".join(f"{x['type']} {x['dur']}" for x in rules["example_rhythm"])
        + f" (total {rules['example_total_beats']:g} beats).",
        "",
        "JSON Schema of the spec (draft 2020-12):",
        json.dumps(schema, separators=(",", ":")),
        "",
        *[line for e in examples for line in (f"Example spec ({e['mode']} mode):", json.dumps(e["spec"], indent=1), "")],
    ])
    target = rules["target"]
    user = "\n".join([
        f"Brief: {text.strip()}",
        f"Mode: {mode}. Output: {preset} {w}x{h}.",
        (f"Length: about {target['seconds']} s = {target['beats']} beats ({target['bars']} bars) at "
         f"{rules['bpm']:g} BPM." if target else
         f"Length: like the example, about {rules['example_total_beats']:g} beats."),
        "Return the spec as one JSON object.",
    ])
    return {
        "mode": mode, "preset": preset, "brief": text, "system": system, "user": user,
        "schema": schema, "examples": examples, "rules": {"general": GENERAL_RULES, "mode": rules},
        "scene_types": schema["x-scene-types"],
        "response_format": {"type": "json_schema", "json_schema": {"name": "kinetic_studio_spec",
                                                                   "schema": schema}},
        "next": ["save the model's answer as spec.json",
                 "kinetic-studio validate spec.json --json --fix-hints  (exit 3: send fix_prompt back to the model)",
                 "kinetic-studio preview spec.json --json  (look at the contact sheet)",
                 "kinetic-studio render spec.json --json"],
    }


def prompt_text(pack: dict[str, Any]) -> str:
    """The pack as one plain-text prompt (system + user), for tools that take a single string."""
    return f"{pack['system']}\n\n---\n\n{pack['user']}\n"
