"""Optional character swap through a ComfyUI backend running Openjutsu's workflows.

Kinetic Studio itself stays local and GPU-free. This module only talks to a ComfyUI server that
someone has set up on an NVIDIA machine with Openjutsu's custom node packs and models (see
docs/SWAP.md). Point ``KSTUDIO_COMFY_URL`` at it (for example ``http://gpu-box:8188``); without it,
or when the server cannot be reached, every entry point reports ``BACKEND_UNREACHABLE`` (exit 7)
and nothing else in Kinetic Studio is affected.

The two vendored API workflows (``kstudio/workflows/{simple,object}.api.json``, MIT, Openjutsu)
are patched with the uploaded inputs and settings, queued with ``POST /prompt``, polled through
``/queue`` and ``/history`` and the finished MP4 is fetched with ``/view`` into the job folder.

Entry points: ``kstudio swap`` (``register``), the MCP tool ``swap`` (``MCP_TOOLS`` /
``MCP_HANDLERS``), :func:`run_swap` and :func:`resolve_clip_source` (used by the ``clip`` scene).
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from typing import Any

from .exitcodes import BackendUnreachable, MissingTool, RenderFailed, UsageError
from .spec import SWAP_MODES, load_spec, swap_dir, swap_key, swap_requests

ENV_URL = "KSTUDIO_COMFY_URL"
WORKFLOW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workflows")
UPSTREAM = "https://github.com/D3voz/openjutsu"
GATEWAY_CODES = (502, 503, 504)
NOT_COMFYUI_HINT = (f"check the host and port in {ENV_URL}: it must point at ComfyUI itself "
                    f"(port 8188 by default), not a web page or proxy; see docs/SWAP.md")
SETUP_HINT = (f"set {ENV_URL} to a ComfyUI server (http://host:8188) with Openjutsu's node packs and models "
              f"installed; see docs/SWAP.md and {UPSTREAM}")
VIDEO_EXT = (".mp4", ".webm", ".mov", ".mkv", ".gif")
MAX_SEED = 0xFFFFFFFF

Progress = Callable[[dict[str, Any]], None]


# --------------------------------------------------------------------------- workflows

def workflow_path(mode: str) -> str:
    return os.path.join(WORKFLOW_DIR, f"{mode}.api.json")


def load_workflow(mode: str) -> dict[str, Any]:
    if mode not in SWAP_MODES:
        raise UsageError(f"unknown swap mode {mode!r}", hint=f"choose from {list(SWAP_MODES)}")
    path = workflow_path(mode)
    try:
        with open(path, encoding="utf-8") as fh:
            graph = json.load(fh)
    except FileNotFoundError:
        raise MissingTool(f"the vendored {mode} workflow is missing: {path}", code="WORKFLOW_MISSING",
                          hint="reinstall kinetic-studio from a full checkout") from None
    if not isinstance(graph, dict):
        raise MissingTool(f"the vendored {mode} workflow is not an API graph: {path}", code="WORKFLOW_MISSING")
    return graph


def _is_link(v: Any) -> bool:
    return isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and isinstance(v[1], int)


def _node(graph: dict[str, Any], class_type: str, title: str | None = None) -> str:
    """Id of the node with this class (and title, when given)."""
    hits = [k for k, n in graph.items() if n.get("class_type") == class_type
            and (title is None or (n.get("_meta") or {}).get("title") == title)]
    if not hits:
        raise MissingTool(f"workflow has no {class_type} node{f' titled {title!r}' if title else ''}",
                          code="WORKFLOW_MISSING")
    return sorted(hits, key=lambda k: (len(k), k))[0]


def silence_when_soundless(graph: dict[str, Any]) -> None:
    """VHS fails as soon as anything reads the soundtrack of a video that has none, so every reader of a
    VHS_LoadVideo's audio goes through a `H3SoundtrackOrSilence` node instead (as Openjutsu does)."""
    for vid in [k for k, n in graph.items() if n.get("class_type") == "VHS_LoadVideo"]:
        snd = f"{vid}_soundtrack"
        read = False
        for node in graph.values():
            ins = node.get("inputs") or {}
            for name, v in ins.items():
                if _is_link(v) and v[0] == vid and v[1] == 2:
                    ins[name] = [snd, 0]
                    read = True
        if read:
            graph[snd] = {"class_type": "H3SoundtrackOrSilence", "inputs": {"audio": [vid, 2], "video_info": [vid, 3]},
                          "_meta": {"title": "Soundtrack (silence if none)"}}


