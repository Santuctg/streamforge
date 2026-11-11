## 13.30 - 2026-11-11

- Capped the closed Categories picker at 260px so it no longer stretches across the remaining identity-row space.

## 13.29 - 2026-11-11

- Kept the Categories picker on the first identity row beside the checkbox and interval fields.
- Widened the last Audio & output column so "HLS segment seconds" no longer breaks across lines, and made encoding-grid label rows grow with wrapped captions.

## 13.28 - 2026-11-11

- Fixed standalone help initialization so section captions without labels mount their ⓘ buttons beside the heading instead of leaving lone icons on separate rows.

## 13.27 - 2026-11-11

- Added explicit section-caption anchors for standalone help buttons so Stream links, Remote output URLs and Encoding nodes keep their ⓘ beside the heading.

## 13.26 - 2026-11-11

- Fixed standalone help-button mounting beside section captions, including Remote output URLs, Encoding nodes and Logo URL; buttons no longer drop to separate rows.

## 13.25 - 2026-11-11

- Fixed the final channel-editor grid override that stretched the failback checkbox; the box is now compact and the primary interval title gets a wider column for its ⓘ button.

## 13.24 - 2026-11-11

- Fixed compact channel-editor label rows to grow for wrapped titles, keeping the ⓘ button and input separated.

## 13.23 - 2026-11-11

- Made the channel failback checkbox compact instead of stretching across the row; wrapped help-button placement remains beside the completed title.

## 13.22 - 2026-11-11

- Corrected wrapped help-title layout so the ⓘ button stays beside the completed caption line rather than dropping below it.

## 13.21 - 2026-11-11

- Access-denied logs now distinguish failed username attempts from unauthenticated policy blocks without logging secrets. Wrapped panel help captions keep their ⓘ button below the complete title instead of overlapping the second line or input border.

## 13.20 - 2026-11-10

- Fixed stale public Node/WebPlayer workers retaining the old Node subscription expiry. WebPlayer expiry pages and channel enforcement now refresh the shared Main-owned expiry snapshot on every decision, so renewed Nodes recover without stale Expired pages and actually expired Nodes cannot remain playable through an old worker.

## 13.19 - 2026-11-10

- Node Agent supervisor startup is now single-owner and stale Unix socket/PID coordination is recovered safely. Expiry-renewal restore intents retry after supervisor readiness and remain pending when a source is genuinely unavailable. Remote Nodes can receive the fix through the existing Main API update flow.

## 13.18 - 2026-11-09

- Turned every inline panel help text into a small ⓘ button next to its caption: hover or focus opens a white bubble with a downward pointer above the button (flipping below near the top edge, clamped to the viewport), click/tap pins it until clicked again, an outside press or Escape closes it, and moving into the bubble keeps it open. The original help elements keep their identity so dynamic texts keep updating, value badges such as "Effective now" stay inline, and hidden sections get their button when revealed. Main-only release; Remote Nodes do not need an update.

## 13.17 - 2026-11-07

- Stopped scheduled Main updates from failing on GitHub's anonymous API quota: one tag-scoped metadata request per sequential minor update, optional STREAMFORGE_GITHUB_TOKEN auth, a 5-minute web feed cache with stale fallback and rate-limit messages that explain the reset and token option. The privileged system-control helper is refreshed by the updater. Node Agent bytes unchanged.

## 13.16 - 2026-11-07

- Fixed Node WebPlayer playback failure for duplicate stream ids: media URLs use the unique channel key, grant aliasing is first-claim and resolution prefers exact key matches. One channel of a duplicated stream no longer fails with 403 "Playback path mismatch" while every other path keeps working.

## 13.15 - 2026-11-05

- Removed collapsed footer alignment from the browser-dependent navigation scrollbar geometry. The two footer controls now use a fixed 56px box at x=9.5 inside the bordered 76px sidebar, matching the upper navigation center at x=37.5 on every desktop scrollbar model.

## 13.14 - 2026-11-05

