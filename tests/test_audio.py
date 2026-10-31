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


# ---------------------------------------------------------------- v0.2: music, mastering, delivery
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

import pytest  # noqa: E402

from kstudio.audio import dsp as A  # noqa: E402
from kstudio.audio import music as M  # noqa: E402

SR = A.SR
HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


def _film(mode="kinetic", style="drive"):
    from kstudio.film import Film
    from kstudio.spec import load_spec
    sp = load_spec(os.path.join(EXAMPLES, mode, "spec.json"))
    sp.setdefault("music", {})["style"] = style
    return Film(sp, 270, 480)


def _corr(x):
    l, r = x[:, 0].astype(np.float64), x[:, 1].astype(np.float64)
    return float((l * r).sum() / np.sqrt((l * l).sum() * (r * r).sum()))


def _bands(x):
    m = x.mean(1).astype(np.float64)
    p = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    c = np.cumsum(p)
    roll = float(f[np.searchsorted(c, 0.95 * c[-1])])
    lows = p[(f >= 60) & (f < 250)].sum()
    mids = p[(f >= 1000) & (f < 4000)].sum()
    return roll, 10 * np.log10(mids / lows)


@pytest.fixture(scope="module")
def drive_bed():
    film = _film()
    M.DEBUG_STEMS = {}
    try:
        x = M.generate("drive", film.tl, film.duration, seed=0)
        stems = dict(M.DEBUG_STEMS)
    finally:
        M.DEBUG_STEMS = None
    return film, x, stems


def test_bed_stereo_correlation_in_range(drive_bed):
    _, x, _ = drive_bed
    assert x.ndim == 2 and np.isfinite(x).all()
    assert 0.55 <= _corr(x) <= 0.80


def test_bed_spectral_targets(drive_bed):
    _, x, _ = drive_bed
    roll, mid_minus_low = _bands(x)
    assert roll >= 4000.0
    assert mid_minus_low >= -10.0


