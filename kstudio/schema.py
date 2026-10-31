"""Spec JSON Schema (draft 2020-12) generated from the engine, plus a validator with JSON-Pointer errors.

Nothing here is hand-typed per scene. The schema is assembled at run time from:
  * the scene registry (kstudio.scenes.REGISTRY): one definition per scene type with its docstring,
    its `defaults` (name, default value, JSON type) and every other parameter its code reads
    (`self.p["key"]`, `self.p.get("key")`, aliases of `self.p`), found by walking the class source;
  * the shared scene keys read by Film / Scene / transitions (`type`, `at`, `dur`, `transition`, ...);
  * engine tables: MODES, THEMES, music STYLES, TRANSITIONS, wipe shapes, directions, CAMERA_KEYS,
    ground styles, grade presets, marker kinds.

Validation runs in two stages: the schema (structure, types, enums, unknown keys) and then a dry
build of the Film (time expressions, marker names, durations, transitions), which catches what a
schema cannot. Every issue is a dict {path, message, hint, keyword, severity} where `path` is a JSON
Pointer into the spec ("/scenes/2/dur").

Types inferred from a scene default are marked `inferred`: `validate` reports them as errors, while
`render` only blocks on structural and build errors (a polymorphic parameter the analysis could not
see must not stop a render that would work).
"""
from __future__ import annotations

import ast
import copy
import difflib
import inspect
import json
import re
import textwrap
from functools import cache, lru_cache
from typing import Any

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID = "https://github.com/28naem-del/kinetic-studio/schemas/spec.schema.json"
SNAP_NAMES = ("beat", "8th", "eighth", "16th", "sixteenth", "bar", "half", "quarter", "off", "none")
TIME_HELP = ('seconds (1.5) or a beat expression: "2b", "1/2b", "1bar", a marker or scene id ("drop"), '
             'or arithmetic on them ("drop-2b")')


# --------------------------------------------------------------------------- source analysis

def _src_tree(obj: Any) -> ast.AST | None:
    try:
        return ast.parse(textwrap.dedent(inspect.getsource(obj)))
    except (OSError, TypeError, SyntaxError):  # no source (zipped / frozen install): defaults only
        return None


def _is_param_dict(node: ast.AST, aliases: set[str]) -> bool:
    """True for `self.p`, `self.p_raw` or a local alias of them."""
    if isinstance(node, ast.Attribute) and node.attr in ("p", "p_raw") and isinstance(node.value, ast.Name) \
            and node.value.id == "self":
        return True
    return isinstance(node, ast.Name) and node.id in aliases


def _key_of(node: ast.AST, aliases: set[str]) -> str | None:
    """The literal key of `P["k"]` / `P.get("k", ...)` where P is the scene's parameter dict."""
    if isinstance(node, ast.Subscript) and _is_param_dict(node.value, aliases):
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr in ("get", "setdefault", "pop") and _is_param_dict(node.func.value, aliases) \
            and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return node.args[0].value
    return None


@cache
def _module_funcs(module_name: str) -> dict[str, tuple[tuple[tuple[str, ...], frozenset[str], bool], ...]]:
    """Module-level helpers: per positional parameter, (keys read from it when it is a parameter dict,
    keys it type-tests, whether the parameter itself is type-tested with isinstance)."""
    import importlib
    tree = _src_tree(importlib.import_module(module_name))
    out: dict[str, tuple[tuple[tuple[str, ...], frozenset[str], bool], ...]] = {}
    if tree is None:
        return out
    for fn in getattr(tree, "body", []):
        if not isinstance(fn, ast.FunctionDef):
            continue
        per: list[tuple[tuple[str, ...], frozenset[str], bool]] = []
        for a in fn.args.args:
            names = {a.arg}
            keys: list[str] = []
            poly: set[str] = set()
            tested = False
            for n in ast.walk(fn):
                k = _key_of(n, names)
                if k and k not in keys:
                    keys.append(k)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "isinstance" and n.args:
                    if isinstance(n.args[0], ast.Name) and n.args[0].id == a.arg:
                        tested = True
                    k0 = _key_of(n.args[0], names)
                    if k0:
                        poly.add(k0)
            per.append((tuple(keys), frozenset(poly), tested))
        out[fn.name] = tuple(per)
    return out


