#!/usr/bin/env bash
# Make the README demo GIFs from rendered 1080x1920 masters.
#
#   scripts/make_gifs.sh <masters dir> [out dir]
#
# <masters dir> holds kinetic.mp4, explainer.mp4, promo.mp4, data.mp4 and social.mp4 (a missing one
# is skipped). Each GIF loops a whole number of bars from its master (start and length in seconds
# below; override one with e.g. GIF_WINDOW_kinetic="5.625 3.75"). Recipe: area downscale to 320 px
# wide, spatial-only denoise so the film grain does not repaint every frame (a temporal denoise would
# ghost across cuts), 15 fps, a 64-colour palette from the changing pixels, Bayer dither (ordered, so
# still areas stay still) and per-frame changed rectangles only. The five together stay under 8 MB.
# Writes <out dir>/<mode>.gif (default: demos/ next to this script) and prints the sizes.
set -euo pipefail

MASTERS="${1:?usage: scripts/make_gifs.sh <masters dir> [out dir]}"
OUT="${2:-$(cd "$(dirname "$0")/.." && pwd)/demos}"
WIDTH="${GIF_WIDTH:-320}"
FPS="${GIF_FPS:-15}"
COLORS="${GIF_COLORS:-64}"
BAYER="${GIF_BAYER_SCALE:-5}"
DENOISE="${GIF_DENOISE:-16}"

command -v ffmpeg >/dev/null || { echo "ffmpeg not found on PATH" >&2; exit 6; }
mkdir -p "$OUT"

window() {  # "start length": whole bars of each example's tempo
  case "$1" in
    kinetic) echo "0 3.75" ;;        # 128 BPM, bars 1-2: slam, stack
    explainer) echo "0 5.714" ;;     # 84 BPM, bars 1-2: cover, definition
    promo) echo "0 6.429" ;;         # 112 BPM, bars 1-3: device, features
    data) echo "0 4" ;;              # 120 BPM, bars 1-2: counter, line
    social) echo "0 5.581" ;;        # 86 BPM, bars 1-2: hook, quote
  esac
}

for mode in kinetic explainer promo data social; do
  src="$MASTERS/$mode.mp4"
  if [ ! -f "$src" ]; then
    echo "skip $mode: $src not found" >&2
    continue
  fi
  var="GIF_WINDOW_$mode"
  read -r start length <<< "${!var:-$(window "$mode")}"
  pre="scale=$WIDTH:-2:flags=area,hqdn3d=$DENOISE:$DENOISE:0:0,fps=$FPS"
  pal="palettegen=max_colors=$COLORS:stats_mode=diff"
  use="paletteuse=dither=bayer:bayer_scale=$BAYER:diff_mode=rectangle"
  ffmpeg -hide_banner -loglevel error -y -ss "$start" -t "$length" -i "$src" \
    -filter_complex "[0:v]$pre,split[a][b];[a]$pal[p];[b][p]$use" -loop 0 "$OUT/$mode.gif"
done

du -ch "$OUT"/*.gif
