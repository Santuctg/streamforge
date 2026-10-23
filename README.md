# StreamForge

StreamForge is a self-hosted live-TV encoding and distribution panel. A **Main
Server** ingests channel sources (HTTP/HLS, MPEG-TS, YouTube Live), runs FFmpeg
to produce HLS output, and serves playlists, an Xtream-compatible API and a Web
Player to viewers. **Remote Nodes** pull channels from the Main Server and
serve viewers closer to them, so playback load is spread across servers.

Current release: see [`VERSION`](VERSION) and the
[GitHub Releases](https://github.com/Santuctg/streamforge/releases) page.

## Architecture

```
               sources (HTTP/HLS, MPEG-TS, YouTube Live)
                                │
                                ▼
┌──────────────────────── Main Server ────────────────────────┐
│ Nginx :80/:443                                               │
│  ├─ streamforge            Panel/API (gunicorn, 127.0.0.1:8800)│
│  ├─ streamforge-public     playback workers (127.0.0.1:8811)  │
│  └─ /relay/…               HLS relay for Remote Nodes         │
│ streamforge-channel-supervisor   owns the FFmpeg processes    │
│ SQLite /var/lib/streamforge/streamforge.db · Redis (optional) │
└──────────────────────────────┬───────────────────────────────┘
                               │ /relay/<relay-key>/<channel>/index.m3u8
                               ▼
┌──────────────────────── Remote Node ────────────────────────┐
│ streamforge-node          control API + Node Panel           │
│ streamforge-node-public   playback workers (127.0.0.1:8821)  │
│ authenticated by the Node token; Panel logins verified live  │
│ against Main                                                 │
└──────────────────────────────────────────────────────────────┘
```

| Path | Contents |
| --- | --- |
| `app/` | Main Server (FastAPI): `main.py` routes, `ffmpeg.py` stream manager, `node_manager.py` Node control, templates and static assets |
| `node_agent/` | Remote Node agent (`app.py`), bundled with Main and deployed to Nodes |
| `deploy/` | Main systemd units and the Nginx site template |
| `scripts/` | installer, updater (`streamforge-update`, `force_update_v*.sh`), recovery CLI (`streamforge`), patchers and regression tests |
| `release-notes/` | per-release notes used for the GitHub Release body |

## Quick start

Fresh install on Debian/Ubuntu, as root, from an extracted release ZIP:

```bash
sudo bash scripts/install.sh
```

Then open `http://SERVER_IP`, sign in as `admin` / `ChangeMe123!` and **change
that password immediately** (Panel Users).

Update an existing server with the release ZIP for the next version:

```bash
sudo streamforge-update --force /root/streamforge-X.YY.zip
```

## Documentation

- [Operations guide](docs/operations.md): install, update, Remote Nodes,
  backup/restore, recovery commands, security notes, troubleshooting.
- [Release process](docs/release-process.md): how a release is built, tested
  and published (for maintainers).
