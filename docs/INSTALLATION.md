# 🚀 Installation & upgrades

This guide installs a StreamForge **Main Server**, upgrades it safely, and
shows how to verify and roll back. Remote Nodes are installed from the Panel —
see [Administration](ADMINISTRATION.md), section *Remote Nodes*.

---

## ✅ Requirements

| Item | Requirement |
| --- | --- |
| OS | Linux with systemd; the installer uses `apt-get` (**Debian / Ubuntu**) |
| Access | `root` (or `sudo`) and a **second SSH session** kept open for recovery |
| Network | Public IP or domain; inbound **80/tcp** and **443/tcp** for viewers and Nodes |
| Disk | Space for HLS segments (`/var/lib/streamforge/hls`), logs and backups |
| Optional | NVIDIA / Intel QSV / VAAPI GPU for hardware encoding; Redis (installed automatically) |

The installer installs: Python 3 + pip packages from `requirements.txt`,
FFmpeg, Nginx, Redis, rsync, OpenSSL, certbot, dnsutils, geoipupdate,
smbclient and the yt-dlp runtime used for YouTube Live.

---

## 📦 1. Download and check the package

```bash
cd /root
VERSION=12.83
curl -fL -o streamforge-$VERSION.zip \
  https://github.com/Santuctg/streamforge/releases/download/v$VERSION/streamforge-$VERSION.zip
sha256sum streamforge-$VERSION.zip      # compare with the hash you were given
unzip -t streamforge-$VERSION.zip        # integrity test
```

---

## 🆕 2. Fresh install (new server only)

```bash
unzip -q streamforge-$VERSION.zip -d /root
sudo bash /root/streamforge-$VERSION/scripts/install.sh
```

What the installer does:

1. Installs system packages and Python dependencies (skipped when
   `requirements.txt` is unchanged).
2. Creates the `streamforge` system user and the data directories under
   `/var/lib/streamforge`.
3. Copies the application to `/opt/streamforge`.
4. Writes `/etc/streamforge.env` **once**, with a random
   `STREAMFORGE_SECRET_KEY` (64 hex characters), a random
   `STREAMFORGE_PANEL_PASSWORD_VAULT_KEY` (base64 of 32 random bytes, since
   v12.82) and the first admin login. Existing env files created by older
   releases get the vault key appended if it is missing.
5. Installs the Nginx site, the systemd units and the CLI tools
   (`streamforge`, `streamforge-update`, `streamforge-cache-clear`,
   `streamforge-uninstall`, `streamforge-capacity-audit`).
6. Starts every service and waits for `/health` before finishing.

When it prints **`StreamForge installed.`**, open the shown address and sign in:

| Username | Password |
| --- | --- |
| `admin` | `ChangeMe123!` |

> ⚠️ Since v12.77 the Panel opens **Change password** first and accepts only a
> password of 12+ characters that is not a default. Afterwards, create your
> own Super Admin and delete `admin`. The env-file admin is only created when
> the database has no Panel admin at all; a deleted `admin` is not re-created
> on restart or update (since v12.73).

> 🚫 **`install.sh` is for new servers only.** On an existing server it replaces
> the installed `app/main.py`, which every updater patches in place. Always
> upgrade with `streamforge-update`.

---

## ⬆️ 3. Upgrade an existing server

```bash
cat /opt/streamforge/VERSION                      # what you run now
sudo streamforge-update --force /root/streamforge-$VERSION.zip
```

- Each release ships `scripts/force_update_v<version>.sh`, which accepts only
  specific installed versions (normally the previous release). **Apply
  releases in order** — if the updater says `requires installed Main vA.BB`,
  install that release first.
- The updater backs up every file it touches to
  `/var/backups/streamforge/v<version>-<timestamp>/`, verifies the result and
  **rolls back automatically** on failure.
- Main-only releases restart just `streamforge` and `streamforge-public`;
  Nginx, the channel supervisor and running streams keep running unless the
  release notes say otherwise.
- A successful run ends with **`SUCCESS: ...`**.

Updating Main updates the **bundled** Node Agent. Push it to Remote Nodes
when the release changes the agent runtime. v12.83 uses the same agent payload
as v12.82; Remote Nodes do not need an update for v12.83.

---

## 🔍 4. Verify

