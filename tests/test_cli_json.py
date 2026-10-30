"""Machine interface of the command line: --json envelopes on stdout, logs on stderr, fixed exit codes."""
import json
import os
import subprocess
import sys

import pytest
from conftest import EXAMPLES, ROOT

from kstudio import __version__, cli, pipeline
from kstudio.exitcodes import Exit, exit_table

KINETIC = os.path.join(EXAMPLES, "kinetic")
BAD = '{"scenes": [{"type": "title", "title": "ok"}, {"type": "titel"}]}'


def run(capsys, *argv, code=0):
    rc = cli.main(list(argv))
    out, err = capsys.readouterr()
    lines = [x for x in out.splitlines() if x.strip()]
    assert len(lines) == 1, out  # exactly one JSON object on stdout
    d = json.loads(lines[0])
    assert rc == code == d["exit_code"], (rc, d)
    assert d["ok"] is (code == 0) and d["version"] and d["command"] == next(a for a in argv if a != "--json")
    return d, err


def test_exit_code_table_is_stable():
    assert {r["name"]: r["exit_code"] for r in exit_table()} == {
        "OK": 0, "INTERNAL": 1, "USAGE": 2, "SPEC_INVALID": 3, "RENDER_FAILED": 4, "AUDIT_FAILED": 5,
        "MISSING_TOOL": 6, "BACKEND_UNREACHABLE": 7, "INTERRUPTED": 130}


@pytest.mark.parametrize("argv,key", [
    (["modes"], "modes"), (["scenes"], "scenes"), (["scenes", "title"], "scenes"), (["schema"], "schema"),
    (["validate", KINETIC], "valid"), (["info", KINETIC], "scenes"), (["exit-codes"], "exit_codes"),
    (["brief", "--mode", "promo", "--text", "launch teaser for a notes app"], "system"),
])
def test_every_command_emits_one_json_object(capsys, argv, key):
    d, _ = run(capsys, *argv, "--json")
    assert key in d


def test_global_flag_and_env(capsys, monkeypatch):
    d, _ = run(capsys, "--json", "modes")
    assert len(d["modes"]) == 5
    monkeypatch.setenv("KSTUDIO_JSON", "1")
    d, _ = run(capsys, "exit-codes")
    assert d["exit_codes"][0]["exit_code"] == 0


def test_schema_lists_all_scene_types(capsys):
    from kstudio.scenes import REGISTRY
    d, _ = run(capsys, "schema", "--json")
    assert d["scene_type_count"] == len(REGISTRY) and d["schema"]["x-scene-types"] == sorted(REGISTRY)
    assert cli.main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out)["$schema"].endswith("2020-12/schema")


def test_new_and_dir_not_empty(capsys, tmp_path):
    d, _ = run(capsys, "new", "promo", str(tmp_path / "p"), "--json")
    assert os.path.exists(d["spec_path"])
    d, _ = run(capsys, "new", "promo", str(tmp_path / "p"), "--json", code=2)
    assert d["error"]["code"] == "DIR_NOT_EMPTY"
    d, _ = run(capsys, "new", "nope", str(tmp_path / "q"), "--json", code=2)
    assert d["error"]["code"] == "MODE_UNKNOWN"


def test_usage_error_is_json_and_exit_2(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["render", KINETIC, "--snap", "quarter", "--json"])
    assert e.value.code == 2
    d = json.loads(capsys.readouterr().out)
    assert d["exit_code"] == 2 and d["error"]["code"] == "USAGE" and d["command"] == "render"


def test_invalid_spec_exit_3_with_path(capsys):
    d, _ = run(capsys, "validate", BAD, "--json", "--fix-hints", code=3)
    assert d["valid"] is False and d["error"]["code"] == "SPEC_INVALID"
    assert d["error"]["path"] == "/scenes/1/type" and d["errors"][0]["path"] == "/scenes/1/type"
    assert "/scenes/1/type" in d["fix_prompt"]
    d, _ = run(capsys, "validate", "/nonexistent/spec.json", "--json", code=3)
    assert d["error"]["code"] == "SPEC_NOT_FOUND"
    d, _ = run(capsys, "validate", "{not json", "--json", code=3)
    assert d["error"]["code"] == "SPEC_PARSE"


