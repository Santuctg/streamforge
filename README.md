# StreamForge
### Self-hosted Live TV Encoding, Relay & Multi-Server Distribution Panel

[![Release](https://img.shields.io/github/v/release/Santuctg/streamforge?label=Latest%20Release&style=for-the-badge)](https://github.com/Santuctg/streamforge/releases/latest)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%2F%20Ubuntu-4B5563?style=for-the-badge&logo=ubuntu)](docs/INSTALLATION.md)
[![Backend](https://img.shields.io/badge/Backend-FastAPI-009688?style=for-the-badge&logo=fastapi)](docs/ARCHITECTURE.md)
[![Encoder](https://img.shields.io/badge/Encoder-FFmpeg-007808?style=for-the-badge&logo=ffmpeg)](docs/ADMINISTRATION.md#-channels--encoding)
[![Docs](https://img.shields.io/badge/Documentation-Read%20Guides-2563EB?style=for-the-badge&logo=readthedocs)](docs/ADMINISTRATION.md)

> **StreamForge** is a self-hosted control panel for running live TV channels: it ingests HTTP/HLS, MPEG-TS and YouTube Live sources, encodes them with FFmpeg into HLS, and delivers them to viewers through M3U playlists, an Xtream-compatible API and a built-in Web Player.
>
> **Typical use:** One **Main Server** encodes every channel once; several **Remote Nodes** in other data centres relay those channels and serve the viewers, with per-user connection limits, IP/ASN access policies and automatic node selection.

**v13.89:** The Node channels toolbar is tidier — title with Add button on one row, filters and count inline on the next, bulk actions right-aligned.
>\n> **v13.88:** Hotfix — the Channels page no longer returns Internal Server Error after v13.87 (the link number now reads from the channel object, not a JSON-only context key).
>\n> **v13.87:** The Channels table shows the active input link number next to the local server name (Main Server #1 / #2), including live failover updates.
>\n> **v13.86:** The collapsed-sidebar scroll runway is smaller still (120px → 40px) — minimal blank space, Settings still moves up when scrolling.
>\n> **v13.85:** The collapsed-sidebar scroll runway is smaller (360px → 120px) — Settings still moves up when scrolling, without a large empty gap.
>\n> **v13.84:** Scrolling the collapsed sidebar now brings every icon upward, including Settings — layout and spacing are unchanged.
>\n> **v13.83:** Reverted the compact collapsed-sidebar spacing — the rail keeps the v13.81 scrolling fix and its original icon spacing.
>\n> **v13.82:** The collapsed sidebar is compact — all icons and the bottom actions fit on one screen instead of being pushed out of view.
>\n> **v13.81:** The collapsed sidebar icon rail scrolls properly — every icon and the bottom actions are reachable again.
>\n> **v13.80:** The user chip stays beside the page heading even with large text or zoom — the subtitle wraps inside its own column instead of pushing the chip to a second line.
>\n> **v13.79:** The topbar no longer bleeds past the screen edges on phones — heading, subtitle and name chip stay inside the viewport at every width.
>\n> **v13.78:** The Providers page now uses real centered cards on phones — every value keeps its label and the full URL shows on one line instead of a squeezed 63px column.
>\n> **v13.77:** The topbar is responsive on phones — the heading and name chip wrap instead of colliding, the heading shrinks and the subtitle wraps.
>
> **v13.76:** Fixed the Providers card view for real — v13.75 accidentally shipped the old flex override that dropped the labels and squeezed the columns; the kept label+value card pattern is centered now.
>
> **v13.75:** The Providers Alt-running column header is centered like its values (the header th had missed the center class, so the values looked right-shifted next to it).
>
> **v13.74:** Hotfix — panel user edit/save worked again (the v13.62 route insertion had dropped the save handler's redirect tail, so the browser landed on a JSON viewer).
>
> **v13.73:** On narrow screens the Providers card view centers its labels and values (the global card rule left-aligned them).
>
> **v13.72:** Every Panel page is now served with Cache-Control no-store — a stale cached copy can no longer show an old layout after an update; hard reloads are no longer required for Panel pages.
>
> **v13.71:** The Providers page keeps its address-bar route across reloads (panel navigation now knows /providers) and the Alt running column is centered like the rest.
>
> **v13.70:** The Providers table cells are center-aligned — every column shows its value in the middle.
>
> **v13.69:** Providers count columns renamed (Total, Pri, Running, Alt, Running) and the provider edit page section titles got proper padding and separators.
>
> **v13.68:** The Providers page now shows per provider: Total channels, Primary channels, Pri running (live on the primary input), Backup channels and Alt running (live after failing over to this provider's backup URL).
>
> **v13.67:** New Providers page — add upstream providers with their URLs and see, per provider, how many channels use it as primary input (with a running count) and how many keep it as a backup input.
>
> **v13.66:** The Panel topbar is now a real header bar on every page — full-bleed panel background, bottom border and soft shadow — instead of a floating text line.
>
> **v13.65:** The Admin Users row buttons sit on one line again — the Actions cell had become a flex container instead of a table cell, which snapped the row borders; the cell is a table cell now and the table scrolls horizontally instead of overflowing.
>
> **v13.64:** The Admin Users table wraps its action buttons inside the row again — the force-password button had pushed the Actions cell past the table border, breaking the row lines.
>
> **v13.63:** Hotfix — v13.62 referenced an unimported name inside the render path, so every Panel page returned 500; the import is restored.
>
> **v13.62:** Password policy is now operator-configurable — Settings has a "Minimum password length" option (12 by default, enforced on Panel users, viewer Xtream passwords and every Remote Node panel) and the Admin Users page gains a per-user "Force password change" button plus a badge.
>
> **v13.61:** The Channels-page HLS preview ("H") button now requires `channels.playback` on the Node panel too — a role without "Open direct HTTP playback" no longer sees it.
>
> **v13.60:** Node panel permission audit — legacy permission strings (`logs.manage`, `stream_users.kill`, `categories.manage`) replaced with the real Main catalog permissions, and channel Info / HLS preview now require the same permissions as the Main panel.
>
> **v13.59:** Fixed "Main Panel HTTP 403: Access denied" when a Node panel user with Edit channels changed a shared channel's HLS segment — Main now exempts the Node HLS override endpoint from the browser IP/ASN panel whitelist like the other Node control endpoints.
>
> **v13.58:** The panel Dark/Light switch now sits with the other buttons (sidebar button row on both panels; compact topbar glyph on phones), and the Node Web Player keeps the LIVE pill and the switch together.
>
> **v13.57:** The Node Settings page and sidebar are now fully light-themed, and the Web Player watch screen shows its Dark/Light switch next to the live status pill.
>
> **v13.56:** Fixed light-theme dropdown option lists staying dark on Main and Node — the light select rules had an impossible `:root html` selector (dead since v13.48) and lost the specificity fight with the dark rule's id guard.
>
> **v13.55:** Fixed the Node Web Player channel-open regression from v13.53 (the watch route had been bound to the new CSS helper, so clicks returned CSS text) and added a route-wiring guard to the release contracts.
>
> **v13.54:** The Main Web Player watch screen loads self-hosted HLS.js from the Nginx `/static/` cache at host root — the old playlist-alias-prefixed URL was silently dropped, forcing playback onto third-party CDNs — and light-theme dropdown option lists now open light on Main and Node.
>
> **v13.53:** The Node Agent now follows the same per-device Dark/Light switch everywhere — the panel light overrides are global (not Categories-only), the light checkbox tick stays visible, and the Node panel login, public Web Player (login/home/watch), Main-disconnected and expired pages carry the theme bootstrap and toggle.

**v13.52:** The Main Web Player watch screen now follows the same per-device Dark/Light switch as the home screen — `player.html` reads the saved theme in `<head>`, shows a toggle and flips its own dark panels to light. Dark stays the default, the choice persists in the browser, and the v13.51 template-compile guard now also renders the watch screen.

> **v12.83 upgrade notes:** HTTPS connections to Remote Nodes now always verify
> certificates; replace self-signed or name-mismatched certificates before
> upgrading, or use `http://` where appropriate. If a CDN or internal proxy is
> in front of Nginx, add only its exact ranges to `set_real_ip_from`—never
> `0.0.0.0/0`. The updater repairs a weak `STREAMFORGE_SECRET_KEY` entry in an
> existing env file before restarting; missing/custom service configuration
> needs operator repair. See the [upgrade guide](docs/INSTALLATION.md#-after-upgrading-to-v1283).

---

## ✨ What can you do with StreamForge?

| Capability | What it lets you do |
| --- | --- |
| 📺 **Channels & encoding** | Run each channel as an FFmpeg process: copy or transcode to H.264/HEVC with CPU (`libx264`/`libx265`), NVIDIA NVENC, Intel QSV or VAAPI. |
| 🔁 **Source failover** | Give a channel several inputs; StreamForge moves to the next source on failure, retries the full list every 60 s, and can fail back to the primary. |
| ▶️ **YouTube Live & MPEG-TS** | Resolve YouTube Live URLs (optional cookies file) and pick a specific program/service from multi-program MPEG-TS inputs. |
| 🛰️ **Remote Nodes** | Deploy the Node Agent over SSH; nodes pull each channel from the Main relay and serve viewers locally. |
| ⚖️ **Node selection** | Route each viewer to an assigned, ready node — pinned by client IP prefix, fixed per user, or load-balanced — and skip nodes in maintenance. |
| 📃 **Playlists & Xtream API** | Issue M3U playlists, Xtream `player_api.php` / `get.php` logins and direct stream URLs for IPTV apps. |
| 🌐 **Web Player** | Branded browser player with login, quick-login and an optional Android APK download. |
| 👥 **Users & playlists** | Viewer and restreamer accounts with channel playlists, expiry dates, connection limits and allowed source IPs. |
| 🔔 **Notifications** | Email expiry reminders and manual service-discontinuation notices to Stream Users **and** Node Server owners, queued with retries and a full delivery log. |
| 🛡️ **Access control** | IP/CIDR and ASN rules for Panel and playback, GeoIP lookups, a 5-failure / 3-hour Panel login IP block shared by Main and Nodes, and Xtream login limits. |
| 🔐 **Panel users & roles** | Role-based permissions, Super Admin, encrypted password vault with audited reveal. |
| 📊 **Monitoring** | Live CPU/RAM/network dashboard, channel bitrate/uptime, viewer sessions with kill, stream logs and metrics history. |
| 💾 **Backup & restore** | Scheduled backups to local disk, SMB, SSH or Google Drive, encrypted with your passphrase; validated one-click restore with a safety backup. |

### Example: One Main Server, two Remote Nodes

```text
  Sources (HTTP/HLS, MPEG-TS, YouTube Live)
                    │
                    ▼
        ┌────────────────────────┐
        │      Main Server       │  FFmpeg → HLS, Panel, playlists
        └───────────┬────────────┘
         /relay/…   │   /relay/…
          ┌─────────┴─────────┐
          ▼                   ▼
   ┌─────────────┐     ┌─────────────┐
   │   Node  A   │     │   Node  B   │   serve viewers
   └──────┬──────┘     └──────┬──────┘
          ▼                   ▼
      viewers             viewers
```

1. Add the channel and its sources under **Channels**, then **Start** it.
2. Add each server under **Nodes** (manually or with **Auto install** over SSH).
3. Assign the channel to the nodes and create viewers under **Users & Playlists**.
4. Hand out the user's M3U link, Xtream login or Web Player URL.

Viewers are sent to a ready node for their channel; the Main Server keeps encoding once, no matter how many nodes relay it.

---

## 🖥️ Dashboard & Monitoring

- **Overview:** CPU, memory, per-interface network throughput, running channels and live viewers.
- **Channels:** status, uptime, bitrate, resolution and codec per channel, with one-click Start / Stop / Restart and **Stream logs**.
- **Nodes:** reachability, version, load and channel readiness for every Remote Node.
- **Viewer sessions:** who is watching what, from where, with session kill.
- **Logs:** activity, access, client and system logs with retention settings.

> **Screenshots:** Real product screenshots have not yet been added to this repository. Add redacted images under `docs/images/` before embedding them here — never expose tokens, playlist links, viewer IPs or internal addresses.

---

## 🧩 Technology & architecture

| Layer | Technology |
| --- | --- |
| Web/API | Python, FastAPI, Gunicorn + Uvicorn workers |
| Frontend | Jinja templates, vanilla JavaScript, CSS, HLS.js |
| Encoding | FFmpeg / ffprobe, yt-dlp for YouTube Live |
| Database | SQLite (`/var/lib/streamforge/streamforge.db`) |
| Shared runtime state | Redis (optional, loopback) |
| Public listener / TLS | Nginx, certbot / ACME DNS-01 |
| Services & tasks | systemd units, path watchers and timers |
| Privileged actions | Separate root helper triggered by request files (restart, reboot, restore) |

```text
Browser / IPTV app → Nginx :80/:443 ─┬─ streamforge          Panel & API      127.0.0.1:8800
                                     ├─ streamforge-public   playback/playlist 127.0.0.1:8811
                                     └─ /relay/…             HLS for Remote Nodes
streamforge-channel-supervisor ── FFmpeg processes ── /var/lib/streamforge/hls
Remote Node: streamforge-node (control + Node Panel) · streamforge-node-public (127.0.0.1:8821)
```

See [Architecture & security](docs/ARCHITECTURE.md) for components, trust boundaries and the update model.

---

## ✅ Requirements

- A Linux/systemd host; the installer targets **Debian/Ubuntu** (`apt-get`) with Python 3.
- Root access for installation and updates, and a second SSH session for recovery.
- A public IP or domain for the Main Server; open ports **80/443** for viewers and Nodes.
- Optional: NVIDIA, Intel QSV or VAAPI hardware for accelerated encoding.
- Remote Nodes: separate Linux hosts reachable from the Main Server over SSH/HTTP(S).

This is a **host-installed application**, **not a Docker Compose stack**. Review the [installation guide](docs/INSTALLATION.md) and `scripts/install.sh` before running privileged commands.

---

## 🚀 Installation / Upgrade

### 1. Download the versioned package

Download `streamforge-X.YY.zip` from **[Releases](https://github.com/Santuctg/streamforge/releases/tag/v13.89)** and check it:

```bash
cd /root
curl -fL -o streamforge-13.89.zip \
  https://github.com/Santuctg/streamforge/releases/download/v13.89/streamforge-13.89.zip
sha256sum streamforge-13.89.zip
```

### 2a. Fresh install (new server only)

```bash
unzip -q streamforge-13.89.zip -d /root
sudo bash /root/streamforge-13.89/scripts/install.sh
```

Open `http://SERVER_IP` and sign in as **`admin` / `ChangeMe123!`**. The Panel then requires a new password of at least 12 characters before anything else.

### 2b. Upgrade an existing server

```bash
sudo streamforge-update --force /root/streamforge-13.89.zip
```

Releases are applied **in order** — each updater accepts only the previous version and rolls back automatically on failure. **Never run `install.sh` on an existing server.**

### 3. Verify

```bash
cat /opt/streamforge/VERSION
systemctl is-active streamforge streamforge-public streamforge-channel-supervisor nginx
curl -fsS http://127.0.0.1:8800/health
```

Expected: the release version, four `active` lines, and `ok`.

---

## 📁 Project structure

```text
streamforge/
├── app/                  # Main Server (FastAPI)
│   ├── main.py           #   routes, Panel, playlists, Xtream API
│   ├── ffmpeg.py         #   channel FFmpeg manager
│   ├── node_manager.py   #   Remote Node control and relay
│   ├── templates/        #   Jinja Panel pages
│   └── static/           #   JavaScript and CSS
├── node_agent/           # Remote Node Agent (bundled with Main)
├── deploy/               # Main systemd units and Nginx site
├── scripts/              # installer, updaters, recovery CLI, patchers, tests
├── release-notes/        # per-release notes (GitHub Release body)
├── docs/                 # guides
├── requirements.txt
└── VERSION
```

---

## 📚 Documentation

| Guide | Contents |
| --- | --- |
| [Installation & upgrades](docs/INSTALLATION.md) | Requirements, fresh install, ordered upgrades, verification and rollback |
| [Administration](docs/ADMINISTRATION.md) | Channels, sources, nodes, users & playlists, access rules, roles, backups, settings |
| [Architecture & security](docs/ARCHITECTURE.md) | Components, services, ports, data flow, trust boundaries, update model |
| [API & testing](docs/API_AND_TESTING.md) | Playlist, Xtream and Web Player endpoints, Node API, regression tests |
| [Operations / troubleshooting](docs/OPERATIONS.md) | Health checks, recovery commands, secret-key rules, troubleshooting |
| [Publishing releases](docs/PUBLISHING.md) | How releases are built, tested and published (maintainers) |
| [Release notes](release-notes/) | Changes per version |

---

## 🧯 Troubleshooting

**Panel unreachable?** Check `systemctl status streamforge nginx`, `curl http://127.0.0.1:8800/health` and `journalctl -u streamforge -n 100 --no-pager`.

**Channel keeps restarting?** Open the channel's **Stream logs**; check the source URL from the server with **Scan**, and `journalctl -u streamforge-channel-supervisor -n 100`.

**All Remote Node channels down at once?** On v12.75 or older, check whether `STREAMFORGE_SECRET_KEY` in `/etc/streamforge.env` was changed — it derived the Node relay keys; restore the previous value and restart the services. From v12.76 relay keys no longer depend on it.

**Updater refuses to run?** Its message names the version it requires; install the missing releases in order.

**Locked out of the Panel?** Use `sudo streamforge resetuser`, `resetdomain` or `resetpanelaccess` on the server.

For more scenarios, see [Operations](docs/OPERATIONS.md#-troubleshooting).

---

## 🔒 Security notes

- Panel passwords need 12+ characters; a default or shorter password must be changed at the next login (v12.77+). Create your own Super Admin and delete `admin` after installation.
- Keep `/etc/streamforge.env` private. From v12.76, rotating `STREAMFORGE_SECRET_KEY` no longer breaks Node relays or playlist links, but still signs everyone out and requires re-entering saved GeoIP keys and Node SSH passwords — see [Operations](docs/OPERATIONS.md#-secret-key-rules).
- **Set a backup passphrase** (Backups → Backup encryption) so off-server backups are encrypted, and store it safely.
- Serve the Panel over HTTPS; the session cookie is marked `Secure` on HTTPS requests (v12.75+). Main and Node Panel pages refuse to be framed by other sites (v12.76+ / v12.77+), and HSTS can be enabled with `STREAMFORGE_HSTS_MAX_AGE`.
- Give staff least-privilege roles; reserve Super Admin for owners.
- Protect backups, playlist links and Node tokens — they grant access to your streams.

## 📦 Releases and support

**Download:** [GitHub Releases](https://github.com/Santuctg/streamforge/releases)

**Report a bug or request a feature:** [GitHub Issues](https://github.com/Santuctg/streamforge/issues)

## 📄 License

No license file is included. **Public source availability does not grant an open-source license.** The project owner should select and publish a license before third-party redistribution or reuse is encouraged.

---

*Project maintained by [Santuctg](https://github.com/Santuctg).*