@cache
def scene_param_reads(cls: type) -> tuple[tuple[str, ...], frozenset[str]]:
    """(keys the scene class reads from its parameters in source order, keys it accepts in more than
    one shape). Walks the class and its scene base classes (not the generic Scene base)."""
    from .scenes.base import Scene
    keys: list[str] = []
    poly: set[str] = set()
    for klass in cls.__mro__:
        if klass is Scene or klass is object or not issubclass(klass, Scene):
            continue
        tree = _src_tree(klass)
        if tree is None:
            continue
        aliases: set[str] = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                    and _is_param_dict(n.value, set()):
                aliases.add(n.targets[0].id)
        funcs = _module_funcs(klass.__module__)
        values: dict[str, str] = {}  # local name -> param key it holds (`sp = self.p["spark"]`)
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                vk = _key_of(n.value, aliases)
                if vk:
                    values[n.targets[0].id] = vk

        def _held(node: ast.AST, aliases: set[str] = aliases, values: dict[str, str] = values) -> str | None:
            return _key_of(node, aliases) or (values.get(node.id) if isinstance(node, ast.Name) else None)

        for n in ast.walk(tree):
            k = _key_of(n, aliases)
            if k and k not in keys:
                keys.append(k)
            if isinstance(n, ast.Compare) and isinstance(n.left, ast.Constant) and isinstance(n.left.value, str) \
                    and any(isinstance(op, (ast.In, ast.NotIn)) for op in n.ops) \
                    and any(_is_param_dict(c, aliases) for c in n.comparators):
                if n.left.value not in keys:
                    keys.append(n.left.value)
            if isinstance(n, ast.Compare) and any(isinstance(op, (ast.Is, ast.IsNot)) for op in n.ops) \
                    and any(isinstance(c, ast.Constant) and isinstance(c.value, bool) for c in n.comparators):
                hk0 = _held(n.left)  # `sp is True`: a flag that also takes other shapes
                if hk0:
                    poly.add(hk0)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.args:
                k0 = _held(n.args[0])
                if k0 and n.func.id == "isinstance":
                    poly.add(k0)
                for j, arg in enumerate(n.args):
                    info = funcs.get(n.func.id)
                    if info is None or j >= len(info):
                        continue
                    hk, hpoly, tested = info[j]
                    if _is_param_dict(arg, aliases):  # helper(self.p): keys the helper reads
                        keys.extend(k for k in hk if k not in keys)
                        poly |= hpoly
                    kj = _held(arg)
                    if kj and tested:  # helper(self.p["k"]) where the helper accepts several shapes
                        poly.add(kj)
    return tuple(keys), frozenset(poly)


@lru_cache(maxsize=1)
def _shared_scene_keys() -> tuple[str, ...]:
    """Scene keys handled by Film / Scene / transitions rather than by the scene's own code."""
    from . import film
    from .scenes import base
    keys: list[str] = []
    fsrc = inspect.getsource(film) if _src_tree(film) is not None else ""
    for pat in (r"\bsc(?:\.get\(|\[)\"([a-z_]+)\"", r"\.b\.p\.get\(\"([a-z_]+)\""):
        for k in re.findall(pat, fsrc):
            if k not in keys:
                keys.append(k)
    try:
        bsrc = inspect.getsource(base.Scene.__init__)
    except (OSError, TypeError):
        bsrc = ""
    for k in re.findall(r"params\.get\(\"([a-z_]+)\"", bsrc):
        if k not in keys:
            keys.append(k)
    for k in sorted(getattr(film, "RESERVED", ())):
        if k not in keys:
            keys.append(k)
    return tuple(keys)


@lru_cache(maxsize=1)
def _transition_param_keys() -> tuple[str, ...]:
    from . import film, transitions
    keys: list[str] = []
    for mod in (transitions, film):
        try:
            src = inspect.getsource(mod)
        except (OSError, TypeError):
            continue
        for k in re.findall(r"\b(?:params|tparams)(?:\.get\(|\[)\"([a-z_]+)\"", src):
            if k not in keys:
                keys.append(k)
    return tuple(keys)


# --------------------------------------------------------------------------- schema pieces

def _json_type(v: Any) -> str | None:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, (list, tuple)):
        return "array"
    if isinstance(v, dict):
        return "object"
    return None


def _from_default(v: Any) -> dict[str, Any]:
    t = _json_type(v)
    if t is None:
        return {}
    out: dict[str, Any] = {"type": t}
    if t == "array" and v:
        its = {_json_type(x) for x in v}
        if len(its) == 1 and None not in its and its != {"array"} and its != {"object"}:
            out["items"] = {"type": its.pop()}
    return out


def _time() -> dict[str, Any]:
    return {"$ref": "#/$defs/time"}


def _example_values() -> dict[str, dict[str, Any]]:
    """scene type -> {key: first value used in a bundled example} (for schema `examples`)."""
    from .spec import examples_dir, load_spec
    out: dict[str, dict[str, Any]] = {}
    d = examples_dir()
    if not d.is_dir():
        return out
    for sub in sorted(p for p in d.iterdir() if p.is_dir()):
        try:
            spec = load_spec(str(sub))
        except Exception:  # noqa: BLE001 - a broken example must not break the schema
            continue
        for sc in spec.get("scenes", []) if isinstance(spec.get("scenes"), list) else []:
            if isinstance(sc, dict) and isinstance(sc.get("type"), str):
                slot = out.setdefault(sc["type"], {})
                for k, v in sc.items():
                    slot.setdefault(k, v)
    return out


