"""Native Skia check for the shared selector; executed in the dependency-equipped lane."""
from pathlib import Path

import pytest

from kstudio import fonts
from kstudio.font_paths import FONT_DIR, resolve_font_path
from kstudio.modes import resolve_theme


@pytest.mark.parametrize("name", ["font.bin", "extensionless"])
def test_real_renamed_font_loads_directly_and_through_theme_alias(tmp_path, monkeypatch, name):
    renamed = tmp_path / name
    renamed.write_bytes((Path(FONT_DIR) / "Inter.ttf").read_bytes())
    monkeypatch.chdir(tmp_path)
    for selection in (str(renamed), name):
        theme = resolve_theme({"base": "ink", "brand": selection})
        family = theme["brand"]
        assert resolve_font_path(family) == selection
        loaded = fonts._base(family)
        assert loaded.getFamilyName() == fonts._base("Inter").getFamilyName()
        assert fonts.font(family, 48).measureText("PORTABLE") > 0
