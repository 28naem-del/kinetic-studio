"""MCP server: stdio and HTTP transports, both protocol eras, tool results, and the generated
agent-facing files (docs/tools, SKILL.md copy, quickstart index, llms.txt)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from kstudio import mcp_server as M

ROOT = Path(__file__).resolve().parent.parent
TOOL_NAMES = ["list_modes", "list_scenes", "get_schema", "validate_spec", "new_spec", "preview", "render", "audit"]
MODERN = {"io.modelcontextprotocol/protocolVersion": M.MODERN_VERSION,
          "io.modelcontextprotocol/clientCapabilities": {}}


def _rpc(i, method, params=None):
    m = {"jsonrpc": "2.0", "id": i, "method": method}
    if params is not None:
        m["params"] = params
    return m


def _stdio(messages, timeout=120):
    """Pipe messages into `python -m kstudio.mcp_server`, return the responses keyed by id."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    lines = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages)
    r = subprocess.run([sys.executable, "-m", "kstudio.mcp_server"], input=lines, capture_output=True, text=True,
                       timeout=timeout, cwd=ROOT, env=env)
    assert r.returncode == 0, r.stderr
    out = [json.loads(x) for x in r.stdout.splitlines() if x.strip()]  # stdout holds JSON-RPC only
    return {m.get("id"): m for m in out}, out


def test_stdio_initialize_list_and_validate():
    by_id, _ = _stdio([
        _rpc(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "0"}}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _rpc(2, "tools/list"),
        _rpc(3, "tools/call", {"name": "validate_spec", "arguments": {"spec_path": "examples/kinetic"}}),
        _rpc(4, "ping"),
    ])
    init = by_id[1]["result"]
    assert init["protocolVersion"] == "2025-11-25"
    assert init["serverInfo"]["name"] == "kinetic-studio"
    assert "tools" in init["capabilities"]
    assert [t["name"] for t in by_id[2]["result"]["tools"]] == TOOL_NAMES
    res = by_id[3]["result"]
    assert res["isError"] is False
    assert res["structuredContent"]["ok"] is True
    assert json.loads(res["content"][0]["text"])["valid"] is True
    assert by_id[4]["result"] == {}


def test_stdio_version_negotiation_and_errors():
    by_id, out = _stdio([
        _rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}),
        _rpc(2, "initialize", {"protocolVersion": "1999-01-01", "capabilities": {}}),
        _rpc(3, "nope/method"),
        _rpc(4, "tools/call", {"name": "no_such_tool", "arguments": {}}),
        "{this is not json",
        _rpc(5, "tools/call", {"name": "list_scenes", "arguments": {"verbose": "yes"}}),
        _rpc(6, "tools/call", {"name": "kstudio_list_modes", "arguments": {}}),
    ])
    assert by_id[1]["result"]["protocolVersion"] == "2024-11-05"
    assert by_id[2]["result"]["protocolVersion"] in M.LEGACY_VERSIONS
    assert by_id[3]["error"]["code"] == -32601
    assert by_id[4]["error"]["code"] == -32602
    assert any(m.get("error", {}).get("code") == -32700 for m in out)
    bad = by_id[5]["result"]
    assert bad["isError"] is True and bad["structuredContent"]["error"]["code"] == "INVALID_ARGUMENTS"
    assert by_id[6]["result"]["structuredContent"]["ok"] is True  # prefixed alias


def test_modern_stateless_requests():
    srv = M.Server(lambda m: None)
    d = srv.handle(_rpc(1, "server/discover", {"_meta": MODERN}))["result"]
    assert d["resultType"] == "complete"
    assert M.MODERN_VERSION in d["supportedVersions"] and "2025-11-25" in d["supportedVersions"]
    assert d["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "kinetic-studio"
    tl = srv.handle(_rpc(2, "tools/list", {"_meta": MODERN}))["result"]
    assert tl["resultType"] == "complete" and len(tl["tools"]) == len(TOOL_NAMES)
    e = srv.handle(_rpc(3, "tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": "2099-01-01",
                                                     "io.modelcontextprotocol/clientCapabilities": {}}}))["error"]
    assert e["code"] == -32022 and e["data"]["requested"] == "2099-01-01" and e["data"]["supported"]
    e = srv.handle(_rpc(4, "tools/list", {"_meta": {"io.modelcontextprotocol/protocolVersion": M.MODERN_VERSION}}))
    assert e["error"]["code"] == -32602
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 9}}) is None
    assert srv.handle({"id": 5, "method": "ping"})["error"]["code"] == -32600


