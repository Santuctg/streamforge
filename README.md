# StreamForge v12.13

## Node playback-start latency hardening

v12.10 removes two synchronous operations from the Remote Node public playback hot path that could make an otherwise healthy channel take several seconds to start.

- Public Gunicorn workers on port 8821 no longer send their own synchronous Main Server heartbeat while authenticating playlist/Xtream/WebPlayer playback. The single Node control worker owns the heartbeat and publishes a small atomic shared connectivity snapshot for all public workers.
- The shared Main-connectivity snapshot is fail-closed when missing/stale and is primed immediately after the control worker finishes loading Node state.
- Direct `/live/<user>/<pass>/<stream>` and legacy `/<user>/<pass>/<stream>` requests no longer build an online catalogue by querying runtime status for every assigned channel before matching one stream ID.
- Direct playback resolves assignment in memory, then validates only the selected channel using its fresh local HLS playlist/segment. It does not wait on a channel-supervisor RPC.
- Existing v12.9 Playlist User expiry editing, v12.8 Web Player Brand HTTP root routing, v12.7 public Xtream routing, v12.5/v12.7 Node-local user ownership, v12.3 playback reservation recovery, and prior rollback/data-preservation behavior remain intact.

Expected effect on a healthy local-HLS channel: the backend portion of a direct channel start should normally fall into the sub-second range instead of randomly blocking for 5–15 seconds because of Main heartbeat or supervisor status calls.
