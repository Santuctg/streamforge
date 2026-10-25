# 🔌 API & testing

The HTTP endpoints StreamForge exposes, how they are authenticated, and how to
run the regression tests.

> Playlist links, tokens and Xtream passwords grant access to your streams.
> Treat them like passwords and never paste them into public tickets.

---

## 📺 Viewer endpoints (Main Server)

Served by the `streamforge-public` workers behind Nginx, below the configured
Playlist URL.

| Endpoint | Authentication | Returns |
| --- | --- | --- |
| `GET /playlist/{token}.m3u` | User token | M3U playlist of the user's channels |
| `GET /get.php?username=&password=&type=m3u_plus&output=m3u8` | Xtream username/password | Xtream-style M3U playlist |
| `GET /player_api.php?username=&password=&action=…` | Xtream username/password | Xtream API JSON: account/server info, `get_live_categories`, `get_live_streams` |
| `GET /live/{username}/{password}/{stream}` | Xtream username/password | Live stream for IPTV apps |
| `GET /play/{token}/{slug}/master.m3u8` | User token | HLS for one channel, routed to a ready Node |
| `GET /watch/{token}/{slug}` · `GET /player/{token}` | User token | Browser player for one channel / the user's channel list |
| `GET /live/{stream_key}/{slug}[/master.m3u8]` | Per-channel stream key | Direct browser player / HLS for one channel |
| `GET /web-player` (+ `/login`, `/quick-login`, `/logout`) | Web Player login | Branded Web Player |
| `GET /ek/{playback_key}/{channel_id}/master.m3u8` | Encrypted, IP-bound playback key | HLS for Web Player sessions |
| `GET /web-player/download` · `GET /{name}.apk` · `GET /update.json` | Node access policy | Android APK download and its update manifest (when configured) |

Xtream/M3U/Web Player credential checks are rate-limited per source IP and
username (v12.76+): blocked requests get the same response as a wrong password.

Every playback request also checks: account enabled and not expired,
channel assigned, connection limit, restreamer source IPs, and the IP/ASN
policy of the serving Node. Rejections are written to the **Client log**.

---

## 🩺 Service endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Returns `ok` (header `X-StreamForge-Process-Role: control` or `public`) |
| `GET /version` | Returns the running Main version, e.g. `12.78` |
| `GET /relay/{relay_key}/{slug}/index.m3u8` (+ segments) | HLS relay pulled by Remote Nodes; the HMAC relay key authorises it |

Local checks on the Main Server:

```bash
curl -fsS http://127.0.0.1:8800/health    # control worker
curl -fsS http://127.0.0.1:8811/health    # playback workers
curl -fsS http://127.0.0.1:8800/version
```

---

## 🛰️ Main ↔ Node API

| Direction | Endpoints | Authentication |
| --- | --- | --- |
| Main → Node | `/api/v1/*` on the Node: health, status, channel sync/start/stop/restart/logs, settings/users/logo sync, viewer sessions and kill, agent update, TLS reconcile | `X-Node-Token` header, constant-time comparison; requests are refused if the Node has no token configured |
| Node → Main | `/api/v1/node-heartbeat/{node_slug}`, `/api/v1/node-panel-live-auth/{node_slug}`, `/api/v1/node-control/*`, `/api/v1/node-access-back-sync/{node_slug}` | Node identity and token checked by Main; Node Panel logins carry an encrypted payload with the browser's IP |

These are internal APIs between your own servers — keep Node tokens secret and
do not build third-party integrations on them; they change between releases.

---

## 🧪 Testing

Regression tests live in `scripts/` and run in GitHub Actions on every pull
request (`.github/workflows/`).

```bash
python3 -m pip install -r requirements.txt httpx   # httpx is needed by TestClient tests

# current release
python3 scripts/test_v1278_dashboard.py
python3 scripts/test_v1278_updater.py
python3 scripts/test_v1277_password_policy.py
python3 scripts/test_v1277_node_headers.py
python3 scripts/test_v1276_stream_link_secret.py
python3 scripts/test_v1276_security.py
python3 scripts/test_v1276_ssh_host_key.py
python3 scripts/test_v1276_backup_encryption.py
python3 scripts/test_v1275_secure_cookie.py
python3 scripts/verify_panel_credential_vault_v1263.py
python3 scripts/verify_release_updater_staging.py

# security behaviour of the Panel
PYTHONPATH=. python3 scripts/test_v1263_vault_security.py
```

| Kind of test | Examples |
| --- | --- |
| Updater upgrade / reinstall / rollback | `test_v12XX_updater.py` (run against a temporary install tree with `STREAMFORGE_HOTFIX_TEST_MODE=1`) |
| Feature behaviour | `test_v1278_dashboard.py`, `test_v1277_password_policy.py`, `test_v1277_node_headers.py`, `test_v1276_security.py`, `test_v1276_backup_encryption.py`, `test_v1276_ssh_host_key.py`, `test_v1276_stream_link_secret.py`, `test_v1275_secure_cookie.py`, `test_v1272_node_login.py` |
| Security contracts | `verify_panel_credential_vault_v1263.py`, `test_v1263_vault_security.py` |
| Release packaging | `verify_release_updater_staging.py`, `git diff --check` |

Notes:

- Tests tied to one release (older `test_v12XX_updater.py` and a few
  feature snapshots) print `SKIP` unless `VERSION` matches that release, so
  `for f in scripts/test_*.py; do python3 "$f"; done` runs clean.
- Tests that boot the app set temporary `STREAMFORGE_DATABASE_URL`,
  `STREAMFORGE_HLS_ROOT` and logo paths, so they never touch a real install.
- Some workflow steps guarded by `if: github.event_name == 'push' …` commit
  and push; never run those steps locally.