def scene_definition(name: str, cls: type, examples: dict[str, Any] | None = None) -> dict[str, Any]:
    """JSON Schema for one scene type, generated from the class."""
    from .scenes.base import BEAT_COUNT_PARAMS, GROUND_STYLES, TIME_LIST_PARAMS, TIME_PARAMS
    examples = examples or {}
    reads, poly = scene_param_reads(cls)
    time_keys = set(TIME_PARAMS) | set(getattr(cls, "time_params", ()))
    beat_keys = set(BEAT_COUNT_PARAMS.get(name, ())) | set(getattr(cls, "beat_params", ()))
    defaults: dict[str, Any] = dict(getattr(cls, "defaults", {}))
    props: dict[str, Any] = {
        "type": {"const": name, "description": "scene type"},
        "id": {"type": "string", "description": "scene id: a marker name other times can refer to "
                                                "(\"at\": \"drop-2b\") and the match-cut key"},
        "at": {**_time(), "description": "start time; default: right after the previous scene"},
        "dur": {"$ref": "#/$defs/duration", "description": "scene length (default 2.0 s); whole beats "
                                                         "such as \"4b\" keep every cut on the grid"},
        "transition": {"$ref": "#/$defs/transition", "description": "how this scene enters (ignored on the "
                                                                    "first scene); default: the mode's"},
        "transition_dur": {"$ref": "#/$defs/duration", "description": "override the transition window"},
        "transition_sfx": {"type": "boolean", "description": "play the transition's sound (default true)"},
        "camera": {"anyOf": [{"type": "boolean"}, {"$ref": "#/$defs/camera"}],
                   "description": "false = locked off; an object overrides the mode camera for this scene"},
        "ground": {"type": "string", "description": "background colour key of the theme (bg, bg2, paper, "
                                                    "accent, accent2, ink ...)"},
        "ground_style": {"enum": list(GROUND_STYLES), "description": "background treatment"},
    }
    for k in _shared_scene_keys():
        props.setdefault(k, {"description": "shared scene key"})
    keys = list(defaults) + [k for k in reads if k not in defaults]
    for k in keys:
        if k in props and k not in defaults:
            continue
        has_default = k in defaults
        dv = defaults.get(k)
        if k in TIME_LIST_PARAMS:
            p: dict[str, Any] = {"type": "array", "description": "list of times (seconds / beats) or items "
                                                                 "with \"t\"/\"at\"/\"dur\""}
        elif k in beat_keys:
            p = {"anyOf": [{"type": "number"}, {"type": "string"}], "description": "a count of beats"}
        elif k in time_keys:
            p = {"anyOf": [{"type": "number"}, {"type": "string", "minLength": 1}, {"type": "null"}],
                 "description": "time: " + TIME_HELP}
        elif k in poly or not has_default:
            p = {"description": "accepts several shapes" if k in poly else "optional; no default"}
        else:
            p = _from_default(dv)
            ex0 = examples.get(k)
            if p and ex0 is not None and _json_type(ex0) not in (None, p["type"]) \
                    and not (p["type"] == "number" and isinstance(ex0, (int, float))):
                p = {"anyOf": [p, _from_default(ex0)]}  # a bundled example uses another shape
            if p:
                p["x-inferred"] = True
        if has_default:
            p["default"] = copy.deepcopy(dv)
        ex = examples.get(k)
        if ex is not None and len(json.dumps(ex, default=str)) <= 160:
            p["examples"] = [ex]
        props[k] = p
    if name == "clip":
        from .spec import SWAP_MODES
        props["source"] = {
            "anyOf": [
                {"type": "null"},
                {"type": "object", "maxProperties": 0},
                {"type": "object", "required": ["swap"], "additionalProperties": False,
                 "properties": {"swap": {
                     "type": "object", "required": ["video", "character"], "additionalProperties": False,
                     "properties": {
                         "video": {"type": "string", "minLength": 1},
                         "character": {"type": "string", "minLength": 1},
                         "mode": {"enum": list(SWAP_MODES)},
                         "prompt": {"type": "string"}, "target": {"type": "string"},
                         "seed": {"type": "integer", "minimum": 0},
                     }}}},
            ], "description": "Optional Openjutsu source; without a backend the input video is used.",
        }
    doc = inspect.cleandoc(cls.__doc__ or "")
    return {"type": "object", "title": name, "description": " ".join(doc.split()),
            "properties": props, "required": ["type"], "additionalProperties": False}


