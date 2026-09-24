#!/usr/bin/env bash
# Render the demo attack clip: swap SOURCE_FACE (one frontal photo of the
# consenting teammate whose ID is used in the demo) onto TARGET_CLIP (a
# front-camera recording of another teammate doing the full capture script).
# Output lands where the "Inject deepfake" toggle in web/index.html plays it.
#
# Usage: tools/render_deepfake.sh <source_face.jpg> <target_clip.mp4|mov> [out.mp4]
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="${1:?source face photo}"
TGT="${2:?target clip}"
OUT="${3:-web/attack/deepfake.mp4}"
FF="$(cd .. && pwd)/facefusion"          # sibling checkout, see notes/08
WORK="$(mktemp -d)"

[ -x "$FF/.venv/bin/python" ] || { echo "FaceFusion venv missing at $FF (notes/08 §3)"; exit 1; }
mkdir -p "$(dirname "$OUT")"

# iPhone clips are HLG HDR (arib-std-b67). FaceFusion's own colour-transfer
# step outputs black frames on this ffmpeg, so tonemap to SDR bt709 first and
# scale to the portrait size the capture page uses.
ffmpeg -v error -y -i "$TGT" \
  -vf "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,scale=720:1280" \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 -an -c:v libx264 -preset veryfast "$WORK/target_sdr.mp4"

(
  cd "$FF" && source .venv/bin/activate && \
  python facefusion.py headless-run \
    -s "$SRC" -t "$WORK/target_sdr.mp4" -o "$WORK/swapped.mp4" \
    --processors face_swapper --face-swapper-model hyperswap_1a_256 \
    --execution-providers coreml --face-selector-mode one \
    --output-video-encoder libx264 --log-level info
)

# Muted, faststart, so the page can loop it without a network stall.
ffmpeg -v error -y -i "$WORK/swapped.mp4" -an -c:v copy -movflags +faststart "$OUT"
rm -rf "$WORK"
echo "wrote $OUT"; ffprobe -v error -show_entries stream=width,height,duration -of csv=p=0 "$OUT"
