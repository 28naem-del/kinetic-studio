# Kinetic Studio with AutoGen and Microsoft Agent Framework

Verified against the official docs on 2026-10-30:

- AutoGen MCP tools: https://microsoft.github.io/autogen/stable/reference/python/autogen_ext.tools.mcp.html
- Agent Framework MCP tools: https://learn.microsoft.com/en-us/agent-framework/agents/tools/local-mcp-tools

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

## AutoGen (AgentChat)

```bash
pip install -U "autogen-agentchat" "autogen-ext[mcp]"
```

```python
import asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.tools.mcp import McpWorkbench, StdioServerParams

server = StdioServerParams(command="uvx", args=["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"], read_timeout_seconds=900)

async def main(model_client):  # any AutoGen chat-completion client
    async with McpWorkbench(server) as studio:
        agent = AssistantAgent("director", model_client=model_client, workbench=studio,
                               system_message="You make short videos with the kinetic-studio tools.")
        result = await agent.run(task="Make a 10-second kinetic reel that says SHIP IT FRIDAY and render it.")
        print(result.messages[-1].content)
```

## Microsoft Agent Framework (Python)

```bash
pip install agent-framework "mcp>=2.2.0,<3"
```

```python
from agent_framework import Agent, MCPStdioTool

async def main(client):  # any Agent Framework chat client
    async with (
        MCPStdioTool(name="kinetic-studio", command="uvx", args=["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"]) as studio,
        Agent(client=client, name="Director",
              instructions="You make short videos with the kinetic-studio tools.") as agent,
    ):
        print(await agent.run("Make a 10-second kinetic reel that says SHIP IT FRIDAY and render it.",
                              tools=studio))
```

From a clone instead of uvx (after `bash install.sh` in the clone): use
`/path/to/kinetic-studio/.venv/bin/python` as the command, `["-m", "kstudio.mcp_server"]` as the
arguments, and set the environment variable `PYTHONPATH=/path/to/kinetic-studio`.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.

## Notes

- `read_timeout_seconds` matters: a 15-second reel takes a few minutes to render at standard quality.
- Agent Framework also connects over HTTP: start `kstudio-mcp --http 8765` and use `MCPStreamableHTTPTool(name="kinetic-studio", url="http://127.0.0.1:8765/mcp")`.
