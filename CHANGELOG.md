# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-31

### Added
- Beat-unit timing everywhere: scene lengths, staggers, cue times and karaoke pacing can be written as
  beats and bars (`"4b"`, `"1/2b"`, `"bar3"`, `"drop-2b"`); `"snap"` in a spec or `render --snap
  [beat|8th|16th|bar]` puts seconds-valued scenes on the grid, and `info` prints the beat-grid lint.
- Layout grid and type scale (96 px margins, 12 columns, safe lines, display-to-meta roles); the common
  scenes are set on it. Variable font axes (weight, width, optical size) now actually change the face,
  with pair kerning kept under tracking and balanced line wrapping.
- Transition grammar: 13 kinds (cut, flash, cross, fade, dissolve, whip, push, zoom, squash, wipe with
  rect / circle / diagonal / type masks, slice, split and `match`, which hands a shared element from one
  scene to the next), windows sized in beats around an on-beat cut, sound peaks aligned to the cut.
- Easing registry (named curves, cubic-bezier, springs fitted to a settle time, staggers), linear-light
  motion blur that follows the measured motion, per-theme colour grades and per-frame film grain.
- 2.5D camera with parallax layers and velocity continuity across cuts; paper and mesh grounds.
- New scenes: `cover`, `keyfact` and `hook`; the kinetic, explainer, promo, data and social examples are
  rebuilt on the beat grid (31 scene types in total).
- Sound: synthesised drums, bass, pads and leads with per-style arrangement, sends and sidechain;
  29 sound effects placed on the 16th grid with velocity from section energy.
- Delivery checks in `report.json`: post-encode loudness and true peak, frame rate (r = avg = n/d),
  frame count, A/V gap and black frames, plus a top-level `pass`.
- Machine interface: `--json` on every command (one JSON object on stdout, JSON-line progress on stderr),
  fixed exit codes (`exit-codes`), and the `schema`, `validate`, `brief` and `doctor` commands. The spec
  JSON Schema is generated from the engine; validation errors carry JSON Pointer paths and fix hints.
- MCP server (`python -m kstudio.mcp_server`, `kstudio-mcp`): zero-dependency, stdio and streamable HTTP
  (`--http`), both protocol eras (2026-07-28 stateless with `server/discover`, and the `initialize`
  handshake for 2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05). Eight tools with JSON Schema inputs
  and outputs: `list_modes`, `list_scenes`, `get_schema`, `validate_spec`, `new_spec`, `preview`
  (contact sheet returned as an image), `render` (progress notifications, cancellable) and `audit`.
  `--call TOOL JSON` runs one tool and prints one JSON object; `run_tool()` dispatches function calls
  in-process.
- `docs/tools/`: the tool list as MCP JSON, strict function-calling schemas (flat and nested) and a
  plain manifest, all generated from the server by `docs/tools/generate.py` and checked by the tests.
- `docs/quickstarts/`: one page per agent framework, each checked against that framework's
  documentation (AutoGen and Agent Framework, Claude Code, Cline, Codex CLI, Continue, CrewAI, Cursor,
  Dify, Gemini CLI, LangChain and LangGraph, n8n, OpenHands, Windsurf, plain Python, shell and curl).
- Optional extension hooks: an installed `kstudio.swap` module can add subcommands
  (`register(subparsers)`) and MCP tools (`MCP_TOOLS` / `MCP_HANDLERS`); see `docs/CLI.md`.
- `skills/kinetic-studio/SKILL.md`: the skill in the portable folder layout (identical to `SKILL.md`).
- `llms.txt` and `llms-full.txt` for language-model readers.
- The wheel ships the example projects and fonts; `kinetic-studio`, `kstudio` and `kstudio-mcp` scripts.
- README demo GIFs in `demos/`, cut from the 1080x1920 renders of the example specs by
  `scripts/make_gifs.sh` (320 px, 15 fps, under 8 MB together).

### Changed
- `SKILL.md` rewritten around the tools: workflow, beat units, the JSON contract, error codes,
  per-mode writing rules and troubleshooting.
- Exit codes: 3 is an invalid spec and 5 a failed audit (a render whose audit fails now exits 5).
- `preview` returns each still as `{t, label, path}` and takes `-o` as a short form of `--out`.
- Flashes are exactly two accent-tinted frames; grain is seeded per frame from the spec, and chunks are
  encoded with one x264 thread, so the same spec renders bit-identical video.
- The true-peak limiter holds -1.3 dBTP on the delivered file and the bitrate stays inside the
  maxrate; the audit measures motion with the grain cancelled.
- Fast pushes and zooms smear into one continuous streak; render workers use OpenCV threads only from
  idle cores.
- The end card is built around one readable handle.
- Explainer and social default to `cross` instead of `fade`.
- `docs/SCENES.md` is generated from the scene registry (`python docs/tools/generate.py --scenes`);
  `docs/ARCHITECTURE.md` covers the new modules.
- `plugin.json` lists the README GIFs as its demos.

### Fixed
- `preview` failed on every call (exit 4); it writes the stills and the contact sheet again.
- Ctrl-C during `render` exits 130 (`INTERRUPTED`) instead of 4, keeping finished chunks for the re-run.
- Gradient fills passed with a zero colour alpha (promo device and tiles, data line area, image scrim)
  were invisible.
- A lower third and a bottom-anchored image caption no longer overlap.

### Removed
- The v0.1 demo MP4s and their reports in `demos/`, and the unused GIFs in `docs/media/`.

## [0.1.0] - 2026-10-30

First public release.

### Added
- Five modes: `kinetic`, `explainer`, `promo`, `data`, `social`, each with an example spec.
- 27 scene types, 7 transitions, 5 themes, per-key theme overrides.
- Timeline markers (sections with energy, cues, captions, sources, SFX, hits) with seconds,
  beats, bars and named-marker expressions.
- Beat detection for user music files.
- Procedural music (5 styles) and 20 synthesised SFX; ducking, -14 LUFS normalisation and a
  look-ahead true-peak limiter.
- Parallel, resumable, RAM-aware renderer (Skia + libx264) with motion blur, bloom, chromatic
  aberration, vignette and grain; presets for 9:16, 16:9, 1:1, 4:5, 30/60 fps and `--4k`.
- Automatic audit (codec, motion, holds, loudness, true peak, duration).
- `kstudio` CLI, agent skill (`SKILL.md`) and `plugin.json`.
- Demo renders, CI, contribution docs.

[Unreleased]: https://github.com/28naem-del/kinetic-studio/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/28naem-del/kinetic-studio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/28naem-del/kinetic-studio/releases/tag/v0.1.0
