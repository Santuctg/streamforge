# 🛠️ Administration guide

A tour of the Main Panel, menu by menu. What you see depends on your role's
permissions (see [Panel users & roles](#-panel-users--roles)).

| Menu | Purpose |
| --- | --- |
| **Overview** | Server resources, live history charts (v12.80+) with current values beside each legend (v12.78+), running channels and live viewers |
| **Channels** | Create, start/stop and inspect channels |
| **Nodes** · **Node Updates** | Remote Nodes and their Node Agent versions |
| **Categories** · **Import streams** | Organise channels; bulk-add from M3U/CSV |
| **Users & Playlists** | Viewer/restreamer accounts and their playlists |
| **Logs** | Activity, access, client and system logs |
| **Panel access** | IP/ASN rules for who may reach the Panel |
| **Panel users** · **Roles & Permissions** | Staff accounts and what they may do |
| **Backups** · **Settings** | Backup targets and restore; branding and global settings |

---

## 📺 Channels & encoding

Each channel is one FFmpeg process owned by the
`streamforge-channel-supervisor` service, producing HLS under
`/var/lib/streamforge/hls`.

**Sources**

- Add one or more source URLs in priority order: HTTP/HTTPS, HLS (`.m3u8`),
  MPEG-TS, or a **YouTube Live** URL.
- Extra HTTP headers can be appended IPTV-style:
  `http://host/stream.m3u8|User-Agent=...&Referer=...`.
- **Scan** probes a source from the server (ffprobe) and shows its streams;
  for multi-program MPEG-TS you can pick the program/service to use.
- **Allowed schemes** (v12.82+): a source must be a transport URL
  (`http`, `https`, `rtmp`, `rtsp`, `rtp`, `udp`, `tcp`, `srt`, `mmsh`,
  `ws`, …). `file:`, `concat:`, `lavfi:`, `ftp:` and plain local paths are
  refused at save time and again at run time, because ffprobe and the encoder
  would open host files and could stream them out. Extra schemes go into
  `STREAMFORGE_SOURCE_SCHEME_ALLOWLIST` (comma-separated); local files need
  `STREAMFORGE_ALLOW_LOCAL_FILE_SOURCES=1`. Restart the service after
  changing either.
- YouTube Live is resolved with yt-dlp. If YouTube asks to "confirm you're not
  a bot", upload a fresh exported `cookies.txt` in **Settings**.

**Failover & recovery**

- When a source fails, the channel moves to the next one quickly.
- After every source has failed, the channel shows **Waiting** and retries the
  whole list every **60 seconds**.
- With **failback** enabled, a channel running on a backup source periodically
  checks the primary and returns to it when it is healthy again.
- A channel whose output stalls is restarted automatically when
  **auto restart** is on.

**Encoding**

| Video option | Encoders |
| --- | --- |
| Copy | Pass the source video through unchanged |
| CPU | `libx264` (H.264), `libx265` (HEVC) |
| NVIDIA | `h264_nvenc`, `hevc_nvenc` |
| Intel | `h264_qsv`, `hevc_qsv` |
| VAAPI | `h264_vaapi`, `hevc_vaapi` |
| Auto | Picks the first working hardware encoder, otherwise CPU |

Audio can be copied or encoded (AAC, AC-3, E-AC-3, MP2, MP3, Opus, FLAC,
Vorbis, PCM). Resolution, frame rate, bitrates and the HLS segment length are
set per channel.

**Monitoring**: the channel list shows status, uptime, bitrate, resolution and
codecs. **Stream logs** lists start/stop/restart/failure events; **Info**
shows a full ffprobe of the input and the produced output.

---

## 🗂️ Categories & import

- **Categories** group channels in playlists and apps; their order is used in
  generated playlists.
- **Import streams** bulk-creates channels from an **M3U** playlist or **CSV**
  file (format detected automatically) and can create missing categories.

---

## 🛰️ Remote Nodes

A Remote Node relays Main channels and serves viewers near it.

**Add a Node** (**Nodes**):

- **Add manually** — store the Node's URL(s) and token for an agent you
  installed yourself.
- **Auto install remote node** — give SSH host, port, user and password; the
  Panel uploads and installs the Node Agent, verifies it and synchronises it.
  You may save the SSH password (encrypted) for later API-update fallback.
  The server's SSH host key is remembered on first contact; a different key
  later is refused (v12.76+). After really reinstalling a server, remove the
  old key with the `ssh-keygen -R` command shown in the error.

**How Nodes get channels**: each Node pulls a channel from the Main relay
(`/relay/<relay-key>/<channel>/index.m3u8`) only for channels assigned to it.

**Node Panel**: each Node has its own Panel; logins are verified live against
the Main Server and follow the same login IP block policy and password rules.
From v12.77 Node Panel pages refuse to be framed by other sites (update Nodes
from **Node Updates**). From v12.81 the Node dashboard charts show current
values beside their legends and update live, like the Main Overview.

**Updating Nodes**: after upgrading Main, open **Node Updates**, update the
Nodes (individually or in bulk) and confirm each reports the new version.
**Update this Node** uses the Node API and is the normal choice. **Update over
SSH** is for recovery or a port change: it reinstalls the Node Agent over SSH,
keeping its token, slug, Panel URL and DNS setting, and restarts the Node
briefly — that is what its confirmation dialog asks about.

**Regenerate API token** (v12.82+, needs `nodes.edit`): mints a fresh Node
API token, rewrites `STREAMFORGE_NODE_TOKEN` in the Node's
`/etc/streamforge-node.env` over saved SSH and restarts the Node Agent, then
stores the new value in Main. The panel keeps the previous token if the push
fails, so Main and the Node never disagree. Rotation is logged. Nodes without
saved SSH host/password cannot be rotated from here — edit the token by hand
on the Node and paste the same value into **Edit Node**.

**Maintenance**: a Node in maintenance is skipped when choosing where to send
viewers.

**HTTPS certificates (v12.83+)**: Remote Node HTTPS always verifies the
certificate; the bypass checkbox has been removed. Use a trusted certificate
matching the Node URL. HTTP sends control traffic without TLS and should only
be considered on a trusted network. The v12.83 agent payload is unchanged from
v12.82, so this release does not require updating Remote Nodes.

---

## 👥 Users & playlists

| Account type | Use |
| --- | --- |
| **Viewer** | Watches via M3U playlist, Xtream login or Web Player |
| **Restreamer** | Pulls streams for re-broadcast; only from its **allowed source IPs** |

Per account you can set: channels or a **playlist profile**, expiry date,
maximum simultaneous connections, Xtream username/password (stored hashed),
the Nodes it may use, and whether it is **load-balanced** or fixed to one
Node.

**Playlist profiles** are reusable channel lists (or "all enabled channels")
with their own channel and category order.

**Where viewers are sent**: StreamForge picks a ready Node assigned to the
channel and allowed for the client — preferring a Node pinned to the client's
IP prefix, then the user's fixed Node, then the remaining candidates.

The **Viewer sessions** page shows live sessions and lets you end one.

---

## 🛡️ Access control

- **Panel access** — allow/deny Panel and API access by IP/CIDR and ASN. If
  you lock yourself out: `sudo streamforge resetpanelaccess`.
- **Proxies (v12.83+)** — Main resolves the rightmost Nginx-appended client IP.
  If a CDN/internal proxy fronts Nginx, configure only its exact trusted ranges
  in `set_real_ip_from`, keep `real_ip_recursive on`, and test/reload Nginx.
  Never trust every address. See [v12.83 upgrade notes](INSTALLATION.md#-after-upgrading-to-v1283).
- **Node playback policy** — per-Node IP/ASN rules for who may play from it.
- **GeoIP** — country/ASN lookups, configured per server under **Nodes →
  (server) → GeoIP**: IPinfo, MaxMind, or IPinfo-first automatic fallback.
  MaxMind databases are refreshed by `streamforge-geoip-update.timer`.
- **Login IP block** — 5 failed Panel logins from one IP within 15 minutes
  block that IP for 3 hours on Main and all Nodes. **Panel users → Security ·
  Panel Login · Blocked IPs** lists them; a Super Admin can unblock after
  reauthenticating.
- **Xtream login limits** (v12.76+) — Xtream API, M3U `get.php`, Xtream
  stream URLs and Web Player logins: 10 failures for one username from one IP
  in 10 minutes block that IP + username for 15 minutes; 100 failures across
  usernames block the IP for 15 minutes. Attempts refused while blocked are
  not counted, so viewers sharing a carrier-grade NAT IP are not all blocked
  by one misconfigured app.

---

## 🔐 Panel users & roles

- **Roles & Permissions** — build roles from fine-grained permissions
  (`channels.*`, `nodes.*`, `stream_users.*`, `logs.*`, `backups.*`,
  `settings.edit`, …); these are permission families, not wildcard values you
  can assign. **Super Admin** is built in and unrestricted.
- Non-Super Admins can grant only permissions they already hold and cannot
  edit/delete their own assigned role or manage roles above their permission
  ceiling. Built-in roles are locked; assigned roles cannot be deleted until
  their accounts have been moved. Action permissions include required view
  permissions automatically.
- Role create/update/delete actions are recorded in the auth audit log;
  updates include granted/revoked permissions and the previous role name.
- **Killing a viewer session** (**Live sessions → Kill**) requires
  `sessions.kill` (v12.82+), not just `dashboard.view`. Roles that already
  had `dashboard.view` were granted it automatically once, so nothing stops
  working on upgrade; remove it from read-only roles if they should not be
  able to disconnect viewers. Every kill is recorded in the audit log.
- **Password policy** (v12.77+) — Panel passwords need 12+ characters and may
  not be `ChangeMe123!` or the `STREAMFORGE_ADMIN_PASSWORD` seed. Signing in
  with a weaker password works once, but every Panel page leads to
  **Change password** until it is replaced.
- **Panel users** — staff accounts with a role; Main Panel access can be
  switched off per user. Changing or resetting a password signs that user out
  everywhere else (v12.76+); changing your own keeps your current browser
  signed in.
- **Password vault** — when a staff password is set or changed, an encrypted,
  administrator-recoverable copy is kept on the Main Server. Authorised
  viewers can reveal it only after reauthentication; every reveal is logged.
  The vault is keyed by `STREAMFORGE_PANEL_PASSWORD_VAULT_KEY`, generated by
  the installer (since v12.82). If that key is ever missing the Panel falls
  back to hash-only passwords instead of refusing the save, logs a warning at
  startup, and only **Show saved password** is unavailable.
- **Vault permissions** — `panel_users.password_reveal` controls reveal,
  subject to reauthentication and target hierarchy. The historical
  `panel_users.password_vault_manage` flag is retained for compatibility; it
  does **not** disable automatic encrypted saving. Only Super Admins may grant
  these sensitive flags. Granting ordinary create/edit access already allows
  password creation/reset; it does not grant password reveal.
- Forgot the admin password? `sudo streamforge resetuser`.

---

## 💾 Backups & restore

- **Targets**: local disk (`/var/lib/streamforge/backups`), **SMB** share,
  **SSH** server or **Google Drive**, each with its own schedule (every N
  hours or at a set time). The newest 20 archives are kept by default.
  Target passwords are stored encrypted. SSH targets use the same remembered
  host keys as Node SSH.
- **Backup encryption** (v12.76+) — set a passphrase (12+ characters) in
  **Backups → Backup encryption**. Every archive sent to a target is then
  encrypted (AES-256-GCM); Main's own retained copies stay readable on this
  server. **Keep the passphrase somewhere safe**: it is required to restore an
  encrypted backup on another server. Until it is set, targets receive
  unencrypted archives and the page shows a warning.
- **What is inside an archive**: the database plus the logo assets, and a
  `etc/streamforge.env` member holding the service's `STREAMFORGE_*`
  values (v12.82 keeps this by default — a restore on a fresh server needs
  those keys to decrypt saved Node SSH passwords, Panel passwords and the
  backup passphrase). Set `STREAMFORGE_BACKUP_INCLUDE_ENV=0` in
  `/etc/streamforge.env` to leave the snapshot out; the manifest then says
  so, and you must re-enter/re-key every secret after such a restore.
- **Saved backups** can be downloaded, restored or deleted from
  **Backups → Local files** (and from remote targets).
- **Restore** accepts a saved archive or an uploaded `.tar.gz`. Encrypted
  archives are decrypted first with the saved passphrase or the one typed in
  the upload form. The archive is
  validated (expected files only, no links or device files), a safety backup
  is taken first, and the current server's Panel URL is preserved so you are
  not locked out. Main restarts during the restore.
- Logos only: `sudo streamforge restorelogos /absolute/path/to/backup.tar.gz`.

---

## ⚙️ Settings

- **Branding** — name, subtitle, logo and favicon used by the Panel and Web
  Player.
- **Limits & retention** — total Main connection limit, viewer session
  timeout, client session reset, log and metrics retention, metrics sampling
  interval and network interfaces shown on the dashboard.
- **YouTube cookies** — upload/remove the `cookies.txt` used for YouTube Live.

**Domain / access URLs** are set on the **Main Server** entry under **Nodes**:
Panel and Playlist URLs (HTTP/HTTPS, ports, path aliases) and the strict URL
lock. If a wrong domain locks you out:
`sudo streamforge resetdomain [http://IP-or-domain/optional-path]`.