```bash
cat /opt/streamforge/VERSION
systemctl is-active streamforge streamforge-public streamforge-channel-supervisor nginx
curl -fsS http://127.0.0.1:8800/health      # Panel/API worker
curl -fsS http://127.0.0.1:8811/health      # playback workers
journalctl -u streamforge -n 50 --no-pager
```

Expected: the release version, four `active` lines, and `ok` twice. Then open
the Panel and confirm that channels are running and Nodes are online.

---

## ↩️ 5. Roll back an upgrade manually

The updater rolls back by itself when a guarded step fails. Undoing a
*successful* upgrade is release-specific: do not restore just `main.py` and
the VERSION files, because mixed-version Python modules can prevent startup.
Keep a second SSH session open and inspect the exact backup first:

```bash
sudo ls -ld /var/backups/streamforge/v1283-*
# Select the backup from the update you intend to undo (not a later reinstall).
sudo find /var/backups/streamforge/v1283-YYYYMMDD-HHMMSS -type f
```

For v12.83, the backup contains the changed application modules/templates,
`deploy/nginx.conf`, both VERSION files, the bundled `node_agent/app.py`, and
copies named `streamforge.env` and `nginx-site` when those external files
existed. Restore **all** backed-up runtime files to their corresponding
installed paths, not the entire backup directory blindly. The external copies
belong at `/etc/streamforge.env` and `/etc/nginx/sites-available/streamforge`.
Restore `app/client_ip.py` if backed up; otherwise it was newly added by this
release and should be removed only after confirming that fact. Restore file
ownership/modes too, test with `sudo nginx -t`, reload Nginx, then restart and
verify `streamforge` and `streamforge-public`.

Do not blindly restore an old env file after later credential changes: doing
so can invalidate sessions or encrypted credentials. Use the updater's
`rollback()` implementation and the matching release notes as the complete
file manifest. If any backup file is missing or you cannot identify the
correct backup, stop and recover with a verified full backup instead.

---

## 🔐 After upgrading to v12.83

- Requires Main v12.82 (or v12.83 for reinstall).
- **Remote Node HTTPS certificates are always verified.** Replace self-signed,
  expired or name-mismatched certificates with trusted, matching certificates
  before upgrading. HTTP is an unencrypted alternative only on networks where
  that risk is acceptable; it is not a TLS workaround for public networks.
- **Client IP uses the rightmost Nginx-appended X-Forwarded-For hop.** Behind
  a CDN/internal proxy, add only its trusted address ranges to
  `set_real_ip_from` in `/etc/nginx/sites-available/streamforge`; never trust
  `0.0.0.0/0` or `::/0`. Keep `real_ip_recursive on;`, then run
  `sudo nginx -t && sudo systemctl reload nginx`. Direct viewers must not be
  trusted as proxies. Recheck the directives after Panel URL/listener changes.
