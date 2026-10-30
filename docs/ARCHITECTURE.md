# Architecture

```
spec.json ──► spec.py (JSONC loader, hash) ──► film.py  Film
                                                 │  Timeline (timeline.py): markers, beat grid, time expressions
                                                 │  Scenes (scenes/*): 27 types, each = draw(ctx) + cues() + shutter()
                                                 │  Boundaries: cut/flash/fade/whip/push/zoom/squash
                                                 ▼
pipeline.build ─► render.py ── N spawn workers (resources.auto_workers: free RAM / CPU)
                 │              each: Compositor (compositor.py) → Skia raster → motion-blur
                 │              subsamples → bloom/chroma/vignette/grain → libx264 chunk .ts
                 │              chunk verified with ffprobe, then atomically renamed (resume)
                 │            concat (-c copy, +faststart)
                 ├─► audio/mix.py: bed (audio/music.py procedural styles | user file | none)
                 │              + SFX (audio/synth.py kit) from sfx markers, ducking,
                 │              loudness measure → gain → true-peak limiter, iterate to -14 LUFS / -1 dBTP
                 ├─► mux (AAC 192k 48 kHz)
                 └─► audit.py: ffprobe + decoded motion analysis + loudness → PASS/FAIL report
```

| module | role |
|---|---|
| `timeline.py` | `Timeline`/`Marker`: sections+energy, cues, captions, sfx, hits; seconds/beats/bars/named expressions; grid-aware beat↔time |
| `beats.py` | onset envelope + autocorrelation tempo + phase fit → `BeatGrid` from any audio file |
| `film.py` | builds scenes on the timeline, resolves markers, emits scene cues/transition SFX, draws a frame at time t |
| `compositor.py` | frame sampling (adaptive motion blur), post chain, deterministic grain |
| `render.py` | chunk planning, worker pool, encoder command, manifest + resume, concat, mux |
| `resources.py` | free-RAM/CPU-aware worker sizing |
| `audio/synth.py` | 20 synthesized SFX with peak offsets for pre-roll alignment |
| `audio/music.py` | 5 arranged styles following section energy, fills, risers, sidechain, hard stop |
| `audio/mix.py` | bed + SFX mix, ducking, LUFS normalisation, lookahead limiter |
| `audit.py` | technical + motion + loudness checks with per-mode thresholds |
| `preview.py` | stills at scene middles and transitions + contact sheet |
| `cli.py` | `kstudio` command |
| `draw.py`, `fonts.py`, `ease.py` | Skia helpers, icons, variable-font instancing, easing/springs/noise |
| `modes.py` | themes + per-mode music/camera/post/transition/audit defaults |
