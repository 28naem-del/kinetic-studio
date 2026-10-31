# Security policy

## Supported versions

| version | supported |
|---|---|
| 0.1.x | yes |

The current package version is 0.2.0. Its security-support status has not yet been declared;
that policy remains an owner decision. The existing 0.1.x policy above is unchanged.

## Reporting a vulnerability

Please **do not** open a public issue. Report it privately via
[GitHub security advisories](https://github.com/28naem-del/kinetic-studio/security/advisories/new).
Include the version, steps to reproduce and the impact. We aim to reply within 7 days.

## Scope notes

- The core renderer runs locally without network calls after installation. Optional swap uploads
  the chosen video and character image to the ComfyUI server you configure and downloads its result.
  A clip with `source.swap` can trigger this during rendering when a backend is configured;
  review [the swap data boundary](docs/SWAP.md) and only enable it for inputs you agree to share.
- The optional HTTP MCP transport is unauthenticated. Bind it to localhost by default; expose it
  only to trusted clients on a trusted network, never directly to the public internet.
- Specs can reference local files (`music.file`, screenshots, images) and the engine invokes
  `ffmpeg`/`ffprobe` as subprocesses with argument lists (no shell). Treat specs from untrusted
  sources like any other file you open: they can read media paths you point them at.
- Keep FFmpeg up to date; media-decoding vulnerabilities in FFmpeg are outside this project but
  affect anything that decodes untrusted audio or images.
