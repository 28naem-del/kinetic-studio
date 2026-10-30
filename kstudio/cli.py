"""kinetic-studio (alias kstudio): the Kinetic Studio command line.

  modes | scenes [type]                    what the engine can make
  new <mode> <dir>                         starter project from the mode's example spec
  schema [--mode m] [-o file]              spec JSON Schema (draft 2020-12) generated from the engine
  validate <spec> [--fix-hints] [--strict] schema + dry-build check, errors with JSON Pointer paths
  brief --mode m --text "..."              prompt pack (system + schema + examples + beat rules) for any LLM
  info <spec>                              resolved timeline (scenes, markers, beats) + grid lint
  preview <spec> [--at 1.2,3]              stills + contact sheet (fast, single process)
  render <spec> [-o out.mp4] [--preset reel|landscape|square] [--fps 30|60]
         [--quality draft|standard|high] [--4k] [--workers auto|N] [--fresh] [--snap [beat|8th|16th|bar]]
  audit <video> [--mode m] [--fps N] [--frames N]   motion / loudness / codec / fps / duration checks
  mix <spec> -o out.wav                    soundtrack only
  beats <audio>                            tempo + beat grid of a music file
  sfx <dir>                                export the synthesized SFX kit as WAVs
  doctor                                   check ffmpeg / ffprobe / libx264 / skia and report versions
  exit-codes                               the exit-code table

<spec> is a spec.json / spec.jsonc / spec.js path, a project directory, inline JSON ('{"scenes": ...}')
or '-' for JSON on stdin.

Machine use: every command takes --json (or set KSTUDIO_JSON=1). stdout then carries exactly one JSON
object: {"ok", "command", "version", "exit_code", ...result} or, on failure, the same envelope with
"error": {"code", "message", "path", "hint", "errors": [...]}. Logs and progress (JSON lines) go to
stderr. Exit codes are fixed (kstudio/exitcodes.py, docs/CLI.md): 0 ok, 1 internal, 2 usage,
3 invalid spec, 4 render failure, 5 audit failed, 6 missing tool, 7 backend unreachable (reserved),
130 interrupted.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

from . import __version__
from .exitcodes import (
    AuditFailed,
    CliError,
    Exit,
    MissingTool,
    SpecInvalid,
    UsageError,
    classify,
    exit_table,
)
from .spec import examples_dir

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = examples_dir()
JSON_ENV = "KSTUDIO_JSON"
RESERVED = ("ok", "command", "version", "exit_code", "error")


# --------------------------------------------------------------------------- plumbing

class Parser(argparse.ArgumentParser):
    """argparse with usage errors raised as UsageError (so --json can report them as JSON)."""

    def error(self, message: str) -> Any:
        raise UsageError(f"{self.prog}: {message}", hint=f"run `{self.prog} --help`", usage=self.format_usage().strip())


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def _event(**kw: Any) -> None:
    """One JSON line on stderr (progress / log events in --json mode)."""
    sys.stderr.write(json.dumps(kw, default=str) + "\n")
    sys.stderr.flush()


@contextlib.contextmanager
def _stdout_to_stderr(enabled: bool) -> Iterator[IO[str]]:
    """In --json mode nothing but the final JSON object may reach stdout: Python-level prints and,
    when stdout is the real fd 1, child processes (ffmpeg, render workers) are sent to stderr."""
    real = sys.stdout
    if not enabled:
        yield real
        return
    try:
        fd: int | None = real.fileno()
    except (AttributeError, OSError, ValueError):
        fd = None
    saved = None
    real.flush()
    if fd == 1:
        saved = os.dup(1)
        os.dup2(2, 1)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            yield real
    finally:
        sys.stderr.flush()
        if saved is not None:
            os.dup2(saved, 1)
            os.close(saved)


def envelope(command: str | None, code: int, result: dict[str, Any] | None = None,
             error: CliError | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": code == 0, "command": command, "version": __version__, "exit_code": int(code)}
    res = dict(result or {})
    for k in RESERVED:  # a result that repeats an envelope value (a report's "version") is merged
        if k in res and k in out and res[k] == out[k]:
            del res[k]
    if any(k in res for k in RESERVED):
        out["result"] = res
    else:
        out.update(res)
    if error is not None:
        err = error.as_dict()
        extra = err.pop("result", None)
        if isinstance(extra, dict):
            for k, v in extra.items():
                if k not in RESERVED:
                    out.setdefault(k, v)
        if error.exit == Exit.SPEC_INVALID:
            if not err.get("errors"):
                err["errors"] = [{"path": err.get("path", ""), "message": err["message"],
                                  "hint": err.get("hint", ""), "keyword": "load", "severity": "error"}]
            first = err["errors"][0]
            err.setdefault("path", first.get("path", ""))
            err.setdefault("hint", first.get("hint", ""))
        out["error"] = err
    return out


def _print_error(prog: str, e: CliError) -> None:
    _log(f"{prog}: error: {e.message}")
    for it in e.errors[:25]:
        _log(f"  {it.get('path') or '/'}: {it.get('message')}")
        if it.get("hint"):
            _log(f"    fix: {it['hint']}")
    if len(e.errors) > 25:
        _log(f"  ... {len(e.errors) - 25} more")
    if e.hint and not e.errors:
        _log(f"  hint: {e.hint}")
    if e.code != "INTERNAL_ERROR":
        _log(f"  ({e.code}, exit {int(e.exit)})")


def _load(src: str) -> dict[str, Any]:
    """Spec from a path / project dir / '-' (stdin) / inline JSON text."""
    from .spec import load_spec, parse_spec_text
    if src.lstrip().startswith("{"):
        spec = parse_spec_text(src, "<inline>")
        spec.setdefault("_dir", os.getcwd())
        spec.setdefault("_path", "<inline>")
        return spec
    return load_spec(src)


def _need_ffmpeg() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        raise MissingTool(f"{' and '.join(missing)} not found on PATH",
                          hint="install ffmpeg (macOS: brew install ffmpeg; Debian/Ubuntu: apt install ffmpeg; "
                               "Windows: winget install ffmpeg)", missing=missing)


def _check_spec(a: argparse.Namespace, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate before an expensive command; raise SpecInvalid on blocking errors, return warnings."""
    from .schema import blocking, validate_spec
    r = validate_spec(spec)
    block = blocking(r["errors"])
    if block:
        first = block[0]
        raise SpecInvalid(f"{len(block)} spec error(s); first at {first['path'] or '/'}: {first['message']}",
                          errors=block)
    warns = [e for e in r["errors"] if e not in block] + r["warnings"]
    if warns and not a.json:
        for w in warns[:10]:
            _log(f"  spec warning {w['path'] or '/'}: {w['message']}")
    return warns


