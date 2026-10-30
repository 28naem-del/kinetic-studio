import numpy as np

from kstudio.audio import synth
from kstudio.audio.mix import duck_curve, limiter


def test_kit_complete_and_bounded():
    for name in synth.KIT:
        x = synth.get(name)
        assert x.ndim in (1, 2) and len(x) > 100, name
        assert np.isfinite(x).all() and np.abs(x).max() <= 1.0, name


def test_limiter_ceiling():
    rng = np.random.default_rng(1)
    x = (rng.normal(0, 0.6, (48000, 2))).astype(np.float32)
    y = limiter(x, ceiling_db=-1.5)
    assert np.abs(y).max() <= 10 ** (-1.5 / 20) + 1e-3
    assert y.shape == x.shape


def test_duck_curve():
    g = duck_curve(48000, [(0.5, 1.0)], depth_db=-6.0)
    assert g.shape[0] == 48000
    assert g.min() < 0.6 and g[:1000].min() > 0.99