def _defs_common() -> dict[str, Any]:
    from . import transitions as TR
    from .audio.music import STYLES
    from .camera import CAMERA_KEYS
    from .grade import GRADES
    from .modes import MODES, THEMES
    from .timeline import MARKER_KINDS

    kinds = sorted(TR.TRANSITIONS)
    tr_pattern = "^(" + "|".join(kinds) + r")(-[a-z0-9]+){0,2}$"
    tr_obj_props: dict[str, Any] = {
        "kind": {"enum": kinds}, "dir": {"enum": sorted(TR.DIRS)}, "shape": {"enum": list(TR.WIPE_SHAPES)},
        "dur": {"$ref": "#/$defs/duration"}, "on": {"enum": ["downbeat", "bar"]},
        "text": {"type": "string"}, "sfx": {"type": ["string", "null", "boolean"]},
    }
    for k in _transition_param_keys():
        tr_obj_props.setdefault(k, {})
    post_keys = sorted({k for m in MODES.values() for k in m.get("post", {})})
    audit_keys = sorted({k for m in MODES.values() for k in m.get("audit", {})})
    return {
        "time": {"description": "A time: " + TIME_HELP,
                 "anyOf": [{"type": "number"}, {"type": "string", "minLength": 1}],
                 "examples": [0, 1.5, "2b", "1/2b", "1bar", "drop", "drop-2b"]},
        "duration": {"description": "A length: seconds or beats (\"4b\", \"1/2b\", \"2bar\"). Whole beats keep "
                                    "cuts on the grid.",
                     "anyOf": [{"type": "number", "minimum": 0}, {"type": "string", "minLength": 1}],
                     "examples": ["4b", "2bar", "1/2b", 2.0]},
        "transition": {"description": "Transition into a scene: \"kind\" or \"kind-dir\" / \"wipe-circle\" "
                                      "shorthand, or an object. Kinds and default beats: "
                                      + ", ".join(f"{k} {TR.TRANSITIONS[k]['beats']:g}b" for k in kinds),
                       "anyOf": [{"type": "string", "pattern": tr_pattern},
                                 {"type": "object", "properties": tr_obj_props, "required": ["kind"],
                                  "additionalProperties": True}],
                       "examples": ["whip-left", "push-up", "wipe-circle", "flash", "cut",
                                    {"kind": "match", "element": "pill"}]},
        "camera": {"type": "object", "description": "2.5D camera feel (mode defaults apply)",
                   "properties": {k: {} for k in CAMERA_KEYS}, "additionalProperties": False},
        "music": {"type": "object", "description": "Soundtrack: a procedural style at a BPM, or a music file "
                                                   "(beats are detected unless bpm is given)",
                  "properties": {"style": {"enum": sorted(STYLES) + ["none"]},
                                 "bpm": {"type": "number", "exclusiveMinimum": 0},
                                 "offset": {"type": "number"}, "file": {"type": "string"},
                                 "sync": {"type": "boolean"}, "start": {"type": "number"},
                                 "gain": {"type": "number"}, "sfx": {"type": "number"},
                                 "sfx_quantize": {"type": "boolean"}, "sfx_grid": {"type": "number"}},
                  "additionalProperties": True},
        "post": {"type": "object", "description": "Finishing: bloom, grain, vignette, chroma, grade preset",
                 "properties": {**{k: {"type": "number"} for k in post_keys if k != "grade"},
                                "grade": {"anyOf": [{"enum": sorted(GRADES)}, {"type": "object"}]}},
                 "additionalProperties": True},
        "audit": {"type": "object", "description": "Quality-audit thresholds",
                  "properties": {k: {"type": "number"} for k in audit_keys}, "additionalProperties": True},
        "theme": {"description": "Theme name, or an object with a `base` theme and colour/font overrides",
                  "anyOf": [{"enum": sorted(THEMES)},
                            {"type": "object", "properties": {"base": {"enum": sorted(THEMES)}},
                             "additionalProperties": True}]},
        "marker": {"type": "object", "description": "Timeline marker: section (energy), sfx (sound), hit, "
                                                    "caption or source/lower_third",
                   "properties": {"kind": {"enum": list(MARKER_KINDS) + ["source", "lower_third"]},
                                  "at": _time(), "name": {"type": "string"}, "dur": {"$ref": "#/$defs/duration"},
                                  "energy": {"type": "number"}, "sound": {"type": "string"},
                                  "gain": {"type": "number"}, "text": {"type": "string"}, "tag": {"type": "string"}},
                   "required": ["at"], "additionalProperties": True},
        "caption": {"type": "object", "properties": {"text": {"type": "string"}, "at": _time(),
                                                     "dur": {"$ref": "#/$defs/duration"},
                                                     "karaoke": {"type": "boolean"}},
                    "required": ["text", "at"], "additionalProperties": True},
        "source": {"type": "object", "properties": {"tag": {"type": "string"}, "text": {"type": "string"},
                                                    "at": _time(), "dur": {"$ref": "#/$defs/duration"}},
                   "required": ["text", "at"], "additionalProperties": True},
    }


