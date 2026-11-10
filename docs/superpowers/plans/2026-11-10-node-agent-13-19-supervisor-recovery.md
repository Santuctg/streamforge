# Node Agent 13.19 Supervisor Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship StreamForge 13.19 so Main can update every Remote Node through the existing API flow, with race-free supervisor startup and durable expiry-channel recovery.

**Architecture:** Keep the existing `/api/v1/agent/update` ZIP protocol and full-release version alignment. Harden the Node Agent's dedicated supervisor lifecycle with a filesystem lock, live PID identity checks, socket ping-before-cleanup, and a readiness-triggered pending-restore retry; preserve restore intents until a channel is confirmed healthy.

**Tech Stack:** Python 3.12-compatible standard library, FastAPI/Uvicorn Node Agent, Unix-domain sockets, JSON state files, pytest-style standalone regression scripts, Bash updater scripts.

**Spec:** `docs/superpowers/specs/2026-11-10-node-agent-13-19-supervisor-recovery-design.md`

## Global Constraints

- Use the existing Main → Node Agent API ZIP update flow; no separate SSH/manual distribution path for normal 13.19 updates.
- Do not change source URLs or make source-down channels appear healthy.
- Preserve `state.json`, `operations-settings.json`, running FFmpeg handoff, and the existing API update backup/rollback behavior.
- A restore intent remains pending after supervisor unavailability or source failure and is removed only after confirmed healthy/live status.
- A supported Node service has exactly one dedicated supervisor owner.
- Main and bundled Node Agent versions remain aligned at `13.19`.

## Review Focus

- Two simultaneous `ensure_channel_supervisor()` calls: one supervisor/socket owner, not two spawned processes. Test in Task 2.
- Stale PID/socket files and PID reuse: stale coordination files are removed only after identity/ping checks. Test in Task 2.
- Supervisor unavailable during expiry renewal: restore intents survive and retry after readiness. Test in Task 3.
- Source fails after FFmpeg spawn: channel remains Down and restore intent remains pending. Test in Task 3.
- API update while FFmpeg is running: update handoff preserves encoders and the new supervisor starts cleanly. Test in Task 4.

---

### Task 1: Establish 13.19 version and regression-test scaffolding

**Files:**
- Modify: `VERSION`
- Modify: `node_agent/VERSION`
- Create: `scripts/test_v1319_node_supervisor.py`
- Create: `scripts/test_v1319_updater.py`
- Create: `scripts/verify_v1319_patch_roundtrip.py`

**Interfaces:**
- Produces the failing behavioral tests and 13.19 updater contract that later tasks must satisfy.

- [ ] **Step 1: Write failing tests**
  - Add tests for single-owner supervisor startup, stale coordination-file recovery, pending expiry restore, source-failure retention, and API update payload/version markers.
  - Clone the v13.18 updater integration structure, but require the new Node Agent digest and `13.01|...|13.19` gate.
- [ ] **Step 2: Run tests and verify the new behavior fails**
  - Run: `python3 scripts/test_v1319_node_supervisor.py` and `python3 scripts/test_v1319_updater.py`.
  - Expected: FAIL because the supervisor lock/readiness behavior and 13.19 files do not exist yet.
- [ ] **Step 3: Bump version metadata to 13.19**
  - Set both version files to `13.19`; do not alter Node runtime code in this step.
- [ ] **Step 4: Run syntax/contract checks**
  - Run: `python3 scripts/test_v1319_node_supervisor.py`.
  - Expected: the test reaches behavior assertions and still fails only on unimplemented supervisor behavior.
- [ ] **Step 5: Commit**
  - Commit: `test: add v13.19 supervisor recovery contracts`

### Task 2: Harden dedicated supervisor ownership and startup

**Files:**
- Modify: `node_agent/app.py` around supervisor constants, `ensure_channel_supervisor()`, `_supervisor_pid_alive()`, and `_ChannelSupervisorRPCServer.serve()`.
- Test: `scripts/test_v1319_node_supervisor.py`

