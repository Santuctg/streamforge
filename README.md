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

**Current release:** [v12.81](https://github.com/Santuctg/streamforge/releases/tag/v12.81) · **Deployment:** Linux host with systemd, Nginx and FFmpeg · **Docker image:** not provided.

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

Download `streamforge-X.YY.zip` from **[Releases](https://github.com/Santuctg/streamforge/releases/latest)** and check it:

```bash
cd /root
curl -fL -o streamforge-12.81.zip \
  https://github.com/Santuctg/streamforge/releases/download/v12.81/streamforge-12.81.zip
sha256sum streamforge-12.81.zip
```

### 2a. Fresh install (new server only)

```bash
unzip -q streamforge-12.81.zip -d /root
sudo bash /root/streamforge-12.81/scripts/install.sh
```

Open `http://SERVER_IP` and sign in as **`admin` / `ChangeMe123!`**. The Panel then requires a new password of at least 12 characters before anything else.

### 2b. Upgrade an existing server

```bash
sudo streamforge-update --force /root/streamforge-12.81.zip
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
