import pytest

from kstudio import resources
from kstudio.presets import make_output
from kstudio.render import plan_chunks


def test_presets():
    assert (make_output("reel").width, make_output("reel").height) == (1080, 1920)
    assert (make_output("landscape").width, make_output("landscape").height) == (1920, 1080)
    assert (make_output("square").width, make_output("square").height) == (1080, 1080)
    k = make_output("reel", fps=60, k4=True)
    assert (k.width, k.height, k.fps) == (2160, 3840, 60)
    assert k.level in ("5.2", "6.0", "5.1")
    with pytest.raises(ValueError):
        make_output("nope")
    with pytest.raises(ValueError):
        make_output("reel", fps=31)


def test_auto_workers_bounds(monkeypatch):
    monkeypatch.setattr(resources, "mem_available_mb", lambda: 7000.0)
    monkeypatch.setattr(resources, "cpu_count", lambda: 8)
    n, info = resources.auto_workers(1080, 1920, 3)
    assert 1 <= n <= 7
    assert n * info["per_worker_mb"] <= 7000 - 900 + info["per_worker_mb"]
    monkeypatch.setattr(resources, "mem_available_mb", lambda: 500.0)
    assert resources.auto_workers(1080, 1920, 3)[0] == 1          # always at least one
    monkeypatch.setattr(resources, "mem_available_mb", lambda: 64000.0)
    assert resources.auto_workers(1080, 1920, 3, jobs=3)[0] == 3   # never more than chunks


def test_plan_chunks_cover_exactly():
    ch = plan_chunks(366, 30, 2.0)
    assert ch[0] == (0, 60) and ch[-1] == (360, 366)
    assert sum(b - a for a, b in ch) == 366
    assert all(ch[i][1] == ch[i + 1][0] for i in range(len(ch) - 1))
