"""The optional swap client, against an in-process mock of the ComfyUI HTTP API.

No GPU and no real ComfyUI: the mock answers /object_info, /upload/image, /prompt, /queue,
/history/<id> and /view the way ComfyUI does, so the whole round trip (upload, submit, poll,
download) and every failure path run in CI."""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import ROOT

from kstudio import cli, swap
from kstudio.spec import SpecError, load_spec, swap_requests, swap_source

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")

# sha256 of Openjutsu's workflows at the pinned upstream commit (see THIRD_PARTY_NOTICES.md)
UPSTREAM_SHA256 = {
    "simple": "24dc5b8ef45788c839beb96b617b404308ddfd8921e89cfd9008442ad29a666a",
    "object": "fbfec05071d7f6d9986ba2650d58db9a0e5810a1e13f8efba36afbbf0f67eee2",
}
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"swapped-video" * 64
LOGIN_PAGE = b"<!doctype html><html><head><title>Router login</title></head><body>Sign in</body></html>"


# --------------------------------------------------------------------------- mock ComfyUI

class Backend:
    def __init__(self):
        self.missing: list[str] = []
        self.reject = False
        self.fail = False
        self.no_video = False
        self.html: str | None = None  # path prefix answered with a web page instead of JSON ("/" = everything)
        self.pending = 2  # history polls before the job is done
        self.video = FAKE_MP4
        self.uploads: dict[str, bytes] = {}
        self.prompts: list[dict] = []
        self.polls = 0
        self.views: list[dict] = []
        self.lock = threading.Lock()

    def known(self):
        classes = set()
        for m in ("simple", "object"):
            classes |= set(swap.node_classes(swap.load_workflow(m)))
        return sorted((classes | {"H3SoundtrackOrSilence"}) - set(self.missing))


def _handler(be: Backend):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _page(self):
            if be.html is not None and self.path.startswith(be.html):
                self._send(200, LOGIN_PAGE, "text/html")
                return True
            return False

        def do_GET(self):
            if self._page():
                return None
            u = urllib.parse.urlsplit(self.path)
            if u.path == "/object_info":
                return self._send(200, {c: {"input": {}} for c in be.known()})
            if u.path == "/queue":
                with be.lock:
                    waiting = be.prompts and be.polls < be.pending
                return self._send(200, {"queue_running": [], "queue_pending": [[1, "p-1", {}, {}, []]] if waiting else []})
            if u.path.startswith("/history/"):
                pid = u.path.rsplit("/", 1)[1]
                with be.lock:
                    be.polls += 1
                    done = be.polls > be.pending
                if not done:
                    return self._send(200, {})
                if be.fail:
                    status = {"status_str": "error", "completed": False, "messages": [
                        ["execution_start", {"prompt_id": pid}],
                        ["execution_error", {"prompt_id": pid, "node_id": "62", "node_type": "MiniMaxH3ReferenceToVideo",
                                             "exception_message": "CUDA out of memory. "}]]}
                    return self._send(200, {pid: {"status": status, "outputs": {}}})
                outputs = {"99": {"images": [{"filename": "grid.png", "subfolder": "", "type": "temp"}]}}
                if not be.no_video:
                    outputs["67"] = {"gifs": [{"filename": "swap_00001.mp4", "subfolder": "swap", "type": "output"}]}
                return self._send(200, {pid: {"status": {"status_str": "success", "completed": True, "messages": []},
                                              "outputs": outputs}})
            if u.path == "/view":
                be.views.append(dict(urllib.parse.parse_qsl(u.query)))
                return self._send(200, be.video, "video/mp4")
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self._page():
                return None
            if self.path == "/upload/image":
                assert self.headers["Content-Type"].startswith("multipart/form-data; boundary=")
                head, _, rest = body.partition(b'name="image"; filename="')
                name = rest.split(b'"', 1)[0].decode()
                payload = rest.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
                assert b'name="type"\r\n\r\ninput' in head and b'name="overwrite"\r\n\r\ntrue' in head
                be.uploads[name] = payload
                return self._send(200, {"name": name, "subfolder": "", "type": "input"})
            if self.path == "/prompt":
                req = json.loads(body)
                if be.reject:
                    return self._send(400, {
                        "error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
                        "node_errors": {"62": {"errors": [{"message": "Node 'MiniMaxH3ReferenceToVideo' not found"}]}}})
                be.prompts.append(req)
                return self._send(200, {"prompt_id": "p-1", "number": 1, "node_errors": {}})
            return self._send(404, {"error": "not found"})

    return H


