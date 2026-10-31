"""Exit codes and machine-readable error codes of the command line: the one table every command uses.

The numbers are a stable contract (documented in docs/CLI.md and tested): scripts and agent
frameworks branch on them without parsing text.

    0    ok
    1    internal error (a bug; re-run with KSTUDIO_DEBUG=1 for the traceback)
    2    usage error (bad flags or arguments, unknown mode, target directory not empty)
    3    invalid spec (parse error, schema violation, unresolvable time, unknown scene type)
    4    render failure (the spec was valid but rendering, encoding or muxing failed)
    5    audit failed (a video was written but the quality audit did not pass)
    6    missing tool (ffmpeg / ffprobe / libx264 / node not installed)
    7    swap backend unavailable, unconfigured, or not a ComfyUI API
    130  interrupted (Ctrl-C); re-run the same command to resume from finished chunks
"""
from __future__ import annotations

from enum import IntEnum
from typing import Any


class Exit(IntEnum):
    OK = 0
    INTERNAL = 1
    USAGE = 2
    SPEC_INVALID = 3
    RENDER_FAILED = 4
    AUDIT_FAILED = 5
    MISSING_TOOL = 6
    BACKEND_UNREACHABLE = 7
    INTERRUPTED = 130


# exit code -> (meaning, error codes that map to it). Printed by `kinetic-studio exit-codes`.
EXIT_TABLE: dict[Exit, tuple[str, tuple[str, ...]]] = {
    Exit.OK: ("success", ()),
    Exit.INTERNAL: ("internal error (a bug); re-run with KSTUDIO_DEBUG=1 for the traceback", ("INTERNAL_ERROR",)),
    Exit.USAGE: ("usage error: bad flags or arguments, unknown mode, target directory not empty",
                 ("USAGE", "MODE_UNKNOWN", "DIR_NOT_EMPTY", "FILE_NOT_FOUND")),
    Exit.SPEC_INVALID: ("invalid spec: parse error, schema violation, unresolvable time, unknown scene type",
                        ("SPEC_INVALID", "SPEC_NOT_FOUND", "SPEC_PARSE")),
    Exit.RENDER_FAILED: ("render failure: the spec was valid but rendering, encoding or muxing failed",
                         ("RENDER_FAILED",)),
    Exit.AUDIT_FAILED: ("audit failed: the video was written but the quality audit did not pass", ("AUDIT_FAILED",)),
    Exit.MISSING_TOOL: ("missing tool: ffmpeg, ffprobe, libx264 or node is not installed", ("MISSING_TOOL",)),
    Exit.BACKEND_UNREACHABLE: ("swap backend unavailable, unconfigured, or not a ComfyUI API",
                               ("BACKEND_UNREACHABLE",)),
    Exit.INTERRUPTED: ("interrupted; re-run the same command to resume from finished chunks", ("INTERRUPTED",)),
}


def exit_table() -> list[dict[str, Any]]:
    return [{"exit_code": int(k), "name": k.name, "meaning": m, "error_codes": list(codes)}
            for k, (m, codes) in EXIT_TABLE.items()]


class CliError(Exception):
    """An error the command line reports as a JSON envelope (with --json) and maps to an exit code."""
    exit: Exit = Exit.INTERNAL
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, *, code: str | None = None, hint: str | None = None,
                 errors: list[dict[str, Any]] | None = None, **details: Any):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.hint = hint
        self.errors = errors or []
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        if self.errors:
            d["errors"] = self.errors
        d.update(self.details)
        return d


class UsageError(CliError):
    exit = Exit.USAGE
    code = "USAGE"


class SpecInvalid(CliError):
    exit = Exit.SPEC_INVALID
    code = "SPEC_INVALID"


class RenderFailed(CliError):
    exit = Exit.RENDER_FAILED
    code = "RENDER_FAILED"


class AuditFailed(CliError):
    exit = Exit.AUDIT_FAILED
    code = "AUDIT_FAILED"


class MissingTool(CliError):
    exit = Exit.MISSING_TOOL
    code = "MISSING_TOOL"


class BackendUnreachable(CliError):
    """The optional swap backend is unconfigured, unreachable, or not a ComfyUI API."""
    exit = Exit.BACKEND_UNREACHABLE
    code = "BACKEND_UNREACHABLE"


class Interrupted(CliError):
    exit = Exit.INTERRUPTED
    code = "INTERRUPTED"


def classify(exc: BaseException, phase: str = "run") -> CliError:
    """Map any exception to a CliError. `phase` is "spec" while a spec is loaded / checked (value and
    key errors are spec problems there) and "render" while frames, audio or the mux are produced."""
    from .spec import DependencyMissing, SpecError, SpecNotFound, SpecParseError
    from .timeline import TimeExprError

    if isinstance(exc, CliError):
        return exc
    if isinstance(exc, KeyboardInterrupt):
        return Interrupted("interrupted; re-run the same command to resume from finished chunks")
    if isinstance(exc, DependencyMissing):
        return MissingTool(str(exc), hint="install the missing program or use a spec.json instead of a JS spec")
    if isinstance(exc, SpecNotFound):
        return SpecInvalid(str(exc), code="SPEC_NOT_FOUND",
                           hint="pass a spec.json path, a project directory, inline JSON or '-' for stdin")
    if isinstance(exc, SpecParseError):
        return SpecInvalid(str(exc), code="SPEC_PARSE", hint="the spec must be one JSON object (comments allowed)")
    if isinstance(exc, (SpecError, TimeExprError)):
        return SpecInvalid(str(exc))
    if isinstance(exc, SystemExit) and isinstance(exc.code, str) and "not found on PATH" in exc.code:
        return MissingTool(exc.code.replace("kstudio: ", ""))
    if isinstance(exc, FileNotFoundError) and exc.filename and str(exc.filename).split("/")[-1] in (
            "ffmpeg", "ffprobe", "node"):
        return MissingTool(f"`{exc.filename}` not found on PATH")
    if phase == "spec" and isinstance(exc, (KeyError, ValueError, TypeError, IndexError)):
        msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        return SpecInvalid(str(msg))
    if phase == "render":
        return RenderFailed(f"{type(exc).__name__}: {exc}")
    return CliError(f"{type(exc).__name__}: {exc}")
