---
name: kinetic-studio
description: Turn a short brief into a finished, beat-synced motion-graphics video (MP4, 9:16/16:9/1:1, 30/60 fps, optional 4K) with original synthesized music and SFX, normalised to -14 LUFS, and an automatic quality audit. Five modes - kinetic type teaser, explainer/education, product/app promo, data/crypto stats, social quote/karaoke clip. Use when the user asks for a reel, short, teaser, explainer, promo, stats or quote video and no footage is needed. Runs locally (Python + Skia + ffmpeg); no paid APIs, no sign-ups.
---

# Kinetic Studio

A local, programmatic video engine. You write a small JSON spec (scenes + timeline markers);
the engine renders it in parallel, scores it with procedural music and SFX locked to the beat
grid, mixes to -14 LUFS / -1 dBTP and audits the result.

Project root: the folder containing this file (call it `$KSTUDIO`). Launcher: `$KSTUDIO/bin/kstudio`.
First run on a new machine: `bash $KSTUDIO/install.sh` (needs python3 >= 3.10 and ffmpeg with libx264).

## Workflow (brief -> video)

1. **Pick the mode** from the brief (table below). If unclear, ask one question or default to `kinetic`.
2. `bin/kstudio new <mode> <project_dir>` copies a working example spec into `<project_dir>/spec.json`.
3. **Rewrite the spec** from the brief (rules below). Keep 8-15 s for social, one idea per scene.
4. `bin/kstudio info <project_dir>` - validates and prints the resolved timeline (scene starts, markers).
   Fix any `kstudio: error:` it reports.
5. `bin/kstudio preview <project_dir>` - renders stills + `contact_sheet.jpg` in ~1 s. **Look at the
   contact sheet** (Read the image) before the full render: check text fits, nothing overlaps, the
   last frame is the intended end card.
6. `bin/kstudio render <project_dir> --workers auto --preset reel` - final MP4 at
   `<project_dir>/out/<name>_reel.mp4`, plus `.report.json` (timings, loudness, audit) and
   `.render.log` (JSONL progress). Exit code 3 = audit failed, 2 = spec error.
7. Report the path, duration, audit verdict and any failed check. If the audit fails on motion,
   shorten holds (smaller `dur`), add a scene, or use a busier transition; do not just relax thresholds.

Interrupted renders resume: re-run the same command; finished 2-second chunks are reused
(`--fresh` forces a clean render). Any change to the spec, assets or engine invalidates old chunks.

## Modes

| mode | use for | look / sound | key scenes |
|---|---|---|---|
| `kinetic` | hype teasers, announcements, hooks | black/bone/red, whip transitions, `drive` 128 BPM | `slam`, `stack`, `marquee`, `type3d`, `scramble`, `countdown`, `burst` |
| `explainer` | education, science, "what is X" | navy/teal lab theme, fades, `calm` 84 BPM | `title`, `definition`, `diagram`, `bullets`, `keyfact`, `text`, `end_card` + `sources` lower-thirds |
| `promo` | app/product launches | violet studio, push transitions, `bright` 112 BPM | `device`, `features`, `cta`, `logo`, `image` |
| `data` | market/crypto/KPI stories | terminal green/red, zoom transitions, `pulse` 120 BPM | `counter`, `stat_grid`, `bars`, `line`, `candles`, `ticker` |
| `social` | quotes, tips, voice-style captions | warm espresso/amber, `lofi` 86 BPM | `quote`, `karaoke`, `text` |

Any scene works in any mode; the mode only sets theme, music, camera feel, transitions and audit
thresholds. Full parameter list: `bin/kstudio scenes -v` or `docs/SCENES.md`.

## Spec cheat-sheet

```jsonc
{
  "mode": "kinetic",                 // kinetic | explainer | promo | data | social
  "handle": "@you",                  // small corner handle (optional)
  "progress": false,                 // thin progress bar at the bottom
  "theme": "ink",                    // or {"base": "ink", "accent": "#ff3b30", ...}
  "music": { "style": "drive", "bpm": 128, "gain": -3, "sfx": 1.0 },
  //  styles: drive | calm | bright | pulse | lofi | none
  //  own track: { "file": "track.mp3", "start": 12.0 }  -> beats are detected from the file
  "transition": "whip",              // default between scenes: cut flash fade whip push zoom squash (+ "-left" etc.)
  "markers": [
    { "kind": "section", "at": 0, "name": "intro", "energy": 1 },   // energy 0-3 drives music layers + camera
    { "kind": "section", "at": "drop", "name": "drop", "energy": 3 },
    { "kind": "sfx", "at": "drop-2b", "sound": "riser_short" },      // see `bin/kstudio sfx`
    { "kind": "hit", "at": "8b", "strength": 0.8 },                  // camera shake / flash accent
    { "kind": "lower_third", "at": 0.6, "dur": 2.5, "tag": "Note", "text": "..." },
    { "kind": "cue", "at": "bar3", "name": "music_stop" }            // hard music stop
  ],
  "captions": [ { "at": 1.0, "dur": 2.0, "text": "burned-in caption" } ],
  "sources":  [ { "at": "studied+0.6", "dur": 2.3, "text": "Author et al., Journal, Year" } ],
  "scenes": [ { "type": "slam", "id": "open", "words": ["ONE", "TWO"], "dur": "4b", "transition": "flash" } ]
}
```

