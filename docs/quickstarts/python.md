# Kinetic Studio with plain Python

Verified against the official docs on 2026-10-30:

- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

No agent framework needed. Three ways, from the simplest.

## 1. Call the tools in-process

```bash
pip install git+https://github.com/28naem-del/kinetic-studio
```

```python
from kstudio.mcp_server import run_tool

r = run_tool("new_spec", {"mode": "kinetic", "dir": "friday"})
r = run_tool("validate_spec", {"spec_path": r["spec_path"]})
assert r["ok"], r["errors"]
r = run_tool("render", {"spec_path": "friday", "quality": "draft"})
print(r["output"], r["pass"], r["failed_checks"])
```

## 2. Function calling with any model API

`docs/tools/functions.json` (flat function tools) and `docs/tools/functions-chat.json` (nested)
hold strict function definitions named `kstudio_<tool>`; `docs/tools/tools.schema.json` is a plain
manifest for other APIs. The dispatcher accepts the prefixed names and the JSON-text arguments a
model returns:

```python
import json
from kstudio.mcp_server import run_tool

TOOLS = json.load(open("docs/tools/functions.json"))

def dispatch(name: str, arguments: str) -> str:
    return json.dumps(run_tool(name, arguments))  # send this back as the tool result
```

## 3. Speak MCP over stdio yourself

```python
import json, subprocess

p = subprocess.Popen(["kstudio-mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

def rpc(i, method, params=None):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n")
    p.stdin.flush()
    while True:
        msg = json.loads(p.stdout.readline())
        if msg.get("id") == i:
            return msg
        print("progress:", msg["params"].get("message"))  # notifications/progress

rpc(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "me", "version": "1"}})
p.stdin.write('{"jsonrpc": "2.0", "method": "notifications/initialized"}\n')
print(rpc(2, "tools/call", {"name": "validate_spec", "arguments": {"spec_path": "friday"}})["result"]["structuredContent"])
print(rpc(3, "tools/call", {"name": "render", "arguments": {"spec_path": "friday"}, "_meta": {"progressToken": "r"}}))
```

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.
