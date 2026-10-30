# Kinetic Studio with Gemini CLI

Verified against the official docs on 2026-10-30:

- https://geminicli.com/docs/tools/mcp-server/

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

## Add the server

In `~/.gemini/settings.json` (every project) or `.gemini/settings.json` (this project):

```json
{
  "mcpServers": {
    "kinetic-studio": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"],
      "timeout": 900000
    }
  }
}
```

`timeout` is in milliseconds (default 600000); a long render needs the headroom. `gemini mcp add`
writes the same entry from the command line.

From a clone instead of uvx (after `bash install.sh` in the clone): use
`/path/to/kinetic-studio/.venv/bin/python` as the command, `["-m", "kstudio.mcp_server"]` as the
arguments, and set the environment variable `PYTHONPATH=/path/to/kinetic-studio`.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.
