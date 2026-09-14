#!/usr/bin/env bash
set -u
# STREAMFORGE_YOUTUBE_RUNTIME_BOOTSTRAP_V1014:
# YouTube extraction in current yt-dlp uses an external JS runtime. Deno is
# optional for the rest of StreamForge, so bootstrap it without making normal
# IPTV service installation depend on deno.land availability.
log(){ printf '[StreamForge YouTube] %s\n' "$*"; }
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  missing=()
  for pkgcmd in unzip curl; do command -v "$pkgcmd" >/dev/null 2>&1 || missing+=("$pkgcmd"); done
  if [[ ${#missing[@]} -gt 0 ]]; then
    apt-get update -y >/dev/null 2>&1 || true
    apt-get install -y unzip curl ca-certificates >/dev/null 2>&1 || true
  fi
fi
if command -v deno >/dev/null 2>&1; then
  log "Deno runtime detected: $(deno --version 2>/dev/null | head -1)"
  exit 0
fi
if ! command -v curl >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1; then
  log "WARNING: curl/unzip unavailable; YouTube JS runtime could not be bootstrapped."
  exit 0
fi
export DENO_INSTALL=/usr/local
if curl -fsSL --connect-timeout 10 --max-time 60 https://deno.land/install.sh | sh >/dev/null 2>&1 && command -v deno >/dev/null 2>&1; then
  log "Installed $(deno --version 2>/dev/null | head -1)."
else
  log "WARNING: Deno install failed; normal streams remain available, but some YouTube sources may not resolve."
fi
exit 0
