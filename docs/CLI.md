# Command line reference

`kinetic-studio` (short alias `kstudio`) is the whole engine behind one command. Every command can be
driven blind by a script or an agent framework: pass `--json` and stdout carries exactly one JSON
object, logs and progress go to stderr, and the exit code says what happened.

```bash
uv tool install kinetic-studio            # or: pipx install kinetic-studio / pip install kinetic-studio
uvx --from kinetic-studio kinetic-studio --help
kinetic-studio doctor                     # ffmpeg / ffprobe / libx264 / skia check
```

The wheel ships the example projects (`kinetic-studio new <mode> <dir>` copies one) and the fonts.
Optional extra: `kinetic-studio[mcp]` (the `kstudio-mcp` server needs no third-party package; the extra
keeps the install line stable). `kstudio-mcp` is installed with the package.

## Commands

| Command | What it does | Main `--json` keys |
|---|---|---|
| `modes` | list the five modes | `modes[]` {name, description, theme, music{style,bpm}, transition, example} |
| `scenes [type] [-v]` | scene types and their fields | `count`, `scenes[]` {type, description, fields} |
| `new <mode> <dir> [--force]` | start a project from the mode example | `project_dir`, `spec_path`, `files[]`, `next[]` |
| `schema [--mode m] [--types a,b] [-o file] [--compact]` | spec JSON Schema (draft 2020-12) | `schema`, `scene_types[]`, `scene_type_count`, `path` |
| `validate <spec> [--fix-hints] [--strict] [--no-build]` | schema check + dry build | `valid`, `errors[]`, `warnings[]`, `summary`, `fix_prompt` |
| `brief --mode m --text "..." [--duration s] [--preset p] [--all-scenes] [-o file]` | prompt pack, no model call | `system`, `user`, `prompt`, `schema`, `examples[]`, `rules`, `scene_types[]`, `response_format`, `next[]` |
| `info <spec> [-v]` | resolved timeline + beat-grid lint | `mode`, `duration`, `bpm`, `scenes[]`, `transitions[]`, `lint[]` |
| `preview <spec> [--at 1.2,3] [--preset] [--scale] [--quality]` | stills + contact sheet | `stills[]`, `contact_sheet`, `out_dir`, `seconds`, `warnings[]` |
| `render <spec> [-o out.mp4] [--preset] [--fps] [--quality draft\|standard\|high] [--4k] [--workers] [--fresh] [--snap [beat\|8th\|16th\|bar]] [--no-audit] [-q]` | final MP4 (parallel, resumable) | the build report: `output`, `duration`, `frames`, `size`, `fps`, `render`, `audio`, `mux`, `audit`, `delivery`, `pass`, `report_path`, `warnings[]` |
| `audit <video> [--mode m] [--fps N] [--frames N] [--min-motion x] [--max-hold s]` | quality audit of any video | `pass`, `checks{name: {ok, value, want}}`, ... |
| `mix <spec> -o out.wav` | soundtrack only | `output`, `final{lufs,true_peak}`, `sfx_count`, `music` |
| `beats <audio>` | tempo and beat grid of a music file | `bpm`, `offset`, `beats`, `confidence`, `first_beats[]` |
| `sfx <dir>` | export the synthesized SFX kit | `dir`, `count`, `files[]` |
| `doctor` | dependency check | `python`, `platform`, `checks{tool: {ok, required, path, version}}`, `missing[]` |
| `exit-codes` | the table below | `exit_codes[]` {exit_code, name, meaning, error_codes[]} |

`<spec>` is a `spec.json` / `spec.jsonc` / `spec.js` path, a project directory, inline JSON
(`'{"scenes": [...]}'`) or `-` for JSON on stdin. `--json` works before or after the command
(`kinetic-studio --json render ...` or `kinetic-studio render ... --json`); `KSTUDIO_JSON=1` turns it on
for every call.

## JSON envelope

Success:

```json
{"ok": true, "command": "validate", "version": "0.1.0", "exit_code": 0,
 "valid": true, "spec_path": "/abs/spec.json", "errors": [], "warnings": [],
 "summary": {"mode": "promo", "duration": 15.0, "bpm": 112.0, "beats": 28.0, "scenes": 4, "transitions": 3}}
```

The command's result keys sit next to the four envelope keys (if a result ever needs one of the
reserved names `ok`, `command`, `version`, `exit_code`, `error`, the whole result moves under `result`).

Failure adds `error`; whatever the command produced before failing stays in the object (for example
an audit failure still carries `output` and `audit`):

