"""The clip scene: footage under titles, nearest-frame mapping, the decode cache, fallbacks and the example."""
import json
import os
import shutil
import subprocess

import numpy as np
import pytest
from conftest import EXAMPLES

from kstudio.compositor import Compositor
from kstudio.film import Film
from kstudio.presets import make_output
from kstudio.scenes import REGISTRY
from kstudio.scenes import clip as C
from kstudio.spec import SpecError, load_spec

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("KSTUDIO_CLIP_CACHE", str(tmp_path / "clip-cache"))


def _video(path, colors, secs=1.0, rate=10, size="96x160"):
    """A test video: one solid colour per `secs` segment, at `rate` fps."""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for c in colors:
        cmd += ["-f", "lavfi", "-i", f"color=c={c}:s={size}:d={secs}:r={rate}"]
    n = len(colors)
    cmd += ["-filter_complex", "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", str(path)]
    subprocess.run(cmd, check=True)
    return str(path)


def _frame(spec, t, scale=0.2):
    out = make_output("reel", quality="draft", scale=scale)
    film = Film(spec, out.width, out.height)
    return Compositor(film, out).frame(int(round(t * out.fps)))[..., :3].astype(int)


def _share(img, pred):
    return float(pred(img[..., 0], img[..., 1], img[..., 2]).mean())


def _green(r, g, b):
    return (g > r + 40) & (g > b + 40)


def test_clip_is_registered_with_beat_trim_params():
    cls = REGISTRY["clip"]
    assert {"in", "out"} <= set(cls.time_params)
    assert {"src", "fit", "grade", "blur", "opacity", "over", "source"} <= set(cls.defaults)


@pytest.mark.parametrize("src_t,want", [(0.0, 0), (0.049, 0), (0.051, 1), (1.0, 10), (9.0, 19), (-1.0, 0)])
def test_nearest_frame_index(src_t, want):
    assert C.frame_index(src_t, 0.0, 10.0, 20) == want


def test_hold_and_loop_source_time():
    tl_spec = {"mode": "kinetic", "music": {"bpm": 120}, "scenes": [
        {"type": "clip", "src": "x.mp4", "dur": 4.0},
        {"type": "clip", "src": "x.mp4", "dur": 4.0, "end": "loop", "speed": 2.0}]}
    film = Film(tl_spec, 108, 192)
    hold, loop = film.scenes
    assert hold.src_time(3.5, 1.0, 2.0) == pytest.approx(2.0)
    assert loop.src_time(0.75, 1.0, 2.0) == pytest.approx(1.5)  # 1.5 s of source wraps to 0.5 s past `in`


def test_beat_trim_resolves_to_seconds():
    spec = {"mode": "kinetic", "music": {"bpm": 120}, "scenes": [
        {"type": "clip", "src": "x.mp4", "in": "2b", "out": "6b", "dur": "4b"}]}
    sc = Film(spec, 108, 192).scenes[0]
    assert sc.p["in"] == pytest.approx(1.0) and sc.p["out"] == pytest.approx(3.0)


@pytest.mark.parametrize("bad", [{"fit": "stretch"}, {"end": "bounce"}, {"over": [{"title": "no type"}]},
                                 {"over": [{"type": "clip"}]}, {"source": {"swap": {"video": "a.mp4"}}}])
def test_bad_params_are_spec_errors(bad):
    spec = {"mode": "kinetic", "scenes": [{"type": "clip", "src": "x.mp4", **bad}]}
    with pytest.raises(SpecError):
        Film(spec, 108, 192)


@needs_ffmpeg
def test_footage_shows_under_the_title(tmp_path):
    _video(tmp_path / "green.mp4", ["0x20c040"], secs=2.0)
    title = {"type": "title", "kicker": "Footage", "title": "Under the title"}
    with_clip = {"mode": "kinetic", "_dir": str(tmp_path), "scenes": [
        {"type": "clip", "src": "green.mp4", "dur": 2.0, "scrim": 0.3, "camera": False, "over": [title]}]}
    plain = {"mode": "kinetic", "_dir": str(tmp_path), "scenes": [{**title, "dur": 2.0, "camera": False}]}
    a, b = _frame(with_clip, 1.5), _frame(plain, 1.5)
    h = a.shape[0]
    band = slice(int(h * 0.2), int(h * 0.45))  # the title block on the top anchor
    luma = lambda im: 0.2126 * im[..., 0] + 0.7152 * im[..., 1] + 0.0722 * im[..., 2]  # noqa: E731
    assert _share(a[band], _green) > 0.5, "footage should fill the title band around the letters"
    assert _share(b[band], _green) < 0.01
    ink = luma(a[band]) > 170
    assert ink.mean() > 0.01, "title letters should be drawn over the footage"
    # the letters sit where the plain title has them
    assert (ink & (luma(b[band]) > 170)).sum() > 0.6 * ink.sum()


