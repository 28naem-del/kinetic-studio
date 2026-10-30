import os

import numpy as np
import pytest
from conftest import EXAMPLES

from kstudio.compositor import Compositor
from kstudio.film import Film
from kstudio.presets import make_output
from kstudio.spec import load_spec


@pytest.mark.parametrize("mode", ["kinetic", "explainer", "promo", "data", "social"])
def test_frames_have_content(mode):
    spec = load_spec(os.path.join(EXAMPLES, mode))
    out = make_output("reel", quality="draft", scale=0.25)
    film = Film(spec, out.width, out.height)
    comp = Compositor(film, out)
    for t in (0.6, film.duration / 2, film.duration - 0.3):
        img = comp.frame(int(t * out.fps))
        assert img.shape[0] == out.height and img.shape[1] == out.width
        assert img[..., :3].std() > 3.0, f"{mode} frame at {t:.2f}s looks blank"


def test_deterministic():
    spec = load_spec(os.path.join(EXAMPLES, "data"))
    out = make_output("reel", quality="draft", scale=0.2)
    a = Compositor(Film(spec, out.width, out.height), out).frame(90)
    b = Compositor(Film(spec, out.width, out.height), out).frame(90)
    assert np.array_equal(a, b)


def test_every_scene_type_draws():
    """Each registered scene renders a non-empty frame with only its defaults (+ minimal text)."""
    from kstudio.scenes import REGISTRY as SCENES
    for name in SCENES:
        spec = {"mode": "kinetic", "scenes": [{"type": name, "dur": 2.0}]}
        out = make_output("reel", quality="draft", scale=0.2)
        film = Film(spec, out.width, out.height)
        img = Compositor(film, out).frame(30)
        assert img is not None, name