```json
{"ok": false, "command": "validate", "version": "0.1.0", "exit_code": 3, "valid": false,
 "errors": [{"path": "/scenes/1/type", "message": "unknown scene type \"titel\"",
             "hint": "did you mean \"title\"? scene types: bars, brand, ...", "keyword": "enum",
             "severity": "error", "value": "titel"}],
 "error": {"code": "SPEC_INVALID", "message": "1 spec error(s); first at /scenes/1/type: ...",
           "path": "/scenes/1/type", "hint": "...", "errors": [ ... ]}}
```

* `error.code` is one of the error codes in the table below; `error.path` is a JSON Pointer
  (RFC 6901) into the spec for spec errors (`""` = the whole document).
* Each item of `errors[]` / `warnings[]`: `path`, `message`, `hint`, `keyword` (the schema keyword or
  `build` for dry-build failures such as an unresolvable time), `severity`, optional `value`, `inferred`.
* `render` / `audit` failures add `failed_checks[]`; `MISSING_TOOL` adds `missing[]`; usage errors add
  `usage`.

stderr in `--json` mode carries JSON lines for long commands:

```
{"event": "log", "message": "kinetic: 13.12s, 394 frames @ 1080x1920 30fps [draft], 7 chunks, 3 workers ..."}
{"event": "progress", "frames": 120, "total": 394, "fps": 12.7, "eta": 21.5}
```

Anything the engine or ffmpeg prints is redirected to stderr, so stdout stays a single JSON object.

## Exit codes

Defined once in `kstudio/exitcodes.py`; `kinetic-studio exit-codes --json` prints them.

| Exit | Name | Meaning | `error.code` values |
|---|---|---|---|
| 0 | OK | success | |
| 1 | INTERNAL | a bug; re-run with `KSTUDIO_DEBUG=1` for the traceback | `INTERNAL_ERROR` |
| 2 | USAGE | bad flags or arguments, unknown mode, directory not empty, file not found | `USAGE`, `MODE_UNKNOWN`, `DIR_NOT_EMPTY`, `FILE_NOT_FOUND` |
| 3 | SPEC_INVALID | parse error, schema violation, unresolvable time, unknown scene type | `SPEC_INVALID`, `SPEC_NOT_FOUND`, `SPEC_PARSE` |
| 4 | RENDER_FAILED | the spec was valid but rendering, encoding or muxing failed | `RENDER_FAILED` |
| 5 | AUDIT_FAILED | the video was written but the quality audit did not pass | `AUDIT_FAILED` |
| 6 | MISSING_TOOL | ffmpeg, ffprobe, libx264 or node is not installed | `MISSING_TOOL` |
| 7 | BACKEND_UNREACHABLE | reserved for a pluggable render backend; not raised yet | `BACKEND_UNREACHABLE` |
| 130 | INTERRUPTED | Ctrl-C; re-run the same command to resume from finished chunks | `INTERRUPTED` |

Usage errors are raised as `SystemExit(2)` (argparse behaviour) after the envelope is printed, so the
process exit code is 2 either way.

## Schema

`kinetic-studio schema` prints the JSON Schema of a spec (draft 2020-12). It is generated at run time,
not hand-written: the top level and shared objects (time expressions, transitions, camera, music,
post, audit, markers, captions) come from the engine tables, and each scene type gets a
`$defs/scene.<type>` built from the scene class (its `defaults`, the keys its code reads, time and
beat-count parameters, and parameters the code accepts in several shapes). Scenes are a `oneOf`
discriminated by `type` (`"x-discriminator": "type"`), and `x-scene-types` lists every type.
Types inferred only from a default value carry `"x-inferred": true`.

```bash
kinetic-studio schema | python -m json.tool > spec.schema.json
kinetic-studio schema --mode promo        # only the promo + common scene types
```

`validate` uses the same schema with a built-in checker (no extra dependency), then dry-builds the
timeline to catch what a schema cannot: unknown markers, unresolvable beat expressions, zero-length
scenes. Unknown fields are warnings (errors with `--strict`). `render` and `preview` run the same check
first and stop with exit 3 before any frame is drawn.

## Brief to spec loop (any LLM, no model call inside)

```bash
kinetic-studio brief --mode promo --duration 15 --text "launch teaser for Inkwell, a notes app" --json > pack.json
# send pack.system as the system prompt and pack.user as the user message to any model;
# pack.response_format is a ready json_schema response format for APIs that support one
kinetic-studio validate spec.json --json --fix-hints   # exit 0: render; exit 3: send fix_prompt back
kinetic-studio preview spec.json --json
kinetic-studio render spec.json --json -o out.mp4
```

The pack contains the system prompt (output contract, the mode's beat-unit rules, the schema and two
complete example specs: the mode's own and a contrasting mode), the user message (the brief plus the
length rounded to whole bars at the mode's BPM), and the same parts as separate keys. `--text -`
reads the brief from stdin; without `--json` the plain-text prompt is printed.
