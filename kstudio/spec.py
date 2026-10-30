"""Spec loading: JSON (with // and /* */ comments, trailing commas) or a JS/MJS module
whose default export is the spec object (evaluated with the system `node`)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from typing import Any


class SpecError(ValueError):
    pass


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
    path = os.path.abspath(path)
    if os.path.isdir(path):
        for name in ("spec.json", "spec.jsonc", "spec.mjs", "spec.js"):
            if os.path.exists(os.path.join(path, name)):
                path = os.path.join(path, name)
                break
        else:
            raise SpecError(f"no spec.json / spec.js in {path}")
    if path.endswith((".js", ".mjs")):
        node = shutil.which("node")
        if not node:
            raise SpecError("JS specs need `node` on PATH (or use spec.json)")
        code = ("import(process.argv[1]).then(m=>{let s=m.default??m.spec;"
                "if(typeof s==='function')s=s();return Promise.resolve(s)})"
                ".then(s=>process.stdout.write(JSON.stringify(s)))")
        url = "file://" + path
        r = subprocess.run([node, "--input-type=module", "-e", code, url], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise SpecError(f"node failed to evaluate {path}:\n{r.stderr}")
        spec = json.loads(r.stdout)
    else:
        with open(path, encoding="utf-8") as fh:
            try:
                spec = json.loads(strip_json_comments(fh.read()))
            except json.JSONDecodeError as e:
                raise SpecError(f"{path}: {e}") from None
    if not isinstance(spec, dict):
        raise SpecError("spec must be an object")
    spec.setdefault("_dir", os.path.dirname(path))
    spec.setdefault("_path", path)
    return spec


def spec_hash(spec: dict[str, Any], extra: Any = None) -> str:
    clean = {k: v for k, v in spec.items() if not k.startswith("_")}
    blob = json.dumps([clean, extra], sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
