---
name: kinetic-studio
description: Turn a short brief into a finished, beat-synced motion-graphics video (MP4, 9:16 / 16:9 / 1:1 / 4:5, 24-60 fps, optional 4K) with procedural music and sound effects, mixed to -14 LUFS, and an automatic quality audit. Five modes - kinetic type teaser, explainer, product promo, data story, social quote or karaoke clip. Use when someone asks for a reel, short, teaser, explainer, promo, stats video or quote video and no camera footage is needed. Runs locally (Python, Skia, ffmpeg); no paid API, no account, no cloud call.
license: MIT
compatibility: Python 3.10+ and ffmpeg/ffprobe with libx264 on PATH. Works through MCP (stdio or HTTP), the command line or Python.
metadata:
  version: "0.2"
  triggers: "make a reel, make a short, kinetic typography, animated text video, explainer video, product promo video, app launch video, stats video, data video, quote video, karaoke captions, motion graphics, beat-synced video"
---

# Kinetic Studio

A local, programmatic video engine. You write a small JSON spec (scenes on a beat grid plus timeline
markers); the engine renders it in parallel with motion blur and a film grade, scores it with
procedural music and sound effects locked to the beat, mixes to -14 LUFS / -1 dBTP and audits the
file. Same spec, same video: renders are deterministic and resumable.

## When to use

- Short vertical or landscape videos built from words, numbers, charts, UI mock-ups and logos:
  teasers, announcements, explainers, product promos, KPI or market recaps, quotes, captions.
- Anything that should cut on the beat of the music.

Not for: generating live-action footage, faces or voices, lip-sync or text-to-speech. A user's own
photo can go in an `image` scene and their own video in a `clip` scene (any mode: trimmed on the
beat, with titles, captions or counters drawn over it in `over`). The engine does not write copy:
you write the words from the brief.

## 30-second path

Pick one way in. All three run the same engine and return the same JSON.

**MCP (any MCP-capable agent).** Register the server, then call its tools:

```
command: uvx
args:    ["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"]
```

From a clone instead: `python -m kstudio.mcp_server` with the clone as working directory.
Per-framework snippets: `docs/quickstarts/`.

**Command line.**

```
kstudio new kinetic my-reel            # starter project from the mode's example
kstudio validate my-reel               # errors with JSON Pointer paths and hints
kstudio preview my-reel                # stills + contact_sheet.jpg in a few seconds
kstudio render my-reel                 # my-reel/out/my-reel_reel.mp4 + .report.json
```

`kstudio --json <command>` prints exactly one JSON object; exit codes: 0 ok, 2 usage, 3 spec
invalid, 4 render failed, 5 audit failed, 6 missing tool (`kstudio doctor` checks ffmpeg and the
rest). `kinetic-studio` is the same command.

**Function calling without MCP.** Load `docs/tools/functions.json` (or `tools.schema.json`) as
your tool list and dispatch each call with
`from kstudio.mcp_server import run_tool; run_tool(name, arguments_json)`.

## Workflow (brief -> video)

| step | MCP tool | command line |
|---|---|---|
| 1. see what exists | `list_modes`, `list_scenes` | `kstudio modes`, `kstudio scenes -v` |
| 2. get the contract | `get_schema {mode}` (schema + example spec) | `kstudio schema --mode <m>`, `docs/SCENES.md` |
| 3. start a project | `new_spec {mode, dir, spec_json?}` | `kstudio new <mode> <dir>` |
| 4. check the spec | `validate_spec {spec_path}` | `kstudio validate <dir>` |
| 5. look before rendering | `preview {spec_path}` (contact sheet image) | `kstudio preview <dir>` |
| 6. render | `render {spec_path}` (progress notifications) | `kstudio render <dir>` |
| 7. verify | `audit {video, mode}` (render already audits) | `kstudio audit <video> --mode <m>` |

1. **Pick the mode** from the brief (table below). If unclear, default to `kinetic`.
2. **Write the spec** from the mode's example: keep its structure, replace the words, numbers and
   colours. 8-15 s for social, one idea per scene.
3. **Validate** and fix every error (each has a JSON Pointer `path`, a `message` and a `hint`).
   Fix warnings about off-grid lengths too.
4. **Preview and look at the contact sheet**: text fits, nothing overlaps, the last frame is the
   intended end card.
5. **Render**, then report the output path, duration, audit verdict and any failed check. If motion
   or hold checks fail, shorten holds, add a scene or use a busier transition; do not relax thresholds.

