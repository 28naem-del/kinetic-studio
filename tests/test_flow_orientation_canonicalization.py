"""R4 guards: geometry-only orientation canonicalization, independent seeds and valid boundaries."""
from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from kstudio.compositor import _calc_flow

CASES = [
    ((24, 14), (2, 0), 7, "contiguous"),
    ((24, 14), (-2, 0), 19, "strided"),
    ((24, 14), (0, 2), 41, "contiguous"),
    ((24, 14), (0, -2), 73, "strided"),
    ((31, 14), (2, 0), 101, "strided"),
    ((14, 24), (-2, 0), 131, "contiguous"),
    ((14, 31), (0, 2), 151, "strided"),
    ((14, 24), (0, -2), 181, "contiguous"),
]


def _engine():
    return cv2.DISOpticalFlow.create(cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST)


def _settings(engine):
    return (
        engine.getFinestScale(),
        engine.getCoarsestScale(),
        engine.getPatchSize(),
        engine.getPatchStride(),
        engine.getUseSpatialPropagation(),
        engine.getUseMeanNormalization(),
        engine.getGradientDescentIterations(),
        engine.getVariationalRefinementIterations(),
    )


def _world_pair(shape, shift, seed, layout="contiguous"):
    height, width = shape
    dx, dy = shift
    margin = max(abs(dx), abs(dy)) + 4
    world = np.random.default_rng(seed).integers(
        0, 256, (height + 2 * margin, width + 2 * margin), dtype=np.uint8
    )
    first = world[margin:margin + height, margin:margin + width].copy()
    second = world[
        margin - dy:margin - dy + height,
        margin - dx:margin - dx + width,
    ].copy()
    if layout == "strided":
        first_storage = np.empty((height, width * 2), np.uint8)
        second_storage = np.empty((height, width * 2), np.uint8)
        first_storage[:, ::2] = first
        second_storage[:, ::2] = second
        first, second = first_storage[:, ::2], second_storage[:, ::2]
        assert not first.flags.c_contiguous and not second.flags.c_contiguous
    return first, second


def _r3_tiny_flow(engine, first, second):
    """Frozen R3 landscape/square path used only as an exact non-regression oracle."""
    height, width = first.shape
    patch, finest = engine.getPatchSize(), engine.getFinestScale()
    factor = 1 << finest
    while True:
        enlarged_short = min(height, width) * factor
        enlarged_long = max(height, width) * factor
        long_scale = int(math.log(enlarged_long / (4.0 * patch)) / math.log(2.0) + 0.5)
        short_scale = int(math.log(enlarged_short / patch) / math.log(2.0))
        if min(long_scale, short_scale) >= finest:
            break
        factor += 1
    size = (width * factor, height * factor)
    enlarged_first = cv2.resize(first, size, interpolation=cv2.INTER_LINEAR)
    enlarged_second = cv2.resize(second, size, interpolation=cv2.INTER_LINEAR)
    flow = engine.calc(enlarged_first, enlarged_second, None)
    restored = cv2.resize(flow, (width, height), interpolation=cv2.INTER_LINEAR)
    return restored / np.float32(factor)


@pytest.mark.parametrize("shape,shift,seed,layout", CASES)
def test_r4_independent_boundary_valid_signed_motion(shape, shift, seed, layout):
    first, second = _world_pair(shape, shift, seed, layout)
    before = first.copy(), second.copy()
    engine = _engine()
    settings = _settings(engine)

    flow = _calc_flow(engine, first, second)

    assert flow.shape == (*shape, 2)
    assert flow.dtype == np.float32 and flow.flags.c_contiguous
    assert np.isfinite(flow).all()
    observed = np.median(flow[3:-3, 3:-3], axis=(0, 1))
    np.testing.assert_allclose(observed, shift, atol=0.6, rtol=0)
    assert _settings(engine) == settings
    np.testing.assert_array_equal(first, before[0])
    np.testing.assert_array_equal(second, before[1])


def test_r4_portrait_is_exact_inverse_of_same_landscape_problem():
    first, second = _world_pair((24, 14), (0, -2), 227)
    portrait = _calc_flow(_engine(), first, second)
    landscape = _calc_flow(
        _engine(), np.ascontiguousarray(first.T), np.ascontiguousarray(second.T)
    )
    landscape_back = np.ascontiguousarray(landscape.transpose(1, 0, 2)[..., [1, 0]])
    np.testing.assert_array_equal(portrait, landscape_back)


@pytest.mark.parametrize("shape,shift,seed", [
    ((14, 24), (2, 0), 251),
    ((14, 14), (0, -2), 277),
])
def test_r4_landscape_and_square_are_byte_identical_to_r3(shape, shift, seed):
    first, second = _world_pair(shape, shift, seed, "strided")
    expected = _r3_tiny_flow(_engine(), first, second)
    actual = _calc_flow(_engine(), first, second)
    np.testing.assert_array_equal(actual, expected)


def test_r4_engine_reuse_preserves_tiny_landscape_and_normal_results():
    portrait = _world_pair((24, 14), (0, 2), 307)
    landscape = _world_pair((14, 24), (-2, 0), 331)
    normal = _world_pair((135, 240), (2, 0), 353)
    engine = _engine()
    settings = _settings(engine)

    _calc_flow(engine, *portrait)
    reused_landscape = _calc_flow(engine, *landscape)
    reused_normal = _calc_flow(engine, *normal)

    expected_landscape = _calc_flow(_engine(), *landscape)
    expected_normal = _engine().calc(*normal, None)
    np.testing.assert_array_equal(reused_landscape, expected_landscape)
    np.testing.assert_array_equal(reused_normal, expected_normal)
    assert _settings(engine) == settings