**Interfaces:**
- Produces `SUPERVISOR_LOCK_FILE` and private lifecycle helpers used by expiry recovery.
- Keeps public `channel_supervisor_ping()` and `ensure_channel_supervisor() -> bool` signatures unchanged.

- [ ] **Step 1: Add focused failing lifecycle tests**
  - Test no coordination files → one supervisor can bind.
  - Test stale PID/socket → stale files are removed and one supervisor starts.
  - Test live PID/socket → existing supervisor is reused; no second spawn.
  - Test concurrent ensure calls under a temporary lock directory → exactly one spawn attempt.
- [ ] **Step 2: Implement serialized ownership**
  - Add an exclusive filesystem lock beside the socket, held across ping/PID validation, stale cleanup, spawn, and readiness wait.
  - Validate PID identity using `/proc/<pid>/cmdline` and process start time where available; do not treat arbitrary PID reuse as the supervisor.
- [ ] **Step 3: Make socket cleanup safe**
  - Ping the configured socket before unlinking it.
  - Remove the socket/PID only when the owner is absent, stale, or unresponsive; never unlink a live supervisor’s socket while it is serving.
  - Ensure the socket parent exists before bind/chmod/PID writes.
- [ ] **Step 4: Run lifecycle tests**
  - Run: `python3 scripts/test_v1319_node_supervisor.py -k supervisor` (or the script’s standalone equivalent).
  - Expected: PASS with one owner and no bind/PID race.
- [ ] **Step 5: Commit**
  - Commit: `fix: serialize Node channel supervisor ownership`

### Task 3: Make expiry restore readiness-aware and durable

**Files:**
- Modify: `node_agent/app.py` in `enforce_node_expiry()`, `start()`, supervisor readiness/recovery paths, and status/health handling.
- Test: `scripts/test_v1319_node_supervisor.py`

**Interfaces:**
- Keeps `_expiry_restore_settings()`, `_persist_expiry_restore()`, `enforce_node_expiry() -> str`, and `start() -> dict[str, Any]` compatible with existing callers.
- Adds a private pending-restore retry helper invoked after supervisor readiness and by the existing expiry loop.

- [ ] **Step 1: Add failing restore tests**
  - Seed `expiry_restore_desired` with a channel, make supervisor RPC unavailable, clear expiry, and assert the intent remains.
  - Make supervisor ready, retry, and assert the delegated channel becomes desired/running and the intent is removed only after healthy status.
  - Make the source fail after spawn and assert status is Down with the restore intent retained.
- [ ] **Step 2: Implement readiness-triggered retry**
  - After `ensure_channel_supervisor()` reports ready, call the pending-restore helper once; keep periodic retry as a fallback.
  - Treat supervisor-unavailable, `error`, `stopped`, `expired`, and unhealthy-start results as pending, not successful.
- [ ] **Step 3: Confirm healthy success before removal**
  - Remove a restore entry only when the returned/status snapshot reports an alive/running healthy channel; preserve it across source failures.
  - Avoid writing control-worker state in a way that overwrites supervisor-owned desired state.
- [ ] **Step 4: Run restore tests**
  - Run: `python3 scripts/test_v1319_node_supervisor.py -k expiry` (or the script’s standalone equivalent).
  - Expected: PASS; source-down channels remain Down while available channels resume.
- [ ] **Step 5: Commit**
  - Commit: `fix: retry Node expiry restores after supervisor readiness`

### Task 4: Wire the 13.19 API-distributed Node Agent package

**Files:**
- Modify: `app/node_manager.py` only if the existing package needs explicit 13.19 metadata/digest validation; otherwise add contract coverage without changing the proven ZIP protocol.
- Modify: `node_agent/app.py` update endpoint only for 13.19 post-update readiness/rollback checks if required by the tests.
- Test: `scripts/test_v1319_updater.py`

