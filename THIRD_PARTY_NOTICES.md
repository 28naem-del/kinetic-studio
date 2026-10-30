# Third-party notices

This file lists third-party code and fonts included in Kinetic Studio, with
attribution and licence information. Kinetic Studio's own licence is in
`LICENSE`.

## Fonts

All bundled fonts live in `kstudio/assets/fonts/` and are licensed under the
SIL Open Font License 1.1 (SPDX: `OFL-1.1`). The full licence text for each
family ships next to the font file.

| Family | File | Licence text | Copyright |
| --- | --- | --- | --- |
| Archivo | `Archivo.ttf` | `OFL-Archivo.txt` | Copyright 2020 The Archivo Project Authors (https://github.com/Omnibus-Type/Archivo) |
| Fraunces | `Fraunces.ttf` | `OFL-Fraunces.txt` | Copyright 2020 The Fraunces Project Authors (github.com/undercasetype/Fraunces) |
| Inter | `Inter.ttf` | `OFL-Inter.txt` | Copyright 2016 The Inter Project Authors (https://github.com/rsms/inter) |
| JetBrains Mono | `JetBrainsMono.ttf` | `OFL-JetBrainsMono.txt` | Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono) |
| Space Grotesk | `SpaceGrotesk.ttf` | `OFL-SpaceGrotesk.txt` | Copyright 2020 The Space Grotesk Project Authors (https://github.com/floriankarsten/space-grotesk) |

## Code

When adding code adapted from another project, add an entry here in this format:

```
### <name>
- URL: <project or source URL>
- Licence: <SPDX identifier, e.g. MIT>
- Files affected: <paths in this repo>
- Copyright: <copyright line as given by the original project>
```

### Openjutsu
- URL: https://github.com/D3voz/openjutsu (workflows taken at commit 7fe118bb5a61fcfac0148e1bfea3dd29884dbf26)
- Licence: MIT
- Files affected: `kstudio/workflows/simple.api.json`, `kstudio/workflows/object.api.json` (the ComfyUI API
  workflows, unchanged); `kstudio/swap.py` follows its approach of routing a video loader's soundtrack
  through a silence-if-none node before submitting a workflow
- Copyright: Copyright (c) 2026 D3voz

The workflows run only on a ComfyUI server that the user sets up with Openjutsu's custom node packs and
models; none of those are included or downloaded by Kinetic Studio. Licence text:

```
MIT License

Copyright (c) 2026 D3voz

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