def test_modern_tool_call_result_shape():
    sent = []
    srv = M.Server(sent.append)
    done = threading.Event()
    srv.handle(_rpc(7, "tools/call", {"name": "list_modes", "arguments": {}, "_meta": MODERN}),
               respond=lambda r: (sent.append(r), done.set()))
    assert done.wait(60)
    r = sent[-1]["result"]
    assert r["resultType"] == "complete" and r["isError"] is False
    modes = [m["mode"] for m in r["structuredContent"]["modes"]]
    assert {"kinetic", "explainer", "promo", "data", "social"} <= set(modes)


def test_tool_definitions_are_well_formed():
    for t in M.tool_list():
        assert re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", t["name"])
        assert t["inputSchema"]["type"] == "object" and t["inputSchema"]["additionalProperties"] is False
        assert t["outputSchema"]["type"] == "object" and "ok" in t["outputSchema"]["required"]
        assert set(t["annotations"]) >= {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
        assert t["description"] and t["title"]


def test_validate_reports_errors_without_failing_the_call():
    bad = json.dumps({"mode": "kinetic", "scenes": [{"type": "no_such_scene", "dur": "4b"}]})
    data, _, is_error = M.call_tool(M.find_tool("validate_spec"), {"spec_json": bad})
    assert is_error is False and data["ok"] is False and data["errors"]
    data, _, _ = M.call_tool(M.find_tool("validate_spec"), {"spec_json": "{not json"})
    assert data["ok"] is False and data["errors"]
    data, _, is_error = M.call_tool(M.find_tool("validate_spec"), {})
    assert is_error is True and data["error"]["code"] == "USAGE"
    assert M.run_tool("kstudio_validate_spec", '{"spec_path": "examples/kinetic"}')["ok"] is True


def test_new_spec_and_one_frame_preview(tmp_path):
    proj = tmp_path / "proj"
    r = M.run_tool("new_spec", {"mode": "kinetic", "dir": str(proj)})
    assert r["ok"] and Path(r["spec_path"]).is_file()
    again = M.run_tool("new_spec", {"mode": "kinetic", "dir": str(proj)})
    assert again["ok"] is False and again["error"]["code"] == "DIR_NOT_EMPTY"
    events = []
    prog = M.Progress(events.append, "tok", threading.Event(), min_gap=0)
    data, extra, is_error = M.call_tool(M.find_tool("preview"),
                                        {"spec_path": str(proj), "at": [1.0], "scale": 0.25}, prog)
    assert not is_error and data["ok"], data
    assert Path(data["contact_sheet"]).is_file() and len(data["stills"]) == 1
    assert extra and extra[0]["type"] == "image" and extra[0]["mimeType"] == "image/jpeg"
    values = [e["params"]["progress"] for e in events]
    assert values == sorted(set(values)) and values[-1] == 100

    own = tmp_path / "own"
    spec = {"mode": "social", "scenes": [{"type": "title", "title": "HELLO", "dur": "4b"}]}
    r = M.run_tool("new_spec", {"mode": "social", "dir": str(own), "spec_json": json.dumps(spec)})
    assert r["ok"] and json.loads(Path(r["spec_path"]).read_text())["scenes"][0]["title"] == "HELLO"


def test_progress_is_monotonic_rate_limited_and_cancellable():
    sent, ev = [], threading.Event()
    p = M.Progress(sent.append, 5, ev, min_gap=10)
    p(0, 100, "a")
    p(10, 100, "b")      # rate limited
    p(5, 100, "c")       # not increasing
    p(100, 100, "done")  # the end always goes out
    assert [m["params"]["progress"] for m in sent] == [0, 100]
    p.closed = True
    p(None, 100, "late")
    assert len(sent) == 2
    ev.set()
    with pytest.raises(M.Cancelled):
        p(1)
    silent = M.Progress(sent.append, None, threading.Event())
    silent(50)
    assert len(sent) == 2


def _post(url, body, headers=None):
    data = json.dumps(body).encode() if not isinstance(body, bytes) else body
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


@pytest.fixture()
def http_url():
    httpd = M.make_http_server("127.0.0.1", 0, [])
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/mcp"
    httpd.shutdown()
    httpd.server_close()


def test_http_transport(http_url):
    s, _, b = _post(http_url, _rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}))
    assert s == 200 and json.loads(b)["result"]["protocolVersion"] == "2025-06-18"
    s, _, b = _post(http_url, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert s == 202 and b == ""
    hdr = {"MCP-Protocol-Version": M.MODERN_VERSION, "Mcp-Method": "tools/list"}
    s, _, b = _post(http_url, _rpc(2, "tools/list", {"_meta": MODERN}), hdr)
    assert s == 200 and json.loads(b)["result"]["resultType"] == "complete"
    s, _, b = _post(http_url, _rpc(3, "tools/list", {"_meta": MODERN}), {**hdr, "MCP-Protocol-Version": "2025-06-18"})
    assert s == 400 and json.loads(b)["error"]["code"] == -32020
    s, _, b = _post(http_url, _rpc(4, "x/y", {"_meta": MODERN}), {**hdr, "Mcp-Method": "x/y"})
    assert s == 404 and json.loads(b)["error"]["code"] == -32601
    s, _, _ = _post(http_url, _rpc(5, "ping"), {"Origin": "https://evil.example"})
    assert s == 403
    s, _, _ = _post(http_url, b"{oops")
    assert s == 400
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(http_url, timeout=10)
    assert ei.value.code == 405
    call = _rpc(6, "tools/call", {"name": "list_modes", "arguments": {},
                                  "_meta": {**MODERN, "progressToken": "p1"}})
    s, h, b = _post(http_url, call, {**hdr, "Mcp-Method": "tools/call", "Mcp-Name": "list_modes"})
    assert s == 200 and h["Content-Type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in b.splitlines() if line.startswith("data: ")]
    assert events[-1]["id"] == 6 and events[-1]["result"]["structuredContent"]["ok"] is True
    assert all(e.get("method") == "notifications/progress" for e in events[:-1])


def test_one_shot_cli(tmp_path):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))
    r = subprocess.run([sys.executable, "-m", "kstudio.mcp_server", "--call", "validate_spec",
                        json.dumps({"spec_path": "examples/promo"})], capture_output=True, text=True, cwd=ROOT,
                       env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["ok"] is True
    r = subprocess.run([sys.executable, "-m", "kstudio.mcp_server", "--list-tools"], capture_output=True, text=True,
                       cwd=ROOT, env=env, timeout=60)
    assert [t["name"] for t in json.loads(r.stdout)["tools"]] == TOOL_NAMES


# --------------------------------------------------------------------------- generated files

def test_docs_tools_in_sync():
    sys.path.insert(0, str(ROOT / "docs" / "tools"))
    try:
        import generate
    finally:
        sys.path.pop(0)
    assert generate.main(["--check"]) == 0, "run: python docs/tools/generate.py"
    for t in json.loads((ROOT / "docs/tools/functions.json").read_text()):
        p = t["parameters"]
        assert p["additionalProperties"] is False and sorted(p["required"]) == sorted(p["properties"])
        assert t["name"].startswith("kstudio_") and t["strict"] is True


def test_skill_copy_is_identical_and_valid():
    a, b = (ROOT / "SKILL.md").read_bytes(), (ROOT / "skills/kinetic-studio/SKILL.md").read_bytes()
    assert a == b, "skills/kinetic-studio/SKILL.md must be a byte copy of SKILL.md"
    text = a.decode()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md needs YAML frontmatter"
    front = m.group(1)
    assert re.search(r"^name: kinetic-studio$", front, re.M)
    desc = re.search(r"^description: (.+)$", front, re.M)
    assert desc and len(desc.group(1)) <= 1024
    assert len(text.splitlines()) < 500


def test_quickstart_index_is_alphabetical_and_cited():
    qs = ROOT / "docs/quickstarts"
    index = (qs / "README.md").read_text()
    links = re.findall(r"^\s*[-*] \[([^\]]+)\]\(([^)]+\.md)\)", index, re.M)
    assert len(links) >= 15
    names = [n for n, _ in links]
    assert names == sorted(names, key=str.lower), names
    for _, f in links:
        body = (qs / f).read_text()
        assert re.search(r"https?://\S+", body), f"{f} must cite the framework's docs URL"
        assert "Verified against" in body, f


def test_llms_txt():
    text = (ROOT / "llms.txt").read_text()
    assert text.startswith("# Kinetic Studio\n")
    assert re.search(r"^> .+", text, re.M)
    assert re.search(r"^## .+", text, re.M) and "## Optional" in text
    assert (ROOT / "llms-full.txt").stat().st_size > len(text)