@pytest.fixture
def backend(monkeypatch):
    be = Backend()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(be))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    be.url = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv(swap.ENV_URL, be.url)
    yield be
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.delenv(swap.ENV_URL, raising=False)
    monkeypatch.delenv("KSTUDIO_SWAP_DIR", raising=False)
    monkeypatch.delenv("KSTUDIO_JSON", raising=False)
    monkeypatch.setenv("KSTUDIO_CLIP_CACHE", str(tmp_path / "clip-cache"))
    swap._WARNED.clear()


@pytest.fixture
def inputs(tmp_path):
    v, c = tmp_path / "dance.mp4", tmp_path / "hero.png"
    v.write_bytes(b"source-video-bytes")
    c.write_bytes(b"\x89PNG\r\n\x1a\ncharacter")
    return str(v), str(c)


def _dead_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def run(capsys, *argv, code=0):
    rc = cli.main(["swap", *argv, "--json"])
    out, err = capsys.readouterr()
    lines = [x for x in out.splitlines() if x.strip()]
    assert len(lines) == 1, out
    d = json.loads(lines[0])
    assert rc == code == d["exit_code"], (rc, d)
    assert d["ok"] is (code == 0) and d["command"] == "swap"
    return d, err


# --------------------------------------------------------------------------- workflows

@pytest.mark.parametrize("mode", ["simple", "object"])
def test_vendored_workflows_are_upstream_unchanged(mode):
    with open(swap.workflow_path(mode), "rb") as fh:
        assert hashlib.sha256(fh.read()).hexdigest() == UPSTREAM_SHA256[mode]


def test_patch_simple_fills_inputs_and_keeps_the_original():
    wf = swap.load_workflow("simple")
    before = json.dumps(wf, sort_keys=True)
    g = swap.patch_workflow(wf, "simple", "v.mp4", "c.png", prompt="  the  dancer ", target="woman", seed=7)
    assert json.dumps(wf, sort_keys=True) == before
    by_cls = lambda c: [n for n in g.values() if n["class_type"] == c]  # noqa: E731
    assert by_cls("LoadImage")[0]["inputs"]["image"] == "c.png"
    assert by_cls("VHS_LoadVideo")[0]["inputs"]["video"] == "v.mp4"
    plan = by_cls("H3OneClickSwapPlan")[0]["inputs"]
    assert plan["roles"] == "the dancer" and plan["seed"] == 7
    sam = [n for n in by_cls("CLIPTextEncode") if n["_meta"]["title"] == "SAM: what to replace"]
    assert sam[0]["inputs"]["text"] == "woman"


def test_patch_object_and_soundtrack_rerouting():
    g = swap.patch_workflow(swap.load_workflow("object"), "object", "v.mp4", "c.png", prompt="a red raincoat",
                            target="jacket", seed=2**40 + 3)
    plan = next(n for n in g.values() if n["class_type"] == "H3ObjectSwapPlan")["inputs"]
    assert (plan["change_to"], plan["target"], plan["seed"]) == ("a red raincoat", "jacket", 3)
    vid = next(k for k, n in g.items() if n["class_type"] == "VHS_LoadVideo")
    readers = [(k, n) for k, n in g.items() for v in n["inputs"].values() if v == [vid, 2]]
    assert [n["class_type"] for _, n in readers] == ["H3SoundtrackOrSilence"]
    assert any(v == [readers[0][0], 0] for n in g.values() for v in n["inputs"].values())
    with pytest.raises(Exception, match="unknown swap mode"):
        swap.patch_workflow(g, "face", "v", "c")


def test_find_video_prefers_the_final_assembly():
    graph = {"5": {"class_type": "MiniMaxH3ChainAssemble"}, "1": {"class_type": "SaveVideo"}}
    outs = {"1": {"gifs": [{"filename": "seg.mp4"}]}, "5": {"gifs": [{"filename": "final.mp4"}]},
            "2": {"images": [{"filename": "a.png"}]}}
    assert swap.find_video(outs, graph)["filename"] == "final.mp4"
    assert swap.find_video({"2": outs["2"]}, graph) is None


# --------------------------------------------------------------------------- round trip

