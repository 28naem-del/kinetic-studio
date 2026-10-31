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
| `preview <spec> [-o dir] [--at 1.2,3] [--preset] [--scale] [--quality]` | stills + contact sheet (default dir `<project>/out/preview`) | `stills[]` {t, label, path}, `contact_sheet`, `out_dir`, `seconds`, `summary`, `warnings[]` |
| `render <spec> [-o out.mp4] [--preset] [--fps] [--quality draft\|standard\|high] [--4k] [--workers] [--fresh] [--snap [beat\|8th\|16th\|bar]] [--no-audit] [-q]` | final MP4 (parallel, resumable) | the build report: `output`, `duration`, `frames`, `size`, `fps`, `render`, `audio`, `mux`, `audit`, `delivery`, `pass`, `report_path`, `warnings[]` |
| `audit <video> [--mode m] [--fps N] [--frames N] [--min-motion x] [--max-hold s]` | quality audit of supported video inputs | `pass`, `checks{name: {ok, value, want}}`, ... |
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
{"ok": true, "command": "validate", "version": "0.2.0", "exit_code": 0,
 "valid": true, "spec_path": "/abs/spec.json", "errors": [], "warnings": [],
 "summary": {"mode": "promo", "duration": 15.0, "bpm": 112.0, "beats": 28.0, "scenes": 4, "transitions": 3}}