**Interfaces:**
- Uses existing `NodeController.update_agent(node, app_root, version)` and `/api/v1/agent/update`.
- Produces a package containing the updated `app.py`, `redis_state.py`, `requirements.txt`, and `VERSION=13.19`, preserving the existing FFmpeg handoff.

- [ ] **Step 1: Add failing API package tests**
  - Assert the Main-built ZIP carries Node version 13.19 and the supervisor marker/digest.
  - Assert API update rejects malformed archives, preserves running encoders through handoff, and returns readiness information.
- [ ] **Step 2: Implement only required API compatibility changes**
  - Keep the existing archive allowlist and safe extraction checks.
  - Preserve backup, atomic file replacement, requirements stamp behavior, FFmpeg handoff, and self-restart.
  - Add post-restart supervisor/socket readiness verification if the current endpoint cannot report it.
- [ ] **Step 3: Run updater tests**
  - Run: `python3 scripts/test_v1319_updater.py`.
  - Expected: PASS for payload, idempotent update, rollback, version gates, and handoff.
- [ ] **Step 4: Commit**
  - Commit: `feat: package Node Agent supervisor recovery for API updates`

### Task 5: Create the 13.19 Main updater and release contracts

**Files:**
- Create: `scripts/force_update_v1319.sh` (clone v13.18 structure with the new Node blob and 13.19 gate).
- Modify: `scripts/update.sh`
- Modify: `scripts/verify_release_updater_staging.py` only if its current-version generic checks require a 13.19 branch.
- Test: `scripts/verify_v1319_patch_roundtrip.py`

**Interfaces:**
- Main updater accepts installed Main/Node versions `13.01` through `13.19`, stages `app/static/app.js`, `app/static/style.css`, Node Agent files, and preserves rollback/static-cache contracts.

- [ ] **Step 1: Add failing release-contract assertions**
  - Require v13.19 updater marker, current/previous Node blob handling, `update.sh` routing, Node supervisor markers, and no Nginx restart.
- [ ] **Step 2: Implement updater wiring**
  - Clone v13.18 updater semantics, update target/gates/blob, and include the changed Node Agent payload.
  - Keep old Node blob compatibility only if the 13.19 package’s migration path requires it; assert the exact accepted hashes in the test.
- [ ] **Step 3: Run release contracts**
  - Run: `python3 scripts/verify_v1319_patch_roundtrip.py` and `python3 scripts/verify_release_updater_staging.py`.
  - Expected: PASS.
- [ ] **Step 4: Commit**
  - Commit: `release: add v13.19 Node Agent updater contracts`

### Task 6: Documentation, full verification, and release

**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `docs/INSTALLATION.md`, `docs/API_AND_TESTING.md`.
- Create: `release-notes/v13.19.md`.
- Test: all v13.19 tests plus relevant existing Node/update tests.

- [ ] **Step 1: Document API-based rollout**
  - Explain that Main must reach 13.19 first, then each Remote Node is updated from the Node Updates panel/API; source-down channels remain Down.
- [ ] **Step 2: Run targeted verification**
  - Run: `python3 scripts/test_v1319_node_supervisor.py`, `python3 scripts/test_v1319_updater.py`, `python3 scripts/verify_v1319_patch_roundtrip.py`, `python3 scripts/test_documentation.py`, `python3 scripts/verify_release_updater_staging.py`, `node --check node_agent/app.py` (if supported; otherwise Python compile).
- [ ] **Step 3: Run full relevant suite and inspect failures**
  - Run the tracked test suite under the supported Python version; separate pre-existing environment/version-gated failures from 13.19 regressions.
- [ ] **Step 4: Run `git diff --check` and inspect package contents**
  - Confirm no secrets, diagnostic artifacts, `.DS_Store`, `__pycache__`, or harness files enter the release.
- [ ] **Step 5: Commit, push, and verify CI/release ZIP**
  - Push `main`, wait for Publish StreamForge Release, verify tag `v13.19`, asset SHA256, Node Agent version, and supervisor marker in the ZIP.
