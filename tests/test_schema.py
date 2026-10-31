"""Spec JSON Schema generated from the engine, the validator and the brief prompt pack."""
import json
import os

import pytest
from conftest import EXAMPLES

from kstudio import brief, schema
from kstudio.modes import MODES as MODE_TABLE
from kstudio.scenes import REGISTRY
from kstudio.spec import load_spec

MODES = sorted(d for d in os.listdir(EXAMPLES) if os.path.isdir(os.path.join(EXAMPLES, d)))
# examples named after a mode; the others (examples/clip) show one scene type inside some mode
MODE_EXAMPLES = [m for m in MODES if m in MODE_TABLE]


def test_schema_lists_every_registered_scene_type():
    s = schema.spec_schema()
    assert s["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert s["x-scene-types"] == sorted(REGISTRY)
    for name in REGISTRY:
        d = s["$defs"][f"scene.{name}"]
        assert d["properties"]["type"]["const"] == name
    refs = {x["$ref"] for x in s["properties"]["scenes"]["items"]["oneOf"]}
    assert refs == {f"#/$defs/scene.{n}" for n in REGISTRY}
    json.loads(json.dumps(s))  # plain JSON, no Python-only values


def test_schema_is_derived_from_scene_code():
    s = schema.spec_schema()
    slam = s["$defs"]["scene.slam"]["properties"]
    assert "words" in slam  # read through a module helper, not self.p directly
    assert "transition" in s["$defs"]["scene.title"]["properties"]  # shared Scene keys
    keys, _poly = schema.scene_param_reads(REGISTRY["bars"])
    assert keys and all(isinstance(k, str) for k in keys)


def test_schema_subset():
    s = schema.spec_schema(["title", "cta"])
    assert s["x-scene-types"] == ["cta", "title"]
    assert "scene.bars" not in s["$defs"]


def test_schema_passes_metaschema_when_jsonschema_installed():
    js = pytest.importorskip("jsonschema")
    s = schema.spec_schema()
    js.Draft202012Validator.check_schema(s)
    v = js.Draft202012Validator(s)
    for m in MODES:
        spec = {k: x for k, x in load_spec(os.path.join(EXAMPLES, m)).items() if not k.startswith("_")}
        assert not list(v.iter_errors(spec)), m


@pytest.mark.parametrize("mode", MODES)
def test_examples_validate(mode):
    r = schema.validate_spec(load_spec(os.path.join(EXAMPLES, mode)))
    assert r["valid"], r["errors"]
    assert r["summary"]["scenes"] > 0 and r["summary"]["duration"] > 0


def _issues(spec, **kw):
    return schema.validate_spec(spec, **kw)


def test_errors_carry_json_pointer_paths():
    r = _issues({"scenes": [{"type": "title", "title": "hi"}, {"type": "titel"}]})
    assert not r["valid"]
    e = r["errors"][0]
    assert e["path"] == "/scenes/1/type" and "title" in e["hint"]
    r = _issues({"scenes": 3})
    assert r["errors"][0]["path"] == "/scenes"
    r = _issues({"mode": "kinetik", "scenes": [{"type": "title", "title": "x"}]})
    assert any(x["path"] == "/mode" and "kinetic" in x["hint"] for x in r["errors"])


def test_dry_build_errors_point_at_the_scene():
    r = _issues({"scenes": [{"type": "title", "title": "a", "dur": "4b"},
                            {"type": "title", "title": "b", "dur": "nowhere+zz"}]})
    assert not r["valid"]
    assert r["errors"][0]["path"] == "/scenes/1/dur"


def test_unknown_keys_warn_unless_strict():
    spec = {"scenes": [{"type": "title", "title": "hi", "dur": "4b", "colour": "red"}]}
    r = _issues(spec)
    assert r["valid"] and any(w["path"] == "/scenes/0/colour" for w in r["warnings"])
    r = _issues(spec, strict=True)
    assert not r["valid"] and r["errors"][0]["path"] == "/scenes/0/colour"


def test_fix_prompt_is_actionable():
    r = _issues({"scenes": [{"type": "titel", "title": "hi"}]})
    p = schema.fix_prompt(r)
    assert "/scenes/0/type" in p and "JSON object" in p


@pytest.mark.parametrize("mode", MODE_EXAMPLES)
def test_brief_pack_embeds_schema_examples_and_rules(mode):
    pack = brief.prompt_pack("A 15 second teaser for a note-taking app", mode, duration=15)
    assert pack["schema"]["$schema"].endswith("2020-12/schema")
    assert len(pack["examples"]) == 2 and pack["examples"][0]["mode"] == mode
    assert '"$defs"' in pack["system"] and "Example spec" in pack["system"]
    rules = pack["rules"]["mode"]
    assert rules["bpm"] > 0 and rules["target"]["beats"] % 4 == 0
    assert "beats" in pack["user"]
    for e in pack["examples"]:
        assert schema.validate_spec(dict(e["spec"]))["valid"]
    assert set(pack["scene_types"]) >= {s["type"] for s in pack["examples"][0]["spec"]["scenes"]}


def test_brief_rejects_unknown_mode():
    with pytest.raises(KeyError):
        brief.prompt_pack("x", "nope")
