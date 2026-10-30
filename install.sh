#!/usr/bin/env bash
# Kinetic Studio installer: creates ./.venv and installs the Python dependencies.
# Requires python3 >= 3.10 and ffmpeg/ffprobe (with libx264 + AAC) on PATH.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
command -v ffmpeg >/dev/null || { echo "ffmpeg not found: install it (apt install ffmpeg / brew install ffmpeg)"; exit 1; }
command -v ffprobe >/dev/null || { echo "ffprobe not found (ships with ffmpeg)"; exit 1; }
encoders="$(ffmpeg -hide_banner -encoders 2>/dev/null || true)"
case "$encoders" in *libx264*) ;; *) echo "your ffmpeg lacks libx264"; exit 1;; esac
python3 -m venv .venv
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt
if [ "${1:-}" = "--dev" ]; then .venv/bin/pip install pytest ruff mypy pre-commit; fi
chmod +x bin/kstudio
echo "OK. Try:  bin/kstudio new kinetic my_teaser && bin/kstudio render my_teaser"
