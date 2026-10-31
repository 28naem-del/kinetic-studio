"""Real DIS regressions: narrow-domain safety, translation and unchanged large inputs."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from kstudio.compositor import _calc_flow


def _engine():
    return cv2.DISOpticalFlow.create(cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST)


def _pair(height, width, dx, dy, layout):
    first = np.random.default_rng(21).integers(0, 256, (height, width), dtype=np.uint8)
    second = np.roll(first, (dy, dx), axis=(0, 1))
    if layout == "strided":
        a = np.zeros((height, width * 2), np.uint8)
        b = a.copy()
        a[:, ::2], b[:, ::2] = first, second
        first, second = a[:, ::2], b[:, ::2]
    return first, second


@pytest.mark.parametrize("shape", [(14, 60), (60, 14), (14, 24), (24, 14), (31, 60), (60, 31),
                                    (14, 240), (240, 14)])
@pytest.mark.parametrize("shift", [(2, 0), (-2, 0), (0, 2), (0, -2)])
@pytest.mark.parametrize("layout", ["contiguous", "strided"])
def test_small_flow_translation(shape, shift, layout):
    first, second = _pair(*shape, *shift, layout)
    before = first.copy(), second.copy()
    engine = _engine()
    settings = engine.getFinestScale(), engine.getPatchSize(), engine.getPatchStride()
    flow = _calc_flow(engine, first, second)
    assert flow.shape == (*shape, 2)
    assert flow.dtype == np.float32 and np.isfinite(flow).all()
    # Exclude wrapping boundaries. Finiteness alone would accept the observed
    # wrong-sign result on 60x14; direction AND displacement must be correct.
    middle = flow[3:-3, 3:-3]
    observed = np.median(middle, axis=(0, 1))
    np.testing.assert_allclose(observed, shift, atol=0.6, rtol=0)
    assert settings == (engine.getFinestScale(), engine.getPatchSize(), engine.getPatchStride())
    np.testing.assert_array_equal(first, before[0])
    np.testing.assert_array_equal(second, before[1])


@pytest.mark.parametrize("shape", [(14, 60), (60, 14), (8, 8), (1, 24), (24, 1)])
def test_small_identical_frames_have_zero_flow(shape):
    first, _ = _pair(*shape, 0, 0, "contiguous")
    flow = _calc_flow(_engine(), first, first)
    assert flow.shape == (*shape, 2) and np.isfinite(flow).all()
    np.testing.assert_allclose(flow, 0, atol=1e-5, rtol=0)


@pytest.mark.parametrize("shape", [(32, 60), (60, 32), (135, 240), (240, 135)])
def test_normal_flow_is_byte_identical_to_default_dis(shape):
    first, second = _pair(*shape, 2, 0, "contiguous")
    expected = _engine().calc(first, second, None)
    actual = _calc_flow(_engine(), first, second)
    assert np.isfinite(actual).all()
    np.testing.assert_array_equal(actual, expected)


def test_engine_reuse_does_not_leave_small_image_settings():
    engine = _engine()
    small = _pair(14, 60, 2, 0, "contiguous")
    large = _pair(135, 240, 2, 0, "contiguous")
    _calc_flow(engine, *small)
    actual = _calc_flow(engine, *large)
    expected = _engine().calc(*large, None)
    np.testing.assert_array_equal(actual, expected)


def test_invalid_pair_is_not_silently_replaced_with_zero_flow():
    with pytest.raises(cv2.error):
        _calc_flow(_engine(), np.zeros((14, 60), np.uint8), np.zeros((13, 60), np.uint8))
