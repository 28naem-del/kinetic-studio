# Security policy

## Supported versions

| version | supported |
|---|---|
| 0.1.x | yes |

## Reporting a vulnerability

Please **do not** open a public issue. Report it privately via
[GitHub security advisories](https://github.com/28naem-del/kinetic-studio/security/advisories/new).
Include the version, steps to reproduce and the impact. We aim to reply within 7 days.

## Scope notes

- Kinetic Studio runs entirely locally and makes no network calls.
- Specs can reference local files (`music.file`, screenshots, images) and the engine invokes
  `ffmpeg`/`ffprobe` as subprocesses with argument lists (no shell). Treat specs from untrusted
  sources like any other file you open: they can read media paths you point them at.
- Keep FFmpeg up to date; media-decoding vulnerabilities in FFmpeg are outside this project but
  affect anything that decodes untrusted audio or images.
