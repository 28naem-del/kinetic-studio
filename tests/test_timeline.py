import pytest

from kstudio.timeline import TimeExprError, Timeline


def tl():
    t = Timeline(bpm=120.0)  # 0.5 s per beat
    t.duration = 20.0
    t.add("section", 4.0, "drop", 4.0, energy=3)
    t.add("cue", 2.0, "intro-end")
    return t


def test_units():
    t = tl()
    assert t.resolve(1.5) == 1.5
    assert t.resolve("250ms") == pytest.approx(0.25)
    assert t.resolve("2s") == pytest.approx(2.0)
    assert t.resolve("4b") == pytest.approx(2.0)
    assert t.resolve("b4") == pytest.approx(2.0)
    assert t.resolve("bar3") == pytest.approx(4.0)   # bar 3 starts after 8 beats
    assert t.resolve("1bar") == pytest.approx(2.0)


def test_named_and_offsets():
    t = tl()
    assert t.resolve("drop") == pytest.approx(4.0)
    assert t.resolve("drop+0.6") == pytest.approx(4.6)
    assert t.resolve("drop-2b") == pytest.approx(3.0)
    assert t.resolve("intro-end+1b") == pytest.approx(2.5)   # hyphenated names resolve as names
    assert t.resolve("end") == pytest.approx(20.0)
    with pytest.raises(TimeExprError):
        t.resolve("nope+1")


def test_durations_beat_aware():
    t = tl()
    assert t.resolve_dur("2b", at=1.0) == pytest.approx(1.0)
    assert t.resolve_dur("1bar") == pytest.approx(2.0)
    assert t.resolve_dur(0.75) == 0.75


def test_beat_time_roundtrip_with_grid():
    t = Timeline(bpm=100.0, beat_times=[0.1, 0.7, 1.32, 1.9, 2.5])
    for b in (0, 1.5, 3, 7.25):
        assert t.time_to_beat(t.beat_to_time(b)) == pytest.approx(b, abs=1e-6)


def test_energy_and_hits():
    t = tl()
    assert t.energy_at(5.0) == 3
    t.add("hit", 6.0)
    assert t.hit_env(6.0) == pytest.approx(1.0, abs=1e-6)
    assert t.hit_env(6.5) < 0.1
    assert t.hit_env(5.9) == 0.0
