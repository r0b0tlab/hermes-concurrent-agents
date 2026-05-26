#!/usr/bin/env bash
set -euo pipefail

# Post-production recipe for the educational local-agent-team X video.
# Input is an OBS-captured MKV or MP4. Output is an X-ready MP4.

INPUT=""
OUTPUT=""
CAPTIONS=""
WATERMARK=""
START="00:00:00"
END=""
RESOLUTION="1920:1080"
DRY_RUN=false

usage(){
  cat <<'USAGE'
Usage: post-production.sh --input FILE [OPTIONS]

Options:
  --input FILE      Raw OBS recording (.mkv or .mp4) (required)
  --output FILE     Output MP4 (default: <input-stem>_final.mp4)
  --captions FILE   SRT captions to bake in (optional)
  --watermark FILE  Watermark image to overlay top-right (optional)
  --start HH:MM:SS  Start cut (default: 00:00:00)
  --end HH:MM:SS    End cut (default: full clip)
  --resolution WxH  Output resolution (default: 1920:1080)
  --dry-run         Print ffmpeg commands without running
  -h, --help        Show this help
USAGE
}

run(){ if [[ "$DRY_RUN" == true ]]; then printf '[dry-run] %q ' "$@"; echo; else "$@"; fi; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input) INPUT="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --captions) CAPTIONS="$2"; shift 2 ;;
    --watermark) WATERMARK="$2"; shift 2 ;;
    --start) START="$2"; shift 2 ;;
    --end) END="$2"; shift 2 ;;
    --resolution) RESOLUTION="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[error] unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -n "$INPUT" ]] || { echo "[error] --input is required" >&2; usage; exit 2; }
[[ "$DRY_RUN" == true || -f "$INPUT" ]] || { echo "[error] input not found: $INPUT" >&2; exit 2; }

if [[ -z "$OUTPUT" ]]; then
  stem="${INPUT%.*}"
  OUTPUT="${stem}_final.mp4"
fi

if ! command -v ffmpeg >/dev/null 2>&1 && [[ "$DRY_RUN" != true ]]; then
  echo "[error] ffmpeg is required" >&2; exit 2
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT
REMUXED="$TMPDIR/remuxed.mp4"
TRIMMED="$TMPDIR/trimmed.mp4"
SCALED="$TMPDIR/scaled.mp4"
CAPTIONED="$TMPDIR/captioned.mp4"
WATERMARKED="$TMPDIR/watermarked.mp4"

# 1. Remux to MP4 (stream copy, faststart) if input is MKV.
case "${INPUT,,}" in
  *.mkv)
    run ffmpeg -y -i "$INPUT" -c copy -movflags +faststart "$REMUXED"
    ;;
  *)
    REMUXED="$INPUT"
    ;;
esac

# 2. Trim.
if [[ -n "$END" ]]; then
  run ffmpeg -y -i "$REMUXED" -ss "$START" -to "$END" -c:v libx264 -crf 18 -preset medium -movflags +faststart "$TRIMMED"
else
  run ffmpeg -y -i "$REMUXED" -ss "$START" -c:v libx264 -crf 18 -preset medium -movflags +faststart "$TRIMMED"
fi

# 3. Scale to target resolution (preserves aspect by padding).
run ffmpeg -y -i "$TRIMMED" -vf "scale=$RESOLUTION:force_original_aspect_ratio=decrease,pad=$RESOLUTION:(ow-iw)/2:(oh-ih)/2:color=black" -c:v libx264 -crf 18 -preset medium -movflags +faststart "$SCALED"

CURRENT="$SCALED"

# 4. Bake captions if provided.
if [[ -n "$CAPTIONS" ]]; then
  [[ "$DRY_RUN" == true || -f "$CAPTIONS" ]] || { echo "[error] captions file not found: $CAPTIONS" >&2; exit 2; }
  run ffmpeg -y -i "$CURRENT" -vf "subtitles=$CAPTIONS:force_style='FontName=DejaVu Sans Mono,FontSize=22,PrimaryColour=&H00F0F0F5,OutlineColour=&H00050510,BorderStyle=3,Outline=2,Shadow=0,MarginV=80'" -c:v libx264 -crf 18 -preset medium -movflags +faststart "$CAPTIONED"
  CURRENT="$CAPTIONED"
fi

# 5. Overlay watermark if provided.
if [[ -n "$WATERMARK" ]]; then
  [[ "$DRY_RUN" == true || -f "$WATERMARK" ]] || { echo "[error] watermark file not found: $WATERMARK" >&2; exit 2; }
  run ffmpeg -y -i "$CURRENT" -i "$WATERMARK" -filter_complex "[1:v]scale=240:-1[wm];[0:v][wm]overlay=W-w-32:32:format=auto" -c:v libx264 -crf 18 -preset medium -movflags +faststart "$WATERMARKED"
  CURRENT="$WATERMARKED"
fi

# 6. Final export.
run cp "$CURRENT" "$OUTPUT"
if [[ "$DRY_RUN" != true ]]; then
  printf 'Final video: %s\n' "$OUTPUT"
  printf 'Recommended X upload: H.264, MP4, faststart, <= 140s, <= 512MB\n'
fi
