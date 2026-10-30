# Kinetic Studio with Dify

Verified against the official docs on 2026-10-30:

- https://docs.dify.ai/en/self-host/use-dify/workspace/tools

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

Dify connects to MCP servers over HTTP only, so run Kinetic Studio's HTTP transport on the machine
that should render:

```bash
uvx --from git+https://github.com/28naem-del/kinetic-studio kstudio-mcp --http 0.0.0.0:8765
```

## Add the server

Tools -> MCP -> Add MCP Server (HTTP):

| field | value |
|---|---|
| Server URL | `http://host.docker.internal:8765/mcp` (Dify in Docker on the same machine) or `http://<host>:8765/mcp` |
| Name | Kinetic Studio |
| Server identifier | `kinetic-studio` |

Dify imports the eight tools; use them from an Agent app or an Agent node in a workflow.

From a clone: `PYTHONPATH=/path/to/kinetic-studio /path/to/kinetic-studio/.venv/bin/python -m kstudio.mcp_server --http 0.0.0.0:8765`.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.

## Notes

- The HTTP transport has no authentication. Bind to `127.0.0.1` when Dify runs on the host, and only expose `0.0.0.0` on a trusted network.
- Paths in tool arguments (`dir`, `spec_path`, `output`) are paths on the machine running the server.
- Raise the tool timeout of the Agent node for `render`.
