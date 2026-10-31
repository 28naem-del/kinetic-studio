# Footage and the optional character swap

Kinetic Studio renders on the CPU and never needs a GPU. Two features deal with video footage:

- the **`clip` scene** plays a local video file inside any mode, with titles, callouts, karaoke or
  counters drawn on top. It needs nothing beyond ffmpeg;
- the **swap client** (`kstudio swap`, the MCP tool `swap`, and `source.swap` on a clip scene) is
  optional. It sends a video and a character image to a ComfyUI server that runs
  [Openjutsu](https://github.com/D3voz/openjutsu)'s workflows and downloads the result. Nothing in
  Kinetic Studio depends on it.

## The clip scene

```json
{ "type": "clip", "src": "footage.mp4", "in": "2b", "dur": "4b", "fit": "cover",
  "over": [ { "type": "title", "kicker": "Clip scene", "title": "Your footage, on the beat" } ] }
```

| key | meaning |
|---|---|
| `src` | video file, relative to the spec |
| `in` / `out` | trim points in the source, seconds or beats (`"2b"`); snapped to the grid with `"snap"` |
| `speed`, `end` | playback rate; `hold` freezes the last frame, `loop` wraps |
| `fit`, `focus`, `zoom` | `cover` (crop about `focus`, slow push from `zoom[0]` to `zoom[1]`) or `contain` (letterboxed over a blurred copy, `backdrop`) |
| `grade`, `blur`, `opacity` | a grade preset or dict (as in the spec's `grade`), a blur in px at 1080, a fade |
| `scrim` | a gradient of the theme background that keeps type legible (0 turns it off) |
| `box`, `radius` | picture-in-picture: `[x, y, w, h]` as fractions of the frame |
| `over` | scenes drawn over the footage, each with an optional scene-local `at` and `dur` |

The footage is decoded once by ffmpeg, cropped to what the frame shows and scaled to the output size
(never upscaled), then kept in a disk cache (`$KSTUDIO_CLIP_CACHE`, default
`<tmp>/kstudio-clips`, trimmed to `$KSTUDIO_CLIP_CACHE_MB`, default 1024) keyed by the file, the trim,
the size and the look. Render workers share it, and each output frame shows the nearest source
frame, so footage at any frame rate plays at real speed. A missing file draws a "missing footage"
note instead of failing the render. See `examples/clip/` (its footage comes from
`examples/clip/make_footage.sh`, pure ffmpeg lavfi).

## What the swap backend needs

An NVIDIA machine running [ComfyUI](https://github.com/comfyanonymous/ComfyUI) with Openjutsu's
custom node packs and models installed. Follow the installation steps in the
[Openjutsu README](https://github.com/D3voz/openjutsu#readme); Kinetic Studio does not install
ComfyUI, node packs or model weights and never downloads them. Then point Kinetic Studio at the
server:

```bash
export KSTUDIO_COMFY_URL=http://gpu-box:8188
```

The client uses ComfyUI's plain HTTP API: `GET /object_info` (are the nodes installed?),
`POST /upload/image` (the two inputs), `POST /prompt`, `GET /queue` and `GET /history/<id>` (polled)
and `GET /view` (the finished MP4). Only the two input files and the patched workflow leave the
machine, and only to the URL you set.

## Running a swap

```bash
kstudio swap --video dance.mp4 --character hero.png --json
kstudio swap --video street.mp4 --character ref.png --mode object --target "jacket" --prompt "a red raincoat"
kstudio swap --spec my-project            # make every source.swap in a project's clip scenes
```

- `--mode simple` (default): the character replaces a person. `--prompt` names who is replaced
  (empty: the workflow's describer picks the main person); `--target` is what is tracked (`person`).
- `--mode object`: change what is tracked (`--target`, default `hair`) into `--prompt`.
- `--seed`, `-o OUT` (default `./out/swap/<key>.mp4`), `--url`, `--timeout`, `--poll`, `--force`.

The output path is keyed by the bytes of both inputs and every setting, so a repeated call returns
the existing file (`"cached": true`). A `<out>.swap.json` next to it records the request.

In a spec, a clip scene can take its footage from a swap:

```json
{ "type": "clip", "dur": "8b",
  "source": { "swap": { "video": "dance.mp4", "character": "hero.png", "mode": "simple" } } }
```

The swapped file goes to `<project>/out/swap/<key>.mp4` (or `$KSTUDIO_SWAP_DIR`). When it is not
there yet, the render makes it if `KSTUDIO_COMFY_URL` is set; otherwise, or if the backend fails, the
clip plays the input video and prints one warning, so a render never stops for the backend. Run
`kstudio swap --spec <project>` first to see backend errors as JSON before rendering.

## Errors

| exit | code | when |
|---|---|---|
| 7 | `BACKEND_UNREACHABLE` | `KSTUDIO_COMFY_URL` is unset (`reason: not_configured`); the server cannot be reached, a proxy in front of it answers 502, 503 or 504, or a reply breaks off (while a job runs: five polls in a row) (`reason: unreachable`); or the URL is not ComfyUI's JSON API: a web page, a 404 or login page, JSON that is not ComfyUI's, or a port that does not speak HTTP such as ssh or TLS (`reason: not_comfyui`) |
| 6 | `MISSING_NODES` | the server lacks node types the workflow uses: install Openjutsu's node packs and restart ComfyUI |
| 4 | `UPLOAD_FAILED`, `WORKFLOW_REJECTED`, `SWAP_FAILED`, `NO_OUTPUT`, `DOWNLOAD_FAILED`, `SWAP_TIMEOUT` | ComfyUI itself refused or failed the job; the message carries its error or HTTP status |
| 2 | `FILE_NOT_FOUND`, `USAGE` | an input file is missing or the arguments are wrong |

With `--json`, stdout holds one JSON object (`ok`, `exit_code`, then the result fields or `error`) and progress
events go to stderr as JSON lines.

## Credits and testing

The two workflows in `kstudio/workflows/` are Openjutsu's, unchanged, under the MIT licence (see
`THIRD_PARTY_NOTICES.md`). The client is tested against an in-process mock of the ComfyUI HTTP API
(submit, queue, history, download, rejection, the unreachable case, a server that answers with a web
page or an HTML 404, a proxy answering 502/503/504, a raw non-HTTP port, and replies cut off mid-body).
While a job runs, a dropped or cut-off `/history` poll counts as a hiccup; the client gives up after
five in a row. It has not been run here against a real GPU backend: no NVIDIA machine with Openjutsu's node packs
was available.
