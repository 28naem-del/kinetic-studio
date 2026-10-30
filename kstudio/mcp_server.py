"""Kinetic Studio as a local Model Context Protocol server (stdio, optional streamable HTTP).

    python -m kstudio.mcp_server                  stdio server (what MCP clients launch)
    python -m kstudio.mcp_server --http 8765      streamable HTTP on http://127.0.0.1:8765/mcp
    python -m kstudio.mcp_server --list-tools     print the tool list as JSON and exit
    python -m kstudio.mcp_server --call validate_spec '{"spec_path": "examples/kinetic"}'
                                                  run one tool without a client, print its JSON result

No third-party dependency: the JSON-RPC 2.0 / MCP subset a tool server needs is implemented here.
Both protocol eras are answered on the same connection:

* 2026-07-28 (stateless): every request carries `_meta["io.modelcontextprotocol/protocolVersion"]`
  and client capabilities; `server/discover` describes the server; results carry `resultType`.
* 2025-11-25, 2025-06-18, 2025-03-26, 2024-11-05: the `initialize` handshake.

Tools (one source of truth, `TOOLS`, also exported to docs/tools/ by docs/tools/generate.py):
list_modes, list_scenes, get_schema, validate_spec, new_spec, preview, render, audit. Every tool
takes a JSON Schema-described object and returns one JSON object whose `ok` field says whether the
thing asked about is good (spec valid, video rendered and audited, ...).

stdout carries protocol messages only. The engine and ffmpeg never see the protocol streams: fd 0
is pointed at /dev/null and fd 1 at stderr once the server owns private copies of them.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .modes import MODES
from .presets import PRESETS, QUALITY

SERVER_NAME = "kinetic-studio"
SERVER_TITLE = "Kinetic Studio"
MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SUPPORTED_VERSIONS = (MODERN_VERSION, *LEGACY_VERSIONS)
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
TOOL_PREFIX = "kstudio_"  # function-calling exports use prefixed names; tools/call accepts both

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
HEADER_MISMATCH, MISSING_CLIENT_CAPABILITY, UNSUPPORTED_VERSION = -32020, -32021, -32022

INSTRUCTIONS = (
    "Kinetic Studio renders motion-graphics videos (MP4, H.264 + AAC) from a JSON spec, locally. "
    "Typical flow: list_modes -> get_schema(mode) -> new_spec(mode, dir[, spec_json]) -> validate_spec -> "
    "preview (contact sheet) -> render (progress notifications) -> audit. Times and lengths are seconds or "
    "beat units at the music tempo: \"4b\", \"1/2b\", \"1bar\", \"drop-2b\"; keep scene lengths whole beats "
    "(2b/4b/8b). Fix every validate_spec error before rendering. Paths are local file paths."
)


def log(msg: str) -> None:
    sys.stderr.write(f"kstudio-mcp: {msg}\n")
    sys.stderr.flush()


# --------------------------------------------------------------------------- tool errors

class ToolError(Exception):
    """A tool could not do its job: reported as a tool result with isError true (not a protocol error)."""

    def __init__(self, message: str, code: str = "TOOL_ERROR", hint: str | None = None, **details: Any):
        super().__init__(message)
        self.message, self.code, self.hint, self.details = message, code, hint, details

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        d.update(self.details)
        return d


class Cancelled(KeyboardInterrupt):
    """Raised inside a running tool when its request was cancelled (the render loop stops on it)."""


def _classify(exc: BaseException, phase: str) -> ToolError:
    if isinstance(exc, ToolError):
        return exc
    try:  # the command line's error table, when present
        from .exitcodes import classify
        e = classify(exc, phase)
        return ToolError(e.message, e.code, e.hint, **e.details)
    except ImportError:
        pass
    code = getattr(exc, "code", None)
    if isinstance(exc, SystemExit):
        return ToolError(str(exc.code).replace("kstudio: ", ""), "MISSING_TOOL")
    if isinstance(code, str):
        return ToolError(str(exc), code)
    if phase == "spec" and isinstance(exc, (KeyError, ValueError, TypeError, IndexError)):
        return ToolError(str(exc.args[0] if isinstance(exc, KeyError) and exc.args else exc), "SPEC_INVALID")
    return ToolError(f"{type(exc).__name__}: {exc}", "RENDER_FAILED" if phase == "render" else "INTERNAL_ERROR")


# --------------------------------------------------------------------------- tool definitions

@dataclass
class Tool:
    name: str
    title: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    handler: Callable[[dict[str, Any], Callable[..., None]], dict[str, Any]]
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True
    output: dict[str, Any] = field(default_factory=dict)

    def input_schema(self) -> dict[str, Any]:
        s: dict[str, Any] = {"type": "object", "properties": self.properties, "additionalProperties": False}
        if self.required:
            s["required"] = list(self.required)
        return s

    def output_schema(self) -> dict[str, Any]:
        props = {"ok": {"type": "boolean", "description": "true when the thing asked about is good"},
                 "error": {"type": "object", "description": "present when the tool failed: {code, message, hint}",
                           "properties": {"code": {"type": "string"}, "message": {"type": "string"},
                                          "hint": {"type": "string"}}}}
        props.update(self.output)
        return {"type": "object", "properties": props, "required": ["ok"]}

    def annotations(self) -> dict[str, Any]:
        return {"title": self.title, "readOnlyHint": self.read_only, "destructiveHint": self.destructive,
                "idempotentHint": self.idempotent, "openWorldHint": False}

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "description": self.description,
                "inputSchema": self.input_schema(), "outputSchema": self.output_schema(),
                "annotations": self.annotations()}


MODE_NAMES = tuple(MODES)
PRESET_NAMES = tuple(PRESETS)
QUALITY_NAMES = tuple(QUALITY)
FPS_VALUES = (24, 25, 30, 50, 60)
SNAP_NAMES = ("beat", "8th", "16th", "bar")

_SPEC_PATH = {"type": "string", "description": "Path of a spec file (spec.json / spec.jsonc / spec.js) or of a "
                                               "project directory that contains one."}
_SPEC_JSON = {"type": "string", "description": "The spec itself as JSON text (comments and trailing commas "
                                               "allowed). Use instead of spec_path."}
_MODE = {"type": "string", "enum": list(MODE_NAMES), "description": "Video mode."}
_PRESET = {"type": "string", "enum": list(PRESET_NAMES),
           "description": "Output size: reel/story 1080x1920, landscape/youtube 1920x1080, square 1080x1080, "
                          "portrait 1080x1350. Default reel."}


# --------------------------------------------------------------------------- helpers

def _arg(args: dict[str, Any], key: str, default: Any = None) -> Any:
    v = args.get(key)
    return default if v is None else v


def _abspath(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))


def _load_spec_args(args: dict[str, Any]) -> dict[str, Any]:
    from .spec import load_spec, parse_spec_text
    text, path = args.get("spec_json"), args.get("spec_path")
    if text is not None and path is not None:
        raise ToolError("pass either spec_path or spec_json, not both", "USAGE")
    try:
        if text is not None:
            spec = parse_spec_text(str(text), "<spec_json>")
            spec.setdefault("_dir", _abspath(_arg(args, "base_dir", os.getcwd())))
            spec.setdefault("_path", "<spec_json>")
            return spec
        if path is None:
            raise ToolError("spec_path (or spec_json) is required", "USAGE",
                            hint="pass the project directory created by new_spec")
        return load_spec(_abspath(str(path)))
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _classify(e, "spec") from None


def _schema_api() -> Any:
    """The engine's generated spec schema + validator (kstudio.schema), when this build has it."""
    try:
        from . import schema
    except Exception:  # noqa: BLE001 - optional module
        return None
    return schema if hasattr(schema, "spec_schema") and hasattr(schema, "validate_spec") else None


