# Kinetic Studio with OpenHands

Verified against the official docs on 2026-10-30:

- https://docs.openhands.dev/openhands/usage/cli/mcp-servers
- https://docs.openhands.dev/openhands/usage/settings/mcp-settings

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

## Command line

```bash
openhands mcp add kinetic-studio --transport stdio uvx -- --from git+https://github.com/28naem-del/kinetic-studio kstudio-mcp
```

This writes `~/.openhands/mcp.json`, which you can also edit by hand:

```json
{
  "mcpServers": {
    "kinetic-studio": { "command": "uvx", "args": ["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"] }
  }
}
```

## Web app

Customize -> MCP Servers -> Add custom server -> STDIO. Name `kinetic-studio`, command `uvx`, arguments
one per line: `--from`, `git+https://github.com/28naem-del/kinetic-studio`, `kstudio-mcp`.

From a clone instead of uvx (after `bash install.sh` in the clone): use
`/path/to/kinetic-studio/.venv/bin/python` as the command, `["-m", "kstudio.mcp_server"]` as the
arguments, and set the environment variable `PYTHONPATH=/path/to/kinetic-studio`.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.

## Notes

- The server needs ffmpeg wherever OpenHands runs it (inside its sandbox for the web app). Alternatively run `kstudio-mcp --http 8765` on the host and add it as a Streamable HTTP server. This binds to localhost. The transport has no authentication: use `--http 0.0.0.0:8765` only when sandbox access requires it and the network and clients are trusted; never expose it directly to the public internet.
