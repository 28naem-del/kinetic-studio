import numpy as np
import pytest

from kstudio.beats import SR, estimate


@pytest.mark.parametrize("bpm", [96.0, 120.0, 128.0])
def test_click_track_tempo(bpm):
    dur = 12.0
    x = np.zeros(int(SR * dur), np.float32)
    rng = np.random.default_rng(0)
    clk = (rng.normal(0, 1, 600) * np.exp(-np.arange(600) / 80)).astype(np.float32)
    t = 0.25
    while t < dur - 0.1:
        i = int(t * SR)
        x[i:i + 600] += clk
        t += 60.0 / bpm
    g = estimate(x)
    assert g.bpm == pytest.approx(bpm, rel=0.03)
    assert g.offset % (60 / bpm) == pytest.approx(0.25 % (60 / bpm), abs=0.04)
