# Quickstarts

Kinetic Studio is a local MCP server, a JSON command line and a set of function-calling schemas, so
it plugs into any agent. There are 15 quickstart pages below. Every quickstart was checked against
that framework's current documentation on 2026-10-30; each page cites its reference URL.

The one line every MCP client needs:

```
uvx --from git+https://github.com/28naem-del/kinetic-studio kstudio-mcp
```

(from a clone: `python -m kstudio.mcp_server` with the clone on `PYTHONPATH`; HTTP instead of stdio:
add `--http 8765` and connect to `http://127.0.0.1:8765/mcp`).

## Frameworks (alphabetical)

- [AutoGen and Microsoft Agent Framework](autogen.md): Python, stdio
- [Claude Code](claude-code.md): CLI, stdio
- [Cline](cline.md): editor extension, stdio
- [Codex CLI](codex-cli.md): CLI, stdio
- [Continue](continue.md): editor extension, stdio
- [CrewAI (crew-style multi-agent)](crewai.md): Python, stdio
- [Cursor](cursor.md): editor, stdio
- [Dify](dify.md): platform, HTTP
- [Gemini CLI](gemini-cli.md): CLI, stdio
- [LangChain and LangGraph](langchain.md): Python, stdio
- [n8n](n8n.md): workflow automation, HTTP or Execute Command
- [OpenHands](openhands.md): CLI and web app, stdio or HTTP
- [Plain Python](python.md): no framework: in-process, function calling or raw MCP
- [Plain shell and curl](shell.md): one-shot JSON, raw stdio, curl over HTTP
- [Windsurf](windsurf.md): editor, stdio

## What every client gets

Nine tools are advertised: eight core tools (`list_modes`, `list_scenes`, `get_schema`,
`validate_spec`, `new_spec`, `preview`, `render` with progress notifications, and `audit`) plus
`swap`, which requires an optional configured ComfyUI backend. Swap uploads the chosen video and
character image to that server; see [the swap data boundary](../SWAP.md) before enabling it.
Each tool takes a JSON Schema-described object and returns one JSON object with an `ok` field.
Definitions for clients without MCP live in `docs/tools/`.

Both MCP protocol eras are served: the stateless 2026-07-28 revision (`server/discover`) and the
`initialize` handshake of 2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05.

## The skill

`skills/kinetic-studio/SKILL.md` is a portable skill (frontmatter plus instructions): the workflow,
beat-unit rules, the spec contract and per-mode writing rules. Copy the `skills/kinetic-studio/`
folder into whatever skills directory your agent reads (for example `.agents/skills/`), or paste the
file into the system prompt of agents without skill support.

## Timeouts

A 15-second 1080x1920 reel takes about 1-3 minutes at standard quality on a 10-core laptop (more
on a busy machine); the first start through uvx downloads
the engine. Raise the client's startup and tool timeouts where the quickstart says so, and use
`preview` (seconds) while iterating.
