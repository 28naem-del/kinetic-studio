# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
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
- `skills/kinetic-studio/SKILL.md`: the skill in the portable folder layout (identical to `SKILL.md`).
- `llms.txt` and `llms-full.txt` for language-model readers.
- README demo GIFs in `demos/`, rendered from the v0.2 example specs.

### Changed
- `SKILL.md` rewritten around the tools: workflow, beat units, the JSON contract, error codes,
  per-mode writing rules and troubleshooting.
- The engine now has 31 scene types, 13 transition kinds, 29 synthesised sound effects and colour
  grades per theme; scene lengths and times can be written in beats and bars.
- `docs/SCENES.md` is generated from the scene registry (`python docs/tools/generate.py --scenes`);
  `docs/ARCHITECTURE.md` covers the new modules.

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

[Unreleased]: https://github.com/28naem-del/kinetic-studio/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/28naem-del/kinetic-studio/releases/tag/v0.1.0
