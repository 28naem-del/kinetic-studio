"""Spec loading: JSON (with // and /* */ comments, trailing commas) or a JS/MJS module
whose default export is the spec object (evaluated with the system `node`)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


class SpecError(ValueError):
    """A spec that cannot be used (parse error, missing file, invalid content)."""
    code = "SPEC_INVALID"


class SpecNotFound(SpecError):
    code = "SPEC_NOT_FOUND"


class SpecParseError(SpecError):
    code = "SPEC_PARSE"


class DependencyMissing(SpecError):
    """An external program the spec needs (node for JS specs) is not installed."""
    code = "DEPENDENCY_MISSING"


SPEC_NAMES = ("spec.json", "spec.jsonc", "spec.mjs", "spec.js")


def examples_dir() -> Path:
    """The bundled example projects: `kstudio/examples` inside an installed wheel, `<repo>/examples`
    in a source checkout."""
    try:
        from importlib.resources import files
        packaged = Path(str(files("kstudio") / "examples"))
        if packaged.is_dir():
            return packaged
    except (ModuleNotFoundError, TypeError):  # pragma: no cover - zipped or unusual installs
        pass
    return Path(__file__).resolve().parent.parent / "examples"


def example_spec(mode: str) -> Path | None:
    """Path of the example spec for `mode`, or None when there is no example for it."""
    d = examples_dir() / mode
    for name in SPEC_NAMES:
        if (d / name).is_file():
            return d / name
    return None


def parse_spec_text(text: str, where: str = "<spec>") -> dict[str, Any]:
    """JSON / JSONC text -> spec dict (comments and trailing commas allowed)."""
    try:
        spec = json.loads(strip_json_comments(text))
    except json.JSONDecodeError as e:
        raise SpecParseError(f"{where}: {e}") from None
    if not isinstance(spec, dict):
        raise SpecParseError(f"{where}: spec must be a JSON object, got {type(spec).__name__}")
    return spec


def strip_json_comments(src: str) -> str:
    out, i, n = [], 0, len(src)
    in_str = False
    while i < n:
        ch = src[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1]); i += 2; continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True; out.append(ch); i += 1; continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(ch); i += 1
    text = "".join(out)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def load_spec(path: str) -> dict[str, Any]:
    """Load a spec from a file, a project directory (spec.json / spec.jsonc / spec.mjs / spec.js)
    or "-" (JSON on stdin; relative asset paths then resolve against the working directory)."""
    if path == "-":
        spec = parse_spec_text(sys.stdin.read(), "<stdin>")
        spec.setdefault("_dir", os.getcwd())
        spec.setdefault("_path", "-")
        return spec
    path = os.path.abspath(path)
    if os.path.isdir(path):
        for name in SPEC_NAMES:
            if os.path.exists(os.path.join(path, name)):
                path = os.path.join(path, name)
                break
        else:
            raise SpecNotFound(f"no spec.json / spec.js in {path}")
    elif not os.path.exists(path):
        raise SpecNotFound(f"spec not found: {path}")
    if path.endswith((".js", ".mjs")):
        node = shutil.which("node")
        if not node:
            raise DependencyMissing("JS specs need `node` on PATH (or use spec.json)")
        code = ("import(process.argv[1]).then(m=>{let s=m.default??m.spec;"
                "if(typeof s==='function')s=s();return Promise.resolve(s)})"
                ".then(s=>process.stdout.write(JSON.stringify(s)))")
        url = "file://" + path
        r = subprocess.run([node, "--input-type=module", "-e", code, url], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise SpecParseError(f"node failed to evaluate {path}:\n{r.stderr}")
        spec = json.loads(r.stdout)
        if not isinstance(spec, dict):
            raise SpecParseError("spec must be an object")
    else:
        with open(path, encoding="utf-8") as fh:
            spec = parse_spec_text(fh.read(), path)
    spec.setdefault("_dir", os.path.dirname(path))
    spec.setdefault("_path", path)
    return spec


def spec_hash(spec: dict[str, Any], extra: Any = None) -> str:
    clean = {k: v for k, v in spec.items() if not k.startswith("_")}
    blob = json.dumps([clean, extra], sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


# --------------------------------------------------------------------------- footage sources
# A `clip` scene plays footage from `src` (a local file) or from a `source` object. The only source
# kind today is `{"swap": {...}}`: footage made by an optional, remote ComfyUI backend (kstudio.swap).
# Specs without a `source` are untouched, so this is fully backwards compatible.

SWAP_MODES = ("simple", "object")
SWAP_KEYS = ("video", "character", "mode", "prompt", "target", "seed")


def swap_source(source: Any, base_dir: str = ".") -> dict[str, Any] | None:
    """Normalise a clip scene's `source` value. Returns None when it is not a swap source, else
    {"video", "character" (absolute paths), "mode", "prompt", "target", "seed"}; raises SpecError on bad input."""
    if source is None or source == {}:
        return None
    if not isinstance(source, dict) or set(source) - {"swap"}:
        raise SpecError('clip "source" must be {"swap": {...}}')
    sw = source.get("swap")
    if not isinstance(sw, dict):
        raise SpecError('clip "source.swap" must be an object with "video" and "character"')
    unknown = sorted(set(sw) - set(SWAP_KEYS))
    if unknown:
        raise SpecError(f'clip "source.swap" has unknown keys {unknown}; allowed: {list(SWAP_KEYS)}')
    for k in ("video", "character"):
        if not isinstance(sw.get(k), str) or not sw[k]:
            raise SpecError(f'clip "source.swap.{k}" must be a file path')
    mode = sw.get("mode", "simple")
    if mode not in SWAP_MODES:
        raise SpecError(f'clip "source.swap.mode" must be one of {list(SWAP_MODES)}, got {mode!r}')
    seed = sw.get("seed", 0)
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise SpecError('clip "source.swap.seed" must be a non-negative integer')

    def _abs(p: str) -> str:
        p = os.path.expanduser(p)
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(base_dir, p))

    return {"video": _abs(sw["video"]), "character": _abs(sw["character"]), "mode": mode,
            "prompt": str(sw.get("prompt", "")), "target": str(sw.get("target", "")), "seed": seed}


def _file_digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def swap_key(req: dict[str, Any]) -> str:
    """Content key of a swap request: the bytes of both inputs plus every setting (16 hex chars)."""
    parts = [_file_digest(req["video"]), _file_digest(req["character"]), req["mode"], req["prompt"],
             req.get("target", ""), int(req.get("seed", 0))]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def swap_dir(base_dir: str) -> str:
    """Job folder swapped footage is downloaded into: $KSTUDIO_SWAP_DIR, else <project>/out/swap."""
    return os.environ.get("KSTUDIO_SWAP_DIR") or os.path.join(base_dir, "out", "swap")


def swap_requests(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Every swap source in a spec's clip scenes (and clip scenes nested in `over`), normalised, each with
    its scene path ("/scenes/2") under "path"."""
    base = str(spec.get("_dir", "."))
    out: list[dict[str, Any]] = []

    def visit(scenes: Any, path: str) -> None:
        if not isinstance(scenes, list):
            return
        for i, sc in enumerate(scenes):
            if not isinstance(sc, dict):
                continue
            if sc.get("type") == "clip" and sc.get("source") is not None:
                req = swap_source(sc["source"], base)
                if req is not None:
                    req["path"] = f"{path}/{i}"
                    out.append(req)
            visit(sc.get("over"), f"{path}/{i}/over")

    visit(spec.get("scenes"), "/scenes")
    return out
