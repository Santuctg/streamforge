## 12.23 - 2026-09-18

- Removed the dedicated supervisor RPC from Node Web Player and playlist catalogue loading.
- Node catalogues now use the co-located fresh HLS playlist and newest segment as the authoritative Up signal, eliminating the possible three-second supervisor wait.
- Waiting, Down and stale-output channels remain excluded.

## 12.22 - 2026-09-18

- Fixed v12.21 post-install verification looking for the v12.16 safe-prefetch markers under an incorrect v12.18 suffix.
- Restored fast Main Web Player login by using network-free per-Node readiness cache instead of synchronous Remote Node status requests.
- Preserved strict filtering: cached unknown, Waiting, Down and non-HLS-ready channels remain excluded.

## 12.21 - 2026-09-18

- Fixed the v12.20 updater rejecting the package after the Main Web Player strict-online refactor removed historical compatibility markers.
- Restored legacy integrity markers without restoring the stale status-only fallback; Main Web Player continues to hide Waiting channels.

## 12.20 - 2026-09-18

- Fixed the Main Web Player showing Waiting channels through its stale persisted-status fallback.
- Main Web Player now uses the same authoritative online-channel resolver as Main M3U and Xtream catalogues.
- Channels are exposed only when an assigned local or remote playback target reports both alive and HLS ready.

## 12.19 - 2026-09-18

- Fixed the v12.18 updater integrity check incorrectly searching for the Node channel-switch marker under the v12.18 suffix instead of its original v12.17 marker.
- Preserved the v12.18 fresh-HLS catalogue filtering while allowing clean upgrades from v12.17 and v12.18 packages.

## 12.18 - 2026-09-18

- Fixed Node playlist and Web Player catalogues including channels whose supervisor status was stale after the channel entered Waiting.
- Catalogue loading now requires both current Up status and a real, fresh local HLS playlist/segment before exposing a channel.
- Waiting, Down, stopped, stale-HLS, and non-ready channels are excluded from newly loaded M3U, Xtream and Web Player catalogues.

## 12.17 - 2026-09-18

- Fixed Node Web Player channel switching getting stuck on CONNECTING after rapidly selecting two or more channels.
- Isolated each HLS playback generation so delayed callbacks from a destroyed channel can no longer load, recover, or overwrite the newly selected channel.
- Reset playback freshness tracking on every in-page channel switch for reliable startup and stall detection.

# StreamForge v12.16

- Fixed repeated Web Player `RECONNECTING` loops introduced by the one-segment live-edge startup.
- Main and Remote Node players retain immediate fragment prefetch while starting two complete segments behind the live edge.
- Prevented races against an in-progress HLS segment that could produce intermittent 404/stall recovery cycles.
- Release updater advanced to `force_update_v1216.sh`.

# StreamForge v12.15

- Reduced Main and Brand Web Player startup latency for regular HLS channels.
- Main and Remote Node players now begin at the newest complete segment with immediate fragment prefetch.
- Reconnect recovery retains the wider stable buffer for unreliable links.
- Release updater advanced to `force_update_v1215.sh`.

# StreamForge v12.14

- Fixed server-level Web Player saves clearing existing Remote Node Web Player Brand host mappings.
- Main now sends the complete saved Web Player snapshot, including all brand profiles and shared controls, instead of the form-only server settings.
- Brand logo/favicon/download assets are re-synchronized after a server-level save.
- Release updater advanced to `force_update_v1214.sh`.

# StreamForge v12.13

- Stopped Web Player Brands with blank logo/favicon/app fields from inheriting Main or Node server-level assets.
- A blank brand logo now renders the neutral player mark, a blank favicon emits no brand favicon, and a blank app hides the Download button and clears host-scoped update metadata.
- Applied the same isolated-brand behavior to Main and Remote Node Web Players.
- Release updater advanced to `force_update_v1213.sh`.

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
