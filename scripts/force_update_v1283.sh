#!/usr/bin/env bash
set -Eeuo pipefail
# STREAMFORGE_V1283_HARDENING_UPDATER:
# Main: staged patch of installed sources (scripts/apply_v1283_hardening.py)
# for the rightmost client-IP resolution, the Nginx real_ip trust block, the
# always-verified Node TLS and the secret-key startup hard fail, plus both
# VERSION files and the new app/client_ip.py module. The installed Nginx site
# receives the same real_ip block (nginx -t guarded, then reloaded), and a
# weak or missing STREAMFORGE_SECRET_KEY in /etc/streamforge.env is repaired
# before the workers restart, so the hard failure never fires on an upgraded
# server. Bundled Node Agent: node_agent/app.py is byte-identical to v12.82
# (verified against the pinned blob) and is restored only when an installed
# tree carries something else. The channel supervisor keeps running; Remote
# Nodes update separately.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="${STREAMFORGE_ROOT:-/opt/streamforge}"
BACKUP_ROOT="${STREAMFORGE_BACKUP_ROOT:-/var/backups/streamforge}"
ENV_FILE="${STREAMFORGE_ENV_FILE:-/etc/streamforge.env}"
NGINX_SITE="${STREAMFORGE_NGINX_SITE:-/etc/nginx/sites-available/streamforge}"
TEST="${STREAMFORGE_HOTFIX_TEST_MODE:-0}"
TARGET="12.83"
OLD_NODE_BLOB="2d9595392e713068cc4c0c8f5243ef55b4f5472b"
log(){ echo "[StreamForge v12.83] $*"; }
die(){ echo "[StreamForge v12.83] ERROR: $*" >&2; exit 1; }
[[ "$TEST" == "1" || $EUID -eq 0 ]] || die "root required"
[[ "$(< "$SRC/VERSION")" == "$TARGET" && "$(< "$SRC/node_agent/VERSION")" == "$TARGET" ]] || die "package version mismatch"
case "$(< "$ROOT/VERSION")" in 12.82|12.83) ;; *) die "requires installed Main v12.82 or v12.83";; esac
PATCH_FILES=(
  app/access_control.py
  app/config.py
  app/load_balancer.py
  app/main.py
  app/models.py
  app/node_manager.py
  app/panel_login_guard.py
  app/templates/node_form.html
  app/templates/node_install.html
  app/viewer_tracking.py
  deploy/nginx.conf
)
# STREAMFORGE_V1283_NEW_CLIENT_IP_MODULE: app/client_ip.py does not exist in
# v12.82 trees; it is copied whole (the v12.63 login-guard precedent) and
# removed again by a rollback when the update fails.
NEW_FILES=(
  app/client_ip.py
)
for file in "${PATCH_FILES[@]}" "${NEW_FILES[@]}" VERSION node_agent/VERSION; do
  [[ -f "$SRC/$file" ]] || die "package is missing $file"
done
for file in "${PATCH_FILES[@]}" VERSION node_agent/VERSION; do
  [[ -f "$ROOT/$file" ]] || die "installed Main is missing $file; repair it first"
done
[[ "$(< "$ROOT/node_agent/VERSION")" == "12.82" || "$(< "$ROOT/node_agent/VERSION")" == "12.83" ]] || die "requires bundled Node Agent v12.82 or v12.83"
# STREAMFORGE_RELEASE_SOURCE_NO_PRESTAGE_BYTECODE_V1252: compile only inside
# the staged copy so no __pycache__ can be written into the release sources.
STAGING="$(mktemp -d)"
cleanup(){ rm -rf "$STAGING"; }
trap cleanup EXIT
STAGE="$STAGING/tree"
mkdir -p "$STAGE"
cp -a "$ROOT/." "$STAGE/"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
for file in "${PATCH_FILES[@]}"; do cp -a "$ROOT/$file" "$STAGE/$file"; done
for file in "${NEW_FILES[@]}"; do
  mkdir -p "$STAGE/$(dirname "$file")"
  cp -a "$SRC/$file" "$STAGE/$file"
done
python3 "$SRC/scripts/apply_v1283_hardening.py" "$STAGE" "${PATCH_FILES[@]}" || die "hardening patch preflight failed"
for file in "${PATCH_FILES[@]}"; do
  grep -Fq 'STREAMFORGE_V1283_' "$STAGE/$file" || die "staged $file is missing the v12.83 marker"
done
for file in "${NEW_FILES[@]}"; do
  grep -Fq 'forwarded_client_ip' "$STAGE/$file" || die "staged $file is missing the shared client-IP helper"
done
python3 - "$STAGE" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
for path in root.rglob('*.py'):
    compile(path.read_text(encoding='utf-8'), str(path), 'exec')