def _issue(path: str, message: str, hint: str | None = None, code: str = "SPEC_INVALID") -> dict[str, Any]:
    return {"path": path, "message": message, "hint": hint, "code": code}


def _basic_validate(spec: dict[str, Any]) -> dict[str, Any]:
    """Validation without kstudio.schema: build the Film (time expressions, scene types, transitions)
    and run its beat-grid lint."""
    from .film import Film
    from .pipeline import beat_grid_for
    try:
        film = Film(spec, 1080, 1920, beat_grid=beat_grid_for(spec))
    except Exception as e:  # noqa: BLE001
        te = _classify(e, "spec")
        return {"valid": False, "errors": [_issue("", te.message, te.hint, te.code)], "warnings": [], "summary": None}
    s = film.summary()
    warnings = [_issue("", f"{w['where']}: {w['msg']}", w["msg"], "LINT") for w in film.lint() if w["level"] == "warn"]
    return {"valid": True, "errors": [], "warnings": warnings,
            "summary": {"mode": s["mode"], "duration": s["duration"], "bpm": round(film.tl.bpm, 3),
                        "scenes": len(s["scenes"]), "transitions": len(s["transitions"])}}


def _validate(spec: dict[str, Any], strict: bool = False) -> dict[str, Any]:
    api = _schema_api()
    if api is not None:
        try:
            r = api.validate_spec(spec, strict=strict)
            if isinstance(r, dict) and "errors" in r:
                return {"valid": bool(r.get("valid", not r["errors"])), "errors": list(r["errors"]),
                        "warnings": list(r.get("warnings", [])), "summary": r.get("summary")}
        except Exception as e:  # noqa: BLE001 - fall back to the build check below
            log(f"schema validator failed ({type(e).__name__}: {e}); using the build check")
    return _basic_validate(spec)


# --------------------------------------------------------------------------- handlers

def t_list_modes(args: dict[str, Any], progress: Callable[..., None]) -> dict[str, Any]:
    from .modes import MODES
    from .presets import PRESETS, QUALITY
    modes = []
    for name, m in MODES.items():
        modes.append({"mode": name, "description": m["description"], "theme": m["theme"], "music": m["music"],
                      "transition": m["transition"], "audit": m.get("audit", {})})
    return {"ok": True, "modes": modes,
            "presets": {k: {"width": w, "height": h} for k, (w, h) in PRESETS.items()},
            "quality": list(QUALITY), "fps": list(FPS_VALUES),
            "time_units": ["seconds: 1.5", "beats: \"4b\", \"1/2b\", \"3/4b\"", "bars: \"1bar\", \"bar3\"",
                           "markers and scene ids: \"drop\", \"drop-2b\""]}


def t_list_scenes(args: dict[str, Any], progress: Callable[..., None]) -> dict[str, Any]:
    from .scenes import REGISTRY
    want, verbose = args.get("type"), bool(_arg(args, "verbose", True))
    if want is not None and want not in REGISTRY:
        raise ToolError(f"unknown scene type {want!r}", "USAGE", hint=f"choose from {sorted(REGISTRY)}")
    out = []
    for name, cls in sorted(REGISTRY.items()):
        if want is not None and name != want:
            continue
        para = (cls.__doc__ or "").strip().split("\n\n", 1)[0]
        item: dict[str, Any] = {"type": name, "summary": " ".join(para.split())}
        if verbose:
            item["params"] = json.loads(json.dumps(cls.defaults, default=str))
        out.append(item)
    return {"ok": True, "count": len(out), "scenes": out,
            "common_keys": ["id", "dur", "at", "transition", "transition_dur", "transition_sfx", "ground",
                            "ground_style", "camera"]}


def _fallback_schema() -> dict[str, Any]:
    from .modes import THEMES
    from .scenes import REGISTRY
    time_t = {"type": ["number", "string"], "description": "seconds or a beat expression (\"4b\", \"1bar\", \"drop-2b\")"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "Kinetic Studio spec", "type": "object",
        "required": ["scenes"],
        "properties": {
            "mode": {"enum": list(MODE_NAMES)}, "theme": {"type": ["string", "object"], "enum_hint": sorted(THEMES)},
            "handle": {"type": "string"}, "bpm": {"type": "number"},
            "music": {"type": "object", "properties": {"style": {"type": "string"}, "bpm": {"type": "number"},
                                                       "file": {"type": "string"}}},
            "snap": {"type": ["boolean", "string"]}, "transition": {"type": ["string", "object"]},
            "markers": {"type": "array", "items": {"type": "object", "required": ["kind", "at"]}},
            "scenes": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["type"],
                "properties": {"type": {"enum": sorted(REGISTRY)}, "id": {"type": "string"}, "dur": time_t,
                               "at": time_t, "transition": {"type": ["string", "object"]}}}},
        },
        "x-scene-params": {n: json.loads(json.dumps(c.defaults, default=str)) for n, c in sorted(REGISTRY.items())},
    }