@lru_cache(maxsize=8)
def _build_schema(types: tuple[str, ...] | None) -> str:
    from .modes import MODES
    from .scenes import REGISTRY
    names = sorted(REGISTRY) if types is None else [t for t in sorted(REGISTRY) if t in types]
    ex = _example_values()
    defs = _defs_common()
    for n in names:
        defs[f"scene.{n}"] = scene_definition(n, REGISTRY[n], ex.get(n))
    schema: dict[str, Any] = {
        "$schema": SCHEMA_DIALECT,
        "$id": SCHEMA_ID,
        "title": "Kinetic Studio spec",
        "description": "A brief rendered as a beat-synced motion-graphics video. Scenes play in order; "
                       "times and lengths accept seconds or beat units at the music BPM.",
        "type": "object",
        "properties": {
            "$schema": {"type": "string"},
            "mode": {"enum": sorted(MODES), "description": "preset of theme, music, camera, post and audit "
                                                           "(default kinetic)"},
            "theme": {"$ref": "#/$defs/theme"},
            "theme_overrides": {"type": "object", "description": "colour / font keys merged over the theme"},
            "handle": {"type": "string", "description": "@handle shown in the chrome (kinetic) or end cards"},
            "progress": {"type": "boolean", "description": "draw a progress bar"},
            "bpm": {"type": "number", "exclusiveMinimum": 0, "description": "shortcut for music.bpm"},
            "fps": {"type": "number", "exclusiveMinimum": 0},
            "duration": {**_time(), "description": "film length; default: the end of the last scene"},
            "snap": {"anyOf": [{"type": "boolean"}, {"enum": list(SNAP_NAMES)}, {"type": "number"}],
                     "description": "snap seconds-valued lengths/starts to the beat grid"},
            "transition": {"$ref": "#/$defs/transition"},
            "music": {"$ref": "#/$defs/music"},
            "camera": {"$ref": "#/$defs/camera"},
            "post": {"$ref": "#/$defs/post"},
            "audit": {"$ref": "#/$defs/audit"},
            "lufs": {"type": "number", "description": "loudness target (default -14)"},
            "creator": {"type": "object", "description": "creator identity for social end cards: name, handle, "
                                                         "series, initials", "additionalProperties": True},
            "meta": {"type": "object", "description": "free-form notes; ignored by the renderer"},
            "markers": {"type": "array", "items": {"$ref": "#/$defs/marker"}},
            "captions": {"type": "array", "items": {"$ref": "#/$defs/caption"}},
            "sources": {"type": "array", "items": {"$ref": "#/$defs/source"}},
            "scenes": {"type": "array", "minItems": 1, "description": "scenes in play order",
                       "items": {"oneOf": [{"$ref": f"#/$defs/scene.{n}"} for n in names],
                                 "x-discriminator": "type"}},
        },
        "required": ["scenes"],
        "additionalProperties": False,
        "$defs": defs,
        "x-scene-types": names,
    }
    return json.dumps(schema, sort_keys=False, default=str)


def spec_schema(scene_types: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    """The spec JSON Schema (draft 2020-12). `scene_types` narrows the scene union (smaller prompts)."""
    key = None if scene_types is None else tuple(sorted(set(scene_types)))
    return json.loads(_build_schema(key))


def scene_types() -> list[str]:
    from .scenes import REGISTRY
    return sorted(REGISTRY)


def scene_catalog() -> list[dict[str, Any]]:
    """Every scene type with its description and fields (from the generated schema)."""
    s = spec_schema()
    out = []
    for n in s["x-scene-types"]:
        d = s["$defs"][f"scene.{n}"]
        fields = {}
        for k, p in d["properties"].items():
            if k == "type":
                continue
            f: dict[str, Any] = {}
            if "default" in p:
                f["default"] = p["default"]
            if "type" in p:
                f["type"] = p["type"]
            elif "$ref" in p:
                f["type"] = p["$ref"].rsplit("/", 1)[-1]
            if p.get("description"):
                f["description"] = p["description"]
            fields[k] = f
        out.append({"type": n, "description": d["description"], "fields": fields})
    return out


# --------------------------------------------------------------------------- validator

def _ptr(path: tuple[Any, ...]) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in path)


def _short(v: Any) -> str:
    s = json.dumps(v, default=str)
    return s if len(s) <= 60 else s[:57] + "..."