def test_cli_round_trip_and_cache(capsys, backend, inputs, tmp_path):
    v, c = inputs
    out = str(tmp_path / "job" / "swapped.mp4")
    d, err = run(capsys, "--video", v, "--character", c, "--prompt", "the dancer", "--seed", "5", "-o", out,
                 "--poll", "0.01")
    assert d["cached"] is False and d["output"] == out and d["prompt_id"] == "p-1" and d["bytes"] == len(FAKE_MP4)
    with open(out, "rb") as fh:
        assert fh.read() == FAKE_MP4
    meta = json.load(open(out + ".swap.json"))
    assert meta["key"] == d["key"] and meta["backend_file"] == "swap_00001.mp4" and meta["mode"] == "simple"
    # both inputs were uploaded under content-addressed names and wired into the queued graph
    assert sorted(be for be in backend.uploads.values()) == sorted([open(v, "rb").read(), open(c, "rb").read()])
    g = backend.prompts[0]["prompt"]
    names = {n["class_type"]: n["inputs"] for n in g.values()}
    assert names["VHS_LoadVideo"]["video"] in backend.uploads and names["VHS_LoadVideo"]["video"].endswith("dance.mp4")
    assert names["LoadImage"]["image"] in backend.uploads and names["H3OneClickSwapPlan"]["seed"] == 5
    assert backend.views == [{"filename": "swap_00001.mp4", "subfolder": "swap", "type": "output"}]
    stages = [json.loads(x)["stage"] for x in err.splitlines() if x.startswith("{")]
    assert stages[0] == "connect" and stages[-2:] == ["download", "done"]
    assert "queued" in stages and "upload" in stages
    assert not [f for f in os.listdir(os.path.dirname(out)) if f.endswith(".part")]

    d2, _ = run(capsys, "--video", v, "--character", c, "--prompt", "the dancer", "--seed", "5", "-o", out)
    assert d2["cached"] is True and d2["key"] == d["key"] and len(backend.prompts) == 1


def test_object_mode_round_trip(backend, inputs, tmp_path):
    v, c = inputs
    r = swap.run_swap(v, c, "object", prompt="a red raincoat", target="jacket", out=str(tmp_path / "o.mp4"), poll=0.01)
    assert r["cached"] is False and os.path.getsize(r["output"]) == len(FAKE_MP4)
    plan = next(n for n in backend.prompts[0]["prompt"].values() if n["class_type"] == "H3ObjectSwapPlan")
    assert plan["inputs"]["change_to"] == "a red raincoat"


# --------------------------------------------------------------------------- failures

def test_unreachable_backend_is_json_exit_7(capsys, monkeypatch, inputs, tmp_path):
    v, c = inputs
    monkeypatch.setenv(swap.ENV_URL, _dead_url())
    d, _ = run(capsys, "--video", v, "--character", c, "-o", str(tmp_path / "x.mp4"), code=7)
    e = d["error"]
    assert e["code"] == "BACKEND_UNREACHABLE" and e["reason"] == "unreachable" and e["url"].startswith("http://127.0.0.1")
    assert "KSTUDIO_COMFY_URL" in e["hint"] and not os.path.exists(tmp_path / "x.mp4")


def test_unconfigured_backend_is_json_exit_7(capsys, inputs, tmp_path):
    v, c = inputs
    d, _ = run(capsys, "--video", v, "--character", c, "-o", str(tmp_path / "x.mp4"), code=7)
    assert d["error"]["code"] == "BACKEND_UNREACHABLE" and d["error"]["reason"] == "not_configured"


@pytest.mark.parametrize("where", ["/", "/prompt", "/upload", "/history"])
def test_non_json_backend_is_json_exit_7(capsys, backend, inputs, tmp_path, where):
    """A URL that answers HTTP but not ComfyUI's JSON (a router login page, a proxy, the wrong port)."""
    backend.html = where
    v, c = inputs
    out = tmp_path / "x.mp4"
    d, err = run(capsys, "--video", v, "--character", c, "-o", str(out), "--poll", "0.01", code=7)
    e = d["error"]
    assert e["code"] == "BACKEND_UNREACHABLE" and e["reason"] == "not_comfyui" and e["url"] == backend.url
    assert "not a ComfyUI API" in e["message"] and "Router login" in e["message"]
    assert "8188" in e["hint"] and "KSTUDIO_COMFY_URL" in e["hint"]
    assert "JSONDecodeError" not in json.dumps(d) and "Traceback" not in err
    assert not out.exists() and not [f for f in os.listdir(tmp_path) if f.endswith(".part")]


def test_non_json_backend_through_mcp(backend, inputs, tmp_path):
    from kstudio import mcp_server
    backend.html = "/"
    v, c = inputs
    r = mcp_server.run_tool("swap", {"video": v, "character": c, "output": str(tmp_path / "m.mp4")})
    assert r["ok"] is False and r["error"]["code"] == "BACKEND_UNREACHABLE"
    assert r["error"]["reason"] == "not_comfyui"


