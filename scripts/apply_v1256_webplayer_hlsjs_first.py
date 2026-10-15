#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

MARKER = "STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one source pattern, found {count}")
    return text.replace(old, new, 1)


def patch_main(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return False

    old_head = """  // STREAMFORGE_WEBPLAYER_HLSJS_LOCAL_CDN_FALLBACK_V1163:\n  // Main/brand Web Players use the same playback engine. Prefer the local copy\n  // installed by StreamForge, then fail over across two public CDNs. If all are\n  // temporarily unreachable, keep retrying instead of leaving Firefox/Chrome\n  // permanently in an unsupported state.\n  function ensureHlsLibrary() {\n    if (video.canPlayType('application/vnd.apple.mpegurl')) {\n      startNativePlayback();\n      return;\n    }\n    if (window.Hls && Hls.isSupported()) {\n      startPlayback(fallbackMode);\n      return;\n    }\n"""
    new_head = """  // STREAMFORGE_WEBPLAYER_HLSJS_LOCAL_CDN_FALLBACK_V1163:\n  // Main/brand Web Players use the same playback engine. Prefer the local copy\n  // installed by StreamForge, then fail over across two public CDNs. If all are\n  // temporarily unreachable, keep retrying instead of leaving Firefox/Chrome\n  // permanently in an unsupported state.\n  // STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256:\n  // Chromium may report native HLS as \"maybe\" even when its native path is\n  // less compatible than MSE/HLS.js. Load/use HLS.js first; use native HLS only\n  // after HLS.js is known unavailable or unsupported.\n  function ensureHlsLibrary() {\n    if (window.Hls && Hls.isSupported()) {\n      startPlayback(fallbackMode);\n      return;\n    }\n    if (window.Hls && video.canPlayType('application/vnd.apple.mpegurl')) {\n      startNativePlayback();\n      return;\n    }\n"""
    text = replace_once(text, old_head, new_head, "Main ensureHlsLibrary head")

    old_exhausted = """      if (index >= sources.length) {\n        hlsLibraryLoading = false;\n        setState('reconnecting', 'starting');\n"""
    new_exhausted = """      if (index >= sources.length) {\n        hlsLibraryLoading = false;\n        if (video.canPlayType('application/vnd.apple.mpegurl')) {\n          startNativePlayback();\n          return;\n        }\n        setState('reconnecting', 'starting');\n"""
    text = replace_once(text, old_exhausted, new_exhausted, "Main HLS.js source exhaustion")

    old_onload = """      script.onload = () => {\n        if (window.Hls && Hls.isSupported()) {\n          hlsLibraryLoading = false;\n          startPlayback(fallbackMode);\n        } else {\n          script.remove();\n          attempt(index + 1);\n        }\n      };\n"""
    new_onload = """      script.onload = () => {\n        if (window.Hls && Hls.isSupported()) {\n          hlsLibraryLoading = false;\n          startPlayback(fallbackMode);\n        } else if (video.canPlayType('application/vnd.apple.mpegurl')) {\n          hlsLibraryLoading = false;\n          startNativePlayback();\n        } else {\n          script.remove();\n          attempt(index + 1);\n        }\n      };\n"""
    text = replace_once(text, old_onload, new_onload, "Main HLS.js script onload")

    old_start = """  function startPlayback(stable = false) {\n    if (video.canPlayType('application/vnd.apple.mpegurl')) {\n      startNativePlayback();\n      return;\n    }\n    if (window.Hls && Hls.isSupported()) {\n      loadHls(stable);\n      return;\n    }\n    ensureHlsLibrary();\n  }\n"""
    new_start = """  function startPlayback(stable = false) {\n    if (window.Hls && Hls.isSupported()) {\n      loadHls(stable);\n      return;\n    }\n    if (window.Hls && video.canPlayType('application/vnd.apple.mpegurl')) {\n      startNativePlayback();\n      return;\n    }\n    ensureHlsLibrary();\n  }\n"""
    text = replace_once(text, old_start, new_start, "Main startPlayback")
    path.write_text(text, encoding="utf-8")
    return True


def patch_node(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return False

    old_head = """/* STREAMFORGE_NODE_WEBPLAYER_HLSJS_LOCAL_CDN_FALLBACK_V1163 */function ensureEngine(){{if(video.canPlayType('application/vnd.apple.mpegurl')){{nativeStart();return}}if(window.Hls&&Hls.isSupported()){{load(fallback);return}}if(engineLoading)return;"""
    new_head = """/* STREAMFORGE_NODE_WEBPLAYER_HLSJS_LOCAL_CDN_FALLBACK_V1163 *//* STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256 */function ensureEngine(){{if(window.Hls&&Hls.isSupported()){{load(fallback);return}}if(window.Hls&&video.canPlayType('application/vnd.apple.mpegurl')){{nativeStart();return}}if(engineLoading)return;"""
    text = replace_once(text, old_head, new_head, "Node ensureEngine head")

    old_exhausted = """if(i>=sources.length){{engineLoading=false;setState('reconnecting','starting');setMsg('Player engine is temporarily unavailable. Retrying automatically…');"""
    new_exhausted = """if(i>=sources.length){{engineLoading=false;if(video.canPlayType('application/vnd.apple.mpegurl')){{nativeStart();return}}setState('reconnecting','starting');setMsg('Player engine is temporarily unavailable. Retrying automatically…');"""
    text = replace_once(text, old_exhausted, new_exhausted, "Node HLS.js source exhaustion")

    old_onload = """sc.onload=()=>{{if(window.Hls&&Hls.isSupported()){{engineLoading=false;load(fallback)}}else{{sc.remove();attempt(i+1)}}}};"""
    new_onload = """sc.onload=()=>{{if(window.Hls&&Hls.isSupported()){{engineLoading=false;load(fallback)}}else if(video.canPlayType('application/vnd.apple.mpegurl')){{engineLoading=false;nativeStart()}}else{{sc.remove();attempt(i+1)}}}};"""
    text = replace_once(text, old_onload, new_onload, "Node HLS.js script onload")

    old_start = """function start(){{if(video.canPlayType('application/vnd.apple.mpegurl')){{nativeStart();return}}if(window.Hls&&Hls.isSupported()){{load(false);return}}ensureEngine()}}"""
    new_start = """function start(){{if(window.Hls&&Hls.isSupported()){{load(false);return}}if(window.Hls&&video.canPlayType('application/vnd.apple.mpegurl')){{nativeStart();return}}ensureEngine()}}"""
    text = replace_once(text, old_start, new_start, "Node start")
    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    changed_main = patch_main(root / "app/templates/player.html")
    changed_node = patch_node(root / "node_agent/app.py")
    if changed_main or changed_node:
        print("FIXED")
    else:
        print("ALREADY_FIXED")


if __name__ == "__main__":
    main()