@needs_ffmpeg
def test_nearest_frame_mapping_across_frame_rates(tmp_path):
    _video(tmp_path / "rb.mp4", ["red", "blue"], secs=1.0, rate=10)  # 10 fps source in a 30 fps film
    spec = {"mode": "kinetic", "_dir": str(tmp_path), "scenes": [
        {"type": "clip", "src": "rb.mp4", "dur": 3.0, "scrim": 0, "zoom": 1.0, "camera": False}]}
    red = lambda r, g, b: (r > g + 60) & (r > b + 60)  # noqa: E731
    blue = lambda r, g, b: (b > r + 60) & (b > g + 30)  # noqa: E731
    assert _share(_frame(spec, 0.5), red) > 0.8
    assert _share(_frame(spec, 1.5), blue) > 0.8
    assert _share(_frame(spec, 2.7), blue) > 0.8  # past the end: the last frame holds
    spec["scenes"][0]["in"] = 1.0  # trimmed: blue from the first frame
    assert _share(_frame(spec, 0.2), blue) > 0.8


@needs_ffmpeg
def test_decode_is_cached_and_keyed(tmp_path, monkeypatch):
    src = _video(tmp_path / "g.mp4", ["0x20c040"], secs=1.0)
    plan = C.DecodePlan(src, 0.0, 1.0, 10.0, (0, 0, 96, 160), (48, 80), "null", 0.0)
    a = C.decode(plan)
    assert a.shape == (10, 120, 48) and a.dtype == np.uint8
    rgba = C.to_rgba(a[3])
    assert rgba.shape == (80, 48, 4) and rgba[..., 1].mean() > rgba[..., 0].mean() + 60
    files = sorted(os.listdir(os.environ["KSTUDIO_CLIP_CACHE"]))
    assert any(f.endswith(".yuv") for f in files) and any(f.endswith(".json") for f in files)

    def boom(_plan):
        raise AssertionError("decoded again")
        yield  # pragma: no cover

    monkeypatch.setattr(C, "_ffmpeg_frames", boom)
    assert np.array_equal(C.decode(plan), a)  # cache hit: ffmpeg is not run
    other = C.DecodePlan(src, 0.0, 1.0, 10.0, (0, 0, 96, 160), (24, 40), "null", 0.0)
    assert other.key() != plan.key()  # a new size is a new entry
    with pytest.raises(AssertionError):
        C.decode(other)


@needs_ffmpeg
def test_grade_blur_opacity_and_contain_box(tmp_path):
    _video(tmp_path / "g.mp4", ["0x20c040"], secs=1.0, size="160x96")  # landscape into a portrait frame
    spec = {"mode": "kinetic", "_dir": str(tmp_path), "scenes": [
        {"type": "clip", "src": "g.mp4", "dur": 1.0, "fit": "contain", "box": [0.1, 0.1, 0.8, 0.4],
         "grade": {"saturation": 0.2}, "blur": 6, "opacity": 0.8, "scrim": 0, "camera": False}]}
    img = _frame(spec, 0.5)
    h, w = img.shape[:2]
    inside, outside = img[int(h * 0.25), w // 2], img[int(h * 0.8), w // 2]
    assert inside[1] > inside[0] + 10, "desaturated footage is still greenish inside the box"
    assert not _green(*outside[:, None]).any(), "nothing is drawn outside the box"


def test_missing_footage_draws_a_note(tmp_path, capsys):
    spec = {"mode": "kinetic", "_dir": str(tmp_path), "scenes": [
        {"type": "clip", "src": "nope.mp4", "dur": 1.0, "over": [{"type": "title", "title": "Still here"}]}]}
    img = _frame(spec, 0.5)
    assert img.std() > 3.0


@needs_ffmpeg
def test_example_validates_and_renders_draft(tmp_path):
    from kstudio import schema
    from kstudio.render import render_video
    spec = load_spec(os.path.join(EXAMPLES, "clip"))
    r = schema.validate_spec(spec)
    assert r["valid"], r["errors"]
    size = os.path.getsize(os.path.join(EXAMPLES, "clip", "footage.mp4"))
    assert size < 300_000
    out = make_output("reel", 30, "draft", scale=0.2)
    film = Film(spec, out.width, out.height)
    n = int(round(film.duration * out.fps))
    path = str(tmp_path / "clip.mp4")
    render_video(spec, out, path, str(tmp_path / "work"), film.fingerprint(), n, workers=1)
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                                      "-show_entries", "stream=nb_read_frames,width,height", "-of", "json", path],
                                     capture_output=True, text=True, check=True).stdout)["streams"][0]
    assert int(info["nb_read_frames"]) == n and (info["width"], info["height"]) == (out.width, out.height)
