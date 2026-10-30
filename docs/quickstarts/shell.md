# Kinetic Studio with plain shell and curl

Verified against the official docs on 2026-10-30:

- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

## One tool, one JSON result

```bash
alias ks='uvx --from git+https://github.com/28naem-del/kinetic-studio kstudio-mcp'
ks --list-tools | jq -r '.tools[].name'
ks --call new_spec '{"mode": "kinetic", "dir": "friday"}'
ks --call validate_spec '{"spec_path": "friday"}' | jq '.ok, .errors'
ks --call render '{"spec_path": "friday"}' | jq '{output, pass, failed_checks}'
```

stdout is one JSON object, progress goes to stderr, and the exit code is 0 when `ok` is true.

## Raw MCP over stdio

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"sh","version":"1"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"validate_spec","arguments":{"spec_path":"friday"}}}' \
  | ks | jq -c '{id, ok: .result.structuredContent.ok, tools: [.result.tools[]?.name]}'
```

## curl against the HTTP transport

```bash
ks --http 8765    # in another terminal: http://127.0.0.1:8765/mcp
M='"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}'
curl -s http://127.0.0.1:8765/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' -H 'Mcp-Method: tools/call' -H 'Mcp-Name: validate_spec' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"validate_spec\",\"arguments\":{\"spec_path\":\"friday\"},$M}}"
```

Add `"progressToken": "r"` inside `_meta` of a `render` call to receive progress as a server-sent event
stream that ends with the result.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.
