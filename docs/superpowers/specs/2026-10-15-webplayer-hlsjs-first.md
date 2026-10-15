# v12.56 Web Player HLS.js-First Playback Spec

## Goal
Fix channels that play in Firefox but fail or stall in Chrome because Chromium may report native HLS capability as `"maybe"`, causing StreamForge to bypass HLS.js.

## Required behavior
- Main Web Player and Remote Node Web Player must prefer HLS.js when `window.Hls && Hls.isSupported()` is true.
- Native HLS remains the fallback for browsers that do not support HLS.js/MSE but do report native HLS support (for example Safari/iOS).
- `ensureHlsLibrary()` / Node `ensureEngine()` must not short-circuit to native HLS before attempting the self-hosted HLS.js engine.
- Existing reconnect, buffering, channel switching, autoplay fallback, and stall-recovery behavior must remain unchanged.
- No broad server-side transcode or codec policy change in this release.
- Main and bundled Node Agent versions must both be 12.56.

## Marker
`STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256`

## Release safety
- Carry forward v12.50 DB-session fix, v12.51 Node relay-probe single-owner guard, v12.53 Waiting recovery, v12.54 static-cache sync, and v12.55 aligned release behavior.
- Update Main first, verify, then push the bundled Node Agent from the Main panel.
