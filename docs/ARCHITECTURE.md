# 🧩 Architecture & security

How StreamForge is put together, where trust boundaries are, and why updates
patch installed files instead of replacing them.

---

## 🗺️ Components

```text
                 Sources: HTTP/HLS · MPEG-TS · YouTube Live
                                   │
┌───────────────────────────── Main Server ─────────────────────────────┐
│                                                                        │
│  Nginx :80 / :443 ──┬── streamforge            Panel · API · admin     │
│  (TLS, aliases,     │   gunicorn 127.0.0.1:8800 (1 control worker)     │
│   relay files)      ├── streamforge-public     playlists · Xtream ·     │
│                     │   127.0.0.1:8811         Web Player · playback    │
│                     └── /relay/<key>/…         HLS for Remote Nodes     │
│                                                                        │
│  streamforge-channel-supervisor ── FFmpeg ×N ──► /var/lib/streamforge/hls │
│  SQLite streamforge.db · Redis (optional) · /etc/streamforge.env        │
│  root helpers: main-access (Nginx/domain) · main-system (restart/restore) │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTPS/HTTP · Node token
┌──────────────────────────── Remote Node ──────────────────────────────┐
│  streamforge-node         control API (/api/v1/*) · Node Panel          │
│  streamforge-node-public  playback workers 127.0.0.1:8821               │
│  FFmpeg relay of assigned channels · Nginx · TLS reconcile timer         │
└────────────────────────────────────────────────────────────────────────┘
```

| Unit (Main) | Responsibility |
| --- | --- |
| `streamforge` | The single control worker: Panel pages, admin API, node control, schedules |
| `streamforge-public` | Several playback workers: playlists, Xtream API, Web Player, playback authorisation |
| `streamforge-channel-supervisor` | Starts, watches and restarts every Main FFmpeg process; the control worker talks to it over a local Unix socket |
| `streamforge-main-access.path` → helper | Re-renders Nginx when Panel/Playlist URLs or access rules change |
| `streamforge-main-system.path` → helper | Runs privileged actions requested from the Panel (restart, reboot, restore) |
| `streamforge-main-tls.timer`, `streamforge-geoip-update.timer` | Certificate renewal, GeoIP database refresh |

Redis holds short-lived playback/session state shared by the workers. When it
is missing, StreamForge falls back to local state (single-worker behaviour).

---

## 🔄 Data flow

1. **Ingest** — the supervisor runs FFmpeg per channel; it resolves YouTube
   Live with yt-dlp, applies the chosen encoder, and writes HLS segments.
2. **Relay** — a Remote Node assigned to a channel pulls
   `/relay/<relay-key>/<slug>/index.m3u8` from Main and re-serves it.
3. **Authorise** — a viewer opens a playlist, Xtream or Web Player URL; the
   public worker checks the account (enabled, expiry, channel assignment,
   connection limit, restreamer IPs, access rules).
4. **Route** — StreamForge picks a ready Node (pinned IP prefix → user's fixed
   Node → load-balanced candidates; nodes in maintenance are skipped) and
   issues the player a playlist for that Node's copy of the channel. If the
   Node stops being ready, the next playlist request moves to another ready
   Node.
5. **Serve** — the Node serves the HLS it relays. Web Player sessions use
   short-lived encrypted playback keys bound to the viewer's IP, which the
   Node validates.

---

## 🔐 Trust boundaries

| Boundary | Protection |
| --- | --- |
| Internet → Panel | Session login (scrypt password hashes, 12+ character policy with forced change after a weak login, v12.77+), role permissions, CSRF tokens on sensitive forms, IP/ASN Panel access rules, 5-failure / 3-hour login IP block |
| Internet → playback | Per-account credentials or tokens, expiry and connection limits, restreamer source IPs, per-Node IP/ASN policy, encrypted IP-bound playback keys |
| Main → Node | Node API requires the `X-Node-Token` header, compared in constant time; Node Panel logins are verified live against Main |
| Node → Main relay | Relay URLs carry an HMAC relay key derived from the database `stream_link_secret` (v12.76+) |
| Web workers → root | Workers never run as root; privileged actions are requested through files watched by systemd path units, and the helpers validate every request (e.g. restore archives: allowed paths only, no links or devices) |
| Secrets at rest | Panel and Xtream passwords: scrypt hashes; saved GeoIP keys and Node SSH passwords: Fernet keyed from the secret key; backup destination passwords: Fernet with `/var/lib/streamforge/backups/secret.key`; password vault: Fernet with its own key |
| Backups off-server | With a backup passphrase (v12.76+): AES-256-GCM, scrypt-derived key, chunk order and truncation authenticated |
| Main → SSH servers | Host keys remembered on first contact and enforced afterwards (v12.76+) |
| Browser | All user-supplied names are HTML-escaped; session cookie is `HttpOnly`, `SameSite=Lax`, `Secure` on HTTPS (v12.75+) and invalidated by a password change (v12.76+); `nosniff` and `Referrer-Policy` on every response, `X-Frame-Options`/`frame-ancestors` on Main Panel pages (v12.76+) and Node Panel pages (v12.77+), opt-in HSTS |
| Internet → Xtream logins | 10 failures per IP + username or 100 per IP in 10 minutes → 15-minute block (v12.76+) |

### 🔑 Which key protects what

| Item | Key | Changing `STREAMFORGE_SECRET_KEY` |
| --- | --- | --- |
| Panel session signatures | `STREAMFORGE_SECRET_KEY` | Everyone is logged out |
| Encrypted Web Player playback keys | `STREAMFORGE_SECRET_KEY` | Open sessions restart |
| Saved GeoIP keys, saved Node SSH passwords | `STREAMFORGE_SECRET_KEY` | Must be re-entered |
| Node relay keys, viewer stream links | `stream_link_secret` row in `app_settings` (v12.76+; seeded once from the secret key so existing links keep their value) | Unchanged on v12.76+ (on ≤ v12.75 they broke) |
| Backup destination passwords | `/var/lib/streamforge/backups/secret.key` | Unchanged |
| Panel password vault | `STREAMFORGE_PANEL_PASSWORD_VAULT_KEY` (generated by `install.sh` since v12.82) | Unchanged |
| Encrypted destination backups | Operator backup passphrase | Unchanged |

---

## 🧱 Update model: installed sources are patched in place

Existing servers are never re-imaged from the repository. Each release's
updater (`scripts/force_update_v<version>.sh`) changes a bounded set of
installed files:

1. Check package and installed versions (releases apply **in order**) and that
   the Node runtime is unchanged where the release does not touch it.
2. Copy the installed `app/main.py` to a staging directory and apply an
   idempotent patcher (e.g. `scripts/apply_main_hotfixes.py`) that edits exact
   anchor text and refuses when an anchor is missing.
3. Verify markers and Python syntax, back up to
   `/var/backups/streamforge/v<version>-<timestamp>/`, install atomically.
4. Restart only the services the change needs; roll back on any failure.

The repository's `app/main.py` is kept byte-identical to "previous release +
patcher", so fresh installs and upgraded servers run the same code. This is
also why **`install.sh` must not be used to update** an existing server.