def t_get_schema(args: dict[str, Any], progress: Callable[..., None]) -> dict[str, Any]:
    from .modes import mode_defaults
    from .spec import example_spec, parse_spec_text
    mode, types = args.get("mode"), args.get("scene_types")
    api = _schema_api()
    if api is not None:
        try:
            schema, source = api.spec_schema(list(types) if types else None), "kstudio.schema"
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"cannot build the schema: {e}", "INTERNAL_ERROR") from None
    else:
        schema, source = _fallback_schema(), "builtin"
    out: dict[str, Any] = {"ok": True, "schema": schema, "source": source}
    if mode is not None:
        if mode not in MODE_NAMES:
            raise ToolError(f"unknown mode {mode!r}", "MODE_UNKNOWN", hint=f"choose from {list(MODE_NAMES)}")
        out["mode_defaults"] = mode_defaults(mode)
        ex = example_spec(mode)
        if ex is not None and bool(_arg(args, "include_example", True)):
            text = ex.read_text(encoding="utf-8")
            out["example"] = parse_spec_text(text, str(ex)) if ex.suffix in (".json", ".jsonc") else text
            out["example_path"] = str(ex)
    return out


def t_validate_spec(args: dict[str, Any], progress: Callable[..., None]) -> dict[str, Any]:
    try:
        spec = _load_spec_args(args)
    except ToolError as e:
        if e.code == "USAGE":
            raise
        return {"ok": False, "valid": False, "errors": [_issue("", e.message, e.hint, e.code)], "warnings": [],
                "summary": None}
    r = _validate(spec, bool(_arg(args, "strict", False)))
    out = {"ok": r["valid"], **r, "spec_path": spec.get("_path")}
    api = _schema_api()
    if (r["errors"] or r["warnings"]) and api is not None and hasattr(api, "fix_prompt"):
        try:
            out["fix"] = api.fix_prompt(r, spec)
        except Exception:  # noqa: BLE001
            pass
    return out