# --------------------------------------------------------------------------- commands

def cmd_modes(a: argparse.Namespace) -> dict[str, Any]:
    from .modes import MODES
    from .spec import example_spec
    modes = []
    for k, m in MODES.items():
        ex = example_spec(k)
        modes.append({"name": k, "description": m["description"], "theme": m["theme"],
                      "music": dict(m["music"]), "transition": m["transition"], "example": str(ex) if ex else None})
        if not a.json:
            print(f"{k:<10} {m['description']}\n{'':<10} theme={m['theme']} music={m['music']['style']}@"
                  f"{m['music']['bpm']} transition={m['transition']}")
    return {"modes": modes}


def cmd_scenes(a: argparse.Namespace) -> dict[str, Any]:
    from .schema import scene_catalog
    cat = scene_catalog()
    if a.type:
        cat = [c for c in cat if c["type"] == a.type]
        if not cat:
            from .scenes import REGISTRY
            raise UsageError(f"unknown scene type {a.type!r}", code="USAGE", hint=f"choose from {sorted(REGISTRY)}")
    if not a.json:
        for c in cat:
            print(f"{c['type']:<12} {c['description'].split('. ')[0][:100]}")
            if a.type or a.verbose:
                for k, f in c["fields"].items():
                    if "default" in f:
                        print(f"{'':<14}{k} = {json.dumps(f['default'])}")
    return {"count": len(cat), "scenes": cat}