- **Weak signing keys now stop startup.** The updater repairs weak/missing
  key entries in an existing `/etc/streamforge.env` before restarting. An
  absent env file or a custom service environment still needs operator repair.
  Key rotation logs users out and requires re-entry of saved GeoIP/SSH secrets;
  see [Secret key rules](OPERATIONS.md#-secret-key-rules).
- The updater tests and gracefully reloads Nginx; it does not restart the
  channel supervisor. The Remote Node runtime is unchanged from v12.82.

## 🔐 After upgrading to v12.82

- **Killing viewer sessions needs its own permission.** Disconnecting a
  session on **Live sessions** used to follow `dashboard.view`, so a
  read-only Overview role could stop every stream. Existing roles that
  already had `dashboard.view` receive the new `sessions.kill` permission
  automatically at the first start after the upgrade; afterwards grant or
  revoke it per role under **Roles & Permissions**. The action is now written
  to the audit log.
- **Channel sources must be transport URLs.** `file:`, `concat:`, `lavfi:`,
  `ftp:` and other local/recipe schemes are refused when a channel is saved
  *and* when FFmpeg or ffprobe opens it, because such a source reads host
  files and can play them out as a live stream. `http(s)`, `rtmp(s)`,
  `rtsp(s)`, `rtp`, `udp`, `tcp`, `srt`, `mmsh/t` and `ws(s)` keep working.
  If you deliberately use another scheme or a local path, add it explicitly
  and restart the service:

  ```bash
  sudo sed -i '1i STREAMFORGE_SOURCE_SCHEME_ALLOWLIST=subfile' /etc/streamforge.env
  # or, for local file sources only:
  sudo sed -i '1i STREAMFORGE_ALLOW_LOCAL_FILE_SOURCES=1' /etc/streamforge.env
  sudo systemctl restart streamforge streamforge-channel-supervisor
  ```

- **Backup archives can leave the environment snapshot behind.** Each archive
  still contains a `etc/streamforge.env` member with the service's
  `STREAMFORGE_*` values — a disaster restore on a fresh server needs those
  keys, because saved Node SSH passwords, Panel passwords and the backup
  passphrase are encrypted with them. If you re-key after every restore and
  store archives somewhere less trusted, set
  `STREAMFORGE_BACKUP_INCLUDE_ENV=0`; the manifest then records the exclusion.
- **Node API tokens can be rotated from the Panel.** **Nodes → Regenerate API
  token** mints a new token, pushes it to `/etc/streamforge-node.env` over the
  Node's saved SSH access, restarts the Node Agent and only then stores it,
  so a failed push leaves the working pair untouched. It needs `nodes.edit`
  and saved SSH credentials; a Node installed without saved SSH details must
  be updated manually.
- **Check the log for the signing key.** A missing `STREAMFORGE_SECRET_KEY`
  (or the published development default) is now reported as an error at
  startup, because anyone who knows that value can forge Panel session
  cookies. See
  [Secret key rules](OPERATIONS.md#-secret-key-rules).

## 🔐 After upgrading to v12.81

- Open **Node Updates** and update every Remote Node to v12.81. Node Panel
  dashboards then show values beside the chart legends and live charts.
  Running relays keep playing during the Node update.

## 🔐 After upgrading to v12.80

- Reload the **Overview** page. The charts now update live: their newest point
  every 5 seconds and the full history once per sample interval.

## 🔐 After upgrading to v12.79

- Reload the **Overview** page. The chart legends now show current values,
  which v12.78 alone did not deliver on upgraded servers (its scripts did not
  reach the Nginx static cache).

## 🔐 After upgrading to v12.78

- Reload the **Overview** page. Memory, disk and network values appear
  immediately, and each chart shows its current value beside the legend.
- Remote Nodes need no update for this release (the Node Agent code is
  unchanged); updating them from **Node Updates** only aligns the version.

## 🔐 After upgrading to v12.77

- Open **Node Updates** and update every Remote Node to v12.77, so Node Panels
  send the new security headers.
- Panel users whose password is shorter than 12 characters (or a default) are
  asked to change it at their next login — tell your staff.

## 🔐 After upgrading to v12.76

- Open **Backups → Backup encryption** and set a passphrase (12+ characters),
  then store it outside the server. Until then, backup destinations receive
  unencrypted archives.
- Optional: if **every** port of your Panel/Playlist domain uses HTTPS, add
  `STREAMFORGE_HSTS_MAX_AGE=15552000` to `/etc/streamforge.env` and restart
  `streamforge` and `streamforge-public`. Leave it off if any Playlist or Web
  Player URL on that domain uses plain HTTP.

---

## 🩹 Manual Main hotfix

`scripts/apply_main_hotfixes.py` applies the Main fixes from v12.73–v12.75
(backup restore, branding errors, admin re-seeding, Secure session cookie) to
an installed `app/main.py`. Each fix is skipped when already present, so
re-running is safe. The v12.73 and v12.75 updaters run it automatically.

```bash
sudo cp -a /opt/streamforge/app/main.py /opt/streamforge/app/main.py.bak
sudo python3 scripts/apply_main_hotfixes.py /opt/streamforge/app/main.py
sudo systemctl restart streamforge streamforge-public
```

The v12.76 security changes span several files and new modules, so install
them only with `streamforge-update` (it runs `scripts/apply_v1276_security.py`
on staged copies, adds the new modules and the root helper, and can roll back).

---

## 🗑️ Uninstall

```bash
sudo streamforge-uninstall main      # or: node | all | auto; add --yes to skip the prompt
```

Back up `/var/lib/streamforge`, `/etc/streamforge.env` and `/opt/streamforge/logo`
first if you may need the data again.