def t_new_spec(args: dict[str, Any], progress: Callable[..., None]) -> dict[str, Any]:
    from .spec import examples_dir, parse_spec_text
    mode, target = args["mode"], args.get("dir")
    if mode not in MODE_NAMES:
        raise ToolError(f"unknown mode {mode!r}", "MODE_UNKNOWN", hint=f"choose from {list(MODE_NAMES)}")
    src = examples_dir() / mode
    if not src.is_dir():
        raise ToolError(f"no example for mode {mode!r} in {examples_dir()}", "MODE_UNKNOWN")
    dst = Path(_abspath(str(target)))
    if dst.exists() and not dst.is_dir():
        raise ToolError(f"{dst} exists and is not a directory", "USAGE")
    if dst.exists() and any(dst.iterdir()) and not _arg(args, "force", False):
        raise ToolError(f"{dst} is not empty", "DIR_NOT_EMPTY", hint="pick an empty directory or pass force: true")
    spec_text = args.get("spec_json")
    if spec_text is not None:
        parsed = parse_spec_text(str(spec_text), "<spec_json>")  # raises SPEC_PARSE before anything is written
        parsed.setdefault("mode", mode)
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    for f in sorted(src.iterdir()):
        if f.name in ("out", ".kstudio_work"):
            continue
        if f.is_file():
            shutil.copy2(f, dst / f.name)
            written.append(str(dst / f.name))
        elif f.is_dir():
            shutil.copytree(f, dst / f.name, dirs_exist_ok=True)
            written.append(str(dst / f.name))
    if spec_text is not None:
        for old in ("spec.jsonc", "spec.mjs", "spec.js"):
            (dst / old).unlink(missing_ok=True)
        (dst / "spec.json").write_text(json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    spec_file = next((str(dst / n) for n in ("spec.json", "spec.jsonc", "spec.mjs", "spec.js") if (dst / n).exists()),
                     None)
    return {"ok": True, "dir": str(dst), "spec_path": spec_file, "mode": mode, "files": written,
            "from_example": spec_text is None,
            "next": ["validate_spec {spec_path}", "preview {spec_path}", "render {spec_path}"]}


def _image_content(path: str, max_w: int = 800) -> dict[str, Any] | None:
    try:
        import cv2
        img = cv2.imread(path)
        if img is None:
            return None
        h, w = img.shape[:2]
        if w > max_w:
            img = cv2.resize(img, (max_w, int(round(h * max_w / w))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            return None
        return {"type": "image", "data": base64.b64encode(buf.tobytes()).decode("ascii"), "mimeType": "image/jpeg"}
    except Exception:  # noqa: BLE001 - the image is a convenience, the paths are the result
        return None


def t_preview(args: dict[str, Any], progress: Callable[..., None]) -> dict[str, Any]:
    from .pipeline import beat_grid_for
    from .preview import preview
    spec = _load_spec_args(args)
    out_dir = _abspath(_arg(args, "out_dir", os.path.join(spec["_dir"], "out", "preview")))
    times = args.get("at")
    scale = float(_arg(args, "scale", 0.5))
    progress(0, 100, "rendering stills")
    try:
        r = preview(spec, out_dir, _arg(args, "preset", "reel"), [float(t) for t in times] if times else None,
                    scale, "draft", beat_grid_for(spec))
    except Exception as e:  # noqa: BLE001
        raise _classify(e, "spec") from None
    progress(100, 100, "done")
    out: dict[str, Any] = {"ok": True, "contact_sheet": r["contact_sheet"], "stills": r["stills"],
                           "out_dir": out_dir, "summary": r["summary"]}
    if bool(_arg(args, "include_image", True)) and r["contact_sheet"]:
        img = _image_content(r["contact_sheet"])
        if img is not None:
            out["_content"] = [img]
    return out


def t_render(args: dict[str, Any], progress: Callable[..., None]) -> dict[str, Any]:
    from .pipeline import build
    spec = _load_spec_args(args)
    preset = _arg(args, "preset", "reel")
    if preset not in PRESET_NAMES:
        raise ToolError(f"unknown preset {preset!r}", "USAGE", hint=f"choose from {list(PRESET_NAMES)}")
    out = args.get("output") or os.path.join(spec["_dir"], "out", f"{Path(spec['_dir']).name}_{preset}.mp4")
    out = _abspath(str(out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    workers = args.get("workers")
    progress(0, 100, "planning")

    def on_frames(info: dict[str, Any]) -> None:
        total = max(1, int(info.get("total") or 1))
        eta = info.get("eta")
        progress(1 + 89 * min(1.0, info["frames"] / total), 100,
                 f"frames {info['frames']}/{total} at {info.get('fps', 0):.1f} fps"
                 + (f", eta {eta:.0f}s" if eta is not None else ""))

    def say(msg: str) -> None:
        log(msg)
        progress(None, 100, msg)

    try:
        r = build(spec, out, preset, int(_arg(args, "fps", 30)), _arg(args, "quality", "standard"),
                  bool(_arg(args, "k4", False)), "auto" if workers is None else int(workers),
                  bool(_arg(args, "fresh", False)), bool(_arg(args, "audit", True)),
                  on_progress=on_frames, say=say, snap=args.get("snap"))
    except Cancelled:
        raise
    except BaseException as e:  # noqa: BLE001 - SystemExit from a missing ffmpeg included
        if isinstance(e, KeyboardInterrupt):
            raise
        raise _classify(e, "render") from None
    progress(100, 100, "done")
    a = r.get("audit") or {}
    failed = {k: v for k, v in a.get("checks", {}).items() if not v.get("ok")}
    res: dict[str, Any] = {
        "ok": bool(r.get("pass", True)), "output": r["output"], "pass": bool(r.get("pass", True)),
        "duration": r["duration"], "frames": r["frames"], "size": r["size"], "fps": r["fps"],
        "quality": r["quality"], "seconds": r.get("total_seconds"),
        "report_path": os.path.join(os.path.dirname(out), os.path.splitext(os.path.basename(out))[0] + ".report.json"),
        "loudness": (r.get("audio") or {}).get("final"), "failed_checks": failed,
        "lint_warnings": [w for w in r.get("lint", []) if w.get("level") == "warn"],
    }
    res["_content"] = [{"type": "resource_link", "uri": Path(r["output"]).as_uri(), "name": os.path.basename(out),
                        "mimeType": "video/mp4", "description": "rendered video"}]
    return res


def t_audit(args: dict[str, Any], progress: Callable[..., None]) -> dict[str, Any]:
    from .audit import audit
    from .modes import mode_defaults
    video = _abspath(str(args["video"]))
    if not os.path.isfile(video):
        raise ToolError(f"video not found: {video}", "FILE_NOT_FOUND")
    mode = args.get("mode")
    if mode is not None and mode not in MODE_NAMES:
        raise ToolError(f"unknown mode {mode!r}", "MODE_UNKNOWN", hint=f"choose from {list(MODE_NAMES)}")
    th = dict(mode_defaults(mode).get("audit", {})) if mode else {}
    for k in ("min_motion", "max_hold"):
        if args.get(k) is not None:
            th[k] = float(args[k])
    expect: dict[str, Any] = {}
    if args.get("fps") is not None:
        expect["fps"] = float(args["fps"])
    if args.get("frames") is not None:
        expect["frames"] = int(args["frames"])
    progress(0, 100, "probing and decoding")
    try:
        r = audit(video, expect, th)
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, KeyboardInterrupt):
            raise
        raise _classify(e, "audit") from None
    progress(100, 100, "done")
    failed = sorted(k for k, v in r["checks"].items() if not v["ok"])
    return {"ok": bool(r["pass"]), "pass": bool(r["pass"]), "failed": failed, **r}


TOOLS: list[Tool] = [
    Tool("list_modes", "List modes",
         "List the video modes (kinetic, explainer, promo, data, social) with their theme, music style and tempo, "
         "default transition and audit thresholds, plus the output presets, quality levels, frame rates and the "
         "time units specs use. Call this first.",
         {}, (), t_list_modes,
         output={"modes": {"type": "array", "items": {"type": "object"}}, "presets": {"type": "object"}}),
    Tool("list_scenes", "List scene types",
         "List the scene types a spec can use (title, slam, counter, bars, karaoke, ...) with a one-line summary "
         "and their parameters with default values. Pass `type` for one scene.",
         {"type": {"type": "string", "description": "Only this scene type, e.g. \"counter\"."},
          "verbose": {"type": "boolean", "description": "Include parameters and defaults. Default true."}},
         (), t_list_scenes,
         output={"scenes": {"type": "array", "items": {"type": "object"}}}),
    Tool("get_schema", "Get the spec JSON Schema",
         "Return the JSON Schema of a spec (draft 2020-12). With `mode`, also return that mode's defaults and its "
         "example spec, the best starting point for writing a new one.",
         {"mode": _MODE,
          "scene_types": {"type": "array", "items": {"type": "string"},
                          "description": "Narrow the scene part of the schema to these types. The full schema "
                                         "is large; narrowing keeps it small."},
          "include_example": {"type": "boolean", "description": "Include the mode's example spec. Default true."}},
         (), t_get_schema,
         output={"schema": {"type": "object"}, "example": {"type": ["object", "string"]}}),
    Tool("validate_spec", "Validate a spec",
         "Check a spec without rendering: structure, scene types, parameters, time expressions, transitions, "
         "then a dry build and the beat-grid lint. Returns ok, errors and warnings, each with a JSON Pointer path "
         "and a hint. Pass spec_path, or spec_json for a spec that is not on disk yet.",
         {"spec_path": _SPEC_PATH, "spec_json": _SPEC_JSON,
          "base_dir": {"type": "string", "description": "Directory relative asset paths in spec_json resolve "
                                                        "against. Default: the server's working directory."},
          "strict": {"type": "boolean", "description": "Treat unknown keys as errors. Default false."}},
         (), t_validate_spec,
         output={"valid": {"type": "boolean"}, "errors": {"type": "array", "items": {"type": "object"}},
                 "warnings": {"type": "array", "items": {"type": "object"}}, "summary": {"type": ["object", "null"]}}),
    Tool("new_spec", "Create a project",
         "Create a project directory from a mode's example (spec.json plus any assets). Pass spec_json to write "
         "your own spec instead of the example. Refuses a non-empty directory unless force is true.",
         {"mode": _MODE, "dir": {"type": "string", "description": "Project directory to create."},
          "spec_json": {"type": "string", "description": "Optional spec as JSON text to write as spec.json."},
          "force": {"type": "boolean", "description": "Write into a non-empty directory. Default false."}},
         ("mode", "dir"), t_new_spec, read_only=False, idempotent=False,
         output={"dir": {"type": "string"}, "spec_path": {"type": ["string", "null"]}}),
    Tool("preview", "Preview stills",
         "Render still frames (the middle of every scene and every transition, or the times in `at`) and a "
         "labelled contact sheet, in a few seconds, without audio. Returns the file paths and the contact sheet "
         "as an image. Use it to check layout before a full render.",
         {"spec_path": _SPEC_PATH, "spec_json": _SPEC_JSON,
          "at": {"type": "array", "items": {"type": "number"}, "description": "Times in seconds to render."},
          "preset": _PRESET,
          "scale": {"type": "number", "minimum": 0.1, "maximum": 1.0,
                    "description": "Resolution scale of the stills. Default 0.5."},
          "out_dir": {"type": "string", "description": "Where to write stills. Default <project>/out/preview."},
          "include_image": {"type": "boolean", "description": "Attach the contact sheet image. Default true."}},
         (), t_preview, read_only=False,
         output={"contact_sheet": {"type": ["string", "null"]}, "stills": {"type": "array", "items": {"type": "object"}}}),
    Tool("render", "Render the video",
         "Render the final MP4 (H.264 High + AAC 48 kHz, -14 LUFS, -1 dBTP) with motion blur, grade, music and "
         "sound effects, then audit it. Sends progress notifications when the request has a progressToken. "
         "Resumable: an interrupted render keeps finished chunks. ok is true when the video was written and "
         "passed the audit; failed_checks lists what did not.",
         {"spec_path": _SPEC_PATH,
          "output": {"type": "string", "description": "Output .mp4 path. Default <project>/out/<name>_<preset>.mp4."},
          "preset": _PRESET,
          "fps": {"type": "integer", "enum": list(FPS_VALUES), "description": "Frame rate. Default 30."},
          "quality": {"type": "string", "enum": list(QUALITY_NAMES),
                      "description": "draft (no motion blur, fast), standard (default) or high."},
          "k4": {"type": "boolean", "description": "Double the preset resolution (4K). Default false."},
          "workers": {"type": "integer", "minimum": 1, "description": "Render processes. Default: sized to free "
                                                                      "memory and CPUs."},
          "fresh": {"type": "boolean", "description": "Ignore finished chunks from an earlier run. Default false."},
          "audit": {"type": "boolean", "description": "Run the quality audit. Default true."},
          "snap": {"type": "string", "enum": list(SNAP_NAMES),
                   "description": "Snap seconds-valued scene lengths and starts to this beat division."}},
         ("spec_path",), t_render, read_only=False,
         output={"output": {"type": "string"}, "pass": {"type": "boolean"}, "report_path": {"type": "string"},
                 "failed_checks": {"type": "object"}}),
    Tool("audit", "Audit a video",
         "Measure a finished video: motion ratio and longest hold, black frames, codec and pixel format, "
         "loudness and true peak, frame rate (r = avg = n/d), frame count and audio/video length. ok is true "
         "when every check passes.",
         {"video": {"type": "string", "description": "Path of the video file."},
          "mode": {**_MODE, "description": "Use this mode's motion and hold thresholds."},
          "fps": {"type": "number", "description": "Expected frame rate."},
          "frames": {"type": "integer", "minimum": 1, "description": "Expected video frame count (+-1)."},
          "min_motion": {"type": "number", "minimum": 0, "maximum": 1, "description": "Minimum motion ratio."},
          "max_hold": {"type": "number", "minimum": 0, "description": "Longest allowed still hold in seconds."}},
         ("video",), t_audit,
         output={"pass": {"type": "boolean"}, "failed": {"type": "array", "items": {"type": "string"}},
                 "checks": {"type": "object"}}),
]


def _extension_tools(module: Any) -> list[Tool]:
    """Tools of an optional extension module: `MCP_TOOLS` (Tool objects, or dicts with name, title,
    description, properties / inputSchema, required, read_only, destructive, idempotent, output) and
    `MCP_HANDLERS` ({name: handler(args, progress) -> dict}; a dict tool takes its handler from here)."""
    handlers: dict[str, Any] = dict(getattr(module, "MCP_HANDLERS", None) or {})
    taken = {t.name for t in TOOLS}
    out: list[Tool] = []
    for item in getattr(module, "MCP_TOOLS", None) or []:
        if isinstance(item, Tool):
            tool = item
            if item.name in handlers:
                tool.handler = handlers[item.name]
        elif isinstance(item, dict) and item.get("name") in handlers:
            schema = item.get("inputSchema") or {}
            tool = Tool(str(item["name"]), str(item.get("title") or item["name"]), str(item.get("description", "")),
                        dict(item.get("properties") or schema.get("properties") or {}),
                        tuple(item.get("required") or schema.get("required") or ()), handlers[item["name"]],
                        read_only=bool(item.get("read_only", False)), destructive=bool(item.get("destructive", False)),
                        idempotent=bool(item.get("idempotent", False)), output=dict(item.get("output") or {}))
        else:
            log(f"extension tool skipped (no handler or unknown shape): {item!r:.80}")
            continue
        if tool.name in taken:
            log(f"extension tool {tool.name!r} skipped: the name is taken")
            continue
        taken.add(tool.name)
        out.append(tool)
    return out


def _load_extensions() -> None:
    """Optional extension hook: when a `kstudio.swap` module is installed, its MCP tools join the list
    (and docs/tools/ once regenerated). ImportError means it is not installed: nothing is added."""
    try:
        from kstudio import swap  # type: ignore[attr-defined,unused-ignore]
    except ImportError:
        return
    try:
        TOOLS.extend(_extension_tools(swap))
    except Exception as e:  # noqa: BLE001 - a broken optional extension must not take the built-in tools down
        log(f"optional extension kstudio.swap not loaded: {type(e).__name__}: {e}")


_load_extensions()
TOOL_INDEX = {t.name: t for t in TOOLS}


def find_tool(name: Any) -> Tool | None:
    if not isinstance(name, str):
        return None
    return TOOL_INDEX.get(name) or (TOOL_INDEX.get(name[len(TOOL_PREFIX):]) if name.startswith(TOOL_PREFIX) else None)


def tool_list() -> list[dict[str, Any]]:
    return [t.describe() for t in TOOLS]


def run_tool(name: str, arguments: dict[str, Any] | str | None = None) -> dict[str, Any]:
    """Run one tool in-process and return its JSON result: the dispatcher for function-calling loops.
    `name` may carry the `kstudio_` prefix; `arguments` may be a dict or the JSON text a model produced."""
    tool = find_tool(name)
    if tool is None:
        return {"ok": False, "error": {"code": "UNKNOWN_TOOL", "message": f"unknown tool {name!r}",
                                       "hint": f"tools: {', '.join(t.name for t in TOOLS)}"}}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError as e:
            return {"ok": False, "error": {"code": "INVALID_ARGUMENTS", "message": f"arguments are not JSON: {e}"}}
    data, _, _ = call_tool(tool, arguments)
    return data


# --------------------------------------------------------------------------- argument checking

_JSON_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool) or (isinstance(v, float) and v.is_integer()),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "array": lambda v: isinstance(v, list), "object": lambda v: isinstance(v, dict), "null": lambda v: v is None,
}


def check_args(tool: Tool, args: Any) -> tuple[dict[str, Any], list[str]]:
    """Check arguments against the tool's input schema (the subset the tools use). null = not given."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {}, ["arguments must be an object"]
    problems: list[str] = []
    clean = {k: v for k, v in args.items() if v is not None}
    for k in clean:
        if k not in tool.properties:
            problems.append(f"unknown argument {k!r}; expected one of {sorted(tool.properties)}")
    for k in tool.required:
        if k not in clean:
            problems.append(f"missing required argument {k!r}")
    for k, v in clean.items():
        s = tool.properties.get(k)
        if not s:
            continue
        types = s.get("type")
        types = [types] if isinstance(types, str) else (types or [])
        if types and not any(_JSON_TYPES[t](v) for t in types):
            problems.append(f"{k}: expected {' or '.join(types)}, got {type(v).__name__}")
            continue
        if "enum" in s and v not in s["enum"]:
            problems.append(f"{k}: {v!r} is not one of {s['enum']}")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if "minimum" in s and v < s["minimum"]:
                problems.append(f"{k}: must be >= {s['minimum']}")
            if "maximum" in s and v > s["maximum"]:
                problems.append(f"{k}: must be <= {s['maximum']}")
        if isinstance(v, list) and "items" in s:
            it = s["items"].get("type")
            if it and not all(_JSON_TYPES[it](x) for x in v):
                problems.append(f"{k}: every item must be a {it}")
        if s.get("type") == "integer" and isinstance(v, float):
            clean[k] = int(v)
    return clean, problems


def call_tool(tool: Tool, args: Any, progress: Callable[..., None] | None = None) -> tuple[dict[str, Any], list, bool]:
    """Run a tool. Returns (structured result, extra content blocks, is_error)."""
    progress = progress or (lambda *a, **k: None)
    clean, problems = check_args(tool, args)
    if problems:
        err = ToolError("; ".join(problems), "INVALID_ARGUMENTS", hint=f"see the inputSchema of {tool.name}")
        return {"ok": False, "error": err.as_dict()}, [], True
    try:
        data = tool.handler(clean, progress)
    except Cancelled:
        raise
    except ToolError as e:
        return {"ok": False, "error": e.as_dict()}, [], True
    except BaseException as e:  # noqa: BLE001 - one bad call must not take the server down
        if isinstance(e, KeyboardInterrupt):
            raise
        log(f"{tool.name} failed:\n{traceback.format_exc()}")
        te = _classify(e, "run")
        return {"ok": False, "error": te.as_dict()}, [], True
    extra = data.pop("_content", [])
    return json.loads(json.dumps(data, default=str)), extra, False


def tool_result(data: dict[str, Any], extra: list, is_error: bool) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text}, *extra], "structuredContent": data, "isError": is_error}


# --------------------------------------------------------------------------- protocol core

class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            d["data"] = self.data
        return d


def _error(rid: Any, e: RpcError) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": e.as_dict()}


class Progress:
    """notifications/progress for one request: strictly increasing, rate limited, silent after the end."""

    def __init__(self, send: Callable[[dict], None], token: Any, cancel: threading.Event, min_gap: float = 0.25):
        self.send, self.token, self.cancel, self.min_gap = send, token, cancel, min_gap
        self.last = -1.0
        self.t = 0.0
        self.closed = False

    def __call__(self, value: float | None, total: float = 100, message: str | None = None) -> None:
        if self.cancel.is_set():
            raise Cancelled()
        if self.token is None or self.closed:
            return
        now = time.monotonic()
        if value is None:  # a message without new progress: nudge forward by a hair
            value = min(total - 0.01, self.last + 0.01) if self.last >= 0 else 0.0
        value = round(float(value), 2)
        if value <= self.last:
            return
        if now - self.t < self.min_gap and value < total:
            return
        self.last, self.t = value, now
        params: dict[str, Any] = {"progressToken": self.token, "progress": value, "total": total}
        if message:
            params["message"] = message[:300]
        self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})


class Server:
    """Transport-independent request handling. `send` writes one message to the client."""

    def __init__(self, send: Callable[[dict], None]):
        self.send = send
        self.lock = threading.Lock()
        self.running: dict[Any, threading.Event] = {}
        self.threads: list[threading.Thread] = []
        self.shutting_down = threading.Event()

    # ---- helpers
    @staticmethod
    def era(params: dict[str, Any]) -> str | None:
        meta = params.get("_meta") if isinstance(params, dict) else None
        if isinstance(meta, dict) and META_VERSION in meta:
            return str(meta[META_VERSION])
        return None

    @staticmethod
    def shape(result: dict[str, Any], version: str | None) -> dict[str, Any]:
        if version == MODERN_VERSION:
            result["resultType"] = "complete"
            meta = dict(result.get("_meta") or {})
            meta[META_SERVER_INFO] = {"name": SERVER_NAME, "title": SERVER_TITLE, "version": __version__}
            result["_meta"] = meta
        return result

    def check_meta(self, method: str, params: dict[str, Any]) -> str | None:
        version = self.era(params)
        if version is None:
            return None
        if version not in SUPPORTED_VERSIONS:
            raise RpcError(UNSUPPORTED_VERSION, f"unsupported protocol version {version}",
                           {"supported": list(SUPPORTED_VERSIONS), "requested": version})
        if version == MODERN_VERSION and META_CLIENT_CAPS not in params["_meta"]:
            raise RpcError(INVALID_PARAMS, f"_meta is missing {META_CLIENT_CAPS}")
        return version

    # ---- entry points
    def handle(self, msg: Any, respond: Callable[[dict], None] | None = None) -> dict[str, Any] | None:
        """Handle one decoded message. Returns the response (or None for notifications and for
        tools/call, whose response is sent later through `respond`, default `self.send`)."""
        respond = respond or self.send
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return _error(msg.get("id") if isinstance(msg, dict) else None,
                          RpcError(INVALID_REQUEST, "expected a JSON-RPC 2.0 object"))
        method = msg.get("method")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        if "id" not in msg:
            if method == "notifications/cancelled":
                ev = self.running.get(params.get("requestId"))
                if ev is not None:
                    ev.set()
            return None  # notifications/initialized and anything else: nothing to answer
        rid = msg["id"]
        if method is None and ("result" in msg or "error" in msg):
            return None  # a response to a request we never send
        if not isinstance(method, str):
            return _error(rid, RpcError(INVALID_REQUEST, "missing method"))
        try:
            version = self.check_meta(method, params) if method != "initialize" else None
            if method == "initialize":
                return {"jsonrpc": "2.0", "id": rid, "result": self.initialize(params)}
            if method == "server/discover":
                return {"jsonrpc": "2.0", "id": rid, "result": self.shape(self.discover(), MODERN_VERSION)}
            if method == "ping":
                return {"jsonrpc": "2.0", "id": rid, "result": self.shape({}, version)}
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": rid, "result": self.shape({"tools": tool_list()}, version)}
            if method == "tools/call":
                self.start_call(rid, params, version, respond)
                return None
            raise RpcError(METHOD_NOT_FOUND, f"method not found: {method}")
        except RpcError as e:
            return _error(rid, e)
        except Exception as e:  # noqa: BLE001
            log(traceback.format_exc())
            return _error(rid, RpcError(INTERNAL_ERROR, f"{type(e).__name__}: {e}"))

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        asked = params.get("protocolVersion")
        version = asked if asked in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
        return {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "title": SERVER_TITLE, "version": __version__},
                "instructions": INSTRUCTIONS}

    def discover(self) -> dict[str, Any]:
        return {"supportedVersions": list(SUPPORTED_VERSIONS), "capabilities": {"tools": {}},
                "instructions": INSTRUCTIONS}

    def prepare_call(self, rid: Any, params: dict[str, Any]) -> tuple[Tool, Any, Any]:
        name = params.get("name")
        tool = find_tool(name)
        if tool is None:
            raise RpcError(INVALID_PARAMS, f"unknown tool: {name}", {"tools": [t.name for t in TOOLS]})
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        return tool, params.get("arguments"), meta.get("progressToken")

    def run_call(self, rid: Any, tool: Tool, args: Any, token: Any, version: str | None,
                 send_progress: Callable[[dict], None]) -> dict[str, Any] | None:
        """Run a tools/call to completion. Returns the response, or None when it was cancelled."""
        ev = threading.Event()
        self.running[rid] = ev
        prog = Progress(send_progress, token, ev)
        try:
            data, extra, is_error = call_tool(tool, args, prog)
        except Cancelled:
            log(f"{tool.name} (request {rid}) cancelled")
            return None
        finally:
            prog.closed = True
            self.running.pop(rid, None)
        if ev.is_set():
            return None
        return {"jsonrpc": "2.0", "id": rid, "result": self.shape(tool_result(data, extra, is_error), version)}

    def start_call(self, rid: Any, params: dict[str, Any], version: str | None,
                   respond: Callable[[dict], None]) -> None:
        tool, args, token = self.prepare_call(rid, params)

        def work() -> None:
            resp = self.run_call(rid, tool, args, token, version, self.send)
            if resp is not None:
                respond(resp)

        th = threading.Thread(target=work, name=f"tool-{tool.name}", daemon=True)
        self.threads.append(th)
        th.start()

    def drain(self) -> None:
        """stdin closed: let running tools finish and deliver their results, then return. (A client that
        wants to stop a render sends notifications/cancelled or terminates the process; finished render
        chunks are kept either way.)"""
        for th in self.threads:
            th.join()


# --------------------------------------------------------------------------- stdio transport

def _claim_stdio() -> tuple[Any, Any]:
    """Private copies of the protocol streams; fd 0 -> /dev/null and fd 1 -> stderr for everything else
    (the engine prints, ffmpeg inherits stdin). Python's dups are non-inheritable."""
    sys.stdout.flush()
    in_fd, out_fd = os.dup(0), os.dup(1)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    os.dup2(2, 1)
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115
    sys.stdout = sys.stderr
    return os.fdopen(in_fd, "rb"), os.fdopen(out_fd, "wb")


def serve_stdio() -> int:
    reader, writer = _claim_stdio()
    wlock = threading.Lock()

    def send(obj: dict[str, Any]) -> None:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8") + b"\n"
        with wlock:
            try:
                writer.write(data)
                writer.flush()
            except (BrokenPipeError, ValueError, OSError):
                pass

    server = Server(send)
    log(f"{SERVER_NAME} {__version__} on stdio (protocol {', '.join(SUPPORTED_VERSIONS)})")
    while True:
        line = reader.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            send(_error(None, RpcError(PARSE_ERROR, f"parse error: {e}")))
            continue
        if isinstance(msg, list):
            send(_error(None, RpcError(INVALID_REQUEST, "batches are not supported")))
            continue
        resp = server.handle(msg)
        if resp is not None:
            send(resp)
    server.drain()
    return 0


# --------------------------------------------------------------------------- streamable HTTP transport

def make_http_server(host: str = "127.0.0.1", port: int = 0, allow_origins: list[str] | None = None) -> Any:
    """A ThreadingHTTPServer answering MCP at /mcp (call serve_forever on it). Port 0 = any free port."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse

    allow_origins = list(allow_origins or [])
    local_hosts = {"localhost", "127.0.0.1", "::1", "[::1]"}
    server = Server(lambda obj: None)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"{SERVER_NAME}/{__version__}"

        def log_message(self, fmt: str, *a: Any) -> None:
            log("http " + fmt % a)

        def _json(self, status: int, body: Any = None) -> None:
            data = b"" if body is None else json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            if origin in allow_origins or "*" in allow_origins:
                return True
            return (urlparse(origin).hostname or "") in local_hosts

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_DELETE = do_GET  # noqa: N815

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path.rstrip("/") not in ("/mcp", ""):
                return self._json(404, _error(None, RpcError(METHOD_NOT_FOUND, "the endpoint is /mcp")))
            if not self._origin_ok():
                return self._json(403, _error(None, RpcError(INVALID_REQUEST, "origin not allowed")))
            try:
                n = int(self.headers.get("Content-Length") or 0)
                msg = json.loads(self.rfile.read(n) or b"null")
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, _error(None, RpcError(PARSE_ERROR, f"parse error: {e}")))
            if not isinstance(msg, dict):
                return self._json(400, _error(None, RpcError(INVALID_REQUEST, "expected one JSON-RPC object")))
            rid, method = msg.get("id"), msg.get("method")
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            version = Server.era(params)
            header_v = self.headers.get("MCP-Protocol-Version")
            if version is not None:
                bad = None
                if header_v != version:
                    bad = f"MCP-Protocol-Version header {header_v!r} does not match _meta {version!r}"
                elif self.headers.get("Mcp-Method") != method:
                    bad = f"Mcp-Method header {self.headers.get('Mcp-Method')!r} does not match {method!r}"
                elif method == "tools/call" and self.headers.get("Mcp-Name") != params.get("name"):
                    bad = f"Mcp-Name header {self.headers.get('Mcp-Name')!r} does not match {params.get('name')!r}"
                if bad:
                    return self._json(400, _error(rid, RpcError(HEADER_MISMATCH, bad)))
            elif header_v and header_v not in SUPPORTED_VERSIONS:
                return self._json(400, _error(rid, RpcError(UNSUPPORTED_VERSION, f"unsupported version {header_v}",
                                                            {"supported": list(SUPPORTED_VERSIONS),
                                                             "requested": header_v})))
            if "id" not in msg:
                server.handle(msg)
                return self._json(202)
            if method != "tools/call":
                resp = server.handle(msg)
                status = 404 if resp and resp.get("error", {}).get("code") == METHOD_NOT_FOUND else 200
                return self._json(status, resp)
            try:
                v = server.check_meta(method, params)
                tool, args, token = server.prepare_call(rid, params)
            except RpcError as e:
                return self._json(200, _error(rid, e))
            if "text/event-stream" in (self.headers.get("Accept") or "") and token is not None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                wlock = threading.Lock()

                def event(obj: dict[str, Any]) -> None:
                    with wlock:
                        try:
                            self.wfile.write(b"event: message\ndata: " + json.dumps(obj, default=str).encode() + b"\n\n")
                            self.wfile.flush()
                        except OSError:
                            pass

                resp = server.run_call(rid, tool, args, token, v, event)
                if resp is not None:
                    event(resp)
                self.close_connection = True
                return None
            resp = server.run_call(rid, tool, args, None, v, lambda obj: None)
            return self._json(200, resp)

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def serve_http(host: str, port: int, allow_origins: list[str]) -> int:
    httpd = make_http_server(host, port, allow_origins)
    log(f"{SERVER_NAME} {__version__} on http://{host}:{httpd.server_address[1]}/mcp")
    if host not in ("127.0.0.1", "localhost", "::1"):
        log("warning: listening beyond this machine; there is no authentication, keep it on a trusted network")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


# --------------------------------------------------------------------------- command line

def _one_shot(name: str, raw: str | None) -> int:
    tool = find_tool(name)
    if tool is None:
        log(f"unknown tool {name!r}; tools: {', '.join(t.name for t in TOOLS)}")
        return 2
    try:
        args = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        log(f"arguments are not JSON: {e}")
        return 2
    _, writer = _claim_stdio()

    def progress_line(value: float | None, total: float = 100, message: str | None = None) -> None:
        if message:
            log(f"[{'' if value is None else f'{value:5.1f}%'}] {message}")

    data, extra, is_error = call_tool(tool, args, progress_line)
    writer.write((json.dumps(data, indent=1, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
    writer.flush()
    return 1 if is_error else (0 if data.get("ok") else 1)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kstudio-mcp", description="Kinetic Studio MCP server (stdio by default).")
    p.add_argument("--version", action="version", version=f"kstudio-mcp {__version__}")
    p.add_argument("--http", metavar="[HOST:]PORT", help="serve streamable HTTP on HOST:PORT/mcp (default host 127.0.0.1)")
    p.add_argument("--allow-origin", action="append", default=[], metavar="ORIGIN",
                   help="extra browser Origin accepted by the HTTP transport (localhost is always accepted)")
    p.add_argument("--list-tools", action="store_true", help="print the MCP tool list as JSON and exit")
    p.add_argument("--call", nargs="+", metavar=("TOOL", "JSON"), help="run one tool with JSON arguments and exit")
    a = p.parse_args(argv)
    if a.list_tools:
        print(json.dumps({"tools": tool_list()}, indent=1))
        return 0
    if a.call:
        return _one_shot(a.call[0], a.call[1] if len(a.call) > 1 else None)
    if a.http:
        host, _, port = a.http.rpartition(":")
        return serve_http(host.strip("[]") or "127.0.0.1", int(port), a.allow_origin)
    return serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
