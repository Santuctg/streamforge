# Web Player HLS.js-First Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Main and Node Web Players prefer HLS.js/MSE over Chromium's ambiguous native-HLS `"maybe"` path while retaining native fallback.

**Architecture:** Change only browser engine-selection order in the two existing player implementations. Add source-level regression verification, then package it as aligned Main/Node v12.56 without changing FFmpeg/transcode policy.

**Tech Stack:** FastAPI/Jinja, inline JavaScript, HLS.js 1.7.1, Python verification scripts, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-15-webplayer-hlsjs-first.md`

## Global Constraints
- Marker: `STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256`.
- Preserve native HLS fallback.
- Preserve existing reconnect/buffer/channel-switch/autoplay/stall logic.
- No server-side codec/transcode policy change.
- Main and bundled Node Agent versions both become 12.56.
- Main-first deployment; Node pushed from Main panel only after Main verification.

## Review Focus
- Chromium returns `"maybe"` for native HLS while HLS.js is supported: HLS.js must win.
- Safari/iOS lacks HLS.js/MSE but supports native HLS: native fallback must remain.
- HLS.js library initially absent: self-hosted/CDN loader must run before native fallback.
- Channel switching must still use the same selected engine order.
- Release updater must preserve earlier v12.50/v12.51/v12.53/v12.54/v12.55 fixes.

---

### Task 1: Add RED engine-selection regression test

**Files:**
- Create: `scripts/verify_webplayer_hlsjs_first_v1256.py`
- Create: `.github/workflows/v1256-webplayer-hlsjs-first.yml`

**Interfaces:**
- Consumes: `app/templates/player.html`, `node_agent/app.py` source text.
- Produces: a verifier that fails if native-HLS checks precede HLS.js support/loader selection.

- [ ] Write the verifier asserting the marker exists in both player implementations, HLS.js support is checked before native HLS in startup paths, loader paths do not native-short-circuit first, and native fallback remains.
- [ ] Run it in CI and confirm RED against v12.55 behavior.
- [ ] Commit the failing regression test.

### Task 2: Implement HLS.js-first engine selection

**Files:**
- Modify: `app/templates/player.html`
- Modify: `node_agent/app.py`

**Interfaces:**
- Consumes: Task 1 verifier.
- Produces: HLS.js-first startup/loader behavior with unchanged native fallback.

- [ ] Reorder Main player startup and library-loader selection with marker comments.
- [ ] Reorder Node player startup and engine-loader selection with marker comments.
- [ ] Run Task 1 verifier and existing carry-forward verification; expect GREEN.
- [ ] Commit implementation.

### Task 3: Package aligned v12.56 release

**Files:**
- Modify: `VERSION`
- Modify: `node_agent/VERSION`
- Create: `scripts/force_update_v1256.sh`
- Modify: `scripts/update.sh`
- Create: `release-notes/v12.56.md`
- Modify: `CHANGELOG.md`
- Modify: `.github/workflows/publish-release.yml`
- Modify: `.github/workflows/release-sanity.yml`
- Create: `.github/workflows/v1256-webplayer-hlsjs-first-release.yml` if needed for bounded release checks.

**Interfaces:**
- Consumes: green browser regression verifier and v12.55 updater/static-cache/version-alignment contracts.
- Produces: Main 12.56 + bundled Node Agent 12.56 release package.

- [ ] Add bounded updater that verifies v12.56 marker/carry-forward contracts, installs changed Main/player/Node payload safely, syncs Main static cache only if applicable, restarts only required Main HTTP workers, and leaves channel supervisor/Nginx untouched.
- [ ] Update release metadata/workflows for 12.56.
- [ ] Run release sanity and carry-forward checks; expect GREEN.
- [ ] Open PR for final review; merge/publish only after explicit approval.