Interrupted renders resume: run the same render again and finished 2-second chunks are reused
(`fresh: true` / `--fresh` forces a clean render).

## Beat units (the one rule that matters)

Every time and length can be written in beats at the music tempo, and should be:

| write | means |
|---|---|
| `"4b"`, `"1/2b"`, `"3/4b"` | 4 beats, half a beat, three quarters of a beat |
| `"1bar"`, `"2bars"` | one bar (4 beats), two bars |
| `"bar3"` | the start of bar 3 (1-based, like a DAW) |
| `"drop"`, `"drop-2b"`, `"intro+1.25s"`, `"end-1b"` | a named marker or scene id, with offsets |
| `1.5`, `"1.5s"`, `"250ms"` | seconds (avoid for scene lengths) |

- Scene lengths: whole beats, ideally 2b, 4b or 8b. Cuts, transition windows and their sound
  effects then land on the grid. Validation warns when a length is off the grid.
- `"snap": true` in the spec (or `snap` on render) moves seconds-valued lengths and starts onto the
  beat grid.
- With your own music (`"music": {"file": "track.mp3"}`) the tempo and downbeat come from the file.

## JSON contract

**Spec** (comments and trailing commas allowed; full schema: `get_schema`):

```jsonc
{
  "mode": "kinetic",                       // kinetic | explainer | promo | data | social
  "handle": "@you",                        // small corner handle (optional)
  "theme": "ink",                          // ink | lab | studio | terminal | warm, or {"base": "ink", "accent": "#ff3b30"}
  "music": { "style": "drive", "bpm": 128 },  // drive | calm | bright | pulse | lofi | none, or {"file": "track.mp3"}
  "transition": "whip",                    // default between scenes
  "markers": [
    { "kind": "section", "at": 0, "name": "intro", "energy": 1 },   // energy 0-3 drives music layers and camera
    { "kind": "section", "at": "drop", "name": "drop", "energy": 3 },
    { "kind": "sfx", "at": "drop-2b", "sound": "riser_short" },
    { "kind": "hit", "at": "8b", "strength": 0.8 },
    { "kind": "lower_third", "at": "1b", "dur": "4b", "tag": "Note", "text": "..." }
  ],
  "scenes": [
    { "type": "slam", "id": "open", "words": ["STOP", "SCROLLING"], "dur": "4b" },
    { "type": "stack", "lines": ["ONE IDEA", "PER", "BEAT"], "dur": "4b", "transition": "whip-left" },
    { "type": "burst", "id": "drop", "text": "GO", "dur": "4b", "transition": "flash" }
  ]
}
```

Transitions (per scene, on the incoming one): `cut`, `flash`, `cross` (alias `fade`), `dissolve`,
`whip`, `push`, `zoom`, `squash`, `wipe` (`rect`, `circle`, `diagonal`, `type`), `slice`, `split`,
`match` (shared-element hand-off). Shorthand `"whip-left"`, `"wipe-circle"`, or a dict
`{"kind": "wipe", "shape": "circle", "dur": "1b", "dir": "up", "on": "downbeat"}`.

**Tool results.** Every tool returns one JSON object. `ok` says whether the thing asked about is good
(spec valid, video rendered and audited). On failure there is `error: {code, message, hint}` and the
MCP result has `isError: true`; validation problems are a normal result with `ok: false` and an
`errors` list. Codes: `USAGE`, `MODE_UNKNOWN`, `DIR_NOT_EMPTY`, `FILE_NOT_FOUND`, `SPEC_INVALID`,
`SPEC_NOT_FOUND`, `SPEC_PARSE`, `RENDER_FAILED`, `MISSING_TOOL`, `INVALID_ARGUMENTS`, `INTERNAL_ERROR`.

## Modes

| mode | use for | look / sound | example scenes |
|---|---|---|---|
| `kinetic` | hype teasers, announcements, hooks | ink: black, bone, signal red; whips; `drive` 128 BPM | `slam`, `stack`, `marquee`, `type3d`, `scramble`, `countdown`, `burst` |
| `explainer` | education, "what is X" | lab: editorial paper, navy, cobalt; crosses; `calm` 84 BPM | `cover`, `definition`, `diagram`, `bullets`, `keyfact`, `end_card` |
| `promo` | app and product launches | studio: violet gradient; pushes; `bright` 112 BPM | `device`, `features`, `cta`, `brand`, `logo`, `image` |
| `data` | market, crypto, KPI stories | terminal: green up, red down; cuts; `pulse` 120 BPM | `counter`, `line`, `candles`, `bars`, `stat_grid`, `ticker` |
| `social` | quotes, tips, spoken-line captions | warm: espresso, amber; `lofi` 86 BPM | `hook`, `quote`, `karaoke`, `follow`, `text` |