```

The command's result keys sit next to the four envelope keys (if a result ever needs one of the
reserved names `ok`, `command`, `version`, `exit_code`, `error`, the whole result moves under `result`).

Failure adds `error`; whatever the command produced before failing stays in the object (for example
an audit failure still carries `output` and `audit`):

```json
{"ok": false, "command": "validate", "version": "0.2.0", "exit_code": 3, "valid": false,
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
* Completed `render` / `audit` reports with failed quality checks add `failed_checks[]`; early audit
  input rejection may return an error without a completed audit report. `MISSING_TOOL` adds
  `missing[]`; usage errors add `usage`.

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
| 5 | AUDIT_FAILED | quality audit failed or audit input was rejected | `AUDIT_FAILED` |
| 6 | MISSING_TOOL | ffmpeg, ffprobe, libx264 or node is not installed | `MISSING_TOOL` |
| 7 | BACKEND_UNREACHABLE | optional swap backend unconfigured, unreachable, or not a ComfyUI API | `BACKEND_UNREACHABLE` |
| 130 | INTERRUPTED | Ctrl-C; re-run the same command to resume from finished chunks | `INTERRUPTED` |

Ctrl-C during `render` stops the workers, keeps every finished 2-second chunk and exits 130 with
`error.code` `INTERRUPTED`; the same command then resumes from the finished chunks.

Usage errors are raised as `SystemExit(2)` (argparse behaviour) after the envelope is printed, so the
process exit code is 2 either way.

## Audit input policy and measurement basis

The CLI, render-time audit, MCP `audit` and Python `kstudio.audit.audit` use
**`reference-1080-v2`** for motion and black-frame sampling. This is a supported-input policy,
not a claim that every decodable video can be audited.

| Boundary | Supported input / analysis |
|---|---|
| Coded video dimensions | Integer short edge **>=144 px**, long edge **<=4096 px**, total **<=9,000,000 pixels** |
| Analysis grid | Even axes **16..512 px**, total **<=65,536 pixels**; neither axis may exceed the corresponding source axis |
| Display geometry | Square pixels (`1:1` SAR); absent, `N/A` or `0:1` SAR is treated as unspecified square pixels. Explicit non-unit SAR is unsupported |
| Rotation | Identity only (0 modulo 360 degrees), checked in rotate tags and display-matrix rotation metadata; decoder autorotation is disabled |
| Decoded sequence | At least three complete frames and a successful decoder exit |

The grid is one-eighth of a 1080-short-edge reference, rounded down to even dimensions, and
FFmpeg samples the source directly onto it without upscaling. For example, 720x1280,
1080x1920 and 2160x3840 all use 134x240; 1920x1080 uses 240x134; 1080x1080 uses 134x134.
Meeting these limits does not waive codec, audio or other quality checks.
The coded-size limits alone are not sufficient: extreme aspect ratios can exceed the analysis
bounds. For example, 1080x4096 is rejected by the analysis-area limit.

Rejected geometry raises `AUDIT_FAILED` (CLI exit **5**), never a successful skip or an automatic
fallback resize. Machine-readable error reasons distinguish:

- `unsupported_geometry`: invalid/tiny/oversized coded dimensions or an analysis-grid bound violation.
- `unsupported_display_geometry`: explicit non-unit SAR, nonidentity rotation or invalid display metadata.
- `invalid_decode`: a detected nonzero decoder exit, partial final frame or fewer than three decoded frames.

Geometry/display validation follows metadata probing and precedes timing/frame decoding. These
specific errors do not imply that every malformed-file or external-tool failure has the same reason.
MCP/function callers receive `error.code` and `error.reason`; the one-shot MCP command retains its
existing success/failure exit convention, rather than the CLI's numeric exit table.

Reports identify the basis in `motion.motion_basis` and `motion.black_basis` and give the actual grid
in `motion.motion_analysis_size`. The denoising equation and thresholds are unchanged: motion gate
**1.75**, raw still threshold **0.6**, black threshold **10.0**, and the existing per-mode acceptance
thresholds. Supported short-edge 1080/default master grids retain their prior measurement basis.
For non-1080 inputs, sampled motion, hold and black-frame metrics can change; compare reports with
the basis recorded and retain historical results. This is not universal perceptual calibration or
a guarantee of identical results for arbitrary resolutions, crops or near-black images.

The bound is **per-frame analysis geometry**, not a global duration, runtime or total-memory limit.
Decoder work and accumulated per-frame metrics still depend on the input and its duration.
An audit PASS does not replace visual/readability/audio review or establish release readiness.

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

## Extension hooks

Two optional hooks let an add-on module named `kstudio.swap` extend the command line and the MCP
server without changes to either. The add-on is optional: when `import kstudio.swap` raises
`ImportError` (not installed, or one of its own dependencies is missing) both hooks do nothing and the
built-in commands and tools are unchanged.

**Command line.** After the built-in subcommands are added, `build_parser()` imports `kstudio.swap`
and calls `swap.register(subparsers)` with the argparse subparsers object. A subcommand added there
works like a built-in one when it sets `fn` to a function that takes the parsed arguments and returns
a dict:

```python
def register(subparsers):
    s = subparsers.add_parser("swap", help="...")
    s.add_argument("--video", required=True)
    s.add_argument("--json", action="store_true")   # so --json also works after the command
    s.set_defaults(fn=cmd_swap)                     # cmd_swap(args) -> dict (the JSON result keys)
```

The returned dict becomes the `--json` envelope; raising an error class from `kstudio/exitcodes.py`
(for example `BackendUnreachable`, exit 7) gives the matching exit code and `error.code`. An exception
inside `register` is reported as one line on stderr and the built-in commands keep working.

**MCP server.** When `kstudio.mcp_server` is imported it appends the add-on's tools to `TOOLS`:

* `swap.MCP_TOOLS`: a list of tool definitions, either `kstudio.mcp_server.Tool` objects or dicts with
  `name`, `title`, `description`, `properties` (or a full `inputSchema`), `required`, and optional
  `read_only`, `destructive`, `idempotent` (all default false) and `output` (extra output-schema
  properties).
* `swap.MCP_HANDLERS`: `{tool name: handler(arguments, progress) -> dict}`. A dict tool without a
  handler is skipped. A `Tool` object keeps its own handler unless this mapping names it.

Add-on tools are listed by `tools/list` and `--list-tools`, dispatched by `tools/call`, `--call` and
`run_tool()` with the same argument checking and error shape as the built-in tools. A tool whose name
is already taken is skipped with a note on stderr. Run `python docs/tools/generate.py` after adding
tools so `docs/tools/` lists them too.