def patch_workflow(graph: dict[str, Any], mode: str, video: str, character: str, prompt: str = "",
                   target: str = "", seed: int = 0) -> dict[str, Any]:
    """A copy of the workflow with the uploaded file names and the settings filled in.

    simple: `prompt` names who the character replaces ("" lets the describer pick the main person),
    `target` is what SAM tracks (default "person"). object: `prompt` is what the target becomes,
    `target` what SAM tracks and changes (default: the exported "hair")."""
    g = copy.deepcopy(graph)
    g[_node(g, "LoadImage")]["inputs"]["image"] = character
    g[_node(g, "VHS_LoadVideo")]["inputs"]["video"] = video
    seed = int(seed) & MAX_SEED
    if mode == "simple":
        plan = g[_node(g, "H3OneClickSwapPlan")]["inputs"]
        plan["roles"] = " ".join(prompt.split())
        plan["seed"] = seed
        if target:
            g[_node(g, "CLIPTextEncode", "SAM: what to replace")]["inputs"]["text"] = target
    elif mode == "object":
        plan = g[_node(g, "H3ObjectSwapPlan")]["inputs"]
        if prompt:
            plan["change_to"] = prompt
        if target:
            plan["target"] = target
            g[_node(g, "CLIPTextEncode", "SAM: what to change")]["inputs"]["text"] = target
        plan["seed"] = seed
    else:
        raise UsageError(f"unknown swap mode {mode!r}", hint=f"choose from {list(SWAP_MODES)}")
    silence_when_soundless(g)
    return g


def node_classes(graph: dict[str, Any]) -> list[str]:
    return sorted({str(n.get("class_type")) for n in graph.values() if isinstance(n, dict)})


# --------------------------------------------------------------------------- HTTP client

def backend_url(url: str | None = None) -> str:
    u = (url or os.environ.get(ENV_URL) or "").strip().rstrip("/")
    if not u:
        raise BackendUnreachable(f"no swap backend configured ({ENV_URL} is not set)", reason="not_configured",
                                 hint=SETUP_HINT)
    if "://" not in u:
        u = "http://" + u
    if urllib.parse.urlsplit(u).scheme not in ("http", "https"):
        raise UsageError(f"swap backend URL must be http(s): {u}")
    return u