Any scene works in any mode; the mode only sets theme, music, camera feel, default transition and
audit thresholds. Parameters per scene: `list_scenes {type}` or `docs/SCENES.md`.

## Brief -> spec rules per mode

**kinetic.** Hook in the first 2 beats (`slam`, 2-4 words). One idea per scene, 2-4 words per line;
capitals read best. Build energy with sections 1 -> 2 -> 3, put `riser_short` 2 beats before the
drop, end on `burst` or `logo`.

**explainer.** `cover` (the question) -> `definition` -> `diagram` or `keyfact` -> `bullets` ->
`end_card`. 4-8 beats per scene, at most ~6 words per bullet. Put citations in `sources` (author,
journal, year) timed to the scene they support. For health or medical topics:
- describe what it is and where it is "studied in" / "researched for"; never say it treats, cures,
  prevents, reverses or is safe or effective;
- no doses, routes, protocols, sourcing or before/after claims;
- end on `end_card` with "For learning only." / "Not medical advice." (the defaults).

For finance, add "Illustrative data - not financial advice" in a `lower_third`.

**promo.** `device` hero (headline = product name, 2-3 callouts, `screen: "shot.png"` for a real
screenshot in the project directory, else a procedural UI) -> `features` (4 items, 2-3 word title
plus a short line) -> `cta` (verb-first button) -> `brand` or `logo` (wordmark, tagline, url).

**data.** Lead with the single most striking number (`counter`), then 2-3 charts, end on `ticker` or
`stat_grid`. Use real numbers from the brief; if they are illustrative, say so in a `lower_third`.
`line` takes `series` (numbers) or a seeded random walk `{"seed", "n", "start", "drift", "vol"}`;
`candles` takes `ohlc` (`[o, h, l, c]` rows) or the same keys.

**social.** `hook` -> `quote` (at most 20 words, author and role) and/or `karaoke` with the spoken
line (`"timing": "beat"`, or per-word `"words": [{"w": "Make", "t": 0.0}, ...]` from a transcript,
2-3 `emphasis` words) -> `follow`.

## Output options

Presets: `reel` / `story` 1080x1920, `landscape` / `youtube` 1920x1080, `square` 1080x1080,
`portrait` 1080x1350; `k4` / `--4k` doubles both sides. Frame rates 24, 25, 30, 50, 60. Quality
`draft` (no motion blur, for checks), `standard` (default), `high`.

## Troubleshooting

| symptom | fix |
|---|---|
| `MISSING_TOOL` / ffmpeg not found | install ffmpeg with libx264 (`brew install ffmpeg`, `apt install ffmpeg`) |
| `SPEC_PARSE` | the spec must be one JSON object; check quotes and brackets |
| `SPEC_INVALID` with a `path` | fix that field; the `hint` names the allowed values |
| warning "not a whole number of beats" | write the length as `"4b"` or set `"snap": true` |
| text clipped or overlapping in the contact sheet | shorter words, fewer lines, or a smaller `size` / `weight` |
| audit fails `motion_ratio` or `longest_hold` | shorter scenes, a busier transition, an extra scene |
| audit fails `loudness` / `true_peak` | re-render; with your own track, lower `music.gain` |
| render slow or memory-bound | `quality: "draft"` while iterating; fewer `workers` |
| MCP client times out on `render` | raise its tool timeout to 15 minutes (a 15 s reel renders in a few minutes) or use `preview` first |
| render interrupted | run it again: finished chunks resume |

## Limits

- A 15-second 1080x1920 / 30 fps reel at `standard` takes about 1-3 minutes on a 10-core laptop
  (4-13x the video length; more on a busy machine). `draft` is faster; use `preview` while iterating.
- Pure 2D/2.5D motion graphics: no footage generation, no lip-sync, no voice-over. Footage you
  supply plays in a `clip` scene.
- Optional character swap: `kstudio swap` / the MCP tool `swap` / a clip's `source.swap` send a video
  and a character image to a ComfyUI server you run on an NVIDIA GPU with Openjutsu's workflows
  (`KSTUDIO_COMFY_URL`). Without one they return `BACKEND_UNREACHABLE` (exit 7) and a clip plays the
  input video. Nothing else needs it. See `docs/SWAP.md`.
- Do not post or upload results anywhere without the user's explicit approval.
