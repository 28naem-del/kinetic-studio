# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/28naem-del/kinetic-studio/releases/tag/v0.1.0
