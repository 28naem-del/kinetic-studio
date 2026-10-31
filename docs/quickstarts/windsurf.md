# Kinetic Studio with Windsurf

Verified against the official docs on 2026-10-30:

- https://docs.windsurf.com/windsurf/cascade/mcp (now redirects to https://docs.devin.ai/desktop/cascade/mcp)

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

## Add the server

Cascade -> MCP servers -> view raw config, which opens `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "kinetic-studio": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"]
    }
  }
}
```

Refresh the MCP list (or restart) after saving.

From a clone instead of uvx (after `bash install.sh` in the clone): use
`/path/to/kinetic-studio/.venv/bin/python` as the command, `["-m", "kstudio.mcp_server"]` as the
arguments, and set the environment variable `PYTHONPATH=/path/to/kinetic-studio`.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.

## Notes

- Windsurf's MCP documentation now redirects to the Devin Desktop pages, which keep MCP servers in `.devin/config.json` (project) or `~/.config/devin/config.json` (user) with the same `mcpServers` entry. Use whichever file your installed version reads.
