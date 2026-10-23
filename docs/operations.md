# StreamForge operations guide

This guide covers running a StreamForge Main Server and its Remote Nodes:
installing, updating, recovering access, backups, and the security settings
that matter in production. Commands assume Debian/Ubuntu and root (`sudo`).

## Main Server layout

| Item | Location / name |
| --- | --- |
| Application code | `/opt/streamforge` (`app/main.py`, `node_agent/`, `scripts/`) |
| Data | `/var/lib/streamforge` (SQLite `streamforge.db`, `hls/`, `backups/`, `restore-inbox/`) |
| Configuration | `/etc/streamforge.env` (secret key, admin seed, paths) |
| Logos | `/opt/streamforge/logo` |
| Updater backups | `/var/backups/streamforge/v<version>-<timestamp>/` |
| Nginx site | `/etc/nginx/sites-available/streamforge` |

| systemd unit | Role |
| --- | --- |
| `streamforge` | Panel/API control worker (gunicorn on `127.0.0.1:8800`) |
| `streamforge-public` | Playback/playlist workers (`127.0.0.1:8811`) |
| `streamforge-channel-supervisor` | Owns and restarts the Main FFmpeg processes |
| `nginx` | Public listener on 80/443; forwards to the workers above |
| `streamforge-main-access.path`, `streamforge-main-system.path` | Apply Panel access/domain changes and privileged actions (restart, reboot, restore) requested from the Panel |
| `streamforge-main-tls.timer`, `streamforge-geoip-update.timer` | TLS renewal and GeoIP database refresh |
| `redis-server` | Optional shared playback/session state; Main falls back to local state without it |

Check everything at once:

```bash
systemctl is-active streamforge streamforge-public streamforge-channel-supervisor nginx
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8800/login   # expect 200
journalctl -u streamforge -n 100 --no-pager
```

## Fresh install

