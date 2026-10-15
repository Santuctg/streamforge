#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256"
NATIVE = "canPlayType('application/vnd.apple.mpegurl')"


def fail(message: str) -> None:
    raise SystemExit(f"FAIL: {message}")


def function_slice(text: str, start_token: str, end_token: str, label: str) -> str:
    start = text.find(start_token)
    if start < 0:
        fail(f"{label}: missing {start_token}")
    end = text.find(end_token, start + len(start_token))
    if end < 0:
        fail(f"{label}: missing end token {end_token}")
    return text[start:end]


def assert_hls_first(block: str, label: str) -> None:
    hls = block.find("Hls.isSupported()")
    native = block.find(NATIVE)
    if hls < 0:
        fail(f"{label}: Hls.isSupported() check missing")
    if native < 0:
        fail(f"{label}: native HLS fallback missing")
    if hls > native:
        fail(f"{label}: native HLS is still selected before HLS.js/MSE")


def verify_main(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER not in text:
        fail("Main player marker missing")
    ensure = function_slice(text, "function ensureHlsLibrary()", "function startPlayback", "Main ensureHlsLibrary")
    start = function_slice(text, "function startPlayback", "video.addEventListener('playing'", "Main startPlayback")
    assert_hls_first(ensure, "Main ensureHlsLibrary")
    assert_hls_first(start, "Main startPlayback")
    if "sources = [" not in ensure or "static/vendor/hls.min.js" not in ensure:
        fail("Main self-hosted HLS.js loader was lost")
    if "startNativePlayback()" not in ensure or "startNativePlayback()" not in start:
        fail("Main native HLS fallback was lost")


def verify_node(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER not in text:
        fail("Node player marker missing")
    ensure = function_slice(text, "function ensureEngine()", "function start()", "Node ensureEngine")
    start = function_slice(text, "function start()", "/* STREAMFORGE_WEBPLAYER_PERSISTENT_AUTO_RECONNECT_V3055 */", "Node start")
    assert_hls_first(ensure, "Node ensureEngine")
    assert_hls_first(start, "Node start")
    if "hls.min.js" not in ensure or "cdn.jsdelivr.net/npm/hls.js@1.7.1" not in ensure:
        fail("Node HLS.js loader/fallback sources were lost")
    if "nativeStart()" not in ensure or "nativeStart()" not in start:
        fail("Node native HLS fallback was lost")


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    verify_main(root / "app/templates/player.html")
    verify_node(root / "node_agent/app.py")
    print("PASS: v12.56 Main/Node Web Players prefer HLS.js/MSE and retain native HLS fallback")


if __name__ == "__main__":
    main()
