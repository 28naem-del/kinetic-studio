"""Engine round 3: no shutter ghost across hard cuts, slice/whip streaks held to an accent, a flash
frame never needs flow, concat timestamps on the frame grid, risers ending on the cut, drums on
the straight 16th grid and an ink-theme CTA label with contrast headroom."""
from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
import pytest

from kstudio import draw as D
from kstudio import render as RD
from kstudio import transitions as TR
from kstudio.audio import instruments as I
from kstudio.audio import music as M
from kstudio.audio import synth
from kstudio.audio.dsp import SR
from kstudio.audio.mix import place_sfx
from kstudio.compositor import Compositor
from kstudio.film import Film
from kstudio.modes import MODES, THEMES
from kstudio.presets import make_output
from kstudio.scenes.common import RING_LABEL_HEADROOM, contrast_ratio, cta_colors
from kstudio.spec import load_spec
from kstudio.timeline import Timeline

ROOT = Path(__file__).resolve().parent.parent
HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _film(name: str, scale: float = 0.25):
    spec = dict(load_spec(str(ROOT / "examples" / name)))
    out = make_output("reel", 30, "standard", scale=scale)
    spec["_fps"] = out.fps
    film = Film(spec, out.width, out.height)
    return film, Compositor(film, out)


# ---------------------------------------------------------------- shutter vs hard cuts
@pytest.mark.parametrize("name,frames", [("explainer", (342, 343)), ("kinetic", (224, 225, 337, 338))])
def test_shutter_never_integrates_across_a_hard_cut(name, frames):
    film, comp = _film(name)
    hard = [b.t for b in film.boundaries if b.dur <= 0]
    assert hard
    seen: list[float] = []
    orig = comp._sample

    def spy(t):
        seen.append(t)
        return orig(t)

    comp._sample = spy
    for i in frames:
        seen.clear()
        comp.frame(i)
        lo, hi = film.shot_bounds(i / film.fps)
        assert seen and all(lo - 1e-9 <= t < hi for t in seen), (i, lo, hi, min(seen), max(seen))


def test_shot_bounds_split_at_hard_cuts_only():
    film, _ = _film("kinetic")
    hard = sorted(b.t for b in film.boundaries if b.dur <= 0)
    c = hard[0]
    assert film.shot_bounds(c)[0] == c                 # on the cut: the incoming shot
    assert film.shot_bounds(c - 1e-3)[1] == c
    soft = [b for b in film.boundaries if b.dur > 0]
    for b in soft:                                     # a soft transition is one continuous shot
        lo, hi = film.shot_bounds(b.t)
        assert lo < b.t0 and hi > b.t1


# ---------------------------------------------------------------- streak accents
_SLICE = {"mode": "kinetic", "music": {"style": "drive", "bpm": 120}, "_fps": 30,
          "scenes": [{"type": "slam", "words": ["ONE"], "dur": "4b"},
                     {"type": "slam", "words": ["TWO"], "dur": "4b", "transition": {"kind": "slice", "dur": "1b"}},
                     {"type": "slam", "words": ["THREE"], "dur": "4b", "transition": "slice"}]}


@pytest.mark.parametrize("name", ["synthetic", "promo"])
def test_slice_window_is_at_most_a_quarter_beat_and_three_frames(name):
    film = Film(_SLICE, 108, 192) if name == "synthetic" else _film(name)[0]
    sl = [b for b in film.boundaries if b.kind == "slice"]
    assert sl
    for b in sl:
        quarter = film.tl.beat_to_time(film.tl.time_to_beat(b.t) + 0.25) - b.t
        assert b.dur <= min(quarter, 3.0 / film.fps) + 1e-6
        assert b.beats <= 0.25 + 1e-9


def test_whip_speed_lines_span_at_most_three_frames():
    film, _ = _film("kinetic")
    whips = [b for b in film.boundaries if b.kind.startswith("whip")]
    assert whips
    for b in whips:
        half = TR.streak_half(film, b.t)
        assert 0 < 2 * half <= TR.STREAK_FRAMES / film.fps + 1e-9


# ---------------------------------------------------------------- flash frames
def test_flash_frames_skip_flow_and_leave_nothing_unresolved():
    film, comp = _film("kinetic", 0.5)
    flashes = [b.t for b in film.boundaries if b.kind == "flash"]
    assert flashes
    for cut in flashes:
        i0 = TR.flash_first_frame(cut, film.fps)
        for i in (i0 - 1, i0, i0 + 1, i0 + 2):
            comp.frame(i)
    assert comp.stats["unresolved"] == 0
    assert comp.stats["flash_frames"] >= len(flashes)


# ---------------------------------------------------------------- concat timing
def test_concat_list_carries_exact_chunk_durations(tmp_path):
    chunks = RD.plan_chunks(63, 30)
    assert chunks[-1] == (60, 63)                      # a 3-frame tail keeps its own chunk
    lst = tmp_path / "concat.txt"
    RD.write_concat_list(str(lst), str(tmp_path), chunks, 30)
    lines = lst.read_text().splitlines()
    durs = [float(x.split()[1]) for x in lines if x.startswith("duration ")]
    assert len(durs) == len(chunks) and lines[0].startswith("file '")
    assert abs(sum(durs) - 63 / 30) < 1e-5 and abs(durs[-1] - 3 / 30) < 1e-6


