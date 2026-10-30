"""kstudio — Kinetic Studio command line.

  kstudio modes | scenes [type]           what the engine can make
  kstudio new <mode> <dir>                starter project from the mode's example spec
  kstudio info <spec>                     resolved timeline (scenes, markers, beats) + grid lint
  kstudio preview <spec> [--at 1.2,3]     stills + contact sheet (fast, single process)
  kstudio render <spec> [-o out.mp4] [--preset reel|landscape|square] [--fps 30|60]
             [--quality draft|standard|high] [--4k] [--workers auto|N] [--fresh]
             [--snap [beat|8th|16th|bar]]
  kstudio audit <video> [--mode m] [--fps N] [--frames N]
                                          motion / loudness / codec / fps / duration checks
  kstudio mix <spec> -o out.wav           soundtrack only
  kstudio beats <audio>                   tempo + beat grid of a music file
  kstudio sfx <dir>                       export the synthesized SFX kit as WAVs
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _load(path: str) -> dict:
    from .spec import load_spec
    return load_spec(path)


def _bar(info: dict) -> None:
    total = max(1, info["total"])
    k = int(30 * info["frames"] / total)
    eta = f"eta {info['eta']:.0f}s" if info.get("eta") is not None else ""
    sys.stderr.write(f"\r  [{'#' * k}{'.' * (30 - k)}] {info['frames']}/{total} frames  {info['fps']:.1f} fps  {eta}   ")
    sys.stderr.flush()


def cmd_modes(a: argparse.Namespace) -> int:
    from .modes import MODES
    for k, m in MODES.items():
        print(f"{k:<10} {m['description']}\n{'':<10} theme={m['theme']} music={m['music']['style']}@{m['music']['bpm']} "
              f"transition={m['transition']}")
    return 0


def cmd_scenes(a: argparse.Namespace) -> int:
    from .scenes import REGISTRY
    for name, cls in sorted(REGISTRY.items()):
        if a.type and name != a.type:
            continue
        doc = (cls.__doc__ or "").strip().splitlines()
        print(f"{name:<12} {doc[0] if doc else ''}")
        if a.type or a.verbose:
            for k, v in cls.defaults.items():
                print(f"{'':<14}{k} = {json.dumps(v)}")
    return 0


def cmd_new(a: argparse.Namespace) -> int:
    src = EXAMPLES / a.mode
    if not src.exists():
        print(f"no example for mode {a.mode!r}; available: {sorted(p.name for p in EXAMPLES.iterdir() if p.is_dir())}")
        return 2
    dst = Path(a.dir)
    if dst.exists() and any(dst.iterdir()) and not a.force:
        print(f"{dst} is not empty (use --force)")
        return 2
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, dst / f.name)
        elif f.is_dir() and f.name not in ("out", ".kstudio_work"):
            shutil.copytree(f, dst / f.name, dirs_exist_ok=True)
    print(f"created {dst}/spec.json ({a.mode}). Next: kstudio preview {dst}  then  kstudio render {dst}")
    return 0


def cmd_info(a: argparse.Namespace) -> int:
    from .film import Film
    from .pipeline import beat_grid_for
    spec = _load(a.spec)
    film = Film(spec, 1080, 1920, beat_grid=beat_grid_for(spec))
    s = film.summary()
    if a.json:
        print(json.dumps(s, indent=1, default=str))
        return 0
    print(f"mode {s['mode']}  duration {s['duration']}s  bpm {film.tl.bpm:.2f}")
    for sc in s["scenes"]:
        print(f"  {sc['start']:7.2f}s  +{sc['dur']:<6.2f} {sc['type']:<12} {sc['id']}")
    for b in s["transitions"]:
        print(f"  {b['t']:7.2f}s  transition {b['kind']} ({b['dur']}s)")
    kinds: dict[str, int] = {}
    for m in film.tl.markers:
        kinds[m.kind] = kinds.get(m.kind, 0) + 1
    print("  markers:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    lint = film.lint()
    warns = [w for w in lint if w["level"] == "warn"]
    print(f"  lint: {len(warns)} warning(s), {len(lint) - len(warns)} info")
    for w in lint if a.verbose else warns:
        print(f"    {w['level']:<4} {w['where']}: {w['msg']}")
    return 0


def cmd_preview(a: argparse.Namespace) -> int:
    from .pipeline import beat_grid_for
    from .preview import preview
    spec = _load(a.spec)
    out = a.out or os.path.join(spec["_dir"], "out", "preview")
    times = [float(x) for x in a.at.split(",")] if a.at else None
    t0 = time.time()
    r = preview(spec, out, a.preset, times, a.scale, a.quality, beat_grid_for(spec))
    print(f"{len(r['stills'])} stills in {time.time() - t0:.1f}s -> {r['contact_sheet']}")
    return 0


def cmd_render(a: argparse.Namespace) -> int:
    from .audit import format_report
    from .pipeline import build
    spec = _load(a.spec)
    out = a.output or os.path.join(spec["_dir"], "out", f"{Path(spec['_dir']).name}_{a.preset}.mp4")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    workers = a.workers if a.workers == "auto" else int(a.workers)
    r = build(spec, out, a.preset, a.fps, a.quality, a.k4, workers, a.fresh, not a.no_audit,
              on_progress=None if a.quiet else _bar, say=(lambda s: None) if a.quiet else print, snap=a.snap)
    if not a.quiet:
        sys.stderr.write("\n")
    rv = r["render"]
    print(f"done: {r['output']}  ({r['duration']}s, video {r['video_seconds']}s with {rv['workers']} workers, "
          f"{rv['resumed_chunks']} chunks resumed; audio {r['audio_seconds']}s; total {r['total_seconds']}s)")
    print(f"audio: {r['audio']['final']['lufs']} LUFS, {r['audio']['final']['true_peak']} dBTP")
    if "audit" in r:
        print(format_report(r["audit"]))
    return 0 if r.get("pass", True) else 3


def cmd_audit(a: argparse.Namespace) -> int:
    from .audit import audit, format_report
    from .modes import mode_defaults
    th = dict(mode_defaults(a.mode).get("audit", {})) if a.mode else {}
    if a.min_motion is not None:
        th["min_motion"] = a.min_motion
    if a.max_hold is not None:
        th["max_hold"] = a.max_hold
    expect: dict = {}
    if a.fps is not None:
        expect["fps"] = a.fps
    if a.frames is not None:
        expect["frames"] = a.frames
    r = audit(a.video, expect, th)
    print(json.dumps(r, indent=1) if a.json else format_report(r))
    return 0 if r["pass"] else 3


def cmd_mix(a: argparse.Namespace) -> int:
    from .audio.mix import mix_film
    from .film import Film
    from .pipeline import beat_grid_for
    spec = _load(a.spec)
    film = Film(spec, 1080, 1920, beat_grid=beat_grid_for(spec))
    r = mix_film(film, a.output)
    print(f"{a.output}: {r['final']['lufs']} LUFS, {r['final']['true_peak']} dBTP, {r['sfx_count']} sfx, music {r['music']}")
    return 0


def cmd_beats(a: argparse.Namespace) -> int:
    from .beats import analyse_file
    g = analyse_file(a.audio)
    print(json.dumps({**g.as_dict(), "first_beats": [round(x, 3) for x in g.beat_times[:8]]}, indent=1))
    return 0


def cmd_sfx(a: argparse.Namespace) -> int:
    from .audio.synth import export_kit
    paths = export_kit(a.dir)
    print(f"wrote {len(paths)} sounds to {a.dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kstudio", description="Kinetic Studio — programmatic motion-graphics videos.")
    p.add_argument("--version", action="version", version=f"kstudio {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("modes", help="list modes").set_defaults(fn=cmd_modes)
    s = sub.add_parser("scenes", help="list scene types and their parameters")
    s.add_argument("type", nargs="?")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_scenes)
    s = sub.add_parser("new", help="start a project from a mode example")
    s.add_argument("mode")
    s.add_argument("dir")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_new)
    s = sub.add_parser("info", help="print the resolved timeline and beat-grid lint")
    s.add_argument("spec")
    s.add_argument("--json", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true", help="also list info-level lint items")
    s.set_defaults(fn=cmd_info)
    s = sub.add_parser("preview", help="render stills + a contact sheet")
    s.add_argument("spec")
    s.add_argument("--out")
    s.add_argument("--at", help="comma-separated times in seconds")
    s.add_argument("--preset", default="reel")
    s.add_argument("--scale", type=float, default=0.5)
    s.add_argument("--quality", default="draft")
    s.set_defaults(fn=cmd_preview)
    s = sub.add_parser("render", help="render the final MP4 (parallel, resumable)")
    s.add_argument("spec")
    s.add_argument("-o", "--output")
    s.add_argument("--preset", default="reel")
    s.add_argument("--fps", type=int, default=30)
    s.add_argument("--quality", default="standard", choices=["draft", "standard", "high"])
    s.add_argument("--4k", dest="k4", action="store_true", help="double the preset resolution")
    s.add_argument("--workers", default="auto")
    s.add_argument("--fresh", action="store_true", help="ignore finished chunks")
    s.add_argument("--snap", nargs="?", const="beat", choices=["beat", "8th", "16th", "bar"],
                   help="snap seconds-valued scene lengths/starts to the beat grid (bare --snap = beat); "
                        "cue sfx snap to the given division")
    s.add_argument("--no-audit", action="store_true", help="skip the audit (mux fps/true-peak checks still run)")
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(fn=cmd_render)
    s = sub.add_parser("audit", help="audit a finished video")
    s.add_argument("video")
    s.add_argument("--mode")
    s.add_argument("--min-motion", type=float)
    s.add_argument("--max-hold", type=float)
    s.add_argument("--fps", type=float, help="expected frame rate (r = avg = n/d must equal it)")
    s.add_argument("--frames", type=int, help="expected video frame count (+-1)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_audit)
    s = sub.add_parser("mix", help="render the soundtrack only")
    s.add_argument("spec")
    s.add_argument("-o", "--output", default="mix.wav")
    s.set_defaults(fn=cmd_mix)
    s = sub.add_parser("beats", help="analyse a music file's tempo and beats")
    s.add_argument("audio")
    s.set_defaults(fn=cmd_beats)
    s = sub.add_parser("sfx", help="export the SFX kit")
    s.add_argument("dir")
    s.set_defaults(fn=cmd_sfx)
    a = p.parse_args(argv)
    try:
        return int(a.fn(a) or 0)
    except KeyboardInterrupt:
        print("\ninterrupted — re-run the same command to resume from finished chunks")
        return 130
    except Exception as e:  # noqa: BLE001
        from .spec import SpecError
        from .timeline import TimeExprError
        if isinstance(e, (SpecError, TimeExprError, KeyError, ValueError)) and not os.environ.get("KSTUDIO_DEBUG"):
            print(f"kstudio: error: {e}", file=sys.stderr)
            return 2
        if isinstance(e, RuntimeError) and not os.environ.get("KSTUDIO_DEBUG"):
            print(f"kstudio: render error: {e}", file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
