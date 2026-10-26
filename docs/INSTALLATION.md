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
VERSION=12.81
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

Updating Main updates only the **bundled** Node Agent. Push it to your Remote
Nodes afterwards from **Node Updates**.

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

The updater rolls back by itself when a step fails. To undo a *successful*
upgrade, restore the files from its backup directory:

```bash
B=$(ls -d /var/backups/streamforge/v<version>-* | tail -1)
ls -R "$B"                                   # restore exactly these files
sudo cp -a "$B/app/main.py"        /opt/streamforge/app/main.py
sudo cp -a "$B/VERSION"            /opt/streamforge/VERSION
sudo cp -a "$B/node_agent/VERSION" /opt/streamforge/node_agent/VERSION
sudo systemctl restart streamforge streamforge-public
```

---

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