def test_fills_and_risers_end_on_cuts(drive_bed):
    film, _, stems = drive_bed
    p = M.plan("drive", film.tl, film.duration)
    cuts = p["cuts"]
    fills = [e for e in p["events"] if e["kind"] == "fill"]
    assert fills and all(min(abs(e["end"] - c) for c in cuts) < 1e-9 for e in fills)
    assert all(min(abs(e["end"] - c) for c in cuts) < 1e-9 for e in p["events"] if e["kind"] == "riser")
    fill = stems["d_fill"]
    for e in fills:
        k = int(round(e["end"] * SR))
        nxt = [f["start"] for f in fills if f["start"] >= e["end"] - 1e-9]
        stop = int(round(nxt[0] * SR)) if nxt else len(fill)
        assert np.abs(fill[k:min(stop, k + SR // 4)]).max(initial=0.0) == 0.0   # silent from the cut on
        assert np.abs(fill[max(0, k - SR // 8):k]).max() > 1e-3                # and sounding into it


def test_lufs_meter_reference():
    t = np.arange(SR * 3) / SR
    x = A.stereo((0.5 * np.sin(2 * np.pi * 997 * t)).astype(np.float32))
    assert abs(A.lufs(x) - (-6.02)) < 0.05


def test_true_peak_limiter_hot_signal():
    rng = np.random.default_rng(3)
    n = SR * 2
    t = np.arange(n) / SR
    # +10 dB over full scale, square waves and a tone at fs/4 + offset (big inter-sample peaks)
    x = np.stack([np.sign(np.sin(2 * np.pi * 331 * t)) + rng.normal(0, 0.4, n),
                  np.sin(2 * np.pi * 12001 * t + 0.785) + np.sign(np.sin(2 * np.pi * 447 * t))], 1) * 3.0
    y = limiter(x.astype(np.float32), ceiling_db=-1.0)
    assert y.shape == x.shape
    assert A.true_peak_db(y) <= -1.0
    assert A.true_peak_db(x) > 6.0


def test_sfx_velocity_and_grid():
    from kstudio.audio.mix import place_sfx, sfx_velocity
    from kstudio.timeline import Timeline
    tl = Timeline(bpm=120.0, duration=4.0)
    tl.add("section", 0.0, "a", 2.0, energy=0)
    tl.add("section", 2.0, "b", 2.0, energy=3)
    tl.add("sfx", 0.513, "", 0.0, sound="pop")                 # 13 ms late -> pulled to 0.5
    tl.add("sfx", 2.26, "", 0.0, sound="ui_click")             # 10 ms late -> 2.25 (16th)
    tl.add("sfx", 1.0, "", 0.0, sound="whoosh", align="peak")  # peak on the beat
    tl.add("sfx", 3.0, "", 0.0, sound="riser_short", align="end")
    tl.add("sfx", 1.31, "", 0.0, sound="tick", quantize=False)

    class F:
        cfg: dict = {}
    F.tl = tl
    p = {d["sound"]: d for d in place_sfx(F())}
    assert p["pop"]["anchor"] == 0.5 and p["ui_click"]["anchor"] == 2.25
    assert abs(p["whoosh"]["start"] - (1.0 - synth.PEAK["whoosh"])) < 1e-4
    assert abs(p["riser_short"]["start"] + len(synth.get("riser_short")) / SR - 3.0) < 1e-3
    assert p["tick"]["anchor"] == 1.31
    assert p["pop"]["velocity"] < p["ui_click"]["velocity"]     # quiet intro vs energy-3 section
    assert sfx_velocity(0) == 0.7 and sfx_velocity(3) == 1.0
    assert p["pop"]["pitch"] != 0.0 and p["whoosh"]["pitch"] == 0.0
    for name in ("sub_drop", "downlifter", "ui_click", "ui_tap", "tick_hi", "pop_soft", "blip_up", "blip_down", "snap"):
        assert name in synth.KIT and name in synth.GAIN
    y = synth.render("pop", 0.6, 0.5, 3)
    assert y.shape[1] == 2 and np.abs(y).max() < np.abs(synth.get("pop")).max()


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _video(path, dur=2.0):
    _ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=320x240:r=30:d={dur}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-profile:v", "high", path)


def _hot_wav(path, dur=2.0, limit_db=None):
    t = np.arange(int(SR * dur)) / SR
    x = np.stack([np.sign(np.sin(2 * np.pi * 331 * t)), np.sign(np.sin(2 * np.pi * 447 * t))], 1) * 0.98
    x = x.astype(np.float32)
    if limit_db is not None:
        x = limiter(x, limit_db)
    A.write_wav(path, x)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_audit_fails_on_over_peak_file(tmp_path):
    from kstudio.audit import audit
    v, a, out = str(tmp_path / "v.mp4"), str(tmp_path / "a.wav"), str(tmp_path / "hot.mp4")
    _video(v)
    _hot_wav(a)
    _ffmpeg("-i", v, "-i", a, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out)
    r = audit(out, {"fps": 30, "duration": 2.0})
    assert r["loudness"]["true_peak"] > -1.0
    assert not r["checks"]["true_peak"]["ok"] and not r["pass"]


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_audit_fails_on_fps_drift(tmp_path):
    from kstudio.audit import audit
    out = str(tmp_path / "vfr.mp4")
    # 60 frames whose last 30 timestamps are compressed: r_frame_rate 30, frames/duration ~30.6
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=2", "-vf",
            "setpts='if(lt(N,30),N,30+(N-30)*0.96)/30/TB'", "-fps_mode", "passthrough", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", out)
    r = audit(out, {"fps": 30})
    assert not r["checks"]["fps"]["ok"]
    assert not audit(out, {})["checks"]["fps"]["ok"]   # also without an expected rate (CLI audit)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_mux_holds_true_peak_and_exact_fps(tmp_path):
    from kstudio.audit import audit
    from kstudio.render import mux
    v, a, out = str(tmp_path / "v.mp4"), str(tmp_path / "a.wav"), str(tmp_path / "out.mp4")
    _video(v, 2.0)
    _hot_wav(a, 1.93, limit_db=-1.0)     # a few ms short (the -shortest trap) and hot enough for AAC overs
    r = mux(v, a, out)
    assert r["true_peak_ok"] and r["loudness"]["true_peak"] <= -1.0
    assert r["fps_ok"] and r["video_out"]["frames"] == 60
    c = audit(out, {"fps": 30, "duration": 2.0})["checks"]
    assert c["true_peak"]["ok"] and c["fps"]["ok"] and c["frames"]["ok"] and c["av_length"]["ok"]


def test_resources_rss_budget(monkeypatch):
    from kstudio import resources
    monkeypatch.setattr(resources, "mem_available_mb", lambda: 64000.0)
    monkeypatch.setattr(resources, "cpu_count", lambda: 16)
    monkeypatch.delenv("KSTUDIO_RSS_BUDGET_MB", raising=False)
    n, info = resources.auto_workers(1080, 1920, 3, jobs=50)
    assert n == info["by_budget"] and info["est_total_rss_mb"] <= 3000
    monkeypatch.setenv("KSTUDIO_RSS_BUDGET_MB", "0")
    assert resources.auto_workers(1080, 1920, 3, jobs=50)[0] == 15
    if os.uname().sysname == "Darwin":
        mac = resources.macos_memory()
        assert mac and mac["total_mb"] > 1000 and 0 < mac["available_mb"] <= mac["total_mb"]
