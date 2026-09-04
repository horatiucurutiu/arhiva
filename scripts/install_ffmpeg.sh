#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$SCRIPT_DIR/../vendor/ffmpeg"
URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"

mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading $URL ..."
curl -L "$URL" -o "$TMP/ffmpeg.tar.xz"

echo "Extracting ..."
tar -xf "$TMP/ffmpeg.tar.xz" -C "$TMP"

EXTRACTED_DIR="$(find "$TMP" -maxdepth 1 -type d -name 'ffmpeg-*')"
if [ -z "$EXTRACTED_DIR" ]; then
    echo "Could not find extracted ffmpeg directory" >&2
    exit 1
fi

cp "$EXTRACTED_DIR/bin/ffmpeg" "$DEST/ffmpeg"
cp "$EXTRACTED_DIR/bin/ffprobe" "$DEST/ffprobe"
chmod +x "$DEST/ffmpeg" "$DEST/ffprobe"

echo "Installed to $DEST:"
"$DEST/ffmpeg" -version | head -1
"$DEST/ffprobe" -version | head -1
