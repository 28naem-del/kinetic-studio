# Kinetic Studio with n8n

Verified against the official docs on 2026-10-30:

- https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.toolmcp/

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

n8n's MCP Client Tool node connects over HTTP, so run Kinetic Studio's HTTP transport on the machine
that should render:

```bash
uvx --from git+https://github.com/28naem-del/kinetic-studio kstudio-mcp --http 0.0.0.0:8765
```

## Add the tool

1. In an AI Agent node, add the **MCP Client Tool** sub-node.
2. Server transport: **HTTP Streamable**. Endpoint: `http://host.docker.internal:8765/mcp` (n8n in
   Docker on the same machine) or `http://<host>:8765/mcp`.
3. Authentication: None. Tools to include: All, or Selected (for example only `validate_spec` and
   `render`).

## Without the agent node

An **Execute Command** node can run one tool and hand its JSON to the next node:

```bash
python -m kstudio.mcp_server --call render '{"spec_path": "/data/reels/friday"}'
```

stdout is exactly one JSON object; the exit code is 0 when `ok` is true.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.

## Notes

- Kinetic Studio speaks streamable HTTP, not the deprecated SSE transport. If your n8n only offers an SSE endpoint, update n8n or use the Execute Command route.
- The HTTP transport has no authentication; keep it on localhost or a trusted network.