**Times** accept seconds (`1.5`, `1.5s`, `250ms`), beats (`4b`, `b4`), bars (`1bar`, `2bars`,
`bar3` = start of bar 3), named markers and scene ids (`drop`, `studied+0.6`, `drop-2b`, `end`).
Scenes run back-to-back unless given `at`. Durations in beats (`"dur": "4b"`) keep cuts on the beat.
Comments (`//`, `/* */`) and trailing commas are allowed in spec.json.

## Brief -> spec rules per mode

**kinetic** - Hook in the first 2 beats (`slam` with 2-4 words). One idea per scene, 2-4 words per
line, ALL CAPS reads best. Build energy with sections 1 -> 2 -> 3, put `riser_short` 2 beats before
the drop, end on `burst` or `logo`. Durations in beats (`3b`, `4b`).

**explainer** - Structure: `title` (question) -> `definition` -> `diagram` or `keyfact` -> `bullets`
-> `end_card`. 2-4 s per scene, max ~6 words per bullet. Put citations in `sources` (author, journal,
year) timed to the scene they support. For health or medical topics:
- describe what it is and where it is **"studied in"** / "researched for"; never say it treats, cures,
  prevents, reverses or is safe/effective;
- no doses, routes, protocols, sourcing or before/after claims;
- always end on `end_card` with "For learning only." / "Not medical advice." (the defaults).
For finance use "Illustrative data · not financial advice" in a `lower_third`.

**promo** - `device` hero (headline = product name, 2-3 callouts with icons, `screen: "shot.png"` for
a real screenshot placed in the project dir, else procedural UI from `ui`) -> `features` (4 items,
2-3 words title + short line) -> `cta` (verb-first button) -> `logo` (wordmark + tagline + url).

**data** - Lead with the single most striking number (`counter`), then 2-3 charts, end on `ticker`
or `stat_grid`. Pass real numbers from the brief/data source; if numbers are illustrative say so in a
`lower_third`. `line` takes `series` as a list of numbers or a seeded random walk
`{"seed","n","start","drift","vol"}`; `candles` takes `ohlc` (list of `[o,h,l,c]`) or the same
random-walk keys.

**social** - `quote` (<= 20 words, author + role) and/or `karaoke` with the spoken line; set `per`
(seconds per word, 0.28-0.4), `"timing": "beat"`, or exact per-word timings
`"words": [{"w": "Make", "t": 0.0}, ...]` (seconds from scene start, e.g. from a transcript);
list 2-3 `emphasis` words.

## Commands

```
bin/kstudio modes                         # list modes
bin/kstudio scenes [type] [-v]            # scene types and parameters
bin/kstudio new <mode> <dir> [--force]    # start from the mode's example
bin/kstudio info <dir> [--json]           # validate + resolved timeline
bin/kstudio preview <dir> [--at 1.2,3.4] [--preset reel] [--scale 0.5] [--quality draft]
bin/kstudio render <dir> [-o out.mp4] [--preset reel|landscape|square|portrait] [--fps 30|60]
               [--quality draft|standard|high] [--4k] [--workers auto|N] [--fresh] [--no-audit] [-q]
bin/kstudio audit <video.mp4> [--mode explainer] [--min-motion 0.6] [--max-hold 2] [--json]
bin/kstudio mix <dir> -o mix.wav          # soundtrack only
bin/kstudio beats <track.mp3>             # tempo + beat grid of a music file
bin/kstudio sfx <dir>                     # export the synthesized SFX kit as WAVs
```

Presets: `reel`/`story` 1080x1920 (9:16), `landscape`/`youtube` 1920x1080, `square` 1080x1080,
`portrait` 1080x1350. `--4k` doubles both sides. `--quality draft` is ~3x faster for checks.

## Performance and limits

- ~1.5-2x real time per second of 1080x1920@30 on 8 CPUs at `standard` (12 s video -> ~25 s).
  `--workers auto` sizes workers to free RAM (~550 MB each at 1080x1920) and CPU count.
- Pure 2D motion graphics: no live footage generation, no lip-sync, no TTS voice-over.
  For a user's own footage or photos use the `image` scene (Ken Burns) or composite afterwards.
- Text must be supplied; the engine does not write copy. You (the agent) write it from the brief.
- Do not post or upload results anywhere without the user's explicit approval.