class Comfy:
    """Minimal ComfyUI HTTP API client (urllib only)."""

    def __init__(self, url: str, timeout: float = 30.0):
        self.url = url
        self.timeout = timeout

    def unreachable(self, why: Any) -> BackendUnreachable:
        return BackendUnreachable(f"swap backend unreachable at {self.url}: {why}", reason="unreachable",
                                  url=self.url, hint=SETUP_HINT)

    def not_comfyui(self, path: str, what: str) -> BackendUnreachable:
        return BackendUnreachable(f"{self.url} answered {path} with {what}; it is not a ComfyUI API",
                                  reason="not_comfyui", url=self.url, hint=NOT_COMFYUI_HINT)

    def _open(self, path: str, data: bytes | None = None, headers: dict[str, str] | None = None,
              timeout: float | None = None) -> Any:
        req = urllib.request.Request(self.url + path, data=data, headers=headers or {},
                                     method="POST" if data is not None else "GET")
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)  # noqa: S310 - http(s) only
        except urllib.error.HTTPError as e:
            if e.code in GATEWAY_CODES:  # a proxy in front of a ComfyUI that is down or restarting
                e.close()
                raise self.unreachable(f"HTTP {e.code} from a proxy or gateway (is ComfyUI running behind it?)") from None
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            raise self.unreachable(getattr(e, "reason", e)) from None
        except http.client.BadStatusLine as e:  # the port speaks something else (ssh, TLS, a raw service)
            line = str(e).strip()[:40]
            raise self.not_comfyui(path, f"a reply that is not HTTP ({line!r})") from None
        except http.client.HTTPException as e:  # a broken reply (header line too long, cut off)
            raise self.unreachable(f"broken HTTP reply ({type(e).__name__})") from None

    def _decode(self, path: str, raw: bytes, empty: bytes = b"null") -> Any:
        """JSON body of a reply; anything else means the URL is not a ComfyUI API (a web page, a proxy
        or login screen, a router answering for an unknown host, the wrong port)."""
        try:
            return json.loads(raw or empty)
        except ValueError:
            head = raw[:60].decode("utf-8", "replace").strip().replace("\n", " ")
            raise self.not_comfyui(path, f"something other than JSON ({head!r})") from None

    def _read(self, r: Any, size: int | None = None) -> bytes:
        try:
            return bytes(r.read() if size is None else r.read(size))
        except (ConnectionError, TimeoutError, OSError) as e:
            raise self.unreachable(e) from None
        except http.client.HTTPException as e:  # IncompleteRead: the connection dropped mid-reply
            raise self.unreachable(f"the reply broke off ({type(e).__name__})") from None

    def get_json(self, path: str) -> Any:
        with self._open(path) as r:
            return self._decode(path, self._read(r))

    def post_json(self, path: str, body: Any) -> Any:
        data = json.dumps(body).encode()
        with self._open(path, data, {"Content-Type": "application/json"}) as r:
            return self._decode(path, self._read(r))

    def upload(self, path: str) -> str:
        """Upload a file into ComfyUI's input folder; returns the name to put in a loader node."""
        boundary = "kstudio" + uuid.uuid4().hex
        name = os.path.basename(path)
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            payload = fh.read()
        digest.update(payload)
        # content-addressed name: identical inputs reuse the upload, different ones never collide
        stored = f"kstudio_{digest.hexdigest()[:12]}_{name}"
        parts = []
        for k, v in (("type", "input"), ("overwrite", "true")):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{stored}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + payload + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        try:
            with self._open("/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"},
                            timeout=max(self.timeout, 300.0)) as r:
                info = self._decode("/upload/image", self._read(r), b"{}")
        except urllib.error.HTTPError as e:
            e.close()
            raise RenderFailed(f"the swap backend refused the upload of {name}: HTTP {e.code}",
                               code="UPLOAD_FAILED") from None
        if not isinstance(info, dict):
            info = {}
        sub = str(info.get("subfolder") or "")
        got = str(info.get("name") or stored)
        return f"{sub}/{got}" if sub else got

    def submit(self, graph: dict[str, Any], client_id: str) -> str:
        try:
            r = self.post_json("/prompt", {"prompt": graph, "client_id": client_id})
        except urllib.error.HTTPError as e:
            raise _rejected(e) from None
        if not isinstance(r, dict) or not r.get("prompt_id"):
            raise RenderFailed("the swap backend did not queue the workflow", code="WORKFLOW_REJECTED",
                               response=r)
        if r.get("node_errors"):
            raise RenderFailed("the swap backend rejected the workflow", code="WORKFLOW_REJECTED",
                               node_errors=r["node_errors"], hint=_node_hint(json.dumps(r["node_errors"])))
        return str(r["prompt_id"])

    def missing_nodes(self, classes: list[str]) -> list[str]:
        """Node classes the server does not know. ComfyUI always serves /object_info as a JSON object,
        so any other reply there means the URL is not ComfyUI (a web server on the wrong port, a login)."""
        try:
            info = self.get_json("/object_info")
        except urllib.error.HTTPError as e:
            e.close()
            raise self.not_comfyui("/object_info", f"HTTP {e.code}") from None
        if not isinstance(info, dict):
            raise self.not_comfyui("/object_info", f"JSON that is not a node list ({type(info).__name__})")
        # A generic JSON API is not a ComfyUI registry missing custom nodes.
        core = {"KSampler", "LoadImage", "CLIPTextEncode"}
        if not any(isinstance(info.get(c), dict) and isinstance(info[c].get("input"), dict) for c in core):
            raise self.not_comfyui("/object_info", "JSON without ComfyUI core node definitions")
        return [c for c in classes if c not in info]

    def download(self, item: dict[str, Any], dest: str) -> int:
        q = urllib.parse.urlencode({"filename": item.get("filename", ""), "subfolder": item.get("subfolder", ""),
                                    "type": item.get("type", "output")})
        tmp = f"{dest}.{os.getpid()}.part"
        n = 0
        try:
            with self._open(f"/view?{q}", timeout=max(self.timeout, 300.0)) as r, open(tmp, "wb") as out:
                while True:
                    block = self._read(r, 1 << 20)
                    if not block:
                        break
                    out.write(block)
                    n += len(block)
                left = getattr(r, "length", None)  # bytes the server announced but never sent
                if isinstance(left, int) and left > 0:
                    raise self.unreachable(f"the download broke off with {left} bytes missing")
            if n == 0:
                raise RenderFailed("the swap backend returned an empty video", code="NO_OUTPUT")
            os.replace(tmp, dest)
        except urllib.error.HTTPError as e:
            e.close()
            raise RenderFailed(f"could not download the swapped video: HTTP {e.code}", code="DOWNLOAD_FAILED") from None
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)
        return n


