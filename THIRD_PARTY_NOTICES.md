# Third-party notices

Kinetic Studio's own code is original (clean-room) work under the MIT licence (see `LICENSE`).
It contains no code copied from other video-template products. It uses or bundles the following
third-party components.

## Bundled: fonts (SIL Open Font License 1.1)

The full licence text for each family ships next to the font in `kstudio/assets/fonts/OFL-<Family>.txt`.
Under the OFL the fonts may be bundled, embedded and redistributed with software, including
commercial software, but may not be sold on their own. Reserved Font Names apply to modified versions.

| Font | Authors | File | Licence |
|---|---|---|---|
| Archivo (variable) | Copyright 2020 The Archivo Project Authors (github.com/Omnibus-Type/Archivo) | `Archivo.ttf` | OFL-1.1 |
| Inter (variable) | Copyright 2016 The Inter Project Authors (github.com/rsms/inter) | `Inter.ttf` | OFL-1.1 |
| Fraunces (variable) | Copyright 2020 The Fraunces Project Authors (github.com/undercasetype/Fraunces) | `Fraunces.ttf` | OFL-1.1 |
| JetBrains Mono (variable) | Copyright 2020 The JetBrains Mono Project Authors (github.com/JetBrains/JetBrainsMono) | `JetBrainsMono.ttf` | OFL-1.1 |
| Space Grotesk (variable) | Copyright 2020 The Space Grotesk Project Authors (github.com/floriankarsten/space-grotesk) | `SpaceGrotesk.ttf` | OFL-1.1 |

## Python dependencies (installed by the user, not bundled)

| Package | Licence |
|---|---|
| skia-python (and Google Skia) | BSD-3-Clause |
| NumPy | BSD-3-Clause |
| opencv-python-headless (OpenCV) | Apache-2.0 (OpenCV 4.5+); wheel bundles its own third-party notices |
| pytest, ruff, mypy, pre-commit (dev only) | MIT |

## External tool (not bundled)

**FFmpeg / ffprobe** are called as separate executables through the command line. They are not linked
or distributed with this project. FFmpeg is LGPL-2.1+; builds with libx264 enabled (required for H.264
output) are GPL-2.0+. The user installs FFmpeg separately and is responsible for complying with the
licence of the build they use.

## Audio

All music beds and sound effects are synthesised procedurally at render time by this package's own
code (`kstudio/audio`). No samples, loops or recordings are bundled. Audio the user supplies (`music.file`)
remains the user's responsibility.
