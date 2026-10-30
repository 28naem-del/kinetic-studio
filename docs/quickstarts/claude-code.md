# Kinetic Studio with Claude Code

Verified against the official docs on 2026-10-30:

- https://code.claude.com/docs/en/mcp

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

## Add the server

```bash
claude mcp add --transport stdio kinetic-studio -- uvx --from git+https://github.com/28naem-del/kinetic-studio kstudio-mcp
```

`-s project` writes it to `.mcp.json` so the whole team gets it; `-s user` makes it available in
every project. The equivalent `.mcp.json`:

```json
{
  "mcpServers": {
    "kinetic-studio": { "command": "uvx", "args": ["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"] }
  }
}
```

## Add the skill (optional)

Copy `skills/kinetic-studio/` from this repository into your project's or user's skills folder so the
agent loads the workflow and spec rules when a video is requested.

From a clone instead of uvx (after `bash install.sh` in the clone): use
`/path/to/kinetic-studio/.venv/bin/python` as the command, `["-m", "kstudio.mcp_server"]` as the
arguments, and set the environment variable `PYTHONPATH=/path/to/kinetic-studio`.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.

## Notes

- The first start downloads dependencies; if it times out, start with `MCP_TIMEOUT=120000 claude`.
- `get_schema` without `scene_types` returns a large schema; pass the scene types you need, or raise `MAX_MCP_OUTPUT_TOKENS`.
