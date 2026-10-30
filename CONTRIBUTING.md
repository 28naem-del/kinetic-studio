# Contributing to Kinetic Studio

Thanks for helping! Bug reports, new scenes, themes, music styles and docs are all welcome.

## Setup

```bash
git clone https://github.com/28naem-del/kinetic-studio.git
cd kinetic-studio
bash install.sh --dev        # .venv with runtime deps + pytest, ruff, mypy, pre-commit
.venv/bin/pre-commit install
```

You need FFmpeg with libx264 on your PATH.

## Before you open a pull request

```bash
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/python -m pytest -q
```

CI runs the same checks on every push and pull request.

## Adding a scene

1. Add a class in `kstudio/scenes/` that subclasses `Scene`, with a docstring (shown by
   `kstudio scenes`), `defaults`, and `draw(ctx)`. Optionally add `cues()` for SFX and `shutter()`
   for motion blur.
2. Register it in the scene registry so `kstudio scenes` lists it.
3. Regenerate the reference: `bin/kstudio scenes -v` → `docs/SCENES.md`.
4. Add a test, and check a preview with `bin/kstudio preview`.

## Guidelines

- Keep everything local: no network calls, no API keys, no telemetry.
- Only add assets (fonts, images, audio) under a licence compatible with MIT redistribution, and
  record them in `THIRD_PARTY_NOTICES.md`.
- Keep large binaries out of git. Demo masters go to GitHub Releases.
- Example content must be neutral and factual; no health, financial or legal claims.

By contributing you agree that your contributions are licensed under the MIT licence and that you
will follow the [Code of Conduct](CODE_OF_CONDUCT.md).