@pytest.mark.parametrize("n", [61, 62, 121])
def test_one_or_two_frame_tails_join_the_previous_chunk(n):
    ch = RD.plan_chunks(n, 30)
    assert ch[-1][1] == n and all(b - a >= RD.MIN_CHUNK_FRAMES for a, b in ch)
    assert sum(b - a for a, b in ch) == n and all(ch[i][1] == ch[i + 1][0] for i in range(len(ch) - 1))


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_concatenated_render_keeps_the_frame_rate(tmp_path):
    spec = {"mode": "kinetic", "_fps": 30,
            "scenes": [{"type": "slam", "words": ["ONE", "TWO"], "dur": 1.2},
                       {"type": "slam", "words": ["THREE"], "dur": 0.9}]}
    out = make_output("reel", 30, "draft", scale=0.1)
    film = Film(spec, out.width, out.height)
    n = 61                                             # 0.5 s chunks: 15+15+15+16 (the 1-frame tail joins)
    path = str(tmp_path / "v.mp4")
    RD.render_video(spec, out, path, str(tmp_path / "work"), film.fingerprint(), n, workers=1, chunk_seconds=0.5)
    vt = RD.video_timing(path)
    assert vt["frames"] == n and vt["avg_frame_rate"] == "30/1"
    assert abs(vt["video_duration"] - n / 30) < 1e-3


# ---------------------------------------------------------------- risers
class _B:
    def __init__(self, t: float):
        self.t, self.dur = t, 0.0


def _sfx_film(boundaries, *sfx):
    tl = Timeline(bpm=120.0, duration=8.0)
    tl.add("section", 0.0, "a", 8.0, energy=2)
    for t, data in sfx:
        tl.add("sfx", t, "", 0.0, **data)

    class F:
        cfg: dict = {}
    F.tl = tl
    F.boundaries = [_B(t) for t in boundaries]
    return F()


def test_risers_without_align_end_on_the_next_cut():
    ln = len(synth.render("riser_short", 1.0, 0.0, 0)) / SR
    f = _sfx_film([3.0, 6.0], (2.5, {"sound": "riser_short"}),          # "cut-2b" style marker
                  (6.0, {"sound": "riser_short"}),                       # an end card's own scene start
                  (4.5, {"sound": "riser_short", "align": "start"}),     # explicit align wins
                  (0.5, {"sound": "riser_short"}))                       # no cut in reach: ends on the anchor
    got = sorted((round(p["t"], 3), round(p["start"] + ln, 4), p["align"]) for p in place_sfx(f))
    assert got == [(0.5, 0.5, "end"), (2.5, 3.0, "end"), (4.5, round(4.5 + ln, 4), "start"), (6.0, 6.0, "end")]


@pytest.mark.parametrize("name", ["kinetic", "promo", "social"])
def test_example_risers_end_within_10ms_of_a_cut(name):
    film, _ = _film(name)
    cuts = [b.t for b in film.boundaries]
    rise = [p for p in place_sfx(film) if p["sound"] in ("riser", "riser_short", "reverse")]
    assert rise
    for p in rise:
        end = p["start"] + len(p["_clip"]) / SR
        assert min(abs(end - c) for c in cuts) <= 0.010, (p["t"], end)
    for ev in M.plan(film.cfg["music"]["style"], film.tl, film.duration)["events"]:
        if ev["kind"] == "riser":
            assert min(abs(ev["end"] - c) for c in cuts) <= 0.010


# ---------------------------------------------------------------- drums on the grid
@pytest.mark.parametrize("style", sorted(M.STYLES))
def test_every_drum_hit_is_on_the_straight_16th_grid(style, monkeypatch):
    st = M.STYLES[style]
    tl = Timeline(bpm=st.bpm, duration=8.0)
    tl.add("section", 0.0, "a", 4.0, energy=1)
    tl.add("section", 4.0, "b", 4.0, energy=3)
    hits: list[float] = []
    orig = M._Bus.add

    def spy(self, stem, clip, t, gain=1.0, pan=None):
        if stem in ("kick", "clap", "hats", "perc"):
            hits.append(float(t))
        return orig(self, stem, clip, t, gain, pan)

    monkeypatch.setattr(M._Bus, "add", spy)
    M.generate(style, tl, 8.0)
    assert len(hits) > 20
    q = [tl.time_to_beat(t) * 4 for t in hits]
    assert max(abs(x - round(x)) for x in q) < 1e-6


def test_lofi_kicks_sit_on_quarters_or_eighths():
    pat = M.STYLES["lofi"].drums["bbkick"][1]
    assert all(pat[k] == "." for k in range(16) if k % 2)
    assert M.STYLES["lofi"].bass_pat[1] == pat


@pytest.mark.parametrize("clip", [I.kick(), I.tom()])
def test_drum_voices_decay_to_silence_instead_of_a_cut(clip):
    tail = np.abs(np.asarray(clip, np.float64))
    assert tail[-8:].max() < 1e-3 * tail.max()


# ---------------------------------------------------------------- CTA label
@pytest.mark.parametrize("theme", sorted(THEMES))
def test_cta_label_has_render_headroom(theme):
    pal = D.Palette(THEMES[theme])
    blooms = {float(m["post"].get("bloom", 0.0)) for m in MODES.values()} | {0.0}
    for bloom in blooms:
        fill, label, ring = cta_colors(pal, "bg", bloom)
        cr = contrast_ratio(label, fill)
        if ring and label == pal.rgb("accent"):
            assert cr >= 4.5 * RING_LABEL_HEADROOM - 1e-9
        assert cr >= 4.5
        assert math.isfinite(cr)