- Shifted only the collapsed footer controls onto the navigation icon center line. Simplified Playlist User notification labels and removed helper copy. Moved timezone from Notification Center to Settings as a worker-loaded global system timezone, preserving the previous notification timezone as fallback.

## 13.13 - 2026-11-05

- Centered collapsed-sidebar footer controls against the full 76px sidebar. Added a prominent independently saved server timezone panel, modal delivery-message viewing, immediate matching Main Update processing state, and grouped Playlist User Email/SMS notification controls.

## 13.12 - 2026-11-05

- Put all Node owner controls in one large-screen row, render saved templates in Test SMS, add targeted expiry-variable tests and optically align collapsed footer glyphs.

## 13.11 - 2026-11-05

- Fixed the updater version gate so installed v13.09 servers can update successfully. Aligned collapsed-sidebar account/logout icons on wide screens.

## 13.10 - 2026-11-05

- Applied the configured server time zone to notification `{expiry_date}` values.

## 13.09 - 2026-11-05

- Added View message to sent/failed delivery history, including subject/body and SMS bodies. Added configurable server default time zone. Forced Node owner information into one line.

## 13.08 - 2026-11-05

- Fixed manual campaign rendering for `expiry_date` and `days_remaining`, including whitespace-tolerant brace syntax. Forced Node owner information into one line.

## 13.07 - 2026-11-05

- Repackaged the complete Notification Center template and verified the `{expiry_date}` / `{days_remaining}` fixes and compact Node owner row in the release archive.

## 13.06 - 2026-11-05

- Fixed `{expiry_date}` and `{days_remaining}` rendering, including double-brace syntax. Compacted Node owner information into one responsive line.

## 13.05 - 2026-11-05

- Added separate short SMS templates for Stream Users and Node Server owners; SMS no longer reuses long email bodies.

## 13.04 - 2026-11-05

- Added Maestro SMS API integration to Notification Center with encrypted API-key storage, sender ID, text/Unicode mode and a test SMS action.
- Added mobile numbers and SMS notification toggles for Stream Users and Node Server owners; automatic SMS expiry reminders use the existing queued delivery and retry worker.
- New bounded updater `scripts/force_update_v1304.sh`; both VERSION files bumped to 13.04. Node Agent runtime bytes are unchanged.

## 13.03 - 2026-11-04

- Kept browser-autofilled form fields on the dark theme: the Notification Center's *Test recipient*, SMTP credentials, *From email* and other autofilled inputs no longer flash a light background that clashed with the panel (global `:-webkit-autofill` override in `app/static/style.css`).
- The updater now republishes the Nginx direct static cache (`/var/cache/streamforge/main-static`) and verifies it byte for byte, so the fixed stylesheet reaches browsers immediately.
- New bounded updater `scripts/force_update_v1303.sh`; both VERSION files bumped to 13.03. Node Agent runtime bytes are unchanged and Remote Nodes do not require an update.

## 13.02 - 2026-11-04

- Added the **Notification Center** (`Notifications` in the sidebar): SMTP delivery settings with an encrypted stored password, a test-email control, separate Stream User and Node Server expiry templates, manual campaigns and a persistent delivery history with retry/resend.
- Added contact fields to both recipient types — Stream Users (`contact_email`, notifications toggle) and Node Servers (`contact_name`, `contact_email`, notifications toggle) — plus an additive, idempotent schema migration and indexes for both tables.
- Automatic expiry reminders for Stream Users **and** Node Server owners at configurable day offsets (default 30/14/7/3/1 plus the expiry day), deduplicated per recipient/expiry/event so restarts never double-send; renewing or changing an expiry cancels queued reminders for the old date.
- Queued, persisted delivery with up to three attempts and back-off, error capture per message, manual retry/resend, and campaign status roll-up. New permissions `notifications.view`, `notifications.send`, `notifications.manage` (Super Admin wildcard included).
- No email is sent until SMTP is configured and delivery is explicitly enabled; the automatic-reminder switch is independent of the master delivery switch. SMTP passwords are encrypted at rest and never rendered back to the browser.
- Hardened the Main Updates confirmation dialog so remote release data is no longer interpolated into an inline JavaScript string.
- New bounded updater `scripts/force_update_v1302.sh`; both VERSION files bumped to 13.02. Node Agent runtime bytes are unchanged and Remote Nodes do not require an update.