def _node_hint(text: str) -> str:
    return ("install Openjutsu's custom node packs and models on the ComfyUI machine and restart it; "
            f"see docs/SWAP.md and {UPSTREAM}") if "not found" in text.lower() or "does not exist" in text.lower() \
        else "check the ComfyUI console for the failing node"


def _rejected(e: urllib.error.HTTPError) -> RenderFailed:
    try:
        body = json.loads(e.read() or b"{}")
    except (ValueError, OSError, http.client.HTTPException):
        body = {}
    finally:
        e.close()
    err = body.get("error") if isinstance(body, dict) else None
    msg = err.get("message") if isinstance(err, dict) else str(err or f"HTTP {e.code}")
    text = json.dumps(body)
    return RenderFailed(f"the swap backend rejected the workflow: {msg}", code="WORKFLOW_REJECTED",
                        node_errors=(body.get("node_errors") if isinstance(body, dict) else None) or {},
                        hint=_node_hint(text))


def _execution_error(status: dict[str, Any]) -> str:
    messages = status.get("messages")
    if not isinstance(messages, list):
        return "the workflow failed"
    for m in messages:
        if isinstance(m, list) and len(m) == 2 and m[0] == "execution_error" and isinstance(m[1], dict):
            d = m[1]
            message = str(d.get("exception_message") or "the workflow failed").strip()
            return f"{d.get('node_type', 'node')} ({d.get('node_id', '?')}): {message}"
    return "the workflow failed"


def find_video(outputs: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any] | None:
    """The finished video among a prompt's outputs, preferring the "Final Video" assembly node."""
    final = [k for k, n in graph.items() if n.get("class_type") == "MiniMaxH3ChainAssemble"]
    order = final + sorted(k for k in outputs if k not in final)
    for nid in order:
        node_output = outputs.get(nid) or {}
        if not isinstance(node_output, dict):
            continue
        for key, items in node_output.items():
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, dict) and str(it.get("filename", "")).lower().endswith(VIDEO_EXT):
                    return {**it, "node": nid, "kind": key}
    return None


# --------------------------------------------------------------------------- running a swap

def _inputs(video: str, character: str) -> tuple[str, str]:
    v, c = os.path.abspath(os.path.expanduser(video)), os.path.abspath(os.path.expanduser(character))
    for label, p in (("video", v), ("character image", c)):
        if not os.path.isfile(p):
            raise UsageError(f"{label} not found: {p}", code="FILE_NOT_FOUND")
    return v, c


def default_output(req: dict[str, Any], base_dir: str) -> str:
    return os.path.join(swap_dir(base_dir), swap_key(req) + ".mp4")