PY
# The installed Nginx site is a copy made at install time, so it needs the
# same real_ip block. Verify the exact patch on a staged copy first: the
# anchor form when the site still matches deploy/nginx.conf, the prepend form
# when runtime management has rewritten it around the anchor.
NGINX_PATCHED=0
if [[ -f "$NGINX_SITE" ]]; then
  cp -a "$NGINX_SITE" "$STAGING/nginx-site"
  python3 - "$STAGING/nginx-site" "$SRC/scripts/apply_v1283_hardening.json" <<'PY' || die "Nginx site real_ip preflight failed"
import difflib, json, pathlib, sys
site = pathlib.Path(sys.argv[1])
spec = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))["files"]["deploy/nginx.conf"]
text = site.read_text(encoding="utf-8")
if "STREAMFORGE_V1283_NGINX_REAL_IP_V1" not in text:
    for original, updated in spec["hunks"]:
        if text.count(original) == 1:
            text = text.replace(original, updated, 1)
            continue
        # The anchor is gone (a runtime-managed rewrite): prepend only the
        # added real_ip lines, never the hunk's surrounding context, which
        # could duplicate unrelated configuration in a foreign file.
        old_lines, new_lines = original.splitlines(True), updated.splitlines(True)
        added = []
        for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
            if tag in ("insert", "replace"):
                added.extend(new_lines[j1:j2])
        block = "".join(added)
        if not block.endswith("\n"):
            block += "\n"
        text = block + text
    site.write_text(text, encoding="utf-8")
PY
fi
mkdir -p "$BACKUP_ROOT"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUP_ROOT/v1283-$STAMP"
FILES=(node_agent/app.py VERSION node_agent/VERSION "${PATCH_FILES[@]}")
mkdir -p "$BACKUP"
for file in "${FILES[@]}"; do
  [[ -f "$ROOT/$file" ]] || continue
  mkdir -p "$BACKUP/$(dirname "$file")"
  cp -a "$ROOT/$file" "$BACKUP/$file"
done
# Remember which new modules already existed, so a rollback removes only the
# ones this update created, and back up the env file and Nginx site the same
# way as every patched source.
NEW_EXISTING=()
for file in "${NEW_FILES[@]}"; do
  if [[ -e "$ROOT/$file" ]]; then
    NEW_EXISTING+=("$file")
    mkdir -p "$BACKUP/$(dirname "$file")"
    cp -a "$ROOT/$file" "$BACKUP/$file"
  fi
done
if [[ -f "$ENV_FILE" ]]; then
  mkdir -p "$BACKUP"
  cp -a "$ENV_FILE" "$BACKUP/streamforge.env"
  chmod 600 "$BACKUP/streamforge.env"
fi
if [[ -f "$NGINX_SITE" ]]; then
  mkdir -p "$BACKUP"
  cp -a "$NGINX_SITE" "$BACKUP/nginx-site"
fi
log "Backup created: $BACKUP"
ROLLBACK=1
RESTARTED=0
rollback(){
  local rc="${1:-1}"
  trap - ERR
  if [[ "$ROLLBACK" == "1" ]]; then
    log "Rolling back from $BACKUP"
    for file in "${FILES[@]}"; do
      [[ -f "$BACKUP/$file" ]] || continue
      mkdir -p "$ROOT/$(dirname "$file")"
      cp -a "$BACKUP/$file" "$ROOT/$file" || true
    done
    for file in "${NEW_FILES[@]}"; do
      local existed=0
      for keep in "${NEW_EXISTING[@]+"${NEW_EXISTING[@]}"}"; do
        [[ "$keep" == "$file" ]] && existed=1
      done
      if [[ "$existed" == "1" && -f "$BACKUP/$file" ]]; then
        cp -a "$BACKUP/$file" "$ROOT/$file" || true
      else
        rm -f "$ROOT/$file"
      fi
    done
    [[ -f "$BACKUP/streamforge.env" ]] && cp -a "$BACKUP/streamforge.env" "$ENV_FILE" || true
    [[ -f "$BACKUP/nginx-site" ]] && cp -a "$BACKUP/nginx-site" "$NGINX_SITE" || true
    find "$ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    # Only touch running services when they were already reloaded once; a
    # pre-write refusal must not disturb the running Panel or Nginx.
    if [[ "$TEST" != 1 ]]; then
      if [[ "$NGINX_PATCHED" == "1" ]] && command -v nginx >/dev/null 2>&1; then
        nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
      fi
      if [[ "$RESTARTED" == "1" ]]; then
        systemctl restart streamforge >/dev/null 2>&1 || true
        systemctl restart streamforge-public >/dev/null 2>&1 || true
      fi
    fi
  fi
  exit "$rc"
}
trap 'rollback $?' ERR
atomic_copy(){ local src="$1" dest="$2"; cp -a "$src" "${dest}.tmp.$$"; mv -f "${dest}.tmp.$$" "$dest"; }
# The Node Agent payload is unchanged from v12.82; restore it only when the
# installed tree carries something else (a repaired or drifted file).
SOURCE_NODE_BLOB="$(shasum -a 1 "$SRC/node_agent/app.py" | awk '{print $1}')"
INSTALLED_NODE_BLOB="$(shasum -a 1 "$ROOT/node_agent/app.py" | awk '{print $1}')"
[[ "$SOURCE_NODE_BLOB" == "$OLD_NODE_BLOB" ]] || die "package Node Agent blob drifted from the pinned v12.82 blob"
if [[ "$INSTALLED_NODE_BLOB" != "$SOURCE_NODE_BLOB" ]]; then
  log "Restoring the Node Agent payload to the pinned v12.82 bytes"
  atomic_copy "$SRC/node_agent/app.py" "$ROOT/node_agent/app.py"