def cmd_new(a: argparse.Namespace) -> dict[str, Any]:
    ex = examples_dir()
    src = ex / a.mode
    available = sorted(p.name for p in ex.iterdir() if p.is_dir()) if ex.is_dir() else []
    if not src.is_dir():
        raise UsageError(f"no example for mode {a.mode!r}", code="MODE_UNKNOWN", hint=f"available: {available}")
    dst = Path(a.dir)
    if dst.exists() and any(dst.iterdir()) and not a.force:
        raise UsageError(f"{dst} is not empty", code="DIR_NOT_EMPTY", hint="use --force or pick an empty directory")
    dst.mkdir(parents=True, exist_ok=True)
    files = []
    for f in sorted(src.iterdir()):
        if f.name in ("out", ".kstudio_work", "__pycache__", "__init__.py"):
            continue
        if f.is_file():
            shutil.copy2(f, dst / f.name)
            files.append(str((dst / f.name).resolve()))
        elif f.is_dir():
            shutil.copytree(f, dst / f.name, dirs_exist_ok=True)
            files.append(str((dst / f.name).resolve()) + "/")
    spec_path = next((str((dst / n).resolve()) for n in ("spec.json", "spec.jsonc", "spec.mjs", "spec.js")
                      if (dst / n).exists()), None)
    if not a.json:
        print(f"created {dst}/spec.json ({a.mode}). Next: kinetic-studio preview {dst}  then  kinetic-studio render {dst}")
    return {"mode": a.mode, "project_dir": str(dst.resolve()), "spec_path": spec_path, "files": files,
            "next": [f"kinetic-studio validate {dst} --json", f"kinetic-studio preview {dst} --json",
                     f"kinetic-studio render {dst} --json"]}


def cmd_schema(a: argparse.Namespace) -> dict[str, Any]:
    from .brief import mode_scene_types
    from .modes import MODES
    from .schema import spec_schema
    types: list[str] | None = None
    if a.mode:
        if a.mode not in MODES:
            raise UsageError(f"unknown mode {a.mode!r}", code="MODE_UNKNOWN", hint=f"choose from {sorted(MODES)}")
        types = mode_scene_types(a.mode)
    if a.types:
        types = sorted(set(types or []) | {t.strip() for t in a.types.split(",") if t.strip()})
    schema = spec_schema(types)
    text = json.dumps(schema, indent=None if a.compact else 1)
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).write_text(text + "\n", encoding="utf-8")
        if not a.json:
            print(f"wrote {a.output} ({len(schema['x-scene-types'])} scene types)")
    elif not a.json:
        print(text)
    return {"schema": schema, "scene_types": schema["x-scene-types"], "scene_type_count": len(schema["x-scene-types"]),
            **({"path": str(Path(a.output).resolve())} if a.output else {})}


def cmd_validate(a: argparse.Namespace) -> dict[str, Any]:
    from .schema import fix_prompt, validate_spec
    a._phase = "spec"
    try:
        spec = _load(a.spec)
    except Exception as e:  # noqa: BLE001 - load failures are reported in the validation shape
        err = classify(e, "spec")
        if a.fix_hints and isinstance(err, SpecInvalid):
            err.details["fix_prompt"] = (f"The spec could not be read: {err.message}. Return the spec as one valid "
                                         "JSON object (no prose, no markdown fences).")
        raise err from None
    r = validate_spec(spec, strict=a.strict, build=not a.no_build)
    res: dict[str, Any] = {"valid": r["valid"], "spec_path": spec.get("_path"), "errors": r["errors"],
                           "warnings": r["warnings"], "summary": r["summary"]}
    if a.fix_hints:
        res["fix_prompt"] = fix_prompt(r, spec)
    if not a.json:
        if r["valid"]:
            s = r["summary"] or {}
            print(f"valid: {spec.get('_path')}" + (f"  ({s.get('scenes')} scenes, {s.get('duration')} s, "
                                                    f"{s.get('beats')} beats @ {s.get('bpm')} BPM)" if s else ""))
        for w in r["warnings"]:
            print(f"  warning {w['path'] or '/'}: {w['message']}")
        if a.fix_hints and (r["errors"] or r["warnings"]):
            print(res["fix_prompt"])
    if not r["valid"]:
        first = r["errors"][0]
        raise SpecInvalid(f"{len(r['errors'])} spec error(s); first at {first['path'] or '/'}: {first['message']}",
                          errors=r["errors"], result=res)
    return res


