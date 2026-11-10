# Node Agent 13.19 Supervisor and Expiry Recovery

## Intent

Ship a versioned Node Agent update for every Remote Node so that renewing an
expired Node subscription reliably resumes channels that were running before
expiry, while preventing the dedicated channel supervisor from entering a
Unix-socket/PID spawn race.

## Goals

- Keep expiry restore intents durable until a channel start actually succeeds.
- Retry restore after the dedicated supervisor becomes ready.
- Ensure only one supervisor owns the Unix socket and PID file.
- Recover stale socket/PID state safely after service restart or supervisor
  crash.
- Preserve existing channel state, source configuration, and running FFmpeg
  processes during normal update/restart behavior.
- Provide a versioned `13.19` Node Agent package/updater and regression tests
  suitable for applying to all Remote Nodes.

## Non-goals

- Do not change source URLs or repair provider-side `403`/`409` errors.
- Do not make source-down channels appear healthy.
- Do not change Main subscription date semantics.
- Do not alter public playback workers or unrelated Main UI behavior.

## Approaches considered

1. **Manual recovery script only** — quick socket/PID cleanup and service
   restart, but the race remains and can recur. Rejected as incomplete.
2. **Main-side repeated start requests** — depends on the Node supervisor being
   reachable and can amplify the race. Rejected as unreliable.
3. **Node Agent 13.19 patch release (chosen)** — fix supervisor ownership and
   startup reconciliation in the Node Agent, retain pending expiry restore
   intents, and retry after supervisor readiness. This addresses the failure at
   its source and is distributable to every Node.

## Design

### Supervisor lifecycle

- Before binding, reconcile the socket and PID file using process identity,
  rather than blindly unlinking a live supervisor's socket.
- Treat a live, responding supervisor as authoritative and do not spawn a
  second one.
- If the recorded PID is stale and the socket is not serving, remove only the
  stale coordination files and start one replacement supervisor.
- Make control-worker supervisor startup/retry serialized and idempotent.

### Expiry restore

- Keep `expiry_restore_desired` entries until the delegated start returns a
  successful running/starting result.
- If the supervisor is unavailable, leave entries pending without repeatedly
  spawning competing supervisors.
- Retry pending entries after supervisor readiness and on the existing expiry
  enforcement interval.
- Preserve the current behavior that source failures remain Down and continue
  retrying without losing the restore intent.

### Update package

- Bump Node Agent version to `13.19`.
- Add a versioned Node Agent updater/package that accepts the current supported
  Node versions and performs backup, atomic replacement, rollback, service
  restart, and post-update checks.
- Extend the existing Main → Node Agent API ZIP update flow; do not introduce a
  separate SSH/manual distribution path.
- Keep the proven Node runtime payload contract and ensure Main’s Node update
  path can distribute the package to all Remote Nodes.

### Tests

- Supervisor startup with no coordination files.
- Supervisor startup with stale PID/socket files.
- Concurrent/idempotent ensure calls do not create two supervisors.
- Expiry renewal while supervisor is temporarily unavailable retains pending
  restore intents.
- Pending intents resume after supervisor readiness.
- Source start failure keeps its restore intent and remains Down; restore
  success is confirmed by the first healthy/live observation, not merely by
  spawning FFmpeg.
- Updater success, idempotent re-run, rollback, version gate, and payload
  digest checks, including the existing FFmpeg update-handoff preservation path.

## Success criteria

After updating a Node to 13.19, extending its expiry causes previously running
channels with available sources to resume without manual socket cleanup; a
source that is genuinely unavailable remains Down, and the Node supervisor
has exactly one live PID/socket owner.
