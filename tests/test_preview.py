"""`kinetic-studio preview`: stills and a contact sheet, in human and --json mode."""
import json
import os
import subprocess
import sys

from conftest import EXAMPLES, ROOT

from kstudio import cli

KINETIC = os.path.join(EXAMPLES, "kinetic")


def test_preview_human_mode_writes_stills_and_sheet(capsys, tmp_path):
    out = tmp_path / "p"
    assert cli.main(["preview", KINETIC, "-o", str(out), "--at", "0.5,2.0", "--scale", "0.25"]) == 0
    printed, _ = capsys.readouterr()
    assert printed.startswith("2 stills in ") and "contact_sheet.jpg" in printed
    assert (out / "contact_sheet.jpg").is_file()
    assert len(list(out.glob("still_*.jpg"))) == 2


def test_preview_json_is_one_object_with_absolute_still_paths(capsys, tmp_path):
    out = tmp_path / "p"
    rc = cli.main(["preview", KINETIC, "--out", str(out), "--at", "1.0", "--scale", "0.25", "--json"])
    stdout, _ = capsys.readouterr()
    lines = [x for x in stdout.splitlines() if x.strip()]
    assert rc == 0 and len(lines) == 1
    d = json.loads(lines[0])
    assert d["ok"] is True and d["exit_code"] == 0 and d["command"] == "preview"
    assert [set(s) for s in d["stills"]] == [{"t", "label", "path"}]
    still = d["stills"][0]
    assert os.path.isabs(still["path"]) and os.path.isfile(still["path"]) and still["t"] == 1.0
    assert os.path.isfile(d["contact_sheet"]) and d["out_dir"] == str(out)
    assert d["summary"]["mode"] == "kinetic"


def test_preview_default_times_cover_every_scene(capsys, tmp_path):
    out = tmp_path / "p"
    assert cli.main(["preview", KINETIC, "-o", str(out), "--scale", "0.25", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    labels = {s["label"] for s in d["stills"]}
    assert {sc["id"] for sc in d["summary"]["scenes"]} <= labels


def test_preview_bad_times_is_a_usage_error(capsys, tmp_path):
    assert cli.main(["preview", KINETIC, "-o", str(tmp_path / "p"), "--at", "soon", "--json"]) == 2
    d = json.loads(capsys.readouterr().out)
    assert d["error"]["code"] == "USAGE"


def test_preview_subprocess_stdout_is_pure_json(tmp_path):
    env = dict(os.environ, PYTHONPATH=ROOT, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "kstudio", "preview", KINETIC, "-o", str(tmp_path / "p"),
                        "--at", "3", "--scale", "0.25", "--json"], capture_output=True, text=True, env=env,
                       cwd=str(tmp_path), timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    lines = r.stdout.splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["stills"][0]["path"].endswith(".jpg")
