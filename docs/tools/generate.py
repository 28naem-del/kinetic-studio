"""Write the tool definitions in docs/tools/ from the one source of truth, kstudio.mcp_server.TOOLS.

    python docs/tools/generate.py           rewrite the files
    python docs/tools/generate.py --check   exit 1 if any file is out of date (the test suite runs this)
    python docs/tools/generate.py --scenes  also rewrite docs/SCENES.md from the scene registry

Files:
    mcp-tools.json          the MCP tools/list result, exactly as the server returns it
    functions.json          function tools, flat form ({"type": "function", "name", "parameters", "strict"})
    functions-chat.json     function tools, nested form ({"type": "function", "function": {...}})
    tools.schema.json       a plain manifest: name, description, input and output JSON Schema per tool
    ../../llms-full.txt     llms.txt followed by the skill, tool docs, quickstart index, scenes and architecture

Function-calling exports use `kstudio_`-prefixed names so they do not collide with other tools; the
dispatcher (kstudio.mcp_server.run_tool) accepts both forms. Strict function schemas list every property
as required and make the optional ones nullable; the server treats null as "not given".
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from kstudio.mcp_server import SERVER_NAME, TOOL_PREFIX, TOOLS  # noqa: E402


def _nullable(p: dict[str, Any]) -> dict[str, Any]:
    p = dict(p)
    t = p.get("type")
    types = [t] if isinstance(t, str) else list(t or [])
    if types and "null" not in types:
        p["type"] = types + ["null"]
    if "enum" in p and None not in p["enum"]:
        p["enum"] = list(p["enum"]) + [None]
    return p


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema -> the strict function-calling subset: every object closed, every property required,
    optional properties nullable."""
    s = copy.deepcopy(schema)
    t = s.get("type")
    if t == "object" or (isinstance(t, list) and "object" in t):
        props = s.get("properties", {})
        req = set(s.get("required", []))
        for k, p in list(props.items()):
            p = strict_schema(p)
            props[k] = p if k in req else _nullable(p)
        s["properties"] = props
        s["required"] = list(props)
        s["additionalProperties"] = False
    if "items" in s and isinstance(s["items"], dict):
        s["items"] = strict_schema(s["items"])
    return s


def build() -> dict[str, str]:
    mcp = {"tools": [t.describe() for t in TOOLS]}
    flat, nested, manifest = [], [], []
    for t in TOOLS:
        fn_name = TOOL_PREFIX + t.name
        params = strict_schema(t.input_schema())
        flat.append({"type": "function", "name": fn_name, "description": t.description, "parameters": params,
                     "strict": True})
        nested.append({"type": "function", "function": {"name": fn_name, "description": t.description,
                                                        "parameters": params, "strict": True}})
        manifest.append({"name": fn_name, "mcp_name": t.name, "title": t.title, "description": t.description,
                         "input_schema": t.input_schema(), "output_schema": t.output_schema(),
                         "annotations": {k: v for k, v in t.annotations().items() if k != "title"}})
    generic = {
        "$comment": "Written by docs/tools/generate.py from kstudio/mcp_server.py. Do not edit by hand.",
        "name": SERVER_NAME,
        "description": "Local motion-graphics video engine: JSON spec in, audited MP4 out.",
        "invoke": {
            "mcp_stdio": {"command": "python", "args": ["-m", "kstudio.mcp_server"]},
            "mcp_http": {"command": "python", "args": ["-m", "kstudio.mcp_server", "--http", "8765"],
                         "url": "http://127.0.0.1:8765/mcp"},
            "cli": "python -m kstudio.mcp_server --call <tool> '<json arguments>'",
            "python": "from kstudio.mcp_server import run_tool; run_tool(name, arguments)",
        },
        "tools": manifest,
    }

    def dump(obj: Any) -> str:
        return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"

    return {"mcp-tools.json": dump(mcp), "functions.json": dump(flat),
            "functions-chat.json": dump(nested), "tools.schema.json": dump(generic),
            "../../llms-full.txt": llms_full()}


LLMS_PARTS = ["SKILL.md", "docs/tools/README.md", "docs/quickstarts/README.md", "docs/SCENES.md",
              "docs/ARCHITECTURE.md"]


def llms_full() -> str:
    """llms.txt, then the documents it links to that an agent needs most, each under its path."""
    out = [(ROOT / "llms.txt").read_text(encoding="utf-8").rstrip()]
    for rel in LLMS_PARTS:
        out += ["", "---", "", f"<!-- {rel} -->", "", (ROOT / rel).read_text(encoding="utf-8").rstrip()]
    return "\n".join(out) + "\n"


FAMILIES = [("kinetic", "Kinetic type"), ("explainer", "Explainer"), ("promo", "Promo"), ("data", "Data"),
            ("social", "Social"), ("common", "Any mode"), ("clip", "Footage (any mode)")]


