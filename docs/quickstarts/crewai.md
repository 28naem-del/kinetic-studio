# Kinetic Studio with CrewAI (crew-style multi-agent)

Verified against the official docs on 2026-10-30:

- https://docs.crewai.com/en/mcp/overview

Needs Python 3.10+, ffmpeg with libx264 on PATH, and [uv](https://docs.astral.sh/uv/). The first start downloads the engine (about a minute); later starts are instant.

```bash
uv pip install crewai 'crewai-tools[mcp]'
```

A three-role crew: a copywriter writes the words, a motion director turns them into a spec and renders
it, a reviewer checks the audit. Only the director and the reviewer get the tools.

```python
from crewai import Agent, Crew, Task
from crewai_tools import MCPServerAdapter
from mcp import StdioServerParameters

params = StdioServerParameters(command="uvx", args=["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"])

with MCPServerAdapter(params, connect_timeout=120) as studio:
    writer = Agent(role="Copywriter", goal="Write punchy on-screen copy, 2-4 words per line",
                   backstory="You write hooks for short vertical videos.")
    director = Agent(role="Motion director", goal="Turn copy into a valid Kinetic Studio spec and render it",
                     backstory="You keep every scene a whole number of beats.", tools=studio)
    reviewer = Agent(role="Reviewer", goal="Check the rendered video passes the audit",
                     backstory="You read audit reports.", tools=studio)
    crew = Crew(agents=[writer, director, reviewer], tasks=[
        Task(description="Copy for a 10-second teaser: SHIP IT FRIDAY", expected_output="4-6 short lines",
             agent=writer),
        Task(description="Write the spec (get_schema mode=kinetic, new_spec, validate_spec), preview, render",
             expected_output="the MP4 path", agent=director),
        Task(description="Audit the MP4 and list any failed check", expected_output="PASS or the failures",
             agent=reviewer),
    ])
    print(crew.kickoff())
```

Newer CrewAI releases also take the server directly on an agent:

```python
from crewai.mcp import MCPServerStdio

director = Agent(role="Motion director", goal="...", backstory="...",
                 mcps=[MCPServerStdio(command="uvx", args=["--from", "git+https://github.com/28naem-del/kinetic-studio", "kstudio-mcp"])])
```

From a clone instead of uvx (after `bash install.sh` in the clone): use
`/path/to/kinetic-studio/.venv/bin/python` as the command, `["-m", "kstudio.mcp_server"]` as the
arguments, and set the environment variable `PYTHONPATH=/path/to/kinetic-studio`.

## Try it

> Use kinetic-studio to make a 10-second kinetic reel that says "SHIP IT FRIDAY". Validate the spec, show me the preview contact sheet, then render it and tell me the audit result.
