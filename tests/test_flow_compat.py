"""Runtime evidence for OpenCV's allocating calc overload and the typed compatibility shim."""
import cv2
import numpy as np

from kstudio.audio.music import Style
from kstudio.compositor import _calc_flow


def test_allocating_flow_wrapper_is_bit_exact():
    old_threads = cv2.getNumThreads()
    cv2.setNumThreads(1)
    try:
        raw = cv2.DISOpticalFlow.create(cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST)
        wrapped = cv2.DISOpticalFlow.create(cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST)
        rng = np.random.default_rng(3103)
        frame = rng.integers(0, 256, (64, 96), dtype=np.uint8)
        for dy, dx in ((0, 0), (0, 2), (3, 0), (-2, 4), (1, -5), (8, 8)):
            shifted = np.ascontiguousarray(np.roll(frame, (dy, dx), axis=(0, 1)))
            expected = raw.calc(frame, shifted, None)
            actual = _calc_flow(wrapped, frame, shifted)
            assert actual.dtype == np.float32 and actual.shape == (64, 96, 2)
            assert np.isfinite(actual).all()
            np.testing.assert_array_equal(actual, expected)
            assert actual.tobytes() == expected.tobytes()
    finally:
        cv2.setNumThreads(old_threads)


def test_kick_default_is_a_fresh_empty_dictionary():
    first = Style("test", 120, 0, [], {})
    second = Style("test", 120, 0, [], {})
    assert first.kick == second.kick == {}
    assert first.kick is not second.kick
    first.kick["seed"] = 3
    assert second.kick == {}