def cmd_brief(a: argparse.Namespace) -> dict[str, Any]:
    from .brief import prompt_pack, prompt_text
    text = a.text if a.text is not None else (" ".join(a.words) if a.words else None)
    if text == "-":
        text = sys.stdin.read()
    if not text or not text.strip():
        raise UsageError("brief needs --text '...' (or '-' to read it from stdin)", hint="kinetic-studio brief "
                         "--mode promo --text 'launch teaser for ...'")
    try:
        pack = prompt_pack(text, a.mode, a.duration, a.preset, a.all_scenes)
    except KeyError as e:
        raise UsageError(str(e.args[0] if e.args else e), code="MODE_UNKNOWN") from None
    prompt = prompt_text(pack)
    if a.output:
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        Path(a.output).write_text(prompt, encoding="utf-8")
    if not a.json:
        print(prompt if not a.output else f"wrote {a.output} ({len(prompt)} chars)")
    return {**pack, "prompt": prompt, **({"path": str(Path(a.output).resolve())} if a.output else {})}


def cmd_info(a: argparse.Namespace) -> dict[str, Any]:
    from .film import Film
    from .pipeline import beat_grid_for
    a._phase = "spec"
    spec = _load(a.spec)
    film = Film(spec, 1080, 1920, beat_grid=beat_grid_for(spec))
    s = film.summary()
    lint = film.lint()
    if not a.json:
        print(f"mode {s['mode']}  duration {s['duration']}s  bpm {film.tl.bpm:.2f}")
        for sc in s["scenes"]:
            print(f"  {sc['start']:7.2f}s  +{sc['dur']:<6.2f} {sc['type']:<12} {sc['id']}")
        for b in s["transitions"]:
            print(f"  {b['t']:7.2f}s  transition {b['kind']} ({b['dur']}s)")
        kinds: dict[str, int] = {}
        for m in film.tl.markers:
            kinds[m.kind] = kinds.get(m.kind, 0) + 1
        print("  markers:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
        warns = [w for w in lint if w["level"] == "warn"]
        print(f"  lint: {len(warns)} warning(s), {len(lint) - len(warns)} info")
        for w in lint if a.verbose else warns:
            print(f"    {w['level']:<4} {w['where']}: {w['msg']}")
    return {**s, "bpm": round(film.tl.bpm, 3), "lint": lint}


def cmd_preview(a: argparse.Namespace) -> dict[str, Any]:
    from .pipeline import beat_grid_for
    from .preview import preview
    a._phase = "spec"
    spec = _load(a.spec)
    warns = _check_spec(a, spec)
    out = a.out or os.path.join(spec["_dir"], "out", "preview")
    try:
        times = [float(x) for x in a.at.split(",")] if a.at else None
    except ValueError:
        raise UsageError(f"--at takes comma-separated seconds, got {a.at!r}") from None
    t0 = time.time()
    a._phase = "render"
    r = preview(spec, out, a.preset, times, a.scale, a.quality, beat_grid_for(spec))
    secs = round(time.time() - t0, 2)
    if not a.json:
        print(f"{len(r['stills'])} stills in {secs:.1f}s -> {r['contact_sheet']}")
    return {"stills": [os.path.abspath(p) for p in r["stills"]], "contact_sheet": os.path.abspath(r["contact_sheet"]),
            "out_dir": os.path.abspath(out), "seconds": secs, "summary": r.get("summary"), "warnings": warns}


def _progress_bar(info: dict) -> None:
    total = max(1, info["total"])
    k = int(30 * info["frames"] / total)
    eta = f"eta {info['eta']:.0f}s" if info.get("eta") is not None else ""
    sys.stderr.write(f"\r  [{'#' * k}{'.' * (30 - k)}] {info['frames']}/{total} frames  {info['fps']:.1f} fps  {eta}   ")
    sys.stderr.flush()


def _progress_jsonl() -> Any:
    last = [0.0]

    def on(info: dict) -> None:
        now = time.time()
        if now - last[0] >= 0.5 or info.get("frames") == info.get("total"):
            last[0] = now
            _event(event="progress", frames=info.get("frames"), total=info.get("total"),
                   fps=round(float(info.get("fps") or 0), 2),
                   eta=None if info.get("eta") is None else round(float(info["eta"]), 1))
    return on


def _failed_checks(r: dict[str, Any]) -> list[str]:
    checks = (r.get("audit") or {}).get("checks") or r.get("checks") or {}
    return sorted(k for k, c in checks.items() if isinstance(c, dict) and not c.get("ok", True))


def cmd_render(a: argparse.Namespace) -> dict[str, Any]:
    from .audit import format_report
    from .pipeline import build
    a._phase = "spec"
    spec = _load(a.spec)
    warns = _check_spec(a, spec)
    _need_ffmpeg()
    out = a.output or os.path.join(spec["_dir"], "out", f"{Path(spec['_dir']).name}_{a.preset}.mp4")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    try:
        workers: str | int = a.workers if a.workers == "auto" else int(a.workers)
    except ValueError:
        raise UsageError(f"--workers takes 'auto' or a number, got {a.workers!r}") from None
    if a.json:
        on_progress = None if a.quiet else _progress_jsonl()
        say = (lambda s: None) if a.quiet else (lambda s: _event(event="log", message=str(s)))
    else:
        on_progress = None if a.quiet else _progress_bar
        say = (lambda s: None) if a.quiet else print
    a._phase = "render"
    r = build(spec, out, a.preset, a.fps, a.quality, a.k4, workers, a.fresh, not a.no_audit,
              on_progress=on_progress, say=say, snap=a.snap)
    if not a.quiet and not a.json:
        sys.stderr.write("\n")
    report = os.path.splitext(os.path.abspath(r.get("output", out)))[0] + ".report.json"
    res = {**r, "output": os.path.abspath(r.get("output", out)), "warnings": warns,
           **({"report_path": report} if os.path.exists(report) else {})}
    if not a.json:
        rv = r["render"]
        print(f"done: {r['output']}  ({r['duration']}s, video {r['video_seconds']}s with {rv['workers']} workers, "
              f"{rv['resumed_chunks']} chunks resumed; audio {r['audio_seconds']}s; total {r['total_seconds']}s)")
        print(f"audio: {r['audio']['final']['lufs']} LUFS, {r['audio']['final']['true_peak']} dBTP")
        if "audit" in r:
            print(format_report(r["audit"]))
    if not r.get("pass", True):
        failed = _failed_checks(r)
        raise AuditFailed(f"video written but the audit failed: {', '.join(failed) or 'see report'}",
                          failed_checks=failed, result=res)
    return res


def cmd_audit(a: argparse.Namespace) -> dict[str, Any]:
    from .audit import audit, format_report
    from .modes import mode_defaults
    if not os.path.exists(a.video):
        raise UsageError(f"video not found: {a.video}", code="FILE_NOT_FOUND")
    _need_ffmpeg()
    try:
        th = dict(mode_defaults(a.mode).get("audit", {})) if a.mode else {}
    except KeyError as e:
        raise UsageError(str(e.args[0]), code="MODE_UNKNOWN") from None
    if a.min_motion is not None:
        th["min_motion"] = a.min_motion
    if a.max_hold is not None:
        th["max_hold"] = a.max_hold
    expect: dict = {}
    if a.fps is not None:
        expect["fps"] = a.fps
    if a.frames is not None:
        expect["frames"] = a.frames
    a._phase = "render"
    r = audit(a.video, expect, th)
    if not a.json:
        print(format_report(r))
    if not r["pass"]:
        failed = _failed_checks(r)
        raise AuditFailed(f"audit failed: {', '.join(failed)}", failed_checks=failed, result=r)
    return r


def cmd_mix(a: argparse.Namespace) -> dict[str, Any]:
    from .audio.mix import mix_film
    from .film import Film
    from .pipeline import beat_grid_for
    a._phase = "spec"
    spec = _load(a.spec)
    _need_ffmpeg()
    film = Film(spec, 1080, 1920, beat_grid=beat_grid_for(spec))
    a._phase = "render"
    r = mix_film(film, a.output)
    if not a.json:
        print(f"{a.output}: {r['final']['lufs']} LUFS, {r['final']['true_peak']} dBTP, {r['sfx_count']} sfx, "
              f"music {r['music']}")
    return {**r, "output": os.path.abspath(a.output)}


def cmd_beats(a: argparse.Namespace) -> dict[str, Any]:
    from .beats import analyse_file
    if not os.path.exists(a.audio):
        raise UsageError(f"audio file not found: {a.audio}", code="FILE_NOT_FOUND")
    _need_ffmpeg()
    a._phase = "render"
    g = analyse_file(a.audio)
    res = {**g.as_dict(), "first_beats": [round(x, 3) for x in g.beat_times[:8]]}
    if not a.json:
        print(json.dumps(res, indent=1))
    return res


def cmd_sfx(a: argparse.Namespace) -> dict[str, Any]:
    from .audio.synth import export_kit
    a._phase = "render"
    paths = export_kit(a.dir)
    if not a.json:
        print(f"wrote {len(paths)} sounds to {a.dir}")
    return {"dir": os.path.abspath(a.dir), "count": len(paths), "files": [os.path.abspath(p) for p in paths]}


def _tool_version(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (r.stdout or r.stderr).strip().splitlines()
    return line[0] if line else None


def cmd_doctor(a: argparse.Namespace) -> dict[str, Any]:
    import importlib.util

    from .fonts import FONT_DIR
    checks: dict[str, dict[str, Any]] = {}
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        checks[tool] = {"ok": bool(path), "required": True, "path": path,
                        "version": _tool_version([tool, "-version"]) if path else None}
    x264 = False
    if checks["ffmpeg"]["ok"]:
        try:
            enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10)
            x264 = "libx264" in enc.stdout
        except (OSError, subprocess.TimeoutExpired):
            x264 = False
    checks["libx264"] = {"ok": x264, "required": True}
    for mod in ("skia", "numpy", "cv2"):
        try:
            m = __import__(mod)
            checks[mod] = {"ok": True, "required": True, "version": str(getattr(m, "__version__", "") or "")}
        except Exception as e:  # noqa: BLE001 - report any import failure (missing EGL libs, ...)
            checks[mod] = {"ok": False, "required": True, "error": f"{type(e).__name__}: {e}"}
    node = shutil.which("node")
    checks["node"] = {"ok": bool(node), "required": False, "path": node, "note": "only for spec.js / spec.mjs"}
    checks["mcp_server"] = {"ok": importlib.util.find_spec("kstudio.mcp_server") is not None, "required": False,
                            "note": "kstudio-mcp (stdio / HTTP MCP server, standard library only)"}
    ex = examples_dir()
    checks["examples"] = {"ok": ex.is_dir(), "required": True, "path": str(ex)}
    checks["fonts"] = {"ok": os.path.isdir(FONT_DIR) and bool(os.listdir(FONT_DIR)), "required": True,
                       "path": FONT_DIR}
    missing = [k for k, c in checks.items() if c["required"] and not c["ok"]]
    res = {"python": platform.python_version(), "platform": platform.platform(), "checks": checks,
           "missing": missing}
    if not a.json:
        for k, c in checks.items():
            mark = "ok " if c["ok"] else ("MISSING" if c["required"] else "-- ")
            detail = c.get("version") or c.get("path") or c.get("note") or c.get("error") or ""
            print(f"  {mark:<7} {k:<9} {detail}")
    if missing:
        raise MissingTool(f"missing: {', '.join(missing)}", hint="install ffmpeg with libx264 (brew install ffmpeg "
                          "/ apt install ffmpeg) and reinstall kinetic-studio for Python packages", result=res)
    return res


def cmd_exit_codes(a: argparse.Namespace) -> dict[str, Any]:
    table = exit_table()
    if not a.json:
        for row in table:
            print(f"  {row['exit_code']:>3}  {row['name']:<20} {row['meaning']}")
    return {"exit_codes": table}


# --------------------------------------------------------------------------- parser

def build_parser(prog: str = "kinetic-studio") -> Parser:
    common = Parser(add_help=False)
    common.add_argument("--json", action="store_true", help="write exactly one JSON object to stdout")
    p = Parser(prog=prog, description="Kinetic Studio: brief -> beat-synced motion-graphics video, fully local.",
               epilog="exit codes: " + ", ".join(f"{r['exit_code']} {r['name'].lower()}" for r in exit_table())
               + ". With --json (or KSTUDIO_JSON=1) stdout holds one JSON object; logs go to stderr.")
    p.add_argument("--version", action="version", version=f"kinetic-studio {__version__}")
    p.add_argument("--json", dest="json_main", action="store_true", help="JSON output for any command")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def add(name: str, fn: Any, help_: str) -> Parser:
        s = sub.add_parser(name, help=help_, parents=[common], description=help_)
        s.set_defaults(fn=fn)
        return s

    add("modes", cmd_modes, "list modes")
    s = add("scenes", cmd_scenes, "list scene types and their parameters")
    s.add_argument("type", nargs="?")
    s.add_argument("-v", "--verbose", action="store_true")
    s = add("new", cmd_new, "start a project from a mode example")
    s.add_argument("mode")
    s.add_argument("dir")
    s.add_argument("--force", action="store_true")
    s = add("schema", cmd_schema, "print the spec JSON Schema (draft 2020-12) generated from the engine")
    s.add_argument("--mode", help="only the scene types designed for this mode (plus common scenes)")
    s.add_argument("--types", help="comma-separated scene types to include")
    s.add_argument("-o", "--output", help="write the schema to a file")
    s.add_argument("--compact", action="store_true", help="no indentation")
    s = add("validate", cmd_validate, "validate a spec: schema + dry build; errors carry JSON Pointer paths")
    s.add_argument("spec", help="spec path, project dir, inline JSON or '-' for stdin")
    s.add_argument("--fix-hints", action="store_true", help="add a repair prompt an LLM can act on")
    s.add_argument("--strict", action="store_true", help="unknown keys are errors, not warnings")
    s.add_argument("--no-build", action="store_true", help="schema check only (skip the dry build)")
    s = add("brief", cmd_brief, "prompt pack (system + schema + examples + beat rules) to turn a brief into a spec")
    s.add_argument("words", nargs="*", help="the brief (or use --text)")
    s.add_argument("--text", help="the brief; '-' reads it from stdin")
    s.add_argument("--mode", default="kinetic")
    s.add_argument("--duration", type=float, help="target length in seconds (rounded to whole bars)")
    s.add_argument("--preset", default="reel")
    s.add_argument("--all-scenes", action="store_true", help="embed every scene type, not just the mode's")
    s.add_argument("-o", "--output", help="also write the plain-text prompt to a file")
    s = add("info", cmd_info, "print the resolved timeline and beat-grid lint")
    s.add_argument("spec")
    s.add_argument("-v", "--verbose", action="store_true", help="also list info-level lint items")
    s = add("preview", cmd_preview, "render stills + a contact sheet")
    s.add_argument("spec")
    s.add_argument("--out")
    s.add_argument("--at", help="comma-separated times in seconds")
    s.add_argument("--preset", default="reel")
    s.add_argument("--scale", type=float, default=0.5)
    s.add_argument("--quality", default="draft", choices=["draft", "standard", "high"])
    s = add("render", cmd_render, "render the final MP4 (parallel, resumable)")
    s.add_argument("spec")
    s.add_argument("-o", "--output")
    s.add_argument("--preset", default="reel")
    s.add_argument("--fps", type=int, default=30)
    s.add_argument("--quality", default="standard", choices=["draft", "standard", "high"])
    s.add_argument("--4k", dest="k4", action="store_true", help="double the preset resolution")
    s.add_argument("--workers", default="auto")
    s.add_argument("--fresh", action="store_true", help="ignore finished chunks")
    s.add_argument("--snap", nargs="?", const="beat", choices=["beat", "8th", "16th", "bar"],
                   help="snap seconds-valued scene lengths/starts to the beat grid (bare --snap = beat); "
                        "cue sfx snap to the given division")
    s.add_argument("--no-audit", action="store_true", help="skip the audit (mux fps/true-peak checks still run)")
    s.add_argument("-q", "--quiet", action="store_true")
    s = add("audit", cmd_audit, "audit a finished video")
    s.add_argument("video")
    s.add_argument("--mode")
    s.add_argument("--min-motion", type=float)
    s.add_argument("--max-hold", type=float)
    s.add_argument("--fps", type=float, help="expected frame rate (r = avg = n/d must equal it)")
    s.add_argument("--frames", type=int, help="expected video frame count (+-1)")
    s = add("mix", cmd_mix, "render the soundtrack only")
    s.add_argument("spec")
    s.add_argument("-o", "--output", default="mix.wav")
    s = add("beats", cmd_beats, "analyse a music file's tempo and beats")
    s.add_argument("audio")
    s = add("sfx", cmd_sfx, "export the SFX kit")
    s.add_argument("dir")
    add("doctor", cmd_doctor, "check ffmpeg / ffprobe / libx264 / skia and report versions")
    add("exit-codes", cmd_exit_codes, "print the exit-code table")
    return p


def _prog() -> str:
    name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    return name if name in ("kinetic-studio", "kstudio") else "kinetic-studio"


def _guess_command(argv: list[str]) -> str | None:
    return next((x for x in argv if not x.startswith("-")), None)


def main(argv: list[str] | None = None) -> int:
    """Entry point of the `kinetic-studio` / `kstudio` console scripts. Returns the exit code; usage
    errors raise SystemExit(2) like argparse (after printing the JSON envelope in --json mode)."""
    args = list(sys.argv[1:] if argv is None else argv)
    want_json = "--json" in args or _truthy(os.environ.get(JSON_ENV))
    prog = _prog()
    p = build_parser(prog)
    try:
        a = p.parse_args(args)
    except UsageError as e:
        if want_json:
            sys.stdout.write(json.dumps(envelope(_guess_command(args), int(e.exit), None, e)) + "\n")
            sys.stdout.flush()
        else:
            _log(e.details.get("usage", ""))
            _log(f"{e.message}")
        raise SystemExit(int(e.exit)) from None
    a.json = bool(getattr(a, "json", False) or a.json_main or _truthy(os.environ.get(JSON_ENV)))
    a._phase = "run"
    code: int = Exit.OK
    result: dict[str, Any] | None = None
    error: CliError | None = None
    with _stdout_to_stderr(a.json) as real_stdout:
        try:
            result = a.fn(a)
        except SystemExit as e:
            if isinstance(e.code, int) or e.code is None:
                raise
            error = classify(e, a._phase)
        except BaseException as e:  # noqa: BLE001 - mapped to the exit-code table
            if os.environ.get("KSTUDIO_DEBUG") and not isinstance(e, (CliError, KeyboardInterrupt)):
                raise
            error = classify(e, a._phase)
        if error is not None:
            code = int(error.exit)
            if not a.json:
                _print_error(prog, error)
    if a.json:
        real_stdout.write(json.dumps(envelope(a.cmd, code, result, error), default=str) + "\n")
        real_stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
