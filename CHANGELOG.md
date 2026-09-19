## 12.34 - 2026-09-19

- Shortened the first `index.m3u8` request on every WebPlayer channel switch by removing duplicate panel/user validation from the already-validated playback-grant path.
- Kept playback-grant validation and connection-limit reservation synchronous and fail-closed while moving the larger viewer metadata Redis transaction behind the authorization response.
- Added `Server-Timing` propagation for cold live-playlist authorization, exposing separate `sf-grant` and `sf-reserve` durations in browser DevTools.
- Advanced the release updater to `force_update_v1234.sh`.

## 12.33 - 2026-09-19

- Removed per-worker Nginx AIO thread pools from Remote Node direct HLS segment delivery; a measured 40-worker Node was creating about 1,280 unnecessary threads and reporting 1,322 service tasks.
- Retained zero-copy `sendfile`, `tcp_nopush` and segment `open_file_cache` for high-throughput local immutable HLS delivery.
- Applied the fix to every managed HTTP and HTTPS Node frontend and added updater guards preventing `aio threads` from returning.
- Advanced the release updater to `force_update_v1233.sh`.

## 12.32 - 2026-09-19

- Stopped the two-minute Node TLS reconciliation timer from reloading Nginx when neither generated configuration nor certificate contents changed.
- Added a persistent runtime fingerprint covering the managed Nginx configuration, authorization-cache configuration and referenced certificate/key contents.
- Detects accumulated Nginx master generations and performs one clean restart during the first required reconciliation, preventing hundreds of stranded HLS keep-alive workers from causing intermittent login and playback latency.
- Advanced the release updater to `force_update_v1232.sh`.

## 12.31 - 2026-09-18

- Removed periodic live-playlist stalls caused by the first request after each two-second Nginx authorization-cache expiry waiting synchronously for Python/Redis.
- Nginx now serves the last successful authorization immediately while refreshing the live-session heartbeat in the background; initial playback authorization remains synchronous and fail-closed.
- Applied the behavior to both managed HTTPS and host-aware HTTP Node frontends.
- Advanced the release updater to `force_update_v1231.sh`.

## 12.30 - 2026-09-18

- Removed the remaining Node WebPlayer startup bottleneck by sending the initial player and channel-switch URLs directly to Nginx's authenticated live `index.m3u8` route.
- WebPlayer playback no longer waits for the Python/Redis `master.m3u8` control-plane request; Nginx continues to enforce live authorization, connection reservation and session heartbeats.
- Preserved `/node-play/.../master.m3u8` for external M3U and non-WebPlayer compatibility.
- Advanced the release updater to `force_update_v1230.sh`.

## 12.29 - 2026-09-18

- Re-published the v12.28 Main-parity Node HLS change with lossless source blobs after the GitHub v12.28 `node_agent/app.py` object was truncated during connector upload.
- Added release verification against the committed GitHub blob so UTF-8 decoding, compatibility markers and the two-stage Nginx media implementation are confirmed before deployment.
- Advanced the release updater to `force_update_v1229.sh`.

## 12.28 - 2026-09-18

- Restored Main-parity two-stage HLS delivery on Remote Node Web Players: Python authorizes the static master once, while Nginx directly refreshes the live media playlist and serves segments.
- Removed the v12.25 flat-media behavior that made every one-second `master.m3u8` refresh repeat Python/Redis authorization and caused intermittent 1–2.65 second server TTFB.
- Preserved same-origin multi-URL routing, strict connection limits, live-session heartbeats and Nginx direct segment delivery.
- Advanced the release updater to `force_update_v1228.sh`.

## 12.27 - 2026-09-18

- Fixed the v12.26 updater rejecting the flat Node Web Player media bootstrap because a second historical v6.5 direct-HLS marker check was still stale.
- Audited all positive package marker guards against the current source so the release does not fail one legacy check at a time.
- Advanced the release updater to `force_update_v1227.sh`.

## 12.26 - 2026-09-18

- Fixed the v12.25 updater rejecting the new flat Node Web Player media bootstrap because it still required the superseded v6.8 same-origin compatibility marker.
- Package and installed-file validation now accept either the legacy same-origin implementation or the v12.25 root-relative flat-media implementation.
- Advanced the release updater to `force_update_v1226.sh` for a clean upgrade from v12.24 and v12.25.

## 12.25 - 2026-09-18

- Fixed the measured Node Web Player startup bottleneck where master.m3u8 and index.m3u8 each spent about 1.25-1.30 seconds in serial authorization.
- The authorized Node master endpoint now returns the media playlist directly, eliminating the second control-plane playlist request while retaining direct Nginx segment delivery.
- Viewer metadata is recorded after the master response; connection-limit reservation remains synchronous and fail-closed.

## 12.24 - 2026-09-18

- Fixed remaining Node Web Player 2-3 second page delay on large channel lineups.
- Parallelized independent local HLS readiness checks with a bounded worker pool while preserving playlist order and strict Waiting-channel exclusion.
- Reused one session-wide wildcard playback grant for every channel card on a watch page, eliminating one Redis round-trip per channel.

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