def test_validate_reads_stdin(capsys, monkeypatch):
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(BAD))
    d, _ = run(capsys, "validate", "-", "--json", code=3)
    assert d["errors"][0]["path"] == "/scenes/1/type"


def _fake_build(result_pass=True, boom=None):
    def build(spec, out, *args, on_progress=None, say=print, snap=None, **kw):
        if boom:
            raise boom
        say("rendering")
        print("stray print from the engine")
        if on_progress:
            on_progress({"frames": 10, "total": 10, "fps": 30.0, "eta": 0.0})
        return {"output": out, "version": __version__, "duration": 1.0, "video_seconds": 0.1, "audio_seconds": 0.1,
                "total_seconds": 0.2,
                "render": {"workers": 1, "resumed_chunks": 0}, "audio": {"final": {"lufs": -14.0, "true_peak": -1.5}},
                "audit": {"pass": result_pass, "checks": {"motion": {"ok": result_pass, "value": 1, "want": 2}}},
                "pass": result_pass}
    return build


def test_render_json_ok_audit_fail_and_render_fail(capsys, monkeypatch, tmp_path):
    out = str(tmp_path / "k.mp4")
    monkeypatch.setattr(cli, "_need_ffmpeg", lambda: None)
    monkeypatch.setattr(pipeline, "build", _fake_build(True))
    d, err = run(capsys, "render", KINETIC, "-o", out, "--json")
    assert d["output"] == os.path.abspath(out) and d["pass"] is True and "result" not in d
    events = [json.loads(x) for x in err.splitlines() if x.startswith("{")]
    assert any(e["event"] == "progress" for e in events) and "stray print" in err
    monkeypatch.setattr(pipeline, "build", _fake_build(False))
    d, _ = run(capsys, "render", KINETIC, "-o", out, "--json", code=5)
    assert d["error"]["code"] == "AUDIT_FAILED" and d["error"]["failed_checks"] == ["motion"] and d["output"]
    monkeypatch.setattr(pipeline, "build", _fake_build(boom=RuntimeError("encoder died")))
    d, _ = run(capsys, "render", KINETIC, "-o", out, "--json", code=4)
    assert d["error"]["code"] == "RENDER_FAILED" and "encoder died" in d["error"]["message"]


def test_render_invalid_spec_never_builds(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "build", _fake_build(boom=AssertionError("must not run")))
    d, _ = run(capsys, "render", BAD, "-o", str(tmp_path / "x.mp4"), "--json", code=3)
    assert d["error"]["path"] == "/scenes/1/type"


def test_missing_tool_exit_6(capsys, monkeypatch, tmp_path):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    d, _ = run(capsys, "render", KINETIC, "-o", str(tmp_path / "x.mp4"), "--json", code=6)
    assert d["error"]["code"] == "MISSING_TOOL" and "ffmpeg" in d["error"]["missing"]


def test_human_mode_keeps_errors_on_stderr(capsys):
    assert cli.main(["validate", BAD]) == 3
    out, err = capsys.readouterr()
    assert out == "" and "/scenes/1/type" in err


def test_subprocess_stdout_is_pure_json(tmp_path):
    env = dict(os.environ, PYTHONPATH=ROOT, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "kstudio", "validate", BAD, "--json"], capture_output=True,
                       text=True, env=env, cwd=str(tmp_path), timeout=120)
    assert r.returncode == int(Exit.SPEC_INVALID)
    assert json.loads(r.stdout)["error"]["path"] == "/scenes/1/type"
    r = subprocess.run([sys.executable, "-m", "kstudio", "brief", "--mode", "data", "--text", "-", "--json"],
                       input="quarterly revenue story", capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert d["brief"] == "quarterly revenue story" and len(d["examples"]) == 2 and '"$defs"' in d["system"]