1. Download the release ZIP from
   [GitHub Releases](https://github.com/Santuctg/streamforge/releases),
   verify its SHA-256, and extract it.
2. Run the installer from the extracted folder:

   ```bash
   sudo bash scripts/install.sh
   ```

   It installs the system packages (Python, FFmpeg, Nginx, Redis, certbot,
   geoipupdate), creates the `streamforge` service user, writes
   `/etc/streamforge.env` with a random `STREAMFORGE_SECRET_KEY`, and enables
   the services listed above.
3. Open `http://SERVER_IP` and sign in as `admin` / `ChangeMe123!`.
4. **Change that password immediately** (Panel users), or create your own
   Super Admin and delete `admin`. The env-configured admin is only created
   when the database has no Panel admin at all; a deleted `admin` is not
   re-created on restart or update (since v12.73).

> `install.sh` is for **new servers only**. On an existing server it
> overwrites the installed `app/main.py`, which the updaters patch in place.
> Always update with `streamforge-update`.

## Updating the Main Server

Each release ships a scoped updater, `scripts/force_update_v<version>.sh`,
that only accepts specific installed versions (usually the previous release),
backs up what it touches, verifies the result and rolls back on failure.
Releases must therefore be applied **in order**.

```bash
cat /opt/streamforge/VERSION                       # current version
cd /root
curl -fL -o streamforge-X.YY.zip \
  https://github.com/Santuctg/streamforge/releases/download/vX.YY/streamforge-X.YY.zip
sha256sum streamforge-X.YY.zip                     # compare with the published hash
sudo streamforge-update --force /root/streamforge-X.YY.zip
```

A successful run ends with `SUCCESS: ...`. If the updater refuses with
`requires installed Main vA.BB`, install the intermediate releases first.

Typical scope of a Main-only release: patch a staged copy of the installed
`app/main.py` (or copy specific templates), update `VERSION` and
`node_agent/VERSION`, then restart only `streamforge` and
`streamforge-public`. Nginx, the channel supervisor and running streams are
not restarted unless the release notes say so.

### Manual rollback of an update

Every updater writes a backup directory first:

```bash
B=$(ls -d /var/backups/streamforge/v<version>-* | tail -1); ls -R "$B"
sudo cp -a "$B/app/main.py"        /opt/streamforge/app/main.py
sudo cp -a "$B/VERSION"            /opt/streamforge/VERSION
sudo cp -a "$B/node_agent/VERSION" /opt/streamforge/node_agent/VERSION
sudo systemctl restart streamforge streamforge-public
```

Restore exactly the files present in that backup directory.

### Main hotfix patcher

`scripts/apply_main_hotfixes.py` applies the Main fixes released in
v12.73–v12.75 to an installed `app/main.py`; each fix is skipped when already
present, so it is safe to re-run. The v12.73 and v12.75 updaters use it
automatically; manual use:

```bash
sudo cp -a /opt/streamforge/app/main.py /opt/streamforge/app/main.py.bak
sudo python3 scripts/apply_main_hotfixes.py /opt/streamforge/app/main.py
sudo systemctl restart streamforge streamforge-public
```

## Remote Nodes

- **Add a Node**: Panel → **Nodes**. "Add manually" stores the Node URL and
  token; "Auto install remote node" also installs the agent over SSH
  (`scripts/install_node_agent.sh`, options such as `--token`, `--port`,
  `--public-port`, `--hostname`, `--panel-url`).
- **Update Nodes**: updating Main updates only the *bundled* Node Agent. Push
  it to running Nodes from Panel → **Node Updates** and confirm each Node
  reports the new version.
- Node services: `streamforge-node` (control API and Node Panel),
  `streamforge-node-public` (playback, `127.0.0.1:8821`), and the
  `streamforge-node-tls` path/timer.
- Nodes pull every Main channel from
  `/relay/<relay-key>/<channel>/index.m3u8` on the Main Server. The relay key
  is derived from `STREAMFORGE_SECRET_KEY` (see below).

## Backups and restore

Panel → **Backups** creates archives in `/var/lib/streamforge/backups`
(retained locally; remote targets such as SMB/Google Drive are optional).
A restore — from a saved archive or an uploaded `.tar.gz` — is validated
(allowed paths only, no links/devices), executed by the privileged
`streamforge-main-system` helper, preceded by a safety backup, and keeps the
current server's Panel access URL so you are not locked out.

Logos only: `sudo streamforge restorelogos /absolute/path/to/backup.tar.gz`.

## Recovery commands

| Situation | Command |
| --- | --- |
| Forgot the admin password / no Super Admin left | `sudo streamforge resetuser` (prompts for username and a 10+ character password) |
| Wrong Panel/Playlist domain or URL lock | `sudo streamforge resetdomain [http://IP-or-domain/optional-path]` |
| Locked out by Panel IP/ASN access rules | `sudo streamforge resetpanelaccess` |
| Free disk used by package/application caches (never deletes the database, channels, logos, backups or active HLS) | `sudo streamforge-cache-clear [auto\|main\|node\|all]` |

Five failed Panel logins from one IP within 15 minutes block that IP from
logging in for 3 hours (Main and Node panels share the counter). Panel users →
Security · Panel Login · Blocked IPs lists them; a Super Admin can unblock an
IP after reauthenticating.

## Security notes

- **Admin password**: never leave `admin` / `ChangeMe123!` in place. The
  value in `/etc/streamforge.env` is only used to seed a brand-new database.
- **`STREAMFORGE_SECRET_KEY`** must be a long random value (the installer
  generates 64 hex characters). Keep it private.
- **Do not rotate `STREAMFORGE_SECRET_KEY` on a running deployment** unless
  you are prepared for the consequences. The same key derives:
  - Panel login sessions (everyone is logged out);
  - **the relay keys Remote Nodes use to pull channels** (all Node channels
    stop until the Nodes are resynchronised with the new relay URLs);
  - **the public stream keys in viewer playlist links** (existing links stop
    working);
  - encrypted playback keys, and the encrypted copies of saved secrets:
    backup target passwords, MaxMind/IPinfo keys and saved Node SSH
    passwords (they must be entered again).

  If the key was rotated by mistake, restore the previous value in
  `/etc/streamforge.env` and run
  `sudo systemctl restart streamforge streamforge-public streamforge-channel-supervisor`.
- **HTTPS**: since v12.75 the Main session cookie is marked `Secure` whenever
  the request arrived over HTTPS (from the `X-Forwarded-Proto` header Nginx
  sets). Plain-HTTP access keeps working without it.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Panel does not load | `systemctl is-active streamforge nginx`; `journalctl -u streamforge -n 100`; `curl http://127.0.0.1:8800/login` |
| Playback fails but Panel works | `systemctl is-active streamforge-public`; `curl http://127.0.0.1:8811/health` (expects `ok`) |
| Main channels stopped / restarting | `journalctl -u streamforge-channel-supervisor -n 100`; channel → Stream logs in the Panel |
| All Remote Node channels down at once | Was `STREAMFORGE_SECRET_KEY` changed? Compare `/etc/streamforge.env` with your backup (see Security notes) |
| Updater refuses to run | Read the `requires installed Main ...` message and apply the missing releases in order |
| Branding/settings save shows an error | Expected for invalid input (empty brand name, unsupported image); fixed to show a message instead of a 500 in v12.73 |
