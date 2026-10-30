# Kinetic Studio with LangChain and LangGraph

Verified against the official docs on 2026-10-30:

- https://docs.langchain.com/oss/python/langchain/mcp
- https://docs.langchain.com/oss/python/langchain/mcp/connections

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

```bash
pip install "langchain[mcp]"
```

```python
from langchain.agents import create_agent
from langchain.mcp import MCPAdapter

CONFIG = {"mcpServers": {"kinetic-studio": {"command": "uvx", "args": ["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"]}}}

async def main(model):  # any chat model your LangChain install supports
    async with MCPAdapter(CONFIG) as adapter:
        tools = await adapter.list_tools()
        agent = create_agent(model, tools)  # a LangGraph agent
        return await agent.ainvoke({"messages": [{"role": "user", "content":
            "Make a 10-second kinetic reel that says SHIP IT FRIDAY and render it."}]})
```

Releases that still use the separate `langchain-mcp-adapters` package:

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({"kinetic-studio": {"transport": "stdio", "command": "uvx", "args": ["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"]}})
tools = await client.get_tools()
```

In a hand-built LangGraph graph, put `tools` in a `ToolNode` as with any other tools.

From a clone instead of uvx (after `bash install.sh` in the clone): use
`/path/to/kinetic-studio/.venv/bin/python` as the command, `["-m", "kstudio.mcp_server"]` as the
arguments, and set the environment variable `PYTHONPATH=/path/to/kinetic-studio`.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.
