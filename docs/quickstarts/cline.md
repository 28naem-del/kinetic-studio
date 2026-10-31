# Kinetic Studio with Cline

Verified against the official docs on 2026-10-30:

- https://docs.cline.bot/mcp/configuring-mcp-servers

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

## Add the server

Open the MCP Servers panel, choose Configure, and add this to `cline_mcp_settings.json`
(the command-line Cline keeps it at `~/.cline/data/settings/cline_mcp_settings.json`):

```json
{
  "mcpServers": {
    "kinetic-studio": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"],
      "env": {},
      "disabled": false,
      "autoApprove": ["list_modes", "list_scenes", "get_schema", "validate_spec"]
    }
  }
}
```

`autoApprove` lists the read-only tools; `new_spec`, `preview` and `render` write files and still ask.

From a clone instead of uvx (after `bash install.sh` in the clone): use
`/path/to/kinetic-studio/.venv/bin/python` as the command, `["-m", "kstudio.mcp_server"]` as the
arguments, and set the environment variable `PYTHONPATH=/path/to/kinetic-studio`.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.
