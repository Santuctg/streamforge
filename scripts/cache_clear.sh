#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${1:-auto}"

usage(){
  cat <<'EOF'
Usage:
  streamforge-cache-clear [auto|main|node|all]

Clears safe StreamForge/application/package caches only.
It does NOT delete the database, channel configuration, logos, backups,
active HLS data, or user data.
EOF
}

if [[ "$MODE" == "-h" || "$MODE" == "--help" ]]; then
  usage
  exit 0
fi
case "$MODE" in auto|main|node|all) ;; *) usage >&2; exit 2 ;; esac

[[ "$(id -u)" -eq 0 ]] || { echo "ERROR: run with sudo/root" >&2; exit 1; }

has_main(){ [[ -d /opt/streamforge || -d /var/lib/streamforge ]]; }
has_node(){ [[ -d /opt/streamforge-node || -d /var/lib/streamforge-node ]]; }

if [[ "$MODE" == "auto" ]]; then
  if has_main && has_node; then MODE="all"
  elif has_main; then MODE="main"
  elif has_node; then MODE="node"
  else MODE="all"
  fi
fi

clear_common(){
  rm -rf /root/.cache/pip /tmp/pip-* /tmp/pip-build-* /tmp/pip-install-* 2>/dev/null || true
  if command -v apt-get >/dev/null 2>&1; then
    apt-get clean >/dev/null 2>&1 || true
  fi
}

clear_main(){
  find /opt/streamforge -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find /opt/streamforge -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
  rm -rf /var/cache/streamforge/* 2>/dev/null || true
  rm -f /opt/streamforge/.gunicorn 2>/dev/null || true
  echo "[StreamForge] Main cache cleared."
}

clear_node(){
  find /opt/streamforge-node -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find /opt/streamforge-node -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
  rm -rf /var/cache/streamforge-node/* 2>/dev/null || true
  echo "[StreamForge] Node cache cleared."
}

clear_common
case "$MODE" in
  main) clear_main ;;
  node) clear_node ;;
  all) clear_main; clear_node ;;
esac

echo "[StreamForge] Package/temp cache cleared."