def run_swap(video: str, character: str, mode: str = "simple", prompt: str = "", target: str = "", seed: int = 0,
             out: str | None = None, url: str | None = None, timeout: float = 3600.0, poll: float = 2.0,
             force: bool = False, progress: Progress | None = None) -> dict[str, Any]:
    """Run one swap on the backend and download the result to `out`. Raises CliError subclasses:
    UsageError (bad input), BackendUnreachable (exit 7), MissingTool (nodes or workflow), RenderFailed."""
    emit = progress or (lambda ev: None)
    if mode not in SWAP_MODES:
        raise UsageError(f"unknown swap mode {mode!r}", hint=f"choose from {list(SWAP_MODES)}")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise UsageError("seed must be a non-negative integer")
    video, character = _inputs(video, character)
    req = {"video": video, "character": character, "mode": mode, "prompt": prompt, "target": target, "seed": seed}
    key = swap_key(req)
    out = os.path.abspath(out or default_output(req, os.getcwd()))
    result: dict[str, Any] = {"output": out, "key": key, "mode": mode, "video": video, "character": character,
                              "prompt": prompt, "target": target, "seed": seed}
    if os.path.isfile(out) and not force:
        emit({"stage": "done", "cached": True})
        return {**result, "cached": True, "bytes": os.path.getsize(out)}
    base = backend_url(url)
    workflow = load_workflow(mode)
    api = Comfy(base)
    t_start = time.monotonic()
    emit({"stage": "connect", "url": base})
    missing = api.missing_nodes(node_classes(patch_workflow(workflow, mode, "v", "c")))
    if missing:
        raise MissingTool(f"the swap backend is missing {len(missing)} node type(s): {', '.join(missing[:8])}",
                          code="MISSING_NODES", missing=missing, hint=_node_hint("not found"))
    emit({"stage": "upload", "file": os.path.basename(video)})
    v_name = api.upload(video)
    emit({"stage": "upload", "file": os.path.basename(character)})
    c_name = api.upload(character)
    graph = patch_workflow(workflow, mode, v_name, c_name, prompt, target, seed)
    pid = api.submit(graph, "kstudio-" + uuid.uuid4().hex[:12])
    emit({"stage": "queued", "prompt_id": pid})
    deadline = t_start + timeout
    hiccups = 0
    while True:
        try:
            hist = api.get_json(f"/history/{urllib.parse.quote(pid)}")
            if not isinstance(hist, dict):
                raise api.not_comfyui("/history", "JSON that is not a history object")
            hiccups = 0
        except (BackendUnreachable, urllib.error.HTTPError) as e:
            if isinstance(e, urllib.error.HTTPError):
                e.close()
            hiccups += 1  # a busy GPU box or its proxy can drop a poll; give up after a few in a row
            if isinstance(e, BackendUnreachable) and e.details.get("reason") == "not_comfyui":
                raise
            if hiccups >= 5:
                if isinstance(e, BackendUnreachable):
                    raise
                raise api.unreachable(f"/history answered HTTP {e.code} {hiccups} times in a row") from None
            hist = None
        entry = hist.get(pid) if isinstance(hist, dict) else None
        if isinstance(entry, dict):
            status = entry.get("status") or {}
            if not isinstance(status, dict) or not isinstance(entry.get("outputs", {}), dict):
                raise api.not_comfyui("/history", "malformed prompt status or outputs")
            if status.get("messages") is not None and not isinstance(status["messages"], list):
                raise api.not_comfyui("/history", "prompt messages that are not a list")
            if status.get("status_str") == "error":
                msg = _execution_error(status)
                raise RenderFailed(f"the swap failed on the backend: {msg}", code="SWAP_FAILED", prompt_id=pid,
                                   hint=_node_hint(msg))
            if status.get("completed", True) and entry.get("outputs") is not None:
                item = find_video(entry.get("outputs") or {}, graph)
                if item is None:
                    raise RenderFailed("the swap finished without a video output", code="NO_OUTPUT", prompt_id=pid)
                break
        stage, pos = "running", None
        try:
            q = api.get_json("/queue") or {}
            if not isinstance(q, dict):
                raise api.not_comfyui("/queue", "JSON that is not a queue object")
            pending_items = q.get("queue_pending") or []
            if not isinstance(pending_items, list):
                raise api.not_comfyui("/queue", "queue_pending that is not a list")
            pending = [x[1] for x in pending_items if isinstance(x, list) and len(x) > 1]
            if pid in pending:
                stage, pos = "queued", pending.index(pid) + 1
        except urllib.error.HTTPError as e:
            e.close()  # queue position is optional progress, not the job result
        except (BackendUnreachable, ValueError):
            pass
        emit({"stage": stage, "position": pos, "elapsed": round(time.monotonic() - t_start, 1), "prompt_id": pid})
        if time.monotonic() > deadline:
            raise RenderFailed(f"the swap did not finish within {timeout:.0f} s", code="SWAP_TIMEOUT", prompt_id=pid,
                               hint="raise --timeout or check the ComfyUI queue")
        time.sleep(max(poll, 0.01))
    emit({"stage": "download", "file": item.get("filename")})
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    n = api.download(item, out)
    meta = {**result, "prompt_id": pid, "backend_file": item.get("filename"), "bytes": n,
            "seconds": round(time.monotonic() - t_start, 1), "workflow": os.path.basename(workflow_path(mode))}
    with open(out + ".swap.json", "w", encoding="utf-8") as fh:
        json.dump({k: v for k, v in meta.items() if k != "output"}, fh, indent=2)
    emit({"stage": "done", "cached": False})
    return {**meta, "cached": False}