## 13.01 - 2026-11-04

- Made **Main Updates** fully responsive across wide desktop, laptop, tablet, phone and extra-small phone widths with dedicated 1100px, 760px, 700px, 430px and 350px breakpoints.
- Tablet layouts now use balanced two-column summary/security cards, phones switch to one column, and extra-small phones simplify card icons and arrange update-job steps in a readable 2×2 grid.
- Added robust wrapping and minimum-width guards for long request IDs, SHA256 details, release names, alerts and action labels; the page is centered at wide widths and prevents horizontal overflow.
- New bounded updater `scripts/force_update_v1301.sh`; both VERSION files bumped to 13.01. Update security and Node Agent runtime bytes are unchanged.

## 13.00 - 2026-11-04

- Redesigned **Main Updates** with a polished release overview, clearer installed/latest/update state cards, verified GitHub release details, security-verification cards, and a structured update-job progress display.
- Fixed the previous mobile CSS issues: alerts now use the shared theme components, controls no longer create oversized header gaps, cards use compact responsive spacing, and release details/actions no longer crowd or clip at narrow widths.
- All page-specific rules are scoped under the Main Updates interface; the authenticated update route, confirmation, SHA256 security model, job refresh and privileged helper are unchanged.
- New bounded updater `scripts/force_update_v1300.sh`; both VERSION files bumped to 13.00. Node Agent runtime bytes are unchanged and Remote Nodes do not require an update.

## 12.99 - 2026-11-04

- Added **Main Updates** to the authenticated web panel. It checks the official `Santuctg/streamforge` GitHub Releases feed, shows installed/latest versions, and queues an update from the browser.
- Added the `main_system.update` permission. The web process never executes root commands: the existing root-owned Main system helper independently re-fetches official release metadata, requires the exact release asset name/URL and GitHub SHA256 digest, downloads with a size limit, verifies SHA256/ZIP integrity, and runs pending official releases sequentially through `streamforge-update`.
- Update progress/result is shown on the page and refreshes while scheduled/downloading/installing. GitHub checks are briefly cached to avoid API-rate exhaustion.
- New updater `scripts/force_update_v1299.sh` installs the privileged verifier and updated sandboxed systemd service. Both VERSION files bumped to 12.99; Node Agent payload is unchanged.

## 12.98 - 2026-11-03

- Fixed Node subscription renewal auto-resume: the saved desired-running set is no longer cleared before channels start. Start failures and channels not yet loaded remain persisted and are retried on the enforcement loop; restore entries are removed only after a start is accepted.
- New updater `scripts/force_update_v1298.sh`; both VERSION files bumped to 12.98. Remote Nodes must be updated from Node Updates for this Node Agent fix.

## 12.97 - 2026-11-03

- Main dashboard Nodes card now says **All nodes online** when no Nodes are offline and **All nodes active** when none are expired. Nonzero offline/expired counts remain aligned left/right; zero Nodes show neither status label.
- New updater `scripts/force_update_v1297.sh`; both VERSION files bumped to 12.97. Node Agent behavior is unchanged.

## 12.96 - 2026-11-03

- Node Web Player expired page now uses the requested copy, shows `Expired: <Bangladesh date/time> · EXPIRED`, and displays the Node logo beneath the notice when configured.
- Main dashboard Node status labels now sit on one row: offline count on the left, expired count on the right. Each label is hidden when its count is zero.
- New updater `scripts/force_update_v1296.sh`; both VERSION files bumped to 12.96.

## 12.95 - 2026-11-03