def test_rejected_workflow(capsys, backend, inputs, tmp_path):
    backend.reject = True
    v, c = inputs
    d, _ = run(capsys, "--video", v, "--character", c, "-o", str(tmp_path / "x.mp4"), "--poll", "0.01", code=4)
    e = d["error"]
    assert e["code"] == "WORKFLOW_REJECTED" and "failed validation" in e["message"]
    assert "62" in e["node_errors"] and "node packs" in e["hint"]


def test_failed_execution(capsys, backend, inputs, tmp_path):
    backend.fail = True
    v, c = inputs
    d, _ = run(capsys, "--video", v, "--character", c, "-o", str(tmp_path / "x.mp4"), "--poll", "0.01", code=4)
    assert d["error"]["code"] == "SWAP_FAILED" and "CUDA out of memory" in d["error"]["message"]
    assert d["error"]["prompt_id"] == "p-1" and not os.path.exists(tmp_path / "x.mp4")


def test_no_video_output_and_timeout(backend, inputs, tmp_path):
    v, c = inputs
    backend.no_video = True
    with pytest.raises(swap.RenderFailed) as ei:
        swap.run_swap(v, c, out=str(tmp_path / "a.mp4"), poll=0.01)
    assert ei.value.code == "NO_OUTPUT"
    backend.no_video, backend.polls, backend.pending = False, 0, 10**6
    with pytest.raises(swap.RenderFailed) as ei:
        swap.run_swap(v, c, out=str(tmp_path / "b.mp4"), poll=0.01, timeout=0.2)
    assert ei.value.code == "SWAP_TIMEOUT"


def test_missing_nodes_exit_6(capsys, backend, inputs, tmp_path):
    backend.missing = ["SAM3_VideoTrack", "MiniMaxH3ReferenceToVideo"]
    v, c = inputs
    d, _ = run(capsys, "--video", v, "--character", c, "-o", str(tmp_path / "x.mp4"), code=6)
    assert d["error"]["code"] == "MISSING_NODES" and sorted(d["error"]["missing"]) == sorted(backend.missing)
    assert not backend.uploads and not backend.prompts  # nothing is sent before the check


def test_bad_input_and_usage(capsys, inputs, tmp_path):
    v, _ = inputs
    d, _ = run(capsys, "--video", v, "--character", str(tmp_path / "nope.png"), code=2)
    assert d["error"]["code"] == "FILE_NOT_FOUND"
    d, _ = run(capsys, "--video", v, code=2)
    assert d["error"]["code"] == "USAGE"


