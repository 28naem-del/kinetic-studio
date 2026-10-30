import json
import os

import pytest
from conftest import EXAMPLES

from kstudio.spec import load_spec, spec_hash, strip_json_comments


def test_strip_comments_keeps_strings():
    src = '{"url": "https://x.y//z", /* block */ "a": [1, 2,], // tail\n "b": "/* not */",}'
    d = json.loads(strip_json_comments(src))
    assert d == {"url": "https://x.y//z", "a": [1, 2], "b": "/* not */"}


@pytest.mark.parametrize("mode", ["kinetic", "explainer", "promo", "data", "social"])
def test_examples_load_and_build(mode):
    from kstudio.film import Film
    spec = load_spec(os.path.join(EXAMPLES, mode))
    assert spec["mode"] == mode
    f = Film(spec, 540, 960)
    assert 8.0 <= f.duration <= 15.0
    assert f.scenes and f.scenes[0].start == 0


def test_hash_stable_and_sensitive():
    a = {"mode": "kinetic", "scenes": [{"type": "slam", "text": "A"}]}
    b = {"mode": "kinetic", "scenes": [{"type": "slam", "text": "B"}]}
    assert spec_hash(a) == spec_hash(json.loads(json.dumps(a)))
    assert spec_hash(a) != spec_hash(b)


def test_explainer_example_is_safe():
    """The bundled health explainer must stay educational: no dosing, ends on the disclaimer."""
    spec = load_spec(os.path.join(EXAMPLES, "explainer"))
    blob = json.dumps(spec).lower()
    for bad in ("mg", "dose", "dosage", "inject", "cure", "treat", "prevents", "reverses"):
        assert f" {bad} " not in f" {blob} ".replace('"', " ")
    last = spec["scenes"][-1]
    assert last["type"] == "end_card"