- Main dashboard Nodes card now shows the total number only (e.g. `9`), followed by the number of offline Nodes and the number of expired Nodes.
- Offline is derived from total minus online; expired is counted separately using Bangladesh wall time.
- New updater `scripts/force_update_v1295.sh`; both VERSION files bumped to 12.95. Node Agent behavior is unchanged.

## 12.94 - 2026-11-03

- When a Node subscription expires, its Web Player root now shows a dedicated **Subscription Expired** page instead of login/player content; it explains that payment and subscription renewal restore access automatically.
- Main Server dashboard now displays the number of expired Nodes in the Nodes card, calculated using Bangladesh wall time.
- New updater `scripts/force_update_v1294.sh` includes the dashboard and Node Web Player changes; both VERSION files bumped to 12.94. Remote Nodes must be updated from Node Updates for their Web Player expiry screen.

## 12.93 - 2026-11-03

- Expired Node expiry date/time is now strongly red on both the Main Nodes page and the Node dashboard.
- Centered the Node dashboard payment notice and changed its heading/body to the requested wording: “💰 Payment Pending” and “The subscription has expired, so all channels are stopped until the payment is made. Once it is paid, everything resumes automatically.”
- New updater `scripts/force_update_v1293.sh` stages the Nodes template and Node Agent; both VERSION files bumped to 12.93. Remote Nodes must be updated from Node Updates for the dashboard notice.

## 12.92 - 2026-11-03

- Node expiry edit now allows intentional backdating (removed the browser `min` restriction); past values remain marked expired and keep the service lock enforced.
- Replaced the raw expired-service warning on the Node dashboard with a styled, payment-focused notice explaining that channels are off pending payment and will resume automatically after Main extends the subscription. Added visible spacing between the notice and dashboard cards.
- New updater `scripts/force_update_v1292.sh` stages the Main node form and Node Agent; both VERSION files bumped to 12.92. Remote Nodes must be updated from Node Updates for the notice change.

## 12.91 - 2026-11-03