fi
python3 "$SRC/scripts/apply_v1283_hardening.py" "$ROOT" "${PATCH_FILES[@]}"
for file in "${NEW_FILES[@]}"; do
  mkdir -p "$ROOT/$(dirname "$file")"
  atomic_copy "$SRC/$file" "$ROOT/$file"
done
printf '%s\n' "$TARGET" > "$ROOT/VERSION"
printf '%s\n' "$TARGET" > "$ROOT/node_agent/VERSION"
[[ "$(< "$ROOT/VERSION")" == "$TARGET" && "$(< "$ROOT/node_agent/VERSION")" == "$TARGET" ]] || die "version write verification failed"
# STREAMFORGE_V1283_SECRET_KEY_ENV_REPAIR_V1: v12.83 refuses to start on a
# missing/development/short STREAMFORGE_SECRET_KEY, so the update repairs a
# weak key in the env file before the workers restart. A strong key is never
# touched, and the env file backup rolls the edit back with everything else.
if [[ -f "$ENV_FILE" ]]; then
  python3 - "$ENV_FILE" <<'PY' || die "STREAMFORGE_SECRET_KEY repair failed"
import pathlib, os, re, secrets, stat, sys, tempfile
path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
pattern = re.compile(r"^STREAMFORGE_SECRET_KEY=(.*)$", re.M)
values = pattern.findall(text)
key = values[-1].strip() if values else ""
if key and key != "dev-change-me-now" and len(key) >= 32:
    print("STREAMFORGE_SECRET_KEY is already strong; left untouched")
else:
    fresh = secrets.token_hex(32)
    if values:
        text = pattern.sub(f"STREAMFORGE_SECRET_KEY={fresh}", text)
    else:
        text = text.rstrip("\n") + f"\nSTREAMFORGE_SECRET_KEY={fresh}\n"
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".streamforge.env.")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(tmp, stat.S_IMODE(path.stat().st_mode) or 0o600)
    os.replace(tmp, path)
    print("STREAMFORGE_SECRET_KEY repaired with a fresh 64-hex value")
PY
else
  log "No $ENV_FILE on this host; nothing to repair"
fi
# The Nginx site carries the staged real_ip block; test the full configuration
# before reloading so a syntax or include problem rolls the update back.
if [[ -f "$NGINX_SITE" ]]; then
  atomic_copy "$STAGING/nginx-site" "$NGINX_SITE"
  NGINX_PATCHED=1
  if [[ "$TEST" != 1 ]] && command -v nginx >/dev/null 2>&1; then
    nginx -t
    systemctl reload nginx
  fi
fi
# Controlled failure for the release test: every contract write already
# happened, so the rollback path must undo all of it.
if [[ "${STREAMFORGE_V1283_TEST_FAIL_AFTER_WRITE:-0}" == "1" ]]; then
  log "Controlled failure after write"
  rollback 1
fi
find "$ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
# STREAMFORGE_V1283_UPDATER_RESTARTS_MAIN_WORKERS_V1: patching the sources on
# disk is not enough — the running Gunicorn workers keep serving the previous
# release (and the previous security posture) until they reload. Restart only
# the Main HTTP workers: the channel supervisor and live streams keep running.
if [[ "$TEST" != 1 ]]; then
  RESTARTED=1
  log "Restarting Main HTTP workers only (channel supervisor and streams keep running)"
  systemctl restart streamforge
  systemctl restart streamforge-public
  systemctl is-active --quiet streamforge || rollback 1
  systemctl is-active --quiet streamforge-public || rollback 1
fi
ROLLBACK=0
trap - ERR
log "Main sources updated in place, Nginx real_ip applied and Main HTTP workers restarted."
log "Update Remote Nodes from the Panel: Node Updates."
