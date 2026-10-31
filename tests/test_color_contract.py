"""Explicit RGB readback contracts across Skia's native pixel layouts."""
import numpy as np
import pytest
import skia

from kstudio.compositor import Compositor
from kstudio.presets import make_output

RGBA = skia.ColorType.kRGBA_8888_ColorType
BGRA = skia.ColorType.kBGRA_8888_ColorType
UNPREMUL = skia.AlphaType.kUnpremul_AlphaType


@pytest.mark.parametrize(
    ("storage", "color", "expected", "opposite"),
    [
        (RGBA, skia.ColorRED, [255, 0, 0, 255], [0, 0, 255, 255]),
        (RGBA, skia.ColorBLUE, [0, 0, 255, 255], [255, 0, 0, 255]),
        (BGRA, skia.ColorRED, [255, 0, 0, 255], [0, 0, 255, 255]),
        (BGRA, skia.ColorBLUE, [0, 0, 255, 255], [255, 0, 0, 255]),
    ],
)
def test_explicit_rgba_readback_is_independent_of_storage_layout(storage, color, expected, opposite):
    pixels = np.zeros((2, 2, 4), dtype=np.uint8)
    info = skia.ImageInfo.Make(2, 2, storage, UNPREMUL)
    surface = skia.Surface.MakeRasterDirect(info, pixels)
    assert surface is not None
    surface.getCanvas().clear(color)

    actual = surface.makeImageSnapshot().toarray(colorType=RGBA)[0, 0].tolist()

    assert actual == expected
    assert actual != opposite  # red and blue must never pass with their channels exchanged


def test_compositor_rgba_surface_writes_red_and_blue_in_rgb_order():
    class Film:
        cfg = {}
        spec = {}
        theme = {}
        duration = 1.0

        @staticmethod
        def draw(canvas, t):
            pass

        @staticmethod
        def shutter_mult(t):
            return 1.0

    out = make_output("square", quality="draft", scale=0.01)
    compositor = Compositor(Film(), out, post={"grain": 0, "vignette": 0}, grade="none")

    compositor.canvas.clear(skia.ColorRED)
    assert compositor.rgba[0, 0].tolist() == [255, 0, 0, 255]
    compositor.canvas.clear(skia.ColorBLUE)
    assert compositor.rgba[0, 0].tolist() == [0, 0, 255, 255]
