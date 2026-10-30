# Tool definitions

Kinetic Studio's eight tools, described once in `kstudio/mcp_server.py` (`TOOLS`) and exported here
for clients that do not speak MCP. Do not edit the JSON files by hand.

| file | for |
|---|---|
| `mcp-tools.json` | the MCP `tools/list` result, exactly as the server returns it |
| `functions.json` | function tools in the flat form `{"type": "function", "name", "description", "parameters", "strict": true}` |
| `functions-chat.json` | the same tools in the nested form `{"type": "function", "function": {...}}` used by chat-completions style APIs |
| `tools.schema.json` | a plain manifest: name, description, input and output JSON Schema, hints and the ways to invoke the server |
| `generate.py` | writes the four files from `TOOLS` and `/llms-full.txt` from the docs; `--check` fails when any is out of date; `--scenes` rewrites `docs/SCENES.md` from the scene registry |

```bash
python docs/tools/generate.py           # regenerate after changing a tool or the skill and docs
python docs/tools/generate.py --check   # what the test suite runs (tests/test_mcp.py)
```

## Names and arguments

Function-calling exports name each tool `kstudio_<tool>` (for example `kstudio_render`) so they do
not collide with other tools; MCP uses the bare names. The dispatcher accepts both.

The function schemas are in strict form: every property is listed as required and the optional ones
accept `null`. The server treats `null` as "not given", so a model can fill every field.

## Dispatching calls

```python
import json
from kstudio.mcp_server import run_tool

tools = json.load(open("docs/tools/functions.json"))   # pass to your model API as its tool list

def on_tool_call(name: str, arguments: str) -> str:
    """name: "kstudio_validate_spec" or "validate_spec"; arguments: the JSON text from the model."""
    return json.dumps(run_tool(name, arguments))           # one JSON object with an "ok" field
```

From a shell or another language: `python -m kstudio.mcp_server --call <tool> '<json>'` prints the
same JSON object on stdout and exits 0 when `ok` is true.

## The tools

| tool | does | writes files |
|---|---|---|
| `list_modes` | modes with theme, music, transition, example scenes; presets, quality, frame rates, time units | no |
| `list_scenes` | the scene types, or one type's parameters | no |
| `get_schema` | the spec JSON Schema (optionally only some scene types), mode defaults and the mode's example spec | no |
| `validate_spec` | checks a spec file or inline JSON; errors carry a JSON Pointer path, message and hint | no |
| `new_spec` | starts a project directory from a mode's example, or from your spec JSON | yes |
| `preview` | renders stills and a contact sheet (returned as an image) in seconds | yes |
| `render` | renders, mixes and audits the MP4, with progress notifications | yes |
| `audit` | re-checks any MP4 (motion, holds, loudness, true peak, frame count) | yes (report) |
