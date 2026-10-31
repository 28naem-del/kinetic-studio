# Kinetic Studio with Codex CLI

Verified against the official docs on 2026-10-30:

- https://learn.chatgpt.com/docs/extend/mcp?surface=cli (redirected from https://developers.openai.com/codex/mcp)

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

## Add the server

```bash
codex mcp add kinetic-studio -- uvx --from git+https://github.com/28naem-del/kinetic-studio kstudio-mcp
```

Or in `~/.codex/config.toml`:

```toml
[mcp_servers.kinetic-studio]
command = "uvx"
args = ["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"]
startup_timeout_sec = 120   # first start downloads dependencies (default 10)
tool_timeout_sec = 900      # a 15 s reel renders in a few minutes (default 60)
```

From a clone instead of uvx (after `bash install.sh` in the clone): use
`/path/to/kinetic-studio/.venv/bin/python` as the command, `["-m", "kstudio.mcp_server"]` as the
arguments, and set the environment variable `PYTHONPATH=/path/to/kinetic-studio`. In TOML: `env = { PYTHONPATH = "/path/to/kinetic-studio" }`.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.
