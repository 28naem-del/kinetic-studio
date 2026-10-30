# Kinetic Studio with Continue

Verified against the official docs on 2026-10-30:

- https://docs.continue.dev/customize/deep-dives/mcp

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

## Add the server

Create `.continue/mcpServers/kinetic-studio.yaml` in your workspace:

```yaml
name: Kinetic Studio
version: 0.0.1
schema: v1
mcpServers:
  - name: kinetic-studio
    type: stdio
    command: uvx
    args: ["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"]
```

Continue also accepts a JSON file in the same folder in the `{"mcpServers": {...}}` format other tools use.
MCP tools are available in agent mode.

From a clone instead of uvx (after `bash install.sh` in the clone): use
`/path/to/kinetic-studio/.venv/bin/python` as the command, `["-m", "kstudio.mcp_server"]` as the
arguments, and set the environment variable `PYTHONPATH=/path/to/kinetic-studio`.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.