def test_help_works_without_a_backend():
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop(swap.ENV_URL, None)
    r = subprocess.run([sys.executable, "-m", "kstudio", "swap", "--help"], capture_output=True, text=True,
                       cwd=ROOT, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "--character" in r.stdout and "KSTUDIO_COMFY_URL" in r.stdout and "exit 7" in r.stdout


# --------------------------------------------------------------------------- MCP

def test_mcp_tool_listed_and_dispatched(backend, inputs, tmp_path, monkeypatch):
    from kstudio import mcp_server
    tool = mcp_server.find_tool("swap")
    assert tool is not None and list(tool.required) == ["video", "character"]
    v, c = inputs
    r = mcp_server.run_tool("swap", {"video": v, "character": c, "output": str(tmp_path / "m.mp4")})
    assert r["ok"] is True and r["cached"] is False and os.path.getsize(r["output"]) == len(FAKE_MP4)
    monkeypatch.setenv(swap.ENV_URL, _dead_url())
    r = mcp_server.run_tool("kstudio_swap", {"video": v, "character": c, "output": str(tmp_path / "n.mp4")})
    assert r["ok"] is False and r["error"]["code"] == "BACKEND_UNREACHABLE"


# --------------------------------------------------------------------------- spec source

def test_swap_source_validation_and_requests(tmp_path):
    assert swap_source(None) is None
    req = swap_source({"swap": {"video": "a.mp4", "character": "b.png", "seed": 3}}, str(tmp_path))
    assert req == {"video": str(tmp_path / "a.mp4"), "character": str(tmp_path / "b.png"), "mode": "simple",
                   "prompt": "", "target": "", "seed": 3}
    for bad in ({"url": "x"}, {"swap": "a.mp4"}, {"swap": {"video": "a.mp4", "character": "b.png", "mode": "face"}},
                {"swap": {"video": "a.mp4", "character": "b.png", "seed": -1}},
                {"swap": {"video": "a.mp4", "character": "b.png", "steps": 4}}):
        with pytest.raises(SpecError):
            swap_source(bad)
    spec = {"_dir": str(tmp_path), "scenes": [
        {"type": "title"}, {"type": "clip", "src": "x.mp4"},
        {"type": "clip", "source": {"swap": {"video": "a.mp4", "character": "b.png"}}}]}
    assert [r["path"] for r in swap_requests(spec)] == ["/scenes/2"]


def test_clip_source_never_raises(monkeypatch, tmp_path, capsys):
    """Whatever the backend does, a clip's swap source resolves to a file and never to an exception."""
    monkeypatch.setenv(swap.ENV_URL, "http://127.0.0.1:1")

    def boom(*a, **k):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(swap, "run_swap", boom)
    req = swap_source({"swap": {"video": "a.mp4", "character": "b.png"}}, str(tmp_path))
    # an input file is missing: nothing to swap, the clip gets the input path (and reports missing footage)
    assert swap.resolve_clip_source(req, str(tmp_path)) == str(tmp_path / "a.mp4")
    assert "swap source skipped" in capsys.readouterr().err
    (tmp_path / "a.mp4").write_bytes(b"v")
    (tmp_path / "b.png").write_bytes(b"c")
    assert swap.resolve_clip_source(req, str(tmp_path)) == str(tmp_path / "a.mp4")
    err = capsys.readouterr().err
    assert "RuntimeError: something unexpected" in err and "playing the input video" in err


def _spec_dir(tmp_path, video):
    d = tmp_path / "project"
    d.mkdir()
    shutil.copy(video, d / "dance.mp4")
    (d / "hero.png").write_bytes(b"\x89PNG\r\n\x1a\nhero")
    spec = {"mode": "kinetic", "scenes": [
        {"type": "clip", "dur": 1.0, "scrim": 0, "zoom": 1.0, "camera": False,
         "source": {"swap": {"video": "dance.mp4", "character": "hero.png"}}}]}
    (d / "spec.json").write_text(json.dumps(spec))
    return str(d)


def _video(path, color):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=96x160:d=1:r=10",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)
    return str(path)


def _first_frame(spec_dir):
    from kstudio.compositor import Compositor
    from kstudio.film import Film
    from kstudio.presets import make_output
    out = make_output("reel", quality="draft", scale=0.2)
    film = Film(load_spec(spec_dir), out.width, out.height)
    img = Compositor(film, out).frame(10)[..., :3].astype(int)
    return img[..., 0].mean(), img[..., 1].mean(), img[..., 2].mean()


@needs_ffmpeg
def test_clip_swap_source_falls_back_to_the_input_video(tmp_path, capsys):
    d = _spec_dir(tmp_path, _video(tmp_path / "green.mp4", "0x20c040"))
    r, g, b = _first_frame(d)
    assert g > r + 60 and g > b + 60, "no backend: the clip plays the input video"
    assert "swap source not made" in capsys.readouterr().err


@needs_ffmpeg
def test_clip_swap_source_through_the_backend_and_spec_preflight(tmp_path, capsys, backend):
    with open(_video(tmp_path / "red.mp4", "red"), "rb") as fh:
        backend.video = fh.read()  # what the "GPU" returns
    d = _spec_dir(tmp_path, _video(tmp_path / "green.mp4", "0x20c040"))
    res, _ = run(capsys, "--spec", d, "--poll", "0.01")
    assert res["count"] == 1 and res["swaps"][0]["scene"] == "/scenes/0"
    assert res["swaps"][0]["output"].startswith(os.path.join(d, "out", "swap"))
    r, g, b = _first_frame(d)
    assert r > g + 60 and r > b + 60, "the clip plays the swapped footage"
    assert len(backend.prompts) == 1  # the render reused the preflight's file


@needs_ffmpeg
def test_clip_swap_source_plays_the_input_when_the_backend_is_not_comfyui(tmp_path, capsys, backend):
    backend.html = "/"
    d = _spec_dir(tmp_path, _video(tmp_path / "green.mp4", "0x20c040"))
    r, g, b = _first_frame(d)
    assert g > r + 60 and g > b + 60 and g > 150, "a web page instead of ComfyUI: the clip plays the input video"
    err = capsys.readouterr().err
    assert "BACKEND_UNREACHABLE" in err and "playing the input video" in err
    assert not os.listdir(os.path.join(d, "out", "swap")) or not [
        f for f in os.listdir(os.path.join(d, "out", "swap")) if f.endswith(".mp4")]
