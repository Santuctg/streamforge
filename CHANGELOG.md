# StreamForge v12.12

- Fixed Main-managed Node logo changes not appearing in the Node Web Player.
- Node Web Player name/logo lookup now reads the latest shared synchronized identity across public worker processes.
- Local Node Web Player logo responses now disable browser caching so replacements at the same URL appear immediately.
- Release updater advanced to `force_update_v1212.sh`.

# StreamForge v12.11

- Fixed Main Channels page ID/row order synchronization after saving a playlist hierarchy.
- Playlist category/channel order now updates the authoritative Main catalogue sort order while preserving database primary keys.
- GitHub-generated ZIP updates now normalize script permissions automatically.
- Release updater advanced to `force_update_v1211.sh` so v12.10 installations perform a real version upgrade.

# StreamForge v12.10

- Fixed intermittent multi-second Remote Node channel-start latency.
- Added `STREAMFORGE_NODE_PUBLIC_SHARED_PANEL_CONNECTIVITY_V1210`: port-8821 public workers use the control worker's atomic shared Main-connectivity snapshot instead of issuing foreground Main heartbeat requests.
- Added `STREAMFORGE_NODE_SHARED_HEARTBEAT_FAST_PRIME_V1210`: the control heartbeat thread primes shared connectivity immediately after startup state is loaded.
- Added `STREAMFORGE_NODE_DIRECT_STREAM_SINGLE_READY_V1210`: direct stream lookup matches the requested stream before any readiness validation.
- Added `STREAMFORGE_NODE_DIRECT_STREAM_HLS_FAST_READY_V1210`: direct playback checks only the selected channel's local HLS freshness and avoids a supervisor RPC in the request hot path.
- Preserved all v12.9 Node Playlist User expiry edit support and earlier StreamForge fixes/settings/data.