- **Fixed the node expiry timezone semantics**: the expiry time typed on the Main form is now treated as **Bangladesh wall time everywhere**. Previously a naive entry (the form's default) was read as UTC, so `12:12 PM` displayed as `6:12 PM` on the Node dashboard and enforcement fired 6 hours off (marker `STREAMFORGE_NODE_EXPIRY_BD_WALL_TIME_V1291`).
- The Node Agent now interprets **naive heartbeat values as Asia/Dhaka (+06:00) local time** in both the dashboard card and the expiry lock decision; explicit UTC-offset values remain epoch-accurate. Main's Nodes-list "Expires" badge compares BD wall time and is labelled `(BD)`; the Node form help text states BD time.
- Existing expiry entries saved before v12.91 were stored under the UTC interpretation — **set the date again** once updated.
- New updater `scripts/force_update_v1291.sh` (repoints `scripts/update.sh`); Node Agent bundle changed — **Remote Nodes must be updated from Node Updates** for the fix.
- Both VERSION files bumped to 12.91.

## 12.90 - 2026-11-03

- Node subscription expiry is now **enforced**: once the date passes, the Node's control worker stops every desired-running channel (`desired_running=False` persisted + FFmpeg killed) and refuses new starts until Main extends or clears the expiry — then the pre-lock channels resume automatically (marker `STREAMFORGE_NODE_EXPIRY_SERVICE_LOCK_V1290`).
- Expiry card on the Node dashboard now shows **Bangladesh time (UTC+6) in 12-hour format** (`02 Dec 2026 01:40 AM`) instead of UTC; a red banner appears while the service lock is active.
- Enforcement runs on a 30-second loop (env-tunable `STREAMFORGE_NODE_EXPIRY_ENFORCEMENT_INTERVAL_SECONDS`), on every heartbeat that changes the expiry, and across Node restarts (restore set persisted in node operations settings). Corrupt/invalid expiry values never lock the service. Public playback workers never enforce; the `start()` guard engages before the supervisor-delegation branch so panel/API starts cannot bypass the lock.
- New updater `scripts/force_update_v1290.sh` (repoints `scripts/update.sh`); Node Agent bundle changed again — **Remote Nodes must be updated from Node Updates** to gain enforcement.
- Both VERSION files bumped to 12.90.

## 12.89 - 2026-11-03

- Node subscription expiry date is now shown on the **Node's own dashboard** (`/panel`) as a "Subscription expires" card — red with an EXPIRED marker past the date, green "Unlimited" when no expiry is set.
- Main sends `node_expires_at` in the Node heartbeat response; the Node Agent persists it and renders the card (marker `STREAMFORGE_NODE_EXPIRY_HEARTBEAT_V1289`).
- New updater `scripts/force_update_v1289.sh` (repoints `scripts/update.sh`); updates the bundled Node Agent payload and, when present, a local Node Agent at `/opt/streamforge-node`.
- **Remote Nodes must be updated from Node Updates** to display the expiry card on their dashboards (the Node Agent payload changed for the first time since v12.83).
- Both VERSION files bumped to 12.89.

## 12.88 - 2026-11-03

- Removed node expiry from Main Panel dashboard (`/`) — expiry date now shows only on Node pages (`/nodes/{id}`) as requested.
- Both VERSION files bumped to 12.88. Remote Node runtime payload unchanged — Remote Nodes need no update for v12.88.

## 12.87 - 2026-11-03

- Node expiry date shown on Main Panel dashboard (`/`) with expired node count and color-coded status.
- Both VERSION files bumped to 12.87. Remote Node runtime payload unchanged — Remote Nodes need no update for v12.87.

## 12.86 - 2026-11-03

- Node expiry date now shown on Nodes dashboard (`/nodes`) card with 'Expired' badge for past-due nodes and 'Unlimited' for nodes without expiry.
- Fixed `force_update_v1285.sh` to stage updated templates from package source (`$SRC`) instead of installed root (`$ROOT`), so expiry date field appears correctly after update.
- Fixed `scripts/update.sh` to point to `force_update_v1286.sh` (was incorrectly pointing to v12.85 updater).
- Fixed naive/aware datetime comparison for the SQLite `expires_at` value on the Nodes dashboard (`datetime.utcnow()` instead of timezone-aware `now`).
- Both VERSION files bumped to 12.86. Remote Node runtime payload unchanged — Remote Nodes need no update for v12.86.

## 12.85 - 2026-11-03

- Added Node subscription expiry date (`expires_at`) to Remote Nodes. Playback/channel access is blocked after expiry until the date is extended or cleared (unlimited). Leave empty for no expiry.
- Node form (`/nodes/new`, `/nodes/{id}/edit`) gains an expiry date-time picker with shortcut buttons: `+1 month`, `+2 months`, `Unlimited (∞)`.
- New migration script `scripts/migrate_node_expiry.py` adds the `expires_at` column to the `nodes` table (idempotent).
- Both VERSION files bumped to 12.85. Remote Node runtime payload unchanged — Remote Nodes need no update for v12.85.
- Fixed `scripts/update.sh` to point to `scripts/force_update_v1285.sh` (was incorrectly pointing to v12.84 updater). New staged updater runs `scripts/migrate_node_expiry.py` live migration and restarts only Main workers.

## 12.84 - 2026-10-30

- Fixed login landing pages for roles that can access only Settings or Backups; their existing permissions now lead to the allowed page instead of `/no-access`.
- Added auth audit events for successful role creation, editing and deletion, including actor, role identity and granted/revoked permission keys. Denied changes do not log success; hierarchy, built-in and own-role protections remain enforced.
- Clarified the legacy vault-management permission: it does not control automatic encrypted password saving. Password reveal remains a separate, reauthenticated permission. No password-storage behavior was changed.
- Corrected installation versions, incomplete rollback guidance, TLS/realip/signing-key instructions and testing documentation; added documentation and Role Manager regression tests.
- The v12.84 updater requires v12.83 or v12.84, preflights exact-text role patches over two runtime files, backs up all touched files, preserves installed file metadata and rolls back failed writes/restarts. It restarts only the two Main HTTP services; Nginx, the channel supervisor, env and database schema are unchanged. Remote Node runtime is byte-identical to v12.83 and needs no update.

## 12.83 - 2026-10-27

- Client identity is resolved from the **rightmost** `X-Forwarded-For` hop — the one local Nginx appends from the real socket — everywhere a viewer IP is derived (`app/client_ip.py`, shared by the access policy, load balancer, viewer tracking, session fingerprints and the login guard), gated on the loopback backend bind that Uvicorn cannot rewrite. Previously the leftmost (client-written) hop was trusted, so any viewer could choose any source IP with one forged header.
- `deploy/nginx.conf` gains a real_ip block (`set_real_ip_from 127.0.0.1/::1`, `real_ip_recursive on`) so a forged chain can never move `$remote_addr`; CDN/internal-proxy ranges are added explicitly by the operator, and the updater patches the installed site, tests it with `nginx -t` and reloads it.
- The per-node "Verify HTTPS certificate" option is removed: HTTPS to Remote Nodes always verifies its certificate (model column, form fields and the unverified SSL context dropped). Nodes with self-signed certificates must install trusted ones or use `http://` URLs.
- A weak or missing `STREAMFORGE_SECRET_KEY` now refuses to start the Panel (the hard failure v12.82 promised for a later release), with the exact fix in the message; the updater repairs the key in `/etc/streamforge.env` (backed up) before restarting, so upgrades never fail to boot.
- The v12.82 patch round-trip and updater test read their fixtures from the release tags, and the batch-2A secret-key check follows the new hard-fail contract.
- Updater: `scripts/force_update_v1283.sh` patches the Main sources in place from `scripts/apply_v1283_hardening.json` (11 files) after a staged preflight, adds the new `app/client_ip.py` module, repairs the signing key, applies and reloads the Nginx real_ip site patch inside the rollback window, and restarts the Main HTTP workers; the channel supervisor and live streams keep running, and the unchanged Node Agent payload is only restored when an installed tree carries something else.

## 12.82 - 2026-10-27

- Hardened the Panel's widest trust boundaries, in two reviewed batches with a regression test and a revert-based negative proof for every change.
- Channel sources must be transport URLs: `ensure_safe_input_target()` refuses `file:`, `concat:`, `lavfi:`, `ftp:` and local paths at save time and inside `build_input_args()`, so no FFmpeg or ffprobe path can read host files or play them out as a stream (`STREAMFORGE_SOURCE_SCHEME_ALLOWLIST` / `STREAMFORGE_ALLOW_LOCAL_FILE_SOURCES` opt in).
- Killing a viewer session now needs the new `sessions.kill` permission instead of `dashboard.view`, and the Panel route writes an audit entry; roles that had `dashboard.view` before the split receive the permission once, under an `AppSetting` marker, so deliberate revocations survive restarts.
- Node API token rotation: **Nodes → Regenerate API token** mints a token, pushes it to `/etc/streamforge-node.env` over saved SSH (charset-restricted, verified, agent restarted) and stores it only after the push succeeds (`STREAMFORGE_V1282_NODE_TOKEN_ROTATE_V1`).
- Backup archives keep the runtime `STREAMFORGE_*` environment snapshot by default (a disaster restore needs those keys); `STREAMFORGE_BACKUP_INCLUDE_ENV=0` drops it and the manifest records the exclusion.
- A missing or published development `STREAMFORGE_SECRET_KEY` is reported as an error at startup; the hard failure is deferred to a later release.
- Fresh installs now generate `STREAMFORGE_PANEL_PASSWORD_VAULT_KEY`, and the four password-bearing writes fall back to hash-only storage when it is absent, so the installation default password can always be rotated.
- smbclient destinations and credentials are validated before use: `;`/`!` in a destination or a line break in the `-A` file can no longer execute commands on the Main Server during a backup.
- Fixed `permission_denied_handler` rendering the access-denied page without the shared template context (a 500 instead of the 403 page) and the XSS reachable through category, role, node and update confirmation dialogs.
- Updater: `scripts/force_update_v1282.sh` patches the Main sources in place from `scripts/apply_v1282_hardening.json` after a staged preflight, restarts the Main HTTP workers so the new code is live, keeps the channel supervisor and Nginx running, and restores the unchanged Node Agent payload only when an installed tree carries something else.

## 12.48 - 2026-10-14

- Fixed the inherited updater's relay-map preflight to accept the composite `$status:$uri` map introduced for path-aware log filtering.
- Quoted the relay-segment regex map key containing `{32}`, preventing Nginx from failing with `unexpected "{"` during update.
- Applied the same quoted regex to the fresh Nginx template and Main runtime access-policy generator so future config regeneration retains the fix.
- Added v12.48 regression checks for updater guard compatibility, regex quoting, fallback conditional logging, and bounded hourly log rotation.
- Replaces the failed v12.47 deployment attempt; the v12.47 ZIP should not be retried.

## 12.47 - 2026-10-14

- Fixed Nginx internal relay fallback logging so the status-aware access-log policy is applied after `try_files` redirects to `@streamforge_relay_fallback`.
- Suppressed high-volume stale relay segment 404s and relay client-disconnect 499s while retaining playlist 503s and other diagnostic errors.
- Added hourly logrotate execution so the 100 MB max-size rule is checked throughout the day; retained seven compressed rotations.
- Removed the known-bad HLS.js GitHub release URL that caused a harmless 404 before CDN fallback.
- Preserved rollback safety, additive schema ordering, and Main/Node dashboard date/time parity.

## 12.39 - 2026-10-09

- Fixed the Remote Node TLS `PathChanged=` watcher causing PID 1/systemd to consume ~100% CPU after `tls-reconcile.request` was deleted by the reconciler.
- Kept `/var/lib/streamforge-node/tls-reconcile.request` persistent across successful TLS reconciliation and seeded it before enabling the path watcher during install/update.
- Guarded repeated `systemctl enable nginx` calls with `systemctl is-enabled --quiet nginx`, avoiding unnecessary systemd manager work during periodic TLS reconciliation.
- Added a regression guard and GitHub Actions check for the persistent request-file behavior and conditional Nginx enable logic.
- Production validation on the affected Node showed PID 1 current CPU at 0.00%, ~95% CPU idle, active TLS path/timer units, successful request-triggered TLS reconciliation, and the request file remaining present afterward.
- Added `force_update_v1239.sh` as an update-safe bridge around the proven v12.36 full updater so existing installations can deploy the v12.39 source and Node Agent fixes through `streamforge-update`.

## 12.36 - 2026-09-19

- Reduced measured post-auth WebPlayer channel-switch latency by moving the normal local-HLS profile from a two-segment to a one-segment live edge.
- Kept safe fragment prefetch and retained the two-segment stable fallback profile for recovery from unstable sources.
- This change targets browser startup buffering only; v12.35 server timings remain sub-10ms and authorization behavior is unchanged.
- Advanced the release updater to `force_update_v1236.sh`.

## 12.35 - 2026-09-19

- Eliminated the measured ~1.32-second `sf-grant` cold-channel delay by embedding the WebPlayer's already-authorized online channel references in its session playback grant.
- Cold media authorization now resolves stream ID or channel key with a direct grant-map lookup instead of rebuilding, enriching and sorting the user's complete channel catalogue.
- Kept IP binding, user validity, channel assignment, fresh-HLS validation and connection limits enforced; older and external playback grants retain the compatible catalogue-validation fallback.
- Advanced the release updater to `force_update_v1235.sh`.

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
