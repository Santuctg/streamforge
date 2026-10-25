# 🧭 Operations & troubleshooting

Day-to-day health checks, recovery commands, the rules for
`STREAMFORGE_SECRET_KEY`, and fixes for common problems. For install and
upgrade steps see [Installation & upgrades](INSTALLATION.md).

---

## 📍 Where things live (Main Server)

| Item | Location |
| --- | --- |
| Application | `/opt/streamforge` |
| Database, HLS, backups | `/var/lib/streamforge` (`streamforge.db`, `hls/`, `backups/`) |
| Configuration | `/etc/streamforge.env` |
| Logos | `/opt/streamforge/logo` |
| Updater backups | `/var/backups/streamforge/v<version>-<timestamp>/` |
| Nginx site | `/etc/nginx/sites-available/streamforge` |

| Service | Role | Logs |
| --- | --- | --- |
| `streamforge` | Panel / API (127.0.0.1:8800) | `journalctl -u streamforge` |
| `streamforge-public` | Playlists, Xtream, Web Player (127.0.0.1:8811) | `journalctl -u streamforge-public` |
| `streamforge-channel-supervisor` | Main FFmpeg processes | `journalctl -u streamforge-channel-supervisor` |
| `nginx` | Public 80/443 | `/var/log/nginx/` |
| `streamforge-node`, `streamforge-node-public` | Remote Node control / playback (on each Node) | `journalctl -u streamforge-node` |

---

## 🩺 Health check

```bash
cat /opt/streamforge/VERSION
systemctl is-active streamforge streamforge-public streamforge-channel-supervisor nginx
curl -fsS http://127.0.0.1:8800/health      # expect: ok
curl -fsS http://127.0.0.1:8811/health      # expect: ok
journalctl -u streamforge -n 100 --no-pager
```

In the Panel: **Overview** for resources and viewers, **Channels** for
channel status, **Nodes** for Node reachability and versions, **Logs** for
events.

> A healthy `/health` proves the workers answer; it does not prove that
> sources are reachable or that viewers on other networks can play. Test a
> channel from a real client after changes.

---

## 🆘 Recovery commands

| Situation | Command |
| --- | --- |
| Forgot the admin password / no Super Admin left | `sudo streamforge resetuser` — prompts for a username and a 12+ character password |
| Wrong Panel/Playlist domain or URL lock | `sudo streamforge resetdomain [http://IP-or-domain/optional-path]` |
| Locked out by Panel IP/ASN rules | `sudo streamforge resetpanelaccess` |
| Logos lost after a migration | `sudo streamforge restorelogos /absolute/path/to/backup.tar.gz` |
| Disk filled by package/application caches | `sudo streamforge-cache-clear [auto\|main\|node\|all]` — never deletes the database, channels, logos, backups or active HLS |
| Login blocked for your IP | Wait 3 hours, or a Super Admin unblocks it in **Panel users → Blocked IPs** |
| Undo an upgrade | See [Installation & upgrades](INSTALLATION.md), section *Roll back an upgrade manually* |

---

## 🔑 Secret key rules

`STREAMFORGE_SECRET_KEY` in `/etc/streamforge.env` is generated once by the
installer (64 random hex characters). What changing it affects depends on the
installed version:

| Item | Key it uses | If `STREAMFORGE_SECRET_KEY` changes |
| --- | --- | --- |
| Panel login sessions | Secret key | Everyone is logged out |
| Web Player playback keys | Secret key | Open Web Player sessions restart |
| Saved GeoIP keys, saved Node SSH passwords | Secret key | Must be entered again |
| **Remote Node relay keys, viewer stream links** | **v12.76+:** `stream_link_secret` in the database (seeded once from the secret key) · **≤ v12.75:** the secret key | **v12.76+: unchanged** · ≤ v12.75: **all Node channels stop and playlist links break** |
| Backup destination passwords | Separate key file `/var/lib/streamforge/backups/secret.key` | Unchanged |
| Panel password vault | Separate `STREAMFORGE_PANEL_PASSWORD_VAULT_KEY` | Unchanged |

**Rules**

1. Keep it private: never paste it into chats, tickets or screenshots.
2. Rotate it only when it may have leaked, and on v12.76 or newer. Expect
   everyone to sign in again and re-enter saved GeoIP keys and Node SSH
   passwords. On v12.75 or older, **do not rotate it** — update first.
3. Back up `/etc/streamforge.env` together with your database backups.

```bash
sudo cp -a /etc/streamforge.env /etc/streamforge.env.bak
NEW=$(openssl rand -hex 32)
sudo sed -i "s/^STREAMFORGE_SECRET_KEY=.*/STREAMFORGE_SECRET_KEY=$NEW/" /etc/streamforge.env
sudo systemctl restart streamforge streamforge-public
```

**Rotated by mistake on v12.75 or older? Put the old value back:**

```bash
sudo cp -a /etc/streamforge.env.bak /etc/streamforge.env     # or edit the line by hand
sudo systemctl restart streamforge streamforge-public streamforge-channel-supervisor
```