def _cell(v: Any) -> str:
    text = json.dumps(v, ensure_ascii=False)
    return "`" + (text if len(text) <= 60 else text[:57] + "...") + "`"


def scenes_md() -> str:
    """docs/SCENES.md from the scene registry, the transition table and the shared scene keys."""
    from kstudio.camera import CAMERA_KEYS
    from kstudio.mcp_server import run_tool
    from kstudio.scenes import REGISTRY
    from kstudio.scenes.base import GROUND_STYLES
    from kstudio.transitions import DIRS, TRANSITIONS, WIPE_SHAPES

    cat = run_tool("list_scenes", {"verbose": True})
    by_type = {c["type"]: c for c in cat["scenes"]}
    out = [
        "# Scene reference",
        "",
        f"{len(by_type)} scene types. Any scene works in any mode; the mode only sets theme, music, camera feel,",
        "default transition and audit thresholds. The same information is live from the `list_scenes` MCP tool",
        "and `kstudio scenes -v`; the full JSON Schema comes from `get_schema`. Regenerate this page with",
        "`python docs/tools/generate.py --scenes`.",
        "",
        "## Times and lengths",
        "",
        "Every time or length takes seconds (`1.5`, `\"1.5s\"`, `\"250ms\"`) or beats at the music tempo:",
        "`\"4b\"`, `\"1/2b\"`, `\"1bar\"`, `\"bar3\"` (start of bar 3), or a marker or scene id with an offset",
        "(`\"drop-2b\"`, `\"intro+1.25s\"`, `\"end-1b\"`). Write scene lengths in whole beats.",
        "",
        "## Keys every scene accepts",
        "",
        "| key | meaning |",
        "|---|---|",
        "| `type` | the scene type (below) |",
        "| `id` | a name other times can refer to (`\"drop\"`, `\"drop-2b\"`) |",
        "| `dur` | length, ideally whole beats (`\"4b\"`) |",
        "| `at` | start time; default: right after the previous scene |",
        "| `transition` | transition into this scene (see below); default: the mode's |",
        "| `transition_dur` | transition window length (`\"1/2b\"`) |",
        "| `transition_sfx` | `false` silences the transition sound (pick another with `\"sfx\"` in a transition dict) |",
        "| `ground` | backdrop colour: a theme colour name (default `bg`) or a hex colour |",
        f"| `ground_style` | {', '.join(f'`{g}`' for g in GROUND_STYLES)} |",
        f"| `camera` | overrides of the mode camera: {', '.join(f'`{k}`' for k in CAMERA_KEYS)} |",
        "",
        "## Transitions",
        "",
        "Set on the incoming scene: a name, a shorthand (`\"whip-left\"`, `\"wipe-circle\"`) or a dict such as",
        "`{\"kind\": \"wipe\", \"shape\": \"circle\", \"dur\": \"1b\", \"dir\": \"up\"}`. "
        f"Directions: {', '.join(f'`{d}`' for d in DIRS)}. Wipe shapes: {', '.join(f'`{w}`' for w in WIPE_SHAPES)}.",
        "",
        "| kind | window (beats) | sound at the cut |",
        "|---|---|---|",
    ]
    for name, t in TRANSITIONS.items():
        alias = f" (alias of `{t['alias']}`)" if t.get("alias") else ""
        out.append(f"| `{name}`{alias} | {t['beats']:g} | {t['sfx'] or '-'} |")
    for fam, title in FAMILIES:
        types = sorted(k for k, cls in REGISTRY.items() if cls.__module__.rsplit(".", 1)[-1] == fam)
        if not types:
            continue
        out += ["", f"## {title}", ""]
        for t in types:
            c = by_type[t]
            out += [f"### `{t}`", "", c["summary"], ""]
            if c.get("params"):
                out += ["| key | default |", "|---|---|"]
                out += [f"| `{k}` | {_cell(v)} |" for k, v in c["params"].items()]
                out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str]) -> int:
    files = build()
    if "--scenes" in argv:
        (ROOT / "docs" / "SCENES.md").write_text(scenes_md(), encoding="utf-8")
        print("wrote docs/SCENES.md")
    if "--check" in argv:
        stale = [n for n, text in files.items()
                 if not (HERE / n).exists() or (HERE / n).read_text(encoding="utf-8") != text]
        if stale:
            print(f"out of date: {', '.join(stale)}; run python docs/tools/generate.py", file=sys.stderr)
            return 1
        print(f"docs/tools and llms-full.txt in sync ({len(files)} files, {len(TOOLS)} tools)")
        return 0
    for n, text in files.items():
        (HERE / n).write_text(text, encoding="utf-8")
        print(f"wrote {(HERE / n).resolve().relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
