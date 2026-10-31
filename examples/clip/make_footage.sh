#!/usr/bin/env bash
# Make footage.mp4 for this example: 3 s of abstract warm and cool bokeh, 540x960, 30 fps, no audio.
# Everything is synthesised by ffmpeg's lavfi sources (a radial gradient field plus two blurred
# Game of Life layers drifting across it), so the clip carries no third-party material.
# Usage: bash examples/clip/make_footage.sh [out.mp4]
set -euo pipefail
out="${1:-$(dirname "$0")/footage.mp4}"
warm="life=s=30x54:r=4:seed=9:ratio=0.16:life_color=#ff9a40:death_color=#000000:mold=0:stitch=1"
cool="life=s=20x36:r=3:seed=4:ratio=0.14:life_color=#3fa8ff:death_color=#000000:mold=0:stitch=1"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "gradients=s=540x960:r=30:d=3:c0=0x03070c:c1=0x0d2633:c2=0x2a120a:nb_colors=3:speed=0.02:type=radial:seed=5" \
  -f lavfi -i "$warm,scale=600:1080:flags=bilinear,boxblur=14:3,framerate=fps=30,crop=540:960:'30+30*sin(t*0.9)':'120-40*t'" \
  -f lavfi -i "$cool,scale=600:1080:flags=bilinear,boxblur=24:3,framerate=fps=30,crop=540:960:'30-25*sin(t*0.8)':'60+20*t'" \
  -filter_complex "[1:v]format=gbrp,eq=contrast=1.5:brightness=0.02[a];[2:v]format=gbrp,eq=contrast=1.4[b];[0:v]format=gbrp[g];[g][a]blend=all_mode=addition:all_opacity=0.75[ga];[ga][b]blend=all_mode=addition:all_opacity=0.55,noise=alls=4:allf=t,format=yuv420p" \
  -t 3 -r 30 -c:v libx264 -preset slow -crf 28 -threads 1 -movflags +faststart -map_metadata -1 "$out"
echo "$out"