Nodes and channels recover within a minute or two. Secrets saved *while* the
new key was active must be entered again.

---

## 🔒 Security checklist

- [ ] `admin` deleted after creating your own Super Admin; every Panel password has 12+ characters (enforced at login from v12.77)
- [ ] Remote Nodes updated from **Node Updates** to the Main version
- [ ] `STREAMFORGE_SECRET_KEY` is a long random value and has never been shared
- [ ] Panel served over HTTPS (session cookie is `Secure` on HTTPS since v12.75); optionally `STREAMFORGE_HSTS_MAX_AGE` when every port of the domain uses HTTPS
- [ ] Staff use least-privilege roles; Super Admin limited to owners
- [ ] Panel access restricted by IP/ASN where possible
- [ ] Backups go off-server (SMB / SSH / Google Drive) **with a backup passphrase set** (v12.76+), and the passphrase is stored safely elsewhere
- [ ] A second SSH session is open before changing access rules or updating

---

## 🧯 Troubleshooting

**Panel does not load.**
`systemctl is-active streamforge nginx` → `journalctl -u streamforge -n 100 --no-pager` →
`curl http://127.0.0.1:8800/login`. If the domain changed, run
`sudo streamforge resetdomain`.

**Panel loads but playback fails.**
`systemctl is-active streamforge-public`; `curl http://127.0.0.1:8811/health`.
Check the **Client log** for the rejection reason (expired account,
connection limit, restreamer IP, access rule).

**A Main channel keeps restarting or shows Waiting.**
Open its **Stream logs**, then **Scan** the source from the server. YouTube
sources asking to "confirm you're not a bot" need a fresh `cookies.txt` in
**Settings**. Supervisor log: `journalctl -u streamforge-channel-supervisor -n 100`.

**All Remote Node channels stopped at the same time.**
Check whether `STREAMFORGE_SECRET_KEY` changed (see
[Secret key rules](#-secret-key-rules); from v12.76 this no longer affects
relays). Otherwise check that Nodes can reach
the Main relay URL and look at **Nodes** for errors.

**One Node offline.**
On the Node: `systemctl is-active streamforge-node streamforge-node-public nginx`
and `journalctl -u streamforge-node -n 100`. Confirm its token and URL under
**Nodes**.

**Updater refuses to run.**
Read the message: `requires installed Main vA.BB` means you must install that
release first; `package version mismatch` means the ZIP is not the one the
updater expects.

**Saving Settings shows an error.**
Invalid input (empty brand name, unsupported image) shows a message. Since
v12.73 this no longer causes a 500 error.

**Restore from a saved backup fails with `name 'shutil' is not defined`.**
Fixed in v12.73 — upgrade.

**A deleted `admin` user came back after an update.**
Fixed in v12.73 — upgrade; then delete it once more.

**Node install/admin or SSH backup says "SSH host key … has changed".**
Since v12.76 StreamForge remembers each server's SSH host key. If you really
reinstalled that server, run the `ssh-keygen -R` command shown in the error
and retry; otherwise stop — the connection may be intercepted.

**An IPTV app or Web Player login keeps failing with correct credentials.**
After repeated wrong passwords, that IP + username is blocked for 15 minutes
(100 failures across usernames block the whole IP). Fix the saved password in
the app and wait 15 minutes. Blocks appear in **Logs** as
"Xtream login blocked after repeated failures".

**Restore says "This backup is encrypted".**
Use the **Restore Main Server** upload form and type the backup passphrase
that was active when the backup was made. Offline:
`python3 /opt/streamforge/app/backup_crypto.py decrypt IN.tar.gz OUT.tar.gz`.

**Signed out after someone changed your password.**
Expected since v12.76: a password change ends every other session of that
user.

**SSH Node update log shows `fetch_hlsjs.sh: No such file or directory`.**
Harmless on v12.77 and older: the update still finishes and the Node fetches
hls.js by itself later. Fixed in v12.78, which ships the helper in the SSH
package.

**Overview charts stopped moving.**
From v12.80 the charts update live, but pause while you are zoomed in
(double-click the chart to reset), while the pointer is over a chart and while
the browser tab is in the background.

**After v12.78 the chart legends still show no values.**
The v12.78 updater did not refresh the Nginx static cache, so browsers kept
the v12.77 scripts. Install v12.79, then reload the page. A manual equivalent:
`sudo python3 /opt/streamforge/scripts/sync_main_static_cache.py /opt/streamforge/app/static /var/cache/streamforge/main-static`.

**Overview shows `—` for memory, disk or network for a few seconds.**
Before v12.78 these filled in at the first live refresh, five seconds after
the page opened. From v12.78 they render with the page; empty charts show
"Loading…" while their history is fetched.

**Every Panel page sends me to "Change password".**
Since v12.77 a login with a default password or one shorter than 12 characters
must set a new password first. Choose 12+ characters that are not
`ChangeMe123!` or the `STREAMFORGE_ADMIN_PASSWORD` seed.
