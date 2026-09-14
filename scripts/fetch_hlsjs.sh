#!/usr/bin/env bash
set -u

TARGET="${1:-/opt/streamforge/app/static/vendor/hls.min.js}"
VERSION="${STREAMFORGE_HLSJS_VERSION:-1.7.1}"
mkdir -p "$(dirname "$TARGET")"

URLS=(
  "https://github.com/video-dev/hls.js/releases/download/v${VERSION}/hls.min.js"
  "https://cdn.jsdelivr.net/npm/hls.js@${VERSION}/dist/hls.min.js"
  "https://unpkg.com/hls.js@${VERSION}/dist/hls.min.js"
)

for url in "${URLS[@]}"; do
  tmp="${TARGET}.tmp"
  if curl -fsSL --connect-timeout 8 --max-time 45 "$url" -o "$tmp"; then
    if [[ -s "$tmp" ]]; then
      mv -f "$tmp" "$TARGET"
      chmod 0644 "$TARGET"
      echo "Installed HLS.js v${VERSION}: $TARGET"
      exit 0
    fi
  fi
  rm -f "$tmp"
done

echo "Warning: HLS.js could not be downloaded. The player will try the CDN in the browser." >&2
exit 0
