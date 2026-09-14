#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${1:-auto}"
YES=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
  esac
done

usage(){
  cat <<'EOF'
Usage:
  streamforge-uninstall main [--yes]
  streamforge-uninstall node [--yes]
  streamforge-uninstall all  [--yes]
  streamforge-uninstall auto [--yes]
EOF
}

if [[ "$MODE" == "-h" || "$MODE" == "--help" ]]; then usage; exit 0; fi
if [[ "$MODE" == "--yes" || "$MODE" == "-y" ]]; then MODE="auto"; YES=1; fi
case "$MODE" in main|node|all|auto) ;; *) usage >&2; exit 2 ;; esac

[[ "$(id -u)" -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }

has_main(){
  [[ -e /opt/streamforge/app || -e /etc/streamforge.env || -e /etc/systemd/system/streamforge.service ]]
}
has_node(){
  [[ -e /opt/streamforge-node/app.py || -e /etc/streamforge-node.env || -e /etc/systemd/system/streamforge-node.service || -e /etc/systemd/system/streamforge-node-public.service ]]
}

if [[ "$MODE" == "auto" ]]; then
  if has_main && has_node; then MODE="all"
  elif has_main; then MODE="main"
  elif has_node; then MODE="node"
  else
    echo "No StreamForge installation detected."
    exit 0
  fi
fi

if [[ "$YES" -ne 1 ]]; then
  echo "WARNING: this permanently removes StreamForge $MODE data/config/services/backups."
  read -r -p "Type UNINSTALL to continue: " answer
  [[ "$answer" == "UNINSTALL" ]] || { echo "Cancelled."; exit 1; }
fi

stop_disable(){
  systemctl disable --now "$1" >/dev/null 2>&1 || true
  systemctl stop "$1" >/dev/null 2>&1 || true
}

remove_main(){
  echo "[StreamForge] Removing Main Server..."
  # STREAMFORGE_RESETUSER_COMMAND_UNINSTALL_V3015:
  for unit in \
    streamforge.service streamforge-public.service streamforge-channel-supervisor.service \
    streamforge-main-access.path streamforge-main-access.service \
    streamforge-main-system.path streamforge-main-system.service \
    streamforge-geoip-update.timer streamforge-geoip-update.service; do
    stop_disable "$unit"
  done

  rm -f \
    /etc/systemd/system/streamforge.service \
    /etc/systemd/system/streamforge-public.service \
    /etc/systemd/system/streamforge-channel-supervisor.service \
    /etc/systemd/system/streamforge-main-access.path \
    /etc/systemd/system/streamforge-main-access.service \
    /etc/systemd/system/streamforge-main-system.path \
    /etc/systemd/system/streamforge-main-system.service \
    /etc/systemd/system/streamforge-geoip-update.timer \
    /etc/systemd/system/streamforge-geoip-update.service \
    /usr/local/sbin/streamforge-apply-main-access \
    /usr/local/sbin/streamforge-main-system-control \
    /usr/local/bin/streamforge-capacity-audit \
    /usr/local/bin/streamforge \
    /usr/local/bin/streamforge-update \
    /etc/streamforge.env \
    /etc/default/streamforge \
    /etc/logrotate.d/streamforge \
    /etc/nginx/sites-enabled/streamforge \
    /etc/nginx/sites-available/streamforge

  rm -rf \
    /opt/streamforge \
    /var/lib/streamforge \
    /var/backups/streamforge \
    /var/cache/streamforge \
    /run/streamforge \
    /etc/systemd/system/streamforge.service.d \
    /etc/systemd/system/nginx.service.d/streamforge-high-concurrency.conf \
    /etc/sysctl.d/99-streamforge-high-concurrency.conf

  rm -f /etc/nginx/conf.d/streamforge*.conf 2>/dev/null || true
  rm -rf /tmp/streamforge-update-* /tmp/streamforge-main-* 2>/dev/null || true

  if id streamforge >/dev/null 2>&1; then
    userdel streamforge >/dev/null 2>&1 || true
  fi
  echo "[StreamForge] Main Server removed."
}

remove_node(){
  echo "[StreamForge] Removing Remote Node..."
  # STREAMFORGE_NODE_PUBLIC_SYSTEMD_UNINSTALL_V79
  stop_disable streamforge-node-public.service
  stop_disable streamforge-node.service

  rm -f \
    /etc/systemd/system/streamforge-node-public.service \
    /etc/systemd/system/streamforge-node.service \
    /etc/streamforge-node.env \
    /etc/default/streamforge-node \
    /etc/logrotate.d/streamforge-node

  rm -rf \
    /etc/systemd/system/streamforge-node.service.d \
    /opt/streamforge-node \
    /var/lib/streamforge-node \
    /var/backups/streamforge-node \
    /var/cache/streamforge-node \
    /run/streamforge-node

  rm -f /etc/nginx/conf.d/streamforge-node*.conf 2>/dev/null || true
  rm -rf /tmp/streamforge-node-* /tmp/streamforge-uninstall-* 2>/dev/null || true

  if id streamforge-node >/dev/null 2>&1; then
    userdel streamforge-node >/dev/null 2>&1 || true
  fi
  echo "[StreamForge] Remote Node removed."
}

case "$MODE" in
  main) remove_main ;;
  node) remove_node ;;
  all) remove_node; remove_main ;;
esac

systemctl daemon-reload >/dev/null 2>&1 || true
systemctl reset-failed >/dev/null 2>&1 || true

if command -v nginx >/dev/null 2>&1; then
  nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
fi
if command -v sysctl >/dev/null 2>&1; then
  sysctl --system >/dev/null 2>&1 || true
fi

# Remove the uninstall command itself when no StreamForge component remains.
if ! has_main && ! has_node; then
  rm -f /usr/local/sbin/streamforge-uninstall
fi

echo "[StreamForge] Uninstall complete."
