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
| Forgot the admin password / no Super Admin left | `sudo streamforge resetuser` — prompts for a username and a 10+ character password |
| Wrong Panel/Playlist domain or URL lock | `sudo streamforge resetdomain [http://IP-or-domain/optional-path]` |
| Locked out by Panel IP/ASN rules | `sudo streamforge resetpanelaccess` |
| Logos lost after a migration | `sudo streamforge restorelogos /absolute/path/to/backup.tar.gz` |
| Disk filled by package/application caches | `sudo streamforge-cache-clear [auto\|main\|node\|all]` — never deletes the database, channels, logos, backups or active HLS |
| Login blocked for your IP | Wait 3 hours, or a Super Admin unblocks it in **Panel users → Blocked IPs** |
| Undo an upgrade | See [Installation & upgrades](INSTALLATION.md), section *Roll back an upgrade manually* |

---

## 🔑 Secret key rules

`STREAMFORGE_SECRET_KEY` in `/etc/streamforge.env` is generated once by the
installer (64 random hex characters). Many things are derived from it:

| Derived from the key | If the key changes |
| --- | --- |
| Panel login sessions | Everyone is logged out |
| **Remote Node relay keys** | **All Remote Node channels stop** |
| **Public stream keys in playlist links** | **Existing viewer links stop working** |
| Encrypted playback keys | Open Web Player sessions must restart |
| Encrypted saved secrets | Backup target passwords, GeoIP keys and saved SSH passwords must be re-entered |

**Rules**

1. Keep it private: never paste it into chats, tickets or screenshots.
2. **Do not rotate it on a running deployment.** If you ever must, plan a
   maintenance window: resync every Node, re-send playlists to every user, and
   re-enter all saved secrets.
3. Back up `/etc/streamforge.env` together with your database backups.

**Rotated by mistake? Put the old value back:**

```bash
sudo cp -a /etc/streamforge.env.bak /etc/streamforge.env     # or edit the line by hand
sudo systemctl restart streamforge streamforge-public streamforge-channel-supervisor
```

Nodes and channels recover within a minute or two. Secrets saved *while* the
new key was active must be entered again.

---

## 🔒 Security checklist

- [ ] `admin` / `ChangeMe123!` replaced (or `admin` deleted after creating your own Super Admin)
- [ ] `STREAMFORGE_SECRET_KEY` is a long random value and has never been shared
- [ ] Panel served over HTTPS (session cookie is `Secure` on HTTPS since v12.75)
- [ ] Staff use least-privilege roles; Super Admin limited to owners
- [ ] Panel access restricted by IP/ASN where possible
- [ ] Backups go off-server (SMB / SSH / Google Drive) and are protected
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
[Secret key rules](#-secret-key-rules)). Otherwise check that Nodes can reach
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