def _types_ok(v: Any, t: str | list[str]) -> bool:
    ts = [t] if isinstance(t, str) else t
    for x in ts:
        if x == "null" and v is None:
            return True
        if x == "boolean" and isinstance(v, bool):
            return True
        if x == "number" and isinstance(v, (int, float)) and not isinstance(v, bool):
            return True
        if x == "integer" and isinstance(v, int) and not isinstance(v, bool):
            return True
        if x == "string" and isinstance(v, str):
            return True
        if x == "array" and isinstance(v, list):
            return True
        if x == "object" and isinstance(v, dict):
            return True
    return False


def _jtype(v: Any) -> str:
    return {bool: "boolean", int: "number", float: "number", str: "string", list: "array",
            dict: "object", type(None): "null"}.get(type(v), type(v).__name__)


def _suggest(word: str, options: list[str]) -> str | None:
    m = difflib.get_close_matches(str(word), [str(o) for o in options], n=1, cutoff=0.6)
    return m[0] if m else None


class _Validator:
    def __init__(self, root: dict[str, Any]):
        self.root = root
        self.issues: list[dict[str, Any]] = []

    def resolve(self, s: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in s:
            ref = s["$ref"]
            node: Any = self.root
            for part in ref.lstrip("#/").split("/"):
                node = node[part.replace("~1", "/").replace("~0", "~")]
            extra = {k: v for k, v in s.items() if k != "$ref"}
            s = {**node, **{k: v for k, v in extra.items() if k not in node or k == "description"}}
        return s

    def add(self, path: tuple[Any, ...], keyword: str, message: str, hint: str, severity: str = "error",
            **kw: Any) -> None:
        self.issues.append({"path": _ptr(path), "message": message, "hint": hint, "keyword": keyword,
                            "severity": severity, **kw})

    def sub(self, v: Any, s: dict[str, Any], path: tuple[Any, ...]) -> list[dict[str, Any]]:
        saved = self.issues
        self.issues = []
        self.check(v, s, path)
        out, self.issues = self.issues, saved
        return [i for i in out if i["severity"] == "error"]

    def check(self, v: Any, s: dict[str, Any], path: tuple[Any, ...], ctx: str = "") -> None:
        s = self.resolve(s)
        where = _ptr(path) or "the spec"
        if "anyOf" in s:
            branches = s["anyOf"]
            results = [self.sub(v, b, path) for b in branches]
            if not any(len(r) == 0 for r in results):
                best = min(results, key=len)
                if len(best) == 1 and best[0]["path"] != _ptr(path):
                    self.issues.extend(best)  # a single nested problem in the closest branch
                else:
                    desc = s.get("description", "")
                    self.add(path, "anyOf", f"{where}: {_short(v)} is not an accepted value",
                             (desc + " " if desc else "") + self._expect_hint(branches), value=v)
            else:
                ok = next(i for i, r in enumerate(results) if not r)
                self.check(v, branches[ok], path, ctx)  # collect warnings of the matching branch
            return
        if "oneOf" in s and s.get("x-discriminator"):
            self.check_union(v, s, path)
            return
        if "type" in s and not _types_ok(v, s["type"]):
            want = s["type"] if isinstance(s["type"], str) else " or ".join(s["type"])
            hint = f"set {where} to a {want} (got {_jtype(v)} {_short(v)})"
            if "default" in s:
                hint += f"; the default is {_short(s['default'])}"
            extra: dict[str, Any] = {"inferred": True} if s.get("x-inferred") else {}
            self.add(path, "type", f"{where} must be a {want}, got {_jtype(v)}", hint, value=v, **extra)
            return
        if "const" in s and v != s["const"]:
            self.add(path, "const", f"{where} must be {_short(s['const'])}", f"set it to {_short(s['const'])}")
        if "enum" in s and v not in s["enum"]:
            opts = list(s["enum"])
            sug = _suggest(v, [o for o in opts if isinstance(o, str)]) if isinstance(v, str) else None
            self.add(path, "enum", f"{where}: {_short(v)} is not one of {opts}",
                     (f"did you mean {_short(sug)}? " if sug else "") + f"use one of: {', '.join(map(str, opts))}",
                     value=v, **({"suggestion": sug} if sug else {}))
        if isinstance(v, str):
            if len(v) < s.get("minLength", 0):
                self.add(path, "minLength", f"{where} must not be empty", "write a non-empty value")
            if "pattern" in s and not re.search(s["pattern"], v):
                self.add(path, "pattern", f"{where}: {_short(v)} does not match the expected form",
                         s.get("description", "") or f"must match {s['pattern']}", value=v)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if "minimum" in s and v < s["minimum"]:
                self.add(path, "minimum", f"{where} must be >= {s['minimum']}", f"use a value >= {s['minimum']}")
            if "exclusiveMinimum" in s and v <= s["exclusiveMinimum"]:
                self.add(path, "exclusiveMinimum", f"{where} must be > {s['exclusiveMinimum']}",
                         f"use a value > {s['exclusiveMinimum']}")
        if isinstance(v, dict):
            props = s.get("properties", {})
            for r in s.get("required", []):
                if r not in v:
                    self.add(path + (r,), "required", f"{where} is missing \"{r}\"", f"add \"{r}\" to {where}")
            for k, item in v.items():
                if k in props:
                    self.check(item, props[k], path + (k,))
                elif str(k).startswith("_"):
                    continue  # loader bookkeeping (_dir, _path)
                elif s.get("additionalProperties", True) is False:
                    sug = _suggest(k, list(props))
                    name = f"a {s['title']} scene" if s.get("title") else where
                    self.add(path + (k,), "additionalProperties", f"\"{k}\" is not a field of {name}",
                             (f"rename it to \"{sug}\"" if sug else f"remove \"{k}\"") +
                             f"; fields: {', '.join(sorted(props))}", severity="warning",
                             **({"suggestion": sug} if sug else {}))
                elif isinstance(s.get("additionalProperties"), dict):
                    self.check(item, s["additionalProperties"], path + (k,))
        if isinstance(v, list):
            if len(v) < s.get("minItems", 0):
                self.add(path, "minItems", f"{where} needs at least {s['minItems']} item(s)",
                         f"add at least {s['minItems']} item(s) to {where}")
            if "items" in s:
                for i, item in enumerate(v):
                    self.check(item, s["items"], path + (i,))

    def check_union(self, v: Any, s: dict[str, Any], path: tuple[Any, ...]) -> None:
        key = s["x-discriminator"]
        where = _ptr(path)
        by: dict[str, dict[str, Any]] = {}
        for b in s["oneOf"]:
            rb = self.resolve(b)
            by[rb["properties"][key]["const"]] = b
        if not isinstance(v, dict):
            self.add(path, "type", f"{where} must be an object, got {_jtype(v)}",
                     f"write the scene as an object with a \"{key}\"", value=v)
            return
        if key not in v:
            self.add(path + (key,), "required", f"{where} has no \"{key}\"",
                     f"add \"{key}\": one of {', '.join(sorted(by))}")
            return
        t = v[key]
        if t not in by:
            sug = _suggest(t, sorted(by)) if isinstance(t, str) else None
            self.add(path + (key,), "enum", f"unknown scene type {_short(t)}",
                     (f"did you mean \"{sug}\"? " if sug else "") + f"scene types: {', '.join(sorted(by))}",
                     value=t, **({"suggestion": sug} if sug else {}))
            return
        self.check(v, by[t], path)

    @staticmethod
    def _expect_hint(branches: list[dict[str, Any]]) -> str:
        parts = []
        for b in branches:
            if "enum" in b:
                parts.append("one of " + ", ".join(map(str, b["enum"])))
            elif "$ref" in b:
                parts.append(b["$ref"].rsplit("/", 1)[-1] + " object")
            elif "type" in b:
                parts.append(str(b["type"]))
        return "expected " + " or ".join(parts) if parts else ""


def _find_value(obj: Any, needle: Any, path: tuple[Any, ...] = ()) -> tuple[Any, ...] | None:
    if obj == needle and not isinstance(obj, (dict, list)):
        return path
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not str(k).startswith("_"):
                r = _find_value(v, needle, path + (k,))
                if r is not None:
                    return r
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            r = _find_value(v, needle, path + (i,))
            if r is not None:
                return r
    return None


def _find_term(obj: Any, term: str) -> tuple[Any, ...] | None:
    """Path of the first value equal to `term` or a string expression that contains it as a token."""
    exact = _find_value(obj, term)
    if exact is not None or not term.strip():
        return exact
    pat = re.compile(r"(?<![\w.])" + re.escape(term) + r"(?![\w])")

    def walk(o: Any, path: tuple[Any, ...]) -> tuple[Any, ...] | None:
        if isinstance(o, str):
            return path if pat.search(o) else None
        items = o.items() if isinstance(o, dict) else enumerate(o) if isinstance(o, list) else ()
        for k, v in items:
            if isinstance(k, str) and k.startswith("_"):
                continue
            r = walk(v, path + (k,))
            if r is not None:
                return r
        return None
    return walk(obj, ())


def _build_issue(exc: BaseException, spec: dict[str, Any]) -> dict[str, Any]:
    """A Film build failure -> issue with the best JSON Pointer we can recover."""
    msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
    msg = str(msg)
    path: tuple[Any, ...] = ()
    idx = None
    tb = exc.__traceback__
    while tb is not None:  # the scene being built when it failed (Film._build's loop variable)
        f = tb.tb_frame
        if f.f_code.co_name == "_build" and isinstance(f.f_locals.get("i"), int) and "raw" in f.f_locals:
            idx = f.f_locals["i"]
        tb = tb.tb_next
    m = re.search(r"scene #(\d+)", msg)
    if m:
        idx = int(m.group(1))
    quoted = re.findall(r"'([^']*)'", msg)
    if idx is not None:
        path = ("scenes", idx)
        sc = spec.get("scenes", [])[idx] if idx < len(spec.get("scenes", [])) else {}
        for q in quoted:
            sub = _find_term(sc, q)
            if sub is not None:
                path = path + sub
                break
        else:
            if "duration" in msg:
                path = path + ("dur",)
            elif "transition" in msg:
                path = path + ("transition",)
    else:
        for q in quoted:
            sub = _find_term(spec, q)
            if sub is not None:
                path = sub
                break
    hint = "fix the value at this path"
    low = msg.lower()
    if "time" in low or "marker" in low or "duration" in low:
        hint = ("write times as " + TIME_HELP + "; a marker name must be a scene id or a markers[].name "
                "defined in the spec")
    if "unknown marker" in low:
        known = re.search(r"known: (\[.*?\])", msg)
        hint = "use a defined marker or scene id" + (f" ({known.group(1)})" if known else "") + \
            ", or plain beats such as \"8b\""
    if "non-positive duration" in low:
        hint = "give the scene a positive length such as \"4b\""
    if "unknown transition" in low:
        hint = msg.split(";", 1)[-1].strip()
    return {"path": _ptr(path), "message": msg, "hint": hint, "keyword": "build", "severity": "error",
            "exception": type(exc).__name__}


def validate_spec(spec: dict[str, Any], strict: bool = False, build: bool = True) -> dict[str, Any]:
    """Validate a spec dict. Returns {valid, errors, warnings, summary}. `strict` turns unknown-key
    warnings into errors. `build` dry-builds the Film when the schema stage is clean."""
    v = _Validator(spec_schema())
    v.check(spec, v.root, ())
    errors = [i for i in v.issues if i["severity"] == "error"]
    warnings = [i for i in v.issues if i["severity"] != "error"]
    if strict:
        for w in warnings:
            w["severity"] = "error"
        errors, warnings = errors + warnings, []
    summary: dict[str, Any] | None = None
    if build and not errors:
        from .film import Film
        clean = {k: copy.deepcopy(val) for k, val in spec.items() if k not in ("_fps",)}
        try:
            film = Film(clean, 1080, 1920)
        except Exception as e:  # noqa: BLE001 - every build failure is a spec problem to report
            errors.append(_build_issue(e, spec))
        else:
            s = film.summary()
            summary = {"mode": s["mode"], "duration": s["duration"], "bpm": round(film.tl.bpm, 3),
                       "beats": round(film.tl.time_to_beat(film.duration) - film.tl.time_to_beat(0.0), 3),
                       "scenes": len(s["scenes"]), "transitions": len(s["transitions"])}
            for item in film.lint():
                if item["level"] != "warn":
                    continue
                m = re.match(r"scene #(\d+)", item["where"])
                warnings.append({"path": _ptr(("scenes", int(m.group(1)), "dur")) if m else "",
                                 "message": f"{item['where']}: {item['msg']}", "hint": item["msg"],
                                 "keyword": "lint", "severity": "warning"})
    return {"valid": not errors, "errors": errors, "warnings": warnings, "summary": summary}


def blocking(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Errors that must stop a render (structure, enums, build); default-inferred type checks do not."""
    return [e for e in errors if not e.get("inferred")]


def fix_prompt(result: dict[str, Any], spec: dict[str, Any] | None = None) -> str:
    """Validation result -> instructions an LLM can act on to repair the spec."""
    errs = result.get("errors", [])
    warns = result.get("warnings", [])
    if not errs and not warns:
        return "The spec is valid. Render it as is."
    lines = []
    if errs:
        lines.append(f"The Kinetic Studio spec failed validation with {len(errs)} error(s). Fix exactly these "
                     "problems, keep every other field unchanged, and return the complete corrected spec as one "
                     "JSON object: no prose, no markdown fences.")
    else:
        lines.append("The Kinetic Studio spec is valid but has warnings. Apply these fixes if they match the "
                     "intent, keep every other field unchanged, and return the complete spec as one JSON object.")
    lines.append("")
    n = 0
    for it in errs + warns:
        n += 1
        label = "ERROR" if it["severity"] == "error" else "warning"
        at = it["path"] or "(spec root)"
        lines.append(f"{n}. [{label}] at {at}: {it['message']}")
        if it.get("hint"):
            lines.append(f"   Fix: {it['hint']}")
    lines += ["", "Rules: times and lengths are seconds or beats (\"4b\", \"1/2b\", \"1bar\"); scene lengths "
              "should be whole beats (2/4/8); only use scene types and fields from the schema."]
    return "\n".join(lines)