# --------------------------------------------------------------------------- clip scene hook

_WARNED: set[str] = set()


@contextlib.contextmanager
def _locked(path: str) -> Iterator[None]:
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield
        return
    with open(path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _warn_once(key: str, msg: str) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        print(f"kstudio: {msg}", file=sys.stderr)


def resolve_clip_source(req: dict[str, Any], base_dir: str) -> str:
    """Footage file for a clip's swap source: the swapped video in the job folder when it exists or
    the backend can make it now, else the input video (with one warning). Never raises: whatever the
    backend answers, the error goes to stderr and the input video plays (a missing input video is then
    reported by the clip scene like any missing footage)."""
    try:
        key = swap_key(req)
        out = default_output(req, base_dir)
    except OSError as e:  # an input file is missing or unreadable, so there is nothing to swap
        _warn_once(str(req.get("video")), f"swap source skipped ({e}); playing the input video")
        return str(req["video"])
    if os.path.isfile(out):
        return out
    if not os.environ.get(ENV_URL):
        _warn_once(key, f"swap source not made ({ENV_URL} is not set); playing the input video. "
                        f"Run `kstudio swap --spec <project>` once a backend is available.")
        return str(req["video"])
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with _locked(out + ".lock"):
            if not os.path.isfile(out):
                run_swap(req["video"], req["character"], req["mode"], req["prompt"], req["target"], int(req["seed"]),
                         out=out)
        return out
    except Exception as e:  # any backend failure falls back to the input video; never into the frame
        _warn_once(key, f"swap source failed ({getattr(e, 'code', type(e).__name__)}: {e}); playing the input video")
        return str(req["video"])


def run_spec(spec_path: str, url: str | None = None, timeout: float = 3600.0, poll: float = 2.0, force: bool = False,
             progress: Progress | None = None) -> dict[str, Any]:
    """Make every swap source of a spec, into the folder its clip scenes read from."""
    spec = load_spec(spec_path)
    base = str(spec.get("_dir", "."))
    reqs = swap_requests(spec)
    done = []
    for r in reqs:
        out = default_output(r, base) if os.path.isfile(r["video"]) and os.path.isfile(r["character"]) else None
        res = run_swap(r["video"], r["character"], r["mode"], r["prompt"], r["target"], r["seed"], out=out, url=url,
                       timeout=timeout, poll=poll, force=force, progress=progress)
        done.append({"scene": r["path"], **res})
    return {"spec": os.path.abspath(spec_path), "swaps": done, "count": len(done)}


# --------------------------------------------------------------------------- command line

def _cli_progress(as_json: bool) -> Progress:
    def emit(ev: dict[str, Any]) -> None:
        if as_json:
            sys.stderr.write(json.dumps({"event": "progress", **ev}) + "\n")
        else:
            bits = [f"{k}={v}" for k, v in ev.items() if k != "stage" and v is not None]
            sys.stderr.write(f"  swap: {ev.get('stage')} {' '.join(bits)}\n")
        sys.stderr.flush()
    return emit


def cmd_swap(a: Any) -> dict[str, Any]:
    emit = _cli_progress(bool(getattr(a, "json", False)))
    if a.spec:
        if a.video or a.character:
            raise UsageError("use either --spec or --video/--character")
        return run_spec(a.spec, url=a.url, timeout=a.timeout, poll=a.poll, force=a.force, progress=emit)
    if not a.video or not a.character:
        raise UsageError("kstudio swap needs --video and --character (or --spec DIR)",
                         hint="kstudio swap --video clip.mp4 --character face.png [--mode object --prompt ...]")
    return run_swap(a.video, a.character, a.mode, a.prompt or "", a.target or "", a.seed, out=a.output, url=a.url,
                    timeout=a.timeout, poll=a.poll, force=a.force, progress=emit)


def register(subparsers: Any) -> None:
    """Add `kstudio swap` to the command line (called by kstudio.cli)."""
    s = subparsers.add_parser(
        "swap", help="optional: swap a character into a video on a ComfyUI backend (KSTUDIO_COMFY_URL)",
        description="Replace a person (simple) or change an object (object) in a video with Openjutsu's workflows "
                    f"on a ComfyUI server at ${ENV_URL}. Unset or unreachable backend: exit 7 (BACKEND_UNREACHABLE).")
    s.add_argument("--video", help="source video")
    s.add_argument("--character", help="character / reference image")
    s.add_argument("--mode", choices=list(SWAP_MODES), default="simple",
                   help="simple: the character replaces a person; object: change a tracked thing")
    s.add_argument("--prompt", default="", help="simple: who gets replaced; object: what the target becomes")
    s.add_argument("--target", default="", help="what SAM tracks (simple default: person; object default: hair)")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("-o", "--output", help="output .mp4 (default ./out/swap/<key>.mp4)")
    s.add_argument("--spec", help="make every clip swap source in this project instead")
    s.add_argument("--url", help=f"backend URL (default ${ENV_URL})")
    s.add_argument("--timeout", type=float, default=3600.0, help="give up after this many seconds")
    s.add_argument("--poll", type=float, default=2.0, help="seconds between status polls")
    s.add_argument("--force", action="store_true", help="run again even if the output exists")
    s.add_argument("--json", action="store_true", help="write exactly one JSON object to stdout")
    s.set_defaults(fn=cmd_swap)


# --------------------------------------------------------------------------- MCP

def t_swap(args: dict[str, Any], progress: Callable[..., None]) -> dict[str, Any]:
    stages = {"connect": 2, "upload": 5, "queued": 10, "running": 50, "download": 95, "done": 100}

    def emit(ev: dict[str, Any]) -> None:
        progress(stages.get(str(ev.get("stage")), None), 100, str(ev.get("stage")))

    r = run_swap(str(args["video"]), str(args["character"]), str(args.get("mode") or "simple"),
                 str(args.get("prompt") or ""), str(args.get("target") or ""), int(args.get("seed") or 0),
                 out=args.get("output"), timeout=float(args.get("timeout") or 3600.0), progress=emit)
    return {"ok": True, **r}


MCP_TOOLS: list[dict[str, Any]] = [{
    "name": "swap",
    "title": "Swap a character into a video (optional backend)",
    "description": "Optional. Replace a person in a video with a character image (mode simple), or change a tracked "
                   "object (mode object), on a ComfyUI server running Openjutsu's workflows, set by KSTUDIO_COMFY_URL. "
                   "Downloads the MP4 for use as a clip scene's src. Returns BACKEND_UNREACHABLE when no backend is "
                   "configured or reachable; nothing else depends on it.",
    "properties": {
        "video": {"type": "string", "description": "Path of the source video."},
        "character": {"type": "string", "description": "Path of the character / reference image."},
        "mode": {"type": "string", "enum": list(SWAP_MODES), "description": "simple (default) or object."},
        "prompt": {"type": "string", "description": "simple: who gets replaced; object: what the target becomes."},
        "target": {"type": "string", "description": "What SAM tracks (simple: person, object: hair by default)."},
        "seed": {"type": "integer", "minimum": 0, "description": "Generation seed. Default 0."},
        "output": {"type": "string", "description": "Output .mp4. Default ./out/swap/<key>.mp4."},
        "timeout": {"type": "number", "minimum": 1, "description": "Seconds to wait. Default 3600."},
    },
    "required": ["video", "character"],
    "read_only": False, "destructive": False, "idempotent": True,
    "output": {"output": {"type": "string"}, "key": {"type": "string"}, "cached": {"type": "boolean"},
               "prompt_id": {"type": "string"}},
}]
MCP_HANDLERS: dict[str, Callable[[dict[str, Any], Callable[..., None]], dict[str, Any]]] = {"swap": t_swap}
