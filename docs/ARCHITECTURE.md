# Architecture

```
            agents (MCP stdio / streamable HTTP)   function calling   shell
                       │                                  │              │
                       ▼                                  ▼              ▼
            mcp_server.py: 8 tools ─────── run_tool() ──────── --call / cli.py (kstudio)
                       │
spec.json ──► spec.py (JSONC / JS loader, hash) ──► schema.py (JSON Schema from the engine, validation, fix hints)
                       │
                       ▼
                  film.py  Film
                       │  timeline.py: markers, beat grid, time expressions ("4b", "bar3", "drop-2b")
                       │  scenes/*: 31 types, each = draw(ctx) + cues() + shutter()
                       │  transitions.py: beat-quantised windows (cut ... match), element hand-off
                       │  camera.py: 2.5D dolly, parallax, drift with continuity across cuts
                       │  layout.py: 12-column grid, safe zones, type scale
                       ▼
pipeline.build ─► render.py ── N spawn workers (resources.py: free RAM / CPU)
                 │              each: compositor.py → Skia raster → linear-light motion blur
                 │              → grade.py (tone curve, split toning) → bloom / vignette / grain
                 │              → libx264 chunk, verified with ffprobe, renamed atomically (resume)
                 │            concat (-c copy, +faststart)
                 ├─► audio/mix.py: bed (audio/music.py procedural styles | user file | none)
                 │              + SFX (audio/synth.py kit) from sfx markers and transition cues,
                 │              ducking, loudness → gain → true-peak limiter, iterate to -14 LUFS / -1 dBTP
                 ├─► mux (AAC 48 kHz)
                 └─► audit.py: ffprobe + decoded motion analysis + loudness → PASS/FAIL report.json
```

Renders are deterministic: a frame is a pure function of (spec, time), so any worker can draw any
frame and an interrupted render resumes from its finished 2-second chunks.

## Interfaces

| module | role |
|---|---|
| `mcp_server.py` | zero-dependency MCP server: stdio (default) and streamable HTTP (`--http`); both protocol eras (2026-07-28 stateless with `server/discover`, and `initialize` for 2025-11-25 / 2025-06-18 / 2025-03-26 / 2024-11-05); tools `list_modes`, `list_scenes`, `get_schema`, `validate_spec`, `new_spec`, `preview`, `render` (progress notifications, cancellable), `audit`; `run_tool()` for in-process function calling; `--call` for one-shot JSON |
| `cli.py` | the `kstudio` command (`modes`, `scenes`, `new`, `info`, `preview`, `render`, `audit`, `mix`, `beats`, `sfx`) |
| `schema.py` | the spec JSON Schema built at run time from the scene registry and engine tables; validator with JSON Pointer paths, hints and a repair prompt |
| `exitcodes.py` | exit codes and machine-readable error codes shared by the command line and the tools |
| `brief.py` | brief → prompt pack (system prompt, schema, examples, beat rules) for any model; no model call |
| `docs/tools/generate.py` | exports the MCP tool list as function-calling schemas and a manifest; `--check` keeps them in sync |

## Engine

| module | role |
|---|---|
| `spec.py` | loads JSON with comments and trailing commas (or a JS module's default export) |
| `timeline.py` | `Timeline` / `Marker`: sections with energy 0-3, cues, captions, sfx, hits; seconds, beats, bars and named-marker expressions; beat ↔ time on the grid |
| `beats.py` | onset envelope + autocorrelation tempo + phase fit → `BeatGrid` from any audio file |
| `film.py` | builds scenes on the timeline, resolves markers and snapping, emits scene cues and transition sounds, draws the frame at time t |
| `scenes/` | `kinetic`, `explainer`, `promo`, `data`, `social` and `common` scene families on a shared `base.Scene` (grounds: solid, gradient, grid, dots, blobs, paper, mesh) |
| `transitions.py` | 13 transition kinds with beat-length windows, anchors, shutter multipliers and default sounds; wipe shapes, directions, shared-element `match` |
| `camera.py` | per-mode and per-scene camera (push, carry, drift, roll, truck, yaw, pitch, bump, shake ...) |
| `layout.py` | grid and type roles at 1080 reference px, scaled to any preset |
| `ease.py` | easing grammar, springs with bounded overshoot, staggers, deterministic noise |
| `draw.py`, `fonts.py` | Skia helpers, icons, palettes, variable-font instancing |
| `compositor.py` | adaptive sub-frame motion blur, post chain, dithered output |
| `grade.py` | colour grades `neutral`, `ink`, `lab`, `studio`, `terminal`, `warm` compiled to lookup tables |
| `modes.py` | 5 themes and per-mode music, camera, post, transition and audit defaults |
| `presets.py` | output presets (reel, story, landscape, youtube, square, portrait) and quality levels (draft, standard, high) |
| `render.py`, `pipeline.py` | chunk planning, worker pool, encoder, manifest and resume, concat, mix, mux, audit, report |
| `resources.py` | free-RAM and CPU aware worker count |
| `preview.py` | stills at scene middles and transitions plus a contact sheet |
| `audit.py` | codec, frame rate, frames, duration, motion ratio, longest hold, black frames, loudness, true peak, A/V gap, with per-mode thresholds |

## Audio

| module | role |
|---|---|
| `audio/music.py` | 5 arranged styles (`drive`, `calm`, `bright`, `pulse`, `lofi`) following section energy: fills, risers, sidechain, hard stop |
| `audio/instruments.py` | synthesised drums, bass, keys and pads (no samples) |
| `audio/synth.py` | 29 synthesised sound effects (14 of them UI sounds) with peak offsets for alignment |
| `audio/mix.py` | bed + effects, ducking, LUFS normalisation, look-ahead limiter |
| `audio/dsp.py` | NumPy DSP: envelopes, filters, oscillators, convolution reverb, WAV I/O |
