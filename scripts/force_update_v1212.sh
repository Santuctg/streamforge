#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${STREAMFORGE_SERVICE_NAME:-streamforge}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATER_BASENAME="$(basename "${BASH_SOURCE[0]}")"
SOURCE_UPDATER="$SOURCE_DIR/scripts/$UPDATER_BASENAME"
BACKUP_ROOT="${STREAMFORGE_BACKUP_DIR:-/var/backups/streamforge}"
DATA_DIR="${STREAMFORGE_DATA_DIR:-/var/lib/streamforge}"
ENV_FILE="${STREAMFORGE_ENV_FILE:-/etc/streamforge.env}"
LOGO_DIR="/opt/streamforge/logo"
ASN_FILE="/opt/streamforge/GeoLite2-ASN.mmdb"
COUNTRY_FILE="/opt/streamforge/GeoLite2-Country.mmdb"
LOCAL_NODE_DIR="${STREAMFORGE_LOCAL_NODE_DIR:-/opt/streamforge-node}"
NODE_ENV_FILE="${STREAMFORGE_NODE_ENV_FILE:-/etc/streamforge-node.env}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR=""
UPDATE_COMMITTED=0
MUTATION_STARTED=0

log(){ printf '[StreamForge v12.12] %s\n' "$*"; }
die(){ printf '[StreamForge v12.12] ERROR: %s\n' "$*" >&2; return 1; }

for arg in "$@"; do
  case "$arg" in
    --deploy-configs) ;;
    -h|--help) echo "Usage: sudo bash scripts/update.sh [--deploy-configs]"; exit 0;;
    *) die "Unknown option: $arg";;
  esac
done

[[ ${EUID} -eq 0 ]] || die "Run with sudo/root"
for cmd in systemctl rsync curl python3 nginx cmp; do command -v "$cmd" >/dev/null 2>&1 || die "$cmd not found"; done
[[ "$(cat "$SOURCE_DIR/VERSION" 2>/dev/null)" == "12.12" ]] || die "Wrong package version"
[[ "$(cat "$SOURCE_DIR/node_agent/VERSION" 2>/dev/null)" == "12.12" ]] || die "Wrong Node Agent version"
# STREAMFORGE_GITHUB_ZIP_EXECUTABLE_NORMALIZATION_V1210:
# GitHub-generated ZIP archives may omit Unix executable metadata even though
# the scripts are present. Restore it before the package integrity guards.
find "$SOURCE_DIR/scripts" "$SOURCE_DIR/node_agent" -maxdepth 1 -type f \
  \( -name '*.sh' -o -name '*.py' -o -name 'streamforge*' \) \
  -exec chmod 0755 {} +
grep -Fq 'force_update_v1212.sh' "$SOURCE_DIR/scripts/update.sh" || die "v12.12 update.sh does not select force_update_v1212.sh"
# STREAMFORGE_PACKAGE_CLEAN_V65: a release archive carries one version-specific force updater only.
mapfile -t _sf_force_updaters < <(find "$SOURCE_DIR/scripts" -maxdepth 1 -type f -name 'force_update_v*.sh' -printf '%f\n' | sort)
[[ ${#_sf_force_updaters[@]} -eq 1 && "${_sf_force_updaters[0]}" == "force_update_v1212.sh" ]] || die "v12.12 package contains stale/duplicate force updater scripts"
# STREAMFORGE_UPDATE_SELF_REFERENCE_CURRENT_UPDATER_V129:
[[ "$UPDATER_BASENAME" == "force_update_v1212.sh" ]] || die "Unexpected updater filename: $UPDATER_BASENAME"
# STREAMFORGE_V1158_MULTI_BRAND_LIVE_ASSET_GUARDS:
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_LIVE_ALIAS_ASSET_DOWNLOAD_V1158' "$SOURCE_DIR/app/main.py" || die "v11.58 Main brand live-alias/download runtime missing"
grep -Fq 'brand_logo_file: UploadFile | None = File(None)' "$SOURCE_DIR/app/main.py" || die "v11.58 Main brand logo upload handler missing"
grep -Fq 'brand_download_file: UploadFile | None = File(None)' "$SOURCE_DIR/app/main.py" || die "v11.58 Main brand Player download handler missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_ASSET_TRANSPORT_V1158' "$SOURCE_DIR/app/node_manager.py" || die "v11.58 Remote Node brand asset transport missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_BRAND_ASSET_SYNC_V1158' "$SOURCE_DIR/node_agent/app.py" || die "v11.58 Node brand asset receiver missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_JSON_MULTI_BRAND_THEME_V1158' "$SOURCE_DIR/node_agent/app.py" || die "v11.58 Node brand update.json theme missing"
grep -Fq 'brand_logo_file' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.58 brand logo upload UI missing"
grep -Fq 'brand_download_file' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.58 brand Player download UI missing"
# STREAMFORGE_V1159_WEBPLAYER_BRAND_FULL_SETTINGS_GUARDS:
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_FULL_SETTINGS_PARITY_V1159' "$SOURCE_DIR/app/main.py" || die "v11.59 Main brand settings parity runtime missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_FULL_RUNTIME_PARITY_V1159' "$SOURCE_DIR/app/main.py" || die "v11.59 Main brand effective settings runtime missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_TRANSPORT_DERIVED_FIELDS_V1159' "$SOURCE_DIR/app/node_manager.py" || die "v11.59 brand transport normalization missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_BRAND_FULL_SETTINGS_PARITY_V1159' "$SOURCE_DIR/node_agent/app.py" || die "v11.59 Node brand settings normalization missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_BRAND_EFFECTIVE_CONTROLS_V1159' "$SOURCE_DIR/node_agent/app.py" || die "v11.59 Node brand effective login/viewer controls missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_FULL_SETTINGS_UI_V1159' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.59 brand full settings UI missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_FULL_SETTINGS_JS_V1159' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.59 brand settings scoped UI JS missing"
# STREAMFORGE_V1163_WEBPLAYER_BRAND_ASSET_ROW_UI_GUARDS:
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_ASSET_SINGLE_ROW_LAYOUT_V1163' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.63 single-row brand asset UI missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_ASSET_SINGLE_ROW_LAYOUT_CSS_V1163' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.63 single-row brand asset styling missing"
! grep -Fq 'name="brand_logo_url"' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.63 brand logo URL input still present"
grep -Fq 'brand-assets-inline-grid' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.63 brand logo/favicon single-row grid missing"
grep -Fq 'brand-asset-current' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.63 current brand asset preview missing"
! grep -Fq 'name="brand_favicon_url"' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.63 brand favicon URL input still present"
grep -Fq 'Remove current favicon' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.63 current brand favicon remove control missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_VIEWER_INFO_CARD_PARITY_V1163' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.63 brand viewer info card parity markup missing"
grep -Fq 'brand-viewer-info-option' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "v11.63 brand viewer info option styling missing"
# STREAMFORGE_V1163_WEBPLAYER_BROWSER_PLAYBACK_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_SELF_HOSTED_HLSJS_V1163' "$SOURCE_DIR/app/templates/player.html" || die "v11.63 Main Web Player local HLS.js preload missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_HLSJS_LOCAL_CDN_FALLBACK_V1163' "$SOURCE_DIR/app/templates/player.html" || die "v11.63 Main Web Player HLS.js fallback loader missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_PROGRESS_WATCHDOG_V1163' "$SOURCE_DIR/app/templates/player.html" || die "v11.63 Main Web Player progress watchdog missing"
grep -Fq 'STREAMFORGE_NODE_SELF_HOSTED_HLSJS_V1163' "$SOURCE_DIR/node_agent/app.py" || die "v11.63 Node local HLS.js route/storage missing"
grep -Fq 'STREAMFORGE_NODE_SELF_HOSTED_HLSJS_BACKGROUND_FETCH_V1163' "$SOURCE_DIR/node_agent/app.py" || die "v11.63 Node background HLS.js cache fetch missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_HLSJS_LOCAL_CDN_FALLBACK_V1163' "$SOURCE_DIR/node_agent/app.py" || die "v11.63 Node Web Player HLS.js fallback loader missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_PROGRESS_WATCHDOG_V1163' "$SOURCE_DIR/node_agent/app.py" || die "v11.63 Node Web Player progress watchdog missing"
grep -Fq 'STREAMFORGE_NODE_HLSJS_UPDATE_PAYLOAD_V1163' "$SOURCE_DIR/app/node_manager.py" || die "v11.63 Node HLS.js update payload missing"
grep -Fq 'STREAMFORGE_NODE_SELF_HOSTED_HLSJS_UPDATE_V1163' "$SOURCE_DIR/node_agent/app.py" || die "v11.63 Node HLS.js update fetch missing"
grep -Fq 'STREAMFORGE_NODE_SELF_HOSTED_HLSJS_INSTALL_V1163' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v11.63 Node HLS.js installer fetch missing"
# STREAMFORGE_V1167_WEBPLAYER_NO_FALSE_RECONNECT_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_STALL_GUARD_HLS_FRESHNESS_V1167' "$SOURCE_DIR/app/ffmpeg.py" || die "v11.67 Main HLS-freshness stall guard missing"
grep -Fq 'STREAMFORGE_NODE_STALL_GUARD_HLS_FRESHNESS_V1167' "$SOURCE_DIR/node_agent/app.py" || die "v11.67 Node HLS-freshness stall guard missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_NON_DESTRUCTIVE_STALL_RECOVERY_V1167' "$SOURCE_DIR/app/templates/player.html" || die "v11.67 Main/Brand non-destructive stall recovery missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_NON_DESTRUCTIVE_STALL_RECOVERY_V1167' "$SOURCE_DIR/node_agent/app.py" || die "v11.67 Node non-destructive stall recovery missing"
grep -Fq 'STREAMFORGE_MAIN_HLS_BROWSER_SEGMENT_GRACE_V1167' "$SOURCE_DIR/app/ffmpeg.py" || die "v11.67 Main browser HLS segment grace missing"
grep -Fq 'STREAMFORGE_NODE_HLS_BROWSER_SEGMENT_GRACE_V1167' "$SOURCE_DIR/node_agent/app.py" || die "v11.67 Node browser HLS segment grace missing"
grep -Fq 'local_hls_delete_threshold = max(3, (30 + local_hls_time - 1) // local_hls_time)' "$SOURCE_DIR/app/ffmpeg.py" || die "v11.67 Main 30-second HLS retention missing"
grep -Fq 'local_hls_delete_threshold = max(3, (30 + local_hls_time - 1) // local_hls_time)' "$SOURCE_DIR/node_agent/app.py" || die "v11.67 Node 30-second HLS retention missing"
! grep -Fq "scheduleReconnect('No fresh HLS segment received. Reconnecting automatically…')" "$SOURCE_DIR/app/templates/player.html" || die "v11.67 destructive Main stalled-event reconnect still present"
! grep -Fq "retrySoon('No fresh HLS segment received. Reconnecting automatically…')" "$SOURCE_DIR/node_agent/app.py" || die "v11.67 destructive Node stalled-event reconnect still present"
! grep -Fq 'Date.now() - lastPlaybackProgressAt >= 10000' "$SOURCE_DIR/app/templates/player.html" || die "v11.67 obsolete Main currentTime-only 10s reconnect still present"
! grep -Fq 'Date.now()-lastProgressAt>=10000' "$SOURCE_DIR/node_agent/app.py" || die "v11.67 obsolete Node currentTime-only 10s reconnect still present"
# STREAMFORGE_V123_LOGIN_STATIC_REGRESSION_GUARDS:
# STREAMFORGE_V121_RUNTIME_AUDIT_FIX_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_PUBLIC_DESIRED_FFMPEG_RESERVE_V121' "$SOURCE_DIR/scripts/streamforge-public-start" || die "v12.1 Main desired-FFmpeg public worker reserve missing"
grep -Fq 'desired_local_ffmpeg' "$SOURCE_DIR/scripts/streamforge-public-start" || die "v12.1 Main desired local FFmpeg worker-state metric missing"
grep -Fq 'STREAMFORGE_MAIN_FFMPEG_STDERR_EXIT_PRESERVE_V121' "$SOURCE_DIR/app/ffmpeg.py" || die "v12.1 Main FFmpeg stderr preservation missing"
grep -Fq 'STREAMFORGE_MAIN_CLOCK_SAFE_HLS_FRESHNESS_V121' "$SOURCE_DIR/app/ffmpeg.py" || die "v12.1 Main clock-safe HLS freshness missing"
grep -Fq 'STREAMFORGE_NODE_CLOCK_SAFE_HLS_FRESHNESS_V121' "$SOURCE_DIR/node_agent/app.py" || die "v12.1 Node clock-safe HLS freshness missing"
grep -Fq 'STREAMFORGE_NODE_FFMPEG_EXIT_REASON_PRESERVE_V121' "$SOURCE_DIR/node_agent/app.py" || die "v12.1 Node FFmpeg exit-reason preservation missing"
grep -Fq 'STREAMFORGE_MAIN_SUPERVISOR_CLIENT_DISCONNECT_SAFE_V121' "$SOURCE_DIR/app/main_channel_supervisor.py" || die "v12.1 Main supervisor BrokenPipe protection missing"
grep -Fq 'STREAMFORGE_MAIN_NGINX_PUBLIC_STATIC_CACHE_V122' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v12.3 Main Nginx readable static-cache path missing"
grep -Fq '/var/cache/streamforge/main-static' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v12.3 Main published static-cache root missing"
grep -Fq 'X-StreamForge-Static-Path "nginx-direct-cache"' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v12.3 Main direct static-cache response marker missing"
grep -Fq 'STREAMFORGE_MAIN_NGINX_PUBLIC_STATIC_CACHE_PUBLISH_V122' "$SOURCE_DIR/scripts/install.sh" || die "v12.3 fresh-install static-cache publisher missing"
grep -Fq 'STREAMFORGE_MAIN_NGINX_PUBLIC_STATIC_CACHE_PUBLISH_V122' "$SOURCE_UPDATER" || die "v12.3 update static-cache publisher missing"
grep -Fq 'STREAMFORGE_MAIN_LOGIN_CLEAN_REDIRECT_V122' "$SOURCE_DIR/app/main.py" || die "v12.3 clean login redirect missing"
grep -Fq 'STREAMFORGE_MAIN_LOGIN_ADDRESS_BAR_HIDE_V122' "$SOURCE_DIR/app/templates/base.html" || die "v12.3 inline login address-bar hiding missing"
# STREAMFORGE_V123_NODE_RUNTIME_CONSISTENCY_GUARDS:
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_CROSS_PROCESS_SYNC_V123' "$SOURCE_DIR/node_agent/app.py" || die "v12.3 Node playlist-user cross-process persistence missing"
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_SIGNATURE_RELOAD_V123' "$SOURCE_DIR/node_agent/app.py" || die "v12.3 Node playlist-user signature reload missing"
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_AUTH_RELOAD_V123' "$SOURCE_DIR/node_agent/app.py" || die "v12.3 Node Xtream/WebPlayer user pre-auth reload missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_NO_STALE_USER_WRITE_V123' "$SOURCE_DIR/node_agent/app.py" || die "v12.3 Node public stale-user overwrite guard missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_PLAYLIST_REOPEN_RESERVATION_V123' "$SOURCE_DIR/node_agent/app.py" || die "v12.3 Node live-playlist reservation recovery missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_DESIRED_FFMPEG_RESERVE_V123' "$SOURCE_DIR/node_agent/public_start.py" || die "v12.3 Node desired-FFmpeg public worker reserve missing"
grep -Fq 'STREAMFORGE_NODE_V123_PUBLIC_START_ROOT_GUARD' "$SOURCE_DIR/node_agent/app.py" || die "v12.3 Node API partial-update root guard missing"
grep -Fq 'APP_VERSION == "12.3"' "$SOURCE_DIR/app/main.py" || die "v12.3 Main saved-SSH Node runtime preference missing"
grep -Fq "'ffmpeg_workload': ffmpeg_workload" "$SOURCE_DIR/node_agent/public_start.py" || die "v12.3 Node FFmpeg workload worker-state metric missing"
# STREAMFORGE_V124_WEBPLAYER_BRAND_RUNTIME_GUARDS:
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_ALIAS_LIFECYCLE_V124' "$SOURCE_DIR/app/main.py" || die "v12.4 Web Player brand alias lifecycle fix missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_SIGNATURE_RELOAD_V124' "$SOURCE_DIR/node_agent/app.py" || die "v12.4 Node access snapshot signature reload missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_BRAND_CROSS_PROCESS_RELOAD_V124' "$SOURCE_DIR/node_agent/app.py" || die "v12.4 Node brand cross-process reload missing"
# STREAMFORGE_V125_AUTHORITATIVE_RUNTIME_GUARDS:
grep -Fq 'STREAMFORGE_NODE_TEST_SYNC_LOCAL_USER_AUTHORITATIVE_V125' "$SOURCE_DIR/node_agent/app.py" || die "v12.5 Test & Sync Node-local user preservation missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_SYNC_USER_IMMUTABLE_V125' "$SOURCE_DIR/node_agent/app.py" || die "v12.5 access sync user-registry isolation missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_WATCHER_USER_IMMUTABLE_V125' "$SOURCE_DIR/node_agent/app.py" || die "v12.5 access watcher user-registry isolation missing"
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_NO_READ_SIDE_WRITE_V125' "$SOURCE_DIR/node_agent/app.py" || die "v12.5 user read-side write removal missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_BRAND_DISK_AUTHORITATIVE_V125' "$SOURCE_DIR/node_agent/app.py" || die "v12.5 Node brand disk-authoritative lookup missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_SCHEME_INHERIT_V125' "$SOURCE_DIR/app/main.py" || die "v12.5 Web Player brand scheme inheritance missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_HOST_RETIRE_V125' "$SOURCE_DIR/app/main.py" || die "v12.5 Web Player brand host retirement missing"
# STREAMFORGE_V127_NODE_RUNTIME_OWNERSHIP_AND_PUBLIC_ROUTE_GUARDS:
grep -Fq 'STREAMFORGE_NODE_TEST_SYNC_MODE_USER_IMMUTABLE_V127' "$SOURCE_DIR/node_agent/app.py" || die "v12.7 Test & Sync mode user-registry isolation missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_USER_SYNC_STREAM_USER_IMMUTABLE_V127' "$SOURCE_DIR/node_agent/app.py" || die "v12.7 panel-user sync user-registry isolation missing"
grep -Fq 'STREAMFORGE_NODE_CATEGORY_STATE_SEPARATE_WRITER_V127' "$SOURCE_DIR/node_agent/app.py" || die "v12.7 separate category writer missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_ACCESS_PATCH_V127' "$SOURCE_DIR/node_agent/app.py" || die "v12.7 Web Player download access patch missing"
grep -Fq 'STREAMFORGE_NODE_LEGACY_XTREAM_DIRECT_ROUTE_V127' "$SOURCE_DIR/node_agent/app.py" || die "v12.7 legacy Xtream route classification missing"
grep -Fq 'STREAMFORGE_NODE_LEGACY_XTREAM_DIRECT_ENDPOINT_V127' "$SOURCE_DIR/node_agent/app.py" || die "v12.7 legacy Xtream endpoint missing"
grep -Fq 'STREAMFORGE_NODE_STREAM_ROOT_PUBLIC_BACKEND_V127' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v12.7 stream-root public backend routing missing"
grep -Fq 'STREAMFORGE_NODE_LEGACY_XTREAM_DIRECT_NGINX_V127' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v12.7 legacy Xtream Nginx public routing missing"
# STREAMFORGE_V128_HTTP_BRAND_ROOT_GUARD:
grep -Fq 'STREAMFORGE_NODE_HTTP_HOST_ROOT_PUBLIC_BACKEND_V128' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v12.8 HTTP Web Player Brand root public routing missing"
grep -Fq 'def nginx_http_front_blocks(' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v12.8 host-aware HTTP frontend builder missing"
# STREAMFORGE_V129_NODE_PLAYLIST_USER_EXPIRY_EDIT_GUARD:
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_EDIT_EXPIRY_V129' "$SOURCE_DIR/node_agent/app.py" || die "v12.12 Node Playlist User expiry edit support missing"
grep -Fq 'current.expires_at = submitted_expires_at' "$SOURCE_DIR/node_agent/app.py" || die "v12.12 Node Playlist User expiry save path missing"
# STREAMFORGE_V1210_NODE_PLAY_START_HOT_PATH_GUARDS:
grep -Fq 'STREAMFORGE_NODE_PUBLIC_SHARED_PANEL_CONNECTIVITY_V1210' "$SOURCE_DIR/node_agent/app.py" || die "v12.12 shared Main-connectivity playback gate missing"
grep -Fq 'PANEL_CONNECTIVITY_FILE' "$SOURCE_DIR/node_agent/app.py" || die "v12.12 shared Main-connectivity state file missing"
grep -Fq 'STREAMFORGE_NODE_SHARED_HEARTBEAT_FAST_PRIME_V1210' "$SOURCE_DIR/node_agent/app.py" || die "v12.12 shared heartbeat startup prime missing"
grep -Fq 'if NODE_MODE == "public":' "$SOURCE_DIR/node_agent/app.py" || die "v12.12 public workers can still run foreground Main heartbeat"
grep -Fq 'STREAMFORGE_NODE_DIRECT_STREAM_SINGLE_READY_V1210' "$SOURCE_DIR/node_agent/app.py" || die "v12.12 single-channel direct readiness lookup missing"
grep -Fq 'STREAMFORGE_NODE_DIRECT_STREAM_HLS_FAST_READY_V1210' "$SOURCE_DIR/node_agent/app.py" || die "v12.12 direct stream local-HLS readiness fast path missing"
grep -Fq 'if channel is None or not _direct_channel_hls_ready(channel):' "$SOURCE_DIR/node_agent/app.py" || die "v12.12 direct stream lookup does not validate only selected HLS channel"
grep -Fq 'STREAMFORGE_HLSJS_VERSION:-1.7.1' "$SOURCE_DIR/scripts/fetch_hlsjs.sh" || die "v11.63 HLS.js 1.7.1 fetch default missing"
# STREAMFORGE_V1151_PUBLIC_ALIAS_RUNTIME_RELOAD_GUARDS:
grep -Fq 'STREAMFORGE_NODE_PUBLIC_OUTER_ACCESS_RELOAD_V1151' "$SOURCE_DIR/node_agent/app.py" || die "v11.51 outer access-policy reload missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_ACCESS_RUNTIME_RELOAD_V1151' "$SOURCE_DIR/node_agent/app.py" || die "v11.51 duplicate inner access-policy stat removal missing"
grep -Fq 'STREAMFORGE_NODE_GATEWAY_ERROR_PATH_PRIVACY_V1151' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v11.51 Node gateway error path privacy page missing"
grep -Fq 'error_page 502 504 = @streamforge_gateway_error;' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v11.51 Node gateway interception missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNELS_CATALOGUE_SQL_ORDER_V1111' "$SOURCE_DIR/app/main.py" || die "v11.22 Main Channels catalogue SQL order fix missing"
grep -Fq 'catalogue_order = channel_catalogue_sql_order()' "$SOURCE_DIR/app/main.py" || die "v11.22 Main Channels live catalogue does not use catalogue order"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_AUTOPLAY_MUTED_FALLBACK_V1111' "$SOURCE_DIR/app/templates/player.html" || die "v11.22 Main WebPlayer autoplay fallback missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_AUTOPLAY_MUTED_FALLBACK_V1111' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node WebPlayer autoplay fallback missing"
# STREAMFORGE_V1112_MAIN_WEBPLAYER_FAST_PATH_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_BATCH_SETTINGS_V1112' "$SOURCE_DIR/app/main.py" || die "v11.22 Main WebPlayer batched settings lookup missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_PREFETCH_CATALOG_V1112' "$SOURCE_DIR/app/main.py" || die "v11.22 Main WebPlayer prefetched catalogue missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_DIRECT_MEMBERSHIP_LOOKUP_V1112' "$SOURCE_DIR/app/main.py" || die "v11.22 Main WebPlayer single-channel direct membership lookup missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_ASYNC_LOCAL_READY_V1112' "$SOURCE_DIR/app/main.py" || die "v11.22 Main WebPlayer async Local HLS readiness missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_WATCH_SETTINGS_ONCE_V1112' "$SOURCE_DIR/app/main.py" || die "v11.22 Main WebPlayer watch settings dedupe missing"
# STREAMFORGE_V1167_SUPERSEDED_HLSJS_PRELOAD_GUARD_FIX:
# v11.63 replaced the old CDN preload marker with the server-local HLS.js preload.
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_SELF_HOSTED_HLSJS_V1163' "$SOURCE_DIR/app/templates/player.html" || die "v11.63 Main WebPlayer self-hosted HLS.js preload missing"
# STREAMFORGE_V1113_DASHBOARD_PLAYER_LATENCY_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_METRICS_TAIL_READ_V1113' "$SOURCE_DIR/app/metrics_history.py" || die "v11.22 metrics tail reader missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_LATEST_METRIC_TAIL_V1113' "$SOURCE_DIR/app/main.py" || die "v11.22 Dashboard latest-metric tail path missing"
grep -Fq 'STREAMFORGE_MAIN_METRICS_WINDOW_TAIL_V1113' "$SOURCE_DIR/app/main.py" || die "v11.22 metrics history window-tail path missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_FIRST_PAINT_POLL_DELAY_V1113' "$SOURCE_DIR/app/static/app.js" || die "v11.22 Dashboard first-paint poll delay missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_HISTORY_AFTER_PAINT_V1113' "$SOURCE_DIR/app/static/dashboard.js" || die "v11.22 Dashboard history after-paint load missing"
grep -Fq 'STREAMFORGE_MAIN_CACHED_HLS_READY_STATE_V1113' "$SOURCE_DIR/app/node_manager.py" || die "v11.22 cached HLS readiness state missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_CACHED_READY_POOL_V1113' "$SOURCE_DIR/app/load_balancer.py" || die "v11.22 WebPlayer cached-ready pool missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_FAST_NODE_ROUTE_V1113' "$SOURCE_DIR/app/main.py" || die "v11.22 WebPlayer fast node route missing"
grep -Fq 'prefer_cached_ready=bool(wp)' "$SOURCE_DIR/app/main.py" || die "v11.22 WebPlayer master fast-routing flag missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_FAST_CHANNEL_SWITCH_BUFFER_V1113' "$SOURCE_DIR/app/templates/player.html" || die "v11.22 WebPlayer fast channel-switch buffer missing"
grep -Fq 'master.m3u8?wp=1' "$SOURCE_DIR/app/templates/player.html" || die "v11.22 WebPlayer cards do not request fast master routing"
# STREAMFORGE_V1114_MAIN_PANEL_HOTPATH_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_ONLINE_ONLY_FIRST_PAINT_V1114' "$SOURCE_DIR/app/main.py" || die "v11.22 Dashboard online-only first paint missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_NODE_SQL_COUNTS_V1114' "$SOURCE_DIR/app/main.py" || die "v11.22 Dashboard SQL Node counts missing"
grep -Fq 'STREAMFORGE_MAIN_STATUS_NO_HISTORICAL_LOG_SCAN_V1114' "$SOURCE_DIR/app/main.py" || die "v11.22 status hot-path log-scan removal missing"
grep -Fq 'STREAMFORGE_MAIN_VIEWER_COUNTS_NO_NODE_RELATIONSHIPS_V1114' "$SOURCE_DIR/app/main.py" || die "v11.22 viewer-count relationship removal missing"
grep -Fq 'STREAMFORGE_MAIN_METRICS_CACHE_ONLY_VIEWERS_V1114' "$SOURCE_DIR/app/main.py" || die "v11.22 metrics cache-only viewer path missing"
grep -Fq 'STREAMFORGE_MAIN_METRICS_LOCAL_CACHE_ONLY_CHANNELS_V1114' "$SOURCE_DIR/app/main.py" || die "v11.22 metrics local/cache-only channel path missing"
grep -Fq 'STREAMFORGE_MAIN_USERS_ASSOCIATION_COUNT_ONLY_V1114' "$SOURCE_DIR/app/main.py" || die "v11.22 Users association-count path missing"
grep -Fq 'STREAMFORGE_MAIN_TEMPLATE_PERMISSION_CACHE_V1114' "$SOURCE_DIR/app/main.py" || die "v11.22 template permission cache missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_METRICS_RELAXED_POLL_V1114' "$SOURCE_DIR/app/static/app.js" || die "v11.22 relaxed Dashboard metrics poll missing"
grep -Fq 'dashboard_http_outputs.get(channel.id)' "$SOURCE_DIR/app/templates/dashboard.html" || die "v11.22 Dashboard HLS action missing"
! sed -n '/def status_json(/,/return {"channels": payload/p' "$SOURCE_DIR/app/main.py" | grep -Fq '_status_log_counts(db)' || die "v11.22 status path still scans historical logs"
! sed -n '/def _metrics_channel_counts()/,/metrics_history = MetricsHistory/p' "$SOURCE_DIR/app/main.py" | grep -Fq 'allow_remote_fetch=True' || die "v11.22 metrics sampler still probes Remote Nodes"
# STREAMFORGE_V1115_MAIN_DEDICATED_SUPERVISOR_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_DEDICATED_CHANNEL_SUPERVISOR_V1115' "$SOURCE_DIR/app/ffmpeg.py" || die "v11.22 Main dedicated channel supervisor proxy missing"
grep -Fq 'class MainChannelSupervisorProxy:' "$SOURCE_DIR/app/ffmpeg.py" || die "v11.22 Main supervisor RPC proxy missing"
grep -Fq 'STREAMFORGE_MAIN_DEDICATED_CHANNEL_SUPERVISOR_ENTRYPOINT_V1115' "$SOURCE_DIR/app/main_channel_supervisor.py" || die "v11.22 Main supervisor entrypoint missing"
grep -Fq 'STREAMFORGE_MAIN_DEDICATED_CHANNEL_SUPERVISOR_CONTROL_ISOLATION_V1115' "$SOURCE_DIR/app/main.py" || die "v11.22 Main control-worker channel isolation missing"
grep -Fq 'STREAMFORGE_MAIN_SUPERVISOR_SURVIVES_PANEL_RESTART_V1115' "$SOURCE_DIR/app/main.py" || die "v11.22 Main supervisor panel-restart isolation missing"
grep -Fq 'ExecStart=/usr/bin/python3 -m app.main_channel_supervisor' "$SOURCE_DIR/deploy/streamforge-channel-supervisor.service" || die "v11.22 Main supervisor systemd unit missing"
grep -Fq 'streamforge-channel-supervisor.service' "$SOURCE_DIR/deploy/streamforge.service" || die "v11.22 Main service does not order after channel supervisor"
grep -Fq 'STREAMFORGE_MAIN_PANEL_RESPONSE_BUFFERING_V1115' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v11.22 Main generated Nginx response buffering missing"
grep -Fq 'STREAMFORGE_MAIN_PANEL_GZIP_V1115' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v11.22 Main generated Nginx gzip missing"
grep -Fq 'response_buffering=(int(backend_port) == 8800)' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v11.22 Main-only Nginx buffering selector missing"
grep -Fq 'STREAMFORGE_MAIN_PANEL_RESPONSE_BUFFERING_V1115' "$SOURCE_DIR/deploy/nginx.conf" || die "v11.22 packaged Main Nginx response buffering missing"
grep -Fq 'STREAMFORGE_MAIN_PANEL_GZIP_V1115' "$SOURCE_DIR/deploy/nginx.conf" || die "v11.22 packaged Main Nginx gzip missing"
# STREAMFORGE_V1116_MAIN_FRONTEND_CRITICAL_PATH_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_PANEL_STATIC_SHELL_ASSETS_V1116' "$SOURCE_DIR/app/templates/base.html" || die "v11.22 cacheable Main shell assets missing"
grep -Fq 'STREAMFORGE_MAIN_PANEL_STATIC_SHELL_ASSETS_V1116' "$SOURCE_DIR/app/static/panel_nav.js" || die "v11.22 cacheable Main native-navigation runtime missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_STATIC_JS_V1116' "$SOURCE_DIR/app/static/dashboard.js" || die "v11.22 cacheable Dashboard runtime missing"
grep -Fq 'STREAMFORGE_MAIN_STATUS_ROW_CACHE_V1116' "$SOURCE_DIR/app/static/app.js" || die "v11.22 cached channel status DOM map missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_STATUS_FIRST_FRAME_V1116' "$SOURCE_DIR/app/static/app.js" || die "v11.22 first-frame Dashboard status refresh missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_METRICS_AFTER_5S_V1116' "$SOURCE_DIR/app/static/app.js" || die "v11.22 post-first-paint metrics cadence missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_HISTORY_IDLE_V1116' "$SOURCE_DIR/app/static/dashboard.js" || die "v11.22 idle Dashboard history loader missing"
grep -Fq 'STREAMFORGE_MAIN_BRAND_ASYNC_DECODE_V1116' "$SOURCE_DIR/app/templates/base.html" || die "v11.22 async branding decode marker missing"
grep -Fq 'STREAMFORGE_MAIN_BRANDING_HASH_CACHE_V1116' "$SOURCE_DIR/app/main.py" || die "v11.22 content-hashed branding cache fix missing"
grep -Fq '"Cache-Control": "public, max-age=31536000, immutable"' "$SOURCE_DIR/app/main.py" || die "v11.22 immutable branding cache header missing"
! grep -Fq 'setTimeout(refresh, 800)' "$SOURCE_DIR/app/static/app.js" || die "v11.22 still carries the obsolete 800ms Dashboard status hold"
! grep -Fq 'setTimeout(() => load(), 900)' "$SOURCE_DIR/app/static/dashboard.js" || die "v11.22 still carries the obsolete 900ms Dashboard graph hold"
# STREAMFORGE_V1117_EXPLICIT_NVENC_PROFILE_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_NVENC_DEDICATED_FFMPEG_V1117' "$SOURCE_DIR/app/config.py" || die "v11.22 Main dedicated NVENC FFmpeg setting missing"
grep -Fq 'STREAMFORGE_MAIN_NVENC_DEDICATED_FFMPEG_V1117' "$SOURCE_DIR/app/ffmpeg.py" || die "v11.22 Main dedicated NVENC binary selection missing"
grep -Fq 'STREAMFORGE_MAIN_EXPLICIT_NVENC_PROFILE_V1117' "$SOURCE_DIR/app/main.py" || die "v11.22 Main explicit NVENC profile handling missing"
grep -Fq 'NVIDIA H.264 NVENC — dedicated GPU' "$SOURCE_DIR/app/templates/channel_form.html" || die "v11.22 Main NVIDIA H.264 profile option missing"
grep -Fq 'NVIDIA H.264 NVENC' "$SOURCE_DIR/app/templates/channels.html" || die "v11.22 Main bulk NVIDIA H.264 profile option missing"
grep -Fq 'STREAMFORGE_NODE_NVENC_DEDICATED_FFMPEG_V1117' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node dedicated NVENC binary selection missing"
grep -Fq 'NVENC_FFMPEG = os.getenv("STREAMFORGE_NODE_NVENC_FFMPEG"' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node NVENC FFmpeg setting missing"
grep -Fq 'nvenc_preset = "p4" if requested == "h264_nvenc" else "p1"' "$SOURCE_DIR/app/ffmpeg.py" || die "v11.22 Main explicit NVENC p4 profile missing"
grep -Fq 'nvenc_preset = "p4" if requested == "h264_nvenc" else "p1"' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node explicit NVENC p4 profile missing"
! grep -R -n -E 'NVENC_MAX|nvenc.*limit|limit.*nvenc|GPU capacity reached' "$SOURCE_DIR/app" "$SOURCE_DIR/node_agent" >/dev/null 2>&1 || die "v11.22 contains an unwanted NVENC channel limit"
# STREAMFORGE_V1118_CHANNEL_SAVE_ASYNC_SYNC_GUARDS:
grep -Fq 'STREAMFORGE_CHANNEL_SAVE_ASYNC_SYNC_V1118' "$SOURCE_DIR/app/main.py" || die "v11.22 non-blocking Main channel save queue missing"
grep -Fq 'STREAMFORGE_CHANNEL_SAVE_ASYNC_SYNC_V1118' "$SOURCE_DIR/app/node_manager.py" || die "v11.22 selective channel-logo sync control missing"
grep -Fq 'queue_channel_save_sync(' "$SOURCE_DIR/app/main.py" || die "v11.22 Main channel save does not queue remote propagation"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_SAVE_ASYNC_RESTART_V1118' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node-local non-blocking save/restart path missing"
# STREAMFORGE_V1119_BULK_PROFILE_ASYNC_GUARDS:
grep -Fq 'STREAMFORGE_BULK_PROFILE_ASYNC_APPLY_V1119' "$SOURCE_DIR/app/main.py" || die "v11.22 non-blocking Bulk Encoding Profile apply missing"
grep -Fq 'queue_bulk_profile_sync(background_targets)' "$SOURCE_DIR/app/main.py" || die "v11.22 Bulk Encoding Profile POST does not queue background apply"
# STREAMFORGE_V1120_BULK_PROFILE_GUARD_COMPAT:
grep -Fq 'STREAMFORGE_BULK_PROFILE_ASYNC_TARGETED_RESTART_COMPAT_V1120' "$SOURCE_DIR/app/main.py" || die "v11.22 async targeted bulk restart compatibility marker missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_LIVE_CATALOGUE_V1122' "$SOURCE_DIR/app/main.py" || die "v11.22 Main Channels live catalogue path missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_LIVE_SEARCH_NO_RELOAD_V1122' "$SOURCE_DIR/app/static/app.js" || die "v11.22 Main Channels no-reload search marker missing"
# STREAMFORGE_V1123_ONLINE_USERS_NETWORK_GRAPH_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_ONLINE_USERS_BACKGROUND_CACHE_V1123' "$SOURCE_DIR/app/main.py" || die "v11.23 Online Users background cache missing"
grep -Fq 'STREAMFORGE_MAIN_ONLINE_USERS_NONBLOCKING_PAGE_V1123' "$SOURCE_DIR/app/main.py" || die "v11.23 Online Users page still blocks on live Remote fetch"
grep -Fq 'STREAMFORGE_MAIN_ONLINE_USERS_NONBLOCKING_ROUTE_V1123' "$SOURCE_DIR/app/main.py" || die "v11.23 Online Users live-data route still blocks on live Remote fetch"
grep -Fq 'STREAMFORGE_MAIN_ONLINE_USERS_NO_CACHE_COMMIT_V1123' "$SOURCE_DIR/app/main.py" || die "v11.23 Online Users cache-only ORM reload guard missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_SESSION_BACKGROUND_TIMEOUT_V1123' "$SOURCE_DIR/app/node_manager.py" || die "v11.23 bounded background viewer timeout missing"
grep -Fq 'STREAMFORGE_MAIN_NETWORK_COUNTER_CHURN_GUARD_V1123' "$SOURCE_DIR/app/system_metrics.py" || die "v11.23 network counter-churn guard missing"
grep -Fq 'STREAMFORGE_MAIN_NETWORK_HISTORY_INTERVAL_AVERAGE_V1123' "$SOURCE_DIR/app/metrics_history.py" || die "v11.23 network history interval average missing"
grep -Fq 'STREAMFORGE_MAIN_NETWORK_HISTORY_STARTUP_CARRY_V1123' "$SOURCE_DIR/app/metrics_history.py" || die "v11.23 network startup carry guard missing"
[[ "$(grep -Fc 'stats = collect_viewer_stats(db, allow_remote_fetch=False, include_geo=False)' "$SOURCE_DIR/app/main.py")" -eq 2 ]] || die "v11.23 Online Users routes are not both cache-only"
# STREAMFORGE_V1124_BULK_NODE_ASSIGNMENT_GUARDS:
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_ASYNC_V1124' "$SOURCE_DIR/app/main.py" || die "v11.50 Bulk Node background worker missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_FAST_RETURN_V1124' "$SOURCE_DIR/app/main.py" || die "v11.50 Bulk Node fast-return path missing"
grep -Fq 'STREAMFORGE_BULK_SELECTED_EAGER_V1124' "$SOURCE_DIR/app/main.py" || die "v11.50 selected-channel eager-load optimization missing"
grep -Fq '_BULK_NODE_ASSIGN_SYNC_EXECUTOR = ThreadPoolExecutor(max_workers=2' "$SOURCE_DIR/app/main.py" || die "v11.50 dedicated Bulk Node sync executor missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_DURABLE_RETURN_V1137' "$SOURCE_DIR/app/main.py" || die "v11.50 durable Bulk Node Add queue missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_RESPONSE_FIRST_V1137' "$SOURCE_DIR/app/main.py" || die "v11.50 response-first Bulk Node Add path missing"
grep -Fq 'STREAMFORGE_BULK_RELAY_SELECTED_NODE_ONLY_V1137' "$SOURCE_DIR/app/main.py" || die "v11.50 selected-node-only relay path missing"
grep -Fq 'STREAMFORGE_BULK_RELAY_RESPONSE_FIRST_V1137' "$SOURCE_DIR/app/main.py" || die "v11.50 response-first relay queue missing"
grep -Fq 'queue_bulk_node_assignment_sync(removal_targets)' "$SOURCE_DIR/app/main.py" || die "v11.50 Bulk Node removal cleanup queue missing"
grep -Fq 'node_controller.sync_channel_batch_to_node(' "$SOURCE_DIR/app/main.py" || die "v11.50 Bulk Node true-batch added-node sync missing"
# STREAMFORGE_V1125_BULK_NODE_ASSIGNMENT_GUARDS:
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_DIRECT_SQL_V1125' "$SOURCE_DIR/app/main.py" || die "v11.50 direct-SQL Bulk Node assignment missing"
grep -Fq 'STREAMFORGE_BULK_NODE_NO_ORM_HYDRATION_V1125' "$SOURCE_DIR/app/main.py" || die "v11.50 Bulk Node no-ORM hydration guard missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_AJAX_V1125' "$SOURCE_DIR/app/main.py" || die "v11.50 Bulk Node JSON response path missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_AJAX_UI_V1125' "$SOURCE_DIR/app/static/app.js" || die "v11.50 Bulk Node AJAX UI missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_NO_NATIVE_SPINNER_V1125' "$SOURCE_DIR/app/static/panel_nav.js" || die "v11.50 Bulk Node native spinner bypass missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_AJAX_NOTICE_V1125' "$SOURCE_DIR/app/templates/channels.html" || die "v11.50 Bulk Node in-page result notice missing"
# STREAMFORGE_V1126_LIVE_SESSIONS_GEO_TLS_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_VIEWER_GEO_BACKGROUND_V1126' "$SOURCE_DIR/app/main.py" || die "v11.50 Main non-blocking Live Sessions GeoIP cache missing"
grep -Fq 'STREAMFORGE_MAIN_VIEWER_HEARTBEAT_DETAIL_PREFETCH_V1126' "$SOURCE_DIR/app/main.py" || die "v11.50 heartbeat-driven viewer detail prefetch missing"
grep -Fq 'request_remote_viewer_node_refresh(int(node.id), active_hint=active_connections_hint)' "$SOURCE_DIR/app/main.py" || die "v11.50 Node heartbeat viewer-detail scheduler missing"
grep -Fq 'central_viewer_session_details(db, node_id=node.id, include_geo=False)' "$SOURCE_DIR/app/main.py" || die "v11.50 heartbeat still performs synchronous GeoIP"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_FAST_DETAIL_RETRY_V1126' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "v11.50 Live Sessions fast detail retry missing"
grep -Fq 'details_pending' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "v11.50 Live Sessions detail warmup state missing"
grep -Fq 'STREAMFORGE_NODE_VIEWER_GEO_BACKGROUND_V1126' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node non-blocking viewer GeoIP cache missing"
grep -Fq 'STREAMFORGE_NODE_DNS01_TEST_AUTO_ISSUE_V1126' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node DNS-01 test auto-issuance missing"
grep -Fq 'STREAMFORGE_MAIN_DNS01_TEST_AUTO_ISSUE_V1126' "$SOURCE_DIR/app/main.py" || die "v11.50 Main DNS-01 test auto-issuance missing"
grep -Fq 'OnUnitActiveSec=2min' "$SOURCE_DIR/node_agent/deploy/streamforge-node-tls.timer" || die "v11.50 Node pending TLS retry interval missing"
grep -Fq 'OnUnitActiveSec=2min' "$SOURCE_DIR/deploy/streamforge-main-tls.timer" || die "v11.50 Main pending TLS retry interval missing"
# STREAMFORGE_V1127_TARGETED_NODE_SYNC_GUARDS:
grep -Fq 'STREAMFORGE_NODE_TARGETED_CHANNEL_PENDING_V1127' "$SOURCE_DIR/app/sync_queue.py" || die "v11.50 targeted channel pending queue missing"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_NO_FULL_SWEEP_V1127' "$SOURCE_DIR/app/main.py" || die "v11.50 heartbeat full-sweep guard missing"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_TARGETED_RETRY_V1127' "$SOURCE_DIR/app/main.py" || die "v11.50 targeted heartbeat retry worker missing"
grep -Fq 'node_ids={int(item) for item in remote_node_ids}' "$SOURCE_DIR/app/main.py" || die "v11.50 channel-save Node scope missing"
grep -Fq 'STREAMFORGE_BULK_NODE_TRUE_BATCH_V1127' "$SOURCE_DIR/app/main.py" || die "v11.50 Bulk Node true-batch worker missing"
grep -Fq 'STREAMFORGE_NODE_TRUE_BATCH_CONFIG_SYNC_V1127' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 Main bulk Node config client missing"
grep -Fq 'STREAMFORGE_NODE_BULK_CONFIG_API_V1127' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node bulk config API missing"
grep -Fq 'STREAMFORGE_NODE_BULK_CONFIG_APPLY_V1127' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node supervisor bulk apply missing"
grep -Fq 'STREAMFORGE_NODE_PERIODIC_MAIN_HEARTBEAT_V1139' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 periodic Main heartbeat interval missing"
grep -Fq 'STREAMFORGE_NODE_PERIODIC_MAIN_HEARTBEAT_LOOP_V1139' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 periodic Main heartbeat loop missing"
grep -Fq 'STREAMFORGE_NODE_PERIODIC_MAIN_HEARTBEAT_START_V1139' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 periodic Main heartbeat startup missing"
grep -Fq 'name="streamforge-node-main-heartbeat"' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 heartbeat thread name/start missing"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_LOG_CLEAR_PUBLIC_PREFIX_V1140' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node channel log clear public-prefix fix missing"
grep -Fq "f\"{(_CURRENT_PANEL_PREFIX.get() or '')}/channels/\"" "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node channel log clear prefix-aware implementation missing"
! sed -n '/@app.get("\/panel\/channels\/{key}\/errors.json")/,/@app.post("\/panel\/channels\/{key}\/errors\/clear")/p' "$SOURCE_DIR/node_agent/app.py" | grep -Fq '"clear_endpoint": f"/panel/channels/' || die "v11.50 Node channel log JSON still returns private /panel clear URL"
# STREAMFORGE_V1128_TARGETED_CATALOGUE_PENDING_GUARDS:
grep -Fq 'STREAMFORGE_NODE_CATALOGUE_TARGETED_PENDING_V1128' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 targeted catalogue pending fix missing"
grep -Fq 'STREAMFORGE_NODE_BATCH_FALLBACK_REMAINDER_QUEUE_V1128' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 fallback remainder queue fix missing"
grep -Fq 'STREAMFORGE_NODE_STALE_CATALOGUE_PENDING_CLEANUP_V1128' "$SOURCE_DIR/app/main.py" || die "v11.50 stale catalogue pending cleanup marker missing"
grep -Fq '"channel catalogue queued after control connection failure: ",' "$SOURCE_DIR/app/main.py" || die "v11.50 legacy catalogue pending migration prefix missing"
grep -Fq 'stale_mode_pending = pending_lower.startswith("mode reconcile queued after control connection failure")' "$SOURCE_DIR/app/main.py" || die "v11.50 stale mode pending cleanup logic missing"
grep -Fq 'elif legacy_target_pending_migrated:' "$SOURCE_DIR/app/main.py" || die "v11.50 migrated generic pending clear path missing"
! grep -Fq 'mark_node_sync_pending(db, current, f"Channel catalogue queued after control connection failure:' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 still creates generic catalogue pending keys"
# STREAMFORGE_V1129_TARGETED_LOGO_PENDING_GUARDS:
grep -Fq 'STREAMFORGE_NODE_TARGETED_LOGO_PENDING_V1129' "$SOURCE_DIR/app/sync_queue.py" || die "v11.50 targeted logo pending queue missing"
grep -Fq 'STREAMFORGE_NODE_LOGO_TARGETED_PENDING_V1129' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 logo sync still uses generic pending state"
grep -Fq 'STREAMFORGE_NODE_LOGO_TARGETED_RETRY_V1129' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 targeted logo retry client missing"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_TARGETED_LOGO_RETRY_V1129' "$SOURCE_DIR/app/main.py" || die "v11.50 heartbeat targeted logo retry missing"
grep -Fq 'STREAMFORGE_NODE_LEGACY_GENERIC_PENDING_MIGRATION_V1129' "$SOURCE_DIR/app/main.py" || die "v11.50 legacy generic pending migration missing"
grep -Fq '.stream-summary-cell [data-resolution]{color:#eef5fb;font-weight:800}' "$SOURCE_DIR/app/static/style.css" && grep -Fq '.stream-summary-cell [data-bitrate]{color:#9fb3c7}' "$SOURCE_DIR/app/static/style.css" || die "v11.50 aligned Main metric styling implementation missing"
! grep -Fq 'mark_node_sync_pending(db, node, f"Channel logo queued:' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 still creates generic logo pending keys"
! grep -Fq 'mark_node_sync_pending(db, node, f"Channel logo queued after connection failure:' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 still creates generic logo failure pending keys"
# STREAMFORGE_V1132_HEARTBEAT_AUTHORITATIVE_LIVENESS_GUARDS:
grep -Fq 'STREAMFORGE_HEARTBEAT_AUTHORITATIVE_LIVENESS_V1132' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 heartbeat-authoritative controller liveness fix missing"
grep -Fq 'def node_liveness_recent(' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 liveness grace helper missing"
grep -Fq 'def note_control_failure(' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 control failure isolation helper missing"
grep -Fq 'def note_control_success(' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 control success recovery helper missing"
grep -Fq 'recently_seen = node_controller.node_liveness_recent(node)' "$SOURCE_DIR/app/main.py" || die "v11.50 Nodes status heartbeat grace missing"
grep -Fq 'node_controller.is_effectively_offline(node)' "$SOURCE_DIR/app/main.py" || die "v11.50 Main effective-offline guard missing"
[[ "$(grep -Fc 'node.status = "offline"' "$SOURCE_DIR/app/node_manager.py")" -eq 1 ]] || die "v11.50 node_manager still writes offline outside heartbeat-aware helper"
! grep -Fq '.status = "offline"' "$SOURCE_DIR/app/main.py" || die "v11.50 Main still overwrites Node liveness directly"
# STREAMFORGE_V1133_TARGETED_PENDING_DRAIN_GUARDS:
grep -Fq 'STREAMFORGE_TARGETED_PENDING_SEPARATE_SCHEDULER_V1133' "$SOURCE_DIR/app/main.py" || die "v11.50 separate targeted retry scheduler missing"
grep -Fq 'def _claim_node_targeted_retry(' "$SOURCE_DIR/app/main.py" || die "v11.50 targeted retry claim helper missing"
grep -Fq '_finish_node_targeted_retry(int(node_id), success)' "$SOURCE_DIR/app/main.py" || die "v11.50 targeted retry completion path missing"
grep -Fq 'pending_ids, int(node_id), bypass_transport_backoff=True' "$SOURCE_DIR/app/main.py" || die "v11.50 targeted config retry does not force a scheduled wire attempt"
grep -Fq 'pending_logo_ids, int(node_id), bypass_transport_backoff=True' "$SOURCE_DIR/app/main.py" || die "v11.50 targeted logo retry does not force a scheduled wire attempt"
grep -Fq 'STREAMFORGE_TARGETED_RETRY_BYPASS_TRANSPORT_BACKOFF_V1133' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 targeted transport-backoff bypass marker missing"
grep -Fq 'STREAMFORGE_NODE_CATALOGUE_TRUE_BATCH_RECONCILE_V1135' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 true-batch catalogue reconcile missing"
grep -Fq 'self.sync_channel_batch_to_node(' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 catalogue batch client missing"
grep -Fq 'self.sync_channel_logo_batch_to_node(' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 catalogue targeted logo batch missing"
! sed -n '/def sync_node_channels(/,/def managed_tls_status(/p' "$SOURCE_DIR/app/node_manager.py" | grep -Fq 'self.sync_channel_config(channel, current)' || die "v11.50 catalogue reconcile still uses per-channel config requests"
grep -Fq 'bypass_transport_backoff=bool(bypass_transport_backoff)' "$SOURCE_DIR/app/node_manager.py" || die "v11.50 Node request bypass propagation missing"
grep -Fq 'data-live-channel-filter-form' "$SOURCE_DIR/app/templates/channels.html" || die "v11.22 Main Channels live filter form hook missing"
grep -Fq 'data-live-channel-search' "$SOURCE_DIR/app/templates/channels.html" || die "v11.22 Main Channels live search input hook missing"
! grep -Fq 'data-channel-auto-search' "$SOURCE_DIR/app/templates/channels.html" || die "v11.22 still carries the document-reloading auto-submit hook"
grep -Fq 'STREAMFORGE_BULK_PROFILE_TARGETED_RESTART_V1027' "$SOURCE_DIR/app/main.py" || die "v11.22 historical targeted bulk restart guarantee missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_APPLY_RUNNING_V1025' "$SOURCE_DIR/app/main.py" || die "v11.22 historical running bulk apply guarantee missing"
grep -Fq 'node_ids=node_scope' "$SOURCE_DIR/app/main.py" || die "v11.22 async bulk apply lost targeted node scope"
# STREAMFORGE_PACKAGE_HYGIENE_V107: release ZIPs must not carry interpreter/editor/cache artifacts.
if find "$SOURCE_DIR" -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache' -o -name '.cache' -o -name '.git' -o -name '__MACOSX' \) -print -quit | grep -q .; then die "v11.63 package contains cache/metadata directories"; fi
if find "$SOURCE_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' -o -name '*.swp' -o -name '*.tmp' -o -name '*.bak' -o -name '*.orig' -o -name '*.rej' \) -print -quit | grep -q .; then die "v11.63 package contains generated/editor artifacts"; fi
# STREAMFORGE_V1069_GPU_DASHBOARD_GUARDS:
grep -Fq 'STREAMFORGE_GPU_BACKGROUND_METRICS_V1069' "$SOURCE_DIR/app/system_metrics.py" || die "v11.22 Main background GPU metrics sampler missing"
grep -Fq 'STREAMFORGE_MAIN_GPU_HISTORY_V1069' "$SOURCE_DIR/app/metrics_history.py" || die "v11.22 Main GPU metrics history missing"
grep -Fq 'data-metric-chart="gpu-util"' "$SOURCE_DIR/app/templates/dashboard.html" || die "v11.22 Main GPU utilization graph missing"
grep -Fq 'data-metric-chart="gpu-temp"' "$SOURCE_DIR/app/templates/dashboard.html" || die "v11.22 Main GPU temperature graph missing"
grep -Fq 'STREAMFORGE_MAIN_GPU_DASHBOARD_LIVE_V1069' "$SOURCE_DIR/app/static/app.js" || die "v11.22 Main live GPU dashboard update missing"
grep -Fq 'STREAMFORGE_NODE_GPU_BACKGROUND_METRICS_V1069' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node background GPU metrics sampler missing"
grep -Fq 'STREAMFORGE_NODE_GPU_HISTORY_V1069' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node GPU metrics history missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_INITIAL_NO_GEO_V1069' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node initial dashboard no-GeoIP fast path missing"
grep -Fq 'data-node-metric-chart="gpu-util"' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node GPU utilization graph missing"
grep -Fq 'data-node-metric-chart="gpu-temp"' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node GPU temperature graph missing"
grep -Fq 'STREAMFORGE_RESPONSIVE_METRIC_CHART_HEIGHT_V1071' "$SOURCE_DIR/app/static/style.css" || die "v11.22 Main responsive dashboard graph sizing missing"
grep -Fq 'STREAMFORGE_NODE_RESPONSIVE_METRIC_CHART_HEIGHT_V1071' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node responsive dashboard graph sizing missing"
# STREAMFORGE_V1081_CLIENT_SESSION_RESET_GUARDS:
grep -Fq 'CLIENT_SESSION_RESET_OFFLINE_MINUTES_KEY = "client_session_reset_offline_minutes"' "$SOURCE_DIR/app/main.py" || die "v11.22 Main session reset setting missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_SESSION_CLUSTER_V1081' "$SOURCE_DIR/app/main.py" || die "v11.22 Main logical session clustering missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_SESSION_DEDUPE_HEARTBEAT_V1081' "$SOURCE_DIR/app/viewer_tracking.py" || die "v11.22 Main logical session heartbeat missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_SESSION_OBSERVER_ROW_V1081' "$SOURCE_DIR/app/main.py" || die "v11.22 Main logical session observer row missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_SESSION_CLEAR_DEDUPE_V1081' "$SOURCE_DIR/app/main.py" || die "v11.22 Main Client-log clear session reset missing"
grep -Fq 'STREAMFORGE_CLIENT_SESSION_RESET_OFFLINE_UI_V1081' "$SOURCE_DIR/app/templates/system_branding.html" || die "v11.22 Main session reset UI missing"
grep -Fq 'client_session_reset_offline_minutes: int | None = None' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node session reset sync field missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_RESET_BOUNDARY_V1081' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node logical session boundary missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_CLUSTER_V1081' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node logical session clustering missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_DEDUPE_HEARTBEAT_V1081' "$SOURCE_DIR/node_agent/redis_state.py" || die "v11.22 Node logical session heartbeat missing"
# STREAMFORGE_V1082_WEBPLAYER_FLASH_ERROR_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_FLASH_ERROR_V1082' "$SOURCE_DIR/app/main.py" || die "v11.22 Main clean Web Player login-error flash missing"
grep -Fq '_web_player_flash_redirect(request, "Invalid username or password")' "$SOURCE_DIR/app/main.py" || die "v11.22 Main invalid-login clean redirect missing"
! grep -Fq '_web_player_home_url(request)}?error=Invalid+username+or+password' "$SOURCE_DIR/app/main.py" || die "v11.22 Main still leaks invalid-login error in URL"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_FLASH_ERROR_V1082' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node clean Web Player login-error flash missing"
grep -Fq '_node_web_flash_redirect(request, "Invalid username or password")' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node invalid-login clean redirect missing"
grep -Fq '_node_web_page(user, request, flash_error)' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node root Web Player does not render flash error"
! grep -Fq '_node_web_home_url(request)}?error=Invalid+username+or+password' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node still leaks invalid-login error in URL"
# STREAMFORGE_V1083_NODE_ZERO_ENCODER_RESTART_GUARDS:
grep -Fq 'STREAMFORGE_NODE_UPDATE_FFMPEG_HANDOFF_V1083' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node FFmpeg update handoff missing"
grep -Fq 'def prepare_update_ffmpeg_handoff' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node update handoff snapshot missing"
grep -Fq 'class AdoptedProcess' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node adopted FFmpeg process handle missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_ZERO_CHANNEL_RESTART_V1083' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node zero-encoder-restart update path missing"
grep -Fq 'preserved_encoders = manager.prepare_update_ffmpeg_handoff' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node API update does not preserve live encoders"
grep -Fq 'Running FFmpeg adopted across Node Agent update' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node replacement worker adoption missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_SEAMLESS_UPDATE_LEASE_V1083' "$SOURCE_DIR/app/main.py" || die "v11.22 Main seamless Node-update playback routing missing"
grep -Fq '_node_agent_supports_seamless_code_update' "$SOURCE_DIR/app/main.py" || die "v11.22 Main seamless Node-version gate missing"
# STREAMFORGE_V1084_FRESH_NODE_NGINX_BOOTSTRAP_GUARDS:
grep -Fq 'STREAMFORGE_NODE_FRESH_NGINX_HEALTH_BOOTSTRAP_V1084' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v11.8 fresh-Node authenticated Nginx bootstrap guard missing"
grep -Fq 'def http_front_health_ready(port: int, token: str)' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v11.8 fresh-Node HTTP health verifier missing"
grep -Fq 'existing StreamForge nginx HTTP/TLS frontend is healthy' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v11.8 Nginx bootstrap still trusts a generic listener"
grep -Fq 'authenticated StreamForge health route did not open on HTTP port' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v11.8 managed Nginx post-publish health verification missing"

# STREAMFORGE_V111_RELAY_STABILITY_GUARDS:
grep -Fq 'STREAMFORGE_RELAY_MISS_PUBLIC_FALLBACK_V111' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v11.8 dynamic relay miss fallback missing"
grep -Fq 'try_files $uri @streamforge_relay_fallback;' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v11.8 dynamic relay fast-path still hard-fails missing files"
grep -Fq 'location @streamforge_relay_fallback' "$SOURCE_DIR/deploy/nginx.conf" || die "v11.8 fresh Main relay fallback location missing"
grep -Fq 'X-StreamForge-Internal-Relay-Fallback 1' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v11.8 internal relay fallback header missing"
grep -Fq 'STREAMFORGE_MAIN_INTERNAL_RELAY_FALLBACK_V111' "$SOURCE_DIR/app/main.py" || die "v11.22 Main relay fallback alias bypass missing"
grep -Fq 'STREAMFORGE_MAIN_HLS_BROWSER_SEGMENT_GRACE_V1167' "$SOURCE_DIR/app/ffmpeg.py" || die "v11.67 Main HLS segment grace missing"
grep -Fq 'STREAMFORGE_NODE_HLS_BROWSER_SEGMENT_GRACE_V1167' "$SOURCE_DIR/node_agent/app.py" || die "v11.67 Node HLS segment grace missing"
grep -Fq 'local_hls_delete_threshold = max(3, (30 + local_hls_time - 1) // local_hls_time)' "$SOURCE_DIR/app/ffmpeg.py" || die "v11.67 Main time-based old-segment retention missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_STALL_HEALTH_V111' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node relay-aware stall watchdog missing"
grep -Fq 'Main relay unavailable; keeping FFmpeg reconnect alive' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node relay outage reconnect preservation missing"
! grep -Eq '^[[:space:]]*systemctl[[:space:]]+restart[[:space:]]+nginx([[:space:]]|$)' "$SOURCE_UPDATER" || die "v11.8 updater still performs disruptive Nginx restart"
# STREAMFORGE_V114_CHANNELS_RELAY_PRESSURE_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_ASYNC_HLS_READY_V114' "$SOURCE_DIR/app/node_manager.py" || die "v11.8 async Main HLS readiness cache missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_STATUS_CACHE_ONLY_V114' "$SOURCE_DIR/app/main.py" || die "v11.8 cache-only Main channel status missing"
grep -Fq 'STREAMFORGE_MAIN_HOT_VIEWER_COUNTS_V114' "$SOURCE_DIR/app/main.py" || die "v11.8 hot viewer-count path missing"
grep -Fq 'STREAMFORGE_NODE_LOCAL_RELAY_RECOVERY_V114' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 relay reconnect policy missing"
grep -Fq 'reconnect_http_errors="404,5xx" if relay_mode else "4xx,5xx"' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 relay 404 reconnect missing"
# STREAMFORGE_V1141_V1143_LOCAL_RELAY_UPTIME_GUARDS:
grep -Fq 'STREAMFORGE_NODE_LOCAL_RELAY_STICKY_RECONNECT_V1141' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 sticky Local-relay reconnect policy missing"
grep -Fq 'RELAY_RECONNECT_DELAY_MAX_SECONDS' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Local-relay reconnect window missing"
grep -Fq 'STREAMFORGE_NODE_LOCAL_RELAY_HLS_EOF_FIX_V1145' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Local-relay HLS EOF fix missing"
grep -Fq 'STREAMFORGE_NODE_HTTP_EOF_NON_HLS_ONLY_V1145' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 HLS-aware EOF reconnect guard missing"
grep -Fq 'STREAMFORGE_MAIN_TLS_RECONCILE_NO_LISTENER_FLAP_V1146' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v11.50 Main TLS listener-preservation fix missing"
grep -Fq 'STREAMFORGE_MAIN_NGINX_NOOP_RELOAD_SKIP_V1146' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v11.50 Main Nginx no-op reload suppression missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_RESPAWN_HEALTH_GATE_V1147' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Local-relay deferred respawn health gate missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_WATCHDOG_HEALTH_GATE_V1147' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Local-relay watchdog health gate missing"
grep -Fq 'STREAMFORGE_NODE_UNKNOWN_ICON_QUIET_404_V1147' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node /icon quiet Python fallback missing"
grep -Fq 'STREAMFORGE_NODE_UNKNOWN_ICON_NGINX_QUIET_404_V1147' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v11.50 Node /icon Nginx quiet route missing"
# STREAMFORGE_V1150_NODE_REAL_CPU_SOURCE_GUARDS:
grep -Fq 'STREAMFORGE_NODE_REAL_CPU_PROC_STAT_V1148' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node real /proc/stat CPU sampler missing"
grep -Fq 'def _cpu_counters() -> tuple[int, int]:' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node CPU counter reader missing"
grep -Fq 'busy_delta / total_delta * 100.0' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node CPU busy-delta calculation missing"
grep -Fq '"cpu_metric_version": 2' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node real-CPU history version marker missing"
grep -Fq 'STREAMFORGE_NODE_REAL_CPU_HISTORY_V1148' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node legacy CPU-history filter missing"
# STREAMFORGE_V1150_NODE_ACCESS_ALIAS_PERSISTENCE_SOURCE_GUARDS:
grep -Fq 'STREAMFORGE_NODE_ACCESS_CROSS_PROCESS_WRITE_LOCK_V1149' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node cross-process access writer lock missing"
grep -Fq 'STREAMFORGE_NODE_MODE_SYNC_NO_ACCESS_ALIAS_CLOBBER_V1149' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node mode-sync alias clobber fix missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_SINGLE_OWNER_WRITES_V1150' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node mode-sync field-patch enforcement missing"
grep -Fq 'STREAMFORGE_NODE_NAME_PATCH_NO_ACCESS_ALIAS_CLOBBER_V1150' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Node-name field-patch enforcement missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_AUTHORITATIVE_FULL_WRITE_V1150' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 authoritative access full-write marker missing"
[[ "$(grep -Fc 'self.save_access()' "$SOURCE_DIR/node_agent/app.py")" -eq 2 ]] || die "v11.50 unrelated full access writers remain in Node source"
grep -Fq 'self.patch_access_file({"total_max_connections": int(self.total_max_connections)})' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 supervisor access merge path missing"
! grep -Fq 'reconnect_at_eof=relay_mode' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 regressed v11.45 HLS EOF behavior"
grep -Fq 'if reconnect_at_eof and not hls_http_input:' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 HLS manifests can still enter protocol EOF reconnect"
! grep -Fq 'reconnect_at_eof=relay_mode' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Local-relay still reconnects on normal HLS playlist EOF"
grep -Fq 'reconnect_at_eof=False' "$SOURCE_DIR/node_agent/app.py" || die "v11.50 Local-relay EOF reconnect disable missing"
grep -Fq 'STREAMFORGE_MAIN_RELAY_RUNTIME_STATE_HTTP_V1141' "$SOURCE_DIR/app/main.py" || die "v11.50 Main relay runtime-state response policy missing"
grep -Fq 'STREAMFORGE_MAIN_RELAY_LIVE_PID_GUARD_V1143' "$SOURCE_DIR/app/main.py" || die "v11.50 Main relay live-PID guard missing"
grep -Fq 'STREAMFORGE_MAIN_RELAY_RUNNING_STATE_RETRY_V1144' "$SOURCE_DIR/app/main.py" || die "v11.50 Main relay healthy-state retry guard missing"
grep -Fq 'def _local_relay_recent_media(channel: Channel) -> bool:' "$SOURCE_DIR/app/main.py" || die "v11.50 Main relay recent-media guard missing"
grep -Fq 'process_alive = local_channel_process_alive(channel)' "$SOURCE_DIR/app/main.py" || die "v11.50 Main relay live-PID evidence missing"
grep -Fq 'if state == "restarting" and not process_alive and not recent_media:' "$SOURCE_DIR/app/main.py" || die "v11.50 Main relay confirmed-restart gate missing"
grep -Fq 'headers["Retry-After"] = "1"' "$SOURCE_DIR/app/main.py" || die "v11.50 running-Main relay retry hint missing"
grep -Fq 'STREAMFORGE_MAIN_RELAY_SEGMENT_ROTATION_404_V1143' "$SOURCE_DIR/app/main.py" || die "v11.50 relay segment rotation 404 policy missing"
! sed -n '/def local_relay_segment(/,/return Response(/p' "$SOURCE_DIR/app/main.py" | grep -Fq '_local_relay_missing_exception' || die "v11.50 relay segment miss still escalates to channel-level state"
grep -Fq 'STREAMFORGE_NODE_LOCAL_RELAY_CATCHUP_V114' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 relay catch-up mode missing"
grep -Fq 'self.is_hls_http_input(input_url) and not relay_mode' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 relay still uses -re pacing"
grep -Fq 'STREAMFORGE_NODE_RELAY_FRESHNESS_V114' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 relay freshness health check missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_FAST_RECOVERY_V114' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 relay fast retry ladder missing"

# STREAMFORGE_V115_DEDICATED_NODE_CHANNEL_SUPERVISOR_GUARDS:
grep -Fq 'STREAMFORGE_NODE_DEDICATED_CHANNEL_SUPERVISOR_V115' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 dedicated Node channel supervisor mode missing"
grep -Fq 'STREAMFORGE_NODE_DEDICATED_CHANNEL_SUPERVISOR_ENTRYPOINT_V115' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node channel supervisor entrypoint missing"
grep -Fq 'def ensure_channel_supervisor(self)' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 control-to-supervisor launcher missing"
grep -Fq 'def _channel_supervisor_rpc(self, payload:' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 Unix-socket supervisor RPC client missing"
grep -Fq 'class _ChannelSupervisorRPCServer:' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 Unix-socket supervisor RPC server missing"
grep -Fq 'STREAMFORGE_NODE_SUPERVISOR_DURABLE_FFMPEG_IO_V115' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 durable supervisor FFmpeg I/O missing"
grep -Fq 'STREAMFORGE_NODE_SUPERVISOR_CRASH_ADOPTION_V115' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 supervisor crash PID adoption missing"
grep -Fq 'STREAMFORGE_NODE_LOG_SINGLE_WRITER_SUPERVISOR_V115' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 supervisor append-only log protection missing"
grep -Fq 'manager.ensure_channel_supervisor()' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 control startup does not ensure channel supervisor"
grep -Fq 'NODE_CHANNEL_OWNER = bool(NODE_MODE == "supervisor"' "$SOURCE_DIR/node_agent/app.py" || die "v11.8 dedicated FFmpeg ownership gate missing"
# Existing services are deliberately unchanged: the supervisor is a detached
# app.py process so API upgrades from pre-v11.9 Nodes do not require root unit changes.
grep -Fq 'Environment=STREAMFORGE_NODE_MODE=control' "$SOURCE_DIR/node_agent/deploy/streamforge-node.service" || die "v11.8 unexpectedly changed Node control service mode"
grep -Fq 'Environment=STREAMFORGE_NODE_MODE=public' "$SOURCE_DIR/node_agent/deploy/streamforge-node-public.service" || die "v11.8 unexpectedly changed Node Public service mode"

# STREAMFORGE_V117_SERVER_PAGINATION_WATCH_FLOW_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_CHANNELS_SERVER_PAGINATION_V117' "$SOURCE_DIR/app/main.py" || die "v11.22 historical Channels pagination compatibility marker missing"
grep -Fq 'STREAMFORGE_MAIN_USERS_PLAYLISTS_SERVER_PAGINATION_V117' "$SOURCE_DIR/app/main.py" || die "v11.8 Users & Playlists server-side pagination missing"
grep -Fq 'STREAMFORGE_MAIN_USERS_PLAYLISTS_RUNTIME_FIX_V1110' "$SOURCE_DIR/app/main.py" || die "v11.22 Users & Playlists runtime 500 fix missing"
grep -Fq 'select(func.count(StreamUser.id)).where(*user_filters)' "$SOURCE_DIR/app/main.py" || die "v11.22 Users direct count query missing"
grep -Fq 'href="{{ prefix }}/web-player/watch/{{ channel.slug }}"' "$SOURCE_DIR/app/templates/web_player.html" || die "v11.22 Main WebPlayer separate watch-page flow missing"
! grep -Fq 'data-inline-player' "$SOURCE_DIR/app/templates/web_player.html" || die "v11.22 Main WebPlayer still contains inline catalogue player"
! grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_FIREFOX_GESTURE_PLAYER_V116' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node WebPlayer still contains inline catalogue player"
grep -Fq 'STREAMFORGE_MAIN_VIEWER_COUNT_MICROCACHE_V116' "$SOURCE_DIR/app/main.py" || die "v11.22 Main viewer-count microcache missing"
grep -Fq 'playlist_channel_counts.get' "$SOURCE_DIR/app/templates/users.html" || die "v11.8 Users & Playlists precomputed channel counts missing"
grep -Fq 'return /^(?:back(?:\s+to\b|\b)|go back\b)/i.test(text);' "$SOURCE_DIR/app/static/panel_nav.js" || die "v11.8 Backups navigation word-boundary fix missing"

# STREAMFORGE_V112_WEBPLAYER_DEFAULT_AUDIO_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_DEFAULT_AUDIO_30_V112' "$SOURCE_DIR/app/templates/player.html" || die "v11.22 Main WebPlayer 30% default audio missing"
grep -Fq 'video.volume = 0.30;' "$SOURCE_DIR/app/templates/player.html" || die "v11.22 Main WebPlayer default volume is not 30%"
! grep -Fq '<video id="stream-video" controls autoplay muted playsinline' "$SOURCE_DIR/app/templates/player.html" || die "v11.22 Main WebPlayer still starts muted"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_DEFAULT_AUDIO_30_V112' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node WebPlayer 30% default audio missing"
grep -Fq 'video.defaultMuted=false;video.muted=false;video.volume=0.30;' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node WebPlayer default volume is not 30%"
! grep -Fq '<div class="watch-frame"><video id="video" controls autoplay muted playsinline{poster_attr}></video></div>' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node public WebPlayer still starts muted"

# STREAMFORGE_V1070_NODE_PANEL_HLS_PREVIEW_GUARDS:
grep -Fq 'STREAMFORGE_NODE_PANEL_HLS_PREVIEW_V1070' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node HLS preview player missing"
grep -Fq '@app.get("/panel/channels/{key}/play", response_class=HTMLResponse)' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node HLS preview route missing"
grep -Fq 'X-StreamForge-Panel-Media-Grant' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node HLS preview media grant missing"
grep -Fq 'href="/panel/channels/{escaped_key}/play"' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node Channels HLS action still opens a raw manifest"
grep -Fq 'STREAMFORGE_NODE_CHANNELS_NO_GEO_V1070' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node Channels no-GeoIP fast path missing"
# STREAMFORGE_V1073_NODE_CLIENT_LOG_LIVE_SID_GUARDS:
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_LIVE_SESSION_ID_V1073' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node Live-session ID logging missing"
if grep -Fq 'generation_id = "sess-"' "$SOURCE_DIR/node_agent/app.py"; then die "v11.22 Node still generates a second log-only session ID"; fi
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_LIVE_SID_EPOCH_BIND_V1073' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node SID+epoch historical binding missing"
grep -Fq "header_cells += '<th>Session ID</th>'" "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node Client-log Session ID column missing"
grep -Fq "_node_client_log_session_id(x) or \"—\"" "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node Client-log does not display Live-session SID"
grep -Fq "def independent_logs_clear(request: Request, log_type: str = 'activity')" "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node tab-scoped Panel log clear missing"
grep -Fq '_node_client_login_log_once("xtream-login"' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node Xtream login log dedupe missing"
# STREAMFORGE_V1074_NODE_CUMULATIVE_SID_GUARDS:
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_ONE_ROW_PER_SID_V1074' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node one-row-per-Live-SID logging missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_CUMULATIVE_SID_AGE_V1074' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node cumulative Live-SID Session age missing"
# STREAMFORGE_V1075_NODE_CLIENT_LOG_SINGLE_VISIBLE_SID_GUARD:
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_FOLD_SUCCESS_SID_V1075' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node successful Client-log rows are not folded by Live SID"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_RAW_SID_DEDUPE_V1075' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node raw Client-log SID dedupe missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_CLEAR_DEDUPE_RESET_V1075' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node Client-log clear dedupe reset missing"

# STREAMFORGE_V1077_NATIVE_BROWSER_HISTORY_VIDEO_ONLY_GUARD:
grep -Fq 'STREAMFORGE_MAIN_NATIVE_BROWSER_HISTORY_V1077' "$SOURCE_DIR/app/static/panel_nav.js" || die "v11.22 Main native browser history missing"
grep -Fq 'history.pushState(panelHistoryState' "$SOURCE_DIR/app/static/panel_nav.js" || die "v11.22 Main history.pushState navigation missing"
grep -Fq "addEventListener('popstate'" "$SOURCE_DIR/app/static/panel_nav.js" || die "v11.22 Main browser Back/Forward restore missing"
grep -Fq 'STREAMFORGE_NODE_NATIVE_BROWSER_HISTORY_V1077' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node native browser history missing"
grep -Fq 'history.pushState(mk' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node history.pushState navigation missing"
grep -Fq "addEventListener('popstate'" "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node browser Back/Forward restore missing"
grep -Fq 'STREAMFORGE_NODE_HLS_VIDEO_ONLY_V1076' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node video-only HLS preview missing"
# STREAMFORGE_V1078_CHANNEL_PAGINATION_GUARD:
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_PAGINATION_V1078' "$SOURCE_DIR/app/static/app.js" || die "v11.22 Main Channels pagination logic missing"
grep -Fq 'data-channel-pagination' "$SOURCE_DIR/app/templates/channels.html" || die "v11.22 Main Channels pagination controls missing"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_PAGINATION_V1078' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node Channels pagination logic missing"
grep -Fq 'data-node-channel-pagination' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node Channels pagination controls missing"
if sed -n '/STREAMFORGE_NODE_HLS_VIDEO_ONLY_V1076:/,/@app.get("\/panel-play\/{key}\/master.m3u8"/p' "$SOURCE_DIR/node_agent/app.py" | grep -Fq 'Back to channels'; then die "v11.22 Node HLS preview still renders Panel navigation"; fi
# STREAMFORGE_V1058_ENCRYPTED_CHILD_FASTPATH_GUARDS:
grep -Fq 'STREAMFORGE_ENCRYPTED_CHILD_ROUTE_FASTPATH_V1058' "$SOURCE_DIR/app/main.py" || die "v10.68 encrypted playlist child-route fast path missing"
[[ "$(grep -Fc 'STREAMFORGE_ENCRYPTED_CHILD_ROUTE_FASTPATH_V1058' "$SOURCE_DIR/app/main.py")" -ge 2 ]] || die "v10.68 encrypted index/segment fast-path markers incomplete"
# STREAMFORGE_V1060_CLIENT_LOG_INLINE_SESSION_AGE_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_CLIENT_LOG_SESSION_AGE_INLINE_V1060' "$SOURCE_DIR/app/main.py" || die "v10.68 Main inline Client-log Session age backend missing"
grep -Fq 'STREAMFORGE_MAIN_XTREAM_CLIENT_LOGIN_SESSION_CONTEXT_V1060' "$SOURCE_DIR/app/main.py" || die "v10.68 Main Xtream client login session context missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_LOG_REPLAY_AGE_V1062' "$SOURCE_DIR/app/main.py" || die "v10.68 repeated-playback inline Session age selector missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_LOG_CUMULATIVE_SESSION_AGE_V1063' "$SOURCE_DIR/app/main.py" || die "v10.68 Main cumulative Client-log Session age missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_LIVE_SID_EPOCH_BIND_V1073' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 Node SID+epoch Client-log Session age missing"
[[ "$(grep -Fc 'STREAMFORGE_MAIN_VIEWER_RECONNECT_FIRST_RESET_V1062' "$SOURCE_DIR/app/viewer_tracking.py")" -ge 2 ]] || die "v10.68 viewer reconnect generation reset incomplete"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_SESSION_HISTORY_ASYNC_V1060' "$SOURCE_DIR/app/viewer_tracking.py" || die "v10.68 Main async retained viewer history missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_SESSION_HISTORY_ASYNC_V1060' "$SOURCE_DIR/app/redis_state.py" || die "v10.68 Main best-effort history Redis isolation missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_INLINE_SESSION_AGE_V1060' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node duplicate playback-row folding missing"
# STREAMFORGE_V1064_REMOTE_NODE_TIMEOUT_BACKOFF_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_NODE_TRANSPORT_BACKOFF_V1064' "$SOURCE_DIR/app/node_manager.py" || die "v10.68 Main Node transport backoff missing"
grep -Fq 'STREAMFORGE_NODE_RECONCILE_TRANSPORT_FAILFAST_V1064' "$SOURCE_DIR/app/node_manager.py" || die "v10.68 Node catalogue transport fail-fast missing"
grep -Fq 'STREAMFORGE_NODE_RECONCILE_TRANSPORT_SHORTCIRCUIT_V1064' "$SOURCE_DIR/app/main.py" || die "v10.68 heartbeat registry transport short-circuit missing"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_RECONCILE_BACKOFF_V1064' "$SOURCE_DIR/app/main.py" || die "v10.68 heartbeat reconcile backoff missing"
# STREAMFORGE_V1135_SINGLE_FLIGHT_GUARD_COMPAT:
# v11.33 split explicit full reconciliation and targeted pending delivery into
# separate single-flight schedulers. Verify the stable implementation rather
# than the removed pre-v11.33 local variable spelling.
grep -Fq 'def _claim_node_reconcile(node_id: int) -> bool:' "$SOURCE_DIR/app/main.py" || die "heartbeat full-reconcile single-flight helper missing"
grep -Fq 'full_reconcile_requested and _claim_node_reconcile(int(node.id))' "$SOURCE_DIR/app/main.py" || die "heartbeat full-reconcile single-flight claim missing"
grep -Fq 'def _claim_node_targeted_retry(node_id: int) -> bool:' "$SOURCE_DIR/app/main.py" || die "heartbeat targeted-retry single-flight helper missing"
grep -Fq 'targeted_retry_requested and _claim_node_targeted_retry(int(node.id))' "$SOURCE_DIR/app/main.py" || die "heartbeat targeted-retry single-flight claim missing"
grep -Fq 'bypass_transport_backoff=bool(refresh)' "$SOURCE_DIR/app/node_manager.py" || die "v10.68 manual refresh transport-backoff bypass missing"
grep -Fq '_main_client_log_duration_states(local_rows, db)' "$SOURCE_DIR/app/main.py" || die "v10.68 Main existing-row Session age attachment missing"
grep -Fq 'threading.Thread(target=_main_client_session_history_observer_loop' "$SOURCE_DIR/app/main.py" || die "v10.68 Main control-plane session history observer missing"
grep -Fq "event_logs_snapshot = _node_fold_playback_session_rows(manager.event_logs_snapshot()) if selected_type == 'client'" "$SOURCE_DIR/node_agent/app.py" || die "v10.68 standalone Node playback-row folding call missing"
grep -Fq 'items = _node_fold_playback_session_rows(manager.event_logs_snapshot())' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 remote Node playback-row folding call missing"
# STREAMFORGE_V1025_SOURCE_GUARDS:
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_RUNTIME_SOURCE_LOOKUP_V1024' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node WebPlayer runtime-source fix missing"
! grep -Fq 'manager.config_inputs(item)' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node WebPlayer still reads source fields from catalogue DTO"
grep -Fq 'STREAMFORGE_MAIN_METRICS_PLAYBACK_READY_CHANNELS_V1024' "$SOURCE_DIR/app/main.py" || die "v10.25 Main delivery-ready metrics fix missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_READY_COUNT_V1024' "$SOURCE_DIR/app/main.py" || die "v10.25 Main Dashboard ready-count fix missing"
grep -Fq 'STREAMFORGE_METRICS_IMMEDIATE_FIRST_SAMPLE_V1024' "$SOURCE_DIR/app/metrics_history.py" || die "v10.25 immediate metrics first sample missing"
# STREAMFORGE_V1026_BULK_PROFILE_EDIT_DISPLAY_GUARDS:
grep -Fq 'STREAMFORGE_BULK_PROFILE_EDIT_EFFECTIVE_VALUES_V1026' "$SOURCE_DIR/app/main.py" || die "v10.30 effective per-node encoding profile backend missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_EDIT_DISPLAY_V1026' "$SOURCE_DIR/app/templates/channel_form.html" || die "v10.30 channel edit effective profile display missing"
grep -Fq 'Use channel default — {{ channel.video_codec if channel else' "$SOURCE_DIR/app/templates/channel_form.html" || die "v10.30 inherited video default label missing"
grep -Fq 'Use channel default — {{ channel.hls_segment_time if channel else 1 }}' "$SOURCE_DIR/app/templates/channel_form.html" || die "v10.30 inherited HLS default label missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_EDIT_DISPLAY_V1026' "$SOURCE_DIR/app/static/style.css" || die "v10.30 effective profile styling missing"
# STREAMFORGE_V1027_BULK_PROFILE_NODE_SCOPE_GUARDS:
grep -Fq 'STREAMFORGE_BULK_PROFILE_NODE_SCOPE_V1027' "$SOURCE_DIR/app/main.py" || die "v10.30 node-scoped bulk profile backend missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_TARGETED_RESTART_V1027' "$SOURCE_DIR/app/main.py" || die "v10.30 targeted bulk profile restart missing"
grep -Fq 'node_ids=node_scope' "$SOURCE_DIR/app/main.py" || die "v10.30 bulk profile sync is not node-scoped"
grep -Fq 'STREAMFORGE_TARGETED_CHANNEL_SYNC_V1027' "$SOURCE_DIR/app/node_manager.py" || die "v10.30 targeted channel sync backend missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_NODE_TARGET_UI_V1027' "$SOURCE_DIR/app/templates/channels.html" || die "v10.30 bulk profile node-target UI missing"
grep -Fq 'Encoding target nodes' "$SOURCE_DIR/app/templates/channels.html" || die "v10.30 bulk profile target label missing"
# STREAMFORGE_V1025_BULK_PROFILE_AND_FRESH_SSH_GUARDS:
grep -Fq 'STREAMFORGE_BULK_PROFILE_APPLY_RUNNING_V1025' "$SOURCE_DIR/app/main.py" || die "v10.25 bulk profile runtime-apply backend missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_TARGETED_RESTART_V1027' "$SOURCE_DIR/app/main.py" || die "v10.30 targeted bulk profile restart path missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_RUNNING_RESTART_V1025' "$SOURCE_DIR/app/node_manager.py" || die "v10.25 Main bulk profile restart sync missing"
grep -Fq '?restart_running=1' "$SOURCE_DIR/app/node_manager.py" || die "v10.25 Remote Node restart-running sync query missing"
grep -Fq 'STREAMFORGE_NODE_BULK_PROFILE_RUNNING_RESTART_V1025' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node bulk profile restart endpoint missing"
grep -Fq 'restart_running: bool = False' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node restart-running sync argument missing"
grep -Fq 'data-bulk-profile-v1025' "$SOURCE_DIR/app/templates/channels.html" || die "v10.25 bulk profile UI marker missing"
grep -Fq 'STREAMFORGE_NODE_SSH_YOUTUBE_BOOTSTRAP_PAYLOAD_V1025' "$SOURCE_DIR/app/ssh_installer.py" || die "v10.25 SSH Node YouTube bootstrap payload fix missing"
grep -Fq 'arcname="streamforge_mvp/scripts/bootstrap_youtube_runtime.sh"' "$SOURCE_DIR/app/ssh_installer.py" || die "v10.25 SSH payload does not archive YouTube bootstrap helper"
grep -Fq 'STREAMFORGE_MAIN_YOUTUBE_RUNTIME_INSTALL_STATUS_V1025' "$SOURCE_DIR/scripts/install.sh" || die "v10.25 fresh Main YouTube runtime status check missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_RUNTIME_INSTALL_STATUS_V1025' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v10.25 fresh Node YouTube runtime status check missing"
# STREAMFORGE_YOUTUBE_LIVE_SOURCE_V1012: Main and Node resolve server-local YouTube page URLs at runtime.
grep -Eq '^yt-dlp\[default\]>=2026\.6\.9$' "$SOURCE_DIR/requirements.txt" || die "v10.25 Main yt-dlp dependency is stale"
grep -Eq '^yt-dlp\[default\]>=2026\.6\.9$' "$SOURCE_DIR/node_agent/requirements.txt" || die "v10.25 Node yt-dlp dependency is stale"
grep -Fq 'STREAMFORGE_YOUTUBE_SOURCE_RESOLVER_V1012' "$SOURCE_DIR/app/source_resolver.py" || die "v10.25 Main YouTube resolver missing"
grep -Fq 'STREAMFORGE_YOUTUBE_SOURCE_RESOLVER_V1012' "$SOURCE_DIR/node_agent/source_resolver.py" || die "v10.25 Node YouTube resolver missing"
grep -Fq 'STREAMFORGE_YOUTUBE_LIVE_EDGE_V1017' "$SOURCE_DIR/app/input_options.py" || die "v10.25 Main YouTube live-edge input fix missing"
grep -Fq 'STREAMFORGE_MAIN_YOUTUBE_403_RECONNECT_V1023' "$SOURCE_DIR/app/input_options.py" || die "v10.25 Main YouTube 403 reconnect fix missing"
grep -Fq '"403,408,429,5xx" if youtube_live else "408,429,5xx"' "$SOURCE_DIR/app/input_options.py" || die "v10.25 Main YouTube reconnect policy is stale"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_LIVE_EDGE_V1017' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube live-edge input fix missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_YOUTUBE_EDGE_V1017' "$SOURCE_DIR/app/templates/player.html" || die "v10.25 Main WebPlayer YouTube live-edge profile missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_YOUTUBE_EDGE_V1017' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node WebPlayer YouTube live-edge profile missing"
grep -Fq 'STREAMFORGE_YOUTUBE_ANTIBOT_COOKIE_FALLBACK_V1013' "$SOURCE_DIR/app/source_resolver.py" || die "v10.25 Main YouTube anti-bot/cookies resolver missing"
grep -Fq 'STREAMFORGE_YOUTUBE_ANTIBOT_COOKIE_FALLBACK_V1013' "$SOURCE_DIR/node_agent/source_resolver.py" || die "v10.25 Node YouTube anti-bot/cookies resolver missing"
grep -Fq 'STREAMFORGE_MAIN_YOUTUBE_COOKIES_UPLOAD_V1013' "$SOURCE_DIR/app/main.py" || die "v10.25 Main YouTube cookies upload handler missing"
grep -Fq 'STREAMFORGE_MAIN_YOUTUBE_COOKIES_SETTINGS_V1013' "$SOURCE_DIR/app/templates/system_branding.html" || die "v10.25 Main YouTube cookies Settings UI missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_COOKIES_UPLOAD_V1013' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube cookies Settings handler missing"
grep -Fq 'STREAMFORGE_MAIN_HIDDEN_HLS_TARGET_FIX_V1013' "$SOURCE_DIR/app/static/panel_nav.js" || die "v10.25 Main HLS target=_blank hover-mask fix missing"
grep -Fq 'STREAMFORGE_YOUTUBE_RUNTIME_DIAGNOSTICS_V1014' "$SOURCE_DIR/app/source_resolver.py" || die "v10.25 Main YouTube runtime diagnostics missing"
grep -Fq 'STREAMFORGE_YOUTUBE_RUNTIME_DIAGNOSTICS_V1014' "$SOURCE_DIR/node_agent/source_resolver.py" || die "v10.25 Node YouTube runtime diagnostics missing"
grep -Fq 'STREAMFORGE_MAIN_YOUTUBE_PROBE_TIMEOUT_V1014' "$SOURCE_DIR/app/stream_info.py" || die "v10.25 Main YouTube staged probe timeout missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_PROBE_TIMEOUT_V1014' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube staged probe timeout missing"
grep -Fq 'STREAMFORGE_YOUTUBE_SCAN_METADATA_FASTPATH_V1015' "$SOURCE_DIR/app/stream_info.py" || die "v10.25 Main YouTube scan metadata fast-path missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_SCAN_METADATA_FASTPATH_V1015' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube scan metadata fast-path missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_HLS_INPUT_PARITY_V1015' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube HLS input parity missing"
grep -Fq 'STREAMFORGE_YOUTUBE_LIVE_UNPACED_RUNTIME_V1016' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main YouTube unpaced runtime fix missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_LIVE_UNPACED_RUNTIME_V1016' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube unpaced runtime fix missing"
grep -Fq 'STREAMFORGE_YOUTUBE_LIVE_STALL_TOLERANCE_V1016' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main YouTube stall tolerance missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_LIVE_STALL_TOLERANCE_V1016' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube stall tolerance missing"
grep -Fq 'STREAMFORGE_YOUTUBE_KEYFRAME_SAFE_COPY_HLS_V1020' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main YouTube keyframe-safe copy HLS fix missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_KEYFRAME_SAFE_COPY_HLS_V1020' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube keyframe-safe copy HLS fix missing"
grep -Fq 'local_hls_time = 1 if youtube_source else max(1, channel.hls_segment_time)' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main YouTube low-latency HLS target missing"
grep -Fq 'local_hls_time = 1 if youtube_source else max(1, cfg.hls_segment_time)' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube low-latency HLS target missing"
grep -Fq 'local_hls_list_size = 8 if youtube_source else 6' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main YouTube safe live window missing"
grep -Fq 'local_hls_list_size = 8 if youtube_source else 6' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube safe live window missing"
grep -Fq 'STREAMFORGE_YOUTUBE_KEYFRAME_SAFE_PLAYER_V1020' "$SOURCE_DIR/app/templates/player.html" || die "v10.25 Main YouTube keyframe-safe player profile missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_KEYFRAME_SAFE_PLAYER_V1020' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube keyframe-safe player profile missing"
grep -Fq 'STREAMFORGE_YOUTUBE_SMOOTH_PACED_RUNTIME_V1021' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main YouTube smooth pacing fix missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_SMOOTH_PACED_RUNTIME_V1021' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube smooth pacing fix missing"
grep -Fq 'STREAMFORGE_YOUTUBE_PACED_PLAYER_BUFFER_V1021' "$SOURCE_DIR/app/templates/player.html" || die "v10.25 Main YouTube paced player buffer missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_PACED_PLAYER_BUFFER_V1021' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube paced player buffer missing"
grep -Fq 'STREAMFORGE_YOUTUBE_TRUE_1S_GOP_TRANSCODE_V1022' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main YouTube true-1s transcode path missing"
grep -Fq 'STREAMFORGE_YOUTUBE_TRUE_1S_GOP_OUTPUT_V1022' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main YouTube true-1s HLS output marker missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_TRUE_1S_GOP_TRANSCODE_V1022' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube true-1s transcode path missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_TRUE_1S_GOP_OUTPUT_V1022' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube true-1s HLS output marker missing"
grep -Fq 'force_h264_transcode=youtube_low_latency_transcode' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main YouTube copy-to-H.264 activation missing"
grep -Fq 'hls_segment_time_override=1 if youtube_low_latency_transcode else None' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main YouTube 1s GOP override missing"
grep -Fq 'requested = "auto_h264"' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube copy-to-H.264 activation missing"
grep -Fq 'seg = 1 if youtube_source else max(1, cfg.hls_segment_time)' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube 1s GOP override missing"
if grep -Fq 'delete_segments+program_date_time+temp_file+omit_endlist+split_by_time' "$SOURCE_DIR/app/ffmpeg.py"; then die "v10.25 Main still contains unsafe YouTube split_by_time flags"; fi
if grep -Fq 'delete_segments+program_date_time+temp_file+omit_endlist+split_by_time' "$SOURCE_DIR/node_agent/app.py"; then die "v10.25 Node still contains unsafe YouTube split_by_time flags"; fi
grep -Fq 'def youtube_probe_payload' "$SOURCE_DIR/app/source_resolver.py" || die "v10.25 Main YouTube probe metadata cache missing"
grep -Fq 'def youtube_probe_payload' "$SOURCE_DIR/node_agent/source_resolver.py" || die "v10.25 Node YouTube probe metadata cache missing"
grep -Fq 'STREAMFORGE_YOUTUBE_RUNTIME_BOOTSTRAP_V1014' "$SOURCE_DIR/scripts/bootstrap_youtube_runtime.sh" || die "v10.25 YouTube runtime bootstrap missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_COOKIE_OWNER_SELFHEAL_V1014' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v10.25 Node YouTube cookie owner self-heal missing"
grep -Fq 'resolve_stream_source(source_target, force=youtube_source)' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main YouTube FFmpeg resolution missing"
grep -Fq 'resolve_stream_source(source_target, force=youtube_source)' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node YouTube FFmpeg resolution missing"
grep -Fq 'resolve_stream_source(str(target), force=youtube_source)' "$SOURCE_DIR/app/stream_info.py" || die "v10.25 Main YouTube source-scan resolution missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_SOURCE_SCAN_GUARDED_V1067' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node guarded YouTube source-scan resolution missing"
grep -Fq '_guarded_resolve_stream_source(target, force=youtube_source, bypass_backoff=youtube_source)' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node guarded YouTube source-scan call missing"
# v10.13 durable Web Player upload preservation guards.
grep -Fq 'STREAMFORGE_WEBPLAYER_DOWNLOAD_UPDATE_PRESERVE_V1011' "$SOURCE_UPDATER" || die "v10.25 Web Player update preservation missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_DOWNLOAD_DURABLE_STORAGE_V1011' "$SOURCE_DIR/scripts/install.sh" || die "v10.25 fresh Main Web Player durable storage missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_DOWNLOAD_ROOT=/var/lib/streamforge/webplayer-downloads' "$SOURCE_DIR/scripts/install.sh" || die "v10.25 fresh Main Web Player storage env missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_DURABLE_STORAGE_V1011' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v10.25 fresh Node Web Player durable storage missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_FILE=/var/lib/streamforge-node/webplayer-downloads/managed-download.bin' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v10.25 fresh Node Web Player storage env missing"
grep -Fq 'STREAMFORGE_RESETPANELACCESS_COMMAND_V107' "$SOURCE_DIR/scripts/streamforge" || die "v10.25 resetpanelaccess CLI missing from package"
grep -Fq 'resetpanelaccess)' "$SOURCE_DIR/scripts/streamforge" || die "v10.25 resetpanelaccess command option missing from package"
grep -Fq 'STREAMFORGE_NODE_FRESH_DEPENDENCY_BOOTSTRAP_V107' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v10.25 fresh Node dependency bootstrap missing"
grep -Fq 'STREAMFORGE_NODE_SSH_TAR_HYGIENE_V107' "$SOURCE_DIR/app/ssh_installer.py" || die "v10.25 Node SSH payload hygiene missing"
grep -Fq 'STREAMFORGE_NODE_TARGET_PYTHON_COMPILE_V108' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v10.25 Remote target-Python syntax preflight missing"
grep -Fq 'category_text = html.escape(", ".join(_config_categories(config)))' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Python 3.10 dashboard category f-string repair missing"
grep -Fq 'category_text = html.escape(", ".join(_config_categories(c)))' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Python 3.10 channel-list category f-string repair missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_IP_WHITELIST_V98' "$SOURCE_DIR/node_agent/app.py" || die "v9.8 separate Node Panel IP whitelist runtime missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_PLAYBACK_WHITELIST_SPLIT_V98' "$SOURCE_DIR/node_agent/app.py" || die "v9.8 panel/playback whitelist split missing"
grep -Fq 'STREAMFORGE_NODE_GENERIC_NETWORK_BLOCK_PAGE_V98' "$SOURCE_DIR/node_agent/app.py" || die "v9.8 generic restricted Web Player page missing"
grep -Fq 'STREAMFORGE_NODE_CENTERED_NETWORK_BLOCK_PAGE_V98R3' "$SOURCE_DIR/node_agent/app.py" || die "v9.8 r3 centered embedded-logo restricted page missing"
grep -Fq 'panel_ip_whitelist' "$SOURCE_DIR/app/models.py" || die "v9.8 Panel IP whitelist database model missing"
[[ -x "$SOURCE_DIR/scripts/migrate_v98_panel_ip_whitelist.py" ]] || die "v9.8 Panel IP whitelist migration script missing"
grep -Fq 'STREAMFORGE_V98_PANEL_IP_WHITELIST_MIGRATION' "$SOURCE_DIR/scripts/migrate_v98_panel_ip_whitelist.py" || die "v9.8 Panel IP whitelist migration marker missing"
grep -Fq 'STREAMFORGE_PANEL_FULL_ACCESS_POLICY_V99' "$SOURCE_DIR/app/models.py" || die "v9.9 Panel full access-policy database model missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_FULL_ACCESS_POLICY_V99' "$SOURCE_DIR/node_agent/app.py" || die "v9.9 Node Panel four-rule runtime missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_CLIENT_IP_METHOD_FIX_V99R4' "$SOURCE_DIR/node_agent/app.py" || die "v9.9 r4 Node Panel client-IP method fix missing"
grep -Fq 'ip_text = self._client_ip(request)' "$SOURCE_DIR/node_agent/app.py" || die "v9.9 r4 Node Panel client-IP resolver is stale"
grep -Fq 'Panel IP blacklist' "$SOURCE_DIR/app/templates/node_form.html" || die "v9.9 Main Node Panel access row missing"
grep -Fq 'access-rule-row' "$SOURCE_DIR/node_agent/app.py" || die "v9.9 Node Settings one-line access rows missing"
[[ -x "$SOURCE_DIR/scripts/migrate_v99_panel_access_policy.py" ]] || die "v9.9 Panel access migration script missing"
grep -Fq 'STREAMFORGE_V99_PANEL_ACCESS_POLICY_MIGRATION' "$SOURCE_DIR/scripts/migrate_v99_panel_access_policy.py" || die "v9.9 Panel access migration marker missing"
grep -Fq 'STREAMFORGE_CHANNEL_STREAM_LOG_CLEAR_PREFIX_V66' "$SOURCE_DIR/app/static/app.js" || die "v6.6 prefixed Stream Logs clear fix missing"
grep -Fq 'STREAMFORGE_LOG_TAB_SCOPED_CLEAR_V66' "$SOURCE_DIR/app/main.py" || die "v6.6 Main tab-scoped log clear missing"
grep -Fq 'STREAMFORGE_NODE_SCOPED_LOG_CLEAR_V66' "$SOURCE_DIR/node_agent/app.py" || die "v6.6 Node scoped log clear missing"
grep -Fq 'STREAMFORGE_NODE_SCOPED_LOG_CLEAR_V66' "$SOURCE_DIR/app/node_manager.py" || die "v6.6 Main-to-Node scoped log clear client missing"
grep -Fq 'STREAMFORGE_NODE_MEDIA_AUTH_CLIENT_IP_V67' "$SOURCE_DIR/node_agent/app.py" || die "v6.8 Node media-auth client-IP preservation missing"
grep -Fq 'STREAMFORGE_NODE_MEDIA_AUTH_PROXY_IP_V67' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v6.8 Node Nginx media-auth proxy-IP fix missing"
grep -Fq 'STREAMFORGE_NODE_MULTI_URL_SAME_ORIGIN_MEDIA_V68' "$SOURCE_DIR/node_agent/app.py" || die "v6.8 Node multi-URL same-origin media fix missing"
grep -Fq 'STREAMFORGE_NODE_MULTI_URL_MEDIA_CLIENT_IP_V68' "$SOURCE_DIR/node_agent/app.py" || die "v6.8 Node multi-URL client-IP fix missing"
grep -Fq 'STREAMFORGE_NODE_MULTI_URL_PROXY_CLIENT_IP_V68' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v6.8 Node proxy client-IP forwarding fix missing"
grep -Fq 'STREAMFORGE_BRANDED_BROWSER_TITLES_V68' "$SOURCE_DIR/app/templates/base.html" || die "v6.8 branded browser-title fix missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_REQUEST_COMMIT_V69' "$SOURCE_DIR/app/audit_log.py" || die "v7.0 Client log request-session persistence fix missing"
grep -Fq 'STREAMFORGE_REQUEST_DIRTY_COMMIT_V69' "$SOURCE_DIR/app/db.py" || die "v7.0 request dirty-session commit hook missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_WEBPLAYER_V70' "$SOURCE_DIR/node_agent/app.py" || die "v7.0 Node WebPlayer Client log integration missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_QUICK_LOGIN_V70' "$SOURCE_DIR/node_agent/app.py" || die "v7.0 Node quick-login Client log integration missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_PLAYLIST_CONTEXT_V70' "$SOURCE_DIR/node_agent/app.py" || die "v7.0 Node playlist Client log context missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_LOG_AUTO_LOGIN_V71' "$SOURCE_DIR/app/main.py" || die "v7.1 Main Auto Login Client log integration missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_AUTO_LOGIN_V71' "$SOURCE_DIR/node_agent/app.py" || die "v7.1 Node Auto Login Client log integration missing"
grep -Fq 'streamforge_web_player_auto_log_marker' "$SOURCE_DIR/app/main.py" || die "v7.1 Main Auto Login anti-spam session marker missing"
grep -Fq '_NODE_WEB_AUTO_LOG_COOKIE' "$SOURCE_DIR/node_agent/app.py" || die "v7.1 Node Auto Login anti-spam cookie marker missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_DIRECT_DETAILS_IP_V72' "$SOURCE_DIR/app/main.py" || die "v7.9 Main direct log Details/IP integration missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_DIRECT_DETAILS_IP_V72' "$SOURCE_DIR/app/templates/logs.html" || die "v7.9 Main log IP/details columns missing"
grep -Fq 'STREAMFORGE_NODE_LOG_DIRECT_DETAILS_IP_V72' "$SOURCE_DIR/node_agent/app.py" || die "v7.9 Node log IP/details columns missing"
grep -Fq 'STREAMFORGE_ACCESS_LOG_CLIENT_IP_V73' "$SOURCE_DIR/app/main.py" || die "v7.9 Main Access-log client-IP persistence missing"
grep -Fq 'STREAMFORGE_ACCESS_LOG_CLIENT_IP_V73' "$SOURCE_DIR/node_agent/app.py" || die "v7.9 Node Access-log client-IP persistence missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_MOBILE_NODE_LAYOUT_V73' "$SOURCE_DIR/app/templates/logs.html" || die "v7.9 Main mobile Logs table marker missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_MOBILE_NODE_LAYOUT_V73' "$SOURCE_DIR/app/static/style.css" || die "v7.9 Main mobile Logs Node-style layout missing"
grep -Fq 'STREAMFORGE_LOG_SUBJECT_MAPPING_V74' "$SOURCE_DIR/app/main.py" || die "v7.4 Channel/Node structured subject mapping missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_SUBJECT_MAPPING_V75' "$SOURCE_DIR/app/main.py" || die "v7.9 Client-log Main Panel subject mapping missing"
grep -Fq 'STREAMFORGE_HLS_REALTIME_RE_COMPAT_V94ROLLBACK' "$SOURCE_DIR/app/input_options.py" || die "v9.4 rollback-safe Main HLS -re pacing missing"
grep -Fq 'STREAMFORGE_MAIN_HLS_REALTIME_READRATE_V76' "$SOURCE_DIR/app/ffmpeg.py" || die "v7.9 Main HLS realtime pacing integration missing"
grep -Fq 'build_input_args(resolved_target, realtime_hls=True, youtube_live=youtube_source)' "$SOURCE_DIR/app/ffmpeg.py" || die "v10.25 Main FFmpeg YouTube-aware HLS pacing missing"
grep -Fq 'STREAMFORGE_NODE_HLS_REALTIME_RE_COMPAT_V94ROLLBACK' "$SOURCE_DIR/node_agent/app.py" || die "v9.4 rollback-safe Node HLS -re pacing missing"
grep -Fq 'cmd += ["-re"]' "$SOURCE_DIR/node_agent/app.py" || die "v9.4 rollback-safe Node FFmpeg -re option missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_PLAYLIST_NO_OPEN_FILE_CACHE_V77' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v7.9 Node live-playlist open_file_cache bypass missing"
grep -Fq 'node-nginx-live-playlist-v77' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v7.9 Node fresh playlist Nginx marker missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_STABLE_EDGE_BUFFER_V77' "$SOURCE_DIR/app/templates/player.html" || die "v7.9 Main WebPlayer stable-edge buffer missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_STABLE_EDGE_BUFFER_V77' "$SOURCE_DIR/node_agent/app.py" || die "v7.9 Node WebPlayer stable-edge buffer missing"
grep -Fq 'STREAMFORGE_NODE_V77_ROOT_NGINX_PLAYLIST_GUARD' "$SOURCE_DIR/node_agent/app.py" || die "v7.9 Node root Nginx upgrade guard missing"
grep -Fq 'name="log_type" value="{{ selected_log_type }}"' "$SOURCE_DIR/app/templates/logs.html" || die "v6.6 Logs clear tab selector missing"
# STREAMFORGE_NODE_HIGH_CONCURRENCY_PACKAGE_GUARDS_V65
grep -Fq 'STREAMFORGE_NODE_REDIS_SHARED_STATE_V65' "$SOURCE_DIR/node_agent/app.py" || die "v6.5 Node Redis shared-state integration missing"
[[ -f "$SOURCE_DIR/node_agent/redis_state.py" ]] || die "v6.5 Node Redis state module missing"
grep -Eq '^redis>=5\.0,<7\.0$' "$SOURCE_DIR/node_agent/requirements.txt" || die "v6.5 Node Redis Python dependency missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_POOL_TLS_BACKEND_V65' "$SOURCE_DIR/node_agent/app.py" || die "v6.5 Node public multi-worker pool missing"
grep -Fq 'STREAMFORGE_NODE_NGINX_DIRECT_HLS_V65' "$SOURCE_DIR/node_agent/app.py" || die "v6.5 Node direct HLS master handoff missing"
grep -Fq 'STREAMFORGE_NODE_SINGLE_CHANNEL_READY_V65' "$SOURCE_DIR/node_agent/app.py" || die "v6.5 Node single-channel hot-path readiness check missing"
grep -Fq 'STREAMFORGE_NODE_SESSION_PLAYBACK_GRANT_V65' "$SOURCE_DIR/node_agent/app.py" || die "v6.5 Node session-scoped playback grant optimization missing"
grep -Fq 'STREAMFORGE_NODE_NGINX_DIRECT_MEDIA_V65' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v6.5 Node Nginx direct-media config missing"
grep -Fq 'STREAMFORGE_NODE_AUTH_CACHE_V65' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v6.5 Node media auth cache config missing"
grep -Fq 'STREAMFORGE_NODE_CONTROL_API_PRECEDENCE_V65R8' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v6.5-r8 Node control API routing precedence missing"
grep -Fq 'STREAMFORGE_NODE_NO_EXTERNAL_8810_V65R8' "$SOURCE_DIR/app/node_manager.py" || die "v6.5-r8 external 8810 suppression missing"
grep -Fq 'redis-server redis-tools' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v6.5 Node Redis runtime installer missing"
grep -Fq 'tune_high_concurrency.py' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v6.5 Node high-concurrency Nginx tuning hook missing"
grep -Fq 'agent_root / "redis_state.py"' "$SOURCE_DIR/app/node_manager.py" || die "v6.5 Node update archive Redis module missing"
grep -Fq 'STREAMFORGE_NGINX_RELAY_FASTPATH_V60' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v6.0 Nginx relay fast path missing from access helper"
grep -Fq 'sync_local_relay_links' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v6.0 signed relay link seeding missing"
grep -Fq 'STREAMFORGE_NGINX_RELAY_FASTPATH_V60' "$SOURCE_DIR/app/node_manager.py" || die "v6.0 runtime relay-link maintenance missing"
grep -Fq 'X-StreamForge-Relay "nginx-direct"' "$SOURCE_DIR/deploy/nginx.conf" || die "v6.0 fresh-install Nginx relay marker missing"
grep -Fq 'STREAMFORGE_MAIN_AUTO_RESTART_VISIBLE_V54' "$SOURCE_DIR/app/ffmpeg.py" || die "v5.4 Main auto-restart visibility marker missing"
grep -Fq 'STREAMFORGE_MULTI_REMOTE_OUTPUT_URLS_V54' "$SOURCE_DIR/app/main.py" || die "v5.4 multiple remote-output form backend missing"
grep -Fq 'STREAMFORGE_MULTI_REMOTE_OUTPUT_TEE_V54' "$SOURCE_DIR/app/ffmpeg.py" || die "v5.4 Main multi-output tee runtime missing"
grep -Fq 'STREAMFORGE_MULTI_REMOTE_OUTPUT_EDITOR_V54' "$SOURCE_DIR/app/templates/channel_form.html" || die "v5.4 Main multi-output editor missing"
grep -Fq 'STREAMFORGE_MAIN_MULTI_REMOTE_OUTPUT_EDITOR_EXEC_V55' "$SOURCE_DIR/app/templates/channel_form.html" || die "v5.5 executable Main multi-output editor missing"
grep -Fq 'STREAMFORGE_NODE_HIDE_MAIN_SYNC_SOURCE_V55' "$SOURCE_DIR/node_agent/app.py" || die "v5.5 Node Main-source privacy fix missing"
grep -Fq 'STREAMFORGE_NODE_HIDE_MAIN_SYNC_SOURCE_LIST_V55' "$SOURCE_DIR/node_agent/app.py" || die "v5.5 Node Main-source list privacy fix missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_LOCAL_FAST_PATH_V55' "$SOURCE_DIR/app/main.py" || die "v5.5 Main Web Player Local fast path missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_SINGLE_CHANNEL_FAST_PATH_V55' "$SOURCE_DIR/app/main.py" || die "v5.5 Web Player single-channel fast path missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_ZERO_NETWORK_CATALOG_V55' "$SOURCE_DIR/app/main.py" || die "v5.5 Web Player zero-network catalogue missing"
grep -Fq 'STREAMFORGE_PUBLIC_CATALOG_LOCAL_READINESS_V62R2' "$SOURCE_DIR/app/main.py" || die "v6.2-r2 Public Local playlist readiness fix missing"
grep -Fq 'STREAMFORGE_PUBLIC_CATALOG_LOCAL_STATE_V62R2' "$SOURCE_DIR/app/main.py" || die "v6.2-r2 Public WebPlayer Local catalogue fix missing"
grep -Fq 'STREAMFORGE_MAIN_LOCAL_HLS_READY_CACHE_V55' "$SOURCE_DIR/app/node_manager.py" || die "v5.5 local HLS readiness cache missing"
grep -Fq 'STREAMFORGE_NODE_MULTI_REMOTE_OUTPUT_TEE_V54' "$SOURCE_DIR/node_agent/app.py" || die "v5.4 Node multi-output tee runtime missing"
! grep -Fq 'v2.0.1 ultra-low-latency profile uses 1 second.' "$SOURCE_DIR/app/templates/channel_form.html" || die "Legacy HLS help text remains in Main channel editor"
! grep -Fq 'v2.0.1 ultra-low-latency profile uses 1 second.' "$SOURCE_DIR/node_agent/app.py" || die "Legacy HLS help text remains in Node channel editor"
grep -Fq 'STREAMFORGE_MAIN_SOURCE_SCAN_PUBLIC_PREFIX_V52' "$SOURCE_DIR/app/static/app.js" || die "v5.4 Main prefix-aware source scan missing"
grep -Fq 'STREAMFORGE_NODE_ROOT_SOURCE_SCAN_ACTION_V53' "$SOURCE_DIR/node_agent/app.py" || die "v5.4 Node root-action source scan routing missing"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_EDITOR_JS_ESCAPE_V53' "$SOURCE_DIR/node_agent/app.py" || die "v5.4 Node channel editor JavaScript escaping fix missing"
grep -Fq 'STREAMFORGE_NODE_HIDDEN_POST_DISPATCH_V53' "$SOURCE_DIR/node_agent/app.py" || die "v5.4 hidden-root POST dispatch missing"
grep -Fq 'STREAMFORGE_NODE_SETTINGS_SAFE_RETURN_V53' "$SOURCE_DIR/node_agent/app.py" || die "v5.4 Node Settings safe return missing"
grep -Fq 'STREAMFORGE_NODE_LOCAL_SAVE_ACCESS_APPLY_V56' "$SOURCE_DIR/node_agent/app.py" || die "v5.6 Node local Settings listener reconcile missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_MAIN_WEB_ISOLATION_V57' "$SOURCE_DIR/app/main.py" || die "v5.7 Main Web/DB Node-update isolation missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_MAINTENANCE_LEASE_V57' "$SOURCE_DIR/app/main.py" || die "v5.7 Node update maintenance lease missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_MAINTENANCE_ISOLATION_V57' "$SOURCE_DIR/app/node_manager.py" || die "v5.7 Node maintenance routing state missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_HLS_FAIL_FAST_V57' "$SOURCE_DIR/app/node_manager.py" || die "v5.7 Node update HLS fail-fast missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_PLAYBACK_FALLBACK_V57' "$SOURCE_DIR/app/load_balancer.py" || die "v5.7 playback maintenance fallback missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_LOW_CPU_SSH_PACKAGE_V57' "$SOURCE_DIR/app/ssh_installer.py" || die "v5.7 low-CPU SSH package build missing"
grep -Fq 'STREAMFORGE_NODE_SSH_COMPLETE_PAYLOAD_V65R3' "$SOURCE_DIR/app/ssh_installer.py" || die "v6.5-r3 complete SSH Node payload fix missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_CONCISE_ERROR_V65R3' "$SOURCE_DIR/app/main.py" || die "v6.5-r3 concise Node update error handling missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_ERROR_SUMMARY_ONLY_V65R3' "$SOURCE_DIR/app/templates/node_update.html" || die "v6.5-r3 Node update summary-only error UI missing"
grep -Fq 'STREAMFORGE_NODE_SHARED_PORT_PUBLIC_POOL_V65R4' "$SOURCE_DIR/node_agent/app.py" || die "v6.5-r4 Node shared-port Public pool activation fix missing"
grep -Fq 'STREAMFORGE_NODE_ATOMIC_STATE_WRITE_V65R4' "$SOURCE_DIR/node_agent/app.py" || die "v6.5-r4 Node atomic state-write fix missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_LOW_CPU_PACKAGE_V57' "$SOURCE_DIR/app/node_manager.py" || die "v5.7 low-CPU API package build missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_SKIP_REDUNDANT_LOGO_SYNC_V57' "$SOURCE_DIR/app/node_manager.py" || die "v5.7 redundant post-update logo sync suppression missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_LIGHTWEIGHT_CATALOG_SYNC_V57' "$SOURCE_DIR/app/main.py" || die "v5.7 lightweight post-update catalogue sync missing"
grep -Fq 'STREAMFORGE_NODE_LOCAL_SAVE_CONTROL_SYNC_V58' "$SOURCE_DIR/node_agent/app.py" || die "v5.8 Node local-save native control sync missing"
grep -Fq 'background=BackgroundTask(manager.queue_control_access_reconcile_after_response)' "$SOURCE_DIR/node_agent/app.py" || die "v5.8 Node Settings control-sync handoff missing"
grep -Fq 'STREAMFORGE_NODE_CROSS_PROCESS_ACCESS_RECONCILE_V59' "$SOURCE_DIR/node_agent/app.py" || die "v5.9 Node cross-process access reconcile missing"
grep -Fq 'STREAMFORGE_NODE_MULTI_ALIAS_PORT_ROUTING_V59' "$SOURCE_DIR/node_agent/app.py" || die "v5.9 Node multi-alias port routing missing"
grep -Fq 'watch_access_reconcile_requests' "$SOURCE_DIR/node_agent/app.py" || die "v5.9 Node native access watcher missing"
grep -Fq 'streamforge-node-access-reconcile' "$SOURCE_DIR/node_agent/app.py" || die "v5.9 Node access watcher startup missing"
grep -Fq 'STREAMFORGE_NODE_WAITING_TIMER_V51' "$SOURCE_DIR/node_agent/app.py" || die "Node waiting-time tracker missing from package"
grep -Fq '"waiting_seconds": waiting_seconds' "$SOURCE_DIR/node_agent/app.py" || die "Node waiting-time status payload missing from package"
grep -Fq 'STREAMFORGE_NODE_SETTINGS_HIDE_TOTAL_MAX_V51' "$SOURCE_DIR/node_agent/app.py" || die "Node Main-managed total connection preservation missing from package"
! grep -Fq 'name="total_max_connections"' "$SOURCE_DIR/node_agent/app.py" || die "Node Settings still exposes Total max connections"
grep -Fq 'STREAMFORGE_NODE_SETTINGS_TLS_PANEL_V51' "$SOURCE_DIR/node_agent/app.py" || die "Node Settings SSL/DNS-01 panel missing from package"
grep -Fq 'STREAMFORGE_NODE_SETTINGS_TLS_ACTIONS_V51' "$SOURCE_DIR/node_agent/app.py" || die "Node Settings SSL actions missing from package"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_VISIBLE_WIDTH_V51' "$SOURCE_DIR/node_agent/app.py" || die "Node Channels blank-width fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_V49' "$SOURCE_DIR/app/static/panel_nav.js" || die "Normal Main Panel navigation missing from package"
grep -Fq 'STREAMFORGE_NODE_HIDDEN_NATIVE_ROUTE_V50' "$SOURCE_DIR/node_agent/app.py" || die "Node hidden-route full-reload navigation missing from package"
grep -Fq 'STREAMFORGE_NODE_HIDDEN_NATIVE_ROUTE_HEAD_V50' "$SOURCE_DIR/node_agent/app.py" || die "Node early hidden-route guard missing from package"
grep -Fq 'STREAMFORGE_NODE_PANEL_ROUTE_COOKIE = "streamforge_node_panel_route"' "$SOURCE_DIR/node_agent/app.py" || die "Node hidden-route cookie contract missing from package"
grep -Fq 'STREAMFORGE_NODE_BROWSER_COOKIE_FIX_V60R1' "$SOURCE_DIR/node_agent/app.py" || die "v6.0-r1 Node browser cookie fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_INITIAL_UPTIME_V60R2' "$SOURCE_DIR/app/main.py" || die "v6.0-r2 Main channel initial uptime fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_LOCAL_HLS_READY_RACE_FALLBACK_V60R2' "$SOURCE_DIR/app/node_manager.py" || die "v6.0-r2 local HLS readiness fallback missing from package"
grep -Fq 'STREAMFORGE_NODE_TEST_SYNC_BACKGROUND_V60R2' "$SOURCE_DIR/app/main.py" || die "v6.0-r2 background Node Test & Sync fix missing from package"
grep -Fq 'STREAMFORGE_NODE_STATUS_HEARTBEAT_FIRST_V60R2' "$SOURCE_DIR/app/main.py" || die "v6.0-r2 heartbeat-first Node status fix missing from package"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_LIVE_SUMMARY_V65R6' "$SOURCE_DIR/app/main.py" || die "v6.5-r6 Main heartbeat live-summary cache missing from package"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_LIVE_SUMMARY_V65R6' "$SOURCE_DIR/node_agent/app.py" || die "v6.5-r6 Node heartbeat live-summary sender missing from package"
grep -Fq 'STREAMFORGE_NODE_CARD_TIMEOUT_PRESERVE_HEARTBEAT_V60R2' "$SOURCE_DIR/app/static/app.js" || die "v6.0-r2 Node card timeout preservation fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_LOG_PAGE_LOCAL_FAST_LOAD_V60R3' "$SOURCE_DIR/app/main.py" || die "v6.0-r3 Main Logs fast-load fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_LOG_LIMIT_SELECTOR_V84' "$SOURCE_DIR/app/main.py" || die "v8.4 Main log limit selector backend missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_LIMIT_SELECTOR_V84' "$SOURCE_DIR/app/templates/logs.html" || die "v8.4 Main log limit selector template missing"
grep -Fq 'STREAMFORGE_NODE_LOG_LIMIT_SELECTOR_V84' "$SOURCE_DIR/node_agent/app.py" || die "v8.4 Node log limit selector missing"
grep -Fq 'STREAMFORGE_NODE_LOG_LIMIT_SELECTOR_V84' "$SOURCE_DIR/app/node_manager.py" || die "v8.4 Main-to-Node full log fetch missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_AUTO_FILTER_EXTENDED_LIMIT_V85' "$SOURCE_DIR/app/main.py" || die "v8.9 Main Logs default/extended limit backend missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_AUTO_FILTER_EXTENDED_LIMIT_V85' "$SOURCE_DIR/app/templates/logs.html" || die "v8.9 Main Logs auto-filter template missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_AUTO_FILTER_EXTENDED_LIMIT_V85' "$SOURCE_DIR/app/static/app.js" || die "v8.9 Main Logs auto-filter JavaScript missing"
grep -Fq 'STREAMFORGE_NODE_LOG_AUTO_FILTER_EXTENDED_LIMIT_V85' "$SOURCE_DIR/node_agent/app.py" || die "v8.9 Node Logs auto-filter/extended limit missing"
grep -Fq 'STREAMFORGE_CHANNEL_LIST_LIMIT_SELECTOR_V85' "$SOURCE_DIR/app/main.py" || die "v8.9 Channels list-limit backend missing"
grep -Fq 'STREAMFORGE_CHANNEL_LIST_LIMIT_SELECTOR_V85' "$SOURCE_DIR/app/templates/channels.html" || die "v8.9 Channels list-limit selector missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_PAGINATION_V1078' "$SOURCE_DIR/app/static/app.js" || die "v11.8 Channels pagination JavaScript missing"
grep -Fq 'STREAMFORGE_NODE_LOG_LAYOUT_REPAIR_V86' "$SOURCE_DIR/node_agent/app.py" || die "v8.9 Node Logs layout repair missing"
grep -Fq 'STREAMFORGE_NODE_ROOT_LOGIN_REDIRECT_V87' "$SOURCE_DIR/node_agent/app.py" || die "v8.9 Node root login redirect missing"
grep -Fq 'STREAMFORGE_MAIN_ACTIVE_INPUT_SOURCE_V87' "$SOURCE_DIR/app/main.py" || die "v8.9 Main active-input source backend missing"
grep -Fq 'STREAMFORGE_MAIN_ACTIVE_INPUT_SOURCE_V87' "$SOURCE_DIR/app/templates/channels.html" || die "v8.9 Main active-input source template missing"
grep -Fq 'STREAMFORGE_MAIN_ACTIVE_INPUT_SOURCE_V87' "$SOURCE_DIR/app/static/app.js" || die "v8.9 Main active-input source live refresh missing"
grep -Fq 'STREAMFORGE_NODE_MAIN_SHARED_LOG_SOURCE_HIDDEN_V87' "$SOURCE_DIR/node_agent/app.py" || die "v8.9 Main-shared Node Stream Log source hiding missing"
grep -Fq 'STREAMFORGE_NODE_LOCAL_RESTREAMER_V87' "$SOURCE_DIR/node_agent/app.py" || die "v8.9 Node-local Restreamer support missing"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_FILTER_NO_LIMIT_V87' "$SOURCE_DIR/node_agent/app.py" || die "v8.9 Node Channels no-limit filter repair missing"
grep -Fq 'STREAMFORGE_NODE_LOG_RETENTION_V88' "$SOURCE_DIR/node_agent/app.py" || die "v8.9 Node log retention missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_THEME_CROSS_PROCESS_V88' "$SOURCE_DIR/node_agent/app.py" || die "v8.9 Node WebPlayer cross-process theme sync missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_RETENTION_V88' "$SOURCE_DIR/app/templates/system_branding.html" || die "v8.9 Main log retention settings UI missing"
grep -Fq 'LOG_RETENTION_DAYS_KEY = "log_retention_days"' "$SOURCE_DIR/app/main.py" || die "v8.9 Main log retention backend missing"
grep -Fq 'Unlimited" if max(0, int(item.max_connections or 0)) == 0' "$SOURCE_DIR/node_agent/app.py" || die "v8.9 Node playlist unlimited display fix missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSIONS_ON_DEMAND_V89' "$SOURCE_DIR/node_agent/app.py" || die "v8.9 Node live-session endpoint missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_LIVE_SESSIONS_CLIENT_V89' "$SOURCE_DIR/app/node_manager.py" || die "v8.9 Main-to-Node live-session client missing"
grep -Fq 'STREAMFORGE_MAIN_REMOTE_LIVE_SESSIONS_V89' "$SOURCE_DIR/app/main.py" || die "v8.9 Main Remote Node live-session merge missing"
grep -Fq 'STREAMFORGE_MAIN_CACHED_REMOTE_VIEWER_COUNTS_V90' "$SOURCE_DIR/app/main.py" || die "v9.0 Main cached Remote viewer aggregation missing"
grep -Fq 'STREAMFORGE_NODE_DIRECT_SESSION_ONLY_V90' "$SOURCE_DIR/node_agent/app.py" || die "v9.0 Node direct-session-only view missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_HEARTBEAT_V90' "$SOURCE_DIR/node_agent/app.py" || die "v9.0 Node live-session heartbeat backend missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_HEARTBEAT_NGINX_V90' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v9.0 Node live-session heartbeat Nginx path missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_DASHBOARD_SYNC_V90R2' "$SOURCE_DIR/app/main.py" || die "v9.0-r2 Main Live Sessions/Dashboard sync missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_2S_REFRESH_V90R2' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "v9.0-r2 Main Live Sessions 2s refresh missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_2S_REFRESH_V90R2' "$SOURCE_DIR/node_agent/app.py" || die "v9.0-r2 Node Live Sessions 2s refresh missing"
grep -Fq 'STREAMFORGE_NODE_REDIS_VIEWER_TTL_EXACT_V90R2' "$SOURCE_DIR/node_agent/redis_state.py" || die "v9.0-r2 Node Redis viewer TTL fix missing"
grep -Fq 'STREAMFORGE_NODE_VIEWER_TTL_LOAD_GLOBAL_V90R3' "$SOURCE_DIR/node_agent/app.py" || die "v9.0-r3 Node viewer timeout runtime reload missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_STABLE_TTL_V90R3' "$SOURCE_DIR/node_agent/app.py" || die "v9.0-r3 Node live-session stable TTL fix missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_ROTATION_UI_V90R3' "$SOURCE_DIR/app/templates/system_branding.html" || die "v9.0-r3 Main log rotation UI missing"
grep -Fq '<option value="2">2 sec</option>' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "v9.0-r3 Main 2-second refresh option missing"
grep -Fq '<option value="2">2 sec</option>' "$SOURCE_DIR/node_agent/app.py" || die "v9.0-r3 Node 2-second refresh option missing"
grep -Fq 'STREAMFORGE_NODE_LAST_ACTIVITY_2S_V91' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v9.2 Node 2-second Last Activity heartbeat cache missing"
grep -Fq 'STREAMFORGE_NODE_LAST_ACTIVITY_2S_V91' "$SOURCE_DIR/node_agent/app.py" || die "v9.2 Node 2-second Last Activity backend hint missing"
grep -Fq 'STREAMFORGE_NODE_V91_ROOT_HEARTBEAT_GUARD' "$SOURCE_DIR/node_agent/app.py" || die "v9.2 Node root Nginx heartbeat update guard missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_ROTATION_INLINE_V91' "$SOURCE_DIR/app/templates/system_branding.html" || die "v9.2 inline Main log rotation setting missing"
# STREAMFORGE_REDIS_SHARED_STATE_PREFLIGHT_V61
grep -Fq 'class RedisStateManager' "$SOURCE_DIR/app/redis_state.py" || die "v6.1 Redis runtime-state manager missing from package"
grep -Fq 'STREAMFORGE_REDIS_SHARED_STATE_V61' "$SOURCE_DIR/app/config.py" || die "v6.1 Redis configuration missing from package"
grep -Fq 'STREAMFORGE_REDIS_CONNECTION_TRACKER_V61' "$SOURCE_DIR/app/main.py" || die "v6.1 Redis connection tracker missing from package"
grep -Fq 'STREAMFORGE_REDIS_VIEWER_TRACKER_V61' "$SOURCE_DIR/app/viewer_tracking.py" || die "v6.1 Redis viewer tracker missing from package"
grep -Fq 'STREAMFORGE_REDIS_PLAYBACK_CACHE_V61' "$SOURCE_DIR/app/playback_keys.py" || die "v6.1 Redis playback cache missing from package"
grep -Fq 'STREAMFORGE_STARTUP_STALE_CHANNEL_RACE_FIX_V61R2' "$SOURCE_DIR/app/ffmpeg.py" || die "v6.1-r2 Main startup stale-channel race fix missing from package"
grep -Fq 'STREAMFORGE_STARTUP_STALE_CHANNEL_RACE_FIX_V61R2' "$SOURCE_DIR/app/node_manager.py" || die "v6.1-r2 Node-controller startup stale-channel race fix missing from package"
grep -Eq '^redis>=5\.0,<7\.0$' "$SOURCE_DIR/requirements.txt" || die "v6.1 Redis Python dependency missing from package"
# STREAMFORGE_PUBLIC_PLANE_PREFLIGHT_V62
grep -Fq 'STREAMFORGE_PUBLIC_PROCESS_ISOLATION_V62' "$SOURCE_DIR/app/main.py" || die "v6.2 Public process-isolation guard missing from package"
grep -Fq 'STREAMFORGE_PUBLIC_SQLITE_POOL_ISOLATION_V62' "$SOURCE_DIR/app/db.py" || die "v6.2 Public SQLite pool isolation missing from package"
grep -Fq 'STREAMFORGE_PUBLIC_HARDWARE_AWARE_WORKERS_V65R1' "$SOURCE_DIR/scripts/streamforge-public-start" || die "v6.5 hardware-aware Public worker launcher missing from package"
grep -Fq 'STREAMFORGE_PROCESS_ROLE=public' "$SOURCE_DIR/deploy/streamforge-public.service" || die "v6.2 Public systemd service role missing from package"
grep -Fq 'STREAMFORGE_PUBLIC_PLANE_NGINX_SPLIT_V62' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v6.2 dynamic Nginx Public split missing from package"
grep -Fq 'streamforge_public_backend' "$SOURCE_DIR/deploy/nginx.conf" || die "v6.2 fresh-install Public upstream missing from package"
grep -Fq 'STREAMFORGE_PUBLIC_WORKERS=auto' "$SOURCE_DIR/.env.example" || die "v6.2 Public worker Auto default missing from package"
grep -Fq 'STREAMFORGE_PUBLIC_HARDWARE_AWARE_WORKERS_V65R1' "$SOURCE_DIR/scripts/streamforge-public-start" || die "v6.5-r1 Main hardware-aware worker policy missing"
# STREAMFORGE_V1051_NODE_STAGGERED_AUTOSTART_GUARD
grep -Fq 'STREAMFORGE_NODE_STAGGERED_CHANNEL_AUTOSTART_V1051' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node staggered channel autostart fix missing"
grep -Fq 'STREAMFORGE_NODE_GLOBAL_AUTO_START_PACER_V1052' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node-wide automatic FFmpeg start pacer missing"
grep -Fq 'self._paced_automatic_start(key, config)' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 automatic recovery pacing call missing"
grep -Fq 'STREAMFORGE_NODE_AUTO_STARTS_PER_SECOND' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node automatic-start rate control missing"
grep -Fq 'key not in self._startup_pending' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node startup watchdog suppression missing"
grep -Fq 'STREAMFORGE_NODE_STATUS_LOCK_MINIMIZE_V1066' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node status lock-minimization fix missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_STATUS_NO_GEO_V1066' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node panel GeoIP hot-path fix missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_SESSION_AUTH_CACHE_V1066' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node panel session auth cache missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_FAILURE_BACKOFF_V1066' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node YouTube resolver backoff missing"
# STREAMFORGE_V1054_MAIN_WEBPLAYER_STALL_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_SINGLE_CHANNEL_PLAYBACK_AUTH_V1054' "$SOURCE_DIR/app/main.py" || die "v10.68 Main single-channel playback auth fast path missing"
grep -Fq 'STREAMFORGE_MAIN_HLS_CONNECTION_GRACE_V1054' "$SOURCE_DIR/app/main.py" || die "v10.68 Main HLS connection-reservation grace missing"
grep -Fq 'STREAMFORGE_MAIN_PLAYLIST_LOGO_ALIAS_BASE_V1056' "$SOURCE_DIR/app/main.py" || die "v10.68 Main playlist logo alias-base fix missing"
grep -Fq 'STREAMFORGE_MAIN_ALIAS_LOGO_CANONICAL_UPSTREAM_V1057' "$SOURCE_DIR/app/main.py" || die "v10.68 Main alias-logo canonical-upstream marker missing"
grep -Fq 'STREAMFORGE_MAIN_ALIAS_LOGO_CANONICAL_UPSTREAM_V1057' "$SOURCE_DIR/deploy/nginx.conf" || die "v10.68 fresh Main alias-logo URI normalization missing"
grep -Fq 'STREAMFORGE_MAIN_ALIAS_LOGO_CANONICAL_UPSTREAM_V1057' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v10.68 dynamic Main alias-logo URI normalization missing"
grep -Fq 'rewrite ^/[A-Za-z0-9_-]+(/channel-logos/[A-Za-z0-9._-]+)$ $1 break;' "$SOURCE_DIR/deploy/nginx.conf" || die "v10.68 fresh Main alias-logo rewrite rule missing"
grep -Fq 'rewrite ^/[A-Za-z0-9_-]+(/channel-logos/[A-Za-z0-9._-]+)$ $1 break;' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v10.68 dynamic Main alias-logo rewrite rule missing"
grep -Fq 'STREAMFORGE_MAIN_SHARED_ASSETS_PUBLIC_PLANE_V1055' "$SOURCE_DIR/deploy/nginx.conf" || die "v10.68 fresh Main shared-asset Public-plane route missing"
grep -Fq 'STREAMFORGE_MAIN_SHARED_ASSETS_PUBLIC_PLANE_V1055' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v10.68 dynamic Main shared-asset Public-plane route missing"
grep -Fq 'channel-logos(?:/|$)|node-logos(?:/|$)|branding-assets(?:/|$)' "$SOURCE_DIR/deploy/nginx.conf" || die "v10.68 fresh Main logo route regex missing"
! grep -Fq 'next((item for item in online_user_channels(user) if item.slug == slug), None)' "$SOURCE_DIR/app/main.py" || die "v10.68 Main playback hot path still scans the full online catalogue"
# Preserve the v10.52 channel-switch handover invariant. Child HLS requests
# refresh an existing SID but cannot reopen a genuinely ended/switched slot.
grep -Fq 'trailing HLS segments cannot reopen slots' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node channel-switch handover fix missing"
! grep -Fq 'STREAMFORGE_MAIN_CHILD_SESSION_SELF_HEAL_V1053' "$SOURCE_DIR/app/main.py" || die "v10.68 contains unsafe Main child-session slot reopen logic"
! grep -Fq 'STREAMFORGE_NODE_CHILD_SESSION_SELF_HEAL_V1053' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 contains unsafe Node child-session slot reopen logic"
# STREAMFORGE_V1050_MAIN_PUBLIC_NO_REQUEST_RECYCLE_GUARD
grep -Fq 'STREAMFORGE_MAIN_PUBLIC_NO_REQUEST_RECYCLE_V1050' "$SOURCE_DIR/scripts/streamforge-public-start" || die "v10.68 Main Public no-request-recycle fix missing"
! grep -Fq "'--max-requests', '20000'" "$SOURCE_DIR/scripts/streamforge-public-start" || die "v10.68 Main Public still has 20k request recycling"
grep -Fq 'STREAMFORGE_PUBLIC_MAX_WORKERS=auto' "$SOURCE_DIR/.env.example" || die "v6.5-r1 Main auto worker ceiling missing"
grep -Fq 'STREAMFORGE_NODE_HARDWARE_AWARE_WORKERS_V65R1' "$SOURCE_DIR/node_agent/app.py" || die "v6.5-r1 Node hardware-aware worker policy missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_WORKERS_MAX=auto' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v6.5-r1 Node auto worker ceiling missing"
grep -Fq 'STREAMFORGE_NODE_CONTROL_BACKEND_PORT=8810' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v6.5-r5 Node control backend setting missing"
grep -Fq 'STREAMFORGE_NODE_HTTP_SHARED_PUBLIC_POOL_V65R5' "$SOURCE_DIR/node_agent/app.py" || die "v6.5-r5 shared HTTP public pool logic missing"
grep -Fq 'STREAMFORGE_NODE_HTTP_FRONT_V65R5' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v6.5-r5 Node HTTP Nginx front missing"
grep -Fq 'STREAMFORGE_MEDIA_XACCEL_FASTPATH_V63' "$SOURCE_DIR/app/main.py" || die "v6.3 Main Nginx media X-Accel fast path missing"
grep -Fq 'STREAMFORGE_REMOTE_SIGNED_MEDIA_FASTPATH_V63' "$SOURCE_DIR/app/main.py" || die "v6.3 Remote signed media path missing"
grep -Fq 'STREAMFORGE_PUBLIC_CATALOG_STOPPED_EXCLUSION_V63R2' "$SOURCE_DIR/app/main.py" || die "v6.3-r2 offline playlist exclusion fix missing"
grep -Fq 'STREAMFORGE_PUBLIC_STRICT_LOCAL_UP_V63R3' "$SOURCE_DIR/app/main.py" || die "v6.3-r3 strict Local process-alive playlist guard missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_CLEAN_CANONICAL_ROOT_V63R4' "$SOURCE_DIR/app/main.py" || die "v6.3-r4 clean WebPlayer canonical root missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_CLEAN_CANONICAL_ROOT_V63R5' "$SOURCE_DIR/node_agent/app.py" || die "v6.3-r5 Node clean WebPlayer canonical root missing"
grep -Fq 'STREAMFORGE_STREAM_INFO_ON_DEMAND_REPLICAS_V63R6' "$SOURCE_DIR/app/main.py" || die "v6.3-r6 on-demand Stream Info replicas missing"
grep -Fq 'STREAMFORGE_STREAM_INFO_ON_DEMAND_REPLICAS_V63R6' "$SOURCE_DIR/app/static/app.js" || die "v6.3-r6 Stream Info live-check UI missing"
grep -Fq 'STREAMFORGE_FIREFOX_SIDEBAR_SCROLL_V63R6' "$SOURCE_DIR/app/static/style.css" || die "v6.3-r6 Firefox sidebar scroll fix missing"
grep -Fq 'panelUrl(endpoint)' "$SOURCE_DIR/app/static/app.js" || die "v6.3-r6 panel-prefix probe routing fix missing"
grep -Fq 'STREAMFORGE_REPLICA_NODE_CHANNEL_UPTIME_V63R7' "$SOURCE_DIR/app/main.py" || die "v6.3-r7 Main replica uptime payload missing"
grep -Fq 'STREAMFORGE_REPLICA_NODE_CHANNEL_UPTIME_V63R7' "$SOURCE_DIR/app/static/app.js" || die "v6.3-r7 replica uptime UI missing"
grep -Fq 'STREAMFORGE_REPLICA_NODE_CHANNEL_UPTIME_V63R7' "$SOURCE_DIR/node_agent/app.py" || die "v6.3-r7 Node host uptime status payload missing"
grep -Fq 'data-replica-node-uptime' "$SOURCE_DIR/app/templates/channel_info.html" || die "v6.3-r7 Node uptime card missing"
grep -Fq 'style.css?v={{ app_version }}' "$SOURCE_DIR/app/templates/base.html" || die "Versioned stylesheet cache-bust missing from package"
grep -Fq 'STREAMFORGE_UI_DURATION_DAYS_V63R8' "$SOURCE_DIR/app/static/app.js" || die "v6.3-r8 day-aware uptime formatter missing"
grep -Fq 'STREAMFORGE_TOTAL_OUTPUT_MIBIT_V63R8' "$SOURCE_DIR/app/static/app.js" || die "v6.3-r8 total output formatter missing"
grep -Fq 'STREAMFORGE_FIREFOX_COLLAPSED_NAV_CENTER_V63R8' "$SOURCE_DIR/app/static/style.css" || die "v6.3-r8 Firefox collapsed nav centering missing"
grep -Fq 'STREAMFORGE_FIREFOX_COLLAPSED_TOP_NO_SHRINK_V64R1' "$SOURCE_DIR/app/static/style.css" || die "v6.4-r1 Firefox collapsed sidebar top-block fix missing"
grep -Fq 'app.js?v={{ app_version }}' "$SOURCE_DIR/app/templates/base.html" || die "Versioned JavaScript cache-bust missing from package"
grep -Fq 'STREAMFORGE_LOCAL_HLS_CLEAN_ON_DOWN_V63R3' "$SOURCE_DIR/app/ffmpeg.py" || die "v6.3-r3 Main stale HLS cleanup missing"
grep -Fq 'STREAMFORGE_NODE_HLS_CLEAN_ON_DOWN_V63R3' "$SOURCE_DIR/node_agent/app.py" || die "v6.3-r3 Node stale HLS cleanup missing"
grep -Fq 'STREAMFORGE_NODE_SIGNED_MEDIA_FASTPATH_V63' "$SOURCE_DIR/node_agent/app.py" || die "v6.3 Node signed media endpoint missing"
grep -Fq 'STREAMFORGE_MAIN_HLS_XACCEL_LOCATION_V63' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v6.3 dynamic Main HLS internal Nginx location missing"
grep -Fq 'STREAMFORGE_MAIN_HLS_XACCEL_LOCATION_V63' "$SOURCE_DIR/deploy/nginx.conf" || die "v6.3 fresh Main HLS internal Nginx location missing"
grep -Fq 'STREAMFORGE_NODE_HLS_XACCEL_LOCATION_V63' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v6.3 Node managed-TLS HLS internal Nginx location missing"
grep -Fq 'def _panel_session_cookie_name()' "$SOURCE_DIR/node_agent/app.py" || die "v6.0-r1 Node-scoped session cookie helper missing"
grep -Fq '_set_panel_route_cookie(response, request, "/panel")' "$SOURCE_DIR/node_agent/app.py" || die "v6.0-r1 login route-cookie reset missing"
grep -Fq 'secure=_public_request_is_https(request)' "$SOURCE_DIR/node_agent/app.py" || die "v6.0-r1 HTTPS cookie hardening missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_PREFIXED_LIVE_STATUS_V50' "$SOURCE_DIR/app/templates/node_update.html" || die "Node update prefixed live polling missing from package"
grep -Fq 'STREAMFORGE_NODE_UPDATE_OPTIONS_VISIBLE_V50' "$SOURCE_DIR/app/static/style.css" || die "Node update visible-controls fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_NATIVE_NAV_FEEDBACK_V49' "$SOURCE_DIR/app/static/style.css" || die "Main navigation click feedback missing from package"
grep -Fq 'STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_HEAD_V49' "$SOURCE_DIR/app/templates/base.html" || die "Early hidden-route address-bar guard missing from package"
grep -Fq 'STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_V49' "$SOURCE_DIR/app/main.py" || die "Server-side hidden-route dispatch missing from package"
grep -Fq 'STREAMFORGE_PANEL_ROUTE_COOKIE = "streamforge_panel_route"' "$SOURCE_DIR/app/main.py" || die "Hidden-route cookie contract missing from package"
grep -Fq 'STREAMFORGE_MAIN_REQUEST_ADMIN_CACHE_V48' "$SOURCE_DIR/app/auth.py" || die "Per-request admin cache missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_URL_BASE_ONCE_V48' "$SOURCE_DIR/app/main.py" || die "Channel public-base reuse missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_LIST_REUSE_V48' "$SOURCE_DIR/app/main.py" || die "Channel list reuse optimization missing from package"
grep -Fq 'STREAMFORGE_MAIN_READONLY_PAGE_INIT_V48' "$SOURCE_DIR/app/main.py" || die "Read-only page initialization missing from package"
# v6.5-r7: the legacy Nodes first-paint marker was superseded by
# the heartbeat live-summary Nodes card path verified above; do not reject current packages
# for that removed historical marker.
! grep -Fq 'STREAMFORGE_MAIN_ZERO_FLASH_PANEL_NAV_V3060' "$SOURCE_DIR/app/templates/base.html" || die "Legacy fetch navigation still present in package"
grep -Fq 'STREAMFORGE_MAIN_NODE_LOCAL_CONTROL_SCOPE_V33' "$SOURCE_DIR/app/node_manager.py" || die "Main-local channel control scope missing from package"
grep -Fq 'STREAMFORGE_MAIN_STATUS_LOCAL_ONLY_V33' "$SOURCE_DIR/app/main.py" || die "Main-local status/uptime scope missing from package"
grep -Fq 'STREAMFORGE_MAIN_CONFIG_SAVE_NO_PROCESS_RESTART_V33' "$SOURCE_DIR/app/main.py" || die "Main configuration save no-restart policy missing from package"
grep -Fq 'STREAMFORGE_MAIN_NODE_RECONCILE_CONFIG_ONLY_V33' "$SOURCE_DIR/app/node_manager.py" || die "Main-to-Node config-only reconcile missing from package"
grep -Fq 'STREAMFORGE_NODE_CONFIG_SYNC_NO_RESTART_V33' "$SOURCE_DIR/node_agent/app.py" || die "Node no-restart config sync missing from package"
grep -Fq 'STREAMFORGE_NODE_DESIRED_WATCHDOG_V33' "$SOURCE_DIR/node_agent/app.py" || die "Node desired-running watchdog missing from package"
grep -Fq 'STREAMFORGE_MAIN_STATUS_HOT_CACHE_V34' "$SOURCE_DIR/app/main.py" || die "Main status hot-cache fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_STATUS_EAGER_RUNTIME_V34' "$SOURCE_DIR/app/main.py" || die "Main status eager-runtime fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_STATUS_NO_GEO_NETWORK_V34' "$SOURCE_DIR/app/main.py" || die "Main status GeoIP isolation missing from package"
grep -Fq 'STREAMFORGE_MAIN_RUNTIME_SNAPSHOT_NO_DB_V34' "$SOURCE_DIR/app/ffmpeg.py" || die "Main runtime snapshot no-DB fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_RUNTIME_DB_WRITE_THROTTLE_V34' "$SOURCE_DIR/app/ffmpeg.py" || die "Main FFmpeg DB write throttle missing from package"
grep -Fq 'STREAMFORGE_SQLITE_CONNECTION_LIGHTWEIGHT_V34' "$SOURCE_DIR/app/db.py" || die "SQLite lightweight connection pragmas missing from package"
grep -Fq 'STREAMFORGE_SQLITE_WAL_ONCE_V34' "$SOURCE_DIR/app/db.py" || die "SQLite WAL-once startup fix missing from package"
grep -Fq 'STREAMFORGE_STATUS_LOG_INDEX_V34' "$SOURCE_DIR/app/db.py" || die "Main status log index missing from package"
grep -Fq 'STREAMFORGE_MAIN_POLL_BACKPRESSURE_V34' "$SOURCE_DIR/app/static/app.js" || die "Main browser poll backpressure fix missing from package"
grep -Fq 'STREAMFORGE_CANONICAL_PROTOCOL_REDIRECT_V35' "$SOURCE_DIR/app/main.py" || die "Main canonical protocol redirect missing from package"
grep -Fq 'main-canonical-protocol-redirect' "$SOURCE_DIR/app/main.py" || die "Main protocol redirect response missing from package"
grep -Fq 'STREAMFORGE_CHAINED_PROXY_PROTOCOL_V35' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Main chained-proxy protocol preservation missing from package"
grep -Fq 'STREAMFORGE_NATIVE_TLS_TERMINATION_V37' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Managed Main TLS termination missing from package"
grep -Fq 'STREAMFORGE_AUTO_LETSENCRYPT_V37' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Managed Lets Encrypt provisioning missing from package"
grep -Fq 'X-Forwarded-Proto $streamforge_forwarded_proto' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Native/chained protocol forwarding missing from package"
grep -Fq 'STREAMFORGE_MANAGED_ACME_WEBROOT_V37' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Managed ACME challenge route missing from package"
grep -Fq 'STREAMFORGE_NATIVE_TLS_HEADERS_V37' "$SOURCE_DIR/deploy/nginx.conf" || die "Fresh Nginx native TLS forwarding headers missing from package"
grep -Fq 'STREAMFORGE_CERTBOT_SANDBOX_WRITES_V38' "$SOURCE_DIR/deploy/streamforge-main-access.service" || die "Certbot systemd sandbox write policy missing from package"
grep -Fq 'TimeoutStartSec=300' "$SOURCE_DIR/deploy/streamforge-main-access.service" || die "TLS provisioning service timeout is stale in package"
grep -Fq -- '-/etc/letsencrypt' "$SOURCE_DIR/deploy/streamforge-main-access.service" || die "Lets Encrypt config directory is not writable in package sandbox"
grep -Fq 'STREAMFORGE_CERTBOT_RETRY_AFTER_SANDBOX_FIX_V38' "$SOURCE_UPDATER" || die "Certbot immediate retry repair missing from package"
grep -Fq '/var/lib/letsencrypt /var/log/letsencrypt' "$SOURCE_DIR/scripts/install.sh" || die "Fresh-install Certbot work/log directories missing from package"
[[ -x "$SOURCE_DIR/scripts/streamforge_tls_renew_hook.sh" ]] || die "TLS renewal reload hook missing from package"
grep -Fq 'STREAMFORGE_CHAINED_PROXY_PROTOCOL_V35' "$SOURCE_DIR/deploy/nginx.conf" || die "Fresh Main Nginx chained-proxy protocol preservation missing from package"
grep -Fq 'STREAMFORGE_NODE_CANONICAL_PROTOCOL_REDIRECT_V35' "$SOURCE_DIR/node_agent/app.py" || die "Node canonical protocol redirect missing from package"
grep -Fq 'node-canonical-protocol-redirect' "$SOURCE_DIR/node_agent/app.py" || die "Node protocol redirect response missing from package"
grep -Fq 'STREAMFORGE_NODE_FORWARDED_AUTHORITY_V35' "$SOURCE_DIR/node_agent/app.py" || die "Node forwarded authority support missing from package"
grep -Fq 'STREAMFORGE_NODE_MANAGED_TLS_V39' "$SOURCE_DIR/node_agent/app.py" || die "Node managed TLS state integration missing from package"
grep -Fq 'STREAMFORGE_NODE_TLS_ASYNC_ACCESS_SYNC_V39' "$SOURCE_DIR/node_agent/app.py" || die "Node HTTPS access sync fallback missing from package"
grep -Fq 'STREAMFORGE_NODE_TLS_PUBLIC_GATEWAY_SPLIT_V39' "$SOURCE_DIR/node_agent/app.py" || die "Node HTTPS/plain gateway separation missing from package"
grep -Fq 'STREAMFORGE_NODE_ACME_HTTP01_V39' "$SOURCE_DIR/node_agent/app.py" || die "Node ACME HTTP-01 route missing from package"
grep -Fq 'STREAMFORGE_NODE_ACME_MIDDLEWARE_BYPASS_V40' "$SOURCE_DIR/node_agent/app.py" || die "Node ACME FastAPI middleware bypass missing from package"
grep -Fq 'STREAMFORGE_NODE_DNS01_CNAME_PREFLIGHT_V41' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "Node delegated DNS-01 CNAME preflight missing from package"
grep -Fq 'STREAMFORGE_NODE_ACME_RATE_LIMIT_BACKOFF_V40' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "Node ACME retry backoff missing from package"
grep -Fq 'STREAMFORGE_DELEGATED_DNS01_V41' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Main delegated DNS-01 flow missing from package"
grep -Fq 'STREAMFORGE_NODE_DELEGATED_DNS01_V41' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "Node delegated DNS-01 flow missing from package"
[[ -x "$SOURCE_DIR/scripts/acme_dns_hook.py" ]] || die "Main DNS-01 Certbot hook missing from package"
[[ -x "$SOURCE_DIR/node_agent/acme_dns_hook.py" ]] || die "Node DNS-01 Certbot hook missing from package"
grep -Fq 'STREAMFORGE_DELEGATED_DNS01_UI_V41' "$SOURCE_DIR/app/templates/node_form.html" || die "DNS-01 CNAME UI missing from package"
grep -Fq 'STREAMFORGE_DNS01_CARD_ACTIONS_V42' "$SOURCE_DIR/app/templates/node_form.html" || die "v4.2 DNS-01 card actions UI missing from package"
grep -Fq 'STREAMFORGE_DNS01_CARD_ACTIONS_JS_V42' "$SOURCE_DIR/app/templates/node_form.html" || die "v4.2 DNS-01 card JavaScript missing from package"
grep -Fq 'STREAMFORGE_DNS01_TEST_API_V42' "$SOURCE_DIR/app/main.py" || die "v4.2 DNS-01 test API missing from package"
grep -Fq 'STREAMFORGE_DNS01_RETRY_API_V42' "$SOURCE_DIR/app/main.py" || die "v4.2 DNS-01 retry API missing from package"
grep -Fq 'STREAMFORGE_NODE_TLS_RECONCILE_CLIENT_V42' "$SOURCE_DIR/app/node_manager.py" || die "v4.2 Node TLS reconcile client missing from package"
grep -Fq 'STREAMFORGE_NODE_TLS_RECONCILE_API_V42' "$SOURCE_DIR/node_agent/app.py" || die "v4.2 Node TLS reconcile API missing from package"
grep -Fq 'STREAMFORGE_HTTP_CANONICAL_REDIRECT_CERT_V42' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v4.2 Main HTTP canonical redirect certificate policy missing"
grep -Fq 'STREAMFORGE_NODE_HTTP_CANONICAL_REDIRECT_CERT_V42' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v4.2 Node HTTP canonical redirect certificate policy missing"
grep -Fq 'STREAMFORGE_DNS01_REGISTRATION_METADATA_PERSIST_V43' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v4.3 Main DNS-01 registration metadata persistence missing"
grep -Fq 'STREAMFORGE_NODE_DNS01_REGISTRATION_METADATA_PERSIST_V43' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v4.3 Node DNS-01 registration metadata persistence missing"
grep -Fq 'STREAMFORGE_DNS01_REGISTRATION_METADATA_UI_V43' "$SOURCE_DIR/app/templates/node_form.html" || die "v4.3 DNS-01 registration metadata UI missing"
grep -Fq 'STREAMFORGE_UNKNOWN_TLS_SNI_REJECT_V44' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v4.4 Main unknown TLS SNI rejection missing"
grep -Fq 'ssl_reject_handshake on;' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v4.4 Main TLS reject directive missing"
grep -Fq 'STREAMFORGE_NODE_UNKNOWN_TLS_SNI_REJECT_V44' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v4.4 Node unknown TLS SNI rejection missing"
grep -Fq 'ssl_reject_handshake on;' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v4.4 Node TLS reject directive missing"
grep -Fq 'STREAMFORGE_NODE_CARD_STRICT_DELIVERY_COUNTS_V45' "$SOURCE_DIR/app/main.py" || die "v5.4 Main strict Node-card delivery counts missing"
grep -Fq 'STREAMFORGE_NODE_CARD_STRICT_DELIVERY_COUNTS_V45' "$SOURCE_DIR/node_agent/app.py" || die "v5.4 Node strict delivery counts missing"
! grep -Fq 'data-node-waiting' "$SOURCE_DIR/app/templates/nodes.html" || die "v5.4 Waiting metric must be hidden from Node cards"
grep -Fq 'STREAMFORGE_NODE_CARD_WAITING_AS_DOWN_V46' "$SOURCE_DIR/app/main.py" || die "v5.4 Waiting-as-Down summary backend missing"
grep -Fq 'STREAMFORGE_NODE_CARD_WAITING_AS_DOWN_V46' "$SOURCE_DIR/app/static/app.js" || die "v5.4 Waiting-as-Down summary refresh missing"
grep -Fq 'STREAMFORGE_NODE_CARD_WAITING_AS_DOWN_V46' "$SOURCE_DIR/node_agent/app.py" || die "v5.4 Node detailed-count compatibility marker missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_EMPTY_FILTER_SAFE_V47' "$SOURCE_DIR/app/main.py" || die "v5.4 Live Sessions blank-filter backend fix missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_AUTO_FILTER_V47' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "v5.4 Live Sessions auto-filter UI missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_AUTO_FILTER_JS_V47' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "v5.4 Live Sessions auto-filter JavaScript missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_DELIVERY_FILTER_V92' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "v9.2 Main Live Sessions Delivery filter UI missing"
grep -Fq 'selected_delivery' "$SOURCE_DIR/app/main.py" || die "v9.2 Main Live Sessions Delivery filter backend missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_PAGINATION_SORT_V93' "$SOURCE_DIR/app/main.py" || die "v9.3 Main log pagination/sort backend missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_PAGINATION_SORT_V93' "$SOURCE_DIR/app/templates/logs.html" || die "v9.3 Main log pagination/sort UI missing"
grep -Fq 'STREAMFORGE_NODE_LOG_PAGINATION_SORT_LEVEL_V93' "$SOURCE_DIR/node_agent/app.py" || die "v9.3 Node log pagination/sort/level filter missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_USER_COLUMN_V99R17' "$SOURCE_DIR/app/main.py" || die "v9.9 r17 Main Client log User extraction missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_USER_NO_DETAILS_V99R17' "$SOURCE_DIR/app/templates/logs.html" || die "v9.9 r17 Main Client log User/no-Details UI missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_UNAUTHORIZED_ATTEMPTS_V99R17' "$SOURCE_DIR/app/main.py" || die "v9.9 r17 Main unauthorized Client logging missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_USER_NO_DETAILS_V99R17' "$SOURCE_DIR/node_agent/app.py" || die "v9.9 r17 Node Client log User/no-Details UI missing"
grep -Fq 'STREAMFORGE_DASHBOARD_DISK_USAGE_V96' "$SOURCE_DIR/app/system_metrics.py" || die "v9.6 Main Dashboard disk sampler missing"
grep -Fq 'data-disk-percent' "$SOURCE_DIR/app/templates/dashboard.html" || die "v9.6 Main Dashboard disk card missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_DISK_USAGE_V96' "$SOURCE_DIR/app/static/app.js" || die "v9.6 Main Dashboard disk live refresh missing"
grep -Fq 'STREAMFORGE_NODE_DASHBOARD_DISK_USAGE_V96' "$SOURCE_DIR/node_agent/app.py" || die "v9.6 Node Dashboard disk metrics missing"
grep -Fq 'STREAMFORGE_MAIN_CATALOG_IP_WHITELIST_V97' "$SOURCE_DIR/app/main.py" || die "v9.8 Main catalog IP-whitelist filtering missing"
grep -Fq 'STREAMFORGE_CATALOG_IP_WHITELIST_V97' "$SOURCE_DIR/app/load_balancer.py" || die "v9.8 catalog whitelist matcher missing"
grep -Fq 'STREAMFORGE_PLAYBACK_IP_WHITELIST_ELIGIBLE_POOL_V97' "$SOURCE_DIR/app/load_balancer.py" || die "v9.8 load-balanced whitelist routing missing"
grep -Fq 'STREAMFORGE_ACCESS_POLICY_EQUAL_BOXES_V99R2' "$SOURCE_DIR/app/static/style.css" || die "v9.9 r2 equal access-policy box layout missing"
grep -Fq 'STREAMFORGE_DNS01_LAYOUT_SPACING_V99R2' "$SOURCE_DIR/app/templates/node_form.html" || die "v9.9 r2 DNS-01 layout spacing missing"
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_ROOT_IP_WHITELIST_V97R2' "$SOURCE_DIR/node_agent/app.py" || die "v9.8 r2 Node Web Player root whitelist enforcement missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_ACCESS_RUNTIME_RELOAD_V97R2' "$SOURCE_DIR/node_agent/app.py" || die "v9.8 r2 Node public access runtime reload missing"
grep -Fq 'STREAMFORGE_NODE_DASHBOARD_ACTIVE_NO_FLASH_V94' "$SOURCE_DIR/node_agent/app.py" || die "v9.4 Node Dashboard active-channel flash fix missing"
grep -Fq 'STREAMFORGE_SMB_BACKUP_MODIFIED_TIME_V94' "$SOURCE_DIR/app/backup_manager.py" || die "v9.4 SMB backup modified-time parser missing"
! grep -Fq 'STREAMFORGE_INDEPENDENT_PLAYBACK_SHARE_ROUTING_V95' "$SOURCE_DIR/node_agent/app.py" || die "v9.5 Independent playback sharing code still present in v9.6 stable package"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_HIDDEN_NATIVE_FILTER_V49' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "v5.4 hidden-route Live Sessions filter reload missing"
! grep -Fq '>Apply filter</button>' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "v5.4 Main Live Sessions still exposes Apply filter"
[[ -f "$SOURCE_DIR/deploy/streamforge-main-tls.timer" ]] || die "Main DNS-01 retry timer missing from package"
grep -Fq 'STREAMFORGE_NODE_LOCAL_TLS_PROXY_TRUST_V39' "$SOURCE_DIR/node_agent/app.py" || die "Node local TLS proxy trust boundary missing from package"
[[ -x "$SOURCE_DIR/node_agent/apply_node_tls.py" ]] || die "Node managed TLS reconciler missing from package"
[[ -f "$SOURCE_DIR/node_agent/deploy/streamforge-node-tls.service" ]] || die "Node TLS systemd service missing from package"
grep -Fq '/usr/local/libexec/streamforge-node/apply_node_tls.py' "$SOURCE_DIR/node_agent/deploy/streamforge-node-tls.service" || die "Node root-owned TLS helper service path missing from package"
grep -Fq 'EnvironmentFile=-/etc/streamforge-node.env' "$SOURCE_DIR/node_agent/deploy/streamforge-node-tls.service" || die "Node delegated DNS API environment is not wired into TLS service"
grep -Fq '/usr/local/libexec/streamforge/acme_dns_hook.py' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Main root-owned DNS-01 hook path missing from package"
grep -Fq '/usr/local/libexec/streamforge-node/acme_dns_hook.py' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "Node root-owned DNS-01 hook path missing from package"
[[ -f "$SOURCE_DIR/node_agent/deploy/streamforge-node-tls.path" ]] || die "Node TLS systemd path missing from package"
[[ -f "$SOURCE_DIR/node_agent/deploy/streamforge-node-tls.timer" ]] || die "Node TLS retry timer missing from package"
grep -Fq 'STREAMFORGE_NODE_NATIVE_TLS_DEPENDENCIES_V39' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "Node TLS dependency installer missing from package"
grep -Fq 'STREAMFORGE_NODE_NATIVE_TLS_TRIGGER_V39' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "Node TLS reconciliation trigger missing from package"
grep -Fq 'STREAMFORGE_NODE_TLS_RECONCILE_TRIGGER_RACE_FIX_V78' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v7.9 Node TLS trigger race fix missing from package"
grep -Fq 'STREAMFORGE_NODE_ISOLATED_CERTBOT_INSTALL_V1030' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v10.30 isolated Node Certbot installer missing from package"
grep -Fq 'STREAMFORGE_NODE_ISOLATED_CERTBOT_V1030' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v10.30 Node TLS helper does not prefer isolated Certbot"
grep -Fq 'STREAMFORGE_MAIN_ISOLATED_CERTBOT_V1030' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v10.30 Main TLS helper does not prefer isolated Certbot"
grep -Fq 'STREAMFORGE_NODE_ISOLATED_CERTBOT_RENEWAL_V1030' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v10.30 Node isolated Certbot renewal path missing"
grep -Fq 'STREAMFORGE_MAIN_ISOLATED_CERTBOT_RENEWAL_V1030' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v10.30 Main isolated Certbot renewal path missing"
grep -Fq 'STREAMFORGE_MAIN_ISOLATED_CERTBOT_INSTALL_V1030' "$SOURCE_DIR/scripts/install.sh" || die "v10.30 fresh Main isolated Certbot installer missing"
grep -Fq 'STREAMFORGE_NODE_NGINX_UPDATE_UPTIME_V1032' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v10.34 Node updater still risks stopping the Nginx HTTP frontend"
grep -Fq 'STREAMFORGE_NODE_HTTP_FRONT_READY_BEFORE_TLS_V1032' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v10.34 Node HTTP-before-TLS readiness guard missing"
grep -Fq 'STREAMFORGE_NODE_DISABLE_DISTRO_CERTBOT_TIMER_V1032' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v10.34 distro Certbot timer isolation guard missing"
grep -Fq 'STREAMFORGE_NODE_HTTP_FRONT_BOOTSTRAP_V1032' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v10.34 Node HTTP frontend bootstrap helper missing"
grep -Fq 'STREAMFORGE_NODE_HTTP_FRONT_BOOTSTRAP_CLI_V1032' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v10.34 Node HTTP frontend bootstrap CLI missing"
grep -Fq 'STREAMFORGE_NGINX_SSL_REJECT_COMPAT_V1033' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v10.34 Node legacy-Nginx TLS compatibility missing"
grep -Fq 'STREAMFORGE_MAIN_NGINX_SSL_REJECT_COMPAT_V1033' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v10.34 Main legacy-Nginx TLS compatibility missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_ATOMIC_UNIQUE_TEMP_V1033' "$SOURCE_DIR/node_agent/app.py" || die "v10.34 Node access.json multi-worker atomic-write fix missing"
grep -Fq 'STREAMFORGE_NODE_INTERNAL_MEDIA_AUTH_CANONICAL_BYPASS_V1034' "$SOURCE_DIR/node_agent/app.py" || die "v10.34 Node internal media-auth canonical redirect bypass missing"
grep -Fq 'STREAMFORGE_NODE_DEPLOYED_VERSION_VERIFY_V1034' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v10.34 Node deployed-version verification missing"
# STREAMFORGE_V1035_LOG_SEARCH_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_LOG_SEARCH_V1035' "$SOURCE_DIR/app/main.py" || die "v10.68 Main log search backend missing"
grep -Fq 'STREAMFORGE_NODE_LOG_SEARCH_CLIENT_V1035' "$SOURCE_DIR/app/node_manager.py" || die "v10.68 Main-to-Node log search client missing"
grep -Fq 'STREAMFORGE_NODE_LOG_SEARCH_V1035' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node log search backend missing"
grep -Fq 'STREAMFORGE_LOG_SEARCH_UI_V1035' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Logs search UI missing"
# STREAMFORGE_V1036_RELAY_ERROR_ONLY_LOG_GUARDS:
grep -Fq 'STREAMFORGE_RELAY_ERROR_ONLY_ACCESS_LOG_V1036' "$SOURCE_DIR/deploy/nginx.conf" || die "v10.68 fresh relay access-log filter missing"
grep -Fq 'map $status $streamforge_relay_access_loggable' "$SOURCE_DIR/deploy/nginx.conf" || die "v10.68 fresh relay status map missing"
grep -Fq 'access_log /var/log/nginx/access.log combined if=$streamforge_relay_access_loggable;' "$SOURCE_DIR/deploy/nginx.conf" || die "v10.68 fresh relay conditional access log missing"
grep -Fq 'STREAMFORGE_RELAY_ERROR_ONLY_ACCESS_LOG_V1036' "$SOURCE_DIR/scripts/apply_main_access.py" || die "v10.68 dynamic relay access-log filter missing"
# STREAMFORGE_V1037_LOG_AUTO_SEARCH_RELAY_BACKOFF_GUARDS:
grep -Fq 'STREAMFORGE_LOG_AUTO_SEARCH_UI_V1037' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Main automatic Logs search UI missing"
grep -Fq 'STREAMFORGE_LOG_AUTO_SEARCH_JS_V1037' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Main automatic Logs search behavior missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_SEARCH_V1037' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 standalone Node log search backend missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_SEARCH_UI_V1037' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 standalone Node log search UI missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_AUTO_SEARCH_JS_V1037' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 standalone Node automatic log search missing"
grep -Fq 'STREAMFORGE_NODE_LOCAL_RELAY_404_BACKOFF_V1037' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Local Relay 404 retry backoff missing"
grep -Fq 'STREAMFORGE_NODE_RESTART_BACKOFF_STABLE_RESET_V1037' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 stable retry-backoff reset missing"
# STREAMFORGE_V1039_LIVE_LOG_SEARCH_GUARDS:
grep -Fq 'STREAMFORGE_LOG_LIVE_AJAX_SEARCH_UI_V1038' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Main live AJAX Logs UI missing"
grep -Fq 'STREAMFORGE_LOG_LIVE_AJAX_SEARCH_JS_V1038' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Main live AJAX Logs behavior missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_LIVE_AJAX_V1038' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node live AJAX Logs backend marker missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_LIVE_AJAX_JS_V1038' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node live AJAX Logs behavior missing"
if grep -Fq '420' "$SOURCE_DIR/app/templates/logs.html"; then die "v10.68 Main Logs still contains 420ms debounce"; fi
if grep -E 'timer=setTimeout.*420' "$SOURCE_DIR/node_agent/app.py" >/dev/null; then die "v10.68 Node Logs still contains 420ms debounce"; fi
# STREAMFORGE_V1039_PRIVATE_LOG_ADDRESSBAR_GUARDS:
grep -Fq 'STREAMFORGE_LOG_PRIVATE_ADDRESSBAR_V1039' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Main clean log address-bar UI missing"
grep -Fq 'STREAMFORGE_LOG_PRIVATE_ADDRESSBAR_JS_V1039' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Main clean log address-bar behavior missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_PRIVATE_ADDRESSBAR_V1039' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node clean log address-bar UI missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_PRIVATE_ADDRESSBAR_JS_V1039' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node clean log address-bar behavior missing"
if grep -Fq "history.replaceState(null, '', response.url || url)" "$SOURCE_DIR/app/templates/logs.html"; then die "v10.68 Main Logs still exposes AJAX query state in address bar"; fi
if grep -Fq "history.replaceState(null,'',r.url||url)" "$SOURCE_DIR/node_agent/app.py"; then die "v10.68 Node Logs still exposes AJAX query state in address bar"; fi
# STREAMFORGE_V1040_FIXED_CANONICAL_LOG_ADDRESSBAR_GUARDS:
grep -Fq 'STREAMFORGE_LOG_FIXED_CANONICAL_ADDRESSBAR_V1040' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Main fixed canonical Logs address-bar UI missing"
grep -Fq 'STREAMFORGE_LOG_FIXED_CANONICAL_ADDRESSBAR_JS_V1040' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Main fixed canonical Logs address-bar behavior missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_FIXED_CANONICAL_ADDRESSBAR_V1040' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node fixed canonical Logs address-bar UI missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_FIXED_CANONICAL_ADDRESSBAR_JS_V1040' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node fixed canonical Logs address-bar behavior missing"
if grep -Fq "const cleanVisibleUrl" "$SOURCE_DIR/app/templates/logs.html"; then die "v10.68 Main Logs still has a route-writing address-bar helper"; fi
if grep -Fq "const clean=()=>{const type=String(f.querySelector" "$SOURCE_DIR/node_agent/app.py"; then die "v10.68 Node Logs still has a route-writing address-bar helper"; fi
# STREAMFORGE_V1041_NODE_XTREAM_SMARTERS_COMPAT_GUARDS:
grep -Fq 'STREAMFORGE_NODE_XTREAM_EMPTY_UNSUPPORTED_CATALOG_V1041' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node Xtream empty VOD/Series compatibility fix missing"
! grep -Fq 'raise HTTPException(400, "Unsupported player_api.php action")' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node Xtream API still returns HTTP 400 for unsupported catalogue actions"
# STREAMFORGE_V1042_NODE_PLAYLIST_USER_DISPLAY_NAME_GUARDS:
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_EDIT_DISPLAY_NAME_V1042' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node playlist-user Display name edit fix missing"
grep -Fq 'name="name" value="{html.escape(str(target.name or target.username or ""), quote=True)}"' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node playlist-user Display name field is not prefilled"
grep -Fq 'current.name = effective_display_name' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node playlist-user Display name persistence missing"
grep -Fq '"name": submitted_name or submitted_username or target.name or target.username' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node playlist-user Display name error-state preservation missing"
# STREAMFORGE_V1043_NODE_LIVE_SESSION_KILL_GUARDS:
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_KILL_NO_NAV_V1043' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node Live Sessions no-navigation kill fix missing"
grep -Fq "'X-StreamForge-Live-Kill':'1'" "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node Live Sessions AJAX kill request missing"
grep -Fq 'BackgroundTask(_panel_session_kill_after_response' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node response-first session cleanup missing"
grep -Fq 'STREAMFORGE_NODE_SESSION_KILL_INDEX_V1043' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 Node Redis session-kill index missing"
grep -Fq 'pipe.sadd(sid_key, viewer_key)' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 Node Redis SID index heartbeat missing"
# STREAMFORGE_V1044_NODE_LIVE_SESSION_CANONICAL_POST_GUARDS:
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_KILL_CANONICAL_POST_V1044' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node canonical Live Sessions kill dispatch missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_KILL_CANONICAL_POST_JS_V1044' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node canonical Live Sessions kill browser routing missing"
grep -Fq "'X-StreamForge-Panel-Action':'live-session-kill'" "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node Live Sessions public-root action header missing"
grep -Fq "'X-StreamForge-Live-Session-ID':sid" "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node Live Sessions SID dispatch header missing"
grep -Fq 'elif action == "live-session-kill":' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node outer access validator does not allow canonical session kill"
grep -Fq 'resolved_path = f"/panel/sessions/{kill_sid}/kill"' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node hidden-route session kill dispatcher missing"

# STREAMFORGE_V1045_CLIENT_LOG_SYNC_DURATION_GUARDS
grep -Fq 'STREAMFORGE_NODE_PUBLIC_CLIENT_LOG_SYNC_V1045' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node public-worker Client log sync missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_LOG_SINGLE_WRITER_RETENTION_V1045' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node shared-log single-writer retention guard missing"
grep -Fq 'STREAMFORGE_NODE_XTREAM_MANUAL_LOGIN_LOG_V1045' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node Xtream manual-login Client log event missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_DURATION_V1045' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node Client-log duration renderer missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_DURATION_API_V1045' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node Client-log duration API enrichment missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_DURATION_LOOKUP_V1045' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 Node Redis SID duration lookup missing"
grep -Fq 'STREAMFORGE_MAIN_REMOTE_CLIENT_LOG_DURATION_V1045' "$SOURCE_DIR/app/main.py" || die "v10.68 Main Remote Node duration mapping missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_DURATION_COLUMN_V1045' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Main Client-log Duration column missing"
# STREAMFORGE_V1047_PERSISTENT_SESSION_AGE_GUARDS
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_V1046' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 Node retained session-history writer missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_LOOKUP_V1046' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 Node retained session-history lookup missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_AGE_PERSIST_V1046' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node persistent Client-log Session age missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_LOCAL_V1046' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node local session-history fallback missing"
grep -Fq 'STREAMFORGE_MAIN_REMOTE_CLIENT_SESSION_AGE_PERSIST_V1046' "$SOURCE_DIR/app/main.py" || die "v10.68 Main Remote Node persistent Session age mapping missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_SESSION_AGE_PERSIST_V1046' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Main Client-log Session age column marker missing"
grep -Fq '<th>Session age</th>' "$SOURCE_DIR/app/templates/logs.html" || die "v10.68 Main Client-log Session age heading missing"
grep -Fq 'STREAMFORGE_NODE_PLAYBACK_HEARTBEAT_SAFE_V1047' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 playback-safe viewer heartbeat missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_BATCH_V1047' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 retained session-history Redis batch helper missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_KILL_MARKER_V1047' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 session-history kill marker missing"
# STREAMFORGE_V1049_PLAYBACK_STABILITY_AND_SESSION_TIME_GUARDS
grep -Fq 'STREAMFORGE_NODE_PLAYBACK_CRITICAL_RESERVE_V1049' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 playback-critical reserve isolation missing"
grep -Fq 'STREAMFORGE_NODE_PLAYBACK_HEARTBEAT_ISOLATED_V1049' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 playback heartbeat isolation missing"
grep -Fq 'STREAMFORGE_NODE_VIEWER_ACTIVE_INDEX_V1049' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 active viewer index missing"
grep -Fq "return self._k(f'viewer-active:{self._hash(member, 40)}')" "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 active viewer index key missing"
grep -Fq 'pipe.set(index_key, viewer_key, ex=expire)' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 active viewer index heartbeat missing"
grep -Fq 'STREAMFORGE_NODE_SESSION_OBSERVER_V1049' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 single session-history observer missing"
grep -Fq 'STREAMFORGE_NODE_SINGLE_SESSION_OBSERVER_V1049' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 control-only session observer startup missing"
grep -Fq 'STREAMFORGE_NODE_PLAYBACK_NO_LOG_SIDE_EFFECTS_V1049' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 playback path still lacks logging isolation guard"
grep -Fq '"Playback session started"' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 observer reconnect Client-log message missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_SESSION_FETCH_V1049' "$SOURCE_DIR/app/node_manager.py" || die "v10.68 Main Node session fetch fix missing"
grep -Fq 'STREAMFORGE_MAIN_REMOTE_SESSION_TIME_NORMALIZE_V1049' "$SOURCE_DIR/app/main.py" || die "v10.68 Main Remote session-time normalization missing"
! grep -Fq 'PLAYBACK_START_CONSUME_LUA' "$SOURCE_DIR/node_agent/redis_state.py" || die "v10.68 stale playback-start consume still touches playback Redis"
! grep -Fq 'manager.consume_playback_start(user.token, sid)' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 master playlist still performs Client-log claim work"
! grep -Fq 'client_session_history_flush_loop' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 still carries per-public-worker history flush loop"
! grep -Fq 'queue_client_session_history(' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 playback still queues session history"
! sed -n '/RESERVE_LUA = r/,/def __init__/p' "$SOURCE_DIR/node_agent/redis_state.py" | grep -Fq 'KEYS[3]' || die "v10.68 reserve Lua still contains logging/history key work"
! sed -n '/def touch_viewer(/,/def touch_viewer_history_batch(/p' "$SOURCE_DIR/node_agent/redis_state.py" | grep -Fq 'HISTORY_TOUCH_LUA' || die "v10.68 history write still blocks the critical viewer heartbeat"
grep -Fq "header_cells += '<th>Session age</th>'" "$SOURCE_DIR/node_agent/app.py" || die "v10.68 Node Client-log Session age heading missing"
! grep -Fq '_node_client_log_duration_map(' "$SOURCE_DIR/node_agent/app.py" || die "v10.68 stale live-only Client duration map remains"
grep -Fq 'STREAMFORGE_NODE_CERTBOT_FORCE_RETRY_V1028' "$SOURCE_DIR/node_agent/apply_node_tls.py" || die "v10.30 Node ACME force-retry helper missing from package"
grep -Fq 'STREAMFORGE_NODE_TLS_FORCE_RETRY_V1028' "$SOURCE_DIR/node_agent/app.py" || die "v10.30 Node TLS retry request payload missing from package"
grep -Fq '"10.33", "10.34", "12.3"} and saved_password and node.ssh_host and node.ssh_user' "$SOURCE_DIR/app/main.py" || die "v10.34 saved-SSH Node root-update routing missing from package"
# STREAMFORGE_NODE_PUBLIC_SYSTEMD_PACKAGE_GUARDS_V79
[[ -f "$SOURCE_DIR/node_agent/deploy/streamforge-node-public.service" ]] || die "v7.9 Node Public systemd unit missing from package"
[[ -x "$SOURCE_DIR/node_agent/public_start.py" ]] || die "v7.9 Node Public hardware-aware starter missing from package"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_SYSTEMD_SPLIT_V79' "$SOURCE_DIR/node_agent/public_start.py" || die "v7.9 Node Public starter marker missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_SYSTEMD_OWNER_V79' "$SOURCE_DIR/node_agent/app.py" || die "v7.9 control/public ownership split missing"
grep -Fq 'STREAMFORGE_NODE_V79_PUBLIC_SYSTEMD_ROOT_GUARD' "$SOURCE_DIR/node_agent/app.py" || die "v7.9 API root migration guard missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_SYSTEMD_SPLIT_INSTALL_V79' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v7.9 Node Public unit installer missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_SYSTEMD_ENV_V79' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v7.9 Node Public systemd env policy missing"
grep -Fq 'ExecStart=/usr/bin/python3 /opt/streamforge-node/public_start.py' "$SOURCE_DIR/node_agent/deploy/streamforge-node-public.service" || die "v7.9 Node Public service starter path missing"
# STREAMFORGE_NODE_PUBLIC_HEALTH_PACKAGE_GUARDS_V80
grep -Fq 'STREAMFORGE_NODE_PUBLIC_INTERNAL_HEALTH_V80' "$SOURCE_DIR/node_agent/app.py" || die "v8.0 Node Public internal health bypass missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_HEALTH_READY_V80' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "v8.0 Node Public readiness probe marker missing"
grep -Fq 'NODE_MODE == "public" and original_path == "/api/v1/health" and _valid_control_token(request)' "$SOURCE_DIR/node_agent/app.py" || die "v8.0 authenticated Public health condition missing"
grep -Fq 'STREAMFORGE_MAIN_DNS01_PANEL_ROOT_V81' "$SOURCE_DIR/app/templates/node_form.html" || die "v8.1 Main DNS-01 panel-root marker missing"
grep -Fq 'STREAMFORGE_MAIN_DNS01_PANEL_ROOT_V81' "$SOURCE_DIR/app/templates/node_form.html" || die "v8.1 DNS-01 panel-root compatibility marker missing"
grep -Fq "post(nodeDns01Path('test'), {host})" "$SOURCE_DIR/app/templates/node_form.html" || die "v8.1 DNS-01 Test CNAME action is not prefix-aware"
grep -Fq "post(nodeDns01Path('retry'), {host})" "$SOURCE_DIR/app/templates/node_form.html" || die "v8.1 DNS-01 Retry certificate action is not prefix-aware"
grep -Fq 'STREAMFORGE_MAIN_NODE_DNS01_REMOTE_ACTIONS_V82' "$SOURCE_DIR/app/main.py" || die "v8.2 Main-to-Node DNS-01 test proxy missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_DNS01_RETRY_V82' "$SOURCE_DIR/app/main.py" || die "v8.2 Main DNS-01 retry stale-status fix missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_DNS01_REMOTE_TEST_V82' "$SOURCE_DIR/app/node_manager.py" || die "v8.2 Node DNS-01 control client missing"
grep -Fq 'STREAMFORGE_NODE_DNS01_CONTROL_TEST_V82' "$SOURCE_DIR/node_agent/app.py" || die "v8.2 Node DNS-01 control test endpoint missing"
grep -Fq 'STREAMFORGE_NODE_DNS01_RETRY_VALIDATE_V82' "$SOURCE_DIR/node_agent/app.py" || die "v8.2 Node DNS-01 retry hostname validation missing"
grep -Fq 'STREAMFORGE_MAIN_APP_ROOT_EARLY_BOOTSTRAP_V83' "$SOURCE_DIR/app/templates/base.html" || die "v8.3 early Main app-root bootstrap missing"
grep -Fq 'STREAMFORGE_MAIN_DNS01_SERVER_ROOT_V83' "$SOURCE_DIR/app/templates/node_form.html" || die "v8.3 DNS-01 render-time prefix fix missing"
grep -Fq 'const panelRoot = String({{ app_root|tojson }} || window.STREAMFORGE_APP_ROOT ||' "$SOURCE_DIR/app/templates/node_form.html" || die "v8.3 DNS-01 action path is not render-prefix aware"
! sed -n '/async def node_dns01_retry/,/return JSONResponse(result)/p' "$SOURCE_DIR/app/main.py" | grep -F 'raise HTTPException(404, "DNS-01 delegation is not registered for this hostname")' >/dev/null || die "v8.2 Main DNS-01 retry still exposes hidden 404/444"
grep -Fq 'requested_version in {"7.7", "7.8", "7.9", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "8.9", "9.0", "9.1", "9.2", "9.3", "9.4"}' "$SOURCE_DIR/node_agent/app.py" || die "v8.2 Node root Nginx guard version missing"
grep -Fq 'requested_version in {"7.9", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "8.9", "9.0", "9.1", "9.2", "9.3", "9.4"}' "$SOURCE_DIR/node_agent/app.py" || die "v8.2 Node public-systemd guard version missing"
grep -Fq 'STREAMFORGE_NODE_V39_ROOT_TLS_UPDATE_GUARD' "$SOURCE_DIR/node_agent/app.py" || die "Node v3.9 SSH/root update guard missing from package"
grep -Fq 'STREAMFORGE_MAIN_NODE_ROOT_UPDATE_SSH_PREFERENCE_V1030' "$SOURCE_DIR/app/main.py" || die "v10.30 Node root-update SSH preference marker missing from package"
grep -Fq 'STREAMFORGE_MAIN_NODE_ROOT_UPDATE_SSH_PREFERENCE_V1031' "$SOURCE_DIR/app/main.py" || die "v10.31 Node root-update SSH preference marker missing from package"
grep -Fq 'STREAMFORGE_MAIN_NODE_ROOT_UPDATE_SSH_PREFERENCE_V1032' "$SOURCE_DIR/app/main.py" || die "v10.34 Node root-update SSH preference marker missing from package"
grep -Fq 'STREAMFORGE_MAIN_NODE_ROOT_UPDATE_SSH_PREFERENCE_V1033' "$SOURCE_DIR/app/main.py" || die "v10.34 current Node root-update SSH preference marker missing from package"
grep -Fq 'STREAMFORGE_MAIN_NODE_ROOT_UPDATE_SSH_PREFERENCE_V1034' "$SOURCE_DIR/app/main.py" || die "v10.34 current Node root-update SSH preference marker missing from package"
grep -Fq '"10.33", "10.34", "12.3"} and saved_password and node.ssh_host and node.ssh_user:' "$SOURCE_DIR/app/main.py" || die "v10.34 saved-SSH Node root-update routing missing from package"
grep -Fq 'STREAMFORGE_PUBLIC_URL_SCHEME_PORT_NORMALIZATION_V36' "$SOURCE_DIR/app/main.py" || die "Main public URL scheme/port normalization missing from package"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_URL_SCHEME_PORT_NORMALIZATION_V36' "$SOURCE_DIR/node_agent/app.py" || die "Node public URL scheme/port normalization missing from package"
grep -Fq 'STREAMFORGE_REMOTE_PUBLIC_INTERNAL_PORT_SPLIT_V36' "$SOURCE_DIR/app/main.py" || die "Remote public/internal port split missing from package"
[[ -x "$SOURCE_DIR/scripts/migrate_v36_public_url_ports.py" ]] || die "v3.9 public URL port repair migration missing from package"
[[ -x "$SOURCE_DIR/scripts/migrate_v33_control_scope.py" ]] || die "v3.9 control-scope migration missing from package"
grep -Fq 'STREAMFORGE_IPINFO_COMPATIBLE_LOOKUP_V3059' "$SOURCE_DIR/app/access_control.py" || die "Main IPinfo compatible lookup missing from package"
grep -Fq 'STREAMFORGE_NODE_IPINFO_COMPATIBLE_LOOKUP_V3059' "$SOURCE_DIR/node_agent/app.py" || die "Node IPinfo compatible lookup missing from package"
grep -Fq 'result.ipinfo_error' "$SOURCE_DIR/app/templates/node_asn.html" || die "GeoIP lookup error display missing from package"
grep -Fq 'STREAMFORGE_MAIN_ROOT_PANEL_PREFIX_EXCLUSION_V3059' "$SOURCE_DIR/app/main.py" || die "Main root /panel exclusion missing from package"
grep -Fq 'STREAMFORGE_NODE_ROOT_PANEL_PREFIX_EXCLUSION_V3059' "$SOURCE_DIR/node_agent/app.py" || die "Node root /panel exclusion missing from package"
grep -Fq 'STREAMFORGE_NODE_FIXED_PANEL_ADDRESS_BAR_V3059' "$SOURCE_DIR/node_agent/app.py" || die "Node fixed Panel address bar missing from package"
grep -Fq 'action="/nodes/{{ node.id }}/asn"' "$SOURCE_DIR/app/templates/node_asn.html" || die "GeoIP test explicit route missing from package"
grep -Fq 'STREAMFORGE_MAIN_IPINFO_IPV4_TRANSPORT_FALLBACK_V3060' "$SOURCE_DIR/app/access_control.py" || die "Main IPinfo IPv4 transport fallback missing from package"
grep -Fq 'STREAMFORGE_NODE_IPINFO_IPV4_TRANSPORT_FALLBACK_V3060' "$SOURCE_DIR/node_agent/app.py" || die "Node IPinfo IPv4 transport fallback missing from package"
grep -Fq 'STREAMFORGE_NODE_ZERO_FLASH_PANEL_NAV_V3060' "$SOURCE_DIR/node_agent/app.py" || die "Node zero-flash Panel navigation missing from package"
grep -Fq 'STREAMFORGE_NODE_RELOAD_SAFE_HIDDEN_ROUTE_V3060R2' "$SOURCE_DIR/node_agent/app.py" || die "Node reload-safe hidden Panel route missing from package"
grep -Fq 'STREAMFORGE_NONBLOCKING_SOURCE_SCAN_V3061' "$SOURCE_DIR/app/main.py" || die "Main non-blocking channel source scan missing from package"
grep -Fq 'STREAMFORGE_NODE_NONBLOCKING_SOURCE_SCAN_V3061' "$SOURCE_DIR/node_agent/app.py" || die "Node non-blocking channel source scan missing from package"
grep -Fq 'STREAMFORGE_ANDROID_UPDATE_MANIFEST_ROUTE_V3061' "$SOURCE_DIR/app/main.py" || die "Main Android update.json route missing from package"
grep -Fq 'STREAMFORGE_NODE_ANDROID_UPDATE_MANIFEST_ROUTE_V3061' "$SOURCE_DIR/node_agent/app.py" || die "Node Android update.json route missing from package"
grep -Fq 'STREAMFORGE_UPDATE_JSON_LOCAL_WEBPLAYER_THEME_V109' "$SOURCE_DIR/app/main.py" || die "v10.25 Main update.json Web Player theme payload missing"
grep -Fq '"webplayer": {' "$SOURCE_DIR/app/main.py" || die "v10.25 Main update.json webplayer object missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_JSON_LOCAL_WEBPLAYER_THEME_V109' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node update.json Web Player theme payload missing"
grep -Fq '"webplayer": _node_webplayer_update_theme_snapshot(request)' "$SOURCE_DIR/node_agent/app.py" || die "v11.58 Node update.json request-scoped brand theme snapshot missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_LOGO_ROUTE_V1010' "$SOURCE_DIR/app/main.py" || die "v10.25 Main Web Player logo route missing"
grep -Fq 'STREAMFORGE_UPDATE_JSON_SERVER_LOCAL_LOGO_V1010' "$SOURCE_DIR/app/main.py" || die "v10.25 Main update.json server-local logo missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_JSON_SERVER_LOCAL_LOGO_V1010' "$SOURCE_DIR/node_agent/app.py" || die "v10.25 Node update.json server-local logo missing"
grep -Fq 'STREAMFORGE_MAIN_MOBILE_LOGOUT_VISIBLE_V1010' "$SOURCE_DIR/app/static/style.css" || die "v10.25 Main mobile logout footer missing"
grep -Fq 'STREAMFORGE_ANDROID_UPDATE_MANAGE_UI_V3061' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Android update manage UI missing from package"
grep -Fq 'STREAMFORGE_NODE_FIXED_LOGIN_ADDRESS_BAR_V3059' "$SOURCE_DIR/node_agent/app.py" || die "Node fixed login address bar missing from package"
grep -Fq 'STREAMFORGE_NODE_ROOT_PANEL_ALIAS_V3058' "$SOURCE_DIR/node_agent/app.py" || die "Root Node Panel alias canonicalization missing from package"
grep -Fq 'def _node_panel_public_redirect_location(' "$SOURCE_DIR/node_agent/app.py" || die "Node Panel public redirect mapper missing from package"
grep -Fq 'root_panel_alias = matched_prefix is not None and not access_prefix' "$SOURCE_DIR/node_agent/app.py" || die "Root Node Panel login-form alias detection missing from package"
grep -Fq 'if matched_panel_prefix is not None:' "$SOURCE_DIR/node_agent/app.py" || die "Root Node Panel generated-link canonicalization missing from package"
grep -Fq '"/favicon" if root_panel_alias else "/panel/favicon"' "$SOURCE_DIR/node_agent/app.py" || die "Root Node Panel favicon canonicalization missing from package"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOGIN_ROLE_ISOLATION_V3057' "$SOURCE_DIR/node_agent/app.py" || die "Node Panel login role-isolation fix missing from package"
! sed -n '/^async def node_panel_login(request: Request):/,/^$/p' "$SOURCE_DIR/node_agent/app.py" | grep -F 'manager.webplayer_login_mode' >/dev/null || die "Node Panel login still depends on Web Player login mode"
grep -Fq 'response = RedirectResponse("/panel", status_code=303)' "$SOURCE_DIR/node_agent/app.py" || die "Node Panel post-login dashboard redirect missing from package"
grep -Fq 'STREAMFORGE_WEBPLAYER_QUICK_USER_PRIVACY_V3056' "$SOURCE_DIR/app/main.py" || die "Main Quick Login privacy session state missing from package"
grep -Fq 'STREAMFORGE_WEBPLAYER_QUICK_USER_PRIVACY_V3056' "$SOURCE_DIR/app/templates/web_player.html" || die "Main channel-page Quick Login privacy UI missing from package"
grep -Fq 'not hide_quick_user_info' "$SOURCE_DIR/app/templates/player.html" || die "Main watch-page Quick Login privacy UI missing from package"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_QUICK_USER_PRIVACY_V3056' "$SOURCE_DIR/node_agent/app.py" || die "Node signed Quick Login privacy state missing from package"
grep -Fq '_node_web_cookie_value(user, "quick")' "$SOURCE_DIR/node_agent/app.py" || die "Node Quick Login cookie-origin signing missing from package"
grep -Fq 'controls["show_connection_info"] and not hide_quick_user_info' "$SOURCE_DIR/node_agent/app.py" || die "Node viewer-information privacy guard missing from package"
grep -Fq 'Leave blank to hide the subtitle.' "$SOURCE_DIR/app/templates/system_branding.html" || die "Optional subtitle UI missing from package"
grep -Fq 'if BRANDING_SUBTITLE_KEY in values' "$SOURCE_DIR/app/main.py" || die "Blank subtitle persistence fix missing from package"
grep -Fq 'brand_subtitle: str = Form("")' "$SOURCE_DIR/app/main.py" || die "Blank subtitle form default fix missing from package"
grep -Fq 'subtitle_row.value = cleaned_subtitle' "$SOURCE_DIR/app/main.py" || die "Blank subtitle database persistence guard missing from package"
grep -Fq 'class="brand login-brand"' "$SOURCE_DIR/app/templates/login.html" || die "Centered login branding markup missing from package"
grep -Fq 'data-live-session-body' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "Main online users live table marker missing from package"
grep -Fq 'data-live-refresh-select' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "Main online users refresh interval control missing from package"
grep -Fq 'streamforge.viewerSessions.refreshSeconds' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "Main online users refresh preference persistence missing from package"
grep -Fq '/viewer-sessions/live-data' "$SOURCE_DIR/app/main.py" || die "Main online users live-data endpoint missing from package"
grep -Fq 'nodes-page-content' "$SOURCE_DIR/app/templates/nodes.html" || die "Nodes page spacing wrapper missing from package"
grep -Fq '/system/backups/restore' "$SOURCE_DIR/app/main.py" || die "Main backup restore route missing from package"
grep -Fq 'Restore Main Server' "$SOURCE_DIR/app/templates/backups.html" || die "Main backup restore UI missing from dedicated Backups page package"
grep -Fq 'STREAMFORGE_SHARED_RESTORE_ARCHIVE_POLICY_V3020' "$SOURCE_DIR/app/main.py" || die "Shared web restore archive policy missing from package"
[[ "$(grep -Fc '_validate_main_restore_archive(staged)' "$SOURCE_DIR/app/main.py")" -eq 3 ]] || die "All three Main restore routes must use the shared archive policy"
# STREAMFORGE_RESTORE_PRESERVE_CURRENT_ACCESS_V30
grep -Fq 'STREAMFORGE_RESTORE_PRESERVE_CURRENT_ACCESS_V30' "$SOURCE_DIR/scripts/main_system_control.py" || die "v3.0 restore access-preservation core missing from package"
# STREAMFORGE_PRETTY_STREAM_ERROR_PAGE_V301
grep -Fq 'STREAMFORGE_PRETTY_STREAM_ERROR_PAGE_V301' "$SOURCE_DIR/app/main.py" || die "Branded stream error response helper missing from package"
grep -Fq 'STREAMFORGE_DIRECT_MASTER_PRETTY_ERROR_V301' "$SOURCE_DIR/app/main.py" || die "Direct master pretty error handling missing from package"
grep -Fq 'name="stream_error.html"' "$SOURCE_DIR/app/main.py" || die "Stream error template render missing from package"
grep -Fq 'Try again' "$SOURCE_DIR/app/templates/stream_error.html" || die "Friendly stream error page missing from package"
grep -Fq 'Technical details' "$SOURCE_DIR/app/templates/stream_error.html" || die "Stream error technical details section missing from package"
grep -Fq 'STREAMFORGE_USER_ALLOWED_NODE_MOBILE_V302' "$SOURCE_DIR/app/static/style.css" || die "Users allowed-node mobile layout fix missing from package"
grep -Fq 'class="user-node-filter-field"' "$SOURCE_DIR/app/templates/users.html" || die "Users allowed-node structured filter field missing from package"
grep -Fq 'class="button user-node-filter-clear"' "$SOURCE_DIR/app/templates/users.html" || die "Users allowed-node Clear control missing from package"

grep -Fq 'STREAMFORGE_RESTORE_DESTINATION_ACCESS_SNAPSHOT_V30' "$SOURCE_DIR/scripts/main_system_control.py" || die "v3.0 destination access snapshot missing from package"
grep -Fq 'STREAMFORGE_RESTORE_ENV_ACCESS_PRESERVE_V30' "$SOURCE_DIR/scripts/main_system_control.py" || die "v3.0 restore env access preservation missing from package"
grep -Fq 'STREAMFORGE_RESTORE_DB_ACCESS_PRESERVE_V30' "$SOURCE_DIR/scripts/main_system_control.py" || die "v3.0 restore DB access preservation missing from package"
grep -Fq 'PRESERVED_LOCAL_NODE_FIELDS' "$SOURCE_DIR/scripts/main_system_control.py" || die "v3.0 preserved Main access fields missing from package"
grep -Fq 'STREAMFORGE_RESTORE_ACCESS_LOCKOUT_GUARD_V309' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore access lockout guard missing from package"
grep -Fq 'STREAMFORGE_RESTORE_ACCESS_WAL_CHECKPOINT_V309' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore access WAL checkpoint missing from package"
grep -Fq 'STREAMFORGE_RESTORE_ACCESS_NGINX_REAPPLY_V309' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore Main Nginx reapply missing from package"
grep -Fq 'STREAMFORGE_RESTORE_ACTIVE_URL_PAYLOAD_V3010' "$SOURCE_DIR/app/main.py" || die "Restore active browser URL payload missing from package"
grep -Fq 'STREAMFORGE_RESTORE_ACTIVE_URL_AUTHORITY_V3010' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore active URL authority guard missing from package"
grep -Fq 'STREAMFORGE_RESTORE_PUBLIC_ROUTE_VERIFY_V3011' "$SOURCE_DIR/scripts/main_system_control.py" || die "Post-restore public route verification missing from package"
grep -Fq 'STREAMFORGE_RESTORE_VERIFIED_FAIL_OPEN_V3011' "$SOURCE_DIR/scripts/main_system_control.py" || die "Post-restore automatic fail-open missing from package"
grep -Fq 'STREAMFORGE_RESTORE_REMOVE_SOURCE_DOMAIN_V3012' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restored source-domain removal missing from package"
grep -Fq 'STREAMFORGE_RESTORE_ENV_REMOVE_SOURCE_DOMAIN_V3012' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restored environment source-domain removal missing from package"
grep -Fq 'STREAMFORGE_MAIN_ENV_RECOVERY_ALIAS_V3013' "$SOURCE_DIR/app/main.py" || die "Environment-backed Main recovery alias missing from package"
grep -Fq 'STREAMFORGE_BACKUP_COMPLETE_LOGO_ASSETS_V3017' "$SOURCE_DIR/app/backup_manager.py" || die "Complete backup logo asset bundle missing from package"
grep -Fq 'assets_complete=1' "$SOURCE_DIR/app/backup_manager.py" || die "Backup logo completeness manifest missing from package"
grep -Fq 'STREAMFORGE_BACKUP_STALE_LOGO_TOLERANCE_V3018' "$SOURCE_DIR/app/backup_manager.py" || die "Stale logo reference tolerance missing from package"
grep -Fq 'STREAMFORGE_LOGO_ASSET_FILETYPE_FILTER_V3023' "$SOURCE_DIR/app/backup_manager.py" || die "Logo backup file-type filter missing from package"
grep -Fq 'streamforge-assets/ASSET-MANIFEST.json' "$SOURCE_DIR/app/backup_manager.py" || die "Backup logo hash inventory missing from package"
grep -Fq 'STREAMFORGE_RESTORE_COMPLETE_LOGO_ASSETS_V3017' "$SOURCE_DIR/scripts/main_system_control.py" || die "Complete logo restore core missing from package"
grep -Fq 'STREAMFORGE_RESTORE_ASSET_INVENTORY_V3018' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore logo inventory validation missing from package"
grep -Fq 'STREAMFORGE_RESTORE_ASSET_MEMBER_POLICY_V3019' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore logo archive-member policy missing from package"
grep -Fq 'STREAMFORGE_RESTORE_LOGO_REFERENCE_REBASE_V3021' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restored logo-reference rebase missing from package"
grep -Fq 'STREAMFORGE_CANONICAL_LOGO_STORAGE_V3022' "$SOURCE_DIR/scripts/main_system_control.py" || die "Canonical Main logo storage migration missing from package"
grep -Fq '_restore_verified_logo_payload(' "$SOURCE_DIR/scripts/main_system_control.py" || die "Shared verified restored-logo call missing from package"
grep -Fq 'STREAMFORGE_UPDATE_RECONCILE_CURRENT_LOGOS_V3021' "$SOURCE_UPDATER" || die "Current restored-logo update reconciliation missing from package"
grep -Fq 'Logo files copied into canonical /opt/streamforge/logo' "$SOURCE_UPDATER" || die "Canonical current-logo updater hook missing from package"
grep -Fq 'STREAMFORGE_ROOT_RESTORE_HELPER_REFRESH_V3019' "$SOURCE_UPDATER" || die "Root restore helper refresh transaction missing from package"
grep -Fq 'Restored logo checksum verification failed' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restored logo checksum verification missing from package"
grep -Fq 'STREAMFORGE_LOGO_ROOT' "$SOURCE_DIR/scripts/main_system_control.py" || die "Destination Main logo path preservation missing from package"
grep -Fq 'STREAMFORGE_NODE_LOGO_ROOT' "$SOURCE_DIR/scripts/main_system_control.py" || die "Destination Node logo path preservation missing from package"
grep -Fq 'RESTORE_LOGO_SYNC_MARKER' "$SOURCE_DIR/scripts/main_system_control.py" || die "Post-restore logo sync marker missing from package"
grep -Fq 'STREAMFORGE_RESTORE_REMOTE_LOGO_RESYNC_V3017' "$SOURCE_DIR/app/main.py" || die "Remote Node restored-logo resync missing from package"
grep -Fq 'node_controller.sync_panel_users(node, panel_url)' "$SOURCE_DIR/app/main.py" || die "Remote Node branding resync call missing from package"
grep -Fq 'sync_channel_logo_to_all_nodes(channel_id)' "$SOURCE_DIR/app/main.py" || die "Remote channel-logo resync call missing from package"
grep -Fq 'STREAMFORGE_MAIN_UNKNOWN_PATH_SILENT_DROP_V3014' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Unknown Main path silent-drop generator missing from package"
grep -Fq 'error_page 404 418 = @streamforge_silent_drop;' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Dynamic Main upstream 404 interception missing from package"
grep -Fq 'STREAMFORGE_FRESH_UNKNOWN_PATH_SILENT_DROP_V3014' "$SOURCE_DIR/deploy/nginx.conf" || die "Fresh-install unknown Main path silent-drop config missing from package"
grep -Fq 'error_page 404 418 = @streamforge_silent_drop;' "$SOURCE_DIR/deploy/nginx.conf" || die "Fresh-install upstream 404 interception missing from package"
grep -Fq 'def configured_main_recovery_aliases(' "$SOURCE_DIR/app/main.py" || die "Main recovery alias helper missing from package"
grep -Fq '"dns_only": 0' "$SOURCE_DIR/scripts/main_system_control.py" || die "Verified emergency recovery fail-open missing from package"
grep -Fq 'STREAMFORGE_RESTORE_PRESERVE_ALL_ACCESS_URLS_V3039' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore complete Panel/Playlist URL preservation missing from package"
grep -Fq 'STREAMFORGE_RESTORE_FAIL_OPEN_KEEP_ACCESS_URLS_V3039' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore fail-open URL preservation missing from package"
grep -Fq '"STREAMFORGE_PUBLIC_BASE_URL": active_recovery_url' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore public base URL replacement missing from package"
grep -Fq '"STREAMFORGE_RELAY_BASE_URL": active_recovery_url' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore relay base URL replacement missing from package"
grep -Fq 'def _probe_recovery_access(' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore recovery route probe helper missing from package"
grep -Fq 'def _open_recovery_access(' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore fail-open helper missing from package"
grep -Fq 'def _restore_recovery_access_url(' "$SOURCE_DIR/app/main.py" || die "Restore active URL capture helper missing from package"
grep -Fq 'payload["recovery_url"]' "$SOURCE_DIR/app/main.py" || die "Restore recovery URL request field missing from package"
grep -Fq 'def _validated_recovery_url(' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore recovery URL validator missing from package"
grep -Fq 'def _access_snapshot_with_recovery_url(' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore recovery alias merger missing from package"
[[ "$(grep -Fc '_restore_recovery_access_url(request)' "$SOURCE_DIR/app/main.py")" -eq 3 ]] || die "All three restore routes must capture the active browser URL"
grep -Fq 'active_recovery_url' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore safety snapshot recovery URL evidence missing from package"
grep -Fq 'streamforge-main-access.service' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore listener-regeneration service call missing from package"
grep -Fq 'main-access-snapshot.json' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore access safety snapshot missing from package"
grep -Fq 'safe_fallback = "http://127.0.0.1"' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore no-snapshot port-80 recovery fallback missing from package"
grep -Fq "current server's Main Panel/API and Playlist/App domain" "$SOURCE_DIR/app/templates/backups.html" || die "v3.0 restore preservation notice missing from package"

grep -Fq 'Saved Main Server backups' "$SOURCE_DIR/app/templates/local_backup_files.html" || die "Dedicated local backup files UI missing from package"
grep -Fq 'View backups' "$SOURCE_DIR/app/templates/backups.html" || die "Remote backup browser link missing from dedicated Backups page package"
grep -Fq 'def list_target_backup_archives(' "$SOURCE_DIR/app/backup_manager.py" || die "Remote backup listing backend missing from package"
grep -Fq 'def fetch_target_backup_archive(' "$SOURCE_DIR/app/backup_manager.py" || die "Remote backup download/restore backend missing from package"
grep -Fq 'def delete_target_backup_archive(' "$SOURCE_DIR/app/backup_manager.py" || die "Remote backup delete backend missing from package"
grep -Fq '/system/backups/{target_id}/files' "$SOURCE_DIR/app/main.py" || die "Remote backup browser routes missing from package"
grep -Fq '"backup_files.html",' "$SOURCE_DIR/app/main.py" || die "Dedicated backup files page render missing from package"
grep -A4 -F '"backup_files.html",' "$SOURCE_DIR/app/main.py" | grep -Fq 'db,' || die "Dedicated backup files page render context missing from package"
grep -Fq 'backup-destination-summary' "$SOURCE_DIR/app/templates/backup_files.html" || die "Dedicated backup files page UI missing from package"
grep -Fq 'backup-files-head' "$SOURCE_DIR/app/templates/backup_files.html" || die "Backup files header layout fix missing from package"
grep -Fq '<span class="nav-label">Backups</span>' "$SOURCE_DIR/app/templates/base.html" || die "Main Backups sidebar option missing from package"
grep -Fq 'name="rotation_keep"' "$SOURCE_DIR/app/templates/backups.html" || die "Per-destination backup rotation UI missing from package"
grep -Fq 'data-backup-rotation' "$SOURCE_DIR/app/templates/backups.html" || die "Visible per-destination rotation input missing from package"
grep -Fq 'backup-top-field' "$SOURCE_DIR/app/templates/backups.html" || die "Backup top-row field alignment fix missing from package"
grep -Fq 'align-items:start' "$SOURCE_DIR/app/templates/backups.html" || die "Equal backup form top alignment missing from package"
grep -Fq '.backup-target-form.is-editing .backup-name-field{grid-column:span 3}' "$SOURCE_DIR/app/templates/backups.html" || die "Edit-mode Name alignment missing from package"
grep -Fq '.backup-target-form.is-editing .backup-schedule-field{grid-column:span 3}' "$SOURCE_DIR/app/templates/backups.html" || die "Edit-mode Interval alignment missing from package"
grep -Fq '.backup-target-form.is-editing .backup-time-field{grid-column:span 3}' "$SOURCE_DIR/app/templates/backups.html" || die "Edit-mode Backup time alignment missing from package"
grep -Fq '.backup-target-form.is-editing .backup-rotation-field{grid-column:span 3}' "$SOURCE_DIR/app/templates/backups.html" || die "Edit-mode Rotation alignment missing from package"
grep -Fq 'name="schedule_time" value="00:00"' "$SOURCE_DIR/app/templates/backups.html" || die "Per-destination default backup time missing from package"
grep -Fq 'data-schedule-time=' "$SOURCE_DIR/app/templates/backups.html" || die "Per-destination backup time edit state missing from package"
grep -Fq 'def _backup_schedule_due(' "$SOURCE_DIR/app/backup_manager.py" || die "Clock-anchored backup scheduler missing from package"
grep -Fq '"schedule_time": _normalize_schedule_time(schedule_time)' "$SOURCE_DIR/app/backup_manager.py" || die "Per-destination backup time persistence missing from package"
grep -Fq 'margin-top:-10px!important' "$SOURCE_DIR/app/templates/backups.html" || die "Destination spacing fix missing from package"
grep -Fq 'data-local-backup-edit' "$SOURCE_DIR/app/templates/backups.html" || die "Main Server local backup Edit UI missing from package"
grep -Fq '/system/backups/local-files' "$SOURCE_DIR/app/templates/backups.html" || die "Main Server local View backups link missing from package"
! grep -Fq 'data-local-backup-delete' "$SOURCE_DIR/app/templates/backups.html" || die "Main Server local backup delete UI must not exist in package"
grep -Fq 'local_backup_files.html' "$SOURCE_DIR/app/main.py" || die "Dedicated Main Server local backup route missing from package"
grep -Fq 'system_local_backup_update' "$SOURCE_DIR/app/main.py" || die "Main Server local backup update route missing from package"
grep -Fq 'system_local_backup_run' "$SOURCE_DIR/app/main.py" || die "Main Server local Run now route missing from package"
grep -Fq 'def run_local_backup()' "$SOURCE_DIR/app/backup_manager.py" || die "Main Server local backup execution backend missing from package"
grep -Fq '_backup_schedule_due(local, now)' "$SOURCE_DIR/app/backup_manager.py" || die "Main Server local backup scheduler missing from package"
grep -Fq 'action="/system/backups/local/run"' "$SOURCE_DIR/app/templates/backups.html" || die "Main Server local Run now UI missing from package"
grep -Fq 'name="schedule_hours"' "$SOURCE_DIR/app/templates/backups.html" || die "Main Server local schedule UI missing from package"
grep -Fq '.backup-restore-card{' "$SOURCE_DIR/app/templates/backups.html" || die "Main restore card styling missing from package"
grep -Fq '.local-backup-edit-grid input{' "$SOURCE_DIR/app/templates/backups.html" || die "Local backup edit input styling missing from package"
grep -Fq 'background:var(--field)' "$SOURCE_DIR/app/templates/backups.html" || die "Local backup edit dark field styling missing from package"
grep -Fq '.local-backup-edit-grid input:disabled{' "$SOURCE_DIR/app/templates/backups.html" || die "Local backup disabled destination styling missing from package"
grep -Fq 'data-local-backup-modal' "$SOURCE_DIR/app/templates/backups.html" || die "Local backup Edit modal missing from package"
grep -Fq 'data-backup-edit-modal' "$SOURCE_DIR/app/templates/backups.html" || die "Remote backup Edit modal missing from package"
grep -Fq 'moveFormToModal' "$SOURCE_DIR/app/templates/backups.html" || die "Remote backup modal form handling missing from package"
grep -Fq 'body.backup-modal-open' "$SOURCE_DIR/app/templates/backups.html" || die "Backup modal scroll lock styling missing from package"
grep -Fq 'background:#0d1620!important' "$SOURCE_DIR/app/templates/backups.html" || die "Opaque backup modal background missing from package"
grep -Fq 'background:rgba(0,0,0,.82)' "$SOURCE_DIR/app/templates/backups.html" || die "Backup modal backdrop opacity fix missing from package"
grep -Fq 'backup-edit-cancel-right' "$SOURCE_DIR/app/templates/backups.html" || die "Remote backup Cancel edit right-alignment missing from package"
grep -Fq 'STREAMFORGE_UNIFIED_DROPDOWN_STYLE' "$SOURCE_DIR/app/static/style.css" || die "Project-wide dropdown styling marker missing from package"
grep -Fq 'STREAMFORGE_DROPDOWN_FULL_AUDIT' "$SOURCE_DIR/app/static/style.css" || die "Main authoritative dropdown styling missing from package"
grep -Fq 'STREAMFORGE_CATEGORY_DROPDOWN_UNIFIED' "$SOURCE_DIR/app/static/style.css" || die "Channel category dropdown unification missing from package"
grep -Fq 'install -d -o streamforge -g streamforge -m 0750 "$DATA_DIR/gunicorn-tmp"' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install Gunicorn temp setup missing from package"
grep -Fq 'streamforge-apply-main-access' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install Main web-listener helper setup missing from package"
grep -Fq 'streamforge-main-access.path' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install Main web-listener path watcher missing from package"
grep -Fq 'STREAMFORGE_MAIN_ACCESS_RUNTIME_DIR=/var/lib/streamforge/main-access-runtime' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install Main access runtime env missing from package"
grep -Fq 'STREAMFORGE_GEOIP_SETTINGS_FILE=/opt/streamforge/geoip-settings.json' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install GeoIP settings env missing from package"
grep -Fq 'STREAMFORGE_FRESH_INSTALL_GEOIP_SELF_COPY_V308' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install GeoIP self-copy fix missing from package"
grep -Fq 'STREAMFORGE_AUTO_UPDATE_COMMAND_INSTALL_V3011' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install automatic update command setup missing from package"
grep -Fq 'sudo streamforge-update [--force] [PACKAGE.zip]' "$SOURCE_DIR/scripts/streamforge-update" || die "System update command payload missing from package"
grep -Fq 'STREAMFORGE_RESETUSER_COMMAND_V3015' "$SOURCE_DIR/scripts/streamforge" || die "System resetuser command payload missing from package"
grep -Fq 'STREAMFORGE_RESETDOMAIN_COMMAND_V3016' "$SOURCE_DIR/scripts/reset_domain.py" || die "System resetdomain helper missing from package"
grep -Fq 'resetdomain)' "$SOURCE_DIR/scripts/streamforge" || die "System resetdomain command option missing from package"
grep -Fq 'STREAMFORGE_LOGO_ONLY_RESTORE_V3023' "$SOURCE_DIR/scripts/restore_logos.py" || die "Logo-only restore helper missing from package"
grep -Fq 'restorelogos)' "$SOURCE_DIR/scripts/streamforge" || die "System restorelogos command option missing from package"
grep -Fq 'STREAMFORGE_RESETPANELACCESS_COMMAND_V107' "$SOURCE_DIR/scripts/streamforge" || die "System resetpanelaccess command payload missing from package"
grep -Fq 'resetpanelaccess)' "$SOURCE_DIR/scripts/streamforge" || die "System resetpanelaccess command option missing from package"
grep -Fq 'STREAMFORGE_UPDATE_ACCESS_FROM_ENV_V3023' "$SOURCE_UPDATER" || die "Update access preservation from environment missing from package"
grep -Fq '"sqlite:///$DB_PATH"' "$SOURCE_UPDATER" || die "Access-sync SQLite DB_PATH conversion missing from package"
grep -Fq 'STREAMFORGE_UPDATE_ACCESS_HELPER_PERMISSION_V3025' "$SOURCE_UPDATER" || die "Access helper pre-execution permission repair missing from package"
grep -Fq 'runuser -u streamforge -- test -r "$APP_DIR/scripts/reset_domain.py"' "$SOURCE_UPDATER" || die "Access helper service-user readability guard missing from package"
grep -Fq 'STREAMFORGE_UPDATE_PRESERVE_ACCESS_PATHS_V3026' "$SOURCE_DIR/scripts/reset_domain.py" || die "Update Panel/Playlist path preservation helper missing from package"
grep -Fq 'STREAMFORGE_UPDATE_DATABASE_AUTHORITY_FIRST_V3054' "$SOURCE_DIR/scripts/reset_domain.py" || die "Database-authoritative update access selector missing from package"
grep -Fq 'STREAMFORGE_UPDATE_DATABASE_AUTHORITY_FIRST_V3054' "$SOURCE_UPDATER" || die "Database-authoritative updater access policy missing from package"
grep -Fq 'update-authority "sqlite:///$DB_PATH" "$INSTALLED_VERSION"' "$SOURCE_UPDATER" || die "Updater database access selector call missing from package"
grep -Fq 'streamforge-before-update-access-repair-' "$SOURCE_DIR/scripts/reset_domain.py" || die "v3.0.53 access-regression repair safety backup missing from package"
! sed -n '/^[[:space:]]*# STREAMFORGE_UPDATE_DATABASE_AUTHORITY_FIRST_V3054:/,/^[[:space:]]*# STREAMFORGE_UPDATE_RECONCILE_CURRENT_LOGOS_V3021:/p' "$SOURCE_UPDATER" | grep -F 'database-authority "$CURRENT_PUBLIC_URL"' >/dev/null || die "Updater still replaces database URLs from the environment authority"
grep -Fq 'STREAMFORGE_WEBPLAYER_PERSISTENT_AUTO_RECONNECT_V3055' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player persistent reconnect loop missing from package"
grep -Fq 'STREAMFORGE_WEBPLAYER_PERSISTENT_AUTO_RECONNECT_V3055' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player persistent reconnect loop missing from package"
grep -Fq 'function scheduleReconnect(' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player reconnect scheduler missing from package"
# STREAMFORGE_V1167_NODE_WEBPLAYER_SUPERSEDED_OFFLINE_WATCHDOG_GUARD_FIX:
# v11.63 extended the old error-only interval into the media-progress watchdog.
# Validate the preserved offline retry branch without requiring the obsolete exact interval body.
grep -Fq "toLowerCase()==='error')retrySoon();" "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player offline retry branch missing from package"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_PROGRESS_WATCHDOG_V1163' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player progress watchdog missing from package"
grep -Fq 'STREAMFORGE_CHANNEL_STRICT_HLS_UP_WAITING_V3055' "$SOURCE_DIR/app/main.py" || die "Main strict HLS delivery-state backend missing from package"
grep -Fq 'freshness_window = max(20, segment_time * 8)' "$SOURCE_DIR/app/node_manager.py" || die "Main local HLS freshness gate missing from package"
grep -Fq 'bool(ready and fresh_hls and alive)' "$SOURCE_DIR/node_agent/app.py" || die "Node HLS freshness gate missing from package"
grep -Fq 'STREAMFORGE_CHANNEL_STRICT_HLS_UP_WAITING_V3055' "$SOURCE_DIR/app/static/app.js" || die "Main exact delivery-state filter missing from package"
grep -Fq 'const statusMatched = !state.status || runtimeStatus === state.status;' "$SOURCE_DIR/app/static/app.js" || die "Main paginated exact delivery-state filter logic missing from package"
grep -Fq 'STREAMFORGE_CHANNEL_STRICT_HLS_UP_WAITING_V3055' "$SOURCE_DIR/app/static/style.css" || die "Main Up Waiting Down status styling missing from package"
grep -Fq '<option value="waiting"' "$SOURCE_DIR/app/templates/channels.html" || die "Main Waiting status filter option missing from package"
grep -Fq '<option value="waiting">Waiting</option>' "$SOURCE_DIR/node_agent/app.py" || die "Node Waiting status filter option missing from package"
grep -Fq 'deliveryStatus===statusMode' "$SOURCE_DIR/node_agent/app.py" || die "Node exact Up Waiting Down filter missing from package"
! grep -Fq 'runtime_bitrate > 0 or runtime_uptime >= 2' "$SOURCE_DIR/app/main.py" || die "Main HLS Up state still trusts bitrate or uptime"
grep -Fq 'STREAMFORGE_RESTORE_PRESERVE_SEPARATE_ACCESS_PATHS_V3026' "$SOURCE_DIR/scripts/main_system_control.py" || die "Full restore Panel/Playlist path preservation missing"
grep -Fq 'STREAMFORGE_SHARED_VERIFIED_LOGO_RESTORE_V3026' "$SOURCE_DIR/scripts/main_system_control.py" || die "Shared verified full/logo-only restore pipeline missing"
grep -Fq 'STREAMFORGE_RESTORED_ASSET_CACHE_BUST_V3028' "$SOURCE_DIR/app/main.py" || die "Restored logo cache-bust helper missing"
grep -Fq 'RestoredAssetStaticFiles' "$SOURCE_DIR/app/main.py" || die "Restored logo no-cache static handler missing"
grep -Fq 'channel.logo_url|versioned_asset' "$SOURCE_DIR/app/templates/channels.html" || die "Channels restored-logo versioned URL missing"
grep -Fq 'branding.logo_url|versioned_asset' "$SOURCE_DIR/app/templates/base.html" || die "Main branding restored-logo versioned URL missing"
grep -Fq 'STREAMFORGE_UPDATE_LOGO_SAFETY_SNAPSHOT_V3029' "$SOURCE_DIR/scripts/preserve_update_logos.py" || die "Update logo safety snapshot helper missing"
grep -Fq 'STREAMFORGE_EMPTY_LOGO_PAYLOAD_PRESERVE_V3029' "$SOURCE_DIR/scripts/main_system_control.py" || die "Empty restore logo-payload preservation missing"
grep -Fq 'STREAMFORGE_RESTORE_ENV_SANDBOX_WRITE_V3030' "$SOURCE_DIR/scripts/main_system_control.py" || die "Sandbox-safe environment restore missing"
grep -Fq 'STREAMFORGE_RESTORE_ENV_SANDBOX_WRITE_V3030' "$SOURCE_DIR/deploy/streamforge-main-system.service" || die "Restore service environment sandbox contract missing"
grep -Fq 'if destination == ENV_FILE:' "$SOURCE_DIR/scripts/main_system_control.py" || die "Restore helper still requires a forbidden /etc sibling temporary file"
! sed -n '/^[[:space:]]*# STREAMFORGE_UPDATE_ACCESS_FROM_ENV_V3023/,/^[[:space:]]*# STREAMFORGE_UPDATE_RECONCILE_CURRENT_LOGOS_V3021/p' "$SOURCE_UPDATER" | grep -F '"$DATABASE_URL"' >/dev/null || die "Undefined DATABASE_URL remains in the access-sync block"
grep -Fq 'streamforge-before-domain-reset-' "$SOURCE_DIR/scripts/reset_domain.py" || die "Resetdomain SQLite safety backup missing from package"
grep -Fq '"dns_only": 1' "$SOURCE_DIR/scripts/reset_domain.py" || die "Resetdomain exact Panel role enforcement missing from package"
grep -Fq '"playlist_dns_only": 1' "$SOURCE_DIR/scripts/reset_domain.py" || die "Resetdomain exact Playlist role enforcement missing from package"
grep -Fq 'STREAMFORGE_MAIN_RECOVERY_ALIAS_NO_ROLE_WIDEN_V3031' "$SOURCE_DIR/app/main.py" || die "Main recovery alias still widens saved URL roles"
grep -Fq 'STREAMFORGE_MAIN_AUTOMATIC_ACCESS_POLICY_UI_V3031' "$SOURCE_DIR/app/templates/node_form.html" || die "Automatic Main access policy UI missing"
grep -Fq 'STREAMFORGE_MAIN_AUTOMATIC_RELAY_ACCESS_V3031' "$SOURCE_DIR/app/main.py" || die "Automatic Main relay authority missing"
grep -Fq 'STREAMFORGE_RESTORE_EXACT_ROLE_POLICY_V3031' "$SOURCE_DIR/scripts/main_system_control.py" || die "Full restore exact Main role policy missing"
grep -Fq 'STREAMFORGE_MAIN_EXACT_ALIAS_NO_REDIRECT_V3036' "$SOURCE_DIR/app/main.py" || die "Exact Main alias no-redirect policy missing"
grep -Fq 'STREAMFORGE_UPDATE_ROLE_URL_DECONTAMINATION_V3036' "$SOURCE_DIR/scripts/reset_domain.py" || die "Panel/Playlist URL role decontamination missing"
grep -Fq 'STREAMFORGE_UPDATE_PRESERVE_ALL_ACCESS_URLS_V3038' "$SOURCE_DIR/scripts/reset_domain.py" || die "Update multi-URL preservation missing"
grep -Fq 'STREAMFORGE_PLAYBACK_CHILD_ALIAS_PREFIX_V3036' "$SOURCE_DIR/app/main.py" || die "Playlist child alias-prefix propagation missing"
grep -Fq 'STREAMFORGE_MAIN_REQUEST_MATCHED_PLAYLIST_BASE_V3037' "$SOURCE_DIR/app/main.py" || die "Request-matched Main playlist URL generation missing"
grep -Fq 'STREAMFORGE_NODE_LONGEST_PLAYLIST_ALIAS_V3040' "$SOURCE_DIR/node_agent/app.py" || die "Node longest Playlist/App path-alias matching missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_QUICK_OR_MANUAL_LOGIN_V3041' "$SOURCE_DIR/app/main.py" || die "Main Quick User plus Manual Login backend missing"
grep -Fq 'value="quick"' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Web Player Quick User login mode UI missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_QUICK_MODE_SYNC_V3041' "$SOURCE_DIR/app/node_manager.py" || die "Remote Node Quick User login mode sync missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_QUICK_OR_MANUAL_LOGIN_V3041' "$SOURCE_DIR/node_agent/app.py" || die "Node Quick User plus Manual Login support missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_QUICK_LOGIN_LAYOUT_V3042' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Quick Login option ordering/display-name layout missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_SELECTOR_HIDDEN_V3042' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Manual-mode selected-user visibility fix missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_QUICK_LOGIN_LAYOUT_V3042' "$SOURCE_DIR/node_agent/app.py" || die "Node Quick Login option ordering/display-name layout missing"
grep -Fq 'STREAMFORGE_OFFLINE_NODE_LOCAL_DELETE_V3043' "$SOURCE_DIR/app/main.py" || die "Offline Node local-delete fallback missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_QUICK_CREDENTIAL_STATE_V3043' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Quick Login credential/back state missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_QUICK_CREDENTIAL_STATE_V3043' "$SOURCE_DIR/node_agent/app.py" || die "Node Quick Login credential/back state missing"
grep -Fq 'STREAMFORGE_OFFLINE_NODE_DELETE_PREFLIGHT_V3043' "$SOURCE_UPDATER" || die "Offline Node delete preflight correction missing"
grep -Fq 'STREAMFORGE_CHANNELS_CACHED_NODE_HEALTH_V3044' "$SOURCE_DIR/app/node_manager.py" || die "Channels cached-only Node health support missing"
grep -Fq 'STREAMFORGE_OFFLINE_NODE_STATUS_BACKOFF_V3044' "$SOURCE_DIR/app/node_manager.py" || die "Offline Node status backoff missing"
grep -Fq 'STREAMFORGE_CHANNELS_NO_BLOCKING_VIEWER_FETCH_V3044' "$SOURCE_DIR/app/main.py" || die "Non-blocking Channels viewer aggregation missing"
grep -Fq 'STREAMFORGE_CHANNELS_FAST_OFFLINE_RENDER_V3044' "$SOURCE_DIR/app/main.py" || die "Fast offline Channels initial render missing"
grep -Fq 'STREAMFORGE_OFFLINE_CHANNEL_SETTINGS_QUEUE_V3045' "$SOURCE_DIR/app/node_manager.py" || die "Offline channel-settings queue missing"
grep -Fq 'STREAMFORGE_NODE_RECONNECT_AUTO_SYNC_V3045' "$SOURCE_DIR/app/main.py" || die "Node reconnect auto-sync missing"
grep -Fq 'STREAMFORGE_OFFLINE_CHANNEL_EDIT_SAVE_V3045' "$SOURCE_DIR/app/main.py" || die "Offline channel edit save path missing"
grep -Fq 'STREAMFORGE_OFFLINE_BULK_PROFILE_QUEUE_V3045' "$SOURCE_DIR/app/main.py" || die "Offline bulk profile queue missing"
grep -Fq 'Test &amp; Sync' "$SOURCE_DIR/app/templates/nodes.html" || die "Node Test and Sync control missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_DOWNLOAD_MANAGE_UI_V3045' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Web Player download management UI missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_AUTHENTICATED_DOWNLOAD_V3045' "$SOURCE_DIR/app/main.py" || die "Main Web Player authenticated download missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_DOWNLOAD_NODE_SYNC_V3045' "$SOURCE_DIR/app/node_manager.py" || die "Managed download Node sync missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_SYNC_V3045' "$SOURCE_DIR/node_agent/app.py" || die "Node managed download receiver missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_AUTHENTICATED_DOWNLOAD_V3045' "$SOURCE_DIR/node_agent/app.py" || die "Node authenticated Web Player download missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_INFO_DOWNLOAD_V3046' "$SOURCE_DIR/app/templates/player.html" || die "Main watch-page Download info-row layout missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_INFO_DOWNLOAD_V3046' "$SOURCE_DIR/node_agent/app.py" || die "Node watch-page Download info-row layout missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_500_GUARD_V3046' "$SOURCE_DIR/app/main.py" || die "Bulk encoding-profile error boundary missing"
grep -Fq 'STREAMFORGE_MAIN_INTERACTIVE_POINTER_CURSOR_V3047' "$SOURCE_DIR/app/static/style.css" || die "Main Panel interactive pointer cursor policy missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_INTERACTIVE_POINTER_V3047' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player interactive pointer cursor policy missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_INTERACTIVE_POINTER_V3047' "$SOURCE_DIR/node_agent/app.py" || die "Node Panel interactive pointer cursor policy missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_INTERACTIVE_POINTER_V3047' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player interactive pointer cursor policy missing"
grep -Fq 'STREAMFORGE_NODE_WATCH_INTERACTIVE_POINTER_V3047' "$SOURCE_DIR/node_agent/app.py" || die "Node watch-page interactive pointer cursor policy missing"
grep -Fq 'STREAMFORGE_NEW_NODE_FAVICON_STATE_RESET_V3048' "$SOURCE_DIR/app/main.py" || die "New Node favicon state reset missing"
grep -Fq 'STREAMFORGE_NODE_DELETE_FAVICON_STATE_CLEANUP_V3048' "$SOURCE_DIR/app/main.py" || die "Deleted Node favicon state cleanup missing"
grep -Fq 'STREAMFORGE_STALE_NODE_FAVICON_SELF_HEAL_V3048' "$SOURCE_DIR/app/node_manager.py" || die "Stale Node favicon self-heal missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_500_GUARD_V3048' "$SOURCE_DIR/app/main.py" || die "Bulk Node assignment error boundary missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_MOBILE_INFO_LAYOUT_V3049' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player mobile information layout missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_MOBILE_INFO_LAYOUT_V3049' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player mobile information layout missing"
grep -Fq 'STREAMFORGE_OFFLINE_CHANNEL_CONTROL_FAST_QUEUE_V3050' "$SOURCE_DIR/app/node_manager.py" || die "Offline channel-control fast queue missing"
grep -Fq 'STREAMFORGE_OFFLINE_BULK_ACTION_FAST_QUEUE_V3050' "$SOURCE_DIR/app/node_manager.py" || die "Offline bulk removal fast queue missing"
grep -Fq 'STREAMFORGE_OFFLINE_RUNTIME_SNAPSHOT_SHORT_CIRCUIT_V3050' "$SOURCE_DIR/app/node_manager.py" || die "Offline runtime snapshot short-circuit missing"
python3 - "$SOURCE_DIR/app/main.py" <<'PY_GUARD' || die "Bulk database-only live-state policy missing"
import sys
from pathlib import Path
s = Path(sys.argv[1]).read_text()
start = s.find('if action in {"relay_set", "relay_clear"}:')
end = s.find('if action in {"nodes_add", "nodes_remove", "nodes_set"}:', start + 1)
if start < 0 or end < 0:
    raise SystemExit(1)
block = s[start:end]
required = [
    'STREAMFORGE_BULK_RELAY_DB_ONLY_LIVE_STATE_V1138',
    'mark_node_channel_sync_pending(',
    'STREAMFORGE_BULK_RELAY_RESPONSE_FIRST_V1137',
    'db.commit()',
]
for item in required:
    if item not in block:
        raise SystemExit(1)
for forbidden in ('runtime_snapshot(', 'is_running(', 'channel_runtime(', 'sync_channel_to_nodes('):
    if forbidden in block:
        raise SystemExit(1)
PY_GUARD
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_MOBILE_HEADER_LAYOUT_V3051' "$SOURCE_DIR/app/templates/web_player.html" || die "Main signed-in Web Player mobile header layout missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_MOBILE_HEADER_LAYOUT_V3051' "$SOURCE_DIR/node_agent/app.py" || die "Node signed-in Web Player mobile header layout missing"
grep -Fq 'STREAMFORGE_DASHBOARD_FAST_RENDER_V3052' "$SOURCE_DIR/app/main.py" || die "Fast Dashboard render policy missing"
grep -Fq 'STREAMFORGE_MAIN_HOT_VIEWER_COUNTS_V114' "$SOURCE_DIR/app/main.py" || die "Dashboard/Channels cache-only viewer aggregation missing"
grep -Fq '.where(func.lower(Channel.status).in_(dashboard_live_statuses))' "$SOURCE_DIR/app/main.py" || die "Dashboard live-channel-only query missing"
grep -Fq 'STREAMFORGE_GOOGLE_BACKUP_CARD_LAYOUT_V3053' "$SOURCE_DIR/app/templates/backups.html" || die "Google Drive backup card layout missing"
grep -Fq 'grid-column:1/-1!important;' "$SOURCE_DIR/app/templates/backups.html" || die "Google Drive full-width grid rule missing"
grep -Fq '.google-backup-card[hidden]{display:none!important}' "$SOURCE_DIR/app/templates/backups.html" || die "Google Drive hidden-state layout guard missing"
grep -Fq 'STREAMFORGE_UPDATE_QUIET_AUTHORITY_SYNC_V3037' "$SOURCE_UPDATER" || die "Quiet updater authority synchronization missing"
grep -Fq '"main-unmatched-link-silent-drop"' "$SOURCE_DIR/app/main.py" || die "Unmatched Main link silent-drop response missing"
! grep -Fq 'Local relay hostname/IP' "$SOURCE_DIR/app/templates/node_form.html" || die "Removed Local relay hostname/IP option remains"
! grep -Fq 'Relay scheme' "$SOURCE_DIR/app/templates/node_form.html" || die "Removed Relay scheme option remains"
! grep -Fq 'Allow only configured Main access hostnames' "$SOURCE_DIR/app/templates/node_form.html" || die "Removed Main hostname-lock option remains"
grep -Fq 'STREAMFORGE_PUBLIC_BASE_URL' "$SOURCE_DIR/scripts/reset_domain.py" || die "Resetdomain public environment replacement missing from package"
grep -Fq 'STREAMFORGE_APPLY_MAIN_ACCESS_BIN:-/usr/local/sbin/streamforge-apply-main-access' "$SOURCE_DIR/scripts/streamforge" || die "Resetdomain Nginx regeneration missing from package"
grep -Fq '"$SYSTEMCTL_BIN" restart streamforge' "$SOURCE_DIR/scripts/streamforge" || die "Resetdomain Main restart missing from package"
grep -Fq 'PYTHONPATH="$APP_DIR"' "$SOURCE_DIR/scripts/streamforge" || die "Resetuser Python import path fix missing from package"
grep -Fq 'STREAMFORGE_DATABASE_URL="$DATABASE_URL"' "$SOURCE_DIR/scripts/streamforge" || die "Resetuser database selection missing from package"
grep -Fq 'STREAMFORGE_RESETUSER_COMMAND_INSTALL_V3015' "$SOURCE_DIR/scripts/install.sh" || die "Fresh-install resetuser command setup missing from package"
grep -Fq 'install -m 0755 "$APP_DIR/scripts/streamforge" /usr/local/bin/streamforge' "$SOURCE_DIR/scripts/install.sh" || die "Fresh-install resetuser command install line missing from package"
grep -Fq 'STREAMFORGE_RESETUSER_COMMAND_UNINSTALL_V3015' "$SOURCE_DIR/scripts/uninstall.sh" || die "Resetuser command uninstall cleanup missing from package"
grep -Fq '[[ -f /usr/local/bin/streamforge ]] && cp -a /usr/local/bin/streamforge "$BACKUP_DIR/streamforge-command"' "$SOURCE_UPDATER" || die "Resetuser command rollback backup missing from package"
grep -Fq 'install -o root -g root -m 0755 "$BACKUP_DIR/streamforge-command" /usr/local/bin/streamforge' "$SOURCE_UPDATER" || die "Resetuser command rollback restore missing from package"
grep -Fq 'chmod 0755 "$APP_DIR/scripts/update_geoip_databases.sh"' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install GeoIP updater in-place permission fix missing from package"
grep -Fq 'STREAMFORGE_GEOIP_INHERITED_SECRET_PERMISSION_SAFE_V99R6' "$SOURCE_DIR/scripts/update_geoip_databases.sh" || die "v9.9 r6 GeoIP root-env permission-safe credential loader missing"
grep -Fq 'STREAMFORGE_GEOIPUPDATE_DEPENDENCY_V99R7' "$SOURCE_DIR/scripts/update_geoip_databases.sh" || die "v9.9 r7 GeoIP updater dependency guard missing"
grep -Fq 'certbot dnsutils geoipupdate' "$SOURCE_DIR/scripts/install.sh" || die "v9.9 r7 fresh Main geoipupdate dependency missing"
grep -Fq 'STREAMFORGE_GEOIPUPDATE_AUTO_INSTALL_V99R7' "$SOURCE_UPDATER" || die "v9.9 r7 existing Main geoipupdate installer missing"
! grep -Fq 'install -m 0755 "$APP_DIR/scripts/update_geoip_databases.sh" /opt/streamforge/scripts/update_geoip_databases.sh' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install still copies the GeoIP updater onto itself"
grep -Fq 'install -d -o root -g root -m 0750 /var/backups/streamforge' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install restore backup root missing from package"
grep -Fq 'client_max_body_size 1g;' "$SOURCE_DIR/deploy/nginx.conf" || die "Main Nginx backup upload capacity missing from package"
grep -Fq 'client_max_body_size 1g;' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Dynamic Main Nginx backup upload capacity missing from package"
# STREAMFORGE_FRESH_INSTALL_DEPENDENCY_GUARD_V1031: package order can change as new runtime dependencies are inserted.
# Validate the actual required packages/isolated-Certbot installer instead of a brittle historical apt command string.
grep -Eq 'apt-get install -y.*[[:space:]]smbclient([[:space:]]|$)' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install SMB backup dependency missing from package"
grep -Eq 'apt-get install -y.*[[:space:]]python3-venv([[:space:]]|$)' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install isolated Certbot venv dependency missing from package"
grep -Fq 'STREAMFORGE_MAIN_ISOLATED_CERTBOT_INSTALL_V1030' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install isolated Certbot bootstrap missing from package"
grep -Fq 'runuser -u streamforge -- env TMPDIR=' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install service-user execution path missing from package"
grep -Fq 'STREAMFORGE_FAILBACK_PROBE_TIMEOUT=5' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install failback timeout config missing from package"
grep -Fq 'STREAMFORGE_LOG_PAGE_LIMIT=500' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install log page config missing from package"
grep -Fq 'STREAMFORGE_VIEWER_KEY_TTL_SECONDS=43200' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install viewer key TTL config missing from package"
grep -Fq 'STREAMFORGE_RESTREAM_KEY_TTL_SECONDS=86400' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install restream key TTL config missing from package"
grep -Fq 'verify_main_install.py' "$SOURCE_DIR/scripts/install.sh" || die "Fresh install verifier hook missing from package"
grep -Fq 'missing DB columns in' "$SOURCE_DIR/scripts/verify_main_install.py" || die "Fresh install DB column verifier missing from package"
grep -Fq 'self._active_total=0' "$SOURCE_DIR/app/main.py" || die "Main O(1) connection counter missing from package"
grep -Fq 'self._session_index' "$SOURCE_DIR/app/viewer_tracking.py" || die "Main O(1) viewer index missing from package"
grep -Fq '_schedule_shared_viewers_save' "$SOURCE_DIR/node_agent/app.py" || die "Node batched viewer persistence missing from package"
grep -Fq -- '--backlog 65535' "$SOURCE_DIR/deploy/streamforge.service" || die "Main Gunicorn backlog tuning missing from package"
grep -Fq -- '--backlog 65535' "$SOURCE_DIR/node_agent/deploy/streamforge-node.service" || die "Node Gunicorn backlog tuning missing from package"
grep -Fq 'upstream streamforge_main_backend' "$SOURCE_DIR/deploy/nginx.conf" || die "Nginx upstream keepalive missing from package"
grep -Fq 'worker_connections 65535;' "$SOURCE_DIR/scripts/tune_high_concurrency.py" || die "Nginx worker concurrency tuner missing from package"
grep -Fq 'StreamForge High-Concurrency Readiness Audit' "$SOURCE_DIR/scripts/capacity_audit.py" || die "Capacity audit utility missing from package"
grep -Fq 'has_keepalive_main_proxy' "$SOURCE_DIR/scripts/verify_main_install.py" || die "Keepalive-aware Nginx verifier missing from package"
grep -Fq 'def _prune_target_backup_archives(target_id: str, keep: int = 20)' "$SOURCE_DIR/app/backup_manager.py" || die "Per-target backup rotation helper missing from package"
grep -Fq 'rotation = _prune_target_backup_archives(target_id, keep=keep)' "$SOURCE_DIR/app/backup_manager.py" || die "Per-target backup rotation call missing from package"
grep -Fq 'STREAMFORGE_STRICT_MAX_CONNECTIONS' "$SOURCE_DIR/app/main.py" || die "Main strict max-connections enforcement missing from package"
grep -Fq 'STREAMFORGE_RESTREAM_MAX_CONNECTIONS' "$SOURCE_DIR/app/main.py" || die "Restream max-connections enforcement missing from package"
grep -Fq 'def __init__(self, ttl_seconds: int = 5)' "$SOURCE_DIR/app/main.py" || die "Main connection session timeout is not 5 seconds"
grep -Fq 'const syncCanvasSize=(canvas)' "$SOURCE_DIR/app/static/dashboard.js" || die "Responsive dashboard canvas sizing missing from package"
grep -Fq 'const syncNodeCanvasSize=(canvas)' "$SOURCE_DIR/node_agent/app.py" || die "Responsive Node dashboard canvas sizing missing from package"
grep -Fq 'BRANDING_FAVICON_KEY = "branding_favicon"' "$SOURCE_DIR/app/main.py" || die "Main favicon setting missing from package"
grep -Fq 'Current favicon' "$SOURCE_DIR/app/templates/system_branding.html" || die "Main favicon preview missing from package"
grep -Fq '{% if not node %}' "$SOURCE_DIR/app/templates/node_form.html" || die "Node logo URL add-only guard missing from package"
grep -Fq '<input type="hidden" name="logo_url" value="">' "$SOURCE_DIR/app/templates/node_form.html" || die "Node edit hidden logo_url compatibility field missing from package"
grep -Fq 'node-branding-inline' "$SOURCE_DIR/app/templates/node_form.html" || die "Node branding four-item inline layout missing from package"
grep -Fq 'node-connection-compact-row' "$SOURCE_DIR/app/templates/node_form.html" || die "Compact Node connection row missing from package"
grep -Fq 'bulk-selected-primary' "$SOURCE_DIR/app/templates/channels.html" || die "Channels selected-action layout wrapper missing from package"
grep -Fq 'STREAMFORGE_NODE_LOGO_ROOT", "/opt/streamforge-node/logo"' "$SOURCE_DIR/node_agent/app.py" || die "Canonical Node logo path missing from package"
grep -Fq 'STREAMFORGE_NODE_LOGO_PATH_V2180' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "Node logo legacy migration missing from package"
grep -Fq 'Usage:' "$SOURCE_DIR/scripts/uninstall.sh" || die "Unified uninstall script missing from package"
grep -Fq 'remove_main(){' "$SOURCE_DIR/scripts/uninstall.sh" || die "Main uninstall cleanup missing from package"
grep -Fq 'remove_node(){' "$SOURCE_DIR/scripts/uninstall.sh" || die "Node uninstall cleanup missing from package"
grep -Fq 'def uninstall_node_over_ssh(' "$SOURCE_DIR/app/ssh_installer.py" || die "Remote Node SSH uninstall helper missing from package"
# STREAMFORGE_OFFLINE_NODE_DELETE_PREFLIGHT_V3043:
# Remote uninstall remains available when the Node is reachable, but it is no
# longer allowed to block removal of an offline Node from Main.
grep -Fq 'STREAMFORGE_OFFLINE_NODE_LOCAL_DELETE_V3043' "$SOURCE_DIR/app/main.py" || die "Offline Node local-delete fallback missing from package"
grep -Fq 'Node deleted from Main and Remote Node server cleaned' "$SOURCE_DIR/app/main.py" || die "Reachable Node cleanup success message missing from package"
grep -Fq 'Remote Node cleanup was skipped (offline or SSH unavailable)' "$SOURCE_DIR/app/main.py" || die "Offline Node cleanup-skip result missing from package"
! grep -Fq 'Node delete requires a saved SSH password' "$SOURCE_DIR/app/main.py" || die "Stale saved-SSH delete blocker remains in package"
! grep -Fq 'Node was not deleted because Remote Node cleanup failed' "$SOURCE_DIR/app/main.py" || die "Stale remote-cleanup delete blocker remains in package"
grep -Fq '@app.get("/web-player", response_class=HTMLResponse)' "$SOURCE_DIR/app/main.py" || die "Main playlist web player route missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_LOGIN_CENTER_V2200' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player centered login UI missing from package"
grep -Fq 'branding and branding.logo_url' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player branding logo support missing from package"
grep -Fq 'class="brand brand--login"' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player centered branding markup missing from package"
! grep -Fq 'Browser Web Player' "$SOURCE_DIR/app/templates/web_player.html" || die "Removed Main Web Player subtitle returned in package"
! grep -Fq 'Use the same playlist/Xtream username and password.' "$SOURCE_DIR/app/templates/web_player.html" || die "Removed Main Web Player helper text returned in package"

grep -Fq 'STREAMFORGE_WEB_PLAYER_LOGIN_CENTER_V2200' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player centered login marker missing from package"
grep -Fq 'class="{% if not user %}login-screen{% endif %}"' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player login-screen centering missing from package"
! grep -Fq 'Use the same playlist/Xtream username and password.' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player helper text should not exist in package"
! grep -Fq '>Browser Web Player<' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player subtitle should not exist in package"
grep -Fq 'stripped == "/" and "playlist" in roles' "$SOURCE_DIR/app/main.py" || die "Main playlist-root player mapping missing from package"
grep -Fq 'if stream_match and stream_path == "/":' "$SOURCE_DIR/node_agent/app.py" || die "Node playlist-root player mapping missing from package"
grep -Fq 'def node_web_player(' "$SOURCE_DIR/node_agent/app.py" || die "Node web player UI missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_LOGIN_CENTER_V2200' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player centered login marker missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_LOGO_ROUTE_V2202' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player logo route fix missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_FAVICON_LINK_V2203' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player favicon link fix missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_FAVICON_ROUTE_V2203' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player favicon route fix missing from package"
grep -Fq 'STREAMFORGE_SAFE_UPDATE_MIGRATION_POLICY_V2204' "$SOURCE_UPDATER" || die "Safe update migration policy missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_MULTI_CATEGORY_V2205' "$SOURCE_DIR/app/main.py" || die "Main Web Player multi-category backend missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_CATEGORY_CHIPS_V2205' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player category chips UI missing from package"
grep -Fq 'data-categories=' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player multi-category card metadata missing from package"
grep -Fq 'data-category-chip' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player category chip controls missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_CATEGORY_CHIPS_V2205' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player multi-category chip filter missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_WATCH_SIDEBAR_V2206' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player watch sidebar UI missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_WATCH_SIDEBAR_V2206' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player watch sidebar UI missing from package"
grep -Fq 'watch_prefix' "$SOURCE_DIR/app/main.py" || die "Main Web Player watch sidebar context missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_CINEMA_LAYOUT_V2207' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player cinema watch layout missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_CINEMA_LAYOUT_V2207' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player cinema watch layout missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_FULL_WIDTH_V2208' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player full-width layout missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_NO_VIDEO_OVERLAY_V2209' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player video overlay removal missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_NO_VIDEO_OVERLAY_V2209' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player video overlay removal missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_CLEAN_INFO_V2210' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player clean info layout missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_CLEAN_INFO_VALIDATOR_V2211' "$SOURCE_UPDATER" || die "Clean-info post-deploy validator fix missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_VIEWER_INFO_V2212' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player username/expiry UI missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_USER_EXPIRY_V2212' "$SOURCE_DIR/app/main.py" || die "Main Web Player username/expiry context missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_USER_EXPIRY_V2212' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player username/expiry data missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_CARD_RESPONSIVE_V2213' "$SOURCE_DIR/app/templates/player.html" || die "Main responsive channel-card fix missing from package"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_CARD_RESPONSIVE_V2213' "$SOURCE_DIR/node_agent/app.py" || die "Node responsive channel-card fix missing from package"
grep -Fq 'STREAMFORGE_PLAYER_INFO_LOGO_CONTAIN_V2214' "$SOURCE_DIR/app/templates/player.html" || die "Main current-channel logo contain fix missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYER_INFO_LOGO_CONTAIN_V2214' "$SOURCE_DIR/node_agent/app.py" || die "Node current-channel logo contain fix missing from package"
grep -Fq 'STREAMFORGE_PLAYER_CARD_GRID_STABLE_V2214' "$SOURCE_DIR/app/templates/player.html" || die "Main stable channel-card grid missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYER_CARD_GRID_STABLE_V2214' "$SOURCE_DIR/node_agent/app.py" || die "Node stable channel-card grid missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_NAME_CENTER_V2216' "$SOURCE_DIR/app/templates/player.html" || die "Main channel-name center alignment missing from package"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_NAME_CENTER_V2216' "$SOURCE_DIR/node_agent/app.py" || die "Node channel-name center alignment missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_PANEL_HEIGHT_V2218' "$SOURCE_DIR/app/templates/player.html" || die "Main channel panel height fix missing from package"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_PANEL_HEIGHT_V2218' "$SOURCE_DIR/node_agent/app.py" || die "Node channel panel height fix missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_NORMAL_LAST_ROW_V2218' "$SOURCE_DIR/app/templates/player.html" || die "Main normal last-row sizing missing from package"
grep -Fq 'STREAMFORGE_NODE_NORMAL_LAST_ROW_V2218' "$SOURCE_DIR/node_agent/app.py" || die "Node normal last-row sizing missing from package"
grep -Fq 'STREAMFORGE_WEB_PLAYER_PANEL_HEIGHT_SYNC_V2218' "$SOURCE_DIR/app/templates/player.html" || die "Main panel-height sync missing from package"
grep -Fq 'STREAMFORGE_NODE_PANEL_HEIGHT_SYNC_V2218' "$SOURCE_DIR/node_agent/app.py" || die "Node panel-height sync missing from package"
grep -Fq 'STREAMFORGE_WEBPLAYER_MANAGE_V2219' "$SOURCE_DIR/app/main.py" || die "Main Web Player Manage settings missing from package"
grep -Fq 'Web Player Manage' "$SOURCE_DIR/app/templates/nodes.html" || die "Nodes Web Player Manage action missing from package"
grep -Fq 'Show Username and Expire under the player' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Web Player viewer-info toggle missing from package"
grep -Fq 'name="accent_color"' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Web Player accent color control missing from package"
grep -Fq '"webplayer_settings": settings' "$SOURCE_DIR/app/main.py" || die "Main Web Player settings context missing from package"
grep -Fq 'webplayer_settings: dict[str, Any] | None = None' "$SOURCE_DIR/app/node_manager.py" || die "Remote Web Player settings sync support missing from package"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_MANAGE_V2219' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player Manage payload missing from package"
grep -Fq 'webplayer_show_user_info' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player user-info visibility support missing from package"
grep -Fq '_node_webplayer_theme_css' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player theme support missing from package"
grep -Fq 'page_alpha' "$SOURCE_DIR/app/main.py" || die "Main Web Player transparency settings missing from package"
grep -Fq 'data-alpha="page_alpha"' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Page transparency slider missing from package"
grep -Fq 'data-alpha="panel_alpha"' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Panel transparency slider missing from package"
grep -Fq 'data-alpha="accent_alpha"' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Accent transparency slider missing from package"
grep -Fq 'data-alpha="text_alpha"' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Text transparency slider missing from package"
grep -Fq 'webplayer_page_alpha' "$SOURCE_DIR/app/node_manager.py" || die "Remote Web Player transparency sync missing from package"
grep -Fq 'webplayer_page_alpha: int | None = None' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player transparency payload missing from package"
grep -Fq '_webplayer_rgba' "$SOURCE_DIR/node_agent/app.py" || die "Node RGBA conversion missing from package"
grep -Fq 'STREAMFORGE_MAIN_CATEGORY_GLOW_FIX_V2221' "$SOURCE_DIR/app/templates/player.html" || die "Main watch category glow fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_CATEGORY_GLOW_FIX_V2221' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player category glow fix missing from package"
grep -Fq 'STREAMFORGE_NODE_CATEGORY_GLOW_FIX_V2221' "$SOURCE_DIR/node_agent/app.py" || die "Node watch category glow fix missing from package"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_CATEGORY_GLOW_FIX_V2221' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player category glow fix missing from package"
grep -Fq 'STREAMFORGE_WEBPLAYER_LOGIN_MODE_V2222' "$SOURCE_DIR/app/main.py" || die "Main Web Player login mode support missing from package"
grep -Fq 'Only active users created for this Remote Node are listed. Main Server users are not shown.' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Remote Node Auto Login user scope UI missing from package"
grep -Fq 'Manual Login' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Manual Web Player login option missing from package"
grep -Fq 'Auto Login' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Auto Web Player login option missing from package"
grep -Fq 'webplayer_login_mode' "$SOURCE_DIR/app/node_manager.py" || die "Remote Web Player login mode sync missing from package"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_LOGIN_MODE_V2222' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player auto/manual login support missing from package"
grep -Fq 'STREAMFORGE_NODE_AUTO_LOGIN_REMOTE_USER_LOAD_V2224' "$SOURCE_DIR/app/main.py" || die "Remote Node Auto Login user loader missing from package"
grep -Fq 'webplayer_users_on_node' "$SOURCE_DIR/app/node_manager.py" || die "Remote Node Web Player user fetch client missing from package"
grep -Fq '/api/v1/webplayer/users' "$SOURCE_DIR/app/node_manager.py" || die "Remote Node Web Player user API client missing from package"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_USER_LIST_API_V2224' "$SOURCE_DIR/node_agent/app.py" || die "Remote Node Web Player user list API missing from package"
grep -Fq 'STREAMFORGE_NODE_AUTO_LOGIN_TOKEN_V2224' "$SOURCE_DIR/node_agent/app.py" || die "Remote Node token Auto Login missing from package"
grep -Fq 'STREAMFORGE_NODE_AUTO_LOGIN_ROOT_V2225' "$SOURCE_DIR/node_agent/app.py" || die "Node Playlist root Auto Login resolver fix missing from package"
grep -Fq 'STREAMFORGE_AUTO_LOGIN_HIDE_SIGNOUT_V2226' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Auto Login Sign out visibility fix missing from package"
grep -Fq 'webplayer_settings.login_mode != "auto"' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Auto Login Sign out condition missing from package"
grep -Fq 'STREAMFORGE_NODE_AUTO_LOGIN_HIDE_SIGNOUT_V2226' "$SOURCE_DIR/node_agent/app.py" || die "Node Auto Login Sign out visibility fix missing from package"
grep -Fq 'STREAMFORGE_NODE_WATCH_TEMPLATE_FORMAT_V2227' "$SOURCE_DIR/node_agent/app.py" || die "Node watch template format fix missing from package"
grep -Fq 'STREAMFORGE_NODE_PORTAL_THEME_APPLY_V2228' "$SOURCE_DIR/node_agent/app.py" || die "Node authenticated Web Player color theme missing from package"
grep -Fq 'STREAMFORGE_WEBPLAYER_THEME_ALWAYS_SYNC_V2229' "$SOURCE_DIR/app/main.py" || die "Main Web Player always-sync core missing from package"
grep -Fq 'webplayer_settings=webplayer_sync_settings' "$SOURCE_DIR/app/main.py" || die "Registry sync does not include Web Player settings"
grep -Fq 'access policy + Web Player theme synced' "$SOURCE_DIR/app/main.py" || die "Node update Web Player resync missing from package"
grep -Fq 'STREAMFORGE_WEBPLAYER_THEME_SYNC_VERIFY_V2229' "$SOURCE_DIR/app/node_manager.py" || die "Web Player sync verification missing from package"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_THEME_CORE_V2229' "$SOURCE_DIR/app/templates/web_player.html" || die "Main login/channel Web Player theme core missing from package"
grep -Fq 'STREAMFORGE_MAIN_WATCH_THEME_CORE_V2229' "$SOURCE_DIR/app/templates/player.html" || die "Main player-page theme core missing from package"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_THEME_CORE_V2229' "$SOURCE_DIR/node_agent/app.py" || die "Node login/channel Web Player theme core missing from package"
grep -Fq 'STREAMFORGE_MAIN_VISUAL_BALANCE_V2235' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player visual-balance theme missing from package"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_ACTIVE_CATEGORY_CLARITY_V2237' "$SOURCE_DIR/app/templates/player.html" || die "Main player active-category clarity fix missing from package"
grep -Fq 'STREAMFORGE_WEBPLAYER_CONNECTION_INFO_SETTING_V2238' "$SOURCE_DIR/app/main.py" || die "Web Player connection-info setting missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_HEADER_VIEWER_INFO_V2239' "$SOURCE_DIR/app/main.py" || die "Main channel-page header viewer data missing from package"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_INFO_GROUPS_V2240' "$SOURCE_DIR/app/templates/player.html" || die "Main player separate viewer/connection groups missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_HEADER_INFO_GROUPS_V2241' "$SOURCE_DIR/app/templates/web_player.html" || die "Main channel header grouped viewer info missing from package"
grep -Fq 'STREAMFORGE_PANEL_RUNTIME_PREFIX_FIX_V2242' "$SOURCE_DIR/app/static/app.js" || die "Panel runtime prefix fix missing from package"
grep -Fq 'STREAMFORGE_CHANNEL_STREAM_LOG_PREFIX_FIX_V2244' "$SOURCE_DIR/app/static/app.js" || die "Channel Stream Logs Panel/API prefix fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_LOGS_LOCAL_ONLY_V65R9' "$SOURCE_DIR/app/main.py" || die "Main local-only channel-log scope missing from package"
grep -Fq 'STREAMFORGE_DASHBOARD_LIVE_CHANNEL_ID_ORDER_V65R10' "$SOURCE_DIR/app/main.py" || die "Dashboard Channel-ID order missing from package"
grep -Fq 'STREAMFORGE_CHANNEL_TABLE_SORT_V65R10' "$SOURCE_DIR/app/static/app.js" || die "Main Channels clickable table sorting missing from package"
grep -Fq 'STREAMFORGE_USER_ZERO_UNLIMITED_V2245' "$SOURCE_DIR/app/main.py" || die "Main per-user 0=unlimited enforcement missing from package"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_LIVE_ONLINE_V2246' "$SOURCE_DIR/app/main.py" || die "Main Web Player live Online Use endpoint missing from package"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_LIVE_ONLINE_V2246' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player live Online Use endpoint missing from package"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_HEADER_LIVE_ONLINE_V2246' "$SOURCE_DIR/app/templates/web_player.html" || die "Main channel-page live Online Use refresh missing from package"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_LIVE_ONLINE_V2246' "$SOURCE_DIR/app/templates/player.html" || die "Main player live Online Use refresh missing from package"
grep -Fq 'STREAMFORGE_MAIN_CATEGORY_CHANNEL_PANEL_V2247' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player category/channel unified panel missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_ACTION_AJAX_V2250' "$SOURCE_DIR/app/main.py" || die "Main channel action AJAX response support missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_ACTION_NO_RELOAD_V2250' "$SOURCE_DIR/app/static/app.js" || die "Main Channels no-reload action handler missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_ACTION_BUTTON_STATE_V2251' "$SOURCE_DIR/app/templates/channels.html" || die "Main channel Start/Stop DOM state controls missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_ACTION_VISIBILITY_FIX_V2252' "$SOURCE_DIR/app/templates/channels.html" || die "Main Start/Stop initial visibility fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_ACTION_DISPLAY_SYNC_V2252' "$SOURCE_DIR/app/static/app.js" || die "Main Start/Stop live display sync missing from package"
grep -Fq 'form.style.display = shouldShow' "$SOURCE_DIR/app/static/app.js" || die "Main Start/Stop explicit display update missing"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_ACTION_VISIBILITY_FIX_V2252' "$SOURCE_DIR/node_agent/app.py" || die "Node Start/Stop visibility fix missing from package"
grep -Fq 'form.style.display=show' "$SOURCE_DIR/node_agent/app.py" || die "Node Start/Stop explicit display update missing"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_PAGE_THEME_SYNC_V2252' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player channel-page managed color sync missing"
grep -Fq '.category-chip,.channel-library,.header-info-group' "$SOURCE_DIR/node_agent/app.py" || die "Node category/channel and header info boxes not included in managed panel theme"
grep -Fq 'STREAMFORGE_NODE_PLAYER_INFO_THEME_SYNC_V2252' "$SOURCE_DIR/node_agent/app.py" || die "Node player user/session box managed theme sync missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_INFO_THEME_SYNC_V2253' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player channel-page user/session theme sync missing from package"
grep -Fq '.channel-library,.header-info-group{background:var(--sf-panel)' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player header info boxes are not using managed panel color"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_INFO_THEME_SYNC_V2253' "$SOURCE_DIR/app/templates/player.html" || die "Main player user/session theme sync missing from package"
grep -Fq 'STREAMFORGE_SESSION_HOVER_SETTINGS_V2254' "$SOURCE_DIR/app/main.py" || die "Online session/hover settings missing from Main package"
grep -Fq 'viewer_session_timeout_seconds' "$SOURCE_DIR/app/templates/system_branding.html" || die "Online session timeout Settings field missing"
grep -Fq 'hide_panel_hover_urls' "$SOURCE_DIR/app/templates/system_branding.html" || die "Hide hover URLs Settings toggle missing"
grep -Fq 'STREAMFORGE_PANEL_USER_SELF_PASSWORD_V2254' "$SOURCE_DIR/app/main.py" || die "Panel-user self password change route missing"
grep -Fq 'STREAMFORGE_MAIN_PASSWORD_PREFIX_FIX_V2255' "$SOURCE_DIR/app/main.py" || die "Main password prefix fix missing"
grep -Fq "'/account','/login','/logout'" "$SOURCE_DIR/app/static/panel_nav.js" || die "Main account/login/logout paths are not panel-prefix aware"
grep -Fq "scope.querySelectorAll('a[href],a[data-streamforge-nav-url],a[data-sf-nav-url]').forEach(patchLink)" "$SOURCE_DIR/app/static/panel_nav.js" || die "Main hidden-route anchor patcher does not cover all panel links"
grep -Fq 'STREAMFORGE_NODE_PANEL_SELF_PASSWORD_V2255' "$SOURCE_DIR/app/main.py" || die "Main Node live password-change authorization missing"
grep -Fq 'STREAMFORGE_NODE_CHANGE_PASSWORD_ROUTE_V2255' "$SOURCE_DIR/node_agent/app.py" || die "Node self password route missing"
grep -Fq 'STREAMFORGE_NODE_VIEWER_TTL_SETTING_V2256' "$SOURCE_DIR/app/main.py" || die "Per-Node viewer session timeout setting support missing"
grep -Fq 'STREAMFORGE_NODE_VIEWER_TTL_EDIT_UI_V2256' "$SOURCE_DIR/app/templates/node_form.html" || die "Node Edit online session timeout field missing"
grep -Fq 'name="node_viewer_session_timeout_seconds"' "$SOURCE_DIR/app/templates/node_form.html" || die "Node Edit viewer timeout form field missing"
grep -Fq '_node_viewer_ttl_setting_key(node.id)' "$SOURCE_DIR/app/main.py" || die "Node-specific viewer timeout save missing"
grep -Fq 'STREAMFORGE_RBAC_SPLIT_MIGRATION_V2257' "$SOURCE_DIR/app/main.py" || die "RBAC legacy permission split migration missing"
grep -Fq 'STREAMFORGE_SUPER_ADMIN_RBAC_GUARD_V2257' "$SOURCE_DIR/app/main.py" || die "Super Admin visibility/access guard missing"
grep -Fq 'STREAMFORGE_ALL_GRANULAR_PERMISSION_MIGRATION_V2258' "$SOURCE_DIR/app/main.py" || die "All-permission granular migration missing"
grep -Fq 'STREAMFORGE_ROLE_PERMISSION_CEILING_V2259' "$SOURCE_DIR/app/main.py" || die "Role permission ceiling helper missing"
grep -Fq 'STREAMFORGE_ROLE_PERMISSION_NAME_FIX_V2261' "$SOURCE_DIR/app/main.py" || die "Role permission ceiling symbol fix missing"
grep -Fq 'STREAMFORGE_ROLE_FORM_FILTERED_GROUPS_MAPPING_FIX_V2262' "$SOURCE_DIR/app/main.py" || die "Role form filtered permission-group mapping fix missing"
grep -Fq 'STREAMFORGE_BLOCK_OWN_ASSIGNED_ROLE_EDIT_V2263' "$SOURCE_DIR/app/main.py" || die "Own assigned-role edit protection missing"
grep -Fq 'STREAMFORGE_BLOCK_OWN_ASSIGNED_ROLE_DELETE_V2264' "$SOURCE_DIR/app/main.py" || die "Own assigned-role delete protection missing"
grep -Fq 'can_create_roles' "$SOURCE_DIR/app/main.py" || die "Explicit Super Admin role-create UI capability missing"
grep -Fq 'STREAMFORGE_ROLE_ADD_VISIBILITY_FIX_V2265' "$SOURCE_DIR/app/templates/roles.html" || die "Roles Add Role visibility fix missing"
grep -Fq 'STREAMFORGE_ROLE_CREATE_BY_PERMISSION_V2266' "$SOURCE_DIR/app/main.py" || die "Permission-based role creation support missing"
# STREAMFORGE_ROLE_CREATE_VALIDATOR_FIX_V2267:
grep -Fq '("roles.create", "Add roles"' "$SOURCE_DIR/app/permissions.py" || die "Add roles permission missing from package"
grep -Fq 'STREAMFORGE_RBAC_HIERARCHY_CEILING_V2270' "$SOURCE_DIR/app/main.py" || die "RBAC hierarchy ceiling helper missing"
grep -Fq 'STREAMFORGE_PANEL_USER_REMOTE_NODE_THREE_COLUMN_SPACING_V2274' "$SOURCE_DIR/app/templates/admin_user_form.html" || die "Panel User Remote Node three-column spacing fix missing"
grep -Fq 'STREAMFORGE_MAIN_PASSWORD_SIGNOUT_FOOTER_V2281' "$SOURCE_DIR/app/static/style.css" || die "Main Change password/Sign out footer layout missing"
grep -Fq 'STREAMFORGE_MAIN_SIDEBAR_SUBTITLE_RESTORE_V2282' "$SOURCE_DIR/app/static/style.css" || die "Main sidebar subtitle restore missing"
grep -Fq 'STREAMFORGE_MAIN_PASSWORD_PAGE_ALERT_SPACING_V2283' "$SOURCE_DIR/app/templates/account_password.html" || die "Main password alert spacing fix missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_PASSWORD_ALERT_FIX_V2285' "$SOURCE_DIR/node_agent/app.py" || die "Actual Node Panel password alert fix missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSIONS_PREFIX_AJAX_V2286' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "Main Live Sessions prefix-safe AJAX refresh missing"
grep -Fq 'window.STREAMFORGE_APP_ROOT' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "Main Live Sessions panel-root handling missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSIONS_AJAX_V2286' "$SOURCE_DIR/node_agent/app.py" || die "Node Live Sessions AJAX refresh UI missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSIONS_LAYOUT_V2287' "$SOURCE_DIR/node_agent/app.py" || die "Node Live Sessions panel layout fix missing"
grep -Fq 'STREAMFORGE_MAIN_SIDEBAR_BRAND_SIZE_V2288' "$SOURCE_DIR/app/static/style.css" || die "Main sidebar brand size update missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_FAVICON_ROUTE_V2288' "$SOURCE_DIR/app/main.py" || die "Main Web Player favicon route missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_FAVICON_HEAD_V2289' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player favicon head fix missing"
grep -Fq 'web-player/favicon?v={{ app_version }}' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player favicon cache-busting link missing"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_FAVICON_V2289' "$SOURCE_DIR/app/main.py" || die "Main player favicon context missing"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_FAVICON_HEAD_V2289' "$SOURCE_DIR/app/templates/player.html" || die "Main player favicon head markup missing"
grep -Fq 'STREAMFORGE_VIEWER_INFORMATION_SIDE_BY_SIDE_V2290' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Viewer information side-by-side layout missing"
grep -Fq 'STREAMFORGE_MANUAL_ADD_VIEWER_TTL_V2292' "$SOURCE_DIR/app/templates/node_form.html" || die "Manual Add online session timeout field missing"
grep -Fq 'STREAMFORGE_MANUAL_ADD_VIEWER_TTL_SAVE_V2292' "$SOURCE_DIR/app/main.py" || die "Manual Add online session timeout persistence missing"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_VIEWER_TTL_V2292' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install online session timeout field missing"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_VIEWER_TTL_SAVE_V2292' "$SOURCE_DIR/app/main.py" || die "Auto Install online session timeout persistence missing"
grep -Fq 'STREAMFORGE_MAIN_HIDE_HOVER_SETTING_LAYOUT_V2293' "$SOURCE_DIR/app/templates/system_branding.html" || die "Main hover URL setting layout fix missing"
grep -Fq 'STREAMFORGE_NODE_HIDE_HOVER_SETTING_V2293' "$SOURCE_DIR/app/main.py" || die "Per-Node hover URL setting key missing"
grep -Fq 'STREAMFORGE_NODE_HIDE_HOVER_EDIT_UI_V2293' "$SOURCE_DIR/app/templates/node_form.html" || die "Per-Node hover URL setting UI missing"
grep -Fq 'STREAMFORGE_NODE_HIDE_HOVER_SAVE_V2293' "$SOURCE_DIR/app/main.py" || die "Per-Node hover URL setting persistence missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_HIDE_HOVER_URLS_V2293' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player hover URL hiding missing"
grep -Fq 'STREAMFORGE_MAIN_PANEL_HIDE_HOVER_RUNTIME_V99R18' "$SOURCE_DIR/app/static/panel_nav.js" || die "v9.9 r18 Main Panel hover runtime setting missing"
grep -Fq 'STREAMFORGE_MAIN_CATALOG_PER_NODE_POLICY_V99R18' "$SOURCE_DIR/app/main.py" || die "v9.9 r18 Main catalogue per-Node policy fix missing"
grep -Fq 'STREAMFORGE_MAIN_LOCAL_ACCESS_POLICY_RUNTIME_V100R3' "$SOURCE_DIR/app/main.py" || die "v10.6 r3 Main Local access-policy cache missing"
grep -Fq 'STREAMFORGE_MAIN_PANEL_PLAYBACK_POLICY_ENFORCEMENT_V100R3' "$SOURCE_DIR/app/main.py" || die "v10.6 r3 Main Panel/Playback access enforcement missing"
grep -Fq 'STREAMFORGE_MAIN_RESTRICTED_PAGE_RESPONSIVE_V101' "$SOURCE_DIR/app/main.py" || die "v10.6 responsive Main restricted page missing"
grep -Fq 'STREAMFORGE_NODE_RESTRICTED_PAGE_RESPONSIVE_V101' "$SOURCE_DIR/node_agent/app.py" || die "v10.6 responsive Node restricted pages missing"
grep -Fq 'STREAMFORGE_NODE_INTERNAL_AUTH_PANEL_POLICY_BYPASS_V102' "$SOURCE_DIR/app/main.py" || die "v10.6 authenticated Node internal Panel-policy bypass missing from package"
grep -Fq 'STREAMFORGE_NODE_PANEL_DENY_DIAGNOSTIC_LOG_V102' "$SOURCE_DIR/node_agent/app.py" || die "v10.6 Node Panel deny diagnostic logging missing from package"
grep -Fq 'STREAMFORGE_PANEL_POLICY_ACCESS_LOG_V103' "$SOURCE_DIR/app/main.py" || die "v10.6 Main Panel access-log routing missing from package"
grep -Fq 'STREAMFORGE_NODE_PANEL_POLICY_DIAGNOSTIC_SAFE_V103' "$SOURCE_DIR/node_agent/app.py" || die "v10.6 Node Panel diagnostic safety missing from package"
grep -Fq 'STREAMFORGE_NODE_PANEL_UNHANDLED_ERROR_PAGE_V103' "$SOURCE_DIR/node_agent/app.py" || die "v10.6 Node Panel error-page safety missing from package"
grep -Fq 'STREAMFORGE_NODE_LOG_DEQUE_SNAPSHOT_V104' "$SOURCE_DIR/node_agent/app.py" || die "v10.6 Node log deque snapshot safety missing from package"
grep -Fq 'STREAMFORGE_NODE_METRICS_PLAYBACK_READY_CHANNELS_V105' "$SOURCE_DIR/node_agent/app.py" || die "v10.6 Node dashboard playable Online-channel metric fix missing"
grep -Fq 'STREAMFORGE_DNS01_OUTER_GAP_NOTE_WRAP_V105' "$SOURCE_DIR/app/templates/node_form.html" || die "v10.6 DNS-01 outer spacing/note-wrap fix missing"
grep -Fq 'STREAMFORGE_CONTEXTUAL_LOG_FILTERS_V106' "$SOURCE_DIR/app/templates/logs.html" || die "v10.6 contextual Main log filters missing"
grep -Fq 'STREAMFORGE_NODE_CONTEXTUAL_LOG_FILTERS_V106' "$SOURCE_DIR/node_agent/app.py" || die "v10.6 contextual Node log filters missing"
sed -n '/if kind in {"panel", "control"}:/,/headers = {"Cache-Control": "no-store"}/p' "$SOURCE_DIR/node_agent/app.py" | grep -F 'scope="auth"' >/dev/null || die "v10.6 Node Panel denials are not routed to Access log"
sed -n '/def _main_access_restricted_response/,/STREAMFORGE_MAIN_RESTRICTED_PAGE_RESPONSIVE_V101/p' "$SOURCE_DIR/app/main.py" | grep -F '_log_panel_access_denied' >/dev/null || die "v10.6 Main Panel denials are not routed to Access log"
grep -Fq 'STREAMFORGE_FULL_PLAYBACK_POLICY_ROUTING_V99R18' "$SOURCE_DIR/app/load_balancer.py" || die "v9.9 r18 full playback policy routing missing"
grep -Fq 'STREAMFORGE_STATIC_STRICT_FALLBACK_V100' "$SOURCE_DIR/app/load_balancer.py" || die "v10.6 Static/Strict fallback routing missing"
grep -Fq 'STREAMFORGE_V100_R2_CATALOG_ASSERTION_COMPAT' "$SOURCE_UPDATER" || die "v10.6 r2 catalogue assertion compatibility guard missing"
sed -n '/def online_user_channels/,/^def /p' "$SOURCE_DIR/app/main.py" | grep -F 'permitted = playback_candidate_nodes(user, channel, pinned=None)' >/dev/null || die "v10.6 online catalogue candidate routing missing"
sed -n '/def playback_candidate_nodes/,/^def /p' "$SOURCE_DIR/app/load_balancer.py" | grep -F 'elif bool(user.load_balance_enabled):' >/dev/null || die "v10.6 load-balanced restriction branch missing"
grep -Fq 'STREAMFORGE_STATIC_STRICT_READY_FALLBACK_V100' "$SOURCE_DIR/app/load_balancer.py" || die "v10.6 Static/Strict readiness fallback missing"
grep -Fq 'STREAMFORGE_ROUTED_FALLBACK_VALIDATION_V100' "$SOURCE_DIR/app/load_balancer.py" || die "v10.6 routed fallback validation missing"
grep -Fq 'STREAMFORGE_MEDIA_PLAYLIST_ROUTE_FALLBACK_V100' "$SOURCE_DIR/app/main.py" || die "v10.6 media-playlist fallback redirect missing"
grep -Fq 'STREAMFORGE_DIRECT_MEDIA_PLAYLIST_FALLBACK_V100' "$SOURCE_DIR/app/main.py" || die "v10.6 direct media-playlist fallback redirect missing"
grep -Fq 'STREAMFORGE_CATALOG_STATIC_STRICT_FALLBACK_V100' "$SOURCE_DIR/app/main.py" || die "v10.6 catalogue fallback visibility missing"
grep -Fq 'STREAMFORGE_MAIN_LOCAL_PLAYLIST_HLS_AUTHORITY_V100' "$SOURCE_DIR/app/main.py" || die "v10.6 Main Local playlist HLS-authority fix missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_LOCAL_HLS_AUTHORITY_V100' "$SOURCE_DIR/app/main.py" || die "v10.6 Main WebPlayer Local HLS-authority fix missing"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_HIDE_HOVER_URLS_V2293' "$SOURCE_DIR/app/templates/player.html" || die "Main player hover URL hiding missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_HIDE_HOVER_URLS_V2293' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player hover URL hiding missing"
grep -Fq 'STREAMFORGE_MAIN_PASSWORD_FOOTER_REST_STATE_V2293' "$SOURCE_DIR/app/static/style.css" || die "Main Change password footer rest-state fix missing"
grep -Fq 'STREAMFORGE_MAIN_PASSWORD_FOOTER_EXACT_MATCH_V2294' "$SOURCE_DIR/app/templates/base.html" || die "Main Change password footer structure fix missing"
grep -Fq 'STREAMFORGE_NODE_NAV_ICON_TEXT_SPACING_V2295' "$SOURCE_DIR/node_agent/app.py" || die "Node navigation icon/text spacing fix missing"
grep -Fq 'STREAMFORGE_NODE_FIVE_OPTIONS_ROW_V2296' "$SOURCE_DIR/app/templates/node_form.html" || die "Node Edit/Manual Add five-option row missing"
grep -Fq 'STREAMFORGE_NODE_GLOBAL_NAV_ICON_TEXT_SPACING_V2297' "$SOURCE_DIR/node_agent/app.py" || die "Global Node navigation icon/text spacing fix missing"
grep -Fq 'STREAMFORGE_PROJECT_MOBILE_AUDIT_MAIN_V2298' "$SOURCE_DIR/app/static/style.css" || die "Main project mobile audit CSS missing"
grep -Fq 'STREAMFORGE_NODE_OPTION_TRUE_VERTICAL_CENTER_V2300' "$SOURCE_DIR/app/templates/node_form.html" || die "Node option vertical-center left alignment missing"
grep -Fq 'STREAMFORGE_AUTO_OPTION_TRUE_VERTICAL_CENTER_V2300' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install option vertical-center left alignment missing"
grep -Fq 'justify-content:center!important;' "$SOURCE_DIR/app/templates/node_form.html" || die "Node option true vertical centering CSS missing"
grep -Fq 'justify-content:center!important;' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install option true vertical centering CSS missing"
grep -Fq 'STREAMFORGE_OPTION_CARD_VERTICAL_CENTER_LEFT_V2299' "$SOURCE_DIR/app/templates/system_branding.html" || die "Settings option-card alignment missing"
grep -Fq 'STREAMFORGE_MAIN_MOBILE_SIDEBAR_REGRESSION_FIX_V2299' "$SOURCE_DIR/app/static/style.css" || die "Main mobile sidebar regression fix missing"
grep -Fq '.app-shell.mobile-nav-open .sidebar #main-navigation' "$SOURCE_DIR/app/static/style.css" || die "Main mobile menu open-state selector missing"
grep -Fq 'height:68px!important;' "$SOURCE_DIR/app/static/style.css" || die "Main mobile sidebar compact height missing"
grep -Fq 'STREAMFORGE_MAIN_MOBILE_TARGETED_WIDTH_SAFETY_V2299' "$SOURCE_DIR/app/static/style.css" || die "Main mobile targeted width safety missing"
grep -Fq 'STREAMFORGE_PROJECT_MOBILE_AUDIT_NODE_V2298' "$SOURCE_DIR/node_agent/app.py" || die "Node project mobile audit CSS missing"
grep -Fq 'STREAMFORGE_PROJECT_MOBILE_AUDIT_WEBPLAYER_V2298' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player mobile audit CSS missing"
grep -Fq 'STREAMFORGE_PROJECT_MOBILE_AUDIT_PLAYER_V2298' "$SOURCE_DIR/app/templates/player.html" || die "Main Player mobile audit CSS missing"
grep -Fq 'STREAMFORGE_PROJECT_MOBILE_AUDIT_NODE_WEBPLAYER_V2298' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player mobile audit CSS missing"
grep -Fq '.panel-nav-links .nav-button{' "$SOURCE_DIR/node_agent/app.py" || die "Global Node navigation row selector missing"
grep -Fq 'gap:12px!important;' "$SOURCE_DIR/node_agent/app.py" || die "Global Node navigation icon/text gap missing"
grep -Fq '.panel-sidebar-footer .node-account-icon' "$SOURCE_DIR/node_agent/app.py" || die "Node footer account icon alignment missing"
grep -Fq 'STREAMFORGE_NODE_FIVE_OPTIONS_STYLE_V2296' "$SOURCE_DIR/app/templates/node_form.html" || die "Node Edit/Manual Add five-option style missing"
grep -Fq 'grid-template-columns:repeat(5,minmax(0,1fr))' "$SOURCE_DIR/app/templates/node_form.html" || die "Node Edit/Manual Add five-column layout missing"
grep -Fq 'STREAMFORGE_MANUAL_ADD_HIDE_HOVER_SAVE_V2296' "$SOURCE_DIR/app/main.py" || die "Manual Add per-Node hover setting save missing"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_FIVE_OPTIONS_V2296' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install five-option row missing"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_FIVE_OPTIONS_STYLE_V2296' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install five-option style missing"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_HIDE_HOVER_SAVE_V2296' "$SOURCE_DIR/app/main.py" || die "Auto Install per-Node hover setting save missing"
grep -Fq 'node_viewer_session_timeout_seconds: int = Form(5)' "$SOURCE_DIR/app/main.py" || die "Node install timeout form parameter missing"
grep -Fq 'gap:11px!important;' "$SOURCE_DIR/node_agent/app.py" || die "Node navigation icon/text gap missing"
grep -Fq '.node-signout-icon::before,.node-signout-icon::after{content:none!important}' "$SOURCE_DIR/node_agent/app.py" || die "Legacy Node signout pseudo-icon cleanup missing"
grep -Fq 'STREAMFORGE_MAIN_PASSWORD_FOOTER_GEOMETRY_V2294' "$SOURCE_DIR/app/static/style.css" || die "Main footer equal geometry fix missing"
grep -Fq '<form method="get" action="/account/password"><button type="submit" class="nav-button account main-node-account"' "$SOURCE_DIR/app/templates/base.html" || die "Main Change password is not using the same form/button structure as Sign out"
grep -Fq 'grid-template-columns:repeat(3,minmax(0,1fr))' "$SOURCE_DIR/app/templates/node_form.html" || die "Remote Node three-column grid rule missing"
grep -Fq 'class="viewer-info-options"' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Viewer information options grid missing"
grep -Fq 'grid-template-columns:repeat(2,minmax(0,1fr))' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Viewer information two-column layout missing"
grep -Fq '@media(max-width:800px){.viewer-info-options{grid-template-columns:1fr}}' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Viewer information mobile stack rule missing"
grep -Fq 'X-StreamForge-WebPlayer-Favicon' "$SOURCE_DIR/app/main.py" || die "Main Web Player favicon response marker missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_FAVICON_PREFIX_V2288' "$SOURCE_DIR/node_agent/app.py" || die "Node panel favicon prefix fix missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOGIN_LOGO_ROUTE_V2288' "$SOURCE_DIR/node_agent/app.py" || die "Node panel login logo route missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOGIN_BRANDING_V2288' "$SOURCE_DIR/node_agent/app.py" || die "Node panel login centered branding missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_BRAND_SIZE_V2288' "$SOURCE_DIR/node_agent/app.py" || die "Node sidebar brand size update missing"
grep -Fq 'class="node-live-session-panel"' "$SOURCE_DIR/node_agent/app.py" || die "Node Live Sessions outer panel missing"
grep -Fq '<h2>Online sessions</h2>' "$SOURCE_DIR/node_agent/app.py" || die "Node Live Sessions panel heading missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSIONS_JSON_V2286' "$SOURCE_DIR/node_agent/app.py" || die "Node Live Sessions JSON endpoint missing"
grep -Fq 'data-node-live-session-body' "$SOURCE_DIR/node_agent/app.py" || die "Node Live Sessions dynamic table body missing"
grep -Fq 'background:var(--surface-2)!important;color:var(--text)!important' "$SOURCE_DIR/node_agent/app.py" || die "Node Panel password success alert is not using Node default theme"
grep -Fq '.account-password-alert.success{' "$SOURCE_DIR/node_agent/app.py" || die "Node Panel password success alert selector missing"


grep -Fq 'account-password-alert' "$SOURCE_DIR/app/templates/account_password.html" || die "Main password alert class missing"
grep -Fq 'STREAMFORGE_MAIN_PASSWORD_FOOTER_HOVER_FIX_V2283' "$SOURCE_DIR/app/static/style.css" || die "Main Change password footer hover fix missing"
grep -Fq 'STREAMFORGE_NODE_PASSWORD_FOOTER_HOVER_FIX_V2283' "$SOURCE_DIR/node_agent/app.py" || die "Node Change password footer hover fix missing"

grep -Fq 'class="brand-subtitle">{{ branding.subtitle }}</small>' "$SOURCE_DIR/app/templates/base.html" || die "Main sidebar branding subtitle markup missing"
grep -Fq 'class="brand-version">v{{ app_version }}</small>' "$SOURCE_DIR/app/templates/base.html" || die "Main sidebar version markup missing"

grep -Fq 'class="nav-button account main-node-account"' "$SOURCE_DIR/app/templates/base.html" || die "Main Change password footer action missing"
grep -Fq 'STREAMFORGE_NODE_PASSWORD_SIGNOUT_FOOTER_V2281' "$SOURCE_DIR/node_agent/app.py" || die "Node Change password/Sign out footer layout missing"
grep -Fq 'class="nav-button account" href="/panel/account/password"' "$SOURCE_DIR/node_agent/app.py" || die "Node Change password footer action missing"
! grep -Fq 'items.append(("account", "Change password", "/panel/account/password"))' "$SOURCE_DIR/node_agent/app.py" || die "Node Change password is still in normal navigation"

# STREAMFORGE_LOGOUT_VALIDATOR_FIX_V2280:
grep -Fq 'class="nav-button signout main-node-signout"' "$SOURCE_DIR/app/templates/base.html" || die "Current Node-style signout button markup missing"
grep -Fq 'class="node-signout-icon"' "$SOURCE_DIR/app/templates/base.html" || die "Current Node-style signout icon markup missing"
grep -Fq 'class="node-signout-label">Sign out</span>' "$SOURCE_DIR/app/templates/base.html" || die "Current Node-style signout label markup missing"
grep -Fq 'M10 4H6.8A1.8 1.8 0 0 0 5 5.8v12.4A1.8 1.8 0 0 0 6.8 20H10' "$SOURCE_DIR/app/templates/base.html" || die "Current Node-style signout SVG path missing"

grep -Fq 'class="nav-button signout main-node-signout"' "$SOURCE_DIR/app/templates/base.html" || die "Node-style Main signout button missing"
grep -Fq 'class="node-signout-icon"' "$SOURCE_DIR/app/templates/base.html" || die "Exact Node signout icon class missing"
grep -Fq 'class="node-signout-label">Sign out</span>' "$SOURCE_DIR/app/templates/base.html" || die "Exact Node signout label class missing"
grep -Fq 'grid-template-columns:76px 1fr!important' "$SOURCE_DIR/app/static/style.css" || die "Collapsed Main width does not match Node"

grep -Fq '<div class="main-nav-links">' "$SOURCE_DIR/app/templates/base.html" || die "Main nav links wrapper missing"
! grep -Fq 'Panel online · v{{ app_version }}' "$SOURCE_DIR/app/templates/base.html" || die "Old Main sidebar bottom online/version label still present"
grep -Fq 'M10 4H6.8A1.8 1.8 0 0 0 5 5.8v12.4A1.8 1.8 0 0 0 6.8 20H10' "$SOURCE_DIR/app/templates/base.html" || die "Node sign-out SVG path missing from Main sidebar"
grep -Fq '<svg viewBox="0 0 24 24" aria-hidden="true">' "$SOURCE_DIR/app/templates/base.html" || die "Main navigation SVG icons missing"
grep -Fq 'grid-template-columns: repeat(3, minmax(0, 1fr)) !important;' "$SOURCE_DIR/app/templates/admin_user_form.html" || die "Remote Node desktop three-column layout missing"
grep -Fq 'padding: 14px !important;' "$SOURCE_DIR/app/templates/admin_user_form.html" || die "Remote Node section inner spacing missing"
grep -Fq '@media (max-width: 1100px)' "$SOURCE_DIR/app/templates/admin_user_form.html" || die "Remote Node tablet two-column fallback missing"
grep -Fq '@media (max-width: 760px)' "$SOURCE_DIR/app/templates/admin_user_form.html" || die "Remote Node mobile one-column fallback missing"
grep -Fq 'min-height: 60px !important;' "$SOURCE_DIR/app/templates/admin_user_form.html" || die "Remote Node compact card height missing"
grep -Fq '@media (max-width: 760px)' "$SOURCE_DIR/app/templates/admin_user_form.html" || die "Remote Node mobile one-column fallback missing"
grep -Fq 'compact-node-checks remote-node-access-list' "$SOURCE_DIR/app/templates/admin_user_form.html" || die "Remote Node actual wrapper is missing grid class"

grep -Fq 'role_permissions.issubset(actor_permissions)' "$SOURCE_DIR/app/main.py" || die "Role hierarchy subset check missing"
grep -Fq 'panel_user_is_within_actor_ceiling(actor, user)' "$SOURCE_DIR/app/main.py" || die "Upper panel-user visibility filter missing"
grep -Fq 'You cannot assign a role above your own permission level.' "$SOURCE_DIR/app/main.py" || die "Upper role assignment guard missing"
grep -Fq 'STREAMFORGE_BLOCK_OWN_PANEL_USER_DELETE_V2270' "$SOURCE_DIR/app/main.py" || die "Own panel-user delete guard missing"
grep -Fq 'STREAMFORGE_ROLE_ADMIN_GRANT_WITH_HIERARCHY_V2270' "$SOURCE_DIR/app/main.py" || die "Role admin permission grant hierarchy missing"

grep -Fq '"roles.create": {"roles.view"}' "$SOURCE_DIR/app/permissions.py" || die "Add roles dependency missing from package"

grep -Fq 'permission_required("roles.create")' "$SOURCE_DIR/app/main.py" || die "Role create routes are not guarded by roles.create"
grep -Fq 'enforce_permission(request, db, "roles.create")' "$SOURCE_DIR/app/main.py" || die "Role create action does not enforce roles.create"
grep -Fq '("roles.create", "Add roles"' "$SOURCE_DIR/app/permissions.py" || die "Add roles permission missing"
grep -Fq '"roles.create": {"roles.view"}' "$SOURCE_DIR/app/permissions.py" || die "Add roles dependency missing"
grep -Fq 'admin_is_super_admin(admin) or has_permission(admin, "roles.create")' "$SOURCE_DIR/app/main.py" || die "Add Role button capability is not permission-based"
grep -Fq 'You cannot grant permissions that you do not have.' "$SOURCE_DIR/app/main.py" || die "Role creation permission ceiling missing"
grep -Fq '{% if can_create_roles %}<a class="button primary" href="/roles/new">+ Add role</a>{% endif %}' "$SOURCE_DIR/app/templates/roles.html" || die "Super Admin Add Role button is not rendered from explicit capability"
grep -Fq 'You cannot delete the role currently assigned to your own account.' "$SOURCE_DIR/app/main.py" || die "Own assigned-role delete server guard missing"
grep -Fq "can('roles.delete') and (is_super_admin or admin.role_id != row.role.id)" "$SOURCE_DIR/app/templates/roles.html" || die "Own assigned-role Delete button hide rule missing"
grep -Fq 'actor.role_id == role.id and not admin_is_super_admin(actor)' "$SOURCE_DIR/app/main.py" || die "Own assigned-role server guard missing"
grep -Fq "is_super_admin or admin.role_id != row.role.id" "$SOURCE_DIR/app/templates/roles.html" || die "Own assigned-role Edit button hide rule missing"
grep -Fq 'filtered_groups: dict[str, list[tuple[str, str, str]]] = {}' "$SOURCE_DIR/app/main.py" || die "Filtered role permission groups are not a mapping"
grep -Fq 'filtered_groups[group_name] = visible_items' "$SOURCE_DIR/app/main.py" || die "Filtered role permission groups mapping population missing"
grep -Fq 'permission_groups.items()' "$SOURCE_DIR/app/templates/role_form.html" || die "Role form mapping iteration missing"
grep -Fq 'return set(ALL_PERMISSION_KEYS)' "$SOURCE_DIR/app/main.py" || die "Role ceiling permission catalogue name is wrong"
grep -Fq 'for key in role_permission_set(admin.role)' "$SOURCE_DIR/app/main.py" || die "Role ceiling permission resolver name is wrong"
! grep -Eq '(^|[^A-Z_])PERMISSION_KEYS([^A-Z_]|$)' "$SOURCE_DIR/app/main.py" || die "Undefined standalone PERMISSION_KEYS reference remains"
! grep -Fq 'resolved_permissions(admin.role)' "$SOURCE_DIR/app/main.py" || die "Undefined resolved_permissions reference remains"
grep -Fq 'for group_name, group_items in PERMISSION_GROUPS.items()' "$SOURCE_DIR/app/main.py" || die "Role permission-group OrderedDict iteration is invalid"
grep -Fq 'role_assignable_permissions_for_admin(actor)' "$SOURCE_DIR/app/main.py" || die "Role editor permission ceiling not enforced"
grep -Fq 'permission_groups=permission_groups_for_role_editor(actor)' "$SOURCE_DIR/app/main.py" || die "Role form permission list is not filtered by current user permissions"
grep -Fq 'You cannot grant permissions that you do not have.' "$SOURCE_DIR/app/main.py" || die "Role permission escalation guard missing"
grep -Fq 'This role contains permissions you do not have and cannot be edited by your account.' "$SOURCE_DIR/app/main.py" || die "Higher-privilege role edit guard missing"
! grep -Fq 'Add roles — Super Admin only' "$SOURCE_DIR/app/permissions.py" || die "Obsolete Add roles permission is still visible"

grep -Fq 'main_access.view' "$SOURCE_DIR/app/permissions.py" || die "Main access View permission missing"
grep -Fq 'main_access.edit' "$SOURCE_DIR/app/permissions.py" || die "Main access Edit permission missing"
grep -Fq 'settings.view' "$SOURCE_DIR/app/permissions.py" || die "Settings View permission missing"
grep -Fq 'settings.edit' "$SOURCE_DIR/app/permissions.py" || die "Settings Edit permission missing"
grep -Fq 'backups.add' "$SOURCE_DIR/app/permissions.py" || die "Backup Add permission missing"
grep -Fq 'backups.edit' "$SOURCE_DIR/app/permissions.py" || die "Backup Edit permission missing"
grep -Fq 'backups.run' "$SOURCE_DIR/app/permissions.py" || die "Backup Run permission missing"
grep -Fq 'backups.download' "$SOURCE_DIR/app/permissions.py" || die "Backup Download permission missing"
grep -Fq 'backups.restore' "$SOURCE_DIR/app/permissions.py" || die "Backup Restore permission missing"
grep -Fq 'backups.delete' "$SOURCE_DIR/app/permissions.py" || die "Backup Remove permission missing"
grep -Fq 'nodes.create' "$SOURCE_DIR/app/permissions.py" || die "Node Add permission missing"
grep -Fq 'nodes.edit' "$SOURCE_DIR/app/permissions.py" || die "Node Edit permission missing"
grep -Fq 'nodes.delete' "$SOURCE_DIR/app/permissions.py" || die "Node Remove permission missing"
grep -Fq 'nodes.service_restart' "$SOURCE_DIR/app/permissions.py" || die "Node service restart permission missing"
grep -Fq 'nodes.reboot' "$SOURCE_DIR/app/permissions.py" || die "Node reboot permission missing"
grep -Fq 'categories.create' "$SOURCE_DIR/app/permissions.py" || die "Category Add permission missing"
grep -Fq 'categories.edit' "$SOURCE_DIR/app/permissions.py" || die "Category Edit permission missing"
grep -Fq 'categories.reorder' "$SOURCE_DIR/app/permissions.py" || die "Category Reorder permission missing"
grep -Fq 'categories.delete' "$SOURCE_DIR/app/permissions.py" || die "Category Remove permission missing"
grep -Fq 'logs.clear' "$SOURCE_DIR/app/permissions.py" || die "Log Clear permission missing"

grep -Fq 'panel_users.create' "$SOURCE_DIR/app/permissions.py" || die "Panel user Add permission missing"
grep -Fq 'panel_users.edit' "$SOURCE_DIR/app/permissions.py" || die "Panel user Edit permission missing"
grep -Fq 'panel_users.delete' "$SOURCE_DIR/app/permissions.py" || die "Panel user Remove permission missing"
grep -Fq 'roles.edit' "$SOURCE_DIR/app/permissions.py" || die "Role Edit permission missing"
grep -Fq 'roles.delete' "$SOURCE_DIR/app/permissions.py" || die "Role Remove permission missing"
grep -Fq 'visible_panel_roles(db, actor)' "$SOURCE_DIR/app/main.py" || die "Role visibility filtering missing"
grep -Fq "can('panel_users.create')" "$SOURCE_DIR/app/templates/admin_users.html" || die "Panel user Add UI permission split missing"
grep -Fq "can('panel_users.edit')" "$SOURCE_DIR/app/templates/admin_users.html" || die "Panel user Edit UI permission split missing"
grep -Fq "can('panel_users.delete')" "$SOURCE_DIR/app/templates/admin_users.html" || die "Panel user Remove UI permission split missing"
grep -Fq "can('roles.edit')" "$SOURCE_DIR/app/templates/roles.html" || die "Role Edit UI permission split missing"
grep -Fq "can('roles.delete')" "$SOURCE_DIR/app/templates/roles.html" || die "Role Remove UI permission split missing"

grep -Fq '<rect x="5" y="10" width="14" height="10" rx="2"/>' "$SOURCE_DIR/node_agent/app.py" || die "Node Change password lock icon missing" 
grep -Fq 'active="account"' "$SOURCE_DIR/node_agent/app.py" || die "Node Change password active navigation state missing"

grep -Fq 'action="change_password"' "$SOURCE_DIR/node_agent/app.py" || die "Node live password-change request missing"
grep -Fq 'STREAMFORGE_NODE_HIDE_HOVER_URLS_V2255' "$SOURCE_DIR/node_agent/app.py" || die "Node global internal-link hover suppression missing"

grep -Fq 'Current password' "$SOURCE_DIR/app/templates/account_password.html" || die "Panel-user password form missing"
grep -Fq 'viewer_ttl_seconds' "$SOURCE_DIR/app/node_manager.py" || die "Main-to-Node viewer TTL sync missing"
grep -Fq 'hide_panel_hover_urls' "$SOURCE_DIR/app/node_manager.py" || die "Main-to-Node hover URL preference sync missing"
grep -Fq 'viewer_ttl_seconds: int | None = None' "$SOURCE_DIR/node_agent/app.py" || die "Node viewer TTL setting support missing"

grep -Fq '.channel-tile,.viewer-info-group{background:var(--sf-panel)' "$SOURCE_DIR/app/templates/player.html" || die "Main player user/session boxes are not using managed panel color"

grep -Fq 'STREAMFORGE_MAIN_CHANNEL_ACTION_BUTTON_STATE_V2251' "$SOURCE_DIR/app/static/app.js" || die "Main channel action button live-state sync missing from package"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_ACTION_BUTTON_STATE_V2251' "$SOURCE_DIR/node_agent/app.py" || die "Node channel Start/Stop DOM state controls missing from package"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_ACTION_BUTTON_STATE_SYNC_V2251' "$SOURCE_DIR/node_agent/app.py" || die "Node channel action button live-state sync missing from package"

grep -Fq 'data-channel-runtime-action="start"' "$SOURCE_DIR/app/templates/channels.html" || die "Main Start AJAX marker missing from package"
grep -Fq 'data-channel-runtime-action="stop"' "$SOURCE_DIR/app/templates/channels.html" || die "Main Stop AJAX marker missing from package"
grep -Fq 'data-channel-runtime-action="restart"' "$SOURCE_DIR/app/templates/channels.html" || die "Main Restart AJAX marker missing from package"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_ACTION_AJAX_V2250' "$SOURCE_DIR/node_agent/app.py" || die "Node channel action AJAX response support missing from package"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_ACTION_NO_RELOAD_V2250' "$SOURCE_DIR/node_agent/app.py" || die "Node Channels no-reload action handler missing from package"
grep -Fq 'RedirectResponse(f"/panel/manage/channels?message={message}"' "$SOURCE_DIR/node_agent/app.py" || die "Node fallback redirect does not stay on Channels page"
grep -Fq 'STREAMFORGE_NODE_CATEGORY_CHANNEL_PANEL_V2247' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player category/channel unified panel missing from package"
grep -Fq '<section class="channel-library">' "$SOURCE_DIR/app/templates/web_player.html" || die "Main category/channel container markup missing"
grep -Fq '<section class="channel-library"><nav class="category-strip"' "$SOURCE_DIR/node_agent/app.py" || die "Node category/channel container markup missing"
grep -Fq '.channel-library{padding:16px;border:1px solid var(--line);border-radius:18px' "$SOURCE_DIR/app/templates/web_player.html" || die "Main category/channel panel style missing"
grep -Fq '.channel-library{padding:16px;border:1px solid var(--line);border-radius:18px' "$SOURCE_DIR/node_agent/app.py" || die "Node category/channel panel style missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_HEADER_LIVE_ONLINE_V2246' "$SOURCE_DIR/node_agent/app.py" || die "Node channel-page live Online Use refresh missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYER_LIVE_ONLINE_V2246' "$SOURCE_DIR/node_agent/app.py" || die "Node player live Online Use refresh missing from package"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_DIRECT_SWITCH_NO_URL_V2246' "$SOURCE_DIR/app/templates/player.html" || die "Main direct switch without address change missing"
grep -Fq 'STREAMFORGE_NODE_PLAYER_DIRECT_SWITCH_NO_URL_V2246' "$SOURCE_DIR/node_agent/app.py" || die "Node direct switch without address change missing"
! grep -Fq 'history.pushState' "$SOURCE_DIR/app/templates/player.html" || die "Main player still changes browser address during channel switch"
! grep -F 'STREAMFORGE_NODE_PLAYER_DIRECT_SWITCH_NO_URL_V2246' "$SOURCE_DIR/node_agent/app.py" | grep -Fq 'history.pushState' || die "Node player still changes browser address during channel switch"
grep -Fq 'STREAMFORGE_NODE_USER_ZERO_UNLIMITED_V2245' "$SOURCE_DIR/node_agent/app.py" || die "Node per-user 0=unlimited enforcement missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_EDIT_MAX_V2245' "$SOURCE_DIR/node_agent/app.py" || die "Node playlist-user Max connections edit support missing from package"
grep -Fq 'min="0" max="100" name="max_connections"' "$SOURCE_DIR/app/templates/user_form.html" || die "Main playlist-user Max connections form does not allow zero"
grep -Fq 'name="max_connections" min="0" max="100"' "$SOURCE_DIR/node_agent/app.py" || die "Node playlist-user Max connections form does not allow zero"
grep -Fq '0 means unlimited.' "$SOURCE_DIR/app/templates/user_form.html" || die "Main playlist-user unlimited help text missing"
grep -Fq '0 means unlimited.' "$SOURCE_DIR/node_agent/app.py" || die "Node playlist-user unlimited help text missing"
grep -Fq 'endpoint: panelUrl(rawEndpoint)' "$SOURCE_DIR/app/static/app.js" || die "Channel Stream Logs load endpoint is not panel-prefixed"
grep -Fq 'clearEndpoint: panelUrl(rawClearEndpoint)' "$SOURCE_DIR/app/static/app.js" || die "Channel Stream Logs clear endpoint is not panel-prefixed"
grep -Fq 'state.rawEndpoint || state.endpoint' "$SOURCE_DIR/app/static/app.js" || die "Channel Stream Logs clear-state selector compatibility missing"
grep -Fq 'panelUrl(`/status.json?channel_ids=' "$SOURCE_DIR/app/static/app.js" || die "Channel runtime status request does not use panel root"
grep -Fq 'panelUrl(`/nodes/${id}/status.json`)' "$SOURCE_DIR/app/static/app.js" || die "Node runtime status request does not use panel root"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_HEADER_INFO_GROUPS_V2241' "$SOURCE_DIR/node_agent/app.py" || die "Node channel header grouped viewer info missing from package"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_HEADER_EXPIRY_FIX_V2243' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player channel-header expiry fix missing from package"
grep -Fq 'header_player_expires = "Never"' "$SOURCE_DIR/node_agent/app.py" || die "Node channel-header expiry formatter missing from package"
grep -Fq 'html.escape(header_player_expires)' "$SOURCE_DIR/node_agent/app.py" || die "Node channel-header Expire rendering fix missing from package"
! grep -Fq 'html.escape(_node_web_expiry_label(user))' "$SOURCE_DIR/node_agent/app.py" || die "Broken undefined Node expiry-helper call still present"
grep -Fq 'header-info-group user' "$SOURCE_DIR/app/templates/web_player.html" || die "Main channel header Username/Expire group missing from package"
grep -Fq 'header-info-group connection' "$SOURCE_DIR/app/templates/web_player.html" || die "Main channel header Max/Online group missing from package"
grep -Fq 'header-info-group user' "$SOURCE_DIR/node_agent/app.py" || die "Node channel header Username/Expire group missing from package"
grep -Fq 'header-info-group connection' "$SOURCE_DIR/node_agent/app.py" || die "Node channel header Max/Online group missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYER_INFO_GROUPS_V2240' "$SOURCE_DIR/node_agent/app.py" || die "Node player separate viewer/connection groups missing from package"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_INPAGE_CHANNEL_SWITCH_V2240' "$SOURCE_DIR/app/templates/player.html" || die "Main player in-page channel switch missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYER_INPAGE_CHANNEL_SWITCH_V2240' "$SOURCE_DIR/node_agent/app.py" || die "Node player in-page channel switch missing from package"
grep -Fq 'data-stream-url=' "$SOURCE_DIR/app/templates/player.html" || die "Main channel cards missing direct stream metadata"
grep -Fq 'data-stream-url=' "$SOURCE_DIR/node_agent/app.py" || die "Node channel cards missing direct stream metadata"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_HEADER_INFO_UI_V2239' "$SOURCE_DIR/app/templates/web_player.html" || die "Main channel-page header viewer UI missing from package"
grep -Fq 'header_max_connections' "$SOURCE_DIR/app/templates/web_player.html" || die "Main channel-page Max Connections missing from package"
grep -Fq 'header_online_use' "$SOURCE_DIR/app/templates/web_player.html" || die "Main channel-page Online Use missing from package"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_HEADER_VIEWER_INFO_V2239' "$SOURCE_DIR/node_agent/app.py" || die "Node channel-page header viewer data missing from package"
grep -Fq 'header_viewer_html' "$SOURCE_DIR/node_agent/app.py" || die "Node channel-page right-side viewer UI missing from package"
grep -Fq 'webplayer_settings.show_connection_info' "$SOURCE_DIR/app/templates/web_player.html" || die "Main channel-page connection-info control missing"
grep -Fq 'manager.webplayer_show_connection_info' "$SOURCE_DIR/node_agent/app.py" || die "Node channel-page connection-info control missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_CONNECTION_INFO_MANAGE_V2238' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Web Player connection-info Manage toggle missing from package"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_CONNECTION_INFO_V2238' "$SOURCE_DIR/app/main.py" || die "Main player connection-info data missing from package"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_CONNECTION_INFO_UI_V2238' "$SOURCE_DIR/app/templates/player.html" || die "Main player connection-info UI missing from package"
grep -Fq 'webplayer_show_connection_info' "$SOURCE_DIR/app/node_manager.py" || die "Main-to-Node connection-info sync missing from package"
grep -Fq 'webplayer_show_connection_info' "$SOURCE_DIR/node_agent/app.py" || die "Node connection-info setting support missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYER_CONNECTION_COUNT_V2238' "$SOURCE_DIR/node_agent/app.py" || die "Node per-user online connection counter missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYER_CONNECTION_INFO_UI_V2238' "$SOURCE_DIR/node_agent/app.py" || die "Node player connection-info UI missing from package"
grep -Fq 'Max Connections' "$SOURCE_DIR/app/templates/player.html" || die "Main Max Connections label missing from package"
grep -Fq 'Online Use' "$SOURCE_DIR/node_agent/app.py" || die "Node Online Use label missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYER_ACTIVE_CATEGORY_CLARITY_V2237' "$SOURCE_DIR/node_agent/app.py" || die "Node player active-category clarity fix missing from package"
grep -Fq 'content:"✓"' "$SOURCE_DIR/app/templates/player.html" || die "Main active-category check indicator missing from package"
grep -Fq 'content:"✓"' "$SOURCE_DIR/node_agent/app.py" || die "Node active-category check indicator missing from package"
grep -Fq 'box-shadow:inset 0 0 0 1px currentColor!important' "$SOURCE_DIR/app/templates/player.html" || die "Main active-category contrast ring missing from package"
grep -Fq 'box-shadow:inset 0 0 0 1px currentColor!important' "$SOURCE_DIR/node_agent/app.py" || die "Node active-category contrast ring missing from package"
grep -Fq 'STREAMFORGE_NODE_VISUAL_BALANCE_V2235' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player visual-balance theme missing from package"
grep -Fq 'STREAMFORGE_MAIN_VISUAL_BALANCE_V2235' "$SOURCE_DIR/app/templates/web_player.html" || die "Main Web Player visual balance fix missing from package"
grep -Fq 'STREAMFORGE_NODE_VISUAL_BALANCE_V2235' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player visual balance fix missing from package"
grep -Fq -- '--sf-soft-line:color-mix(in srgb,var(--sf-text) 15%,transparent)' "$SOURCE_DIR/app/templates/web_player.html" || die "Main neutral border derivation missing"
grep -Fq -- '--sf-soft-line:color-mix(in srgb,var(--sf-text) 15%,transparent)' "$SOURCE_DIR/node_agent/app.py" || die "Node neutral border derivation missing"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_BACKGROUND_SYNC_V2234' "$SOURCE_DIR/app/templates/player.html" || die "Main player background color sync fix missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYER_BACKGROUND_SYNC_V2234' "$SOURCE_DIR/node_agent/app.py" || die "Node player background color sync fix missing from package"
grep -Fq 'html{background:#000!important}' "$SOURCE_DIR/app/templates/player.html" || die "Main player transparent page base missing from package"
grep -Fq 'html{{background:#000!important}}' "$SOURCE_DIR/node_agent/app.py" || die "Node player transparent page base missing from package"
grep -Fq 'background:var(--sf-panel)!important' "$SOURCE_DIR/app/templates/web_player.html" || die "Main managed Panel/Card color binding missing"
grep -Fq 'background:var(--sf-accent)!important' "$SOURCE_DIR/app/templates/web_player.html" || die "Main managed Accent color binding missing"
grep -Fq 'background:var(--sf-panel)!important' "$SOURCE_DIR/node_agent/app.py" || die "Node managed Panel/Card color binding missing"
grep -Fq 'background:var(--sf-accent)!important' "$SOURCE_DIR/node_agent/app.py" || die "Node managed Accent color binding missing"
grep -Fq 'STREAMFORGE_NODE_WATCH_THEME_CORE_V2229' "$SOURCE_DIR/node_agent/app.py" || die "Node player-page theme core missing from package"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_THEME_SYNC_ECHO_V2229' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player sync confirmation missing from package"
grep -Fq 'STREAMFORGE_NODE_PORTAL_THEME_APPLY_V2228' "$SOURCE_DIR/node_agent/app.py" || die "Node authenticated portal managed-theme marker missing"
grep -Fq '_node_webplayer_theme_css(request)' "$SOURCE_DIR/node_agent/app.py" || die "Node authenticated portal does not load request-aware managed theme CSS"
grep -Fq '.category-chip.active,.mark,button[type=submit]' "$SOURCE_DIR/node_agent/app.py" || die "Node managed accent selector missing from package"
! grep -Fq 'category-chip.active{{background:linear-gradient(180deg,#ff2525,#db0505);border-color:#ff4a4a;color:#fff;box-shadow:none}}}' "$SOURCE_DIR/node_agent/app.py" || die "Malformed Node watch CSS format brace still present"
grep -Fq '.category-chip.active{{background:linear-gradient(180deg,#ff2525,#db0505);border-color:#ff4a4a;color:#fff;box-shadow:none}}' "$SOURCE_DIR/node_agent/app.py" || die "Node active category selector fix missing from package"
grep -Fq 'logout_html = "" if controls["login_mode"] == "auto"' "$SOURCE_DIR/node_agent/app.py" || die "Node Auto Login Sign out condition missing from package"
grep -Fq 'user = _node_web_current_user(request)' "$SOURCE_DIR/node_agent/app.py" || die "Node direct Playlist root still bypasses Auto Login resolver"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_LOGIN_PREFIX_V2225' "$SOURCE_DIR/node_agent/app.py" || die "Node manual Web Player login prefix fix missing from package"
grep -Fq 'X-StreamForge-WebPlayer-Login' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player login-mode diagnostic header missing from package"
grep -Fq 'webplayer_auto_user_token' "$SOURCE_DIR/node_agent/app.py" || die "Remote Node Auto Login token persistence missing from package"
grep -Fq 'name="auto_user_token"' "$SOURCE_DIR/app/templates/node_webplayer_manage.html" || die "Remote Node Auto Login dropdown token field missing from package"
grep -Fq 'webplayer_auto_user_id' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player auto-login user support missing from package"
grep -Fq 'grid-template-columns:repeat(4,minmax(0,1fr))' "$SOURCE_DIR/node_agent/app.py" || die "Node four-column channel grid missing from package"
grep -Fq 'text-align:center;width:100%' "$SOURCE_DIR/app/templates/player.html" || die "Main centered channel-name CSS missing from package"
grep -Fq 'text-align:center;width:100%' "$SOURCE_DIR/node_agent/app.py" || die "Node centered channel-name CSS missing from package"
grep -Fq 'min-height:0' "$SOURCE_DIR/node_agent/app.py" || die "Node channel-name extra gap fix missing from package"
grep -Fq '<label>Username</label>' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player username label missing from package"
grep -Fq '<label>Expire</label>' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player expire label missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_CLEAN_INFO_V2210' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player clean info layout missing from package"
! grep -Fq 'id="stream-url"' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player stream URL box still exists in package"
! grep -Fq 'id="copy-button"' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player stream copy control still exists in package"
! grep -Fq 'id="player-message"' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player video message overlay still exists in package"
! grep -Fq 'id="msg">Preparing live stream' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player video message overlay still exists in package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_FULL_WIDTH_V2208' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player full-width layout missing from package"
grep -Fq 'max-width:none' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player width cap still active"
grep -Fq 'min-height:min(68vh,760px)' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player large-screen video sizing missing"
grep -Fq 'LIVE STREAMS' "$SOURCE_DIR/app/templates/player.html" || die "Main Web Player live stream browser heading missing from package"
grep -Fq 'LIVE STREAMS' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player live stream browser heading missing from package"
grep -Fq 'x.style.display=ok' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player robust category hide/show logic missing from package"
grep -Fq 'RUN_LEGACY_MIGRATIONS=0' "$SOURCE_UPDATER" || die "Modern migration skip switch missing from package"
grep -Fq 'STREAMFORGE_PACKAGE_FORCE_UPDATER_PRUNE_V2248' "$SOURCE_UPDATER" || die "Package updater-prune policy missing from package"
grep -Fq 'STREAMFORGE_MAIN_PLAYER_CANONICAL_WEBPLAYER_URL_V2249' "$SOURCE_DIR/app/templates/player.html" || die "Main player canonical /web-player URL fix missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYER_CANONICAL_WEBPLAYER_URL_V2249' "$SOURCE_DIR/node_agent/app.py" || die "Node player canonical /web-player URL fix missing from package"
grep -Fq "history.replaceState(null, '', canonicalPlayerUrl)" "$SOURCE_DIR/app/templates/player.html" || die "Main player canonical address replacement missing from package"
grep -Fq "history.replaceState(null,'',canonicalPlayerUrl)" "$SOURCE_DIR/node_agent/app.py" || die "Node player canonical address replacement missing from package"
grep -Fq 'No schema-shape migration required for v8.9; applying Main/Node control-scope compatibility safely.' "$SOURCE_UPDATER" || die "Modern v5.8 compatibility migration path missing from package"
grep -Fq 'Preserving existing channel start/stop settings; legacy desired-state migration skipped.' "$SOURCE_UPDATER" || die "Desired-state migration skip missing from package"
grep -Fq '@app.get("/web-player/favicon")' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player favicon endpoint missing from package"
grep -Fq 'favicon = _node_web_favicon_link(prefix, request)' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player login favicon mapping missing from package"
grep -Fq 'favicon=_node_web_favicon_link(prefix, request)' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player watch favicon mapping missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_PLAYER_LOGO_URL_V2202' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player logo URL fix missing from package"
grep -Fq '@app.get("/web-player/logo")' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player logo endpoint missing from package"
grep -Fq 'login_logo_url = f"{access_prefix}/web-player/logo"' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player local logo mapping missing from package"
! grep -Fq 'Use your playlist/Xtream username and password.' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player helper text should not exist in package"
! grep -Fq 'Browser Web Player' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player subtitle should not exist in package"
grep -Fq 'streamforge_node_web_player' "$SOURCE_DIR/node_agent/app.py" || die "Node web player login cookie missing from package"
grep -Fq 'STREAMFORGE_LOW_DISK_PREFLIGHT_V2184' "$SOURCE_UPDATER" || die "Low-disk preflight missing from package"
grep -Fq 'MIN_FREE_KB=524288' "$SOURCE_UPDATER" || die "Low-disk free-space guard missing from package"
grep -Fq 'Root Playlist/App alias is authoritative when configured' "$SOURCE_DIR/app/main.py" || die "Main Web Player Playlist/App prefix selection fix missing from package"
grep -Fq 'X-StreamForge-Playlist-Prefix' "$SOURCE_DIR/app/main.py" || die "Main Web Player playlist-prefix verification header missing from package"
grep -Fq 'STREAMFORGE_NODE_CROSS_ROLE_REDIRECT_V2186' "$SOURCE_DIR/node_agent/app.py" || die "Node cross-role redirect protection missing from package"
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_ROOT_PRIORITY_V2187' "$SOURCE_DIR/node_agent/app.py" || die "Node Playlist/App exact-root priority fix missing from package"
grep -Fq 'STREAMFORGE_PLAYLIST_ROOT_DIRECT_PLAYER_V2188' "$SOURCE_DIR/app/main.py" || die "Main direct Playlist/App Web Player mapping missing from package"
grep -Fq 'STREAMFORGE_NODE_DIRECT_PLAYLIST_PLAYER_V2188' "$SOURCE_DIR/node_agent/app.py" || die "Node direct Playlist/App Web Player response missing from package"
grep -Fq 'STREAMFORGE_NODE_CANONICAL_PLAYLIST_PATH_V2189' "$SOURCE_DIR/node_agent/app.py" || die "Node canonical Playlist/App path repair missing from package"
grep -Fq 'STREAMFORGE_NODE_SLUG_PAIR_CANONICAL_V2190' "$SOURCE_DIR/node_agent/app.py" || die "Node slug-pair canonical repair missing from package"
grep -Fq 'STREAMFORGE_MAIN_EXACT_ALIAS_NO_REDIRECT_V3036' "$SOURCE_DIR/app/main.py" || die "Main exact-alias rejection missing from package"
! grep -Fq 'def role_redirect(' "$SOURCE_DIR/app/main.py" || die "Main sibling-role redirect fallback remains in package"
grep -Fq 'STREAMFORGE_MAIN_WEB_PLAYER_ROLE_V2192' "$SOURCE_DIR/app/main.py" || die "Main Web Player playlist-role fix missing from package"
grep -Fq 'STREAMFORGE_REQUIREMENTS_SKIP_V2193' "$SOURCE_UPDATER" || die "Dependency reinstall skip logic missing from package"
grep -Fq 'streamforge-cache-clear' "$SOURCE_DIR/scripts/install.sh" || die "Main cache-clear command install missing from package"
grep -Fq 'streamforge-cache-clear' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "Node cache-clear command install missing from package"
grep -Fq 'Usage:' "$SOURCE_DIR/scripts/cache_clear.sh" || die "Cache-clear script missing from package"
grep -Fq '.python-requirements.sha256' "$SOURCE_DIR/node_agent/app.py" || die "Node API dependency stamp logic missing from package"
grep -Fq 'VERSION = VERSION_FILE.read_text' "$SOURCE_DIR/node_agent/app.py" || die "Node VERSION constant missing from package"
! grep -Fq 'APP_VERSION' "$SOURCE_DIR/node_agent/app.py" || die "Undefined APP_VERSION reference still exists in Node Agent"
grep -Fq 'X-StreamForge-Version"] = VERSION' "$SOURCE_DIR/node_agent/app.py" || die "Node version response header fix missing from package"
grep -Fq 'html.escape(VERSION)' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player version marker fix missing from package"
grep -Fq 'STREAMFORGE_NODE_WEB_COOKIE_VALIDITY_V2195' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player cookie validity fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_ROOT_PLAYLIST_SCOPE_REWRITE_V2196' "$SOURCE_DIR/app/main.py" || die "Main root Playlist/App internal scope rewrite fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_EXACT_ALIAS_NO_REDIRECT_V3036' "$SOURCE_DIR/app/main.py" || die "Main exact-alias silent-drop fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_PANEL_UNKNOWN_SILENT_DROP_V2198' "$SOURCE_DIR/app/main.py" || die "Main Panel unknown-link silent-drop fix missing from package"
grep -Fq 'STREAMFORGE_MAIN_PANEL_LINK_PREFIX_V2199' "$SOURCE_DIR/app/main.py" || die "Main Panel link-prefix fix marker missing from package"
grep -Fq 'STREAMFORGE_APP_ROOT' "$SOURCE_DIR/app/templates/base.html" || die "Main Panel active root-path binding missing from package"
grep -Fq "const addRoot=(value)=>" "$SOURCE_DIR/app/static/panel_nav.js" || die "Main Panel URL prefix helper missing from package"
grep -Fq 'STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_V49' "$SOURCE_DIR/app/static/panel_nav.js" || die "Main hidden-route full-reload navigation missing from package"
grep -Fq 'requestAnimationFrame(()=>location.reload())' "$SOURCE_DIR/app/static/panel_nav.js" || die "Main hidden-route native reload trigger missing from package"
grep -Fq 'main-panel-unknown-silent-drop' "$SOURCE_DIR/app/main.py" || die "Main Panel silent-drop route marker missing from package"
grep -Fq 'STREAMFORGE_NODE_PANEL_UNKNOWN_SILENT_DROP_V2198' "$SOURCE_DIR/node_agent/app.py" || die "Node Panel unknown-link silent-drop fix missing from package"
grep -Fq 'def _node_internal_route_exists' "$SOURCE_DIR/node_agent/app.py" || die "Node internal-route validator missing from package"
grep -Fq 'inner_app = getattr(app, "app", app)' "$SOURCE_DIR/node_agent/app.py" || die "Node wrapped FastAPI route lookup missing from package"
grep -Fq 'main-exact-alias-required-silent-drop' "$SOURCE_DIR/app/main.py" || die "Main exact-alias silent-drop route marker missing from package"
grep -Fq 'status_code=418' "$SOURCE_DIR/app/main.py" || die "Main silent-drop trigger status missing from package"
grep -Fq 'proxy_intercept_errors on;' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Main Nginx error interception missing from package"
grep -Fq 'error_page 404 418 = @streamforge_silent_drop;' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Main Nginx silent-drop mapping missing from package"
grep -Fq 'if prefix or stripped != original_path:' "$SOURCE_DIR/app/main.py" || die "Main root alias scope rewrite condition missing from package"
grep -Fq 'return manager.valid_user(token)' "$SOURCE_DIR/node_agent/app.py" || die "Node canonical user validity call missing from package"
! grep -Fq 'user.is_valid()' "$SOURCE_DIR/node_agent/app.py" || die "Invalid NodeUserConfig.is_valid call still exists in package"
grep -Fq 'base = prefix.rstrip("/")' "$SOURCE_DIR/app/main.py" || die "Main Playlist route exact/child matcher missing from package"
grep -Fq 'normalized == base or normalized.startswith(base + "/")' "$SOURCE_DIR/app/main.py" || die "Main Playlist route hierarchy matcher missing from package"
! grep -Fq 'main-canonical-playlist-path' "$SOURCE_DIR/app/main.py" || die "Main canonical Playlist/App redirect remains in package"
! grep -Fq 'matching_current_slugs' "$SOURCE_DIR/app/main.py" || die "Main sibling-alias redirect stripping remains in package"
grep -Fq 'broken_prefix = f"/{panel_slug}/{stream_slug}"' "$SOURCE_DIR/node_agent/app.py" || die "Node broken panel/playlist path detector missing from package"
grep -Fq 'slug-pair-canonical-playlist' "$SOURCE_DIR/node_agent/app.py" || die "Node canonical redirect verification marker missing from package"
grep -Fq 'canonical-playlist-path' "$SOURCE_DIR/node_agent/app.py" || die "Node canonical Playlist/App redirect marker missing from package"
grep -Fq 'canonical-root-playlist-path' "$SOURCE_DIR/node_agent/app.py" || die "Node root Playlist/App stale-path repair missing from package"
grep -Fq 'X-StreamForge-Route' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player route verification header missing from package"
grep -Fq 'X-StreamForge-Version' "$SOURCE_DIR/app/main.py" || die "Main version response header missing from package"
grep -Fq 'if location_targets_stream:' "$SOURCE_DIR/node_agent/app.py" || die "Node cross-role redirect bypass missing from package"
grep -Fq 'location_targets_stream' "$SOURCE_DIR/node_agent/app.py" || die "Node Playlist/App redirect detection missing from package"
grep -Fq 'response.headers["location"] = _node_panel_public_redirect_location(' "$SOURCE_DIR/node_agent/app.py" || die "Node helper-based panel-prefix redirect exclusion missing from package"
grep -Fq 'never the Panel/API prefix' "$SOURCE_DIR/node_agent/app.py" || die "Node Web Player Playlist/App prefix selection fix missing from package"
grep -Fq 'HTTP ${data.response.code}' "$SOURCE_DIR/app/templates/player.html" || die "Web Player playback HTTP error detail missing from package"
grep -Fq 'apt-get clean' "$SOURCE_UPDATER" || die "APT cache cleanup missing from package"
grep -Fq '/root/.cache/pip' "$SOURCE_UPDATER" || die "Pip cache cleanup missing from package"
grep -Fq 'tail -n +3' "$SOURCE_UPDATER" || die "Old rollback-backup pruning missing from package"
! grep -Fq 'remaining channel replicas were preserved' "$SOURCE_DIR/app/main.py" || die "Obsolete Node delete preservation message still present"
grep -Fq 'rm -f /usr/local/sbin/streamforge-uninstall' "$SOURCE_DIR/scripts/uninstall.sh" || die "Uninstall self-cleanup missing from package"
grep -Fq '/var/cache/streamforge-node' "$SOURCE_DIR/scripts/uninstall.sh" || die "Node cache cleanup missing from package"
grep -Fq '/var/cache/streamforge' "$SOURCE_DIR/scripts/uninstall.sh" || die "Main cache cleanup missing from package"
grep -Fq '/opt/streamforge-node/logo' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "Node logo target directory missing from package"
grep -Fq 'v2.1.179 Main Channels layout repair' "$SOURCE_DIR/app/static/style.css" || die "Channels layout repair CSS missing from package"
grep -Fq 'grid-column:1/-1!important' "$SOURCE_DIR/app/static/style.css" || die "Channels full-width encoding profile layout missing from package"
grep -Fq 'node-enabled-head' "$SOURCE_DIR/app/templates/node_form.html" || die "Node header Enabled control missing from package"
grep -Fq 'saved-secret-badge' "$SOURCE_DIR/app/templates/node_form.html" || die "Saved SSH password indicator missing from package"
grep -Fq 'Node Panel/API access URLs' "$SOURCE_DIR/app/templates/node_form.html" || die "Node Panel/API URL field missing from package"
grep -Fq 'v2.1.178 compact Remote Node connection editor' "$SOURCE_DIR/app/static/style.css" || die "Compact Node connection CSS missing from package"
grep -Fq 'v2.1.177 Node edit branding' "$SOURCE_DIR/app/static/style.css" || die "Node branding inline CSS missing from package"
grep -Fq 'cleanup_branding_asset(old_logo_url)' "$SOURCE_DIR/app/main.py" || die "Main branding old-logo cleanup missing from package"
grep -Fq 'cleanup_branding_asset(old_favicon_url)' "$SOURCE_DIR/app/main.py" || die "Main favicon old-file cleanup missing from package"
grep -Fq 'cleanup_node_favicon_file(old_favicon)' "$SOURCE_DIR/app/main.py" || die "Node favicon old-file cleanup missing from package"
grep -Fq 'cleanup_logo_if_unused(db, old_logo)' "$SOURCE_DIR/app/main.py" || die "Channel old-logo cleanup missing from package"
grep -Fq 'node_favicon_content_base64' "$SOURCE_DIR/app/node_manager.py" || die "Node favicon sync payload missing from package"
grep -Fq 'remove_node_favicon' "$SOURCE_DIR/node_agent/app.py" || die "Node favicon removal sync missing from package"
! grep -Fq 'name="favicon_file"' "$SOURCE_DIR/node_agent/app.py" || die "Node-local favicon upload UI still present"
grep -Fq 'current-favicon-card' "$SOURCE_DIR/app/templates/system_branding.html" || die "Main favicon preview styling missing from package"
grep -Fq 'name="favicon_file"' "$SOURCE_DIR/app/templates/system_branding.html" || die "Main favicon upload UI missing from package"
grep -Fq 'branding.favicon_url' "$SOURCE_DIR/app/templates/base.html" || die "Main favicon link missing from package"
grep -Fq 'NODE_FAVICON_ROOT' "$SOURCE_DIR/node_agent/app.py" || die "Node favicon storage missing from package"
grep -Fq '@app.get("/panel/favicon")' "$SOURCE_DIR/node_agent/app.py" || die "Node favicon route missing from package"
grep -Fq 'ResizeObserver' "$SOURCE_DIR/node_agent/app.py" || die "Node dashboard chart resize observer missing from package"
! grep -Fq 'data-node-chart-range' "$SOURCE_DIR/node_agent/app.py" || die "Node dashboard historical range labels still present"
grep -Fq 'node-metrics-chart-title' "$SOURCE_DIR/node_agent/app.py" || die "Node dashboard large-screen chart title styling missing from package"
grep -Fq 'ResizeObserver' "$SOURCE_DIR/app/static/dashboard.js" || die "Dashboard chart resize observer missing from package"
! grep -Fq 'data-chart-range' "$SOURCE_DIR/app/templates/dashboard.html" || die "Dashboard historical range labels still present"
grep -Fq 'v2.1.171 dashboard metric charts' "$SOURCE_DIR/app/static/style.css" || die "Large-screen metrics chart styling missing from package"
grep -Fq 'def __init__(self, ttl_seconds: int = 5)' "$SOURCE_DIR/app/viewer_tracking.py" || die "Main viewer session timeout is not 5 seconds"
grep -Fq 'VIEWER_TTL = max(5, int(os.getenv("STREAMFORGE_NODE_VIEWER_TTL", "5")))' "$SOURCE_DIR/node_agent/app.py" || die "Node session timeout is not 5 seconds"
! grep -Fq 'user.user_type != "restream" and not connection_tracker.allow' "$SOURCE_DIR/app/main.py" || die "Restream connection-limit bypass still present in package"
grep -Fq 'STREAMFORGE_STRICT_MAX_CONNECTIONS' "$SOURCE_DIR/node_agent/app.py" || die "Node strict max-connections enforcement missing from package"
! grep -Fq 'removed=len(sessions); sessions.clear()' "$SOURCE_DIR/app/main.py" || die "Main one-slot replacement behavior still present"
! grep -Fq 'Atomic one-slot handover for channel switching' "$SOURCE_DIR/node_agent/app.py" || die "Node one-slot replacement behavior still present"
python3 - "$SOURCE_DIR/app/main.py" <<'PY_LOCAL_BACKUP_ROUTE_SOURCE'
import sys
text = open(sys.argv[1], encoding="utf-8").read()
lu = text.index('@app.post("/system/backups/local/update"')
du = text.index('@app.post("/system/backups/{target_id}/update"')
lr = text.index('@app.post("/system/backups/local/run"')
dr = text.index('@app.post("/system/backups/{target_id}/run"')
if not (lu < du and lr < dr):
    raise SystemExit("Static local backup routes are shadowed by dynamic target routes")
PY_LOCAL_BACKUP_ROUTE_SOURCE
grep -Fq 'Success · rotation warning:' "$SOURCE_DIR/app/backup_manager.py" || die "Backup rotation warning preservation missing from package"
grep -Eq 'playback_start:[[:space:]]*bool[[:space:]]*=[[:space:]]*False' "$SOURCE_DIR/app/main.py" || die "Playback-start handover support missing from package"
grep -Fq 'self._active_total=0' "$SOURCE_DIR/app/main.py" || die "High-concurrency ConnectionTracker missing from package"
grep -Fq 'proxy_pass http://streamforge_main_backend;' "$SOURCE_DIR/scripts/verify_main_install.py" || die "Named Main upstream verifier missing from package"
grep -Fq 'theoretical_connection_ceiling' "$SOURCE_DIR/scripts/capacity_audit.py" || die "Capacity ceiling calculation missing from package"
grep -Fq 'mini_probe("http://127.0.0.1:8800/health"' "$SOURCE_DIR/scripts/capacity_audit.py" || die "Bounded local capacity probe missing from package"
grep -Fq 'api_url: str = Form(...)' "$SOURCE_DIR/app/main.py" || die "Auto install Manual-style Panel/API URL field missing from package"
grep -Fq 'playlist_url: str = Form(...)' "$SOURCE_DIR/app/main.py" || die "Auto install Manual-style Playlist/App URL field missing from package"
grep -Fq 'enabled: Optional[str] = Form(None)' "$SOURCE_DIR/app/main.py" || die "Auto install Enabled field missing from package"
grep -Fq 'verify_tls: Optional[str] = Form(None)' "$SOURCE_DIR/app/main.py" || die "Auto install Verify TLS field missing from package"
grep -Fq 'access_connect_urls=[result.api_url]' "$SOURCE_DIR/app/main.py" || die "Auto install first-sync root URL fallback missing from package"
grep -Fq 'Same Node configuration as Add manually, plus SSH installation' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto install exact Manual-field UI missing from package"
grep -Fq 'Node Panel/API access URLs' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto install Panel/API URL textarea missing from package"
grep -Fq 'Playlist/App access URLs' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto install Playlist/App URL textarea missing from package"
grep -Fq 'Verify HTTPS certificate' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto install Verify TLS UI missing from package"
grep -Fq '[[ "$(cat "$APP_DIR/VERSION")" == "12.12" ]]' "$SOURCE_UPDATER" || die "Live Main VERSION validator is stale in package"
grep -Fq '[[ "$(cat "$APP_DIR/node_agent/VERSION")" == "12.12" ]]' "$SOURCE_UPDATER" || die "Live Node VERSION validator is stale in package"
grep -Fq 'STREAMFORGE_CANONICAL_PROTOCOL_REDIRECT_V35' "$SOURCE_DIR/app/main.py" || die "v3.5 Main protocol marker missing before install"
grep -Fq '[[ "$LIVE_VERSION" == "12.12" ]]' "$SOURCE_UPDATER" || die "Live service VERSION validator is stale in package"
grep -Fq 'set_env_default STREAMFORGE_FFPROBE_BIN /usr/bin/ffprobe' "$SOURCE_UPDATER" || die "Existing-install FFprobe config backfill missing from package"
grep -Fq 'set_env_default STREAMFORGE_HTTP_USER_AGENT' "$SOURCE_UPDATER" || die "Existing-install HTTP User-Agent backfill missing from package"
grep -Fq 'set_env_default STREAMFORGE_RELAY_BASE_URL' "$SOURCE_UPDATER" || die "Existing-install relay config backfill missing from package"
grep -Fq 'set_env_default STREAMFORGE_AUTO_RESTART_STALL_SECONDS 30' "$SOURCE_UPDATER" || die "Existing-install restart config backfill missing from package"
grep -Fq 'content:none!important' "$SOURCE_DIR/app/static/style.css" || die "Legacy category chevron suppression missing from package"
grep -Fq 'background-position:' "$SOURCE_DIR/app/static/style.css" || die "Unified dropdown chevron positioning missing from package"
grep -Fq 'html body select:not([multiple]):not(#streamforge-dropdown-never)' "$SOURCE_DIR/app/static/style.css" || die "Main high-specificity dropdown normalization missing from package"
grep -Fq 'html body select[multiple][size="1"]:not(#streamforge-dropdown-never)' "$SOURCE_DIR/app/static/style.css" || die "Main multi-select dropdown normalization missing from package"
grep -Fq 'STREAMFORGE_NODE_DROPDOWN_FULL_AUDIT' "$SOURCE_DIR/node_agent/app.py" || die "Node authoritative dropdown styling missing from package"
grep -Fq 'select[multiple][size="1"]{' "$SOURCE_DIR/app/static/style.css" || die "Size-1 multi-select dropdown-height styling missing from package"
grep -Fq 'select[multiple] option:checked{' "$SOURCE_DIR/app/static/style.css" || die "Multi-select selected-option styling missing from package"
grep -Fq 'select:not([multiple]){' "$SOURCE_DIR/app/static/style.css" || die "Unified select arrow selector missing from package"
grep -Fq 'select[multiple]{' "$SOURCE_DIR/app/static/style.css" || die "Multiple-select arrow exclusion missing from package"
grep -Fq 'height:40px!important' "$SOURCE_DIR/app/static/style.css" || die "Unified dropdown height missing from package"
grep -Fq 'border-radius:8px!important' "$SOURCE_DIR/app/static/style.css" || die "Unified dropdown radius missing from package"
grep -Fq 'background-color:#0b1118!important' "$SOURCE_DIR/app/static/style.css" || die "Unified dropdown background missing from package"
grep -Fq 'select:not([multiple]):focus{' "$SOURCE_DIR/app/static/style.css" || die "Unified dropdown focus styling missing from package"
grep -Fq '.backup-form-mode-edit[hidden]' "$SOURCE_DIR/app/templates/backups.html" || die "Remote Cancel edit must stay hidden outside Edit mode in package"
grep -Fq 'backup-edit-heading-copy' "$SOURCE_DIR/app/templates/backups.html" || die "Remote backup edit heading layout missing from package"
grep -Fq 'def local_backup_settings()' "$SOURCE_DIR/app/backup_manager.py" || die "Main Server local backup settings backend missing from package"
grep -Fq 'def save_local_backup_settings(' "$SOURCE_DIR/app/backup_manager.py" || die "Main Server local backup settings save backend missing from package"
grep -Fq 'grid-template-rows:auto 40px auto' "$SOURCE_DIR/app/templates/backups.html" || die "Backup top-row equal-height layout missing from package"
grep -Fq '"rotation_keep": max(1, min(500, int(rotation_keep or 20)))' "$SOURCE_DIR/app/backup_manager.py" || die "Rotation persistence on add missing from package"
grep -Fq 'target["rotation_keep"] = max(1, min(500, int(rotation_keep or 20)))' "$SOURCE_DIR/app/backup_manager.py" || die "Rotation persistence on edit missing from package"
grep -Fq 'data-backup-target-form' "$SOURCE_DIR/app/templates/backups.html" || die "Backup destination form missing from dedicated Backups page package"
grep -Fq 'Test destination' "$SOURCE_DIR/app/templates/backups.html" || die "Backup destination test UI missing from dedicated Backups page package"
grep -Fq 'data-backup-kind' "$SOURCE_DIR/app/templates/backups.html" || die "Backup type selector missing from dedicated Backups page package"
grep -Fq 'name="schedule_hours"' "$SOURCE_DIR/app/templates/backups.html" || die "Backup interval control missing from dedicated Backups page package"
grep -Fq '"rotation_keep"' "$SOURCE_DIR/app/backup_manager.py" || die "Per-destination rotation backend missing from package"
grep -Fq '_prune_target_backup_archives(target_id, keep=keep)' "$SOURCE_DIR/app/backup_manager.py" || die "Remote backup rotation missing from package"
! grep -Fq '<h2>Backups</h2>' "$SOURCE_DIR/app/templates/system_branding.html" || die "Backups section still present in Settings package template"
grep -Fq 'grid-template-columns:180px minmax(0,1fr)' "$SOURCE_DIR/app/templates/backup_files.html" || die "Backup destination summary layout fix missing from package"
grep -Fq 'import re' "$SOURCE_DIR/app/backup_manager.py" || die "Remote backup regex runtime import missing from package"
grep -Fq 'from datetime import datetime' "$SOURCE_DIR/app/backup_manager.py" || die "Google Drive modified-time runtime import missing from package"
! grep -Fq '|urlencode' "$SOURCE_DIR/app/templates/system_branding.html" || die "Unsupported Jinja urlencode filter remains in backup settings page"
grep -Fq 'Delete this saved backup permanently' "$SOURCE_DIR/app/templates/local_backup_files.html" || die "Local saved backup delete UI missing from dedicated local backup page package"
grep -Fq 'def list_local_backup_archives(' "$SOURCE_DIR/app/backup_manager.py" || die "Retained backup listing backend missing from package"
grep -Fq '/system/backups/archive/{archive_name}/download' "$SOURCE_DIR/app/main.py" || die "Backup download route missing from package"
grep -Fq '/system/backups/archive/{archive_name}/restore' "$SOURCE_DIR/app/main.py" || die "Saved backup restore route missing from package"
grep -Fq '_prune_local_backup_archives(keep=local_keep)' "$SOURCE_DIR/app/backup_manager.py" || die "Shared local backup retention calculation missing from package"
grep -Fq 'target.get("rotation_keep")' "$SOURCE_DIR/app/backup_manager.py" || die "Per-destination rotation lookup missing from package"
grep -Fq 'local_keep = int(local_backup_settings().get("rotation_keep") or 20)' "$SOURCE_DIR/app/backup_manager.py" || die "Independent local backup retention lookup missing from package"
grep -Fq '_prune_local_backup_archives(keep=local_keep)' "$SOURCE_DIR/app/backup_manager.py" || die "Independent local retained-backup pruning missing from package"
grep -Fq '_prune_target_backup_archives(target_id, keep=keep)' "$SOURCE_DIR/app/backup_manager.py" || die "Per-destination remote rotation pruning missing from package"
grep -Fq '_prune_target_backup_archives(target_id, keep=keep)' "$SOURCE_DIR/app/backup_manager.py" || die "Configured destination backup rotation missing from package"
grep -Fq '"restore_backup"' "$SOURCE_DIR/scripts/main_system_control.py" || die "Privileged Main restore helper missing from package"
grep -Fq 'action not in {"restart_service", "reboot_server", "restore_backup"}' "$SOURCE_DIR/app/main.py" || die "Main Server action route allowlist missing from package"
! grep -Fq '<h2>Backups</h2>' "$SOURCE_DIR/node_agent/app.py" || die "Node backup Settings option still present in package"
! grep -Fq "/panel/manage/backups/add" "$SOURCE_DIR/node_agent/app.py" || die "Node backup routes still present in package"
! grep -Fq "_node_backup_scheduler_loop" "$SOURCE_DIR/node_agent/app.py" || die "Node backup scheduler still present in package"
grep -Fq 'fetch(endpoint.toString()' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "Main online users AJAX refresh missing from package"
grep -Fq 'background:#111e2b!important' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "Main online users refresh selector visible styling missing from package"
grep -Fq 'border:1px solid #3b5b7c!important' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "Main online users refresh selector border missing from package"
! grep -Fq '<h1>Login</h1>' "$SOURCE_DIR/app/templates/login.html" || die "Login heading still present in package"
grep -Fq 'display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center' "$SOURCE_DIR/app/static/style.css" || die "Centered login branding CSS missing from package"
grep -Fq '.form-section + .form-section' "$SOURCE_DIR/app/templates/system_branding.html" || die "Settings section spacing CSS missing from package"
grep -Fq '.panel-head{' "$SOURCE_DIR/app/templates/system_branding.html" || die "Settings panel spacing CSS missing from package"
grep -Fq 'ExecStart=/usr/bin/gunicorn app.main:app' "$SOURCE_DIR/deploy/streamforge.service" || die "Direct /usr/bin/gunicorn Main service definition missing from package"
grep -Fq -- '--workers 1 --worker-class uvicorn_worker.UvicornWorker' "$SOURCE_DIR/deploy/streamforge.service" || die "Main single ASGI worker configuration missing from package"
! grep -Eq 'venv/bin|python3 -m uvicorn|/usr/bin/env python3' "$SOURCE_DIR/deploy/streamforge.service" || die "Packaged Main service still uses a venv/direct-Uvicorn runtime"
grep -Eq '^gunicorn>=23,<27$' "$SOURCE_DIR/requirements.txt" || die "Main Gunicorn dependency missing from package"
grep -Eq '^uvicorn-worker>=0.4,<1.0$' "$SOURCE_DIR/requirements.txt" || die "Main Uvicorn worker dependency missing from package"
grep -Eq '^cryptography>=43,<47$' "$SOURCE_DIR/requirements.txt" || die "Main AES-GCM cryptography dependency missing from package"
grep -Eq '^pyOpenSSL>=25\.3,<26\.0$' "$SOURCE_DIR/requirements.txt" || die "v10.30 Main Certbot-compatible pyOpenSSL pin missing from package"
grep -Eq '^redis>=5\.0,<7\.0$' "$SOURCE_DIR/requirements.txt" || die "Main Redis dependency missing from package"
grep -Eq '^cryptography>=43,<47$' "$SOURCE_DIR/node_agent/requirements.txt" || die "Node AES-GCM cryptography dependency missing from package"
grep -Eq '^pyOpenSSL>=25\.3,<26\.0$' "$SOURCE_DIR/node_agent/requirements.txt" || die "v10.30 Node Certbot-compatible pyOpenSSL pin missing from package"
grep -Eq '^gunicorn>=23,<27$' "$SOURCE_DIR/node_agent/requirements.txt" || die "Node Gunicorn dependency missing from package"
grep -Eq '^uvicorn-worker>=0.4,<1.0$' "$SOURCE_DIR/node_agent/requirements.txt" || die "Node Uvicorn worker dependency missing from package"
grep -Fq 'ExecStart=/usr/bin/gunicorn app:app' "$SOURCE_DIR/node_agent/deploy/streamforge-node.service" || die "Direct /usr/bin/gunicorn Node service definition missing from package"
grep -Fq -- '--workers 1 --worker-class uvicorn_worker.UvicornWorker' "$SOURCE_DIR/node_agent/deploy/streamforge-node.service" || die "Node single ASGI worker configuration missing from package"
! grep -Eq 'venv/bin|python3 -m uvicorn|/usr/bin/env python3' "$SOURCE_DIR/node_agent/deploy/streamforge-node.service" || die "Packaged Node service still uses a venv/direct-Uvicorn runtime"
grep -Fq 'def _gunicorn_asgi_command' "$SOURCE_DIR/node_agent/app.py" || die "Node child-listener Gunicorn command builder missing from package"
[[ "$(grep -Fc 'cmd = _gunicorn_asgi_command(' "$SOURCE_DIR/node_agent/app.py")" -eq 2 ]] || die "Every Node child listener is not using Gunicorn"
! grep -Fq 'sys.executable, "-m", "uvicorn"' "$SOURCE_DIR/node_agent/app.py" || die "Direct child Uvicorn launcher remains in package"
grep -Fq 'Gunicorn systemd migration requires a saved SSH Node update' "$SOURCE_DIR/node_agent/app.py" || die "API-only systemd migration guard missing from package"
grep -Fq 'APP_VERSION in {' "$SOURCE_DIR/app/main.py" || die "One-time saved-SSH Gunicorn migration guard missing from package"
grep -Fq '"2.1.205"' "$SOURCE_DIR/app/main.py" || die "v3.2 Main compatibility marker missing from package"
grep -Fq "[('activity','Activity log'),('access','Access log'),('client','Client log'),('system','System log')]" "$SOURCE_DIR/app/templates/logs.html" || die "Main Activity-first log tabs missing from package"
grep -Fq "[('activity','Activity log'),('access','Access log'),('client','Client log'),('system','System log')]" "$SOURCE_DIR/node_agent/app.py" || die "Node Activity-first log tabs missing from package"
grep -Fq 'def issue_playback_keys(' "$SOURCE_DIR/app/playback_keys.py" || die "Batch playback-key issuer missing from package"
grep -Fq 'ThreadPoolExecutor(max_workers=workers' "$SOURCE_DIR/app/node_manager.py" || die "Parallel Node catalogue status fetch missing from package"
grep -Fq 'playback_keys = issue_playback_keys(' "$SOURCE_DIR/app/main.py" || die "Fast playlist batch-key path missing from package"
grep -Fq '# v2.1.15: define the batch in this endpoint before the get.php loop.' "$SOURCE_DIR/app/main.py" || die "get.php playback-key regression fix missing from package"
grep -Fq '# v2.1.15: category responses never require request-scoped playback keys.' "$SOURCE_DIR/app/main.py" || die "Xtream category regression fix missing from package"
grep -Fq 'class="table-wrap node-log-table-wrap"' "$SOURCE_DIR/node_agent/app.py" || die "Node log table structure fix missing from package"
grep -Fq 'class="node-log-panel"' "$SOURCE_DIR/node_agent/app.py" || die "Unified Node log card missing from package"
grep -Fq 'class="node-log-tabs"' "$SOURCE_DIR/node_agent/app.py" || die "Node log tabs structure missing from package"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_USER_NO_DETAILS_V99R17' "$SOURCE_DIR/node_agent/app.py" || die "v9.9 r17 Node Client log User/no-Details UI missing from package"
grep -Fq '.node-log-col-time{width:190px}' "$SOURCE_DIR/node_agent/app.py" || die "Stable Node log column sizing missing from package"
grep -Fq '/* v2.1.24 stable Main-to-Node update page */' "$SOURCE_DIR/app/static/style.css" || die "Remote update page layout fix missing from package"
grep -Fq '<div class="node-update-page">' "$SOURCE_DIR/app/templates/node_update.html" || die "Spaced update page container missing from package"
grep -Fq '.node-update-page{display:grid;gap:16px}' "$SOURCE_DIR/app/static/style.css" || die "Update card spacing missing from package"
grep -Fq 'node-update-notice' "$SOURCE_DIR/app/templates/node_update.html" || die "Update notice spacing class missing from package"
grep -Fq '.node-update-notice{margin:0 0 16px}' "$SOURCE_DIR/app/static/style.css" || die "Update notice bottom spacing missing from package"
grep -Fq 'def set_network_interfaces' "$SOURCE_DIR/app/system_metrics.py" || die "Selectable Main network sampler missing from package"
grep -Fq 'name="network_interfaces" multiple' "$SOURCE_DIR/app/templates/system_branding.html" || die "Network interface multi-select missing from package"
grep -Fq 'value="__all__"' "$SOURCE_DIR/app/templates/system_branding.html" || die "All-interfaces option missing from package"
grep -Fq 'system_metrics.set_network_interfaces(selected_interfaces)' "$SOURCE_DIR/app/main.py" || die "Saved NIC selection is not applied to Main metrics"
grep -Fq '.backup-key-field,.backup-key-field input,.graph-span-field,.graph-span-field select{width:100%;max-width:none}' "$SOURCE_DIR/app/templates/system_branding.html" || die "Full-width Settings controls missing from package"
grep -Fq '/system/backups/google/connect' "$SOURCE_DIR/app/main.py" || die "Direct Google sign-in route missing from package"
grep -Fq 'https://oauth2.googleapis.com/token' "$SOURCE_DIR/app/main.py" || die "Google OAuth token exchange missing from package"
grep -Fq 'Sign in with Google' "$SOURCE_DIR/app/templates/backups.html" || die "Google sign-in UI missing from dedicated Backups page package"
grep -Fq 'upload_file_to_google_drive' "$SOURCE_DIR/app/backup_manager.py" || die "Direct Google Drive uploader missing from package"
grep -Fq 'tarfile.open(partial, mode="w:gz")' "$SOURCE_DIR/app/backup_manager.py" || die "Service-safe backup archive writer missing from package"
grep -Fq 'def test_destination(' "$SOURCE_DIR/app/backup_manager.py" || die "Backup destination tester missing from package"
grep -Fq 'from cryptography.fernet import Fernet' "$SOURCE_DIR/app/backup_manager.py" || die "Encrypted backup credential support missing from package"
grep -Fq 'def _upload_secure_smb(' "$SOURCE_DIR/app/backup_manager.py" || die "Authenticated SMB uploader missing from package"
grep -Fq 'password=str(password or "") or None' "$SOURCE_DIR/app/backup_manager.py" || die "SSH password authentication missing from package"
grep -Fq 'paramiko.SSHClient()' "$SOURCE_DIR/app/backup_manager.py" || die "SSH/SFTP backend missing from package"
grep -Fq 'findmnt' "$SOURCE_DIR/app/backup_manager.py" || die "SMB mount validation missing from package"
grep -Fq 'Test destination' "$SOURCE_DIR/app/templates/backups.html" || die "Backup destination test UI missing from package"
grep -Fq 'google-connected-bar' "$SOURCE_DIR/app/templates/backups.html" || die "Compact Google backup UI missing from package"
grep -Fq 'google-oauth-field' "$SOURCE_DIR/app/templates/backups.html" || die "Google OAuth fields missing from dedicated Backups page package"
grep -Fq 'data-google-oauth-input' "$SOURCE_DIR/app/templates/backups.html" || die "Google OAuth inputs missing from dedicated Backups page package"
grep -Fq 'data-google-backup-card' "$SOURCE_DIR/app/templates/backups.html" || die "Google Drive backup panel missing from dedicated Backups page package"
grep -Fq 'input.disabled = googleCard.hidden' "$SOURCE_DIR/app/templates/backups.html" || die "Conditional Google OAuth validation handling missing from package"
grep -Fq 'data-google-backup-card hidden' "$SOURCE_DIR/app/templates/backups.html" || die "Conditional Google backup panel missing from package"
grep -Fq 'backup-destination-field' "$SOURCE_DIR/app/templates/backups.html" || die "Single-row backup fields layout missing from package"
grep -Fq '>Interval' "$SOURCE_DIR/app/templates/backups.html" || die "Backup interval label missing from package"
grep -Fq 'data-backup-edit' "$SOURCE_DIR/app/templates/backups.html" || die "Backup destination Edit UI missing from package"
grep -Fq 'form.classList.add('\''is-editing'\'')' "$SOURCE_DIR/app/templates/backups.html" || die "Backup edit-mode UI lock missing from package"
grep -Fq 'kind = str(target.get("kind") or "local")' "$SOURCE_DIR/app/backup_manager.py" || die "Backup type-lock backend missing from package"
grep -Fq 'input.disabled = googleCard.hidden' "$SOURCE_DIR/app/templates/backups.html" || die "Hidden Google OAuth validation fix missing from package"
grep -Fq 'def update_target(' "$SOURCE_DIR/app/backup_manager.py" || die "Backup destination update backend missing from package"
grep -Fq '/system/backups/{target_id}/update' "$SOURCE_DIR/app/main.py" || die "Backup destination update route missing from package"
grep -Fq 'grid-column:1/-1' "$SOURCE_DIR/app/templates/backups.html" || die "Full-width backup destination/Google panel layout missing from package"
grep -Fq 'value="" placeholder="Backup name"' "$SOURCE_DIR/app/templates/backups.html" || die "Blank backup name default missing from package"
grep -Fq 'backup-schedule-field' "$SOURCE_DIR/app/templates/backups.html" || die "Backup form grid fix missing from package"
grep -Fq 'runtime STREAMFORGE_* snapshot' "$SOURCE_DIR/app/backup_manager.py" || die "Runtime environment snapshot missing from package"
grep -Fq 'Save &amp; backup now' "$SOURCE_DIR/app/templates/backups.html" || die "Google backup-now UI missing from package"
grep -Fq 'run_now: int = Form(0)' "$SOURCE_DIR/app/main.py" || die "Backup-now endpoint support missing from package"
grep -Fq -- '--worker-tmp-dir /var/lib/streamforge/gunicorn-tmp' "$SOURCE_DIR/deploy/streamforge.service" || die "Dedicated Gunicorn worker temp directory missing from package"
grep -Fq '.user-max-connections-field{width:100%}' "$SOURCE_DIR/app/static/style.css" || die "Full-width playlist-user connection field missing from package"
grep -Fq 'multiple size="1" data-network-interface-select' "$SOURCE_DIR/app/templates/system_branding.html" || die "Normal-height network selector missing from package"
grep -Fq '.metrics-settings-grid input:not([type="checkbox"]),.metrics-settings-grid select{height:40px;min-height:40px' "$SOURCE_DIR/app/templates/system_branding.html" || die "Uniform Settings control height missing from package"
! grep -Fq 'How often a graph point is saved.' "$SOURCE_DIR/app/templates/system_branding.html" || die "Removed graph sample helper text still present in package"
grep -Fq 'input:not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="hidden"]),select:not([multiple]):not([data-uniform-height-exempt]):not([data-native-height]){height:40px!important;min-height:40px!important' "$SOURCE_DIR/app/static/style.css" || die "Main uniform form control height missing from package"
grep -Fq 'input:not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="hidden"]),select:not([multiple]):not([data-uniform-height-exempt]):not([data-native-height]){height:40px!important;min-height:40px!important' "$SOURCE_DIR/node_agent/app.py" || die "Node uniform form control height missing from package"
! grep -Fqi 'not scanned' "$SOURCE_DIR/app/templates/channel_form.html" || die "Main channel Not scanned placeholder still present in package"
! grep -Fqi 'not scanned' "$SOURCE_DIR/app/static/app.js" || die "Main dynamic channel Not scanned placeholder still present in package"
! grep -Fqi 'not scanned' "$SOURCE_DIR/node_agent/app.py" || die "Node channel Not scanned placeholder still present in package"
grep -Fq 'label:has(>input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"])),label:has(>select),label:has(>textarea){gap:7px!important;row-gap:7px!important}' "$SOURCE_DIR/app/static/style.css" || die "Main uniform field spacing missing from package"
grep -Fq 'label:has(>input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"])),label:has(>select),label:has(>textarea){gap:7px!important;row-gap:7px!important}' "$SOURCE_DIR/node_agent/app.py" || die "Node uniform field spacing missing from package"
grep -Fq 'data-user-load-balance-toggle' "$SOURCE_DIR/app/templates/user_form.html" || die "Load-balance-aware node selector toggle missing from package"
grep -Fq 'enforceNodeLimit' "$SOURCE_DIR/app/static/app.js" || die "Load-balance-aware node selector UI missing from package"
grep -Fq 'effective_node_ids = effective_node_ids[:1]' "$SOURCE_DIR/app/main.py" || die "Single-node backend enforcement missing from package"
grep -Fq '.form-grid>label:not(.check){display:grid;grid-template-rows:auto auto minmax(16px,auto);align-content:start}' "$SOURCE_DIR/app/static/style.css" || die "Main reserved helper-row alignment missing from package"
grep -Fq '.form-grid>label:not(.check),.panel-form>.grid>label:not(.check),.grid.panel-form>label:not(.check){display:grid;grid-template-rows:auto auto minmax(16px,auto);align-content:start}' "$SOURCE_DIR/node_agent/app.py" || die "Node reserved helper-row alignment missing from package"
grep -Fq '.playlist-profile-panel td.actions{display:table-cell;white-space:nowrap}' "$SOURCE_DIR/app/static/style.css" || die "Playlist Actions table-cell fix missing from package"
grep -Fq '.playlist-profile-panel tbody tr:last-child td{border-bottom:0}' "$SOURCE_DIR/app/static/style.css" || die "Playlist final separator cleanup missing from package"
grep -Fq 'class="panel-head user-list-head"' "$SOURCE_DIR/app/templates/users.html" || die "Users header filter placement missing from package"
! grep -Fq 'class="panel user-filter-panel"' "$SOURCE_DIR/app/templates/users.html" || die "Old standalone user filter panel still present in package"
grep -Fq '.user-add-button{flex:0 0 auto;white-space:nowrap' "$SOURCE_DIR/app/static/style.css" || die "Add user no-wrap fix missing from package"
grep -Fq 'class="form-grid metrics-settings-grid"' "$SOURCE_DIR/app/templates/system_branding.html" || die "Four-column capacity settings markup missing from package"
grep -Fq '.metrics-settings-grid{grid-template-columns:repeat(4,minmax(0,1fr))}' "$SOURCE_DIR/app/templates/system_branding.html" || die "Four-column capacity settings layout missing from package"
grep -Fq 'class="user-max-connections-field"' "$SOURCE_DIR/app/templates/user_form.html" || die "Compact playlist-user connection field missing from package"
grep -Fq '<details class="node-update-log-box">' "$SOURCE_DIR/app/templates/node_update.html" || die "Collapsed live update log missing from package"
! grep -Fq '<details class="node-update-log-box" open>' "$SOURCE_DIR/app/templates/node_update.html" || die "Live update log still defaults to expanded"
grep -Fq 'white-space:pre-wrap!important;overflow-x:hidden!important' "$SOURCE_DIR/app/static/style.css" || die "Wrapped update log overflow fix missing from package"
grep -Fq '.node-update-steps{grid-template-columns:repeat(3,minmax(0,1fr))!important}' "$SOURCE_DIR/app/static/style.css" || die "Responsive update phase layout missing from package"
grep -Fq 'allow_remote_fetch: bool = True' "$SOURCE_DIR/app/node_manager.py" || die "Cache-only Node runtime option missing from package"
grep -Fq 'public_base=base' "$SOURCE_DIR/app/main.py" || die "Single-query playlist logo base reuse missing from package"
grep -Fq 'def public_local_node(db: Session) -> Node:' "$SOURCE_DIR/app/main.py" || die "Read-only public Local Node lookup missing from package"
! grep -Fq 'local_node = ensure_local_node(db)' "$SOURCE_DIR/app/main.py" || die "Xtream catalogue still takes a Local Node write lock"
grep -Fq 'db: Session | None = None' "$SOURCE_DIR/app/audit_log.py" || die "Request-scoped audit logging missing from package"
grep -Fq 'STREAMFORGE_MAIN_XTREAM_PLAYLIST_REQUEST_DB_REUSE_V1061' "$SOURCE_DIR/app/main.py" || die "v10.68 Xtream playlist Client-log request DB reuse marker missing"
grep -Fq 'STREAMFORGE_STATIC_ROUTE_CATALOG_FALLBACK_V100' "$SOURCE_DIR/app/main.py" || die "v10.6 Static-route catalogue fallback missing from package"
grep -Fq 'and bool(item.get("hls_ready"))' "$SOURCE_DIR/app/main.py" || die "Online HLS readiness filter missing from package"
grep -Fq 'now - cached[0] < cache_ttl' "$SOURCE_DIR/app/node_manager.py" || die "Bulk Node success/offline status cache TTL missing from package"
grep -Fq '<table class="session-table">' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "Structured live-session table missing from package"
grep -Fq 'class="session-delivery-cell"' "$SOURCE_DIR/app/templates/viewer_sessions.html" || die "Live-session Delivery cell fix missing from package"
grep -Fq '.session-col-delivery{width:155px}' "$SOURCE_DIR/app/static/style.css" || die "Live-session Delivery column width missing from package"
grep -Fq 'white-space:nowrap;line-height:1.2' "$SOURCE_DIR/app/static/style.css" || die "Unbroken Delivery badge styling missing from package"
grep -Fq 'class="table-wrap node-session-table-wrap"' "$SOURCE_DIR/node_agent/app.py" || die "Structured Node live-session table missing from package"
grep -Fq 'class="node-session-filters"' "$SOURCE_DIR/node_agent/app.py" || die "Node live-session filter layout missing from package"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_NO_DELIVERY_V92' "$SOURCE_DIR/node_agent/app.py" || die "v9.2 Node Live Sessions Delivery-removal marker missing from package"
! grep -Fq '<th>Delivery</th>' "$SOURCE_DIR/node_agent/app.py" || die "v9.2 Node Live Sessions still exposes Delivery column"
grep -Fq 'Math.min(size-1,Math.ceil(size*0.10))' "$SOURCE_DIR/app/static/dashboard.js" || die "Main repeated ninety-percent click zoom missing from package"
grep -Fq 'Math.min(size-1,Math.ceil(size*0.10))' "$SOURCE_DIR/node_agent/app.py" || die "Node repeated ninety-percent click zoom missing from package"
grep -Fq '.hardware-summary{{display:flex!important;align-items:center;justify-content:space-between' "$SOURCE_DIR/node_agent/app.py" || die "Node hardware summary edge alignment missing from package"
grep -Fq 'canvas.onclick=e=>' "$SOURCE_DIR/app/static/dashboard.js" || die "Main single-click synchronized zoom missing from package"
grep -Fq 'canvas.onclick=e=>' "$SOURCE_DIR/node_agent/app.py" || die "Node single-click synchronized zoom missing from package"
grep -Fq 'data-cpu-cores' "$SOURCE_DIR/node_agent/app.py" || die "Node CPU core count display missing from package"
grep -Fq 'data-memory-total' "$SOURCE_DIR/node_agent/app.py" || die "Node RAM total display missing from package"
grep -Fq 'sharedZoomRedraw.forEach(draw=>draw())' "$SOURCE_DIR/app/static/dashboard.js" || die "Main synchronized graph zoom missing from package"
grep -Fq 'sharedZoomRedraw.forEach(draw=>draw())' "$SOURCE_DIR/node_agent/app.py" || die "Node synchronized graph zoom missing from package"
grep -Fq '"total_channels": max(0, int(channels.get("total") or 0))' "$SOURCE_DIR/app/metrics_history.py" || die "Main channel history sampling missing from package"
grep -Fq 'data-metric-chart="channels"' "$SOURCE_DIR/app/templates/dashboard.html" || die "Main Channels graph missing from package"
grep -Fq 'data-node-metric-chart="channels"' "$SOURCE_DIR/node_agent/app.py" || die "Node Channels graph missing from package"
grep -Fq "canvas.ondblclick=()=>" "$SOURCE_DIR/app/static/dashboard.js" || die "Main graph zoom reset missing from package"
grep -Fq "canvas.ondblclick=()=>" "$SOURCE_DIR/node_agent/app.py" || die "Node graph zoom reset missing from package"
grep -Fq 'rm -rf "$APP_DIR/venv"' "$SOURCE_DIR/scripts/install.sh" || die "Fresh Main venv removal missing from package"
grep -Fq 'rm -rf /opt/streamforge-node/venv' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "Node venv removal missing from package"
grep -Fq -- '--no-cache-dir --ignore-installed --upgrade -r' "$SOURCE_UPDATER" || die "No-cache updater pip overlay missing from package"
grep -Fq -- '--no-cache-dir --ignore-installed --upgrade -r' "$SOURCE_DIR/scripts/install.sh" || die "No-cache Main installer pip overlay missing from package"
grep -Fq -- '--no-cache-dir --ignore-installed --upgrade -r' "$SOURCE_DIR/scripts/install_node_agent.sh" || die "No-cache Node installer pip overlay missing from package"
grep -Fq '"--no-cache-dir", "--ignore-installed", "--upgrade"' "$SOURCE_DIR/node_agent/app.py" || die "No-cache Node API updater pip overlay missing from package"
grep -Fq 'def _main_panel_disconnected_page' "$SOURCE_DIR/node_agent/app.py" || die "Styled Main-disconnected page missing from package"
grep -Fq '@app.exception_handler(HTTPException)' "$SOURCE_DIR/node_agent/app.py" || die "Browser/API exception split missing from package"
grep -Fq 'sec-fetch-dest' "$SOURCE_DIR/node_agent/app.py" || die "Browser navigation detection missing from package"
grep -Fq 'HTTP 503 · Live Main authorization required' "$SOURCE_DIR/node_agent/app.py" || die "Disconnected-page status footer missing from package"
grep -Fq 'if not manager.panel_connected(force=True):' "$SOURCE_DIR/node_agent/app.py" || die "Fail-closed Node login-page connectivity gate missing from package"
grep -Fq 'def reload_channel_catalog_if_changed' "$SOURCE_DIR/node_agent/app.py" || die "Read-only Node playlist catalogue refresh missing from package"
grep -Fq 'autostart and NODE_CHANNEL_OWNER and runtime.desired_running' "$SOURCE_DIR/node_agent/app.py" || die "Dedicated channel-owner autostart guard missing from package"
grep -Fq 'def _hls_output_state' "$SOURCE_DIR/node_agent/app.py" || die "External HLS readiness detection missing from package"
grep -Fq 'A path-only Playlist/App URL shares the canonical Panel/API listener' "$SOURCE_DIR/node_agent/app.py" || die "Node shared port-80 default missing from package"
grep -Fq 'playlist_listener_port = max(1, min(65535, int(playlist_port or panel_listener_port)))' "$SOURCE_DIR/app/main.py" || die "Main Node editor shared listener default missing from package"
grep -Fq 'EXTERNAL_PROXY_MODE = os.getenv("STREAMFORGE_NODE_EXTERNAL_PROXY"' "$SOURCE_DIR/node_agent/app.py" || die "Co-located Node external-proxy mode missing from package"
grep -Fq 'Browsers commonly omit an explicit default :80/:443' "$SOURCE_DIR/node_agent/app.py" || die "Default HTTP port Host matching fix missing from package"
grep -Fq 'for value in [*self.panel_urls, *self.stream_urls]:' "$SOURCE_DIR/node_agent/app.py" || die "Multiple Playlist/App listener-port fix missing from package"
grep -Fq 'def stream_gateway_ports' "$SOURCE_DIR/node_agent/app.py" || die "Playlist/App listener-port discovery missing from package"
grep -Fq 'stream_only = value in self.stream_urls and value not in self.panel_urls' "$SOURCE_DIR/node_agent/app.py" || die "Stream-only listener verification missing from package"
grep -Fq "mirror.dataset.bulkChannelMirror = '1'" "$SOURCE_DIR/app/static/app.js" || die "Bulk channel selection submission fix missing from package"
grep -Fq 'node_controller.forget(channel_id)' "$SOURCE_DIR/app/main.py" || die "Bulk channel cleanup ordering fix missing from package"
grep -Fq 'if action == "delete_selected":' "$SOURCE_DIR/app/main.py" || die "Isolated bulk-delete handler missing from package"
grep -Fq 'db.rollback()' "$SOURCE_DIR/app/main.py" || die "Bulk-delete rollback protection missing from package"
grep -Fq 'ALTER TABLE node_stream_users ADD COLUMN playlist_order' "$SOURCE_DIR/scripts/migrate_online_node_users.py" || die "Node-user playlist-order migration missing from package"
grep -Fq 'if "playlist_order" not in node_user_columns:' "$SOURCE_DIR/app/db.py" || die "Runtime node-user schema repair missing from package"
grep -Fq 'bulk_hls_segment_time: str = Form("keep")' "$SOURCE_DIR/app/main.py" || die "Bulk HLS segment control missing from package"
grep -Fq 'bulk_profile_node_ids: list[int] = Form(default=[])' "$SOURCE_DIR/app/main.py" || die "Bulk node-targeted HLS segment backend missing from package"
grep -Fq 'name="bulk_profile_node_ids" multiple' "$SOURCE_DIR/app/templates/channels.html" || die "Bulk HLS node-target selector missing from package"
grep -Fq 'name="node_hls_segment_times"' "$SOURCE_DIR/app/templates/channel_form.html" || die "Per-node HLS segment selector missing from package"
grep -Fq 'Column("hls_segment_time", Integer, nullable=True)' "$SOURCE_DIR/app/models.py" || die "Per-node HLS segment schema missing from package"
grep -Fq 'profile.get("hls_segment_time") or channel.hls_segment_time' "$SOURCE_DIR/app/node_manager.py" || die "Remote Node HLS segment override payload missing from package"
grep -Fq 'channel.hls_segment_time = max(1, min(20, int(hls_segment_time)))' "$SOURCE_DIR/app/ffmpeg.py" || die "Local Node HLS segment override missing from package"
grep -Fq 'for field, value in original_profile.items():' "$SOURCE_DIR/app/ffmpeg.py" || die "Local Node override default-restoration guard missing from package"
grep -Fq 'ALTER TABLE channel_nodes ADD COLUMN hls_segment_time INTEGER' "$SOURCE_DIR/scripts/migrate_v2148.py" || die "Per-node HLS segment migration missing from package"
grep -Fq '"hls_segment_time": max(1, min(20, int(getattr(config, "hls_segment_time", 1) or 1)))' "$SOURCE_DIR/node_agent/app.py" || die "Node-synchronized HLS segment preservation missing from package"
grep -Fq "'hls_selected': 'channels.edit'" "$SOURCE_DIR/node_agent/app.py" || die "Node-local bulk HLS segment backend missing from package"
grep -Fq 'def _node_channel_display_ids()' "$SOURCE_DIR/node_agent/app.py" || die "Owner-aware Node channel numbering missing from package"
grep -Fq 'display_id: int | None = None' "$SOURCE_DIR/node_agent/app.py" || die "Main catalogue display ID field missing from Node package"
grep -Fq '"display_id": _channel_public_catalogue_number(channel.id)' "$SOURCE_DIR/app/node_manager.py" || die "Main catalogue display ID sync missing from package"
grep -Fq 'stream_number = int(channel.id) if user.user_type == "restream"' "$SOURCE_DIR/app/main.py" || die "Restream database-ID playlist numbering missing from package"
grep -Fq 'def channel_public_number_map(db: Session)' "$SOURCE_DIR/app/main.py" || die "Public channel-number map missing from package"
grep -Fq 'public_numbers[channel.id]}/master.m3u8' "$SOURCE_DIR/app/main.py" || die "Public 101+ playlist URL generation missing from package"
grep -Fq 'f"/ek/{playback_key}/{channel_id}/n/{node.id}/index.m3u8"' "$SOURCE_DIR/app/main.py" || die "Public catalogue ID playback propagation missing from package"
grep -Fq 'name="bulk_hls_segment_time"' "$SOURCE_DIR/app/templates/channels.html" || die "Bulk HLS segment selector missing from package"
grep -Fq '{{ channel_display_ids[channel.id] }}' "$SOURCE_DIR/app/templates/channels.html" || die "Contiguous channel display ID rendering missing from package"
grep -Fq 'data-channel-sort-key="displayId"' "$SOURCE_DIR/app/templates/channels.html" || die "Main display-ID sortable header missing from package"
grep -Fq 'data-channel-sort-key="dbId"' "$SOURCE_DIR/app/templates/channels.html" || die "Main database-ID sortable header missing from package"
grep -Fq '<td class="channel-id-cell mono" data-label="DB">{{ channel.id }}</td>' "$SOURCE_DIR/app/templates/channels.html" || die "Main database ID rendering missing from package"
# STREAMFORGE_CHANNELS_MOBILE_VALIDATOR_V2303
grep -Fq 'data-label="Channel"' "$SOURCE_DIR/app/templates/channels.html" || die "Channels mobile field labels missing from package"
# STREAMFORGE_CHANNELS_MOBILE_DESKTOP_TABLE_VALIDATOR_V2304
grep -Fq 'STREAMFORGE_CHANNELS_MOBILE_DESKTOP_TABLE_V2304' "$SOURCE_DIR/app/static/style.css" || die "Channels mobile desktop-table CSS missing from package"
grep -Fq 'width:1180px!important;' "$SOURCE_DIR/app/static/style.css" || die "Channels mobile desktop-table width missing"
grep -Fq 'display:table-header-group!important;' "$SOURCE_DIR/app/static/style.css" || die "Channels mobile table header restore missing"
grep -Fq 'overflow-x:auto!important;' "$SOURCE_DIR/app/static/style.css" || die "Channels mobile horizontal scrolling missing"
# STREAMFORGE_NODES_MOBILE_HEAD_ACTIONS_FIX_V2305
grep -Fq 'node-cluster-panel' "$SOURCE_DIR/app/templates/nodes.html" || die "Nodes mobile panel marker missing from package"
grep -Fq 'STREAMFORGE_NODES_MOBILE_HEAD_ACTIONS_FIX_V2305' "$SOURCE_DIR/app/static/style.css" || die "Nodes mobile head action CSS missing from package"
# STREAMFORGE_BACKUPS_MOBILE_LAYOUT_FIX_V2306
grep -Fq 'STREAMFORGE_BACKUPS_MOBILE_LAYOUT_FIX_V2306' "$SOURCE_DIR/app/templates/backups.html" || die "Backups mobile layout CSS missing from package"
# STREAMFORGE_DASHBOARD_MOBILE_LAYOUT_FIX_V2307
grep -Fq 'dashboard-live-channels-panel' "$SOURCE_DIR/app/templates/dashboard.html" || die "Dashboard live-channel mobile panel marker missing"
grep -Fq 'STREAMFORGE_DASHBOARD_MOBILE_LAYOUT_FIX_V2307' "$SOURCE_DIR/app/static/style.css" || die "Dashboard mobile layout CSS missing from package"
grep -Fq 'width:1190px!important;' "$SOURCE_DIR/app/static/style.css" || die "Dashboard mobile desktop-table width missing"
grep -Fq 'display:table-header-group!important;' "$SOURCE_DIR/app/static/style.css" || die "Dashboard mobile table header restore missing"
grep -Fq 'Swipe horizontally to view all live channel columns' "$SOURCE_DIR/app/static/style.css" || die "Dashboard mobile swipe hint missing"

grep -Fq 'grid-template-columns:minmax(0,1fr)!important;' "$SOURCE_DIR/app/templates/backups.html" || die "Backups mobile one-column form layout missing"
grep -Fq '.backup-actions{' "$SOURCE_DIR/app/templates/backups.html" || die "Backups mobile action grid missing"
grep -Fq 'grid-template-columns:repeat(2,minmax(0,1fr))!important;' "$SOURCE_DIR/app/templates/backups.html" || die "Backups mobile action button layout missing"

grep -Fq '.node-cluster-panel .nodes-head-actions{' "$SOURCE_DIR/app/static/style.css" || die "Nodes mobile head action selector missing from package"
grep -Fq 'width:100%!important;' "$SOURCE_DIR/app/static/style.css" || die "Nodes mobile full-width action layout missing from package"


grep -Fq 'stream_id=display_ids.get(rt.config.key)' "$SOURCE_DIR/node_agent/app.py" || die "Node visible playlist serial mapping missing from package"
grep -Fq "'stream_id': display_ids.get(resolved_key, item.stream_id)" "$SOURCE_DIR/node_agent/app.py" || die "Node effective playlist serial mapping missing from package"
grep -Fq '/{_node_stream_id(channel)}/master.m3u8' "$SOURCE_DIR/node_agent/app.py" || die "Node serial playback URLs missing from package"
grep -Fq 'STREAMFORGE_NODE_SESSION_PLAYBACK_GRANT_V65' "$SOURCE_DIR/node_agent/app.py" || die "Node serial/session playback authorization missing from package"
grep -Fq 'def _node_main_channel_direct_actions' "$SOURCE_DIR/node_agent/app.py" || die "Node Main channel direct action icons missing from package"
grep -Fq "@app.get('/panel/channels/{key}/info'" "$SOURCE_DIR/node_agent/app.py" || die "Node channel information page missing from package"
grep -Fq 'node-info-control' "$SOURCE_DIR/node_agent/app.py" || die "Node information icon styling missing from package"
grep -Fq 'node-log-control' "$SOURCE_DIR/node_agent/app.py" || die "Node stream-log icon styling missing from package"
grep -Fq 'node-channel-control-table{width:100%;min-width:1428px' "$SOURCE_DIR/node_agent/app.py" || die "Unified Node channel UI styling missing from package"
grep -Fq "const mobileSidebar = window.matchMedia('(max-width: 760px)')" "$SOURCE_DIR/app/static/app.js" || die "Main mobile navigation controller missing from package"
grep -Fq '.app-shell.mobile-nav-open .sidebar nav{display:grid}' "$SOURCE_DIR/app/static/style.css" || die "Main mobile navigation layout missing from package"
grep -Fq 'aria-controls="main-navigation"' "$SOURCE_DIR/app/templates/base.html" || die "Main mobile navigation accessibility binding missing from package"
grep -Fq '.channel-panel-head .head-actions{display:grid;width:100%;grid-template-columns:repeat(2,minmax(0,1fr))' "$SOURCE_DIR/app/static/style.css" || die "Channels mobile action grid missing from package"
grep -Fq '.channel-filter-bar{position:static;top:auto;padding:14px 16px}' "$SOURCE_DIR/app/static/style.css" || die "Channels mobile filter flow missing from package"
grep -Fq '.channel-search-form{display:grid;grid-template-columns:1fr;width:100%' "$SOURCE_DIR/app/static/style.css" || die "Channels mobile filter layout missing from package"
grep -Fq 'grid-template-columns:repeat(6,minmax(120px,1fr)) minmax(120px,auto)' "$SOURCE_DIR/app/static/style.css" || die "Six-field bulk profile desktop layout missing from package"
grep -Fq '.bulk-profile-grid select,.bulk-profile-grid input{display:block;width:100%;height:40px' "$SOURCE_DIR/app/static/style.css" || die "Bulk profile themed equal-height controls missing from package"
grep -Fq 'bulk-profile-apply' "$SOURCE_DIR/app/templates/channels.html" || die "Bulk profile aligned action button missing from package"
grep -Fq 'def load_colocated_node_routes' "$SOURCE_DIR/scripts/apply_main_access.py" || die "Co-located Node Nginx route generator missing from package"
grep -Fq 'STREAMFORGE_NODE_EXTERNAL_PROXY 1' "$SOURCE_UPDATER" || die "Co-located Node proxy migration missing from updater"
grep -Fq 'data-node-uptime' "$SOURCE_DIR/app/templates/nodes.html" || die "Node cluster Uptime column missing from package"
python3 - "$SOURCE_DIR/app/templates/nodes.html" "$SOURCE_DIR/app/static/style.css" <<'PY129_PACKAGE_METRICS'
from pathlib import Path
import sys
html = Path(sys.argv[1]).read_text(encoding="utf-8")
css = Path(sys.argv[2]).read_text(encoding="utf-8").replace(" ", "").replace("\n", "")
required = [
    ">Channels<", ">Up<", ">Down<", ">Online users<", ">Uptime<",
    ">CPU<", ">Memory<", ">Download<", ">Upload<",
]
missing = [label for label in required if label not in html]
if missing:
    raise SystemExit("Packaged Node metrics labels missing: " + ", ".join(missing))
if ".node-metrics{grid-template-columns:repeat(9,minmax(0,1fr))}" not in css:
    raise SystemExit("Packaged nine-column Node metrics grid missing")
PY129_PACKAGE_METRICS
grep -Fq '/main-service-restart' "$SOURCE_DIR/app/main.py" || die "Main service restart route missing from package"
grep -Fq '/main-reboot' "$SOURCE_DIR/app/main.py" || die "Main Server reboot route missing from package"
grep -Fq 'ExecStart=/usr/local/sbin/streamforge-main-system-control' "$SOURCE_DIR/deploy/streamforge-main-system.service" || die "Main privileged control service missing from package"
grep -Fq 'PathChanged=/var/lib/streamforge/main-system-runtime/request.json' "$SOURCE_DIR/deploy/streamforge-main-system.path" || die "Main privileged control path watcher missing from package"
grep -Fq 'ALLOWED_ACTIONS = {"restart_service", "reboot_server", "restore_backup"}' "$SOURCE_DIR/scripts/main_system_control.py" || die "Main privileged action allowlist missing from package"

# StreamForge v2.1.152 one-second HLS package validation.
grep -Fq 'default=1' "$SOURCE_DIR/app/models.py" || die "Main one-second HLS model default missing from package"
grep -Fq 'local_hls_time = 1 if youtube_source else max(1, channel.hls_segment_time)' "$SOURCE_DIR/app/ffmpeg.py" || die "Main one-second HLS FFmpeg floor missing from package"
grep -Fq 'local_hls_time = 1 if youtube_source else max(1, cfg.hls_segment_time)' "$SOURCE_DIR/node_agent/app.py" || die "Node one-second HLS FFmpeg floor missing from package"
grep -Fq 'migrate_v201.py' "$SOURCE_UPDATER" || die "v2.1.152 database migration hook missing from package"
[[ -x "$SOURCE_DIR/scripts/migrate_v201.py" ]] || die "v2.1.152 migration script missing from package"
# STREAMFORGE_MIGRATION_SCRIPT_PERMISSIONS_V2301
[[ -x "$SOURCE_DIR/scripts/migrate_v200.py" ]] || die "v2.0 migration script is not executable in package"
[[ -x "$SOURCE_DIR/scripts/migrate_v201.py" ]] || die "v2.1.152 migration script is not executable in package"
grep -Fq 'STREAMFORGE_WEBPLAYER_STABLE_EDGE_BUFFER_V77' "$SOURCE_DIR/app/templates/player.html" || die "v7.9 stable-edge player marker missing from package"
grep -Fq 'liveSyncDurationCount: 2' "$SOURCE_DIR/app/templates/player.html" || die "v7.9 two-segment live sync profile missing from package"
grep -Fq 'liveMaxLatencyDurationCount: 5' "$SOURCE_DIR/app/templates/player.html" || die "v7.9 normal live maximum-latency profile missing from package"
grep -Fq 'liveMaxLatencyDurationCount: 6' "$SOURCE_DIR/app/templates/player.html" || die "v7.9 stable live maximum-latency profile missing from package"
# STREAMFORGE_V1167_SUPERSEDED_NORMAL_BUFFER_GUARD_FIX:
# v11.63 intentionally raised the regular WebPlayer buffer from 10s to 12s.
grep -Fq 'maxBufferLength: 12' "$SOURCE_DIR/app/templates/player.html" || die "v11.63 normal live buffer missing from package"
grep -Fq 'maxBufferLength: 12' "$SOURCE_DIR/app/templates/player.html" || die "v7.9 stable live buffer missing from package"
grep -Fq 'Switching to the stable live buffer' "$SOURCE_DIR/app/templates/player.html" || die "Player stable-buffer fallback missing from package"
grep -Fq '"-tune", "zerolatency"' "$SOURCE_DIR/app/ffmpeg.py" || die "Main zero-latency software encoder flags missing from package"
grep -Fq 'local_hls_list_size = 8 if youtube_source else 6' "$SOURCE_DIR/app/ffmpeg.py" || die "Main compact/balanced HLS live window missing from package"
grep -Fq '"-tune", "zerolatency"' "$SOURCE_DIR/node_agent/app.py" || die "Node zero-latency software encoder flags missing from package"
grep -Fq 'local_hls_list_size = 8 if youtube_source else 6' "$SOURCE_DIR/node_agent/app.py" || die "Node compact/balanced HLS live window missing from package"
grep -Fq 'def _normalize_v200_state_payload' "$SOURCE_DIR/node_agent/app.py" || die "Node v2.0 state migration missing from package"
grep -Fq 'migrate_v200.py' "$SOURCE_UPDATER" || die "v2.0 database migration hook missing from package"
[[ -x "$SOURCE_DIR/scripts/migrate_v200.py" ]] || die "v2.0 migration script missing from package"
! grep -Fq 'Manage channels, encoders, users and playlists.' "$SOURCE_DIR/app/templates/login.html" || die "Old Main login description remains in package"
grep -Fq '/system/branding' "$SOURCE_DIR/app/main.py" || die "Login branding settings route missing from package"
grep -Fq 'save_branding_logo' "$SOURCE_DIR/app/main.py" || die "Branding logo upload support missing from package"
! grep -Fq '<p>Remote Node Panel</p>' "$SOURCE_DIR/node_agent/app.py" || die "Removed Node login subtitle remains in package"
! grep -Fq '· Remote Node Panel</title>' "$SOURCE_DIR/node_agent/app.py" || die "Removed Node login title suffix remains in package"
grep -Fq 'STREAMFORGE_MAIN_NODE_NAME_SYNC_V304' "$SOURCE_DIR/app/node_manager.py" || die "Main Node-name access-sync payload missing from package"
grep -Fq 'STREAMFORGE_MAIN_NODE_NAME_SYNC_V304' "$SOURCE_DIR/node_agent/app.py" || die "Node name access-sync field missing from package"
grep -Fq 'apply_main_node_name(data.get("node_name"))' "$SOURCE_DIR/node_agent/app.py" || die "Heartbeat Node-name recovery missing from package"
grep -Fq 'self.apply_main_node_name(decoded.get("node_name"))' "$SOURCE_DIR/node_agent/app.py" || die "Live-auth Node-name recovery missing from package"
grep -Fq 'saved_node_name = str(raw.get("node_name")' "$SOURCE_DIR/node_agent/app.py" || die "Persistent Node-name access-state load missing from package"
grep -Fq '"node_name": node.name' "$SOURCE_DIR/app/main.py" || die "Main heartbeat/live-auth Node-name response missing from package"
grep -Fq 'STREAMFORGE_NODE_DISCONNECTED_CENTERED_V305' "$SOURCE_DIR/node_agent/app.py" || die "Centered disconnected Node card marker missing from package"
grep -Fq '<title>Main Server Disconnected</title>' "$SOURCE_DIR/node_agent/app.py" || die "Clean disconnected-page browser title missing from package"
grep -Fq 'min-height:100dvh' "$SOURCE_DIR/node_agent/app.py" || die "Disconnected card dynamic-viewport centering missing from package"
! grep -Fq '<div class="brand"><span class="brand-mark"' "$SOURCE_DIR/node_agent/app.py" || die "Disconnected-page logo and Node-name header remains in package"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_LIVE_WORKFLOW_V306' "$SOURCE_DIR/app/main.py" || die "Auto Install live workflow backend missing from package"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_LIVE_WORKFLOW_UI_V306' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install live workflow UI missing from package"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_PREFIX_AWARE_LIVE_WORKFLOW_V65R12' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install prefix-aware live workflow missing from package"
grep -Fq "const panelRoot = String(window.STREAMFORGE_APP_ROOT || '')" "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install panel-prefix root resolver missing from package"
grep -Fq 'action="{{ app_root }}/nodes/install"' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install prefix-aware form action missing from package"
grep -Fq '@app.get("/nodes/install/status/{job_id}"' "$SOURCE_DIR/app/main.py" || die "Auto Install live status endpoint missing from package"
grep -Fq 'progress=lambda phase, message, percent, detail: _node_install_progress' "$SOURCE_DIR/app/main.py" || die "Auto Install real SSH progress callback missing from package"
grep -Fq 'data-node-install-status' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install progress panel missing from package"
grep -Fq '/nodes/install/status/${encodeURIComponent(jobInput.value)}' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install status polling missing from package"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_WORKFLOW_GAP_V307' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install workflow spacing marker missing from package"
grep -Fq '[data-node-install-status]{margin:0 0 16px!important}' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install desktop workflow gap missing from package"
grep -Fq '[data-node-install-status]{margin-bottom:13px!important}' "$SOURCE_DIR/app/templates/node_install.html" || die "Auto Install mobile workflow gap missing from package"
grep -Fq 'filename = safe_slug + extension' "$SOURCE_DIR/app/main.py" || die "Slug-based Main channel logo filenames missing from package"
grep -Fq 'def sync_channel_logo_to_all_nodes' "$SOURCE_DIR/app/node_manager.py" || die "Main-to-all-Nodes logo distribution missing from package"
grep -Fq '/api/v1/channel-logos/{filename}/sync' "$SOURCE_DIR/node_agent/app.py" || die "Authenticated Node logo sync endpoint missing from package"
grep -Fq '@app.post("/api/v1/node-logo/{filename}/sync"' "$SOURCE_DIR/node_agent/app.py" || die "Authenticated Main-to-Node brand-logo endpoint missing from package"
grep -Fq '"node_logo_content_base64": logo_content_base64' "$SOURCE_DIR/app/node_manager.py" || die "Atomic Main-to-Node brand-logo payload missing from package"
grep -Fq 'def recover_node_logo_from_main' "$SOURCE_DIR/node_agent/app.py" || die "Node logo startup recovery missing from package"
grep -Fq 'NODE_LOGO_ROOT = Path(' "$SOURCE_DIR/node_agent/app.py" || die "Node-local brand-logo storage missing from package"
grep -Fq '"/opt/streamforge-node/logo"' "$SOURCE_DIR/node_agent/app.py" || die "Node logo /opt storage path missing from package"
grep -Fq 'legacy_node_logo_root = Path("/var/lib/streamforge-node/node-logos")' "$SOURCE_DIR/node_agent/app.py" || die "Legacy Node logo migration missing from package"
grep -Fq 'logo_url = f"{access_prefix}{logo_url}"' "$SOURCE_DIR/node_agent/app.py" || die "Node logo Panel access-path prefix fix missing from package"
grep -Fq 'path.startswith("/node-logos/")' "$SOURCE_DIR/node_agent/app.py" || die "Node branding Panel-route classification missing from package"
grep -Fq 'v2.0.11 keep user load-balancing node cards clear' "$SOURCE_DIR/app/static/style.css" || die "User load-balancing Node-card spacing fix missing from package"
# STREAMFORGE_GEOIP_COMPACT_FORM_GUARD_V99R13: verify semantic r12 layout markers instead of counting a CSS/class token.
grep -Fq 'STREAMFORGE_GEOIP_COMPACT_PROVIDER_FORM_V99R12' "$SOURCE_DIR/app/templates/node_asn.html" || die "Compact Main GeoIP provider form missing from package"
grep -Fq 'class="check geo-ipinfo-field geo-credential-remove"' "$SOURCE_DIR/app/templates/node_asn.html" || die "Compact Main IPinfo credential row missing from package"
grep -Fq 'class="check geo-maxmind-field geo-credential-remove"' "$SOURCE_DIR/app/templates/node_asn.html" || die "Compact Main MaxMind credential row missing from package"
! grep -Fq 'IPinfo token is configured (Saved' "$SOURCE_DIR/app/templates/node_asn.html" || die "Removed Main GeoIP IPinfo status note returned in package"
grep -Fq 'STREAMFORGE_GEOIP_PROVIDER_CHECKBOX_NOWRAP_V99R15' "$SOURCE_DIR/app/templates/node_asn.html" || die "v9.9 r15 GeoIP checkbox nowrap layout missing from package"
grep -Fq 'STREAMFORGE_GEOIP_TEST_SEPARATE_PANEL_V99R15' "$SOURCE_DIR/app/templates/node_asn.html" || die "v9.9 r15 separate GeoIP test panel missing from package"
grep -Fq '<span class="nav-label">Settings</span>' "$SOURCE_DIR/app/templates/base.html" || die "Main Settings navigation label missing from package"
grep -Fq '<h2>Main Server connection capacity</h2>' "$SOURCE_DIR/app/templates/system_branding.html" || die "Main connection capacity is missing from Settings"
! grep -Fq 'Main server connection capacity' "$SOURCE_DIR/app/templates/users.html" || die "Main connection capacity remains on Users page"
! grep -Fq 'Currently reserved:' "$SOURCE_DIR/app/templates/users.html" || die "Reserved-connections helper remains in package"
! grep -Fq '0 means unlimited' "$SOURCE_DIR/app/templates/users.html" || die "Unlimited helper remains in package"
python3 - "$SOURCE_DIR/app/templates/node_asn.html" <<'PY2016_IPINFO_HELPER'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index('<label class="geo-ipinfo-field">New IPinfo API token')
end = text.index('</label>', start)
if 'field-help' in text[start:end]:
    raise SystemExit('Duplicate saved IPinfo helper remains below the token input')
PY2016_IPINFO_HELPER
! grep -Fq 'Saved IPinfo status' "$SOURCE_DIR/app/templates/node_asn.html" || die "Duplicate IPinfo status row remains in package"
! grep -Fq 'Saved MaxMind key status' "$SOURCE_DIR/app/templates/node_asn.html" || die "Duplicate MaxMind status row remains in package"
! grep -Fq 'Saved IPinfo status' "$SOURCE_DIR/node_agent/app.py" || die "Duplicate Node IPinfo status row remains in package"
! grep -Fq 'Saved MaxMind key status' "$SOURCE_DIR/node_agent/app.py" || die "Duplicate Node MaxMind status row remains in package"
! grep -Fq '"/api/v1/node-control/channel-logo"' "$SOURCE_DIR/node_agent/app.py" || die "Unwanted Node-to-Main channel-logo upload remains in package"
[[ "$(grep -Fc 'Content-Disposition' "$SOURCE_DIR/app/main.py")" -ge 1 ]] || die "Main M3U download filename header missing from package"
[[ "$(grep -Fc 'Content-Disposition' "$SOURCE_DIR/node_agent/app.py")" -ge 1 ]] || die "Node M3U download filename header missing from package"
! grep -Fq 'master.m3u8?sid={urllib.parse.quote(playback_session_id)}' "$SOURCE_DIR/node_agent/app.py" || die "Redundant visible Node catalogue session query remains in package"
grep -Fq 'def _node_channel_logo_public_url' "$SOURCE_DIR/node_agent/app.py" || die "Node public playlist logo URL builder missing from package"
grep -Fq 'matched_prefix = _CURRENT_STREAM_PREFIX.get()' "$SOURCE_DIR/node_agent/app.py" || die "Request-aware Node Playlist/App alias selection missing from package"
grep -Fq '{base}/node-play/{token}/{urllib.parse.quote(key)}/index.m3u8' "$SOURCE_DIR/node_agent/app.py" || die "Alias-safe Node master playlist child URL missing from package"
grep -Fq '{base}/node-play/{token}/{urllib.parse.quote(key)}/{Path(line).name}' "$SOURCE_DIR/node_agent/app.py" || die "Alias-safe Node HLS segment URL missing from package"

grep -Fq 'STREAMFORGE_DB_POOL_HEADROOM_V32' "$SOURCE_DIR/app/db.py" || die "v3.2 DB pool headroom fix missing from package"
grep -Fq '"pool_size": 20' "$SOURCE_DIR/app/db.py" || die "v3.2 SQLite pool size missing from package"
grep -Fq '"max_overflow": 40' "$SOURCE_DIR/app/db.py" || die "v3.2 SQLite pool overflow missing from package"
grep -Fq 'STREAMFORGE_SCAN_RELEASE_DB_V32' "$SOURCE_DIR/app/main.py" || die "v3.2 source-scan DB release fix missing from package"
grep -Fq 'STREAMFORGE_HLS_PROXY_RELEASE_DB_V32' "$SOURCE_DIR/app/main.py" || die "v3.2 HLS proxy DB release fix missing from package"
grep -Fq 'STREAMFORGE_PLAYBACK_READY_RELEASE_DB_V32' "$SOURCE_DIR/app/load_balancer.py" || die "v3.2 playback-ready DB release fix missing from package"
grep -Fq 'STREAMFORGE_BULK_STATUS_RELEASE_DB_V32' "$SOURCE_DIR/app/node_manager.py" || die "v3.2 bulk Node status DB release fix missing from package"

APP_DIR="${STREAMFORGE_APP_DIR:-}"
if [[ -z "$APP_DIR" ]]; then
  APP_DIR="$(systemctl cat "$SERVICE_NAME" 2>/dev/null | awk -F= '/^[[:space:]]*WorkingDirectory=/{print $2; exit}' | xargs || true)"
fi
[[ -n "$APP_DIR" ]] || APP_DIR="/opt/streamforge"
[[ -f "$APP_DIR/app/main.py" ]] || die "Installed application not found at $APP_DIR"
LIVE_UPDATER="$APP_DIR/scripts/$UPDATER_BASENAME"
id streamforge >/dev/null 2>&1 || die "streamforge user does not exist"
INSTALLED_VERSION="$(cat "$APP_DIR/VERSION" 2>/dev/null || printf 'unknown')"
BACKUP_VERSION_TAG="$(printf '%s' "$INSTALLED_VERSION" | tr -cd '0-9')"

# STREAMFORGE_SAFE_UPDATE_MIGRATION_POLICY_V2204:
# Modern StreamForge updates must not replay historical migrations on every
# release. Re-running old migrations can normalize/overwrite live settings.
# v2.1.203+ databases already contain the required legacy schema, so those
# migrations are skipped. Older installations retain the compatibility path.
version_ge(){
  python3 - "$1" "$2" <<'PY_VERSION_GE'
import re, sys
def parts(v):
    nums=[int(x) for x in re.findall(r"\d+", str(v))[:3]]
    return tuple((nums+[0,0,0])[:3])
raise SystemExit(0 if parts(sys.argv[1]) >= parts(sys.argv[2]) else 1)
PY_VERSION_GE
}
# STREAMFORGE_V119_FINAL_GUARD_SOURCE_PREFLIGHT:
# Validate historical editor markers and the v11.7+ server-side Channels controls
# against the package source before any live files/database are touched.
grep -Fq 'v1.11.63 compact single-line channel editor groups' "$SOURCE_DIR/app/static/style.css" || die "v11.22 package Main compact encoding grid CSS missing"
grep -Fq 'v1.11.63 compact single-line Node channel editor groups' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 package Node compact encoding grid CSS missing"
grep -Fq 'v1.11.63 aligned source rows and concise codec labels' "$SOURCE_DIR/app/static/style.css" || die "v11.22 package Main aligned source-row CSS missing"
grep -Fq 'v1.11.63 aligned Node source rows and concise codec labels' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 package Node aligned source-row CSS missing"
grep -Fq 'v1.11.63 right-aligned source actions and multi-category picker' "$SOURCE_DIR/app/static/style.css" || die "v11.22 package Main source action/category CSS missing"
grep -Fq 'v1.11.63 Node source actions and multi-category support' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 package Node source action/category CSS missing"
grep -Fq 'v1.11.67 source-specific MPTS program selectors' "$SOURCE_DIR/app/static/style.css" || die "v11.22 package Main source selector CSS missing"
grep -Fq 'v1.11.67 Node source-specific MPTS program selectors' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 package Node source selector CSS missing"
grep -Fq 'v1.11.67 unified dropdown styling, status filter, and bulk relay tools' "$SOURCE_DIR/app/static/style.css" || die "v11.22 package Main dropdown CSS missing"
grep -Fq 'v1.11.67 unified Node dropdowns, Up/Down filtering, and dynamic HTTP action' "$SOURCE_DIR/node_agent/app.py" || die "v11.22 package Node dropdown CSS missing"
grep -Fq 'v1.11.67 unified search fields and clean logout icon' "$SOURCE_DIR/app/static/style.css" || die "v11.22 package unified search CSS missing"
grep -Fq 'v1.11.68 single-line Node page header actions' "$SOURCE_DIR/app/static/style.css" || die "v11.22 package Node header CSS missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNELS_SERVER_PAGINATION_V117' "$SOURCE_DIR/app/main.py" || die "v11.22 package historical Channels pagination compatibility marker missing"
grep -Fq 'name="q"' "$SOURCE_DIR/app/templates/channels.html" || die "v11.22 package Main Channels search control missing"
grep -Fq 'name="status"' "$SOURCE_DIR/app/templates/channels.html" || die "v11.22 package Main Channels status filter missing"

RUN_LEGACY_MIGRATIONS=1
if version_ge "$INSTALLED_VERSION" "2.1.203"; then
  RUN_LEGACY_MIGRATIONS=0
  log "Modern update detected ($INSTALLED_VERSION -> 12.12); historical migrations will NOT be replayed."
fi
[[ -n "$BACKUP_VERSION_TAG" ]] || BACKUP_VERSION_TAG="unknown"
BACKUP_DIR="$BACKUP_ROOT/pre-v${BACKUP_VERSION_TAG}-$TIMESTAMP"

GEO_SETTINGS_FILE="$APP_DIR/geoip-settings.json"
if [[ -f "$ENV_FILE" ]]; then
  configured_geo="$(grep -E '^STREAMFORGE_GEOIP_SETTINGS_FILE=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  [[ -n "$configured_geo" ]] && GEO_SETTINGS_FILE="$configured_geo"
fi

DB_URL="sqlite:///$DATA_DIR/streamforge.db"
if [[ -f "$ENV_FILE" ]]; then
  configured="$(grep -E '^STREAMFORGE_DATABASE_URL=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  [[ -n "$configured" ]] && DB_URL="$configured"
fi
DB_PATH=""
if [[ "$DB_URL" == sqlite:///* ]]; then
  DB_PATH="${DB_URL#sqlite:///}"
  [[ "$DB_PATH" = /* ]] || DB_PATH="$APP_DIR/$DB_PATH"
fi

log "Detected live directory: $APP_DIR"

# STREAMFORGE_WEBPLAYER_DOWNLOAD_UPDATE_PRESERVE_V1011:
# Resolve persistent upload locations from the live environment. The updater
# snapshots these separately from /opt so an rsync --delete or legacy/custom
# path cannot remove an administrator-uploaded Web Player app.
read_env_value(){
  local file="$1" key="$2"
  [[ -f "$file" ]] && grep -E "^${key}=" "$file" | tail -1 | cut -d= -f2- || true
}
WEBPLAYER_DOWNLOAD_ROOT="$(read_env_value "$ENV_FILE" STREAMFORGE_WEBPLAYER_DOWNLOAD_ROOT)"
WEBPLAYER_DOWNLOAD_ROOT="${WEBPLAYER_DOWNLOAD_ROOT:-/var/lib/streamforge/webplayer-downloads}"
case "$WEBPLAYER_DOWNLOAD_ROOT" in /*) ;; *) WEBPLAYER_DOWNLOAD_ROOT="$APP_DIR/$WEBPLAYER_DOWNLOAD_ROOT" ;; esac
NODE_WEBPLAYER_DOWNLOAD_FILE="$(read_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_FILE)"
NODE_WEBPLAYER_DOWNLOAD_FILE="${NODE_WEBPLAYER_DOWNLOAD_FILE:-/var/lib/streamforge-node/webplayer-downloads/managed-download.bin}"
case "$NODE_WEBPLAYER_DOWNLOAD_FILE" in /*) ;; *) NODE_WEBPLAYER_DOWNLOAD_FILE="$LOCAL_NODE_DIR/$NODE_WEBPLAYER_DOWNLOAD_FILE" ;; esac
WEBPLAYER_SAFETY_DIR=""

snapshot_webplayer_downloads(){
  WEBPLAYER_SAFETY_DIR="$BACKUP_DIR/webplayer-download-safety"
  mkdir -p "$WEBPLAYER_SAFETY_DIR/main" "$WEBPLAYER_SAFETY_DIR/node"
  if [[ -d "$WEBPLAYER_DOWNLOAD_ROOT" ]]; then
    rsync -a "$WEBPLAYER_DOWNLOAD_ROOT/" "$WEBPLAYER_SAFETY_DIR/main/"
  fi
  if [[ -f "$NODE_WEBPLAYER_DOWNLOAD_FILE" ]]; then
    cp -a "$NODE_WEBPLAYER_DOWNLOAD_FILE" "$WEBPLAYER_SAFETY_DIR/node/managed-download.bin"
  fi
}
restore_main_webplayer_downloads(){
  [[ -n "$WEBPLAYER_SAFETY_DIR" && -d "$WEBPLAYER_SAFETY_DIR/main" ]] || return 0
  install -d -o streamforge -g streamforge -m 0755 "$WEBPLAYER_DOWNLOAD_ROOT"
  rsync -a "$WEBPLAYER_SAFETY_DIR/main/" "$WEBPLAYER_DOWNLOAD_ROOT/"
  chown -R streamforge:streamforge "$WEBPLAYER_DOWNLOAD_ROOT" 2>/dev/null || true
}
restore_node_webplayer_download(){
  [[ -n "$WEBPLAYER_SAFETY_DIR" && -f "$WEBPLAYER_SAFETY_DIR/node/managed-download.bin" ]] || return 0
  install -d -o streamforge-node -g streamforge-node -m 0755 "$(dirname "$NODE_WEBPLAYER_DOWNLOAD_FILE")"
  install -o streamforge-node -g streamforge-node -m 0644 "$WEBPLAYER_SAFETY_DIR/node/managed-download.bin" "$NODE_WEBPLAYER_DOWNLOAD_FILE"
}
verify_webplayer_downloads(){
  if [[ -n "$WEBPLAYER_SAFETY_DIR" && -d "$WEBPLAYER_SAFETY_DIR/main" ]]; then
    while IFS= read -r -d '' saved; do
      rel="${saved#$WEBPLAYER_SAFETY_DIR/main/}"
      [[ -f "$WEBPLAYER_DOWNLOAD_ROOT/$rel" ]] || die "Web Player uploaded file disappeared during update: $rel"
      cmp -s "$saved" "$WEBPLAYER_DOWNLOAD_ROOT/$rel" || die "Web Player uploaded file changed during update: $rel"
    done < <(find "$WEBPLAYER_SAFETY_DIR/main" -type f -print0)
  fi
  if [[ -n "$WEBPLAYER_SAFETY_DIR" && -f "$WEBPLAYER_SAFETY_DIR/node/managed-download.bin" ]]; then
    [[ -f "$NODE_WEBPLAYER_DOWNLOAD_FILE" ]] || die "Node Web Player uploaded file disappeared during update"
    cmp -s "$WEBPLAYER_SAFETY_DIR/node/managed-download.bin" "$NODE_WEBPLAYER_DOWNLOAD_FILE" || die "Node Web Player uploaded file changed during update"
  fi
}

requirements_hash(){
  python3 - "$1" <<'PY_REQ_HASH'
import hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
print(hashlib.sha256(path.read_bytes()).hexdigest())
PY_REQ_HASH
}

PREVIOUS_MAIN_REQUIREMENTS_HASH=""
PREVIOUS_NODE_REQUIREMENTS_HASH=""
[[ -f "$APP_DIR/requirements.txt" ]] && PREVIOUS_MAIN_REQUIREMENTS_HASH="$(requirements_hash "$APP_DIR/requirements.txt")"
[[ -f "$LOCAL_NODE_DIR/requirements.txt" ]] && PREVIOUS_NODE_REQUIREMENTS_HASH="$(requirements_hash "$LOCAL_NODE_DIR/requirements.txt")"

# STREAMFORGE_LOW_DISK_PREFLIGHT_V2184
disk_available_kb(){ df -Pk "$APP_DIR" | awk 'NR==2 {print $4}'; }
inode_available(){ df -Pi "$APP_DIR" | awk 'NR==2 {print $4}'; }

safe_reclaim_update_space(){
  log "Running low-disk update preflight cleanup."
  rm -rf /root/.cache/pip /tmp/pip-* /tmp/pip-build-* /tmp/pip-install-* 2>/dev/null || true
  if command -v apt-get >/dev/null 2>&1; then
    apt-get clean >/dev/null 2>&1 || true
  fi
  rm -f "$APP_DIR/.gunicorn" 2>/dev/null || true

  # Automatic rollback snapshots are updater-owned. Retain newest two.
  if [[ -d "$BACKUP_ROOT" ]]; then
    mapfile -t stale_backups < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'pre-v*' -printf '%T@ %p\n' 2>/dev/null | sort -nr | tail -n +3 | cut -d' ' -f2-)
    if ((${#stale_backups[@]})); then
      log "Removing ${#stale_backups[@]} old pre-update rollback backup(s); newest two retained."
      rm -rf -- "${stale_backups[@]}"
    fi
  fi
}

safe_reclaim_update_space
AVAILABLE_KB="$(disk_available_kb || echo 0)"
AVAILABLE_INODES="$(inode_available || echo 0)"
MIN_FREE_KB=524288
MIN_FREE_INODES=10000
log "Disk preflight: ${AVAILABLE_KB} KB free; ${AVAILABLE_INODES} inodes free."
if (( AVAILABLE_KB < MIN_FREE_KB )); then
  die "Insufficient free disk space before update. Need at least 512 MB free after safe cleanup; available ${AVAILABLE_KB} KB."
fi
if (( AVAILABLE_INODES < MIN_FREE_INODES )); then
  die "Insufficient free inodes before update. Need at least ${MIN_FREE_INODES}; available ${AVAILABLE_INODES}."
fi

# STREAMFORGE_SQLITE_SAFE_ROLLBACK_V61R3:
# Validate the live SQLite database before touching the deployment and create a
# transactionally consistent backup using SQLite's online backup API.  A raw
# cp of the main database file is not a safe snapshot while WAL mode is active.
sqlite_integrity_ok(){
  python3 - "$1" <<'PY_SQLITE_CHECK'
import sqlite3, sys
path = sys.argv[1]
try:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    row = connection.execute("PRAGMA quick_check(1)").fetchone()
    connection.close()
    raise SystemExit(0 if row and str(row[0]).strip().lower() == "ok" else 1)
except Exception:
    raise SystemExit(1)
PY_SQLITE_CHECK
}

sqlite_consistent_backup(){
  python3 - "$1" "$2" <<'PY_SQLITE_BACKUP'
import os, sqlite3, sys
source_path, backup_path = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(backup_path), exist_ok=True)
try:
    os.unlink(backup_path)
except FileNotFoundError:
    pass
source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
dest = sqlite3.connect(backup_path, timeout=30)
try:
    source.backup(dest)
    row = dest.execute("PRAGMA quick_check(1)").fetchone()
    if not row or str(row[0]).strip().lower() != "ok":
        raise RuntimeError(f"backup integrity check failed: {row!r}")
finally:
    dest.close()
    source.close()
PY_SQLITE_BACKUP
}

if [[ -n "$DB_PATH" && -f "$DB_PATH" ]]; then
  sqlite_integrity_ok "$DB_PATH" || die "SQLite integrity check failed before update. Database appears corrupted; update aborted without changing the installation."
fi

log "Creating backup: $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
snapshot_webplayer_downloads
rsync -a --exclude 'venv/' "$APP_DIR/" "$BACKUP_DIR/app/"
if [[ -n "$DB_PATH" && -f "$DB_PATH" ]]; then
  sqlite_consistent_backup "$DB_PATH" "$BACKUP_DIR/streamforge.db" || die "Could not create a consistent SQLite rollback backup"
fi
[[ -f "$ENV_FILE" ]] && cp -a "$ENV_FILE" "$BACKUP_DIR/streamforge.env"
[[ -f "$GEO_SETTINGS_FILE" ]] && cp -a "$GEO_SETTINGS_FILE" "$BACKUP_DIR/geoip-settings.json"
[[ -f "/etc/systemd/system/$SERVICE_NAME.service" ]] && cp -a "/etc/systemd/system/$SERVICE_NAME.service" "$BACKUP_DIR/"
if [[ -f /etc/systemd/system/streamforge-public.service ]]; then
  cp -a /etc/systemd/system/streamforge-public.service "$BACKUP_DIR/streamforge-public.service"
fi
if systemctl is-enabled --quiet streamforge-public >/dev/null 2>&1 || systemctl is-active --quiet streamforge-public >/dev/null 2>&1; then
  touch "$BACKUP_DIR/streamforge-public.should-start"
fi
if [[ -f /etc/systemd/system/streamforge-channel-supervisor.service ]]; then
  cp -a /etc/systemd/system/streamforge-channel-supervisor.service "$BACKUP_DIR/streamforge-channel-supervisor.service"
fi
if systemctl is-enabled --quiet streamforge-channel-supervisor >/dev/null 2>&1 || systemctl is-active --quiet streamforge-channel-supervisor >/dev/null 2>&1; then
  touch "$BACKUP_DIR/streamforge-channel-supervisor.should-start"
fi
[[ -f /etc/nginx/sites-available/streamforge ]] && cp -a /etc/nginx/sites-available/streamforge "$BACKUP_DIR/nginx.conf"
[[ -f /usr/local/sbin/streamforge-apply-main-access ]] && cp -a /usr/local/sbin/streamforge-apply-main-access "$BACKUP_DIR/streamforge-apply-main-access"
[[ -d /usr/local/libexec/streamforge ]] && cp -a /usr/local/libexec/streamforge "$BACKUP_DIR/streamforge-libexec"
[[ -f /etc/sudoers.d/streamforge-main-access ]] && cp -a /etc/sudoers.d/streamforge-main-access "$BACKUP_DIR/streamforge-main-access.sudoers"
[[ -f /etc/systemd/system/streamforge-main-access.service ]] && cp -a /etc/systemd/system/streamforge-main-access.service "$BACKUP_DIR/streamforge-main-access.service"
[[ -f /etc/systemd/system/streamforge-main-access.path ]] && cp -a /etc/systemd/system/streamforge-main-access.path "$BACKUP_DIR/streamforge-main-access.path"
[[ -f /etc/systemd/system/streamforge-main-tls.timer ]] && cp -a /etc/systemd/system/streamforge-main-tls.timer "$BACKUP_DIR/streamforge-main-tls.timer"
[[ -f /usr/local/sbin/streamforge-main-system-control ]] && cp -a /usr/local/sbin/streamforge-main-system-control "$BACKUP_DIR/streamforge-main-system-control"
[[ -f /usr/local/bin/streamforge-update ]] && cp -a /usr/local/bin/streamforge-update "$BACKUP_DIR/streamforge-update"
[[ -f /usr/local/bin/streamforge ]] && cp -a /usr/local/bin/streamforge "$BACKUP_DIR/streamforge-command"
[[ -f /etc/systemd/system/streamforge-main-system.service ]] && cp -a /etc/systemd/system/streamforge-main-system.service "$BACKUP_DIR/streamforge-main-system.service"
[[ -f /etc/systemd/system/streamforge-main-system.path ]] && cp -a /etc/systemd/system/streamforge-main-system.path "$BACKUP_DIR/streamforge-main-system.path"
if [[ -f "$LOCAL_NODE_DIR/app.py" ]]; then
  mkdir -p "$BACKUP_DIR/local-node"
  rsync -a --exclude 'venv/' "$LOCAL_NODE_DIR/" "$BACKUP_DIR/local-node/app/"
  [[ -f /etc/systemd/system/streamforge-node.service ]] && cp -a /etc/systemd/system/streamforge-node.service "$BACKUP_DIR/local-node/streamforge-node.service"
  [[ -f "$NODE_ENV_FILE" ]] && cp -a "$NODE_ENV_FILE" "$BACKUP_DIR/local-node/streamforge-node.env"
  if systemctl is-enabled --quiet streamforge-node >/dev/null 2>&1 || systemctl is-active --quiet streamforge-node >/dev/null 2>&1; then
    touch "$BACKUP_DIR/local-node/should-start"
  fi
fi

# STREAMFORGE_UPDATE_LOGO_SAFETY_TRANSACTION_V3029:
# Capture current logos before deployment. If an earlier update already left
# the canonical tree empty, recover from the newest non-empty updater rollback
# snapshot (for example pre-v3027/app/logo from the v3.2 update).
LOGO_SAFETY_DIR="$BACKUP_DIR/logo-safety"
python3 "$SOURCE_DIR/scripts/preserve_update_logos.py" snapshot "$LOGO_DIR" "$BACKUP_ROOT" "$LOGO_SAFETY_DIR"

rollback(){
  local rc="${1:-1}"
  trap - ERR INT TERM EXIT
  log "Update failed; restoring previous application and database."
  systemctl disable --now streamforge-public >/dev/null 2>&1 || true
  systemctl disable --now streamforge-channel-supervisor >/dev/null 2>&1 || true
  systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
  rsync -a --delete --exclude 'venv/' --exclude 'logo/' --exclude 'GeoLite2-ASN.mmdb' --exclude 'GeoLite2-Country.mmdb' "$BACKUP_DIR/app/" "$APP_DIR/" || true
  restore_main_webplayer_downloads || true
  [[ -f "$LOGO_SAFETY_DIR/UPDATE-LOGO-MANIFEST.json" ]] && python3 "$SOURCE_DIR/scripts/preserve_update_logos.py" restore "$LOGO_SAFETY_DIR" "$LOGO_DIR" || true
  if [[ -f "$BACKUP_DIR/streamforge.db" ]]; then
    restore_path="${DB_PATH:-$DATA_DIR/streamforge.db}"
    # The rollback snapshot is a complete standalone SQLite database.  Never
    # attach WAL/SHM files left by the failed/newer database to the restored
    # image; doing so can surface as "database disk image is malformed".
    rm -f "${restore_path}-wal" "${restore_path}-shm" "${restore_path}-journal" || true
    install -D -o streamforge -g streamforge -m 0640 "$BACKUP_DIR/streamforge.db" "$restore_path" || true
    rm -f "${restore_path}-wal" "${restore_path}-shm" "${restore_path}-journal" || true
  fi
  [[ -f "$BACKUP_DIR/streamforge.env" ]] && install -m 0600 "$BACKUP_DIR/streamforge.env" "$ENV_FILE" || true
  [[ -f "$BACKUP_DIR/geoip-settings.json" ]] && { mkdir -p "$(dirname "$GEO_SETTINGS_FILE")"; install -o streamforge -g streamforge -m 0600 "$BACKUP_DIR/geoip-settings.json" "$GEO_SETTINGS_FILE"; } || true
  [[ -f "$BACKUP_DIR/$SERVICE_NAME.service" ]] && install -m 0644 "$BACKUP_DIR/$SERVICE_NAME.service" "/etc/systemd/system/$SERVICE_NAME.service" || true
  if [[ -f "$BACKUP_DIR/streamforge-public.service" ]]; then
    install -m 0644 "$BACKUP_DIR/streamforge-public.service" /etc/systemd/system/streamforge-public.service
  else
    rm -f /etc/systemd/system/streamforge-public.service
  fi
  if [[ -f "$BACKUP_DIR/streamforge-channel-supervisor.service" ]]; then
    install -m 0644 "$BACKUP_DIR/streamforge-channel-supervisor.service" /etc/systemd/system/streamforge-channel-supervisor.service
  else
    rm -f /etc/systemd/system/streamforge-channel-supervisor.service
  fi
  [[ -f "$BACKUP_DIR/nginx.conf" ]] && install -m 0644 "$BACKUP_DIR/nginx.conf" /etc/nginx/sites-available/streamforge || true
  systemctl disable --now streamforge-main-access.path streamforge-main-tls.timer >/dev/null 2>&1 || true
  if [[ -f "$BACKUP_DIR/streamforge-main-access.service" ]]; then install -m 0644 "$BACKUP_DIR/streamforge-main-access.service" /etc/systemd/system/streamforge-main-access.service; else rm -f /etc/systemd/system/streamforge-main-access.service; fi
  if [[ -f "$BACKUP_DIR/streamforge-main-access.path" ]]; then install -m 0644 "$BACKUP_DIR/streamforge-main-access.path" /etc/systemd/system/streamforge-main-access.path; else rm -f /etc/systemd/system/streamforge-main-access.path; fi
  if [[ -f "$BACKUP_DIR/streamforge-main-tls.timer" ]]; then install -m 0644 "$BACKUP_DIR/streamforge-main-tls.timer" /etc/systemd/system/streamforge-main-tls.timer; else rm -f /etc/systemd/system/streamforge-main-tls.timer; fi
  if [[ -f "$BACKUP_DIR/streamforge-apply-main-access" ]]; then install -o root -g root -m 0755 "$BACKUP_DIR/streamforge-apply-main-access" /usr/local/sbin/streamforge-apply-main-access; else rm -f /usr/local/sbin/streamforge-apply-main-access; fi
  rm -rf /usr/local/libexec/streamforge
  if [[ -d "$BACKUP_DIR/streamforge-libexec" ]]; then cp -a "$BACKUP_DIR/streamforge-libexec" /usr/local/libexec/streamforge; fi
  if [[ -f "$BACKUP_DIR/streamforge-main-access.sudoers" ]]; then install -o root -g root -m 0440 "$BACKUP_DIR/streamforge-main-access.sudoers" /etc/sudoers.d/streamforge-main-access; else rm -f /etc/sudoers.d/streamforge-main-access; fi
  systemctl disable --now streamforge-main-system.path >/dev/null 2>&1 || true
  if [[ -f "$BACKUP_DIR/streamforge-main-system.service" ]]; then install -m 0644 "$BACKUP_DIR/streamforge-main-system.service" /etc/systemd/system/streamforge-main-system.service; else rm -f /etc/systemd/system/streamforge-main-system.service; fi
  if [[ -f "$BACKUP_DIR/streamforge-main-system.path" ]]; then install -m 0644 "$BACKUP_DIR/streamforge-main-system.path" /etc/systemd/system/streamforge-main-system.path; else rm -f /etc/systemd/system/streamforge-main-system.path; fi
  if [[ -f "$BACKUP_DIR/streamforge-main-system-control" ]]; then install -o root -g root -m 0755 "$BACKUP_DIR/streamforge-main-system-control" /usr/local/sbin/streamforge-main-system-control; else rm -f /usr/local/sbin/streamforge-main-system-control; fi
  if [[ -f "$BACKUP_DIR/streamforge-update" ]]; then install -o root -g root -m 0755 "$BACKUP_DIR/streamforge-update" /usr/local/bin/streamforge-update; else rm -f /usr/local/bin/streamforge-update; fi
  if [[ -f "$BACKUP_DIR/streamforge-command" ]]; then install -o root -g root -m 0755 "$BACKUP_DIR/streamforge-command" /usr/local/bin/streamforge; else rm -f /usr/local/bin/streamforge; fi
  if [[ -d "$BACKUP_DIR/local-node/app" ]]; then
    systemctl stop streamforge-node >/dev/null 2>&1 || true
    mkdir -p "$LOCAL_NODE_DIR"
  rsync -a --delete --exclude 'venv/' --exclude 'logo/' "$BACKUP_DIR/local-node/app/" "$LOCAL_NODE_DIR/" || true
    restore_node_webplayer_download || true
    [[ -f "$BACKUP_DIR/local-node/streamforge-node.service" ]] && install -m 0644 "$BACKUP_DIR/local-node/streamforge-node.service" /etc/systemd/system/streamforge-node.service || true
    [[ -f "$BACKUP_DIR/local-node/streamforge-node.env" ]] && install -m 0600 "$BACKUP_DIR/local-node/streamforge-node.env" "$NODE_ENV_FILE" || true
  fi
  grep -Fq 'Leave blank to hide the subtitle.' "$APP_DIR/app/templates/system_branding.html" || die "Optional subtitle UI missing"
grep -Fq 'if BRANDING_SUBTITLE_KEY in values' "$APP_DIR/app/main.py" || die "Blank subtitle persistence fix missing"
grep -Fq 'brand_subtitle: str = Form("")' "$APP_DIR/app/main.py" || die "Blank subtitle form default fix missing"
grep -Fq 'subtitle_row.value = cleaned_subtitle' "$APP_DIR/app/main.py" || die "Blank subtitle database persistence guard missing"
grep -Fq '.form-section + .form-section' "$APP_DIR/app/templates/system_branding.html" || die "Settings section spacing CSS missing"
grep -Fq '.panel-head{' "$APP_DIR/app/templates/system_branding.html" || die "Settings panel spacing CSS missing"
systemctl daemon-reload || true
  if [[ -f "$BACKUP_DIR/streamforge-public.should-start" && -f /etc/systemd/system/streamforge-public.service ]]; then
    systemctl enable --now streamforge-public >/dev/null 2>&1 || true
  fi
  if [[ -f "$BACKUP_DIR/streamforge-channel-supervisor.should-start" && -f /etc/systemd/system/streamforge-channel-supervisor.service ]]; then
    systemctl enable --now streamforge-channel-supervisor >/dev/null 2>&1 || true
  fi
  if [[ -f "$BACKUP_DIR/local-node/should-start" ]]; then
    systemctl enable streamforge-node >/dev/null 2>&1 || true
    systemctl restart streamforge-node >/dev/null 2>&1 || true
  fi
  [[ -f "$BACKUP_DIR/streamforge-main-access.path" ]] && systemctl enable --now streamforge-main-access.path >/dev/null 2>&1 || true
  [[ -f "$BACKUP_DIR/streamforge-main-tls.timer" ]] && systemctl enable --now streamforge-main-tls.timer >/dev/null 2>&1 || true
  [[ -f "$BACKUP_DIR/streamforge-main-system.path" ]] && systemctl enable --now streamforge-main-system.path >/dev/null 2>&1 || true
  nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  systemctl restart "$SERVICE_NAME" >/dev/null 2>&1 || true
  if [[ "$(cat "$APP_DIR/VERSION" 2>/dev/null || true)" != "$INSTALLED_VERSION" ]]; then
    log "WARNING: rollback version verification failed (expected $INSTALLED_VERSION)."
  fi
  log "Rollback completed: $BACKUP_DIR"
  exit "$rc"
}

rollback_on_exit(){
  local rc="${1:-1}"
  trap - ERR INT TERM EXIT
  if [[ "$UPDATE_COMMITTED" -eq 0 && "$MUTATION_STARTED" -eq 1 ]]; then
    rollback "$rc"
  fi
  exit "$rc"
}

# Keep an EXIT transaction guard active through every post-deployment check.
# This also catches an explicit `die`, an ERR exit, Ctrl-C and termination.
MUTATION_STARTED=1
trap 'rollback_on_exit $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

set_env_value(){
  local key="$1" value="$2"
  touch "$ENV_FILE"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i -E "s#^${key}=.*#${key}=${value}#" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

set_env_default(){
  local key="$1" value="$2"
  touch "$ENV_FILE"
  if ! grep -qE "^${key}=" "$ENV_FILE"; then
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

set_named_env_value(){
  local file="$1" key="$2" value="$3"
  touch "$file"
  if grep -qE "^${key}=" "$file"; then
    sed -i -E "s#^${key}=.*#${key}=${value}#" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

# STREAMFORGE_REDIS_AUTO_ENABLE_V61: add safe local defaults without
# overwriting an operator-supplied remote/authenticated Redis URL.
set_env_default STREAMFORGE_REDIS_ENABLED 1
set_env_default STREAMFORGE_REDIS_URL redis://127.0.0.1:6379/0
set_env_default STREAMFORGE_REDIS_PREFIX streamforge
set_env_default STREAMFORGE_REDIS_TIMEOUT_MS 150
set_env_default STREAMFORGE_REDIS_TOUCH_INTERVAL_MS 1000
set_env_default STREAMFORGE_REDIS_FAILURE_BACKOFF_SECONDS 5
# STREAMFORGE_PUBLIC_WORKER_CONFIG_V62
set_env_default STREAMFORGE_PUBLIC_WORKERS auto
set_env_default STREAMFORGE_PUBLIC_MAX_WORKERS auto
# v6.5-r1: migrate only the generated legacy 32-worker Auto ceiling.
if grep -qx 'STREAMFORGE_PUBLIC_WORKERS=auto' "$ENV_FILE" 2>/dev/null && grep -qx 'STREAMFORGE_PUBLIC_MAX_WORKERS=32' "$ENV_FILE" 2>/dev/null; then
  set_env_value STREAMFORGE_PUBLIC_MAX_WORKERS auto
fi

if ! command -v redis-server >/dev/null 2>&1; then
  log "Installing local Redis runtime-state service (best effort)."
  apt-get update -y >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y redis-server >/dev/null 2>&1 || log "WARNING: redis-server install failed; StreamForge will use local fallback state."
fi
if command -v redis-server >/dev/null 2>&1; then
  systemctl enable --now redis-server >/dev/null 2>&1 || log "WARNING: redis-server could not be started; local fallback remains active."
  if command -v redis-cli >/dev/null 2>&1 && redis-cli -h 127.0.0.1 -p 6379 ping 2>/dev/null | grep -qx PONG; then
    log "Redis runtime state is online on loopback."
  else
    log "WARNING: Redis loopback ping is not ready; StreamForge will fail open to local compatibility state."
  fi
fi

# STREAMFORGE_GEOIPUPDATE_AUTO_INSTALL_V99R7:
# Main/Local GeoIP settings can use MaxMind, but historical Main installers did
# not install geoipupdate even though Remote Node installers did. Repair the
# dependency before the application is restarted so saved MaxMind credentials
# work immediately after this update.
if ! command -v geoipupdate >/dev/null 2>&1; then
  log "Installing geoipupdate for MaxMind GeoIP database updates."
  apt-get update -y >/dev/null 2>&1 || true
  if DEBIAN_FRONTEND=noninteractive apt-get install -y geoipupdate >/dev/null 2>&1; then
    log "geoipupdate installed."
  else
    log "WARNING: geoipupdate could not be installed; IPinfo remains usable but MaxMind database download will remain unavailable until the package is installed."
  fi
fi

# Add desired_running while the old service still has its live status in SQLite.
# The old application ignores the extra column, then its shutdown cannot erase
# the stored operator intent.
if [[ -n "$DB_PATH" && -f "$DB_PATH" ]]; then
  
log "Google Drive backup uses the direct Google Drive API; rclone is not required."
if ! command -v smbclient >/dev/null 2>&1; then
  log "Installing smbclient for authenticated SMB backup targets."
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y smbclient
fi
command -v smbclient >/dev/null 2>&1 || die "smbclient installation failed"
install -d -o streamforge -g streamforge -m 0700 /var/lib/streamforge/backups/smb-auth

# STREAMFORGE_MANAGED_CERTBOT_DEPENDENCY_V37:
# HTTPS is additive; a transient package-repository issue must not break HTTP.
if ! command -v dig >/dev/null 2>&1; then
  log "Installing dnsutils for delegated DNS-01 verification (best effort)."
  apt-get update -y >/dev/null 2>&1 || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y dnsutils >/dev/null 2>&1 || log "WARNING: dnsutils could not be installed; CNAME checks will remain pending."
fi

if ! command -v certbot >/dev/null 2>&1; then
  log "Installing certbot for managed HTTPS aliases (best effort)."
  if apt-get update -y >/dev/null 2>&1 && DEBIAN_FRONTEND=noninteractive apt-get install -y certbot >/dev/null 2>&1; then
    log "certbot installed."
  else
    log "WARNING: certbot could not be installed now; HTTP remains available and HTTPS provisioning will retry after certbot is installed."
  fi
fi

# STREAMFORGE_YOUTUBE_RUNTIME_UPDATE_BOOTSTRAP_V1014:
# Optional YouTube support should self-heal missing unzip/Deno without making
# normal StreamForge update success depend on the external Deno host.
bash "$SOURCE_DIR/scripts/bootstrap_youtube_runtime.sh" || log "WARNING: YouTube JS runtime bootstrap did not complete; normal streams are unaffected."
# Existing cookie files from older/root-run tooling can be unreadable by the
# service account. Repair ownership before services are restarted.
if [[ -f /var/lib/streamforge/youtube-cookies.txt ]] && id streamforge >/dev/null 2>&1; then
  chown streamforge:streamforge /var/lib/streamforge/youtube-cookies.txt || true
  chmod 0600 /var/lib/streamforge/youtube-cookies.txt || true
fi
if [[ -f /var/lib/streamforge-node/youtube-cookies.txt ]] && id streamforge-node >/dev/null 2>&1; then
  chown streamforge-node:streamforge-node /var/lib/streamforge-node/youtube-cookies.txt || true
  chmod 0600 /var/lib/streamforge-node/youtube-cookies.txt || true
fi

if [[ "$RUN_LEGACY_MIGRATIONS" -eq 1 ]]; then
  log "Legacy compatibility update: capturing historical desired-running state."
  python3 "$SOURCE_DIR/scripts/migrate_v190.py" "$DB_PATH"
else
  log "Preserving existing channel start/stop settings; legacy desired-state migration skipped."
fi
fi

systemctl stop streamforge-public >/dev/null 2>&1 || true
systemctl stop "$SERVICE_NAME"
systemctl stop streamforge-channel-supervisor >/dev/null 2>&1 || true
rm -f "$DATA_DIR/main-channel-supervisor.sock" >/dev/null 2>&1 || true
log "Deploying StreamForge v2.0 low-latency HLS and lower-CPU encoding profiles on Main and Node."
rsync -a --delete \
  --exclude 'venv/' --exclude '*.db' --exclude 'logo/' \
  --exclude 'GeoLite2-ASN.mmdb' --exclude 'GeoLite2-Country.mmdb' --exclude 'geoip-settings.json' \
  "$SOURCE_DIR/" "$APP_DIR/"

# STREAMFORGE_MAIN_SELF_HOSTED_HLSJS_UPDATE_V1163:
bash "$APP_DIR/scripts/fetch_hlsjs.sh" "$APP_DIR/app/static/vendor/hls.min.js" || true
if [[ -f "$APP_DIR/app/static/vendor/hls.min.js" ]]; then
  chown streamforge:streamforge "$APP_DIR/app/static/vendor/hls.min.js" || true
  chmod 0644 "$APP_DIR/app/static/vendor/hls.min.js" || true
fi

# STREAMFORGE_MAIN_NGINX_PUBLIC_STATIC_CACHE_PUBLISH_V122:
# v12.1 pointed Nginx directly into /opt/streamforge/app/static. Existing
# installations commonly keep /opt/streamforge private to the streamforge
# service account, so www-data received 403 and the login/panel rendered as
# unstyled HTML. Mirror only public assets into an Nginx-readable cache.
MAIN_STATIC_DIR=/var/cache/streamforge/main-static
install -d -o root -g root -m 0755 "$MAIN_STATIC_DIR"
rsync -a --delete --chmod=D755,F644 "$APP_DIR/app/static/" "$MAIN_STATIC_DIR/"
chown -R root:root "$MAIN_STATIC_DIR"
[[ -r "$MAIN_STATIC_DIR/style.css" ]] || die "Published Main style.css is not readable"
[[ -r "$MAIN_STATIC_DIR/panel_nav.js" ]] || die "Published Main panel_nav.js is not readable"

# Restore the verified logo safety snapshot immediately after application
# deployment, before reference reconciliation or service startup can run.
python3 "$SOURCE_DIR/scripts/preserve_update_logos.py" restore "$LOGO_SAFETY_DIR" "$LOGO_DIR"
restore_main_webplayer_downloads

# v2.1.152: make release version markers deterministic after deployment.
# Some existing installations can retain the previous VERSION marker even after
# the rsync payload succeeds. Install these two tiny files explicitly so the
# live-version validation reflects the package that was actually deployed.
install -o streamforge -g streamforge -m 0644 "$SOURCE_DIR/VERSION" "$APP_DIR/VERSION"
install -o streamforge -g streamforge -m 0644 "$SOURCE_DIR/node_agent/VERSION" "$APP_DIR/node_agent/VERSION"

find "$APP_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$APP_DIR" -type f -name '*.pyc' -delete 2>/dev/null || true

# v1.11.229: Main and Node services run directly through /usr/bin/gunicorn
# with exactly one uvicorn-worker ASGI worker. Existing venv directories are
# retained only during migration and deleted after both services pass health checks.
install_global_python_requirements(){
  local requirements_file="$1" label="$2" stamp_file="$3" previous_hash="${4:-}"
  local wanted_hash current_stamp
  wanted_hash="$(requirements_hash "$requirements_file")"
  current_stamp="$(cat "$stamp_file" 2>/dev/null || true)"

  # STREAMFORGE_REQUIREMENTS_SKIP_V2193:
  # Do not reinstall Python plugins/dependencies when requirements are unchanged.
  # Existing v2.1.192 installs have no stamp yet, so the pre-update requirements
  # hash is accepted once and becomes the initial stamp without reinstalling.
  if [[ "$current_stamp" == "$wanted_hash" || ( -z "$current_stamp" && -n "$previous_hash" && "$previous_hash" == "$wanted_hash" ) ]]; then
    log "${label} Python dependencies unchanged; skipping pip install."
    install -d -m 0755 "$(dirname "$stamp_file")"
    printf '%s\n' "$wanted_hash" > "$stamp_file"
    return 0
  fi

  if ! python3 -m pip --version >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -y >/dev/null
      apt-get install -y python3-pip gunicorn >/dev/null
    else
      die "python3-pip is required for the system Gunicorn ${label} runtime"
    fi
  fi

  log "${label} Python requirements changed; installing dependencies once."
  local -a pip_args=(install --no-cache-dir --ignore-installed --upgrade -r "$requirements_file")
  if python3 -m pip help install 2>/dev/null | grep -q -- '--break-system-packages'; then
    pip_args=(install --break-system-packages --no-cache-dir --ignore-installed --upgrade -r "$requirements_file")
  fi
  python3 -m pip "${pip_args[@]}"
  install -d -m 0755 "$(dirname "$stamp_file")"
  printf '%s\n' "$wanted_hash" > "$stamp_file"
}

ensure_system_gunicorn(){
  if [[ ! -x /usr/bin/gunicorn ]]; then
    if command -v apt-get >/dev/null 2>&1; then
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -y >/dev/null
      apt-get install -y --reinstall gunicorn >/dev/null
    fi
  fi
  if [[ ! -x /usr/bin/gunicorn ]]; then
    local discovered
    discovered="$(command -v gunicorn || true)"
    [[ -n "$discovered" ]] || die "Gunicorn executable was not installed"
    ln -sfn "$discovered" /usr/bin/gunicorn
  fi
  /usr/bin/gunicorn --version >/dev/null
}

# STREAMFORGE_MAIN_ISOLATED_CERTBOT_UPDATE_V1030:
# Certbot must not share StreamForge's globally-upgraded Python import path.
# Ubuntu/Debian distro Certbot may be paired with older acme/josepy/pyOpenSSL
# packages, so install a pinned ACME client into a dedicated venv and make the
# root TLS helpers prefer that executable.
ensure_streamforge_certbot(){
  local certbot_venv=/opt/streamforge-certbot
  local certbot_version=5.7.0
  local certbot_bin="$certbot_venv/bin/certbot"
  local version_line=""
  if [[ -x "$certbot_bin" ]]; then
    version_line="$($certbot_bin --version 2>/dev/null || true)"
  fi
  if [[ "$version_line" != "certbot $certbot_version" ]]; then
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -y >/dev/null 2>&1 || true
      DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv >/dev/null
    fi
    rm -rf "$certbot_venv"
    python3 -m venv "$certbot_venv"
    "$certbot_venv/bin/python" -m pip install --no-cache-dir --upgrade pip >/dev/null
    "$certbot_venv/bin/python" -m pip install --no-cache-dir "certbot==$certbot_version"
  fi
  [[ -x "$certbot_bin" ]] || die "StreamForge isolated Certbot executable was not installed"
  version_line="$($certbot_bin --version 2>&1)" || die "StreamForge isolated Certbot failed: $version_line"
  [[ "$version_line" == "certbot $certbot_version" ]] || die "Unexpected StreamForge isolated Certbot version: $version_line"
  log "StreamForge isolated Certbot ready: $version_line"
}

install_global_python_requirements "$APP_DIR/requirements.txt" "Main" "$DATA_DIR/.python-requirements.sha256" "$PREVIOUS_MAIN_REQUIREMENTS_HASH"
ensure_system_gunicorn
ensure_streamforge_certbot
python3 - <<'PY_GLOBAL_MAIN'
import cryptography, OpenSSL, fastapi, gunicorn, itsdangerous, jinja2, maxminddb, multipart, paramiko, redis, sqlalchemy, uvicorn, uvicorn_worker, yt_dlp, yt_dlp_ejs
from OpenSSL import crypto
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
assert hasattr(crypto, "X509Extension") and hasattr(crypto, "X509Req")
print('system Gunicorn/ASGI Main dependencies ok; Certbot OpenSSL compatibility ok')
PY_GLOBAL_MAIN
printf 'system-gunicorn-asgi\n' > "$APP_DIR/RUNTIME_MODE"

install_global_node_requirements(){
  local requirements_file="$1"
  install_global_python_requirements "$requirements_file" "Node" "/var/lib/streamforge-node/.python-requirements.sha256" "$PREVIOUS_NODE_REQUIREMENTS_HASH"
  python3 - <<'PY_GLOBAL_NODE'
import cryptography, OpenSSL, fastapi, gunicorn, maxminddb, multipart, pydantic, redis, uvicorn, uvicorn_worker, yt_dlp, yt_dlp_ejs
from OpenSSL import crypto
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
assert hasattr(crypto, "X509Extension") and hasattr(crypto, "X509Req")
print('system Gunicorn/ASGI Node dependencies ok; Certbot OpenSSL compatibility ok')
PY_GLOBAL_NODE
}

if [[ -f "$LOCAL_NODE_DIR/app.py" ]]; then
  log "Converting co-located Node Agent to loopback Gunicorn behind Main Nginx port-80 routing."
  LOCAL_NODE_SHOULD_START=0
  systemctl is-enabled --quiet streamforge-node >/dev/null 2>&1 && LOCAL_NODE_SHOULD_START=1 || true
  systemctl is-active --quiet streamforge-node >/dev/null 2>&1 && LOCAL_NODE_SHOULD_START=1 || true
  systemctl stop streamforge-node >/dev/null 2>&1 || true
  mkdir -p "$LOCAL_NODE_DIR"
  rsync -a --delete --exclude 'venv/' --exclude 'logo/' "$APP_DIR/node_agent/" "$LOCAL_NODE_DIR/"
  # STREAMFORGE_COLOCATED_NODE_SELF_HOSTED_HLSJS_UPDATE_V1163:
  bash "$APP_DIR/scripts/fetch_hlsjs.sh" /var/lib/streamforge-node/hls.min.js || true
  if [[ -f /var/lib/streamforge-node/hls.min.js ]]; then
    chown streamforge-node:streamforge-node /var/lib/streamforge-node/hls.min.js || true
    chmod 0644 /var/lib/streamforge-node/hls.min.js || true
  fi
  restore_node_webplayer_download
  install_global_node_requirements "$LOCAL_NODE_DIR/requirements.txt"
  # Main Nginx owns public port 80 on a co-located deployment. Keep the Node
  # on a loopback-only internal listener and let the generated Nginx host/path
  # routes expose its Panel/API and Playlist/App aliases on public port 80.
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_BIND 127.0.0.1
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_PORT 8810
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_EXTERNAL_PROXY 1
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_CONTROL_BACKEND_PORT 8810
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_PUBLIC_BACKEND_PORT 8821
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_PUBLIC_WORKERS auto
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_PUBLIC_WORKERS_MAX auto
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_PUBLIC_MANAGED_BY_SYSTEMD 0
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_REDIS_ENABLED 1
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_REDIS_URL redis://127.0.0.1:6379/1
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_MEDIA_AUTH_CACHE_SECONDS 60
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_MEDIA_HEARTBEAT_TTL 150
  set_named_env_value "$NODE_ENV_FILE" STREAMFORGE_NODE_LOGO_ROOT "$LOCAL_NODE_DIR/logo"
  grep -qE '^STREAMFORGE_NODE_NVENC_FFMPEG=' "$NODE_ENV_FILE" || printf '%s\n' 'STREAMFORGE_NODE_NVENC_FFMPEG=/opt/ffmpeg-nvenc470/bin/ffmpeg' >> "$NODE_ENV_FILE"
  install -d -o streamforge-node -g streamforge-node -m 0755 "$LOCAL_NODE_DIR/logo"
  if [[ -d /var/lib/streamforge-node/node-logos ]]; then
    rsync -a --ignore-existing /var/lib/streamforge-node/node-logos/ "$LOCAL_NODE_DIR/logo/"
  fi
  chmod 0600 "$NODE_ENV_FILE"
  printf 'system-gunicorn-asgi\n' > "$LOCAL_NODE_DIR/RUNTIME_MODE"
  install -m 0644 "$APP_DIR/node_agent/deploy/streamforge-node.service" /etc/systemd/system/streamforge-node.service
  chown -R streamforge-node:streamforge-node "$LOCAL_NODE_DIR"
  systemctl daemon-reload
  if [[ "$LOCAL_NODE_SHOULD_START" -eq 1 ]]; then
    systemctl enable streamforge-node >/dev/null 2>&1 || true
    systemctl restart streamforge-node
    for attempt in $(seq 1 40); do
      systemctl is-active --quiet streamforge-node && break
      [[ "$attempt" -eq 40 ]] && { journalctl -u streamforge-node -n 120 --no-pager >&2 || true; die "Gunicorn Node service failed to start"; }
      sleep 1
    done
  fi
fi


if [[ -n "$DB_PATH" ]]; then
  if [[ "$RUN_LEGACY_MIGRATIONS" -eq 1 ]]; then
    log "Older installation detected; applying required compatibility migrations once."
    for migration in \
      migrate_rbac.py migrate_nodes.py migrate_multinode_loadbalance.py migrate_ssh_dns_relay.py \
      migrate_online_node_users.py migrate_unified_nodes_users.py migrate_v110.py migrate_v120.py \
      migrate_v130.py migrate_v150.py migrate_v160.py migrate_v180.py migrate_v181.py migrate_v190.py migrate_v1100.py migrate_v11116.py migrate_v11119.py migrate_v11120.py migrate_v11122.py migrate_v11161.py migrate_v11165.py migrate_v11174.py migrate_v11175.py migrate_v11182.py migrate_v11183.py migrate_v2148.py; do
      [[ -f "$APP_DIR/scripts/$migration" ]] && python3 "$APP_DIR/scripts/$migration" "$DB_PATH"
    done
    [[ -f "$APP_DIR/scripts/migrate_v11185.py" ]] && python3 "$APP_DIR/scripts/migrate_v11185.py" "$DB_PATH" "$ENV_FILE"
    [[ -f "$APP_DIR/scripts/migrate_v11189.py" ]] && python3 "$APP_DIR/scripts/migrate_v11189.py" "$DB_PATH"
    [[ -f "$APP_DIR/scripts/migrate_v11190.py" ]] && python3 "$APP_DIR/scripts/migrate_v11190.py" "$DB_PATH"
    [[ -f "$APP_DIR/scripts/migrate_v11191.py" ]] && python3 "$APP_DIR/scripts/migrate_v11191.py" "$DB_PATH"
    [[ -f "$APP_DIR/scripts/migrate_v11197.py" ]] && python3 "$APP_DIR/scripts/migrate_v11197.py" "$DB_PATH"
    [[ -f "$APP_DIR/scripts/migrate_v111107.py" ]] && python3 "$APP_DIR/scripts/migrate_v111107.py" "$DB_PATH"
    [[ -f "$APP_DIR/scripts/migrate_v200.py" ]] && python3 "$APP_DIR/scripts/migrate_v200.py" "$DB_PATH"
    [[ -f "$APP_DIR/scripts/migrate_v201.py" ]] && python3 "$APP_DIR/scripts/migrate_v201.py" "$DB_PATH"
  else
    # v4.0 has no schema-shape migration. The control-scope compatibility
    # migration below only clears stale Main desired-state on remote-only rows.
    log "Modern update detected; applying v9.8/v9.9 additive Panel access schema and Main/Node compatibility safely."

# STREAMFORGE_PACKAGE_FORCE_UPDATER_PRUNE_V2248:
# Keep only the current force updater in the deployed application. Historical
# version-specific updater copies are not required for modern forward updates.
if [[ -d "$APP_DIR/scripts" ]]; then
  find "$APP_DIR/scripts" -maxdepth 1 -type f -name 'force_update_v*.sh' ! -name "$UPDATER_BASENAME" -delete 2>/dev/null || true
fi
fi
  # STREAMFORGE_MAIN_NODE_STATE_SPLIT_MIGRATION_V33:
  # Main desired_running now represents Local FFmpeg only. This migration never
  # edits Remote Node state files.
  [[ -f "$APP_DIR/scripts/migrate_v33_control_scope.py" ]] && python3 "$APP_DIR/scripts/migrate_v33_control_scope.py" "$DB_PATH"
  # STREAMFORGE_V36_PUBLIC_URL_PORT_REPAIR_MIGRATION:
  # Repair v3.5-generated https://host:80 and http://host:443 public aliases
  # before the access runtime/Nginx configuration is regenerated.
  [[ -f "$APP_DIR/scripts/migrate_v36_public_url_ports.py" ]] && python3 "$APP_DIR/scripts/migrate_v36_public_url_ports.py" "$DB_PATH"
  # STREAMFORGE_V98_PANEL_IP_WHITELIST_MIGRATION_ORDER_R2:
  # v9.8 adds nodes.panel_ip_whitelist. Apply this additive migration before
  # reset_domain/apply_main_access/install verification can inspect the new model.
  [[ -x "$APP_DIR/scripts/migrate_v98_panel_ip_whitelist.py" ]] || die "Installed v9.8 Panel IP whitelist migration script missing"
  python3 "$APP_DIR/scripts/migrate_v98_panel_ip_whitelist.py" "$DB_PATH"
  python3 - "$DB_PATH" <<'PY_V98_SCHEMA_VERIFY'
import sqlite3, sys
path = sys.argv[1]
con = sqlite3.connect(path, timeout=10)
try:
    cols = {row[1] for row in con.execute("PRAGMA table_info(nodes)")}
finally:
    con.close()
if "panel_ip_whitelist" not in cols:
    raise SystemExit("v9.8 schema verification failed: nodes.panel_ip_whitelist missing")
print("v9.8 Panel whitelist database schema verified")
PY_V98_SCHEMA_VERIFY
  # STREAMFORGE_V99_PANEL_ACCESS_POLICY_MIGRATION_ORDER:
  # Add the remaining independent Panel browser policy columns before the
  # model/install verifier can inspect the v9.9 Node schema.
  [[ -x "$APP_DIR/scripts/migrate_v99_panel_access_policy.py" ]] || die "Installed v9.9 Panel access migration script missing"
  python3 "$APP_DIR/scripts/migrate_v99_panel_access_policy.py" "$DB_PATH"
  python3 - "$DB_PATH" <<'PY_V99_SCHEMA_VERIFY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1], timeout=10)
try:
    cols = {row[1] for row in con.execute("PRAGMA table_info(nodes)")}
finally:
    con.close()
required = {"panel_ip_whitelist", "panel_ip_blacklist", "panel_asn_whitelist", "panel_asn_blacklist"}
missing = sorted(required - cols)
if missing:
    raise SystemExit("v9.9 schema verification failed: missing " + ", ".join(missing))
print("v9.9 Panel access database schema verified")
PY_V99_SCHEMA_VERIFY
  # STREAMFORGE_WEBPLAYER_BRAND_ACCESS_ALIAS_MIGRATION_V1158:
  # v11.55 stored host-based brand profiles separately from strict Playlist/App
  # aliases. Upgrade existing profiles in-place so a brand that already exists
  # becomes reachable immediately after v11.58 without requiring a re-save.
  python3 - "$DB_PATH" <<'PY_V1158_BRAND_ALIAS'
import json, re, sqlite3, sys, urllib.parse

path = sys.argv[1]
connection = sqlite3.connect(path, timeout=20)
changed_nodes = 0
added_aliases = 0

def root_url(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = raw if "://" in raw else "http://" + raw
    try:
        parsed = urllib.parse.urlsplit(candidate)
        host = str(parsed.hostname or "").strip().lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host:
            return ""
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    safe_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = safe_host if port in {None, default_port} else f"{safe_host}:{port}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, "", "", "")).rstrip("/")

try:
    rows = connection.execute(
        "SELECT key, value FROM app_settings WHERE key LIKE 'webplayer_%_brands_json'"
    ).fetchall()
    for key, raw_json in rows:
        match = re.fullmatch(r"webplayer_(\d+)_brands_json", str(key or ""))
        if not match:
            continue
        node_id = int(match.group(1))
        try:
            brands = json.loads(str(raw_json or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(brands, list):
            continue
        wanted = []
        for brand in brands[:16]:
            if not isinstance(brand, dict):
                continue
            values = brand.get("access_urls") or brand.get("domains") or []
            if isinstance(values, str):
                values = re.split(r"[\s,;]+", values)
            for value in list(values or [])[:32]:
                normalized = root_url(value)
                if normalized and normalized not in wanted:
                    wanted.append(normalized)
        if not wanted:
            continue
        node = connection.execute(
            "SELECT playlist_url, playlist_urls FROM nodes WHERE id=?", (node_id,)
        ).fetchone()
        if not node:
            continue
        primary = str(node[0] or "").strip().rstrip("/")
        current = [line.strip().rstrip("/") for line in str(node[1] or node[0] or "").replace("\r", "").splitlines() if line.strip()]
        before = list(current)
        for value in wanted:
            if value not in current:
                current.append(value)
                added_aliases += 1
        if current != before:
            if not primary:
                primary = current[0]
            connection.execute(
                "UPDATE nodes SET playlist_url=?, playlist_urls=? WHERE id=?",
                (primary, "\n".join(current), node_id),
            )
            changed_nodes += 1
    connection.commit()
finally:
    connection.close()
print(f"v11.58 Web Player brand access migration complete; added {added_aliases} alias(es) on {changed_nodes} node(s)")
PY_V1158_BRAND_ALIAS
  # STREAMFORGE_UPDATE_DATABASE_AUTHORITY_FIRST_V3054:
  # Normal updates preserve the database's exact Panel/Playlist URL lists.
  # The environment URL is only a fallback when the database has no usable
  # authority; it must never replace a configured domain with a detected IP.
  # The helper also repairs the v3.0.53 IP rewrite from its automatic safety
  # snapshot before Nginx is regenerated.
  chown streamforge:streamforge "$APP_DIR" "$APP_DIR/scripts" "$APP_DIR/scripts/reset_domain.py"
  chmod u+rX "$APP_DIR" "$APP_DIR/scripts"
  chmod 0755 "$APP_DIR/scripts/reset_domain.py"
  runuser -u streamforge -- test -r "$APP_DIR/scripts/reset_domain.py"
  CURRENT_PUBLIC_URL="$(runuser -u streamforge -- env PYTHONPATH="$APP_DIR" \
    python3 "$APP_DIR/scripts/reset_domain.py" update-authority "sqlite:///$DB_PATH" "$INSTALLED_VERSION")"
  if [[ -z "$CURRENT_PUBLIC_URL" ]]; then
    # STREAMFORGE_UPDATE_ACCESS_FROM_ENV_V3023 compatibility fallback:
    CURRENT_PUBLIC_URL="$(python3 - "$ENV_FILE" <<'PY_CURRENT_PUBLIC_URL'
from pathlib import Path
import sys
for raw in Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == "STREAMFORGE_PUBLIC_BASE_URL":
        print(value.strip().strip('"').strip("'"))
        break
PY_CURRENT_PUBLIC_URL
)"
  fi
  if [[ -n "$CURRENT_PUBLIC_URL" ]]; then
    # STREAMFORGE_UPDATE_ACCESS_HELPER_PERMISSION_V3025 compatibility marker.
    CURRENT_PUBLIC_URL="$(python3 "$APP_DIR/scripts/reset_domain.py" normalize "$CURRENT_PUBLIC_URL")"
    # STREAMFORGE_UPDATE_QUIET_AUTHORITY_SYNC_V3037: this maintenance step is
    # intentionally silent on success. Only align runtime environment values
    # to the database-selected authority; database URLs remain untouched.
    python3 "$APP_DIR/scripts/reset_domain.py" environment "$CURRENT_PUBLIC_URL" "$ENV_FILE" >/dev/null
  fi
  # STREAMFORGE_UPDATE_RECONCILE_CURRENT_LOGOS_V3021: users who already
  # restored with v3.0.20 have the verified files on disk. Repair those live
  # references during this update so a second full restore is unnecessary.
  CURRENT_LOGO_REPAIR_RESULT="$(python3 - "$APP_DIR/scripts/main_system_control.py" "$DB_PATH" "$ENV_FILE" "$APP_DIR" "$DATA_DIR" <<'PY_CURRENT_LOGO_REBASE'
import pwd
import runpy
import sys
from pathlib import Path

namespace = runpy.run_path(sys.argv[1], run_name="streamforge_update_logo_rebase")
reconcile = namespace["_reconcile_restored_logo_references"]
canonicalize = namespace["_canonicalize_logo_storage"]
reconcile.__globals__["ENV_FILE"] = Path(sys.argv[3])
reconcile.__globals__["APP_DIR"] = Path(sys.argv[4])
reconcile.__globals__["DATA_DIR"] = Path(sys.argv[5])
identity = pwd.getpwnam("streamforge")
copied = canonicalize(identity.pw_uid, identity.pw_gid)
rebased = reconcile(Path(sys.argv[2]))
print(f"{copied},{rebased}")
PY_CURRENT_LOGO_REBASE
)"
  IFS=, read -r CURRENT_LOGO_COPY_COUNT CURRENT_LOGO_REBASE_COUNT <<< "$CURRENT_LOGO_REPAIR_RESULT"
  [[ "$CURRENT_LOGO_COPY_COUNT" =~ ^[0-9]+$ ]] || die "Canonical logo-storage migration returned an invalid result"
  [[ "$CURRENT_LOGO_REBASE_COUNT" =~ ^[0-9]+$ ]] || die "Current restored-logo reference reconciliation returned an invalid result"
  log "Logo files copied into canonical /opt/streamforge/logo: $CURRENT_LOGO_COPY_COUNT"
  log "Current restored logo references rebased to archived files: $CURRENT_LOGO_REBASE_COUNT"
  python3 - "$DB_PATH" <<'PY_NODE_USER_PLAYLIST_ORDER'
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
try:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(node_stream_users)")}
finally:
    connection.close()
if "playlist_order" not in columns:
    raise SystemExit("node_stream_users.playlist_order migration was not applied")
PY_NODE_USER_PLAYLIST_ORDER
  python3 - "$DB_PATH" <<'PY_NODE_HLS_SEGMENT'
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
try:
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(channel_nodes)")}
finally:
    connection.close()
if "hls_segment_time" not in columns:
    raise SystemExit("channel_nodes.hls_segment_time migration was not applied")
PY_NODE_HLS_SEGMENT
  chown streamforge:streamforge "$DB_PATH"
fi

mkdir -p "$LOGO_DIR"
for legacy_logo_dir in "$DATA_DIR/logos" "$DATA_DIR/node-logos"; do
  [[ -d "$legacy_logo_dir" ]] && rsync -a --ignore-existing "$legacy_logo_dir/" "$LOGO_DIR/"
done

if [[ ! -f "$ASN_FILE" ]]; then
  for legacy_asn in "$DATA_DIR/GeoLite2-ASN.mmdb" /var/lib/streamforge-node/GeoLite2-ASN.mmdb; do
    if [[ -f "$legacy_asn" ]]; then install -m 0644 "$legacy_asn" "$ASN_FILE"; break; fi
  done
fi
if [[ ! -f "$COUNTRY_FILE" ]]; then
  for legacy_country in "$DATA_DIR/GeoLite2-Country.mmdb" /var/lib/streamforge-node/GeoLite2-Country.mmdb; do
    if [[ -f "$legacy_country" ]]; then install -m 0644 "$legacy_country" "$COUNTRY_FILE"; break; fi
  done
fi

set_env_value STREAMFORGE_LOGO_ROOT "$LOGO_DIR"
set_env_value STREAMFORGE_NODE_LOGO_ROOT "$LOGO_DIR"
set_env_value STREAMFORGE_ASN_DB_PATH "$ASN_FILE"
set_env_value STREAMFORGE_COUNTRY_DB_PATH "$COUNTRY_FILE"
set_env_value STREAMFORGE_GEOIP_SETTINGS_FILE "$GEO_SETTINGS_FILE"
set_env_value STREAMFORGE_MAIN_ACCESS_RUNTIME_DIR "$DATA_DIR/main-access-runtime"

# Backfill every current Main runtime/config key that older installs may not
# have. Existing administrator-customized values are preserved.
MAIN_HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
[[ -n "$MAIN_HOST_IP" ]] || MAIN_HOST_IP="127.0.0.1"

set_env_default STREAMFORGE_FFMPEG_BIN /usr/bin/ffmpeg
set_env_default STREAMFORGE_NVENC_FFMPEG_BIN /opt/ffmpeg-nvenc470/bin/ffmpeg
set_env_default STREAMFORGE_FFPROBE_BIN /usr/bin/ffprobe
set_env_default STREAMFORGE_FFPROBE_TIMEOUT 10
set_env_default STREAMFORGE_FFPROBE_ANALYZEDURATION 8000000
set_env_default STREAMFORGE_FFPROBE_PROBESIZE 20000000
set_env_default STREAMFORGE_PUBLIC_BASE_URL "http://${MAIN_HOST_IP}"
set_env_default STREAMFORGE_RELAY_BASE_URL "http://${MAIN_HOST_IP}"
set_env_default STREAMFORGE_RELAY_START_WAIT_SECONDS 20
set_env_default STREAMFORGE_CPU_ENCODE_THREADS 2
set_env_default STREAMFORGE_PROGRESS_INTERVAL 3
set_env_default STREAMFORGE_VAAPI_DEVICE /dev/dri/renderD128
set_env_default STREAMFORGE_HTTP_USER_AGENT "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 StreamForge/2.1.158"
set_env_default STREAMFORGE_HTTP_RW_TIMEOUT_US 15000000
set_env_default STREAMFORGE_HTTP_RECONNECT_DELAY_MAX 10
set_env_default STREAMFORGE_AUTO_RESTART_STALL_SECONDS 30
set_env_default STREAMFORGE_AUTO_RESTART_CHECK_INTERVAL 5
set_env_default STREAMFORGE_FAILBACK_PROBE_TIMEOUT 5
set_env_default STREAMFORGE_LOG_PAGE_LIMIT 500
set_env_default STREAMFORGE_VIEWER_KEY_TTL_SECONDS 43200
set_env_default STREAMFORGE_RESTREAM_KEY_TTL_SECONDS 86400
set_env_value STREAMFORGE_NODE_VIEWER_TTL 5
set_env_default STREAMFORGE_TIMEZONE Asia/Dhaka
set_env_default STREAMFORGE_GEOIP_AUTO_UPDATE 0
set_env_default STREAMFORGE_MAXMIND_ACCOUNT_ID ""
set_env_default STREAMFORGE_MAXMIND_LICENSE_KEY ""
set_env_default STREAMFORGE_ACME_DNS_API "https://auth.acme-dns.io"

mkdir -p "$(dirname "$GEO_SETTINGS_FILE")"
[[ -f "$GEO_SETTINGS_FILE" ]] && chown streamforge:streamforge "$GEO_SETTINGS_FILE" && chmod 0600 "$GEO_SETTINGS_FILE"
chmod 0600 "$ENV_FILE"

# v2.1.152: create Gunicorn's worker temp directory BEFORE installing/restarting
# the service. v2.1.85 accidentally created it only in rollback().
install -d -o streamforge -g streamforge -m 0750 /var/lib/streamforge/gunicorn-tmp
install -d -o streamforge -g streamforge -m 0700 /var/lib/streamforge/.ssh
touch /var/lib/streamforge/.ssh/known_hosts
chown streamforge:streamforge /var/lib/streamforge/.ssh/known_hosts
chmod 0600 /var/lib/streamforge/.ssh/known_hosts

runuser -u streamforge -- env TMPDIR=/var/lib/streamforge/gunicorn-tmp python3 - <<'PY_GUNICORN_TMP'
import os, tempfile
fd, path = tempfile.mkstemp(prefix="streamforge-worker-", dir="/var/lib/streamforge/gunicorn-tmp")
os.close(fd)
os.unlink(path)
print("Gunicorn worker temp directory verified before service restart")
PY_GUNICORN_TMP

[[ -d /var/lib/streamforge/gunicorn-tmp ]] || die "Gunicorn worker temp directory was not created"

sed "s#/opt/streamforge#$APP_DIR#g" "$APP_DIR/deploy/streamforge.service" > "/etc/systemd/system/$SERVICE_NAME.service"
chmod 0644 "/etc/systemd/system/$SERVICE_NAME.service"
sed "s#/opt/streamforge#$APP_DIR#g" "$APP_DIR/deploy/streamforge-channel-supervisor.service" > /etc/systemd/system/streamforge-channel-supervisor.service
chmod 0644 /etc/systemd/system/streamforge-channel-supervisor.service
sed "s#/opt/streamforge#$APP_DIR#g" "$APP_DIR/deploy/streamforge-public.service" > /etc/systemd/system/streamforge-public.service
chmod 0644 /etc/systemd/system/streamforge-public.service
chmod 0755 "$APP_DIR/scripts/streamforge-public-start"
install -m 0644 "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/streamforge
python3 "$APP_DIR/scripts/tune_high_concurrency.py"
ln -sfn /etc/nginx/sites-available/streamforge /etc/nginx/sites-enabled/streamforge
install -o root -g root -m 0755 "$APP_DIR/scripts/apply_main_access.py" /usr/local/sbin/streamforge-apply-main-access
install -d -o root -g root -m 0755 /usr/local/libexec/streamforge
install -o root -g root -m 0644 "$APP_DIR/scripts/acme_dns_client.py" /usr/local/libexec/streamforge/acme_dns_client.py
install -o root -g root -m 0755 "$APP_DIR/scripts/acme_dns_hook.py" /usr/local/libexec/streamforge/acme_dns_hook.py
mkdir -p /var/lib/streamforge/acme-webroot /var/lib/streamforge/tls /var/lib/streamforge/acme-dns
if command -v certbot >/dev/null 2>&1; then
  # STREAMFORGE_CERTBOT_SANDBOX_WRITES_V38: certbot runs inside the hardened
  # Main-access oneshot, so its config/work/log roots must exist before the
  # unit's ReadWritePaths= bind mounts are created.
  mkdir -p /etc/letsencrypt/renewal-hooks/deploy /var/lib/letsencrypt /var/log/letsencrypt
  install -o root -g root -m 0755 "$APP_DIR/scripts/streamforge_tls_renew_hook.sh" /etc/letsencrypt/renewal-hooks/deploy/streamforge-nginx-reload
  systemctl enable --now certbot.timer >/dev/null 2>&1 || true
fi
install -d -o streamforge -g streamforge -m 0750 "$DATA_DIR/main-access-runtime"
sed -e "s#/var/lib/streamforge#$DATA_DIR#g" -e "s#/etc/streamforge.env#$ENV_FILE#g" "$APP_DIR/deploy/streamforge-main-access.service" > /etc/systemd/system/streamforge-main-access.service
sed "s#/var/lib/streamforge#$DATA_DIR#g" "$APP_DIR/deploy/streamforge-main-access.path" > /etc/systemd/system/streamforge-main-access.path
install -m 0644 "$APP_DIR/deploy/streamforge-main-tls.timer" /etc/systemd/system/streamforge-main-tls.timer
chmod 0644 /etc/systemd/system/streamforge-main-access.service /etc/systemd/system/streamforge-main-access.path /etc/systemd/system/streamforge-main-tls.timer
rm -f /etc/sudoers.d/streamforge-main-access
rm -f "$DATA_DIR/main-access-runtime/request.json" "$DATA_DIR/main-access-runtime/result.json"
systemctl daemon-reload
systemctl enable --now streamforge-main-access.path streamforge-main-tls.timer
# STREAMFORGE_ROOT_RESTORE_HELPER_REFRESH_V3019: stop the watcher before
# replacing its executable, then prove the privileged copy is exactly the
# package copy and accepts real v3 logo-asset archive members.
systemctl stop streamforge-main-system.path >/dev/null 2>&1 || true
install -o root -g root -m 0755 "$APP_DIR/scripts/main_system_control.py" /usr/local/sbin/streamforge-main-system-control
install -m 0755 "$APP_DIR/scripts/streamforge-capacity-audit" /usr/local/bin/streamforge-capacity-audit
install -m 0755 "$APP_DIR/scripts/uninstall.sh" /usr/local/sbin/streamforge-uninstall
install -m 0755 "$APP_DIR/scripts/cache_clear.sh" /usr/local/sbin/streamforge-cache-clear
install -m 0755 "$APP_DIR/scripts/streamforge-update" /usr/local/bin/streamforge-update
install -m 0755 "$APP_DIR/scripts/streamforge" /usr/local/bin/streamforge
install -d -o streamforge -g streamforge -m 0750 "$DATA_DIR/main-system-runtime"
install -d -o streamforge -g streamforge -m 0700 "$DATA_DIR/restore-inbox"
install -d -o root -g root -m 0750 /var/backups/streamforge
sed "s#/var/lib/streamforge#$DATA_DIR#g" "$APP_DIR/deploy/streamforge-main-system.service" > /etc/systemd/system/streamforge-main-system.service
sed "s#/var/lib/streamforge#$DATA_DIR#g" "$APP_DIR/deploy/streamforge-main-system.path" > /etc/systemd/system/streamforge-main-system.path
chmod 0644 /etc/systemd/system/streamforge-main-system.service /etc/systemd/system/streamforge-main-system.path
rm -f "$DATA_DIR/main-system-runtime/request.json" "$DATA_DIR/main-system-runtime/result.json"
systemctl daemon-reload
systemctl enable --now streamforge-main-system.path
cmp -s "$APP_DIR/scripts/main_system_control.py" /usr/local/sbin/streamforge-main-system-control || die "Root restore helper does not match the installed package"
grep -Fq 'STREAMFORGE_RESTORE_ASSET_MEMBER_POLICY_V3019' /usr/local/sbin/streamforge-main-system-control || die "Root restore helper logo policy is stale"
python3 - /usr/local/sbin/streamforge-main-system-control <<'PY_RESTORE_MEMBER_POLICY'
import runpy
import sys
import tarfile

namespace = runpy.run_path(sys.argv[1], run_name="streamforge_restore_policy_self_test")
check = namespace["_safe_member_name"]
accepted = (
    "streamforge-assets/main-logo/bangla-vision-hd.png",
    "streamforge-assets/node-logo/node-3-favicon.png",
    "streamforge-assets/ASSET-MANIFEST.json",
)
for name in accepted:
    member = tarfile.TarInfo(name)
    member.size = 1
    assert check(member) == name
try:
    check(tarfile.TarInfo("streamforge-assets/unmanaged/file.png"))
except RuntimeError:
    pass
else:
    raise SystemExit("unsafe restore asset path was accepted")
print("Root restore logo archive-member policy verified")
PY_RESTORE_MEMBER_POLICY
nginx -t
STREAMFORGE_MAIN_ACCESS_RUNTIME_DIR="$DATA_DIR/main-access-runtime" STREAMFORGE_DATABASE_PATH="${DB_PATH:-$DATA_DIR/streamforge.db}" /usr/local/sbin/streamforge-apply-main-access
python3 "$APP_DIR/scripts/verify_main_install.py" --app-dir "$APP_DIR" --data-dir "$DATA_DIR" --env-file "$ENV_FILE" --nginx-site /etc/nginx/sites-available/streamforge

if python3 - "${DB_PATH:-$DATA_DIR/streamforge.db}" <<'PY'
import sqlite3, sys
path = sys.argv[1]
try:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
    row = connection.execute(
        "SELECT dns_only, api_urls, api_url, playlist_urls, playlist_url "
        "FROM nodes WHERE node_type='local' ORDER BY id LIMIT 1"
    ).fetchone()
finally:
    try:
        connection.close()
    except Exception:
        pass
strict = bool(row and row[0] and any(str(value or '').strip() for value in row[1:]))
raise SystemExit(0 if strict else 1)
PY
then
  grep -q 'return 444;' /etc/nginx/sites-available/streamforge || die "Strict Main access did not install the silent unknown-host block"
fi

# STREAMFORGE_CERTBOT_RETRY_AFTER_SANDBOX_FIX_V38: v3.7 may have left a
# five-minute failed-attempt throttle after Certbot hit read-only /etc/letsencrypt.
# The corrected service sandbox is live now, so allow one immediate retry.
rm -f "$DATA_DIR/tls/"*.last-attempt 2>/dev/null || true

HANDOFF_REQUEST_ID="updater-$(date +%s%N)"
HANDOFF_TMP="$DATA_DIR/main-access-runtime/.request-$HANDOFF_REQUEST_ID.tmp"
printf '{"request_id":"%s","requested_at":"updater"}\n' "$HANDOFF_REQUEST_ID" > "$HANDOFF_TMP"
chown streamforge:streamforge "$HANDOFF_TMP"
chmod 0640 "$HANDOFF_TMP"
mv -f "$HANDOFF_TMP" "$DATA_DIR/main-access-runtime/request.json"
for attempt in $(seq 1 300); do
  if python3 - "$DATA_DIR/main-access-runtime/result.json" "$HANDOFF_REQUEST_ID" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
request_id = sys.argv[2]
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if payload.get("request_id") == request_id and payload.get("ok") is True else 1)
PY
  then break; fi
  [[ "$attempt" -eq 300 ]] && { systemctl status streamforge-main-access.path streamforge-main-access.service --no-pager || true; die "Main listener systemd handoff test failed after TLS provisioning wait"; }
  sleep 1
done

mkdir -p "$DATA_DIR/hls" "$LOGO_DIR"
chown -R streamforge:streamforge "$APP_DIR" "$DATA_DIR" "$LOGO_DIR"
python3 "$SOURCE_DIR/scripts/preserve_update_logos.py" verify "$LOGO_SAFETY_DIR" "$LOGO_DIR"
[[ -f "$ASN_FILE" ]] && chmod 0644 "$ASN_FILE"
[[ -f "$COUNTRY_FILE" ]] && chmod 0644 "$COUNTRY_FILE"

python3 -m py_compile "$APP_DIR"/app/*.py "$APP_DIR"/node_agent/app.py
python3 - <<PY
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
root = Path(${APP_DIR@Q}) / 'app' / 'templates'
env = Environment(loader=FileSystemLoader(root))
for template in root.glob('*.html'):
    env.get_template(template.name)
import maxminddb
print('templates and MaxMind dependency ok')
PY

[[ "$(cat "$APP_DIR/VERSION")" == "12.12" ]] || die "Live files still have the wrong version"
[[ "$(cat "$APP_DIR/node_agent/VERSION")" == "12.12" ]] || die "Wrong packaged Node Agent version"
grep -Eq '^yt-dlp\[default\]>=2026\.6\.9$' "$APP_DIR/requirements.txt" || die "Installed Main yt-dlp dependency is stale"
grep -Eq '^yt-dlp\[default\]>=2026\.6\.9$' "$APP_DIR/node_agent/requirements.txt" || die "Installed Node yt-dlp dependency is stale"
grep -Fq 'STREAMFORGE_MAIN_YOUTUBE_PROBE_TIMEOUT_V1014' "$APP_DIR/app/stream_info.py" || die "Installed Main YouTube probe timeout fix missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_PROBE_TIMEOUT_V1014' "$APP_DIR/node_agent/app.py" || die "Installed Node YouTube probe timeout fix missing"
grep -Fq 'STREAMFORGE_YOUTUBE_SOURCE_RESOLVER_V1012' "$APP_DIR/app/source_resolver.py" || die "Installed Main YouTube resolver missing"
grep -Fq 'STREAMFORGE_YOUTUBE_SOURCE_RESOLVER_V1012' "$APP_DIR/node_agent/source_resolver.py" || die "Installed Node YouTube resolver missing"
grep -Fq 'STREAMFORGE_YOUTUBE_ANTIBOT_COOKIE_FALLBACK_V1013' "$APP_DIR/app/source_resolver.py" || die "Installed Main YouTube anti-bot/cookies resolver missing"
grep -Fq 'STREAMFORGE_YOUTUBE_ANTIBOT_COOKIE_FALLBACK_V1013' "$APP_DIR/node_agent/source_resolver.py" || die "Installed Node YouTube anti-bot/cookies resolver missing"
grep -Fq 'STREAMFORGE_MAIN_YOUTUBE_COOKIES_UPLOAD_V1013' "$APP_DIR/app/main.py" || die "Installed Main YouTube cookies upload handler missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_COOKIES_UPLOAD_V1013' "$APP_DIR/node_agent/app.py" || die "Installed Node YouTube cookies upload handler missing"
grep -Fq 'STREAMFORGE_MAIN_HIDDEN_HLS_TARGET_FIX_V1013' "$APP_DIR/app/static/panel_nav.js" || die "Installed Main HLS target=_blank fix missing"
grep -Fq 'resolve_stream_source(source_target, force=youtube_source)' "$APP_DIR/app/ffmpeg.py" || die "Installed Main YouTube FFmpeg resolution missing"
grep -Fq 'resolve_stream_source(source_target, force=youtube_source)' "$APP_DIR/node_agent/app.py" || die "Installed Node YouTube FFmpeg resolution missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_AUTO_FILTER_EXTENDED_LIMIT_V85' "$APP_DIR/app/main.py" || die "Installed v8.9 Main Logs default/extended limit backend missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_AUTO_FILTER_EXTENDED_LIMIT_V85' "$APP_DIR/app/templates/logs.html" || die "Installed v8.9 Main Logs auto-filter template missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_AUTO_FILTER_EXTENDED_LIMIT_V85' "$APP_DIR/app/static/app.js" || die "Installed v8.9 Main Logs auto-filter JavaScript missing"
grep -Fq 'STREAMFORGE_NODE_LOG_AUTO_FILTER_EXTENDED_LIMIT_V85' "$APP_DIR/node_agent/app.py" || die "Installed v8.9 Node Logs auto-filter/extended limit missing"
grep -Fq 'STREAMFORGE_CHANNEL_LIST_LIMIT_SELECTOR_V85' "$APP_DIR/app/main.py" || die "Installed v8.9 Channels list-limit backend missing"
grep -Fq 'STREAMFORGE_CHANNEL_LIST_LIMIT_SELECTOR_V85' "$APP_DIR/app/templates/channels.html" || die "Installed v8.9 Channels list-limit selector missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_PAGINATION_V1078' "$APP_DIR/app/static/app.js" || die "Installed v11.22 Channels pagination JavaScript missing"
grep -Fq 'STREAMFORGE_NODE_LOG_LAYOUT_REPAIR_V86' "$APP_DIR/node_agent/app.py" || die "Installed v8.9 Node Logs layout repair missing"
grep -Fq 'STREAMFORGE_NODE_ROOT_LOGIN_REDIRECT_V87' "$APP_DIR/node_agent/app.py" || die "Installed v8.9 Node root login redirect missing"
grep -Fq 'STREAMFORGE_MAIN_ACTIVE_INPUT_SOURCE_V87' "$APP_DIR/app/main.py" || die "Installed v8.9 Main active-input source backend missing"
grep -Fq 'STREAMFORGE_MAIN_ACTIVE_INPUT_SOURCE_V87' "$APP_DIR/app/templates/channels.html" || die "Installed v8.9 Main active-input source template missing"
grep -Fq 'STREAMFORGE_MAIN_ACTIVE_INPUT_SOURCE_V87' "$APP_DIR/app/static/app.js" || die "Installed v8.9 Main active-input source live refresh missing"
grep -Fq 'STREAMFORGE_NODE_MAIN_SHARED_LOG_SOURCE_HIDDEN_V87' "$APP_DIR/node_agent/app.py" || die "Installed v8.9 Main-shared Node Stream Log source hiding missing"
grep -Fq 'STREAMFORGE_NODE_LOCAL_RESTREAMER_V87' "$APP_DIR/node_agent/app.py" || die "Installed v8.9 Node-local Restreamer support missing"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_FILTER_NO_LIMIT_V87' "$APP_DIR/node_agent/app.py" || die "Installed v8.9 Node Channels no-limit filter repair missing"
grep -Fq 'STREAMFORGE_NODE_LOG_RETENTION_V88' "$APP_DIR/node_agent/app.py" || die "Installed v8.9 Node log retention missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_THEME_CROSS_PROCESS_V88' "$APP_DIR/node_agent/app.py" || die "Installed v8.9 Node WebPlayer cross-process theme sync missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_RETENTION_V88' "$APP_DIR/app/templates/system_branding.html" || die "Installed v8.9 Main log retention settings UI missing"
grep -Fq 'LOG_RETENTION_DAYS_KEY = "log_retention_days"' "$APP_DIR/app/main.py" || die "Installed v8.9 Main log retention backend missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSIONS_ON_DEMAND_V89' "$APP_DIR/node_agent/app.py" || die "Installed v8.9 Node live-session endpoint missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_LIVE_SESSIONS_CLIENT_V89' "$APP_DIR/app/node_manager.py" || die "Installed v8.9 Main-to-Node live-session client missing"
grep -Fq 'STREAMFORGE_MAIN_REMOTE_LIVE_SESSIONS_V89' "$APP_DIR/app/main.py" || die "Installed v8.9 Main Remote Node live-session merge missing"
grep -Fq 'STREAMFORGE_MAIN_CACHED_REMOTE_VIEWER_COUNTS_V90' "$APP_DIR/app/main.py" || die "Installed v9.0 Main cached Remote viewer aggregation missing"
grep -Fq 'STREAMFORGE_NODE_DIRECT_SESSION_ONLY_V90' "$APP_DIR/node_agent/app.py" || die "Installed v9.0 Node direct-session-only view missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_HEARTBEAT_V90' "$APP_DIR/node_agent/app.py" || die "Installed v9.0 Node live-session heartbeat backend missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_HEARTBEAT_NGINX_V90' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v9.0 Node live-session heartbeat Nginx path missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_DASHBOARD_SYNC_V90R2' "$APP_DIR/app/main.py" || die "Installed v9.0-r2 Main Live Sessions/Dashboard sync missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_2S_REFRESH_V90R2' "$APP_DIR/app/templates/viewer_sessions.html" || die "Installed v9.0-r2 Main Live Sessions 2s refresh missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_2S_REFRESH_V90R2' "$APP_DIR/node_agent/app.py" || die "Installed v9.0-r2 Node Live Sessions 2s refresh missing"
grep -Fq 'STREAMFORGE_NODE_REDIS_VIEWER_TTL_EXACT_V90R2' "$APP_DIR/node_agent/redis_state.py" || die "Installed v9.0-r2 Node Redis viewer TTL fix missing"
grep -Fq 'STREAMFORGE_NODE_VIEWER_TTL_LOAD_GLOBAL_V90R3' "$APP_DIR/node_agent/app.py" || die "Installed v9.0-r3 Node viewer timeout runtime reload missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_STABLE_TTL_V90R3' "$APP_DIR/node_agent/app.py" || die "Installed v9.0-r3 Node live-session stable TTL fix missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_ROTATION_UI_V90R3' "$APP_DIR/app/templates/system_branding.html" || die "Installed v9.0-r3 Main log rotation UI missing"
grep -Fq '<option value="2">2 sec</option>' "$APP_DIR/app/templates/viewer_sessions.html" || die "Installed v9.0-r3 Main 2-second refresh option missing"
grep -Fq '<option value="2">2 sec</option>' "$APP_DIR/node_agent/app.py" || die "Installed v9.0-r3 Node 2-second refresh option missing"
grep -Fq 'STREAMFORGE_NODE_LAST_ACTIVITY_2S_V91' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v9.2 Node 2-second Last Activity heartbeat cache missing"
grep -Fq 'STREAMFORGE_NODE_LAST_ACTIVITY_2S_V91' "$APP_DIR/node_agent/app.py" || die "Installed v9.2 Node 2-second Last Activity backend hint missing"
grep -Fq 'STREAMFORGE_NODE_V91_ROOT_HEARTBEAT_GUARD' "$APP_DIR/node_agent/app.py" || die "Installed v9.2 Node root Nginx heartbeat update guard missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_ROTATION_INLINE_V91' "$APP_DIR/app/templates/system_branding.html" || die "Installed v9.2 inline Main log rotation setting missing"
grep -Fq 'STREAMFORGE_CHANNEL_STREAM_LOG_CLEAR_PREFIX_V66' "$APP_DIR/app/static/app.js" || die "Installed v6.6 prefixed Stream Logs clear fix missing"
grep -Fq 'STREAMFORGE_LOG_TAB_SCOPED_CLEAR_V66' "$APP_DIR/app/main.py" || die "Installed v6.6 Main tab-scoped log clear missing"
grep -Fq 'STREAMFORGE_NODE_SCOPED_LOG_CLEAR_V66' "$APP_DIR/app/node_manager.py" || die "Installed v6.6 Main-to-Node scoped log clear client missing"
grep -Fq 'STREAMFORGE_NODE_SCOPED_LOG_CLEAR_V66' "$APP_DIR/node_agent/app.py" || die "Installed v6.6 Node scoped log clear missing"
grep -Fq 'STREAMFORGE_NODE_MEDIA_AUTH_CLIENT_IP_V67' "$APP_DIR/node_agent/app.py" || die "Installed v6.8 Node media-auth client-IP preservation missing"
grep -Fq 'STREAMFORGE_NODE_MEDIA_AUTH_PROXY_IP_V67' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v6.8 Node Nginx media-auth proxy-IP fix missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_PLAYLIST_NO_OPEN_FILE_CACHE_V77' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v7.9 Node live-playlist cache bypass missing"
grep -Fq 'STREAMFORGE_NODE_MULTI_URL_SAME_ORIGIN_MEDIA_V68' "$APP_DIR/node_agent/app.py" || die "Installed v6.8 Node multi-URL same-origin media fix missing"
grep -Fq 'STREAMFORGE_NODE_MULTI_URL_MEDIA_CLIENT_IP_V68' "$APP_DIR/node_agent/app.py" || die "Installed v6.8 Node multi-URL client-IP fix missing"
grep -Fq 'STREAMFORGE_NODE_MULTI_URL_PROXY_CLIENT_IP_V68' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v6.8 Node proxy client-IP forwarding fix missing"
grep -Fq 'STREAMFORGE_BRANDED_BROWSER_TITLES_V68' "$APP_DIR/app/templates/base.html" || die "Installed v6.8 branded browser-title fix missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_REQUEST_COMMIT_V69' "$APP_DIR/app/audit_log.py" || die "Installed v7.0 Client log request-session persistence fix missing"
grep -Fq 'STREAMFORGE_REQUEST_DIRTY_COMMIT_V69' "$APP_DIR/app/db.py" || die "Installed v7.0 request dirty-session commit hook missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_WEBPLAYER_V70' "$APP_DIR/node_agent/app.py" || die "Installed v7.0 Node WebPlayer Client log integration missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_QUICK_LOGIN_V70' "$APP_DIR/node_agent/app.py" || die "Installed v7.0 Node quick-login Client log integration missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_PLAYLIST_CONTEXT_V70' "$APP_DIR/node_agent/app.py" || die "Installed v7.0 Node playlist Client log context missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_LOG_AUTO_LOGIN_V71' "$APP_DIR/app/main.py" || die "Installed v7.1 Main Auto Login Client log integration missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_AUTO_LOGIN_V71' "$APP_DIR/node_agent/app.py" || die "Installed v7.1 Node Auto Login Client log integration missing"
grep -Fq 'streamforge_web_player_auto_log_marker' "$APP_DIR/app/main.py" || die "Installed v7.1 Main Auto Login anti-spam session marker missing"
grep -Fq '_NODE_WEB_AUTO_LOG_COOKIE' "$APP_DIR/node_agent/app.py" || die "Installed v7.1 Node Auto Login anti-spam cookie marker missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_DIRECT_DETAILS_IP_V72' "$APP_DIR/app/main.py" || die "Installed v7.9 Main direct log Details/IP integration missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_DIRECT_DETAILS_IP_V72' "$APP_DIR/app/templates/logs.html" || die "Installed v7.9 Main log IP/details columns missing"
grep -Fq 'STREAMFORGE_NODE_LOG_DIRECT_DETAILS_IP_V72' "$APP_DIR/node_agent/app.py" || die "Installed v7.9 Node log IP/details columns missing"
grep -Fq 'STREAMFORGE_ACCESS_LOG_CLIENT_IP_V73' "$APP_DIR/app/main.py" || die "Installed v7.9 Main Access-log client-IP persistence missing"
grep -Fq 'STREAMFORGE_ACCESS_LOG_CLIENT_IP_V73' "$APP_DIR/node_agent/app.py" || die "Installed v7.9 Node Access-log client-IP persistence missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_MOBILE_NODE_LAYOUT_V73' "$APP_DIR/app/templates/logs.html" || die "Installed v7.9 Main mobile Logs marker missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_MOBILE_NODE_LAYOUT_V73' "$APP_DIR/app/static/style.css" || die "Installed v7.9 Main mobile Logs Node-style layout missing"
grep -Fq 'STREAMFORGE_LOG_SUBJECT_MAPPING_V74' "$APP_DIR/app/main.py" || die "Installed v7.9 Channel/Node structured subject mapping missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_SUBJECT_MAPPING_V75' "$APP_DIR/app/main.py" || die "Installed v7.9 Client-log Main Panel subject mapping missing"
grep -Fq 'STREAMFORGE_HLS_REALTIME_RE_COMPAT_V94ROLLBACK' "$APP_DIR/app/input_options.py" || die "Installed v9.4 rollback-safe Main HLS -re pacing missing"
grep -Fq 'STREAMFORGE_MAIN_HLS_REALTIME_READRATE_V76' "$APP_DIR/app/ffmpeg.py" || die "Installed v7.9 Main HLS realtime pacing integration missing"
grep -Fq 'build_input_args(resolved_target, realtime_hls=True, youtube_live=youtube_source)' "$APP_DIR/app/ffmpeg.py" || die "Installed v10.25 Main FFmpeg YouTube-aware HLS pacing missing"
grep -Fq 'STREAMFORGE_NODE_HLS_REALTIME_RE_COMPAT_V94ROLLBACK' "$APP_DIR/node_agent/app.py" || die "Installed v9.4 rollback-safe Node HLS -re pacing missing"
grep -Fq 'cmd += ["-re"]' "$APP_DIR/node_agent/app.py" || die "Installed v9.4 rollback-safe Node FFmpeg -re option missing"
grep -Fq 'STREAMFORGE_NODE_WAITING_TIMER_V51' "$APP_DIR/node_agent/app.py" || die "Installed Node waiting-time tracker missing"
grep -Fq '"waiting_seconds": waiting_seconds' "$APP_DIR/node_agent/app.py" || die "Installed Node waiting-time payload missing"
grep -Fq 'STREAMFORGE_NODE_SETTINGS_HIDE_TOTAL_MAX_V51' "$APP_DIR/node_agent/app.py" || die "Installed Node total connection preservation missing"
! grep -Fq 'name="total_max_connections"' "$APP_DIR/node_agent/app.py" || die "Installed Node Settings still exposes Total max connections"
grep -Fq 'STREAMFORGE_NODE_SETTINGS_TLS_PANEL_V51' "$APP_DIR/node_agent/app.py" || die "Installed Node SSL/DNS-01 panel missing"
grep -Fq 'STREAMFORGE_NODE_SETTINGS_TLS_ACTIONS_V51' "$APP_DIR/node_agent/app.py" || die "Installed Node SSL actions missing"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_VISIBLE_WIDTH_V51' "$APP_DIR/node_agent/app.py" || die "Installed Node Channels width fix missing"
grep -Fq 'STREAMFORGE_NODE_HIDDEN_NATIVE_ROUTE_V50' "$APP_DIR/node_agent/app.py" || die "Installed Node hidden-route full-reload navigation missing"
grep -Fq 'STREAMFORGE_NODE_BROWSER_COOKIE_FIX_V60R1' "$APP_DIR/node_agent/app.py" || die "Installed v6.0-r1 Node browser cookie fix missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_INITIAL_UPTIME_V60R2' "$APP_DIR/app/main.py" || die "Installed v6.0-r2 Main channel initial uptime fix missing"
grep -Fq 'STREAMFORGE_MAIN_LOCAL_HLS_READY_RACE_FALLBACK_V60R2' "$APP_DIR/app/node_manager.py" || die "Installed v6.0-r2 local HLS readiness fallback missing"
grep -Fq 'STREAMFORGE_NODE_TEST_SYNC_BACKGROUND_V60R2' "$APP_DIR/app/main.py" || die "Installed v6.0-r2 background Node Test & Sync fix missing"
grep -Fq 'STREAMFORGE_NODE_STATUS_HEARTBEAT_FIRST_V60R2' "$APP_DIR/app/main.py" || die "Installed v6.0-r2 heartbeat-first Node status fix missing"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_LIVE_SUMMARY_V65R6' "$APP_DIR/app/main.py" || die "Installed v6.5-r6 Main heartbeat live-summary cache missing"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_LIVE_SUMMARY_V65R6' "$APP_DIR/node_agent/app.py" || die "Installed v6.5-r6 Node heartbeat live-summary sender missing"
grep -Fq 'STREAMFORGE_NODE_CARD_TIMEOUT_PRESERVE_HEARTBEAT_V60R2' "$APP_DIR/app/static/app.js" || die "Installed v6.0-r2 Node card timeout preservation fix missing"
grep -Fq 'class RedisStateManager' "$APP_DIR/app/redis_state.py" || die "Installed v6.1 Redis state manager missing"
grep -Fq 'STREAMFORGE_REDIS_CONNECTION_TRACKER_V61' "$APP_DIR/app/main.py" || die "Installed v6.1 Redis connection tracker missing"
grep -Fq 'STREAMFORGE_REDIS_VIEWER_TRACKER_V61' "$APP_DIR/app/viewer_tracking.py" || die "Installed v6.1 Redis viewer tracker missing"
grep -Fq 'STREAMFORGE_REDIS_PLAYBACK_CACHE_V61' "$APP_DIR/app/playback_keys.py" || die "Installed v6.1 Redis playback cache missing"
grep -Fq 'STREAMFORGE_STARTUP_STALE_CHANNEL_RACE_FIX_V61R2' "$APP_DIR/app/ffmpeg.py" || die "Installed v6.1-r2 Main startup stale-channel race fix missing"
grep -Fq 'STREAMFORGE_STARTUP_STALE_CHANNEL_RACE_FIX_V61R2' "$APP_DIR/app/node_manager.py" || die "Installed v6.1-r2 Node-controller startup stale-channel race fix missing"
grep -Fq 'STREAMFORGE_PUBLIC_PROCESS_ISOLATION_V62' "$APP_DIR/app/main.py" || die "Installed v6.2 Public process-isolation guard missing"
grep -Fq 'STREAMFORGE_PUBLIC_SQLITE_POOL_ISOLATION_V62' "$APP_DIR/app/db.py" || die "Installed v6.2 Public SQLite pool isolation missing"
grep -Fq 'STREAMFORGE_PUBLIC_HARDWARE_AWARE_WORKERS_V65R1' "$APP_DIR/scripts/streamforge-public-start" || die "Installed v6.5 hardware-aware Public worker launcher missing"
# STREAMFORGE_V1051_NODE_STAGGERED_AUTOSTART_INSTALLED_GUARD
grep -Fq 'STREAMFORGE_NODE_STAGGERED_CHANNEL_AUTOSTART_V1051' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node staggered channel autostart fix missing"
grep -Fq 'STREAMFORGE_NODE_GLOBAL_AUTO_START_PACER_V1052' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node-wide automatic FFmpeg start pacer missing"
grep -Fq 'self._paced_automatic_start(key, config)' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 automatic recovery pacing call missing"
grep -Fq 'STREAMFORGE_NODE_AUTO_STARTS_PER_SECOND' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node automatic-start rate control missing"
grep -Fq 'key not in self._startup_pending' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node startup watchdog suppression missing"
grep -Fq 'STREAMFORGE_NODE_STATUS_LOCK_MINIMIZE_V1066' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node status lock-minimization fix missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_STATUS_NO_GEO_V1066' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node panel GeoIP hot-path fix missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_SESSION_AUTH_CACHE_V1066' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node panel session auth cache missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_FAILURE_BACKOFF_V1066' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node YouTube resolver backoff missing"
# STREAMFORGE_V1050_MAIN_PUBLIC_NO_REQUEST_RECYCLE_INSTALLED_GUARD
grep -Fq 'STREAMFORGE_MAIN_PLAYLIST_LOGO_ALIAS_BASE_V1056' "$APP_DIR/app/main.py" || die "Installed v10.68 Main playlist logo alias-base fix missing"
grep -Fq 'STREAMFORGE_MAIN_ALIAS_LOGO_CANONICAL_UPSTREAM_V1057' "$APP_DIR/app/main.py" || die "Installed v10.68 Main alias-logo canonical-upstream marker missing"
grep -Fq 'STREAMFORGE_MAIN_ALIAS_LOGO_CANONICAL_UPSTREAM_V1057' "$APP_DIR/deploy/nginx.conf" || die "Installed v10.68 fresh Main alias-logo normalization missing"
grep -Fq 'STREAMFORGE_MAIN_ALIAS_LOGO_CANONICAL_UPSTREAM_V1057' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v10.68 dynamic Main alias-logo normalization missing"
grep -Fq 'STREAMFORGE_MAIN_SHARED_ASSETS_PUBLIC_PLANE_V1055' "$APP_DIR/deploy/nginx.conf" || die "Installed v10.68 fresh Main shared-asset Public-plane route missing"
grep -Fq 'STREAMFORGE_MAIN_SHARED_ASSETS_PUBLIC_PLANE_V1055' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v10.68 dynamic Main shared-asset Public-plane route missing"
grep -Fq 'STREAMFORGE_MAIN_PUBLIC_NO_REQUEST_RECYCLE_V1050' "$APP_DIR/scripts/streamforge-public-start" || die "Installed v10.68 Main Public no-request-recycle fix missing"
! grep -Fq "'--max-requests', '20000'" "$APP_DIR/scripts/streamforge-public-start" || die "Installed v10.68 Main Public still has 20k request recycling"
grep -Fq 'STREAMFORGE_PUBLIC_PLANE_NGINX_SPLIT_V62' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v6.2 Nginx Public split missing"
grep -Fq 'STREAMFORGE_MEDIA_XACCEL_FASTPATH_V63' "$APP_DIR/app/main.py" || die "Installed v6.3 Main media fast path missing"
grep -Fq 'STREAMFORGE_PUBLIC_CATALOG_STOPPED_EXCLUSION_V63R2' "$APP_DIR/app/main.py" || die "Installed v6.3-r2 offline playlist exclusion fix missing"
grep -Fq 'STREAMFORGE_PUBLIC_STRICT_LOCAL_UP_V63R3' "$APP_DIR/app/main.py" || die "Installed v6.3-r3 strict Local process-alive playlist guard missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_CLEAN_CANONICAL_ROOT_V63R4' "$APP_DIR/app/main.py" || die "Installed v6.3-r4 clean WebPlayer canonical root missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_CLEAN_CANONICAL_ROOT_V63R5' "$APP_DIR/node_agent/app.py" || die "Installed v6.3-r5 Node clean WebPlayer canonical root missing"
grep -Fq 'STREAMFORGE_STREAM_INFO_ON_DEMAND_REPLICAS_V63R6' "$APP_DIR/app/main.py" || die "Installed v6.3-r6 on-demand Stream Info replicas missing"
grep -Fq 'STREAMFORGE_STREAM_INFO_ON_DEMAND_REPLICAS_V63R6' "$APP_DIR/app/static/app.js" || die "Installed v6.3-r6 Stream Info live-check UI missing"
grep -Fq 'STREAMFORGE_FIREFOX_SIDEBAR_SCROLL_V63R6' "$APP_DIR/app/static/style.css" || die "Installed v6.3-r6 Firefox sidebar scroll fix missing"
grep -Fq 'STREAMFORGE_REPLICA_NODE_CHANNEL_UPTIME_V63R7' "$APP_DIR/app/main.py" || die "Installed v6.3-r7 Main replica uptime payload missing"
grep -Fq 'STREAMFORGE_REPLICA_NODE_CHANNEL_UPTIME_V63R7' "$APP_DIR/app/static/app.js" || die "Installed v6.3-r7 replica uptime UI missing"
grep -Fq 'STREAMFORGE_REPLICA_NODE_CHANNEL_UPTIME_V63R7' "$APP_DIR/node_agent/app.py" || die "Installed v6.3-r7 Node host uptime status payload missing"
grep -Fq 'data-replica-node-uptime' "$APP_DIR/app/templates/channel_info.html" || die "Installed v6.3-r7 Node uptime card missing"
grep -Fq 'style.css?v={{ app_version }}' "$APP_DIR/app/templates/base.html" || die "Installed versioned stylesheet cache-bust missing"
grep -Fq 'STREAMFORGE_UI_DURATION_DAYS_V63R8' "$APP_DIR/app/static/app.js" || die "Installed v6.3-r8 day-aware uptime formatter missing"
grep -Fq 'STREAMFORGE_TOTAL_OUTPUT_MIBIT_V63R8' "$APP_DIR/app/static/app.js" || die "Installed v6.3-r8 total output formatter missing"
grep -Fq 'STREAMFORGE_FIREFOX_COLLAPSED_NAV_CENTER_V63R8' "$APP_DIR/app/static/style.css" || die "Installed v6.3-r8 Firefox collapsed nav centering missing"
grep -Fq 'STREAMFORGE_FIREFOX_COLLAPSED_TOP_NO_SHRINK_V64R1' "$APP_DIR/app/static/style.css" || die "Installed v6.4-r1 Firefox collapsed sidebar top-block fix missing"
grep -Fq 'app.js?v={{ app_version }}' "$APP_DIR/app/templates/base.html" || die "Installed versioned JavaScript cache-bust missing"
grep -Fq 'STREAMFORGE_LOCAL_HLS_CLEAN_ON_DOWN_V63R3' "$APP_DIR/app/ffmpeg.py" || die "Installed v6.3-r3 Main stale HLS cleanup missing"
grep -Fq 'STREAMFORGE_NODE_HLS_CLEAN_ON_DOWN_V63R3' "$APP_DIR/node_agent/app.py" || die "Installed v6.3-r3 Node stale HLS cleanup missing"
grep -Fq 'STREAMFORGE_NODE_SIGNED_MEDIA_FASTPATH_V63' "$APP_DIR/node_agent/app.py" || die "Installed v6.3 Node media fast path missing"
grep -Fq 'STREAMFORGE_MAIN_HLS_XACCEL_LOCATION_V63' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v6.3 Nginx HLS internal location missing"
grep -Fq 'STREAMFORGE_NODE_HLS_XACCEL_LOCATION_V63' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v6.3 Node TLS media fast path missing"
grep -Fq 'STREAMFORGE_NODE_CONTROL_API_PRECEDENCE_V65R8' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v6.5-r8 Node control API routing precedence missing"
grep -Fq 'STREAMFORGE_NODE_NO_EXTERNAL_8810_V65R8' "$APP_DIR/app/node_manager.py" || die "Installed v6.5-r8 external 8810 suppression missing"
grep -Fq 'def _panel_session_cookie_name()' "$APP_DIR/node_agent/app.py" || die "Installed v6.0-r1 Node-scoped session cookie helper missing"
grep -Fq '_set_panel_route_cookie(response, request, "/panel")' "$APP_DIR/node_agent/app.py" || die "Installed v6.0-r1 login route-cookie reset missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_PREFIXED_LIVE_STATUS_V50' "$APP_DIR/app/templates/node_update.html" || die "Installed Node update prefixed live polling missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_OPTIONS_VISIBLE_V50' "$APP_DIR/app/static/style.css" || die "Installed Node update visible-controls fix missing"
grep -Fq 'STREAMFORGE_DNS01_REGISTRATION_METADATA_PERSIST_V43' "$APP_DIR/scripts/apply_main_access.py" || die "Installed Main DNS-01 registration metadata persistence missing"
grep -Fq 'STREAMFORGE_NODE_DNS01_REGISTRATION_METADATA_PERSIST_V43' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed Node DNS-01 registration metadata persistence missing"
grep -Fq 'STREAMFORGE_DNS01_REGISTRATION_METADATA_UI_V43' "$APP_DIR/app/templates/node_form.html" || die "Installed DNS-01 registration metadata UI missing"
grep -Fq 'STREAMFORGE_UNKNOWN_TLS_SNI_REJECT_V44' "$APP_DIR/scripts/apply_main_access.py" || die "Installed Main unknown TLS SNI rejection missing"
grep -Fq 'ssl_reject_handshake on;' "$APP_DIR/scripts/apply_main_access.py" || die "Installed Main TLS reject directive missing"
grep -Fq 'STREAMFORGE_NODE_UNKNOWN_TLS_SNI_REJECT_V44' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed Node unknown TLS SNI rejection missing"
grep -Fq 'STREAMFORGE_NODE_CARD_STRICT_DELIVERY_COUNTS_V45' "$APP_DIR/app/main.py" || die "Installed Main strict Node-card delivery counts missing"
grep -Fq 'STREAMFORGE_NODE_CARD_STRICT_DELIVERY_COUNTS_V45' "$APP_DIR/node_agent/app.py" || die "Installed Node strict delivery counts missing"
grep -Fq 'STREAMFORGE_NODE_CARD_WAITING_AS_DOWN_V46' "$APP_DIR/app/main.py" || die "Installed v5.4 Waiting-as-Down summary backend missing"
! grep -Fq 'data-node-waiting' "$APP_DIR/app/templates/nodes.html" || die "Installed v5.4 Nodes card still exposes Waiting"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_EMPTY_FILTER_SAFE_V47' "$APP_DIR/app/main.py" || die "Installed v5.4 Live Sessions blank-filter backend fix missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_AUTO_FILTER_V47' "$APP_DIR/app/templates/viewer_sessions.html" || die "Installed v5.4 Live Sessions auto-filter UI missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_AUTO_FILTER_JS_V47' "$APP_DIR/app/templates/viewer_sessions.html" || die "Installed v5.4 Live Sessions auto-filter JavaScript missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_DELIVERY_FILTER_V92' "$APP_DIR/app/templates/viewer_sessions.html" || die "Installed v9.2 Main Live Sessions Delivery filter UI missing"
grep -Fq 'selected_delivery' "$APP_DIR/app/main.py" || die "Installed v9.2 Main Live Sessions Delivery filter backend missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_PAGINATION_SORT_V93' "$APP_DIR/app/main.py" || die "Installed v9.3 Main log pagination/sort backend missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_PAGINATION_SORT_V93' "$APP_DIR/app/templates/logs.html" || die "Installed v9.3 Main log pagination/sort UI missing"
grep -Fq 'STREAMFORGE_NODE_LOG_PAGINATION_SORT_LEVEL_V93' "$APP_DIR/node_agent/app.py" || die "Installed v9.3 Node log pagination/sort/level filter missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_USER_COLUMN_V99R17' "$APP_DIR/app/main.py" || die "Installed v9.9 r17 Main Client log User extraction missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_USER_NO_DETAILS_V99R17' "$APP_DIR/app/templates/logs.html" || die "Installed v9.9 r17 Main Client log User/no-Details UI missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_UNAUTHORIZED_ATTEMPTS_V99R17' "$APP_DIR/app/main.py" || die "Installed v9.9 r17 Main unauthorized Client logging missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_USER_NO_DETAILS_V99R17' "$APP_DIR/node_agent/app.py" || die "Installed v9.9 r17 Node Client log User/no-Details UI missing"
grep -Fq 'STREAMFORGE_DASHBOARD_DISK_USAGE_V96' "$APP_DIR/app/system_metrics.py" || die "Installed v9.6 Main Dashboard disk sampler missing"
grep -Fq 'data-disk-percent' "$APP_DIR/app/templates/dashboard.html" || die "Installed v9.6 Main Dashboard disk card missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_DISK_USAGE_V96' "$APP_DIR/app/static/app.js" || die "Installed v9.6 Main Dashboard disk live refresh missing"
grep -Fq 'STREAMFORGE_NODE_DASHBOARD_DISK_USAGE_V96' "$APP_DIR/node_agent/app.py" || die "Installed v9.6 Node Dashboard disk metrics missing"
grep -Fq 'STREAMFORGE_MAIN_CATALOG_IP_WHITELIST_V97' "$APP_DIR/app/main.py" || die "Installed v9.8 Main catalog IP-whitelist filtering missing"
grep -Fq 'STREAMFORGE_MAIN_PANEL_HIDE_HOVER_RUNTIME_V99R18' "$APP_DIR/app/static/panel_nav.js" || die "Installed v9.9 r18 Main Panel hover runtime setting missing"
grep -Fq 'STREAMFORGE_MAIN_CATALOG_PER_NODE_POLICY_V99R18' "$APP_DIR/app/main.py" || die "Installed v9.9 r18 Main catalogue per-Node policy fix missing"
grep -Fq 'STREAMFORGE_MAIN_LOCAL_ACCESS_POLICY_RUNTIME_V100R3' "$APP_DIR/app/main.py" || die "Installed v10.6 r3 Main Local access-policy cache missing"
grep -Fq 'STREAMFORGE_MAIN_PANEL_PLAYBACK_POLICY_ENFORCEMENT_V100R3' "$APP_DIR/app/main.py" || die "Installed v10.6 r3 Main Panel/Playback access enforcement missing"
grep -Fq 'STREAMFORGE_MAIN_RESTRICTED_PAGE_RESPONSIVE_V101' "$APP_DIR/app/main.py" || die "Installed v10.6 responsive Main restricted page missing"
grep -Fq 'STREAMFORGE_NODE_RESTRICTED_PAGE_RESPONSIVE_V101' "$APP_DIR/node_agent/app.py" || die "Installed v10.6 responsive Node restricted pages missing"
grep -Fq 'STREAMFORGE_NODE_INTERNAL_AUTH_PANEL_POLICY_BYPASS_V102' "$APP_DIR/app/main.py" || die "Installed v10.6 authenticated Node internal Panel-policy bypass missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_DENY_DIAGNOSTIC_LOG_V102' "$APP_DIR/node_agent/app.py" || die "Installed v10.6 Node Panel deny diagnostic logging missing"
grep -Fq 'STREAMFORGE_PANEL_POLICY_ACCESS_LOG_V103' "$APP_DIR/app/main.py" || die "Installed v10.6 Main Panel access-log routing missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_POLICY_DIAGNOSTIC_SAFE_V103' "$APP_DIR/node_agent/app.py" || die "Installed v10.6 Node Panel diagnostic safety missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_UNHANDLED_ERROR_PAGE_V103' "$APP_DIR/node_agent/app.py" || die "Installed v10.6 Node Panel error-page safety missing"
grep -Fq 'STREAMFORGE_NODE_LOG_DEQUE_SNAPSHOT_V104' "$APP_DIR/node_agent/app.py" || die "Installed v10.6 Node log deque snapshot safety missing"
grep -Fq 'STREAMFORGE_NODE_METRICS_PLAYBACK_READY_CHANNELS_V105' "$APP_DIR/node_agent/app.py" || die "Installed v10.6 Node dashboard playable Online-channel metric fix missing"
grep -Fq 'STREAMFORGE_DNS01_OUTER_GAP_NOTE_WRAP_V105' "$APP_DIR/app/templates/node_form.html" || die "Installed v10.6 DNS-01 outer spacing/note-wrap fix missing"
grep -Fq 'STREAMFORGE_CONTEXTUAL_LOG_FILTERS_V106' "$APP_DIR/app/templates/logs.html" || die "Installed v10.6 contextual Main log filters missing"
grep -Fq 'STREAMFORGE_NODE_CONTEXTUAL_LOG_FILTERS_V106' "$APP_DIR/node_agent/app.py" || die "Installed v10.6 contextual Node log filters missing"
grep -Fq 'STREAMFORGE_UPDATE_JSON_LOCAL_WEBPLAYER_THEME_V109' "$APP_DIR/app/main.py" || die "Installed v10.9 Main update.json Web Player theme payload missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_JSON_LOCAL_WEBPLAYER_THEME_V109' "$APP_DIR/node_agent/app.py" || die "Installed v10.9 Node update.json Web Player theme payload missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_LOGO_ROUTE_V1010' "$APP_DIR/app/main.py" || die "Installed v10.25 Main Web Player logo route missing"
grep -Fq 'STREAMFORGE_UPDATE_JSON_SERVER_LOCAL_LOGO_V1010' "$APP_DIR/app/main.py" || die "Installed v10.25 Main update.json server-local logo missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_JSON_SERVER_LOCAL_LOGO_V1010' "$APP_DIR/node_agent/app.py" || die "Installed v10.25 Node update.json server-local logo missing"
grep -Fq 'STREAMFORGE_MAIN_MOBILE_LOGOUT_VISIBLE_V1010' "$APP_DIR/app/static/style.css" || die "Installed v10.25 Main mobile logout footer missing"
grep -Fq 'STREAMFORGE_YOUTUBE_LIVE_UNPACED_RUNTIME_V1016' "$APP_DIR/app/ffmpeg.py" || die "Installed v10.25 Main YouTube unpaced runtime fix missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_LIVE_UNPACED_RUNTIME_V1016' "$APP_DIR/node_agent/app.py" || die "Installed v10.25 Node YouTube unpaced runtime fix missing"
grep -Fq 'STREAMFORGE_YOUTUBE_SMOOTH_PACED_RUNTIME_V1021' "$APP_DIR/app/ffmpeg.py" || die "Installed v10.25 Main YouTube smooth pacing fix missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_SMOOTH_PACED_RUNTIME_V1021' "$APP_DIR/node_agent/app.py" || die "Installed v10.25 Node YouTube smooth pacing fix missing"
grep -Fq 'STREAMFORGE_YOUTUBE_PACED_PLAYER_BUFFER_V1021' "$APP_DIR/app/templates/player.html" || die "Installed v10.25 Main YouTube paced player buffer missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_PACED_PLAYER_BUFFER_V1021' "$APP_DIR/node_agent/app.py" || die "Installed v10.25 Node YouTube paced player buffer missing"
grep -Fq 'STREAMFORGE_YOUTUBE_LIVE_EDGE_V1017' "$APP_DIR/app/input_options.py" || die "Installed v10.25 Main YouTube live-edge input fix missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_LIVE_EDGE_V1017' "$APP_DIR/node_agent/app.py" || die "Installed v10.25 Node YouTube live-edge input fix missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_YOUTUBE_EDGE_V1017' "$APP_DIR/app/templates/player.html" || die "Installed v10.25 Main WebPlayer YouTube live-edge profile missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_YOUTUBE_EDGE_V1017' "$APP_DIR/node_agent/app.py" || die "Installed v10.25 Node WebPlayer YouTube live-edge profile missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_RUNTIME_SOURCE_LOOKUP_V1024' "$APP_DIR/node_agent/app.py" || die "Installed Node WebPlayer runtime-source fix missing"
grep -Fq 'STREAMFORGE_MAIN_METRICS_PLAYBACK_READY_CHANNELS_V1024' "$APP_DIR/app/main.py" || die "Installed Main delivery-ready metrics fix missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_READY_COUNT_V1024' "$APP_DIR/app/main.py" || die "Installed Main Dashboard ready-count fix missing"
grep -Fq 'STREAMFORGE_METRICS_IMMEDIATE_FIRST_SAMPLE_V1024' "$APP_DIR/app/metrics_history.py" || die "Installed immediate metrics first sample missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_EDIT_EFFECTIVE_VALUES_V1026' "$APP_DIR/app/main.py" || die "Installed effective per-node encoding profile backend missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_EDIT_DISPLAY_V1026' "$APP_DIR/app/templates/channel_form.html" || die "Installed channel edit effective profile display missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_EDIT_DISPLAY_V1026' "$APP_DIR/app/static/style.css" || die "Installed effective profile styling missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_NODE_SCOPE_V1027' "$APP_DIR/app/main.py" || die "Installed node-scoped bulk profile backend missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_TARGETED_RESTART_V1027' "$APP_DIR/app/main.py" || die "Installed targeted bulk profile restart missing"
grep -Fq 'STREAMFORGE_TARGETED_CHANNEL_SYNC_V1027' "$APP_DIR/app/node_manager.py" || die "Installed targeted channel sync backend missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_NODE_TARGET_UI_V1027' "$APP_DIR/app/templates/channels.html" || die "Installed bulk profile node-target UI missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_APPLY_RUNNING_V1025' "$APP_DIR/app/main.py" || die "Installed bulk profile runtime-apply backend missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_RUNNING_RESTART_V1025' "$APP_DIR/app/node_manager.py" || die "Installed Main bulk profile restart sync missing"
grep -Fq 'STREAMFORGE_NODE_BULK_PROFILE_RUNNING_RESTART_V1025' "$APP_DIR/node_agent/app.py" || die "Installed Node bulk profile restart endpoint missing"
grep -Fq 'STREAMFORGE_NODE_SSH_YOUTUBE_BOOTSTRAP_PAYLOAD_V1025' "$APP_DIR/app/ssh_installer.py" || die "Installed SSH Node YouTube bootstrap payload fix missing"
grep -Fq 'data-bulk-profile-v1025' "$APP_DIR/app/templates/channels.html" || die "Installed bulk profile UI marker missing"
grep -Fq 'STREAMFORGE_MAIN_YOUTUBE_RUNTIME_INSTALL_STATUS_V1025' "$APP_DIR/scripts/install.sh" || die "Installed fresh Main YouTube runtime status check missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_RUNTIME_INSTALL_STATUS_V1025' "$APP_DIR/scripts/install_node_agent.sh" || die "Installed fresh Node YouTube runtime status check missing"
grep -Fq 'STREAMFORGE_YOUTUBE_KEYFRAME_SAFE_COPY_HLS_V1020' "$APP_DIR/app/ffmpeg.py" || die "Installed v10.25 Main YouTube keyframe-safe HLS fix missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_KEYFRAME_SAFE_COPY_HLS_V1020' "$APP_DIR/node_agent/app.py" || die "Installed v10.25 Node YouTube keyframe-safe HLS fix missing"
grep -Fq 'STREAMFORGE_YOUTUBE_KEYFRAME_SAFE_PLAYER_V1020' "$APP_DIR/app/templates/player.html" || die "Installed v10.25 Main YouTube keyframe-safe player profile missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_KEYFRAME_SAFE_PLAYER_V1020' "$APP_DIR/node_agent/app.py" || die "Installed v10.25 Node YouTube keyframe-safe player profile missing"
grep -Fq 'STREAMFORGE_FULL_PLAYBACK_POLICY_ROUTING_V99R18' "$APP_DIR/app/load_balancer.py" || die "Installed v9.9 r18 full playback policy routing missing"
grep -Fq 'STREAMFORGE_STATIC_STRICT_FALLBACK_V100' "$APP_DIR/app/load_balancer.py" || die "Installed v10.6 Static/Strict fallback routing missing"
grep -Fq 'STREAMFORGE_STATIC_STRICT_READY_FALLBACK_V100' "$APP_DIR/app/load_balancer.py" || die "Installed v10.6 Static/Strict readiness fallback missing"
grep -Fq 'STREAMFORGE_ROUTED_FALLBACK_VALIDATION_V100' "$APP_DIR/app/load_balancer.py" || die "Installed v10.6 routed fallback validation missing"
grep -Fq 'STREAMFORGE_MEDIA_PLAYLIST_ROUTE_FALLBACK_V100' "$APP_DIR/app/main.py" || die "Installed v10.6 media-playlist fallback redirect missing"
grep -Fq 'STREAMFORGE_DIRECT_MEDIA_PLAYLIST_FALLBACK_V100' "$APP_DIR/app/main.py" || die "Installed v10.6 direct media-playlist fallback redirect missing"
grep -Fq 'STREAMFORGE_CATALOG_STATIC_STRICT_FALLBACK_V100' "$APP_DIR/app/main.py" || die "Installed v10.6 catalogue fallback visibility missing"
grep -Fq 'STREAMFORGE_MAIN_LOCAL_PLAYLIST_HLS_AUTHORITY_V100' "$APP_DIR/app/main.py" || die "Installed v10.6 Main Local playlist HLS-authority fix missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_LOCAL_HLS_AUTHORITY_V100' "$APP_DIR/app/main.py" || die "Installed v10.6 Main WebPlayer Local HLS-authority fix missing"
grep -Fq 'STREAMFORGE_CATALOG_IP_WHITELIST_V97' "$APP_DIR/app/load_balancer.py" || die "Installed v9.8 catalog whitelist matcher missing"
grep -Fq 'STREAMFORGE_PLAYBACK_IP_WHITELIST_ELIGIBLE_POOL_V97' "$APP_DIR/app/load_balancer.py" || die "Installed v9.8 load-balanced whitelist routing missing"
grep -Fq 'STREAMFORGE_ACCESS_POLICY_EQUAL_BOXES_V99R2' "$APP_DIR/app/static/style.css" || die "Installed v9.9 r2 equal access-policy box layout missing"
grep -Fq 'STREAMFORGE_DNS01_LAYOUT_SPACING_V99R2' "$APP_DIR/app/templates/node_form.html" || die "Installed v9.9 r2 DNS-01 layout spacing missing"
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_ROOT_IP_WHITELIST_V97R2' "$APP_DIR/node_agent/app.py" || die "Installed v9.8 r2 Node Web Player root whitelist enforcement missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_ACCESS_RUNTIME_RELOAD_V97R2' "$APP_DIR/node_agent/app.py" || die "Installed v9.8 r2 Node public access runtime reload missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_IP_WHITELIST_V98' "$APP_DIR/node_agent/app.py" || die "Installed v9.8 separate Node Panel IP whitelist runtime missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_PLAYBACK_WHITELIST_SPLIT_V98' "$APP_DIR/node_agent/app.py" || die "Installed v9.8 panel/playback whitelist split missing"
grep -Fq 'STREAMFORGE_NODE_GENERIC_NETWORK_BLOCK_PAGE_V98' "$APP_DIR/node_agent/app.py" || die "Installed v9.8 generic restricted Web Player page missing"
grep -Fq 'STREAMFORGE_NODE_CENTERED_NETWORK_BLOCK_PAGE_V98R3' "$APP_DIR/node_agent/app.py" || die "Installed v9.8 r3 centered embedded-logo restricted page missing"
grep -Fq 'panel_ip_whitelist' "$APP_DIR/app/models.py" || die "Installed v9.8 Panel IP whitelist database model missing"
grep -Fq 'STREAMFORGE_V98_PANEL_IP_WHITELIST_MIGRATION' "$APP_DIR/scripts/migrate_v98_panel_ip_whitelist.py" || die "Installed v9.8 Panel IP whitelist migration missing"
grep -Fq 'STREAMFORGE_PANEL_FULL_ACCESS_POLICY_V99' "$APP_DIR/app/models.py" || die "Installed v9.9 Panel full access-policy model missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_FULL_ACCESS_POLICY_V99' "$APP_DIR/node_agent/app.py" || die "Installed v9.9 Node Panel four-rule runtime missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_CLIENT_IP_METHOD_FIX_V99R4' "$APP_DIR/node_agent/app.py" || die "Installed v9.9 r4 Node Panel client-IP method fix missing"
grep -Fq 'ip_text = self._client_ip(request)' "$APP_DIR/node_agent/app.py" || die "Installed v9.9 r4 Node Panel client-IP resolver is stale"
grep -Fq 'Panel IP blacklist' "$APP_DIR/app/templates/node_form.html" || die "Installed v9.9 Main Node Panel access row missing"
grep -Fq 'access-rule-row' "$APP_DIR/node_agent/app.py" || die "Installed v9.9 Node Settings one-line access rows missing"
grep -Fq 'STREAMFORGE_V99_PANEL_ACCESS_POLICY_MIGRATION' "$APP_DIR/scripts/migrate_v99_panel_access_policy.py" || die "Installed v9.9 Panel access migration missing"
grep -Fq 'STREAMFORGE_NODE_DASHBOARD_ACTIVE_NO_FLASH_V94' "$APP_DIR/node_agent/app.py" || die "Installed v9.4 Node Dashboard active-channel flash fix missing"
grep -Fq 'STREAMFORGE_SMB_BACKUP_MODIFIED_TIME_V94' "$APP_DIR/app/backup_manager.py" || die "Installed v9.4 SMB backup modified-time parser missing"
! grep -Fq 'STREAMFORGE_INDEPENDENT_PLAYBACK_SHARE_ROUTING_V95' "$APP_DIR/node_agent/app.py" || die "v9.5 Independent playback sharing code remains after v9.6 stable update"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_HIDDEN_NATIVE_FILTER_V49' "$APP_DIR/app/templates/viewer_sessions.html" || die "Installed v5.4 hidden-route Live Sessions filter reload missing"
! grep -Fq '>Apply filter</button>' "$APP_DIR/app/templates/viewer_sessions.html" || die "Installed v5.4 Main Live Sessions still exposes Apply filter"
grep -Fq 'ssl_reject_handshake on;' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed Node TLS reject directive missing"
grep -Fq 'STREAMFORGE_CANONICAL_PROTOCOL_REDIRECT_V35' "$APP_DIR/app/main.py" || die "Installed Main canonical protocol redirect missing"
grep -Fq 'STREAMFORGE_CHAINED_PROXY_PROTOCOL_V35' "$APP_DIR/scripts/apply_main_access.py" || die "Installed chained-proxy protocol preservation missing"
grep -Fq 'STREAMFORGE_NATIVE_TLS_TERMINATION_V37' "$APP_DIR/scripts/apply_main_access.py" || die "Installed native TLS termination missing"
grep -Fq 'STREAMFORGE_AUTO_LETSENCRYPT_V37' "$APP_DIR/scripts/apply_main_access.py" || die "Installed automatic Lets Encrypt support missing"
grep -Fq 'X-Forwarded-Proto $streamforge_forwarded_proto' "$APP_DIR/scripts/apply_main_access.py" || die "Installed native/chained protocol forwarding missing"
grep -Fq 'STREAMFORGE_MANAGED_ACME_WEBROOT_V37' "$APP_DIR/scripts/apply_main_access.py" || die "Installed ACME webroot routing missing"
grep -Fq 'STREAMFORGE_NODE_CANONICAL_PROTOCOL_REDIRECT_V35' "$APP_DIR/node_agent/app.py" || die "Installed Node canonical protocol redirect missing"
grep -Fq 'STREAMFORGE_IPINFO_COMPATIBLE_LOOKUP_V3059' "$APP_DIR/app/access_control.py" || die "Installed Main IPinfo compatible lookup missing"
grep -Fq 'STREAMFORGE_NODE_IPINFO_COMPATIBLE_LOOKUP_V3059' "$APP_DIR/node_agent/app.py" || die "Installed Node IPinfo compatible lookup missing"
grep -Fq 'result.ipinfo_error' "$APP_DIR/app/templates/node_asn.html" || die "Installed GeoIP lookup error display missing"
grep -Fq 'STREAMFORGE_MAIN_ROOT_PANEL_PREFIX_EXCLUSION_V3059' "$APP_DIR/app/main.py" || die "Installed Main root /panel exclusion missing"
grep -Fq 'STREAMFORGE_NODE_ROOT_PANEL_PREFIX_EXCLUSION_V3059' "$APP_DIR/node_agent/app.py" || die "Installed Node root /panel exclusion missing"
grep -Fq 'STREAMFORGE_NODE_FIXED_PANEL_ADDRESS_BAR_V3059' "$APP_DIR/node_agent/app.py" || die "Installed Node fixed Panel address bar missing"
grep -Fq 'action="/nodes/{{ node.id }}/asn"' "$APP_DIR/app/templates/node_asn.html" || die "Installed GeoIP test explicit route missing"
grep -Fq 'STREAMFORGE_MAIN_IPINFO_IPV4_TRANSPORT_FALLBACK_V3060' "$APP_DIR/app/access_control.py" || die "Installed Main IPinfo IPv4 transport fallback missing"
grep -Fq 'STREAMFORGE_NODE_IPINFO_IPV4_TRANSPORT_FALLBACK_V3060' "$APP_DIR/node_agent/app.py" || die "Installed Node IPinfo IPv4 transport fallback missing"
grep -Fq 'STREAMFORGE_NODE_ZERO_FLASH_PANEL_NAV_V3060' "$APP_DIR/node_agent/app.py" || die "Installed Node zero-flash Panel navigation missing"
grep -Fq 'STREAMFORGE_NODE_RELOAD_SAFE_HIDDEN_ROUTE_V3060R2' "$APP_DIR/node_agent/app.py" || die "Installed Node reload-safe hidden Panel route missing"
grep -Fq 'STREAMFORGE_NODE_FIXED_LOGIN_ADDRESS_BAR_V3059' "$APP_DIR/node_agent/app.py" || die "Installed Node fixed login address bar missing"
grep -Fq 'STREAMFORGE_NODE_ROOT_PANEL_ALIAS_V3058' "$APP_DIR/node_agent/app.py" || die "Installed root Node Panel alias canonicalization missing"
grep -Fq 'def _node_panel_public_redirect_location(' "$APP_DIR/node_agent/app.py" || die "Installed Node Panel public redirect mapper missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOGIN_ROLE_ISOLATION_V3057' "$APP_DIR/node_agent/app.py" || die "Installed Node Panel login role-isolation fix missing"
! sed -n '/^async def node_panel_login(request: Request):/,/^$/p' "$APP_DIR/node_agent/app.py" | grep -F 'manager.webplayer_login_mode' >/dev/null || die "Installed Node Panel login still depends on Web Player mode"
grep -Fq 'STREAMFORGE_WEBPLAYER_QUICK_USER_PRIVACY_V3056' "$APP_DIR/app/main.py" || die "Installed Main Quick Login privacy state missing"
grep -Fq 'not hide_quick_user_info' "$APP_DIR/app/templates/player.html" || die "Installed Main Quick Login player privacy UI missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_QUICK_USER_PRIVACY_V3056' "$APP_DIR/node_agent/app.py" || die "Installed Node Quick Login privacy state missing"
grep -Fq 'STREAMFORGE_RESTORE_ACCESS_LOCKOUT_GUARD_V309' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore access lockout guard missing"
grep -Fq 'STREAMFORGE_RESTORE_ACCESS_WAL_CHECKPOINT_V309' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore access WAL checkpoint missing"
grep -Fq 'STREAMFORGE_RESTORE_ACCESS_NGINX_REAPPLY_V309' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore Main Nginx reapply missing"
grep -Fq 'STREAMFORGE_RESTORE_ACTIVE_URL_PAYLOAD_V3010' "$APP_DIR/app/main.py" || die "Installed restore active browser URL payload missing"
grep -Fq 'STREAMFORGE_RESTORE_ACTIVE_URL_AUTHORITY_V3010' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore active URL authority guard missing"
grep -Fq 'STREAMFORGE_RESTORE_PUBLIC_ROUTE_VERIFY_V3011' "$APP_DIR/scripts/main_system_control.py" || die "Installed post-restore public route verification missing"
grep -Fq 'STREAMFORGE_RESTORE_VERIFIED_FAIL_OPEN_V3011' "$APP_DIR/scripts/main_system_control.py" || die "Installed post-restore automatic fail-open missing"
grep -Fq 'STREAMFORGE_RESTORE_REMOVE_SOURCE_DOMAIN_V3012' "$APP_DIR/scripts/main_system_control.py" || die "Installed restored source-domain removal missing"
grep -Fq 'STREAMFORGE_RESTORE_ENV_REMOVE_SOURCE_DOMAIN_V3012' "$APP_DIR/scripts/main_system_control.py" || die "Installed restored environment source-domain removal missing"
grep -Fq 'STREAMFORGE_MAIN_ENV_RECOVERY_ALIAS_V3013' "$APP_DIR/app/main.py" || die "Installed environment-backed Main recovery alias missing"
grep -Fq 'STREAMFORGE_BACKUP_COMPLETE_LOGO_ASSETS_V3017' "$APP_DIR/app/backup_manager.py" || die "Installed complete backup logo asset bundle missing"
grep -Fq 'assets_complete=1' "$APP_DIR/app/backup_manager.py" || die "Installed backup logo completeness manifest missing"
grep -Fq 'STREAMFORGE_BACKUP_STALE_LOGO_TOLERANCE_V3018' "$APP_DIR/app/backup_manager.py" || die "Installed stale logo reference tolerance missing"
grep -Fq 'STREAMFORGE_LOGO_ASSET_FILETYPE_FILTER_V3023' "$APP_DIR/app/backup_manager.py" || die "Installed logo backup file-type filter missing"
grep -Fq 'streamforge-assets/ASSET-MANIFEST.json' "$APP_DIR/app/backup_manager.py" || die "Installed backup logo hash inventory missing"
grep -Fq 'STREAMFORGE_RESTORE_COMPLETE_LOGO_ASSETS_V3017' "$APP_DIR/scripts/main_system_control.py" || die "Installed complete logo restore core missing"
grep -Fq 'STREAMFORGE_RESTORE_ASSET_INVENTORY_V3018' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore logo inventory validation missing"
grep -Fq 'STREAMFORGE_RESTORE_ASSET_MEMBER_POLICY_V3019' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore logo archive-member policy missing"
grep -Fq 'STREAMFORGE_RESTORE_LOGO_REFERENCE_REBASE_V3021' "$APP_DIR/scripts/main_system_control.py" || die "Installed restored logo-reference rebase missing"
grep -Fq 'STREAMFORGE_CANONICAL_LOGO_STORAGE_V3022' "$APP_DIR/scripts/main_system_control.py" || die "Installed canonical Main logo storage migration missing"
grep -Fq '_restore_verified_logo_payload(' "$APP_DIR/scripts/main_system_control.py" || die "Installed shared verified restored-logo call missing"
grep -Fq 'STREAMFORGE_SHARED_RESTORE_ARCHIVE_POLICY_V3020' "$APP_DIR/app/main.py" || die "Installed shared web restore archive policy missing"
[[ "$(grep -Fc '_validate_main_restore_archive(staged)' "$APP_DIR/app/main.py")" -eq 3 ]] || die "Installed Main restore routes do not all use the shared archive policy"
grep -Fq 'Restored logo checksum verification failed' "$APP_DIR/scripts/main_system_control.py" || die "Installed restored-logo checksum verification missing"
grep -Fq 'RESTORE_LOGO_SYNC_MARKER' "$APP_DIR/scripts/main_system_control.py" || die "Installed post-restore logo sync marker missing"
grep -Fq 'STREAMFORGE_RESTORE_REMOTE_LOGO_RESYNC_V3017' "$APP_DIR/app/main.py" || die "Installed Remote Node restored-logo resync missing"
grep -Fq 'STREAMFORGE_MAIN_UNKNOWN_PATH_SILENT_DROP_V3014' "$APP_DIR/scripts/apply_main_access.py" || die "Installed unknown Main path silent-drop generator missing"
grep -Fq 'error_page 404 418 = @streamforge_silent_drop;' "$APP_DIR/scripts/apply_main_access.py" || die "Installed dynamic Main upstream 404 interception missing"
grep -Fq 'STREAMFORGE_FRESH_UNKNOWN_PATH_SILENT_DROP_V3014' "$APP_DIR/deploy/nginx.conf" || die "Installed fresh unknown Main path silent-drop config missing"
grep -Fq 'error_page 404 418 = @streamforge_silent_drop;' "$APP_DIR/deploy/nginx.conf" || die "Installed fresh upstream 404 interception missing"
grep -Fq 'def configured_main_recovery_aliases(' "$APP_DIR/app/main.py" || die "Installed Main recovery alias helper missing"
grep -Fq 'STREAMFORGE_RESTORE_PRESERVE_ALL_ACCESS_URLS_V3039' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore complete Panel/Playlist URL preservation missing"
grep -Fq 'STREAMFORGE_RESTORE_FAIL_OPEN_KEEP_ACCESS_URLS_V3039' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore fail-open URL preservation missing"
grep -Fq 'def _restore_recovery_access_url(' "$APP_DIR/app/main.py" || die "Installed restore active URL capture helper missing"
grep -Fq 'payload["recovery_url"]' "$APP_DIR/app/main.py" || die "Installed restore recovery URL request field missing"
grep -Fq 'def _validated_recovery_url(' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore recovery URL validator missing"
grep -Fq 'def _access_snapshot_with_recovery_url(' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore recovery alias merger missing"
[[ "$(grep -Fc '_restore_recovery_access_url(request)' "$APP_DIR/app/main.py")" -eq 3 ]] || die "Installed restore routes do not all capture active browser URL"
grep -Fq 'main-access-snapshot.json' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore access safety snapshot missing"
grep -Fq 'safe_fallback = "http://127.0.0.1"' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore no-snapshot recovery fallback missing"
grep -Fq 'STREAMFORGE_USER_ALLOWED_NODE_MOBILE_V302' "$APP_DIR/app/static/style.css" || die "Installed Users allowed-node mobile layout fix missing"
grep -Fq 'class="user-node-filter-field"' "$APP_DIR/app/templates/users.html" || die "Installed Users allowed-node structured filter field missing"
grep -Fq 'class="button user-node-filter-clear"' "$APP_DIR/app/templates/users.html" || die "Installed Users allowed-node Clear control missing"
grep -Fq 'name="schedule_time" value="00:00"' "$APP_DIR/app/templates/backups.html" || die "Installed per-destination default backup time missing"
grep -Fq 'data-schedule-time=' "$APP_DIR/app/templates/backups.html" || die "Installed per-destination backup time edit state missing"
grep -Fq 'def _backup_schedule_due(' "$APP_DIR/app/backup_manager.py" || die "Installed clock-anchored backup scheduler missing"
grep -Fq '"schedule_time": _normalize_schedule_time(schedule_time)' "$APP_DIR/app/backup_manager.py" || die "Installed per-destination backup time persistence missing"
! grep -Fq '<p>Remote Node Panel</p>' "$APP_DIR/node_agent/app.py" || die "Installed Node login still shows the old panel subtitle"
! grep -Fq '· Remote Node Panel</title>' "$APP_DIR/node_agent/app.py" || die "Installed Node login still has the old title suffix"
grep -Fq 'STREAMFORGE_MAIN_NODE_NAME_SYNC_V304' "$APP_DIR/app/node_manager.py" || die "Installed Main Node-name access sync missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_NAME_SYNC_V304' "$APP_DIR/node_agent/app.py" || die "Installed Node-name persistence missing"
grep -Fq 'apply_main_node_name(data.get("node_name"))' "$APP_DIR/node_agent/app.py" || die "Installed heartbeat Node-name repair missing"
grep -Fq 'self.apply_main_node_name(decoded.get("node_name"))' "$APP_DIR/node_agent/app.py" || die "Installed live-auth Node-name repair missing"
grep -Fq '"node_name": node.name' "$APP_DIR/app/main.py" || die "Installed Main heartbeat/live-auth Node name missing"
grep -Fq 'STREAMFORGE_NODE_DISCONNECTED_CENTERED_V305' "$APP_DIR/node_agent/app.py" || die "Installed centered disconnected Node card missing"
grep -Fq '<title>Main Server Disconnected</title>' "$APP_DIR/node_agent/app.py" || die "Installed disconnected-page browser title is stale"
grep -Fq 'min-height:100dvh' "$APP_DIR/node_agent/app.py" || die "Installed disconnected card dynamic-viewport centering missing"
! grep -Fq '<div class="brand"><span class="brand-mark"' "$APP_DIR/node_agent/app.py" || die "Installed disconnected-page logo and Node-name header remains"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_LIVE_WORKFLOW_V306' "$APP_DIR/app/main.py" || die "Installed Auto Install live workflow backend missing"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_LIVE_WORKFLOW_UI_V306' "$APP_DIR/app/templates/node_install.html" || die "Installed Auto Install live workflow UI missing"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_PREFIX_AWARE_LIVE_WORKFLOW_V65R12' "$APP_DIR/app/templates/node_install.html" || die "Installed Auto Install prefix-aware live workflow missing"
grep -Fq "const panelRoot = String(window.STREAMFORGE_APP_ROOT || '')" "$APP_DIR/app/templates/node_install.html" || die "Installed Auto Install panel-prefix root resolver missing"
grep -Fq 'action="{{ app_root }}/nodes/install"' "$APP_DIR/app/templates/node_install.html" || die "Installed Auto Install prefix-aware form action missing"
grep -Fq '@app.get("/nodes/install/status/{job_id}"' "$APP_DIR/app/main.py" || die "Installed Auto Install status endpoint missing"
grep -Fq 'progress=lambda phase, message, percent, detail: _node_install_progress' "$APP_DIR/app/main.py" || die "Installed Auto Install real SSH progress callback missing"
grep -Fq 'data-node-install-status' "$APP_DIR/app/templates/node_install.html" || die "Installed Auto Install progress panel missing"
grep -Fq '/nodes/install/status/${encodeURIComponent(jobInput.value)}' "$APP_DIR/app/templates/node_install.html" || die "Installed Auto Install status polling missing"
grep -Fq 'STREAMFORGE_AUTO_INSTALL_WORKFLOW_GAP_V307' "$APP_DIR/app/templates/node_install.html" || die "Installed Auto Install workflow spacing missing"
grep -Fq '[data-node-install-status]{margin:0 0 16px!important}' "$APP_DIR/app/templates/node_install.html" || die "Installed Auto Install desktop workflow gap missing"
grep -Fq '[data-node-install-status]{margin-bottom:13px!important}' "$APP_DIR/app/templates/node_install.html" || die "Installed Auto Install mobile workflow gap missing"
grep -Fq 'STREAMFORGE_FRESH_INSTALL_GEOIP_SELF_COPY_V308' "$APP_DIR/scripts/install.sh" || die "Installed fresh-install GeoIP self-copy fix missing"
grep -Fq 'STREAMFORGE_AUTO_UPDATE_COMMAND_INSTALL_V3011' "$APP_DIR/scripts/install.sh" || die "Installed automatic update command setup missing"
[[ -x /usr/local/bin/streamforge-update ]] || die "System streamforge-update command was not installed"
grep -Fq 'sudo streamforge-update [--force] [PACKAGE.zip]' /usr/local/bin/streamforge-update || die "System streamforge-update command is stale"
grep -Fq 'STREAMFORGE_RESETUSER_COMMAND_INSTALL_V3015' "$APP_DIR/scripts/install.sh" || die "Installed fresh resetuser command setup missing"
grep -Fq 'STREAMFORGE_RESETUSER_COMMAND_V3015' "$APP_DIR/scripts/streamforge" || die "Installed resetuser command payload missing"
grep -Fq 'STREAMFORGE_RESETDOMAIN_COMMAND_V3016' "$APP_DIR/scripts/reset_domain.py" || die "Installed resetdomain helper missing"
grep -Fq 'resetdomain)' "$APP_DIR/scripts/streamforge" || die "Installed resetdomain command option missing"
grep -Fq 'STREAMFORGE_LOGO_ONLY_RESTORE_V3023' "$APP_DIR/scripts/restore_logos.py" || die "Installed logo-only restore helper missing"
grep -Fq 'restorelogos)' "$APP_DIR/scripts/streamforge" || die "Installed restorelogos command option missing"
grep -Fq '"sqlite:///$DB_PATH"' "$LIVE_UPDATER" || die "Installed access-sync SQLite DB_PATH conversion missing"
grep -Fq 'STREAMFORGE_UPDATE_ACCESS_HELPER_PERMISSION_V3025' "$LIVE_UPDATER" || die "Installed access helper permission repair missing"
grep -Fq 'STREAMFORGE_UPDATE_PRESERVE_ACCESS_PATHS_V3026' "$APP_DIR/scripts/reset_domain.py" || die "Installed access path preservation helper missing"
grep -Fq 'STREAMFORGE_UPDATE_DATABASE_AUTHORITY_FIRST_V3054' "$APP_DIR/scripts/reset_domain.py" || die "Installed database-authoritative update access selector missing"
grep -Fq 'STREAMFORGE_UPDATE_DATABASE_AUTHORITY_FIRST_V3054' "$LIVE_UPDATER" || die "Installed database-authoritative updater policy missing"
! sed -n '/^[[:space:]]*# STREAMFORGE_UPDATE_DATABASE_AUTHORITY_FIRST_V3054:/,/^[[:space:]]*# STREAMFORGE_UPDATE_RECONCILE_CURRENT_LOGOS_V3021:/p' "$LIVE_UPDATER" | grep -F 'database-authority "$CURRENT_PUBLIC_URL"' >/dev/null || die "Installed updater still replaces database URLs from environment"
grep -Fq 'STREAMFORGE_WEBPLAYER_PERSISTENT_AUTO_RECONNECT_V3055' "$APP_DIR/app/templates/player.html" || die "Installed Main Web Player persistent reconnect loop missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_PERSISTENT_AUTO_RECONNECT_V3055' "$APP_DIR/node_agent/app.py" || die "Installed Node Web Player persistent reconnect loop missing"
grep -Fq 'STREAMFORGE_CHANNEL_STRICT_HLS_UP_WAITING_V3055' "$APP_DIR/app/main.py" || die "Installed Main strict HLS delivery-state backend missing"
grep -Fq 'freshness_window = max(20, segment_time * 8)' "$APP_DIR/app/node_manager.py" || die "Installed Main local HLS freshness gate missing"
grep -Fq 'bool(ready and fresh_hls and alive)' "$APP_DIR/node_agent/app.py" || die "Installed Node HLS freshness gate missing"
grep -Fq 'STREAMFORGE_CHANNEL_STRICT_HLS_UP_WAITING_V3055' "$APP_DIR/app/static/app.js" || die "Installed Main exact delivery-state filter missing"
grep -Fq 'const statusMatched = !state.status || runtimeStatus === state.status;' "$APP_DIR/app/static/app.js" || die "Installed Main paginated exact delivery-state filter logic missing"
grep -Fq '<option value="waiting"' "$APP_DIR/app/templates/channels.html" || die "Installed Main Waiting status filter missing"
grep -Fq '<option value="waiting">Waiting</option>' "$APP_DIR/node_agent/app.py" || die "Installed Node Waiting status filter missing"
! grep -Fq 'runtime_bitrate > 0 or runtime_uptime >= 2' "$APP_DIR/app/main.py" || die "Installed Main HLS Up state still trusts bitrate or uptime"
grep -Fq 'STREAMFORGE_RESTORE_PRESERVE_SEPARATE_ACCESS_PATHS_V3026' "$APP_DIR/scripts/main_system_control.py" || die "Installed full restore path preservation missing"
grep -Fq 'STREAMFORGE_SHARED_VERIFIED_LOGO_RESTORE_V3026' "$APP_DIR/scripts/main_system_control.py" || die "Installed shared verified logo restore pipeline missing"
grep -Fq 'STREAMFORGE_RESTORED_ASSET_CACHE_BUST_V3028' "$APP_DIR/app/main.py" || die "Installed restored logo cache-bust helper missing"
grep -Fq 'RestoredAssetStaticFiles' "$APP_DIR/app/main.py" || die "Installed restored logo no-cache static handler missing"
grep -Fq 'channel.logo_url|versioned_asset' "$APP_DIR/app/templates/channels.html" || die "Installed Channels versioned logo URL missing"
grep -Fq 'branding.logo_url|versioned_asset' "$APP_DIR/app/templates/base.html" || die "Installed Main branding versioned logo URL missing"
grep -Fq 'STREAMFORGE_UPDATE_LOGO_SAFETY_SNAPSHOT_V3029' "$APP_DIR/scripts/preserve_update_logos.py" || die "Installed update logo safety helper missing"
grep -Fq 'STREAMFORGE_EMPTY_LOGO_PAYLOAD_PRESERVE_V3029' "$APP_DIR/scripts/main_system_control.py" || die "Installed empty logo-payload preservation missing"
grep -Fq 'STREAMFORGE_RESTORE_ENV_SANDBOX_WRITE_V3030' "$APP_DIR/scripts/main_system_control.py" || die "Installed sandbox-safe environment restore missing"
grep -Fq 'if destination == ENV_FILE:' "$APP_DIR/scripts/main_system_control.py" || die "Installed restore helper still requires a forbidden /etc sibling temporary file"
grep -Fq 'STREAMFORGE_MAIN_RECOVERY_ALIAS_NO_ROLE_WIDEN_V3031' "$APP_DIR/app/main.py" || die "Installed Main recovery alias still widens saved URL roles"
grep -Fq 'STREAMFORGE_MAIN_AUTOMATIC_ACCESS_POLICY_UI_V3031' "$APP_DIR/app/templates/node_form.html" || die "Installed automatic Main access policy UI missing"
grep -Fq 'STREAMFORGE_MAIN_AUTOMATIC_RELAY_ACCESS_V3031' "$APP_DIR/app/main.py" || die "Installed automatic Main relay authority missing"
grep -Fq 'STREAMFORGE_RESTORE_EXACT_ROLE_POLICY_V3031' "$APP_DIR/scripts/main_system_control.py" || die "Installed full restore exact Main role policy missing"
grep -Fq 'STREAMFORGE_MAIN_EXACT_ALIAS_NO_REDIRECT_V3036' "$APP_DIR/app/main.py" || die "Installed exact Main alias no-redirect policy missing"
grep -Fq 'STREAMFORGE_UPDATE_ROLE_URL_DECONTAMINATION_V3036' "$APP_DIR/scripts/reset_domain.py" || die "Installed Panel/Playlist URL role decontamination missing"
grep -Fq 'STREAMFORGE_UPDATE_PRESERVE_ALL_ACCESS_URLS_V3038' "$APP_DIR/scripts/reset_domain.py" || die "Installed update multi-URL preservation missing"
grep -Fq 'STREAMFORGE_PLAYBACK_CHILD_ALIAS_PREFIX_V3036' "$APP_DIR/app/main.py" || die "Installed Playlist child alias-prefix propagation missing"
grep -Fq 'STREAMFORGE_MAIN_REQUEST_MATCHED_PLAYLIST_BASE_V3037' "$APP_DIR/app/main.py" || die "Installed request-matched Main playlist URL generation missing"
grep -Fq 'STREAMFORGE_NODE_LONGEST_PLAYLIST_ALIAS_V3040' "$APP_DIR/node_agent/app.py" || die "Installed Node longest Playlist/App path-alias matching missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_QUICK_OR_MANUAL_LOGIN_V3041' "$APP_DIR/app/main.py" || die "Installed Main Quick User plus Manual Login backend missing"
grep -Fq 'value="quick"' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed Web Player Quick User login mode UI missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_QUICK_MODE_SYNC_V3041' "$APP_DIR/app/node_manager.py" || die "Installed Remote Node Quick User login mode sync missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_QUICK_OR_MANUAL_LOGIN_V3041' "$APP_DIR/node_agent/app.py" || die "Installed Node Quick User plus Manual Login support missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_QUICK_LOGIN_LAYOUT_V3042' "$APP_DIR/app/templates/web_player.html" || die "Installed Main Quick Login option ordering/display-name layout missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_SELECTOR_HIDDEN_V3042' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed Manual-mode selected-user visibility fix missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_QUICK_LOGIN_LAYOUT_V3042' "$APP_DIR/node_agent/app.py" || die "Installed Node Quick Login option ordering/display-name layout missing"
grep -Fq 'STREAMFORGE_OFFLINE_NODE_LOCAL_DELETE_V3043' "$APP_DIR/app/main.py" || die "Installed Offline Node local-delete fallback missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_QUICK_CREDENTIAL_STATE_V3043' "$APP_DIR/app/templates/web_player.html" || die "Installed Main Quick Login credential/back state missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_QUICK_CREDENTIAL_STATE_V3043' "$APP_DIR/node_agent/app.py" || die "Installed Node Quick Login credential/back state missing"
grep -Fq 'STREAMFORGE_OFFLINE_NODE_DELETE_PREFLIGHT_V3043' "$LIVE_UPDATER" || die "Installed Offline Node delete preflight correction missing"
grep -Fq 'STREAMFORGE_CHANNELS_CACHED_NODE_HEALTH_V3044' "$APP_DIR/app/node_manager.py" || die "Installed Channels cached-only Node health support missing"
grep -Fq 'STREAMFORGE_OFFLINE_NODE_STATUS_BACKOFF_V3044' "$APP_DIR/app/node_manager.py" || die "Installed offline Node status backoff missing"
grep -Fq 'STREAMFORGE_CHANNELS_NO_BLOCKING_VIEWER_FETCH_V3044' "$APP_DIR/app/main.py" || die "Installed non-blocking Channels viewer aggregation missing"
grep -Fq 'STREAMFORGE_CHANNELS_FAST_OFFLINE_RENDER_V3044' "$APP_DIR/app/main.py" || die "Installed fast offline Channels initial render missing"
grep -Fq 'STREAMFORGE_OFFLINE_CHANNEL_SETTINGS_QUEUE_V3045' "$APP_DIR/app/node_manager.py" || die "Installed offline channel-settings queue missing"
grep -Fq 'STREAMFORGE_NODE_RECONNECT_AUTO_SYNC_V3045' "$APP_DIR/app/main.py" || die "Installed Node reconnect auto-sync missing"
grep -Fq 'STREAMFORGE_OFFLINE_CHANNEL_EDIT_SAVE_V3045' "$APP_DIR/app/main.py" || die "Installed offline channel edit save path missing"
grep -Fq 'STREAMFORGE_OFFLINE_BULK_PROFILE_QUEUE_V3045' "$APP_DIR/app/main.py" || die "Installed offline bulk profile queue missing"
grep -Fq 'Test &amp; Sync' "$APP_DIR/app/templates/nodes.html" || die "Installed Node Test and Sync control missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_DOWNLOAD_MANAGE_UI_V3045' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed Web Player download management UI missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_AUTHENTICATED_DOWNLOAD_V3045' "$APP_DIR/app/main.py" || die "Installed Main Web Player authenticated download missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_DOWNLOAD_NODE_SYNC_V3045' "$APP_DIR/app/node_manager.py" || die "Installed managed download Node sync missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_SYNC_V3045' "$APP_DIR/node_agent/app.py" || die "Installed Node managed download receiver missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_AUTHENTICATED_DOWNLOAD_V3045' "$APP_DIR/node_agent/app.py" || die "Installed Node authenticated Web Player download missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_INFO_DOWNLOAD_V3046' "$APP_DIR/app/templates/player.html" || die "Installed Main watch-page Download info-row layout missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_INFO_DOWNLOAD_V3046' "$APP_DIR/node_agent/app.py" || die "Installed Node watch-page Download info-row layout missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_500_GUARD_V3046' "$APP_DIR/app/main.py" || die "Installed bulk encoding-profile error boundary missing"
grep -Fq 'STREAMFORGE_MAIN_INTERACTIVE_POINTER_CURSOR_V3047' "$APP_DIR/app/static/style.css" || die "Installed Main Panel interactive pointer cursor policy missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_INTERACTIVE_POINTER_V3047' "$APP_DIR/app/templates/web_player.html" || die "Installed Main Web Player interactive pointer cursor policy missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_INTERACTIVE_POINTER_V3047' "$APP_DIR/node_agent/app.py" || die "Installed Node Panel interactive pointer cursor policy missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_INTERACTIVE_POINTER_V3047' "$APP_DIR/node_agent/app.py" || die "Installed Node Web Player interactive pointer cursor policy missing"
grep -Fq 'STREAMFORGE_NODE_WATCH_INTERACTIVE_POINTER_V3047' "$APP_DIR/node_agent/app.py" || die "Installed Node watch-page interactive pointer cursor policy missing"
grep -Fq 'STREAMFORGE_NEW_NODE_FAVICON_STATE_RESET_V3048' "$APP_DIR/app/main.py" || die "Installed new Node favicon state reset missing"
grep -Fq 'STREAMFORGE_NODE_DELETE_FAVICON_STATE_CLEANUP_V3048' "$APP_DIR/app/main.py" || die "Installed deleted Node favicon state cleanup missing"
grep -Fq 'STREAMFORGE_STALE_NODE_FAVICON_SELF_HEAL_V3048' "$APP_DIR/app/node_manager.py" || die "Installed stale Node favicon self-heal missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_500_GUARD_V3048' "$APP_DIR/app/main.py" || die "Installed bulk Node assignment error boundary missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_MOBILE_INFO_LAYOUT_V3049' "$APP_DIR/app/templates/player.html" || die "Installed Main Web Player mobile information layout missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_MOBILE_INFO_LAYOUT_V3049' "$APP_DIR/node_agent/app.py" || die "Installed Node Web Player mobile information layout missing"
grep -Fq 'STREAMFORGE_OFFLINE_CHANNEL_CONTROL_FAST_QUEUE_V3050' "$APP_DIR/app/node_manager.py" || die "Installed offline channel-control fast queue missing"
grep -Fq 'STREAMFORGE_OFFLINE_BULK_ACTION_FAST_QUEUE_V3050' "$APP_DIR/app/node_manager.py" || die "Installed offline bulk removal fast queue missing"
grep -Fq 'STREAMFORGE_OFFLINE_RUNTIME_SNAPSHOT_SHORT_CIRCUIT_V3050' "$APP_DIR/app/node_manager.py" || die "Installed offline runtime snapshot short-circuit missing"
grep -Fq 'STREAMFORGE_BULK_ACTION_DB_ONLY_LIVE_STATE_V3050' "$APP_DIR/app/main.py" || die "Installed bulk database-only live-state policy missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_MOBILE_HEADER_LAYOUT_V3051' "$APP_DIR/app/templates/web_player.html" || die "Installed Main signed-in Web Player mobile header layout missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_MOBILE_HEADER_LAYOUT_V3051' "$APP_DIR/node_agent/app.py" || die "Installed Node signed-in Web Player mobile header layout missing"
grep -Fq 'STREAMFORGE_DASHBOARD_FAST_RENDER_V3052' "$APP_DIR/app/main.py" || die "Installed fast Dashboard render policy missing"
grep -Fq 'STREAMFORGE_GOOGLE_BACKUP_CARD_LAYOUT_V3053' "$APP_DIR/app/templates/backups.html" || die "Installed Google Drive backup card layout missing"
grep -Fq 'STREAMFORGE_UPDATE_QUIET_AUTHORITY_SYNC_V3037' "$LIVE_UPDATER" || die "Installed quiet updater authority synchronization missing"
grep -Fq '"main-unmatched-link-silent-drop"' "$APP_DIR/app/main.py" || die "Installed unmatched Main link silent-drop response missing"
grep -Fq '"dns_only": 1' "$APP_DIR/scripts/reset_domain.py" || die "Installed resetdomain exact Panel role enforcement missing"
grep -Fq '"playlist_dns_only": 1' "$APP_DIR/scripts/reset_domain.py" || die "Installed resetdomain exact Playlist role enforcement missing"
! grep -Fq 'Local relay hostname/IP' "$APP_DIR/app/templates/node_form.html" || die "Installed removed Local relay hostname/IP option remains"
! grep -Fq 'Relay scheme' "$APP_DIR/app/templates/node_form.html" || die "Installed removed Relay scheme option remains"
! grep -Fq 'Allow only configured Main access hostnames' "$APP_DIR/app/templates/node_form.html" || die "Installed removed Main hostname-lock option remains"
[[ -x /usr/local/bin/streamforge ]] || die "System streamforge command was not installed"
grep -Fq 'STREAMFORGE_RESETUSER_COMMAND_V3015' /usr/local/bin/streamforge || die "System streamforge resetuser command is stale"
grep -Fq 'resetdomain)' /usr/local/bin/streamforge || die "System streamforge resetdomain command is stale"
grep -Fq 'restorelogos)' /usr/local/bin/streamforge || die "System streamforge restorelogos command is stale"
grep -Fq 'STREAMFORGE_RESETPANELACCESS_COMMAND_V107' /usr/local/bin/streamforge || die "System streamforge resetpanelaccess command is stale"
grep -Fq 'STREAMFORGE_NODE_TARGET_PYTHON_COMPILE_V108' "$APP_DIR/scripts/install_node_agent.sh" || die "Installed Remote target-Python syntax preflight missing"
grep -Fq 'resetpanelaccess)' /usr/local/bin/streamforge || die "System streamforge resetpanelaccess option is stale"
grep -Fq 'chmod 0755 "$APP_DIR/scripts/update_geoip_databases.sh"' "$APP_DIR/scripts/install.sh" || die "Installed fresh-install GeoIP updater permission fix missing"
! grep -Fq 'install -m 0755 "$APP_DIR/scripts/update_geoip_databases.sh" /opt/streamforge/scripts/update_geoip_databases.sh' "$APP_DIR/scripts/install.sh" || die "Installed fresh installer still copies the GeoIP updater onto itself"
grep -Fq 'ExecStart=/usr/bin/gunicorn app.main:app' "$APP_DIR/deploy/streamforge.service" || die "Direct Main Gunicorn service definition missing"
grep -Fq -- '--workers 1 --worker-class uvicorn_worker.UvicornWorker' "$APP_DIR/deploy/streamforge.service" || die "Main single ASGI worker configuration missing"
! grep -Eq 'venv/bin|python3 -m uvicorn|/usr/bin/env python3' "$APP_DIR/deploy/streamforge.service" || die "Packaged Main service still uses a venv/direct-Uvicorn runtime"
grep -Eq '^gunicorn>=23,<27$' "$APP_DIR/requirements.txt" || die "Main Gunicorn dependency missing"
grep -Eq '^uvicorn-worker>=0.4,<1.0$' "$APP_DIR/requirements.txt" || die "Main Uvicorn worker dependency missing"
grep -Eq '^cryptography>=43,<47$' "$APP_DIR/requirements.txt" || die "Main AES-GCM cryptography dependency missing"
grep -Eq '^pyOpenSSL>=25\.3,<26\.0$' "$APP_DIR/requirements.txt" || die "Installed v10.30 Main Certbot-compatible pyOpenSSL pin missing"
grep -Eq '^redis>=5\.0,<7\.0$' "$APP_DIR/requirements.txt" || die "Main Redis dependency missing"
grep -Eq '^cryptography>=43,<47$' "$APP_DIR/node_agent/requirements.txt" || die "Node AES-GCM cryptography dependency missing"
grep -Eq '^pyOpenSSL>=25\.3,<26\.0$' "$APP_DIR/node_agent/requirements.txt" || die "Installed v10.30 Node Certbot-compatible pyOpenSSL pin missing"
grep -Eq '^gunicorn>=23,<27$' "$APP_DIR/node_agent/requirements.txt" || die "Node Gunicorn dependency missing"
grep -Eq '^uvicorn-worker>=0.4,<1.0$' "$APP_DIR/node_agent/requirements.txt" || die "Node Uvicorn worker dependency missing"
grep -Fq 'ExecStart=/usr/bin/gunicorn app:app' "$APP_DIR/node_agent/deploy/streamforge-node.service" || die "Direct Node Gunicorn service definition missing"
[[ -f "$APP_DIR/node_agent/deploy/streamforge-node-public.service" ]] || die "Installed v7.9 Node Public systemd unit missing"
[[ -x "$APP_DIR/node_agent/public_start.py" ]] || die "Installed v7.9 Node Public starter missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_SYSTEMD_SPLIT_V79' "$APP_DIR/node_agent/public_start.py" || die "Installed v7.9 Node Public starter marker missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_SYSTEMD_OWNER_V79' "$APP_DIR/node_agent/app.py" || die "Installed v7.9 control/public owner split missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_SYSTEMD_SPLIT_INSTALL_V79' "$APP_DIR/scripts/install_node_agent.sh" || die "Installed v7.9 Node Public installer migration missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_INTERNAL_HEALTH_V80' "$APP_DIR/node_agent/app.py" || die "Installed v8.0 Node Public internal health bypass missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_HEALTH_READY_V80' "$APP_DIR/scripts/install_node_agent.sh" || die "Installed v8.0 Node Public readiness probe marker missing"
grep -Fq 'STREAMFORGE_NODE_ISOLATED_CERTBOT_INSTALL_V1030' "$APP_DIR/scripts/install_node_agent.sh" || die "Installed v10.30 isolated Node Certbot installer missing"
grep -Fq 'STREAMFORGE_NODE_ISOLATED_CERTBOT_V1030' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v10.30 Node TLS helper isolated Certbot preference missing"
grep -Fq 'STREAMFORGE_MAIN_ISOLATED_CERTBOT_V1030' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v10.30 Main TLS helper isolated Certbot preference missing"
grep -Fq 'STREAMFORGE_NODE_ISOLATED_CERTBOT_RENEWAL_V1030' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v10.30 Node isolated Certbot renewal path missing"
grep -Fq 'STREAMFORGE_MAIN_ISOLATED_CERTBOT_RENEWAL_V1030' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v10.30 Main isolated Certbot renewal path missing"
grep -Fq 'STREAMFORGE_NODE_NGINX_UPDATE_UPTIME_V1032' "$APP_DIR/scripts/install_node_agent.sh" || die "Installed v10.34 Node Nginx update-uptime fix missing"
grep -Fq 'STREAMFORGE_NODE_HTTP_FRONT_READY_BEFORE_TLS_V1032' "$APP_DIR/scripts/install_node_agent.sh" || die "Installed v10.34 Node HTTP-before-TLS readiness guard missing"
grep -Fq 'STREAMFORGE_NODE_DISABLE_DISTRO_CERTBOT_TIMER_V1032' "$APP_DIR/scripts/install_node_agent.sh" || die "Installed v10.34 distro Certbot timer isolation guard missing"
grep -Fq 'STREAMFORGE_NODE_HTTP_FRONT_BOOTSTRAP_V1032' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v10.34 Node HTTP frontend bootstrap helper missing"
grep -Fq 'STREAMFORGE_NGINX_SSL_REJECT_COMPAT_V1033' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v10.34 Node legacy-Nginx TLS compatibility missing"
grep -Fq 'STREAMFORGE_MAIN_NGINX_SSL_REJECT_COMPAT_V1033' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v10.34 Main legacy-Nginx TLS compatibility missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_ATOMIC_UNIQUE_TEMP_V1033' "$APP_DIR/node_agent/app.py" || die "Installed v10.34 Node access.json multi-worker atomic-write fix missing"
grep -Fq 'STREAMFORGE_NODE_INTERNAL_MEDIA_AUTH_CANONICAL_BYPASS_V1034' "$APP_DIR/node_agent/app.py" || die "Installed v10.34 Node internal media-auth canonical redirect bypass missing"
grep -Fq 'STREAMFORGE_NODE_DEPLOYED_VERSION_VERIFY_V1034' "$APP_DIR/scripts/install_node_agent.sh" || die "Installed v10.34 Node deployed-version verification missing"
grep -Fq 'STREAMFORGE_MAIN_LOG_SEARCH_V1035' "$APP_DIR/app/main.py" || die "Installed v10.68 Main log search backend missing"
grep -Fq 'STREAMFORGE_NODE_LOG_SEARCH_CLIENT_V1035' "$APP_DIR/app/node_manager.py" || die "Installed v10.68 Main-to-Node log search client missing"
grep -Fq 'STREAMFORGE_NODE_LOG_SEARCH_V1035' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node log search backend missing"
grep -Fq 'STREAMFORGE_LOG_SEARCH_UI_V1035' "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Logs search UI missing"
grep -Fq 'STREAMFORGE_RELAY_ERROR_ONLY_ACCESS_LOG_V1036' "$APP_DIR/deploy/nginx.conf" || die "Installed v10.68 fresh relay access-log filter missing"
grep -Fq 'STREAMFORGE_RELAY_ERROR_ONLY_ACCESS_LOG_V1036' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v10.68 dynamic relay access-log filter missing"
grep -Fq 'STREAMFORGE_LOG_AUTO_SEARCH_UI_V1037' "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Main automatic Logs search UI missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_SEARCH_V1037' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 standalone Node log search backend missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_SEARCH_UI_V1037' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 standalone Node log search UI missing"
grep -Fq 'STREAMFORGE_NODE_LOCAL_RELAY_404_BACKOFF_V1037' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Local Relay 404 retry backoff missing"
grep -Fq 'STREAMFORGE_LOG_LIVE_AJAX_SEARCH_UI_V1038' "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Main live AJAX Logs UI missing"
grep -Fq 'STREAMFORGE_LOG_LIVE_AJAX_SEARCH_JS_V1038' "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Main live AJAX Logs behavior missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_LIVE_AJAX_V1038' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node live AJAX Logs backend marker missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_LIVE_AJAX_JS_V1038' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node live AJAX Logs behavior missing"
grep -Fq 'STREAMFORGE_LOG_PRIVATE_ADDRESSBAR_V1039' "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Main clean log address-bar UI missing"
grep -Fq 'STREAMFORGE_LOG_PRIVATE_ADDRESSBAR_JS_V1039' "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Main clean log address-bar behavior missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_PRIVATE_ADDRESSBAR_V1039' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node clean log address-bar UI missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_PRIVATE_ADDRESSBAR_JS_V1039' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node clean log address-bar behavior missing"
grep -Fq 'STREAMFORGE_LOG_FIXED_CANONICAL_ADDRESSBAR_V1040' "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Main fixed canonical Logs address-bar UI missing"
grep -Fq 'STREAMFORGE_LOG_FIXED_CANONICAL_ADDRESSBAR_JS_V1040' "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Main fixed canonical Logs address-bar behavior missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_FIXED_CANONICAL_ADDRESSBAR_V1040' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node fixed canonical Logs address-bar UI missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_LOG_FIXED_CANONICAL_ADDRESSBAR_JS_V1040' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node fixed canonical Logs address-bar behavior missing"
! grep -Fq "const cleanVisibleUrl" "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Main Logs still rewrites the browser route"
! grep -Fq "const clean=()=>{const type=String(f.querySelector" "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node Logs still rewrites the browser route"
grep -Fq 'STREAMFORGE_NODE_XTREAM_EMPTY_UNSUPPORTED_CATALOG_V1041' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node Xtream empty VOD/Series compatibility fix missing"
! grep -Fq 'raise HTTPException(400, "Unsupported player_api.php action")' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node Xtream API still returns HTTP 400 for unsupported catalogue actions"
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_EDIT_DISPLAY_NAME_V1042' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node playlist-user Display name edit fix missing"
grep -Fq 'current.name = effective_display_name' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node playlist-user Display name persistence missing"
# STREAMFORGE_V1043_NODE_LIVE_SESSION_KILL_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_KILL_NO_NAV_V1043' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node Live Sessions no-navigation kill fix missing"
grep -Fq "'X-StreamForge-Live-Kill':'1'" "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node Live Sessions AJAX kill request missing"
grep -Fq 'BackgroundTask(_panel_session_kill_after_response' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node response-first session cleanup missing"
grep -Fq 'STREAMFORGE_NODE_SESSION_KILL_INDEX_V1043' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 Node Redis session-kill index missing"
grep -Fq 'pipe.sadd(sid_key, viewer_key)' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 Node Redis SID index heartbeat missing"
# STREAMFORGE_V1044_NODE_LIVE_SESSION_CANONICAL_POST_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_KILL_CANONICAL_POST_V1044' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node canonical Live Sessions kill dispatch missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_CLIENT_LOG_SYNC_V1045' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node public-worker Client log sync missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_LOG_SINGLE_WRITER_RETENTION_V1045' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node shared-log single-writer retention guard missing"
grep -Fq 'STREAMFORGE_NODE_XTREAM_MANUAL_LOGIN_LOG_V1045' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node Xtream manual-login Client log event missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_DURATION_V1045' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node Client-log duration renderer missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_DURATION_API_V1045' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node Client-log duration API enrichment missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_DURATION_LOOKUP_V1045' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 Node Redis SID duration lookup missing"
grep -Fq 'STREAMFORGE_MAIN_REMOTE_CLIENT_LOG_DURATION_V1045' "$APP_DIR/app/main.py" || die "Installed v10.68 Main Remote Node duration mapping missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_DURATION_COLUMN_V1045' "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Main Client-log Duration column missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_V1046' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 Node retained session-history writer missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_LOOKUP_V1046' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 Node retained session-history lookup missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_AGE_PERSIST_V1046' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node persistent Client-log Session age missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_LOCAL_V1046' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node local session-history fallback missing"
grep -Fq 'STREAMFORGE_MAIN_REMOTE_CLIENT_SESSION_AGE_PERSIST_V1046' "$APP_DIR/app/main.py" || die "Installed v10.68 Main Remote Node persistent Session age mapping missing"
grep -Fq 'STREAMFORGE_CLIENT_LOG_SESSION_AGE_PERSIST_V1046' "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Main Client-log Session age column marker missing"
grep -Fq '<th>Session age</th>' "$APP_DIR/app/templates/logs.html" || die "Installed v10.68 Main Client-log Session age heading missing"
grep -Fq 'STREAMFORGE_NODE_PLAYBACK_HEARTBEAT_SAFE_V1047' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 playback-safe viewer heartbeat missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_BATCH_V1047' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 retained session-history Redis batch helper missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_SESSION_KILL_MARKER_V1047' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 session-history kill marker missing"
grep -Fq 'STREAMFORGE_NODE_PLAYBACK_CRITICAL_RESERVE_V1049' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 playback-critical reserve isolation missing"
grep -Fq 'STREAMFORGE_NODE_PLAYBACK_HEARTBEAT_ISOLATED_V1049' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 playback heartbeat isolation missing"
grep -Fq 'STREAMFORGE_NODE_VIEWER_ACTIVE_INDEX_V1049' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 active viewer index missing"
grep -Fq 'STREAMFORGE_NODE_SESSION_OBSERVER_V1049' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 single session-history observer missing"
grep -Fq 'STREAMFORGE_NODE_SINGLE_SESSION_OBSERVER_V1049' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 control-only session observer startup missing"
grep -Fq 'STREAMFORGE_NODE_PLAYBACK_NO_LOG_SIDE_EFFECTS_V1049' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 playback logging isolation missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_SESSION_FETCH_V1049' "$APP_DIR/app/node_manager.py" || die "Installed v10.68 Main Node session fetch fix missing"
grep -Fq 'STREAMFORGE_MAIN_REMOTE_SESSION_TIME_NORMALIZE_V1049' "$APP_DIR/app/main.py" || die "Installed v10.68 Main Remote session-time normalization missing"
! grep -Fq 'PLAYBACK_START_CONSUME_LUA' "$APP_DIR/node_agent/redis_state.py" || die "Installed v10.68 stale playback-start consume remains"
! grep -Fq 'manager.consume_playback_start(user.token, sid)' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 master playlist still consumes logging claim"
! grep -Fq 'client_session_history_flush_loop' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 still carries per-public-worker history flush loop"
! grep -Fq 'queue_client_session_history(' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 playback still queues session history"
! sed -n '/RESERVE_LUA = r/,/def __init__/p' "$APP_DIR/node_agent/redis_state.py" | grep -Fq 'KEYS[3]' || die "Installed v10.68 reserve Lua still contains logging/history key work"
! sed -n '/def touch_viewer(/,/def touch_viewer_history_batch(/p' "$APP_DIR/node_agent/redis_state.py" | grep -Fq 'HISTORY_TOUCH_LUA' || die "Installed v10.68 history write still blocks the critical viewer heartbeat"
grep -Fq "header_cells += '<th>Session age</th>'" "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node Client-log Session age heading missing"
! grep -Fq '_node_client_log_duration_map(' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 stale live-only Client duration map remains"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_KILL_CANONICAL_POST_JS_V1044' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node canonical Live Sessions kill browser routing missing"
grep -Fq "'X-StreamForge-Panel-Action':'live-session-kill'" "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node Live Sessions public-root action header missing"
grep -Fq 'resolved_path = f"/panel/sessions/{kill_sid}/kill"' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node hidden-route session kill dispatcher missing"
grep -Fq 'access_log /var/log/nginx/access.log combined if=$streamforge_relay_access_loggable;' /etc/nginx/sites-available/streamforge || die "Active v10.68 relay access-log filter missing"
grep -Fq 'STREAMFORGE_NODE_CERTBOT_FORCE_RETRY_V1028' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v10.30 Node ACME force-retry helper missing"
grep -Fq 'STREAMFORGE_NODE_TLS_FORCE_RETRY_V1028' "$APP_DIR/node_agent/app.py" || die "Installed v10.30 Node TLS retry request payload missing"
grep -Fq 'STREAMFORGE_MAIN_DNS01_PANEL_ROOT_V81' "$APP_DIR/app/templates/node_form.html" || die "Installed v8.1 Main DNS-01 panel-root marker missing"
grep -Fq "post(nodeDns01Path('test'), {host})" "$APP_DIR/app/templates/node_form.html" || die "Installed v8.1 DNS-01 Test CNAME action is not prefix-aware"
grep -Fq "post(nodeDns01Path('retry'), {host})" "$APP_DIR/app/templates/node_form.html" || die "Installed v8.1 DNS-01 Retry certificate action is not prefix-aware"
grep -Fq 'STREAMFORGE_MAIN_NODE_DNS01_REMOTE_ACTIONS_V82' "$APP_DIR/app/main.py" || die "Installed v8.2 Main-to-Node DNS-01 test proxy missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_DNS01_RETRY_V82' "$APP_DIR/app/main.py" || die "Installed v8.2 Main DNS-01 retry fix missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_DNS01_REMOTE_TEST_V82' "$APP_DIR/app/node_manager.py" || die "Installed v8.2 Node DNS-01 control client missing"
grep -Fq 'STREAMFORGE_NODE_DNS01_CONTROL_TEST_V82' "$APP_DIR/node_agent/app.py" || die "Installed v8.2 Node DNS-01 test endpoint missing"
grep -Fq 'STREAMFORGE_MAIN_APP_ROOT_EARLY_BOOTSTRAP_V83' "$APP_DIR/app/templates/base.html" || die "Installed v8.3 early Main app-root bootstrap missing"
grep -Fq 'STREAMFORGE_MAIN_DNS01_SERVER_ROOT_V83' "$APP_DIR/app/templates/node_form.html" || die "Installed v8.3 DNS-01 render-time prefix fix missing"
grep -Fq -- '--workers 1 --worker-class uvicorn_worker.UvicornWorker' "$APP_DIR/node_agent/deploy/streamforge-node.service" || die "Node single ASGI worker configuration missing"
! grep -Eq 'venv/bin|python3 -m uvicorn|/usr/bin/env python3' "$APP_DIR/node_agent/deploy/streamforge-node.service" || die "Packaged Node service still uses a venv/direct-Uvicorn runtime"
grep -Fq 'def _gunicorn_asgi_command' "$APP_DIR/node_agent/app.py" || die "Node child-listener Gunicorn command builder missing"
[[ "$(grep -Fc 'cmd = _gunicorn_asgi_command(' "$APP_DIR/node_agent/app.py")" -eq 2 ]] || die "Every Node child listener is not using Gunicorn"
! grep -Fq 'sys.executable, "-m", "uvicorn"' "$APP_DIR/node_agent/app.py" || die "Direct child Uvicorn launcher remains"
grep -Fq 'Gunicorn systemd migration requires a saved SSH Node update' "$APP_DIR/node_agent/app.py" || die "API-only systemd migration guard missing"
grep -Fq 'APP_VERSION in {' "$APP_DIR/app/main.py" || die "One-time saved-SSH Gunicorn migration guard missing"
grep -Fq '"2.1.205"' "$APP_DIR/app/main.py" || die "v3.2 Main compatibility marker missing"
grep -Fq "[('activity','Activity log'),('access','Access log'),('client','Client log'),('system','System log')]" "$APP_DIR/app/templates/logs.html" || die "Main Activity-first log tabs missing"
grep -Fq "[('activity','Activity log'),('access','Access log'),('client','Client log'),('system','System log')]" "$APP_DIR/node_agent/app.py" || die "Node Activity-first log tabs missing"
grep -Fq 'def issue_playback_keys(' "$APP_DIR/app/playback_keys.py" || die "Batch playback-key issuer missing"
grep -Fq 'ThreadPoolExecutor(max_workers=workers' "$APP_DIR/app/node_manager.py" || die "Parallel Node catalogue status fetch missing"
grep -Fq 'playback_keys = issue_playback_keys(' "$APP_DIR/app/main.py" || die "Fast playlist batch-key path missing"
grep -Fq '# v2.1.15: define the batch in this endpoint before the get.php loop.' "$APP_DIR/app/main.py" || die "get.php playback-key regression fix missing"
grep -Fq '# v2.1.15: category responses never require request-scoped playback keys.' "$APP_DIR/app/main.py" || die "Xtream category regression fix missing"
grep -Fq 'class="table-wrap node-log-table-wrap"' "$APP_DIR/node_agent/app.py" || die "Node log table structure fix missing"
grep -Fq 'class="node-log-panel"' "$APP_DIR/node_agent/app.py" || die "Unified Node log card missing"
grep -Fq 'class="node-log-tabs"' "$APP_DIR/node_agent/app.py" || die "Node log tabs structure missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_USER_NO_DETAILS_V99R17' "$APP_DIR/node_agent/app.py" || die "Installed v9.9 r17 Node Client log User/no-Details UI missing"
grep -Fq '.node-log-col-time{width:190px}' "$APP_DIR/node_agent/app.py" || die "Stable Node log column sizing missing"
grep -Fq '/* v2.1.24 stable Main-to-Node update page */' "$APP_DIR/app/static/style.css" || die "Remote update page layout fix missing"
grep -Fq '<div class="node-update-page">' "$APP_DIR/app/templates/node_update.html" || die "Spaced update page container missing"
grep -Fq '.node-update-page{display:grid;gap:16px}' "$APP_DIR/app/static/style.css" || die "Update card spacing missing"
grep -Fq 'node-update-notice' "$APP_DIR/app/templates/node_update.html" || die "Update notice spacing class missing"
grep -Fq '.node-update-notice{margin:0 0 16px}' "$APP_DIR/app/static/style.css" || die "Update notice bottom spacing missing"
grep -Fq 'def set_network_interfaces' "$APP_DIR/app/system_metrics.py" || die "Selectable Main network sampler missing"
grep -Fq 'name="network_interfaces" multiple' "$APP_DIR/app/templates/system_branding.html" || die "Network interface multi-select missing"
grep -Fq 'value="__all__"' "$APP_DIR/app/templates/system_branding.html" || die "All-interfaces option missing"
grep -Fq 'system_metrics.set_network_interfaces(selected_interfaces)' "$APP_DIR/app/main.py" || die "Saved NIC selection is not applied to Main metrics"
grep -Fq '.backup-key-field,.backup-key-field input,.graph-span-field,.graph-span-field select{width:100%;max-width:none}' "$APP_DIR/app/templates/system_branding.html" || die "Full-width Settings controls missing"
grep -Fq '/system/backups/google/connect' "$APP_DIR/app/main.py" || die "Direct Google sign-in route missing"
grep -Fq 'https://oauth2.googleapis.com/token' "$APP_DIR/app/main.py" || die "Google OAuth token exchange missing"
grep -Fq 'Sign in with Google' "$APP_DIR/app/templates/backups.html" || die "Google sign-in UI missing from dedicated Backups page"
grep -Fq 'upload_file_to_google_drive' "$APP_DIR/app/backup_manager.py" || die "Direct Google Drive uploader missing"
grep -Fq 'tarfile.open(partial, mode="w:gz")' "$APP_DIR/app/backup_manager.py" || die "Service-safe backup archive writer missing"
grep -Fq 'def test_destination(' "$APP_DIR/app/backup_manager.py" || die "Backup destination tester missing"
grep -Fq 'from cryptography.fernet import Fernet' "$APP_DIR/app/backup_manager.py" || die "Encrypted backup credential support missing"
grep -Fq 'def _upload_secure_smb(' "$APP_DIR/app/backup_manager.py" || die "Authenticated SMB uploader missing"
grep -Fq 'password=str(password or "") or None' "$APP_DIR/app/backup_manager.py" || die "SSH password authentication missing"
grep -Fq 'paramiko.SSHClient()' "$APP_DIR/app/backup_manager.py" || die "SSH/SFTP backend missing"
grep -Fq 'Test destination' "$APP_DIR/app/templates/backups.html" || die "Backup destination test UI missing"
grep -Fq 'google-connected-bar' "$APP_DIR/app/templates/backups.html" || die "Compact Google backup UI missing"
grep -Fq 'google-oauth-field' "$APP_DIR/app/templates/backups.html" || die "Google OAuth fields missing from dedicated Backups page"
grep -Fq 'data-google-oauth-input' "$APP_DIR/app/templates/backups.html" || die "Google OAuth inputs missing from dedicated Backups page"
grep -Fq 'data-google-backup-card' "$APP_DIR/app/templates/backups.html" || die "Google Drive backup panel missing from dedicated Backups page"
grep -Fq 'input.disabled = googleCard.hidden' "$APP_DIR/app/templates/backups.html" || die "Conditional Google OAuth validation handling missing"
grep -Fq 'data-google-backup-card hidden' "$APP_DIR/app/templates/backups.html" || die "Conditional Google backup panel missing"
grep -Fq 'backup-destination-field' "$APP_DIR/app/templates/backups.html" || die "Single-row backup fields layout missing"
grep -Fq '>Interval' "$APP_DIR/app/templates/backups.html" || die "Backup interval label missing"
grep -Fq 'data-backup-edit' "$APP_DIR/app/templates/backups.html" || die "Backup destination Edit UI missing"
grep -Fq 'form.classList.add('\''is-editing'\'')' "$APP_DIR/app/templates/backups.html" || die "Backup edit-mode UI lock missing"
grep -Fq 'kind = str(target.get("kind") or "local")' "$APP_DIR/app/backup_manager.py" || die "Backup type-lock backend missing"
grep -Fq 'input.disabled = googleCard.hidden' "$APP_DIR/app/templates/backups.html" || die "Hidden Google OAuth validation fix missing"
grep -Fq 'def update_target(' "$APP_DIR/app/backup_manager.py" || die "Backup destination update backend missing"
grep -Fq '/system/backups/{target_id}/update' "$APP_DIR/app/main.py" || die "Backup destination update route missing"
grep -Fq 'grid-column:1/-1' "$APP_DIR/app/templates/backups.html" || die "Full-width backup destination/Google panel layout missing"
grep -Fq 'value="" placeholder="Backup name"' "$APP_DIR/app/templates/backups.html" || die "Blank backup name default missing"
grep -Fq 'backup-schedule-field' "$APP_DIR/app/templates/backups.html" || die "Backup form grid fix missing"
grep -Fq 'runtime STREAMFORGE_* snapshot' "$APP_DIR/app/backup_manager.py" || die "Runtime environment snapshot missing"
grep -Fq 'Save &amp; backup now' "$APP_DIR/app/templates/backups.html" || die "Google backup-now UI missing"
grep -Fq 'run_now: int = Form(0)' "$APP_DIR/app/main.py" || die "Backup-now endpoint support missing"
grep -Fq -- '--worker-tmp-dir /var/lib/streamforge/gunicorn-tmp' "$APP_DIR/deploy/streamforge.service" || die "Dedicated Gunicorn worker temp directory missing"

grep -Fq '.user-max-connections-field{width:100%}' "$APP_DIR/app/static/style.css" || die "Full-width playlist-user connection field missing"
grep -Fq 'multiple size="1" data-network-interface-select' "$APP_DIR/app/templates/system_branding.html" || die "Normal-height network selector missing"
grep -Fq '.metrics-settings-grid input:not([type="checkbox"]),.metrics-settings-grid select{height:40px;min-height:40px' "$APP_DIR/app/templates/system_branding.html" || die "Uniform Settings control height missing"
! grep -Fq 'How often a graph point is saved.' "$APP_DIR/app/templates/system_branding.html" || die "Removed graph sample helper text still present"
grep -Fq 'STREAMFORGE_UNIFIED_DROPDOWN_STYLE' "$APP_DIR/app/static/style.css" || die "Project-wide dropdown styling marker missing"
grep -Fq 'STREAMFORGE_DROPDOWN_FULL_AUDIT' "$APP_DIR/app/static/style.css" || die "Main authoritative dropdown styling missing"
grep -Fq 'STREAMFORGE_CATEGORY_DROPDOWN_UNIFIED' "$APP_DIR/app/static/style.css" || die "Channel category dropdown unification missing"
grep -Fq 'streamforge-apply-main-access' "$APP_DIR/scripts/install.sh" || die "Fresh install Main web-listener helper setup missing"
grep -Fq 'client_max_body_size 1g;' "$APP_DIR/deploy/nginx.conf" || die "Main Nginx backup upload capacity missing"
grep -Fq 'client_max_body_size 1g;' "$APP_DIR/scripts/apply_main_access.py" || die "Dynamic Main Nginx backup upload capacity missing"
grep -Fq 'content:none!important' "$APP_DIR/app/static/style.css" || die "Legacy category chevron suppression missing"
grep -Fq 'background-position:' "$APP_DIR/app/static/style.css" || die "Unified dropdown chevron positioning missing"
grep -Fq 'html body select:not([multiple]):not(#streamforge-dropdown-never)' "$APP_DIR/app/static/style.css" || die "Main high-specificity dropdown normalization missing"
grep -Fq 'html body select[multiple][size="1"]:not(#streamforge-dropdown-never)' "$APP_DIR/app/static/style.css" || die "Main multi-select dropdown normalization missing"
grep -Fq 'STREAMFORGE_NODE_DROPDOWN_FULL_AUDIT' "$APP_DIR/node_agent/app.py" || die "Node authoritative dropdown styling missing"
grep -Fq 'select[multiple][size="1"]{' "$APP_DIR/app/static/style.css" || die "Size-1 multi-select dropdown-height styling missing"
grep -Fq 'select[multiple] option:checked{' "$APP_DIR/app/static/style.css" || die "Multi-select selected-option styling missing"
grep -Fq 'input:not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="hidden"]),select:not([multiple]):not([data-uniform-height-exempt]):not([data-native-height]){height:40px!important;min-height:40px!important' "$APP_DIR/app/static/style.css" || die "Main uniform form control height missing"
grep -Fq 'input:not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="hidden"]),select:not([multiple]):not([data-uniform-height-exempt]):not([data-native-height]){height:40px!important;min-height:40px!important' "$APP_DIR/node_agent/app.py" || die "Node uniform form control height missing"
! grep -Fqi 'not scanned' "$APP_DIR/app/templates/channel_form.html" || die "Main channel Not scanned placeholder still present"
! grep -Fqi 'not scanned' "$APP_DIR/app/static/app.js" || die "Main dynamic channel Not scanned placeholder still present"
! grep -Fqi 'not scanned' "$APP_DIR/node_agent/app.py" || die "Node channel Not scanned placeholder still present"
grep -Fq 'label:has(>input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"])),label:has(>select),label:has(>textarea){gap:7px!important;row-gap:7px!important}' "$APP_DIR/app/static/style.css" || die "Main uniform field spacing missing"
grep -Fq 'label:has(>input:not([type="checkbox"]):not([type="radio"]):not([type="hidden"])),label:has(>select),label:has(>textarea){gap:7px!important;row-gap:7px!important}' "$APP_DIR/node_agent/app.py" || die "Node uniform field spacing missing"
grep -Fq 'data-user-load-balance-toggle' "$APP_DIR/app/templates/user_form.html" || die "Load-balance-aware node selector toggle missing"
grep -Fq 'enforceNodeLimit' "$APP_DIR/app/static/app.js" || die "Load-balance-aware node selector UI missing"
grep -Fq 'effective_node_ids = effective_node_ids[:1]' "$APP_DIR/app/main.py" || die "Single-node backend enforcement missing"
grep -Fq '.form-grid>label:not(.check){display:grid;grid-template-rows:auto auto minmax(16px,auto);align-content:start}' "$APP_DIR/app/static/style.css" || die "Main reserved helper-row alignment missing"
grep -Fq '.form-grid>label:not(.check),.panel-form>.grid>label:not(.check),.grid.panel-form>label:not(.check){display:grid;grid-template-rows:auto auto minmax(16px,auto);align-content:start}' "$APP_DIR/node_agent/app.py" || die "Node reserved helper-row alignment missing"
grep -Fq '.playlist-profile-panel td.actions{display:table-cell;white-space:nowrap}' "$APP_DIR/app/static/style.css" || die "Playlist Actions table-cell fix missing"
grep -Fq '.playlist-profile-panel tbody tr:last-child td{border-bottom:0}' "$APP_DIR/app/static/style.css" || die "Playlist final separator cleanup missing"
grep -Fq 'class="panel-head user-list-head"' "$APP_DIR/app/templates/users.html" || die "Users header filter placement missing"
! grep -Fq 'class="panel user-filter-panel"' "$APP_DIR/app/templates/users.html" || die "Old standalone user filter panel still present"
grep -Fq '.user-add-button{flex:0 0 auto;white-space:nowrap' "$APP_DIR/app/static/style.css" || die "Add user no-wrap fix missing"
grep -Fq 'class="form-grid metrics-settings-grid"' "$APP_DIR/app/templates/system_branding.html" || die "Four-column capacity settings markup missing"
grep -Fq '.metrics-settings-grid{grid-template-columns:repeat(4,minmax(0,1fr))}' "$APP_DIR/app/templates/system_branding.html" || die "Four-column capacity settings layout missing"
grep -Fq 'class="user-max-connections-field"' "$APP_DIR/app/templates/user_form.html" || die "Compact playlist-user connection field missing"
grep -Fq '<details class="node-update-log-box">' "$APP_DIR/app/templates/node_update.html" || die "Collapsed live update log missing"
! grep -Fq '<details class="node-update-log-box" open>' "$APP_DIR/app/templates/node_update.html" || die "Live update log still defaults to expanded"
grep -Fq 'white-space:pre-wrap!important;overflow-x:hidden!important' "$APP_DIR/app/static/style.css" || die "Wrapped update log overflow fix missing"
grep -Fq '.node-update-steps{grid-template-columns:repeat(3,minmax(0,1fr))!important}' "$APP_DIR/app/static/style.css" || die "Responsive update phase layout missing"
grep -Fq 'allow_remote_fetch: bool = True' "$APP_DIR/app/node_manager.py" || die "Cache-only Node runtime option missing"
grep -Fq 'public_base=base' "$APP_DIR/app/main.py" || die "Single-query playlist logo base reuse missing"
grep -Fq 'def public_local_node(db: Session) -> Node:' "$APP_DIR/app/main.py" || die "Read-only public Local Node lookup missing"
! grep -Fq 'local_node = ensure_local_node(db)' "$APP_DIR/app/main.py" || die "Xtream catalogue still takes a Local Node write lock"
grep -Fq 'db: Session | None = None' "$APP_DIR/app/audit_log.py" || die "Request-scoped audit logging missing"
grep -Fq 'STREAMFORGE_MAIN_XTREAM_PLAYLIST_REQUEST_DB_REUSE_V1061' "$APP_DIR/app/main.py" || die "Installed v10.68 Xtream playlist Client-log request DB reuse marker missing"
grep -Fq 'STREAMFORGE_STATIC_ROUTE_CATALOG_FALLBACK_V100' "$APP_DIR/app/main.py" || die "Installed v10.6 Static-route catalogue fallback missing"
grep -Fq 'and bool(item.get("hls_ready"))' "$APP_DIR/app/main.py" || die "Online HLS readiness filter missing"
grep -Fq 'now - cached[0] < cache_ttl' "$APP_DIR/app/node_manager.py" || die "Bulk Node success/offline status cache TTL missing"
grep -Fq '<table class="session-table">' "$APP_DIR/app/templates/viewer_sessions.html" || die "Structured live-session table missing"
grep -Fq 'class="session-delivery-cell"' "$APP_DIR/app/templates/viewer_sessions.html" || die "Live-session Delivery cell fix missing"
grep -Fq '.session-col-delivery{width:155px}' "$APP_DIR/app/static/style.css" || die "Live-session Delivery column width missing"
grep -Fq 'white-space:nowrap;line-height:1.2' "$APP_DIR/app/static/style.css" || die "Unbroken Delivery badge styling missing"
grep -Fq 'class="table-wrap node-session-table-wrap"' "$APP_DIR/node_agent/app.py" || die "Structured Node live-session table missing"
grep -Fq 'class="node-session-filters"' "$APP_DIR/node_agent/app.py" || die "Node live-session filter layout missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_SESSION_NO_DELIVERY_V92' "$APP_DIR/node_agent/app.py" || die "Installed v9.2 Node Live Sessions Delivery-removal marker missing"
! grep -Fq '<th>Delivery</th>' "$APP_DIR/node_agent/app.py" || die "Installed v9.2 Node Live Sessions still exposes Delivery column"
grep -Fq 'Math.min(size-1,Math.ceil(size*0.10))' "$APP_DIR/app/static/dashboard.js" || die "Installed Main repeated ninety-percent click zoom missing"
grep -Fq 'Math.min(size-1,Math.ceil(size*0.10))' "$APP_DIR/node_agent/app.py" || die "Installed Node repeated ninety-percent click zoom missing"
grep -Fq '.hardware-summary{{display:flex!important;align-items:center;justify-content:space-between' "$APP_DIR/node_agent/app.py" || die "Installed Node hardware summary edge alignment missing"
grep -Fq 'canvas.onclick=e=>' "$APP_DIR/app/static/dashboard.js" || die "Installed Main single-click synchronized zoom missing"
grep -Fq 'canvas.onclick=e=>' "$APP_DIR/node_agent/app.py" || die "Installed Node single-click synchronized zoom missing"
grep -Fq 'data-cpu-cores' "$APP_DIR/node_agent/app.py" || die "Installed Node CPU core count display missing"
grep -Fq 'data-memory-total' "$APP_DIR/node_agent/app.py" || die "Installed Node RAM total display missing"
grep -Fq 'sharedZoomRedraw.forEach(draw=>draw())' "$APP_DIR/app/static/dashboard.js" || die "Installed Main synchronized graph zoom missing"
grep -Fq 'sharedZoomRedraw.forEach(draw=>draw())' "$APP_DIR/node_agent/app.py" || die "Installed Node synchronized graph zoom missing"
grep -Fq '"online_channels": max(0, int(channels.get("online") or 0))' "$APP_DIR/app/metrics_history.py" || die "Installed Main channel history sampling missing"
grep -Fq 'data-metric-chart="channels"' "$APP_DIR/app/templates/dashboard.html" || die "Installed Main Channels graph missing"
grep -Fq 'data-node-metric-chart="channels"' "$APP_DIR/node_agent/app.py" || die "Installed Node Channels graph missing"
grep -Fq "canvas.ondblclick=()=>" "$APP_DIR/app/static/dashboard.js" || die "Installed Main graph zoom reset missing"
grep -Fq "canvas.ondblclick=()=>" "$APP_DIR/node_agent/app.py" || die "Installed Node graph zoom reset missing"
grep -Fq -- '--no-cache-dir --ignore-installed --upgrade -r' "$LIVE_UPDATER" || die "No-cache updater pip overlay missing"
grep -Fq -- '--no-cache-dir --ignore-installed --upgrade -r' "$APP_DIR/scripts/install.sh" || die "No-cache Main installer pip overlay missing"
grep -Fq -- '--no-cache-dir --ignore-installed --upgrade -r' "$APP_DIR/scripts/install_node_agent.sh" || die "No-cache Node installer pip overlay missing"
grep -Fq '"--no-cache-dir", "--ignore-installed", "--upgrade"' "$APP_DIR/node_agent/app.py" || die "No-cache Node API updater pip overlay missing"
grep -Fq 'def _main_panel_disconnected_page' "$APP_DIR/node_agent/app.py" || die "Styled Main-disconnected page missing"
grep -Fq '@app.exception_handler(HTTPException)' "$APP_DIR/node_agent/app.py" || die "Browser/API exception split missing"
grep -Fq 'sec-fetch-dest' "$APP_DIR/node_agent/app.py" || die "Browser navigation detection missing"
grep -Fq 'HTTP 503 · Live Main authorization required' "$APP_DIR/node_agent/app.py" || die "Disconnected-page status footer missing"
grep -Fq 'if not manager.panel_connected(force=True):' "$APP_DIR/node_agent/app.py" || die "Fail-closed Node login-page connectivity gate missing"
grep -Fq 'def reload_channel_catalog_if_changed' "$APP_DIR/node_agent/app.py" || die "Read-only Node playlist catalogue refresh missing"
grep -Fq 'autostart and NODE_CHANNEL_OWNER and runtime.desired_running' "$APP_DIR/node_agent/app.py" || die "Dedicated channel-owner autostart guard missing"
grep -Fq 'def _hls_output_state' "$APP_DIR/node_agent/app.py" || die "External HLS readiness detection missing"
grep -Fq 'playlist_listener_port = max(1, min(65535, int(playlist_port or panel_listener_port)))' "$APP_DIR/app/main.py" || die "Shared Node playlist port default missing"
grep -Fq 'EXTERNAL_PROXY_MODE = os.getenv("STREAMFORGE_NODE_EXTERNAL_PROXY"' "$APP_DIR/node_agent/app.py" || die "Co-located Node external-proxy mode missing"
grep -Fq 'Browsers commonly omit an explicit default :80/:443' "$APP_DIR/node_agent/app.py" || die "Default HTTP port Host matching fix missing"
grep -Fq 'for value in [*self.panel_urls, *self.stream_urls]:' "$APP_DIR/node_agent/app.py" || die "Multiple Playlist/App listener-port fix missing"
grep -Fq 'def stream_gateway_ports' "$APP_DIR/node_agent/app.py" || die "Playlist/App listener-port discovery missing"
grep -Fq 'stream_only = value in self.stream_urls and value not in self.panel_urls' "$APP_DIR/node_agent/app.py" || die "Stream-only listener verification missing"
grep -Fq "mirror.dataset.bulkChannelMirror = '1'" "$APP_DIR/app/static/app.js" || die "Bulk channel selection submission fix missing"
grep -Fq 'node_controller.forget(channel_id)' "$APP_DIR/app/main.py" || die "Bulk channel cleanup ordering fix missing"
grep -Fq 'if action == "delete_selected":' "$APP_DIR/app/main.py" || die "Isolated bulk-delete handler missing"
grep -Fq 'query = {"message": f"Deleted {deleted} channel(s)"}' "$APP_DIR/app/main.py" || die "Bulk-delete safe redirect missing"
grep -Fq 'ALTER TABLE node_stream_users ADD COLUMN playlist_order' "$APP_DIR/scripts/migrate_online_node_users.py" || die "Node-user playlist-order migration missing"
grep -Fq 'if "playlist_order" not in node_user_columns:' "$APP_DIR/app/db.py" || die "Runtime node-user schema repair missing"
grep -Fq 'bulk_hls_segment_time: str = Form("keep")' "$APP_DIR/app/main.py" || die "Bulk HLS segment control missing"
grep -Fq 'bulk_profile_node_ids: list[int] = Form(default=[])' "$APP_DIR/app/main.py" || die "Bulk node-targeted HLS segment backend missing"
grep -Fq 'name="bulk_profile_node_ids" multiple' "$APP_DIR/app/templates/channels.html" || die "Bulk HLS node-target selector missing"
grep -Fq 'name="node_hls_segment_times"' "$APP_DIR/app/templates/channel_form.html" || die "Per-node HLS segment selector missing"
grep -Fq 'Column("hls_segment_time", Integer, nullable=True)' "$APP_DIR/app/models.py" || die "Per-node HLS segment schema missing"
grep -Fq 'profile.get("hls_segment_time") or channel.hls_segment_time' "$APP_DIR/app/node_manager.py" || die "Remote Node HLS segment override payload missing"
grep -Fq 'channel.hls_segment_time = max(1, min(20, int(hls_segment_time)))' "$APP_DIR/app/ffmpeg.py" || die "Local Node HLS segment override missing"
grep -Fq '"hls_segment_time": max(1, min(20, int(getattr(config, "hls_segment_time", 1) or 1)))' "$APP_DIR/node_agent/app.py" || die "Node-synchronized HLS segment preservation missing"
grep -Fq "'hls_selected': 'channels.edit'" "$APP_DIR/node_agent/app.py" || die "Node-local bulk HLS segment backend missing"
grep -Fq 'def _node_channel_display_ids()' "$APP_DIR/node_agent/app.py" || die "Owner-aware Node channel numbering missing"
grep -Fq 'display_id: int | None = None' "$APP_DIR/node_agent/app.py" || die "Main catalogue display ID field missing on Node"
grep -Fq '"display_id": _channel_public_catalogue_number(channel.id)' "$APP_DIR/app/node_manager.py" || die "Main catalogue display ID sync missing"
grep -Fq 'stream_number = int(channel.id) if user.user_type == "restream"' "$APP_DIR/app/main.py" || die "Restream database-ID playlist numbering missing"
grep -Fq 'for field, value in original_profile.items():' "$APP_DIR/app/ffmpeg.py" || die "Local Node override default-restoration guard missing"
grep -Fq 'def channel_public_number_map(db: Session)' "$APP_DIR/app/main.py" || die "Public channel-number map missing"
grep -Fq 'public_numbers[channel.id]}/master.m3u8' "$APP_DIR/app/main.py" || die "Public 101+ playlist URL generation missing"
grep -Fq 'f"/ek/{playback_key}/{channel_id}/n/{node.id}/index.m3u8"' "$APP_DIR/app/main.py" || die "Public catalogue ID playback propagation missing"
grep -Fq 'name="bulk_hls_segment_time"' "$APP_DIR/app/templates/channels.html" || die "Bulk HLS segment selector missing"
grep -Fq '{{ channel_display_ids[channel.id] }}' "$APP_DIR/app/templates/channels.html" || die "Contiguous channel display ID rendering missing"
grep -Fq 'data-channel-sort-key="displayId"' "$APP_DIR/app/templates/channels.html" || die "Installed Main display-ID sortable header missing"
grep -Fq 'data-channel-sort-key="dbId"' "$APP_DIR/app/templates/channels.html" || die "Installed Main database-ID sortable header missing"
grep -Fq '<td class="channel-id-cell mono" data-label="DB">{{ channel.id }}</td>' "$APP_DIR/app/templates/channels.html" || die "Main database ID rendering missing"
grep -Fq 'stream_id=display_ids.get(rt.config.key)' "$APP_DIR/node_agent/app.py" || die "Node visible playlist serial mapping missing"
grep -Fq "'stream_id': display_ids.get(resolved_key, item.stream_id)" "$APP_DIR/node_agent/app.py" || die "Node effective playlist serial mapping missing"
grep -Fq '/{_node_stream_id(channel)}/master.m3u8' "$APP_DIR/node_agent/app.py" || die "Node serial playback URLs missing"
grep -Fq 'STREAMFORGE_NODE_SESSION_PLAYBACK_GRANT_V65' "$APP_DIR/node_agent/app.py" || die "Node serial/session playback authorization missing"
grep -Fq 'def _node_main_channel_direct_actions' "$APP_DIR/node_agent/app.py" || die "Node Main channel direct action icons missing"
grep -Fq "@app.get('/panel/channels/{key}/info'" "$APP_DIR/node_agent/app.py" || die "Node channel information page missing"
grep -Fq 'node-info-control' "$APP_DIR/node_agent/app.py" || die "Node information icon styling missing"
grep -Fq 'node-log-control' "$APP_DIR/node_agent/app.py" || die "Node stream-log icon styling missing"
grep -Fq "const mobileSidebar = window.matchMedia('(max-width: 760px)')" "$APP_DIR/app/static/app.js" || die "Main mobile navigation controller missing"
grep -Fq '.app-shell.mobile-nav-open .sidebar nav{display:grid}' "$APP_DIR/app/static/style.css" || die "Main mobile navigation layout missing"
grep -Fq 'aria-controls="main-navigation"' "$APP_DIR/app/templates/base.html" || die "Main mobile navigation accessibility binding missing"
grep -Fq '.channel-panel-head .head-actions{display:grid;width:100%;grid-template-columns:repeat(2,minmax(0,1fr))' "$APP_DIR/app/static/style.css" || die "Channels mobile action grid missing"
grep -Fq '.channel-filter-bar{position:static;top:auto;padding:14px 16px}' "$APP_DIR/app/static/style.css" || die "Channels mobile filter flow missing"
grep -Fq '.channel-search-form{display:grid;grid-template-columns:1fr;width:100%' "$APP_DIR/app/static/style.css" || die "Channels mobile filter layout missing"
# STREAMFORGE_NODES_MOBILE_HEAD_ACTIONS_FIX_V2305
grep -Fq 'node-cluster-panel' "$APP_DIR/app/templates/nodes.html" || die "Nodes mobile panel marker missing"
grep -Fq 'STREAMFORGE_NODES_MOBILE_HEAD_ACTIONS_FIX_V2305' "$APP_DIR/app/static/style.css" || die "Nodes mobile head action CSS missing"
# STREAMFORGE_BACKUPS_MOBILE_LAYOUT_FIX_V2306
grep -Fq 'STREAMFORGE_BACKUPS_MOBILE_LAYOUT_FIX_V2306' "$APP_DIR/app/templates/backups.html" || die "Backups mobile layout CSS missing"
# STREAMFORGE_DASHBOARD_MOBILE_LAYOUT_FIX_V2307
grep -Fq 'dashboard-live-channels-panel' "$APP_DIR/app/templates/dashboard.html" || die "Dashboard live-channel mobile panel marker missing"
grep -Fq 'STREAMFORGE_DASHBOARD_MOBILE_LAYOUT_FIX_V2307' "$APP_DIR/app/static/style.css" || die "Dashboard mobile layout CSS missing"

grep -Fq 'grid-template-columns:minmax(0,1fr)!important;' "$APP_DIR/app/templates/backups.html" || die "Backups mobile one-column form layout missing"

grep -Fq '.node-cluster-panel .nodes-head-actions{' "$APP_DIR/app/static/style.css" || die "Nodes mobile head action selector missing"
grep -Fq 'width:100%!important;' "$APP_DIR/app/static/style.css" || die "Nodes mobile full-width action layout missing"
grep -Fq 'grid-template-columns:repeat(6,minmax(120px,1fr)) minmax(120px,auto)' "$APP_DIR/app/static/style.css" || die "Six-field bulk profile desktop layout missing"
grep -Fq '.bulk-profile-grid select,.bulk-profile-grid input{display:block;width:100%;height:40px' "$APP_DIR/app/static/style.css" || die "Bulk profile themed equal-height controls missing"
grep -Fq '.bulk-profile-apply{height:40px;min-height:40px' "$APP_DIR/app/static/style.css" || die "Bulk profile action height alignment missing"
grep -Fq 'def load_colocated_node_routes' "$APP_DIR/scripts/apply_main_access.py" || die "Co-located Node Nginx route generator missing"
python3 -c 'from cryptography.hazmat.primitives.ciphers.aead import AESGCM' || die "Global Main AES-GCM import failed"
grep -q 'Local FFmpeg snapshots historically omitted hls_ready' "$APP_DIR/app/node_manager.py" || die "Local playlist readiness fix missing"
grep -q 'item\["hls_ready"\] = bool(local_alive and local_hls_ready)' "$APP_DIR/app/node_manager.py" || die "Local hls_ready publication missing"
grep -q 'channel-editor-form' "$APP_DIR/app/templates/channel_form.html" || die "Main channel editor form marker missing"
grep -q 'channel-toggle-grid' "$APP_DIR/app/templates/channel_form.html" || die "Main channel toggle grid missing"
grep -q 'v1.11.58 unified channel editor field sizing and alignment' "$APP_DIR/app/static/style.css" || die "Main channel editor sizing CSS missing"
grep -q 'v1.11.58 unified Node channel editor field sizing and alignment' "$APP_DIR/node_agent/app.py" || die "Node channel editor sizing CSS missing"
grep -q 'main_class="channel-editor-main"' "$APP_DIR/node_agent/app.py" || die "Node channel editor width class missing"
grep -q 'identity-compact-row' "$APP_DIR/app/templates/channel_form.html" || die "Compact Main identity row missing"
grep -q 'v1.11.58 compact channel editor controls and source rows' "$APP_DIR/app/static/style.css" || die "Compact Main editor CSS missing"
grep -q 'v1.11.58 compact Node channel editor controls and source rows' "$APP_DIR/node_agent/app.py" || die "Compact Node editor CSS missing"
! grep -q 'Seconds between primary-source availability checks while a backup is active' "$APP_DIR/app/templates/channel_form.html" || die "Redundant Main failback helper remains"
! grep -q 'Seconds between primary-source checks while a backup is active' "$APP_DIR/node_agent/app.py" || die "Redundant Node failback helper remains"
grep -q 'identity-compact-row' "$APP_DIR/app/templates/channel_form.html" || die "Main single-line identity controls missing"
grep -q 'v1.11.63 compact single-line channel editor groups' "$APP_DIR/app/static/style.css" || die "Main compact encoding grid CSS missing"
grep -q 'v1.11.63 compact single-line Node channel editor groups' "$APP_DIR/node_agent/app.py" || die "Node compact encoding grid CSS missing"
! grep -q 'Primary source' "$APP_DIR/app/templates/channel_form.html" || die "Main source-role caption remains"
! grep -q 'Backup source' "$APP_DIR/app/templates/channel_form.html" || die "Main backup-role caption remains"
! grep -q 'Primary source' "$APP_DIR/node_agent/app.py" || die "Node source-role caption remains"
! grep -q 'Backup source' "$APP_DIR/node_agent/app.py" || die "Node backup-role caption remains"
! grep -q 'Only these three modes are shown' "$APP_DIR/app/templates/channel_form.html" || die "Main video helper remains"
grep -q 'total_max_connections' "$APP_DIR/app/models.py" || die "Node total connection capacity model missing"
grep -q 'MAIN_TOTAL_CONNECTIONS_KEY' "$APP_DIR/app/main.py" || die "Main total connection capacity missing"
grep -q 'playlist_category_order' "$APP_DIR/node_agent/app.py" || die "Node playlist hierarchy support missing"
grep -q 'run_node_admin_command_over_ssh' "$APP_DIR/app/ssh_installer.py" || die "SSH Node administration support missing"
grep -q 'data-node-manage-search' "$APP_DIR/node_agent/app.py" || die "Node channel search missing"
grep -Fq 'name="q"' "$APP_DIR/app/templates/channels.html" || die "Main server-side channel search control missing"
grep -q 'data-sidebar-toggle' "$APP_DIR/app/templates/base.html" || die "Collapsible sidebar toggle missing"
grep -q 'data-category-rename-open' "$APP_DIR/app/templates/categories.html" || die "Category rename trigger missing"
grep -q 'data-category-rename-modal' "$APP_DIR/app/templates/categories.html" || die "Category rename modal missing"
grep -q '\.category-rename-overlay' "$APP_DIR/app/static/style.css" || die "Category rename modal CSS missing"
grep -q 'initCategoryRenameModal' "$APP_DIR/app/static/app.js" || die "Category rename modal JS missing"
grep -q 'category-channel-actions' "$APP_DIR/app/templates/category_channels.html" || die "Category channel actions markup missing"
grep -q 'category-channel-order-page \.category-channel-actions' "$APP_DIR/app/static/style.css" || die "Category channel header actions CSS missing"
grep -q 'runtime-alert-dot.has-error' "$APP_DIR/app/static/style.css" || die "Clickable orange error indicator styling missing"
grep -q 'persistent Main warning icon without tooltip pseudo-element collision' "$APP_DIR/app/static/style.css" || die "Persistent Main warning icon styling missing"
grep -q 'persistent Node warning icon without tooltip pseudo-element collision' "$APP_DIR/node_agent/app.py" || die "Persistent Node warning icon styling missing"
grep -q 'aria-hidden=\"true\">!</span>' "$APP_DIR/node_agent/app.py" || die "Node warning icon element missing"
grep -q 'channel_errors_json' "$APP_DIR/app/main.py" || die "Channel error history API missing"
grep -q 'effective_status = str(runtime.get("status") or channel.status or "unknown")' "$APP_DIR/app/main.py" || die "Main effective runtime error-state guard missing"
grep -q 'current_error = runtime_error if status_name in {"error", "degraded", "starting", "restarting"} else ""' "$APP_DIR/node_agent/app.py" || die "Node effective runtime error-state guard missing"
grep -q 'data-error-endpoint="/channels/' "$APP_DIR/app/templates/channels.html" || die "Channel stream-log trigger missing"
grep -q 'const channelLogModal' "$APP_DIR/app/static/app.js" || die "Channel stream-log modal JavaScript missing"
grep -q "const sidebarStorageKey = 'streamforge.sidebar.collapsed'" "$APP_DIR/app/static/app.js" || die "Sidebar state persistence missing"
grep -q 'server-cluster' "$APP_DIR/app/templates/channels.html" || die "Multi-node server summary missing"
grep -q 'compact channel control table' "$APP_DIR/app/static/style.css" || die "Compact channel table styling missing"
grep -q 'node-channel-control-table' "$APP_DIR/node_agent/app.py" || die "Compact Node channel table missing"
grep -q 'data-node-card-search' "$APP_DIR/app/templates/nodes.html" || die "Instant Node-card filtering missing"
grep -q 'data-system-uptime' "$APP_DIR/app/templates/dashboard.html" || die "Main system uptime card missing"
grep -q 'dashboard_live_statuses' "$APP_DIR/app/main.py" || die "Main live-only dashboard filter missing"
grep -q 'No live channels are running.' "$APP_DIR/app/templates/dashboard.html" || die "Main live-only dashboard empty state missing"
grep -Fq 'is_live = _node_channel_delivery_status(config, status) == "up"' "$APP_DIR/node_agent/app.py" || die "Node playable-only dashboard filter missing"
grep -q 'data-runtime-live=\"1\"' "$APP_DIR/node_agent/app.py" || die "Node live dashboard row marker missing"
grep -q 'desired_running' "$APP_DIR/app/models.py" || die "Desired-state model missing"
grep -q 'restore_desired_channels' "$APP_DIR/app/node_manager.py" || die "Startup restore logic missing"
grep -q 'business_name' "$APP_DIR/app/templates/viewer_sessions.html" || die "Session GeoIP UI missing"
grep -q 'COUNTRY_DB_FILE' "$APP_DIR/node_agent/app.py" || die "Node Country database support missing"
grep -q 'self._geo_cache.clear()' "$APP_DIR/node_agent/app.py" || die "Node access-policy sync fix missing"
! grep -q 'self._asn_cache.clear()' "$APP_DIR/node_agent/app.py" || die "Broken Node access-policy cache reference remains"
python3 - "$APP_DIR/app/templates/nodes.html" "$APP_DIR/app/static/style.css" <<'PY129_NODE_METRICS'
from pathlib import Path
import sys
html = Path(sys.argv[1]).read_text(encoding="utf-8")
css = Path(sys.argv[2]).read_text(encoding="utf-8").replace(" ", "").replace("\n", "")
required = [
    ">Channels<", ">Up<", ">Down<", ">Online users<", ">Uptime<",
    ">CPU<", ">Memory<", ">Download<", ">Upload<",
]
missing = [label for label in required if label not in html]
if missing:
    raise SystemExit("Node metrics labels missing: " + ", ".join(missing))
if ".node-metrics{grid-template-columns:repeat(9,minmax(0,1fr))}" not in css:
    raise SystemExit("Nine-column Node metrics grid missing")
PY129_NODE_METRICS
grep -q 'session_id=session_id' "$APP_DIR/app/main.py" || die "Session-aware connection enforcement missing"
grep -q 'xtream_password_enc' "$APP_DIR/app/models.py" || die "Encrypted Xtream credential storage missing"
grep -q 'Category order' "$APP_DIR/app/templates/categories.html" || die "Category order UI missing"
grep -q 'category-page-grid' "$APP_DIR/app/templates/categories.html" || die "Category page grid markup missing"
grep -Fq '.category-page-grid{' "$APP_DIR/app/static/style.css" && grep -Fq '.category-management-column{' "$APP_DIR/app/static/style.css" && grep -Fq '.category-order-panel{' "$APP_DIR/app/static/style.css" || die "Category page layout CSS missing"
grep -q '_NODE_UPDATE_JOBS' "$APP_DIR/app/main.py" || die "Background Node update support missing"
grep -q 'def _run_node_api_update' "$APP_DIR/app/main.py" || die "Background API Node update missing"
grep -q 'data-update-progress' "$APP_DIR/app/templates/node_update.html" || die "Live Node update progress UI missing"
grep -q 'Live update log' "$APP_DIR/app/templates/node_update.html" || die "Live Node update log UI missing"
grep -q 'progress=lambda phase' "$APP_DIR/app/main.py" || die "SSH update progress callback missing"
grep -q 'node-channel-control-table{width:100%;min-width:1428px' "$APP_DIR/node_agent/app.py" || die "Unified Node channel UI styling missing"
grep -q 'summary::marker' "$APP_DIR/node_agent/app.py" || die "Node status disclosure marker fix missing"
grep -q 'desktop and mobile' "$APP_DIR/node_agent/app.py" || die "Node desktop CSS scope fix missing"
grep -q 'node-channel-logo' "$APP_DIR/node_agent/app.py" || die "Node channel logo row missing"
grep -q '<th class="node-main-id">ID</th>' "$APP_DIR/node_agent/app.py" || die "Node channel ID column missing"
grep -q 'node-more-menu' "$APP_DIR/node_agent/app.py" || die "Node channel more menu missing"
grep -q 'node table cell structure fix' "$APP_DIR/node_agent/app.py" || die "Node table structure fix CSS missing"
grep -q 'node-channel-log-overlay' "$APP_DIR/node_agent/app.py" || die "Node stream-log modal styling missing"
grep -q '\.channel-log-current\[hidden\]{display:none!important}' "$APP_DIR/app/static/style.css" || die "Main hidden current-error banner fix missing"
grep -q '\.node-channel-log-current\[hidden\]{display:none!important}' "$APP_DIR/node_agent/app.py" || die "Node hidden current-error banner fix missing"
grep -q 'data-node-channel-log-close' "$APP_DIR/node_agent/app.py" || die "Node stream-log close control missing"
grep -q 'data-node-channel-log-pages' "$APP_DIR/node_agent/app.py" || die "Node stream-log pagination missing"
! grep -q '\.node-runtime b{' "$APP_DIR/node_agent/app.py" || die "Over-broad Node runtime badge selector remains"
! grep -q '\.node-runtime span{' "$APP_DIR/node_agent/app.py" || die "Over-broad Node runtime status selector remains"
grep -q '\.node-runtime>span{' "$APP_DIR/node_agent/app.py" || die "Scoped Node runtime status selector missing"
grep -q '\.node-runtime>b{' "$APP_DIR/node_agent/app.py" || die "Scoped Node runtime uptime selector missing"
grep -q 'node-channel-identity-cell' "$APP_DIR/node_agent/app.py" || die "Node identity cell wrapper missing"
grep -q 'node-dashboard-channel-table' "$APP_DIR/node_agent/app.py" || die "Node dashboard channel table component missing"
grep -q 'node-col-channel' "$APP_DIR/node_agent/app.py" || die "Node fixed column layout missing"
! grep -q 'channel-logo-fallback' "$APP_DIR/app/templates/channels.html" || die "Main channel logo placeholder remains"
! grep -q 'channel-logo-fallback' "$APP_DIR/app/templates/dashboard.html" || die "Dashboard channel logo placeholder remains"
! grep -q 'else f.*node-channel-logo-fallback' "$APP_DIR/node_agent/app.py" || die "Node channel logo placeholder remains"
grep -Fq '.main-channel-control-table .main-status-cell>.channel-runtime' "$APP_DIR/app/static/style.css" || die "Main centered runtime indicator styling missing"
grep -q 'persistent Main warning icon without tooltip pseudo-element collision' "$APP_DIR/app/static/style.css" || die "Persistent Main runtime indicator styling missing"
grep -q 'summary.runtime-alert-dot.healthy>span::before' "$APP_DIR/app/static/style.css" || die "Main healthy-dot inner element missing"
grep -q "channel.last_error and channel.status in.*data-tooltip=\"Error detected · click for details\"" "$APP_DIR/app/templates/channels.html" || die "Conditional Main channel error tooltip missing"
grep -q "channel.last_error and channel.status in.*data-tooltip=\"Error detected · click for details\"" "$APP_DIR/app/templates/dashboard.html" || grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_ONLINE_ONLY_FIRST_PAINT_V1114' "$APP_DIR/app/main.py" || die "Conditional Main dashboard error tooltip/online-only replacement missing"
! grep -q 'data-tooltip="{{' "$APP_DIR/app/templates/channels.html" || die "Unconditional Main channel tooltip remains"
! grep -q 'data-tooltip="{{' "$APP_DIR/app/templates/dashboard.html" || die "Unconditional Main dashboard tooltip remains"
! grep -q "dataset.tooltip = 'Healthy'" "$APP_DIR/app/static/app.js" || die "Healthy Main runtime tooltip assignment remains"
grep -q "removeAttribute('data-tooltip')" "$APP_DIR/app/static/app.js" || die "Main healthy tooltip cleanup missing"
grep -q 'historical_error_count = int(error_counts.get' "$APP_DIR/app/main.py" || die "Main log-driven warning count missing"
grep -q 'data-dashboard-channel-search' "$APP_DIR/app/templates/dashboard.html" || die "Dashboard instant search markup missing"
grep -q 'v1.11.54 shrink-wrapped Status/Uptime alignment' "$APP_DIR/app/static/style.css" || die "Centered Status/Uptime alignment styling missing"
grep -q 'main-server-cluster' "$APP_DIR/app/templates/channels.html" || die "Compact Main server cluster markup missing"
grep -q 'dashboard-server-inline' "$APP_DIR/app/templates/dashboard.html" || die "Dashboard inline server summary missing"
grep -q 'v1.11.54 shared server badge spacing and dashboard column alignment' "$APP_DIR/app/static/style.css" || die "Dashboard alignment styling missing"
grep -q 'data-dashboard-channel-result' "$APP_DIR/app/templates/dashboard.html" || die "Dashboard search result counter missing"
grep -q 'channel-info-button' "$APP_DIR/app/templates/dashboard.html" || die "Dashboard direct Info button missing"
grep -q 'dashboard-channel-logo' "$APP_DIR/app/templates/dashboard.html" || die "Dashboard channel logo markup missing"
grep -q 'const dashboardChannelSearch' "$APP_DIR/app/static/app.js" || die "Dashboard instant search JavaScript missing"
grep -q 'v1.11.54 dashboard instant search' "$APP_DIR/app/static/style.css" || die "Dashboard search and Info styling missing"
! grep -q '<details class="channel-more-menu"' "$APP_DIR/app/templates/dashboard.html" || die "Dashboard more menu still present"
grep -Fq '"error_count": sum(int(item.get("error_count")' "$APP_DIR/app/node_manager.py" || die "Remote Node warning aggregation missing"
grep -Fq 'item["error_count"] = len(recent)' "$APP_DIR/node_agent/app.py" || die "Node bulk historical warning count missing"
grep -q "indicatorState = hasError ? 'has-error'.*'idle'" "$APP_DIR/app/static/app.js" || die "Main stopped gray indicator logic missing"
grep -q "indicatorState=hasError?'has-error'.*'idle'" "$APP_DIR/node_agent/app.py" || die "Node stopped gray indicator logic missing"
grep -q 'runtime-alert-dot.idle' "$APP_DIR/app/static/style.css" || die "Main stopped gray indicator styling missing"
grep -q 'node-runtime-alert-dot.idle' "$APP_DIR/node_agent/app.py" || die "Node stopped gray indicator styling missing"
grep -q 'persistent Node warning icon without tooltip pseudo-element collision' "$APP_DIR/node_agent/app.py" || die "Persistent Node runtime indicator styling missing"
grep -q 'summary.node-runtime-alert-dot.healthy>span::before' "$APP_DIR/node_agent/app.py" || die "Node healthy-dot inner element missing"
! grep -q "dataset.tooltip='Healthy'" "$APP_DIR/node_agent/app.py" || die "Healthy Node tooltip assignment remains"
grep -q "removeAttribute('data-tooltip')" "$APP_DIR/node_agent/app.py" || die "Node healthy tooltip cleanup missing"
grep -q '\.compact-control.more{' "$APP_DIR/node_agent/app.py" || die "Neutral Node More-button styling missing"
grep -q 'node-more-glyph' "$APP_DIR/node_agent/app.py" || die "Node vertical More glyph missing"
grep -q 'node-more-glyph::before' "$APP_DIR/node_agent/app.py" || die "Centered Node More indicator styling missing"
grep -q 'node-more-glyph::before' "$APP_DIR/node_agent/app.py" || die "CSS-drawn Node More dots missing"
grep -q 'box-shadow:0 -6px 0' "$APP_DIR/node_agent/app.py" || die "Node More three-dot geometry missing"
! grep -q 'aria-label=.*<span>{glyph}</span>' "$APP_DIR/node_agent/app.py" || die "Legacy runtime glyph span remains"
grep -q 'STREAMFORGE_GEOIP_AUTO_UPDATE' "$APP_DIR/scripts/update_geoip_databases.sh" || die "GeoIP auto updater missing"
grep -q 'STREAMFORGE_GEOIP_INHERITED_SECRET_PERMISSION_SAFE_V99R6' "$APP_DIR/scripts/update_geoip_databases.sh" || die "Installed v9.9 r6 GeoIP root-env permission-safe credential loader missing"
grep -q 'STREAMFORGE_GEOIPUPDATE_DEPENDENCY_V99R7' "$APP_DIR/scripts/update_geoip_databases.sh" || die "Installed v9.9 r7 GeoIP updater dependency guard missing"
grep -Fq 'certbot dnsutils geoipupdate' "$APP_DIR/scripts/install.sh" || die "Installed v9.9 r7 fresh Main geoipupdate dependency missing"
grep -Fq 'def _prune_target_backup_archives(target_id: str, keep: int = 20)' "$APP_DIR/app/backup_manager.py" || die "Per-target backup rotation helper missing"
grep -Fq 'rotation = _prune_target_backup_archives(target_id, keep=keep)' "$APP_DIR/app/backup_manager.py" || die "Per-target backup rotation call missing"
grep -Fq 'STREAMFORGE_STRICT_MAX_CONNECTIONS' "$APP_DIR/app/main.py" || die "Main strict max-connections enforcement missing"
grep -Fq 'STREAMFORGE_RESTREAM_MAX_CONNECTIONS' "$APP_DIR/app/main.py" || die "Restream max-connections enforcement missing"
grep -Fq 'def __init__(self, ttl_seconds: int = 5)' "$APP_DIR/app/main.py" || die "Main connection session timeout is not 5 seconds"
grep -Fq 'const syncCanvasSize=(canvas)' "$APP_DIR/app/static/dashboard.js" || die "Responsive dashboard canvas sizing missing"
grep -Fq 'const syncNodeCanvasSize=(canvas)' "$APP_DIR/node_agent/app.py" || die "Responsive Node dashboard canvas sizing missing"
! grep -Fq 'data-node-chart-range' "$APP_DIR/node_agent/app.py" || die "Node dashboard historical range labels still present"
grep -Fq 'node-metrics-chart-title' "$APP_DIR/node_agent/app.py" || die "Node dashboard large-screen chart styling missing"
! grep -Fq 'data-chart-range' "$APP_DIR/app/templates/dashboard.html" || die "Dashboard historical range labels still present"
grep -Fq 'v2.1.171 dashboard metric charts' "$APP_DIR/app/static/style.css" || die "Large-screen metrics chart styling missing"
grep -Fq 'def __init__(self, ttl_seconds: int = 5)' "$APP_DIR/app/viewer_tracking.py" || die "Main viewer session timeout is not 5 seconds"
grep -Fq 'VIEWER_TTL = max(5, int(os.getenv("STREAMFORGE_NODE_VIEWER_TTL", "5")))' "$APP_DIR/node_agent/app.py" || die "Node session timeout is not 5 seconds"
! grep -Fq 'user.user_type != "restream" and not connection_tracker.allow' "$APP_DIR/app/main.py" || die "Restream connection-limit bypass still present"
grep -Fq 'STREAMFORGE_STRICT_MAX_CONNECTIONS' "$APP_DIR/node_agent/app.py" || die "Node strict max-connections enforcement missing"
python3 - "$APP_DIR/app/main.py" <<'PY_LOCAL_BACKUP_ROUTE_LIVE'
import sys
text = open(sys.argv[1], encoding="utf-8").read()
if not (
    text.index('@app.post("/system/backups/local/update"') < text.index('@app.post("/system/backups/{target_id}/update"')
    and text.index('@app.post("/system/backups/local/run"') < text.index('@app.post("/system/backups/{target_id}/run"')
):
    raise SystemExit("Installed local backup routes are shadowed by dynamic target routes")
PY_LOCAL_BACKUP_ROUTE_LIVE
grep -q 'catalog_session_id' "$APP_DIR/app/main.py" || die "Stable catalogue session fix missing"
grep -Eq 'playback_start:[[:space:]]*bool[[:space:]]*=[[:space:]]*False' "$APP_DIR/app/main.py" || die "Playback-start handover fix missing"
grep -q 'trailing HLS segments cannot reopen slots' "$APP_DIR/node_agent/app.py" || die "Node channel-switch handover fix missing"
grep -q 'STREAMFORGE_MAIN_SINGLE_CHANNEL_PLAYBACK_AUTH_V1054' "$APP_DIR/app/main.py" || die "Main single-channel playback auth fast path missing"
grep -q 'STREAMFORGE_MAIN_HLS_CONNECTION_GRACE_V1054' "$APP_DIR/app/main.py" || die "Main HLS connection-reservation grace missing"
grep -q 'STREAMFORGE_ENCRYPTED_CHILD_ROUTE_FASTPATH_V1058' "$APP_DIR/app/main.py" || die "Main encrypted playlist child-route fast path missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_LOG_SESSION_AGE_INLINE_V1060' "$APP_DIR/app/main.py" || die "Installed Main inline Client-log Session age backend missing"
grep -Fq 'STREAMFORGE_MAIN_XTREAM_CLIENT_LOGIN_SESSION_CONTEXT_V1060' "$APP_DIR/app/main.py" || die "Installed Main Xtream client login session context missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_LOG_REPLAY_AGE_V1062' "$APP_DIR/app/main.py" || die "Installed v10.68 repeated-playback inline Session age selector missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_LOG_CUMULATIVE_SESSION_AGE_V1063' "$APP_DIR/app/main.py" || die "Installed v10.68 Main cumulative Client-log Session age missing"
# STREAMFORGE_V1082_WEBPLAYER_FLASH_ERROR_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_FLASH_ERROR_V1082' "$APP_DIR/app/main.py" || die "Installed v11.22 Main clean Web Player login-error flash missing"
grep -Fq '_web_player_flash_redirect(request, "Invalid username or password")' "$APP_DIR/app/main.py" || die "Installed v11.22 Main invalid-login clean redirect missing"
! grep -Fq '_web_player_home_url(request)}?error=Invalid+username+or+password' "$APP_DIR/app/main.py" || die "Installed v11.22 Main still leaks invalid-login error in URL"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_FLASH_ERROR_V1082' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node clean Web Player login-error flash missing"
grep -Fq '_node_web_flash_redirect(request, "Invalid username or password")' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node invalid-login clean redirect missing"
grep -Fq '_node_web_page(user, request, flash_error)' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node root Web Player does not render flash error"
! grep -Fq '_node_web_home_url(request)}?error=Invalid+username+or+password' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node still leaks invalid-login error in URL"
grep -Fq 'STREAMFORGE_NODE_UPDATE_FFMPEG_HANDOFF_V1083' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node FFmpeg update handoff missing"
grep -Fq 'def prepare_update_ffmpeg_handoff' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node update handoff snapshot missing"
grep -Fq 'class AdoptedProcess' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node adopted FFmpeg process handle missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_ZERO_CHANNEL_RESTART_V1083' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node zero-encoder-restart update path missing"
grep -Fq 'preserved_encoders = manager.prepare_update_ffmpeg_handoff' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node API update does not preserve live encoders"
grep -Fq 'STREAMFORGE_MAIN_NODE_SEAMLESS_UPDATE_LEASE_V1083' "$APP_DIR/app/main.py" || die "Installed v11.22 Main seamless Node-update playback routing missing"
grep -Fq '_node_agent_supports_seamless_code_update' "$APP_DIR/app/main.py" || die "Installed v11.22 Main seamless Node-version gate missing"
# STREAMFORGE_V1084_FRESH_NODE_NGINX_BOOTSTRAP_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_FRESH_NGINX_HEALTH_BOOTSTRAP_V1084' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v11.22 fresh-Node authenticated Nginx bootstrap guard missing"
grep -Fq 'def http_front_health_ready(port: int, token: str)' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v11.22 fresh-Node HTTP health verifier missing"
# STREAMFORGE_V111_RELAY_STABILITY_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_RELAY_MISS_PUBLIC_FALLBACK_V111' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v11.22 relay miss fallback missing"
grep -Fq 'try_files $uri @streamforge_relay_fallback;' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v11.22 relay fast-path fallback missing"
grep -Fq 'STREAMFORGE_MAIN_INTERNAL_RELAY_FALLBACK_V111' "$APP_DIR/app/main.py" || die "Installed v11.22 relay fallback alias bypass missing"
grep -Fq 'STREAMFORGE_MAIN_HLS_BROWSER_SEGMENT_GRACE_V1167' "$APP_DIR/app/ffmpeg.py" || die "Installed v11.67 Main segment retention grace missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_STALL_HEALTH_V111' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node relay-aware watchdog missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_LIVE_SID_EPOCH_BIND_V1073' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node SID+epoch Client-log Session age missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_ONE_ROW_PER_SID_V1074' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node one-row-per-Live-SID logging missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_CUMULATIVE_SID_AGE_V1074' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node cumulative Live-SID Session age missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_FOLD_SUCCESS_SID_V1075' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node successful Client-log SID folding missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_RAW_SID_DEDUPE_V1075' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node raw Client-log SID dedupe missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_CLEAR_DEDUPE_RESET_V1075' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node Client-log clear dedupe reset missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_TRANSPORT_BACKOFF_V1064' "$APP_DIR/app/node_manager.py" || die "Installed v10.68 Main Node transport backoff missing"
grep -Fq 'STREAMFORGE_NODE_RECONCILE_TRANSPORT_FAILFAST_V1064' "$APP_DIR/app/node_manager.py" || die "Installed v10.68 Node catalogue transport fail-fast missing"
grep -Fq 'STREAMFORGE_NODE_RECONCILE_TRANSPORT_SHORTCIRCUIT_V1064' "$APP_DIR/app/main.py" || die "Installed v10.68 heartbeat registry transport short-circuit missing"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_RECONCILE_BACKOFF_V1064' "$APP_DIR/app/main.py" || die "Installed v10.68 heartbeat reconcile backoff missing"
[[ "$(grep -Fc 'STREAMFORGE_MAIN_VIEWER_RECONNECT_FIRST_RESET_V1062' "$APP_DIR/app/viewer_tracking.py")" -ge 2 ]] || die "Installed v10.68 viewer reconnect generation reset incomplete"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_SESSION_HISTORY_ASYNC_V1060' "$APP_DIR/app/viewer_tracking.py" || die "Installed Main async retained viewer history missing"
grep -Fq 'STREAMFORGE_MAIN_CLIENT_SESSION_HISTORY_ASYNC_V1060' "$APP_DIR/app/redis_state.py" || die "Installed Main best-effort history Redis isolation missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_INLINE_SESSION_AGE_V1060' "$APP_DIR/node_agent/app.py" || die "Installed Node duplicate playback-row folding missing"
grep -Fq '_main_client_log_duration_states(local_rows, db)' "$APP_DIR/app/main.py" || die "Installed Main existing-row Session age attachment missing"
grep -Fq 'threading.Thread(target=_main_client_session_history_observer_loop' "$APP_DIR/app/main.py" || die "Installed Main control-plane session history observer missing"
grep -Fq "event_logs_snapshot = _node_fold_playback_session_rows(manager.event_logs_snapshot()) if selected_type == 'client'" "$APP_DIR/node_agent/app.py" || die "Installed standalone Node playback-row folding call missing"
grep -Fq 'items = _node_fold_playback_session_rows(manager.event_logs_snapshot())' "$APP_DIR/node_agent/app.py" || die "Installed remote Node playback-row folding call missing"
grep -q 'api.ipinfo.io/lite' "$APP_DIR/app/access_control.py" || die "IPinfo support missing"
grep -q 'GeoIP provider settings' "$APP_DIR/app/templates/node_asn.html" || die "Web GeoIP settings UI missing"
grep -q '/panel-play/' "$APP_DIR/node_agent/app.py" || die "Node HTTP playback route missing"
grep -q 'Auto — IPinfo first, MaxMind fallback' "$APP_DIR/app/templates/node_asn.html" || die "IPinfo-first Auto option missing"
grep -q 'geoip-inline-test' "$APP_DIR/app/templates/node_asn.html" || die "Compact GeoIP lookup UI missing"
grep -q 'setHidden' "$APP_DIR/app/templates/node_asn.html" || die "Provider field-disable logic missing"
grep -q 'auto     -> IPinfo first' "$APP_DIR/app/access_control.py" || die "Main GeoIP provider order fix missing"
grep -q 'auto     -> IPinfo first' "$APP_DIR/node_agent/app.py" || die "Node GeoIP provider order fix missing"
grep -q 'sync_main_users' "$APP_DIR/app/models.py" || die "Main-user node mode model missing"
grep -q 'viewer_session_kill' "$APP_DIR/app/main.py" || die "Viewer kill route missing"
grep -q '/panel/manage/channels' "$APP_DIR/node_agent/app.py" || die "Independent Node channel management UI missing"
grep -q 'local_channel_limit' "$APP_DIR/app/models.py" || die "Per-node local channel limit model missing"
grep -q 'Node-local channel limit reached' "$APP_DIR/node_agent/app.py" || die "Node local-channel quota enforcement missing"
grep -q 'xtream_output_url' "$APP_DIR/node_agent/app.py" || die "Node Xtream output URL support missing"
grep -q 'Node-local channel created' "$APP_DIR/node_agent/app.py" || die "Node local channel CRUD missing"
grep -q 'channel_source_scan' "$APP_DIR/app/main.py" || die "Main source scan endpoint missing"
grep -q 'probe_node_source' "$APP_DIR/node_agent/app.py" || die "Node source scan support missing"
grep -q 'local_users_preserved' "$APP_DIR/node_agent/app.py" || die "Node-local playlist user preservation missing"
grep -q 'data-channel-uptime' "$APP_DIR/node_agent/app.py" || die "Node channel uptime UI missing"
grep -q 'data-uptime' "$APP_DIR/app/templates/channels.html" || die "Main channel uptime UI missing"
grep -q 'value="profile_update"' "$APP_DIR/app/templates/channels.html" || die "Bulk encoding profile UI missing"
grep -q 'if action == "profile_update"' "$APP_DIR/app/main.py" || die "Bulk encoding profile backend missing"
grep -q '/panel/manage/channels/bulk' "$APP_DIR/node_agent/app.py" || die "Node bulk channel controls missing"
grep -q 'data-stream-source-editor' "$APP_DIR/app/templates/channel_form.html" || die "Main compact stream-source editor missing"
grep -q 'v1.11.63 aligned source rows and concise codec labels' "$APP_DIR/app/static/style.css" || die "Aligned Main source-row styling missing"
grep -q 'v1.11.63 aligned Node source rows and concise codec labels' "$APP_DIR/node_agent/app.py" || die "Aligned Node source-row styling missing"
! grep -R -q 'Copy / Passthrough — lowest CPU\|Copy — lowest CPU' "$APP_DIR/app" "$APP_DIR/node_agent/app.py" || die "Verbose copy codec labels remain"
grep -q 'Stream links & source info' "$APP_DIR/node_agent/app.py" || die "Node compact stream-source editor missing"
grep -q '_effective_user_channels' "$APP_DIR/node_agent/app.py" || die "Independent Node dynamic channel catalogue missing"
grep -q 'kill_viewer_session(session_id)' "$APP_DIR/node_agent/app.py" || die "Node session kill fix missing"
grep -q 'independent_mode' "$APP_DIR/node_agent/app.py" || die "Independent Node offline-control mode missing"
grep -q 'node-control/viewers/kill' "$APP_DIR/app/main.py" || die "Main-proxy session kill endpoint missing"
grep -q 'Independent node local catalogue preserved' "$APP_DIR/app/main.py" || die "Independent channel overwrite protection missing"
grep -q '/api/v1/mode/sync' "$APP_DIR/node_agent/app.py" || die "Node mode sync endpoint missing"
grep -q 'state-independent.json' "$APP_DIR/node_agent/app.py" || die "Independent catalogue archive missing"
grep -q 'state-shared.json' "$APP_DIR/node_agent/app.py" || die "Shared catalogue archive missing"
grep -q 'require_independent_node_user' "$APP_DIR/node_agent/app.py" || die "Shared-mode management guard missing"
grep -q 'configured_channels' "$APP_DIR/app/node_manager.py" || die "Shared catalogue reconciliation helper missing"
grep -q 'Independent node access/GeoIP settings preserved locally' "$APP_DIR/app/main.py" || die "Independent settings preservation missing"
grep -q 'def _node_panel_header' "$APP_DIR/node_agent/app.py" || die "Unified Node Panel header missing"
grep -q '.nav-button.active' "$APP_DIR/node_agent/app.py" || die "Active Node navigation state missing"
grep -q 'overflow-x:auto' "$APP_DIR/node_agent/app.py" || die "Responsive Node navigation missing"
grep -q 'catalog_owner: str = "main"' "$APP_DIR/node_agent/app.py" || die "Channel catalogue ownership model missing"
grep -q 'def _prepare_independent_payload' "$APP_DIR/node_agent/app.py" || die "Independent catalogue migration missing"
grep -q 'Keep only Node-owned channels' "$APP_DIR/node_agent/app.py" || die "Shared-to-Independent clone protection missing"
grep -q 'protect_local_channel_from_main_api' "$APP_DIR/node_agent/app.py" || die "Local-channel collision protection missing"
grep -q 'Main Server · read-only' "$APP_DIR/node_agent/app.py" || die "Read-only Main channel UI missing"
grep -q '_prepare_independent_active_payload' "$APP_DIR/node_agent/app.py" || die "Mixed Independent catalogue state support missing"
grep -q 'Choose the full Node catalogue or a compatible Main Server playlist' "$APP_DIR/node_agent/app.py" || die "Independent Main-playlist selector missing"
grep -q 'desired_keys = \[self._channel_key(channel)' "$APP_DIR/app/node_manager.py" || die "Assigned Main channel reconciliation missing"
grep -q 'Upload logo' "$APP_DIR/node_agent/app.py" || die "Main-style Node channel editor missing"
grep -q 'CHANNEL_LOGO_ROOT' "$APP_DIR/node_agent/app.py" || die "Node channel-logo storage missing"
grep -q 'STREAMFORGE_NODE_CHANNEL_LOGO_ROOT' "$APP_DIR/scripts/install_node_agent.sh" || die "Node channel-logo installer setting missing"
grep -q 'class NodeCategoryConfig' "$APP_DIR/node_agent/app.py" || die "Node category registry model missing"
grep -q 'CATEGORY_FILE' "$APP_DIR/node_agent/app.py" || die "Persistent Node category registry missing"
grep -q 'Main Server categories are read-only' "$APP_DIR/node_agent/app.py" || die "Read-only Main category guard missing"
grep -q '_category_sort_value' "$APP_DIR/node_agent/app.py" || die "Node playlist category ordering missing"
grep -q 'sync_category_catalog_to_remote_nodes' "$APP_DIR/app/main.py" || die "Main category synchronization helper missing"
grep -q '_category_grouped_channels' "$APP_DIR/app/main.py" || die "Main playlist category ordering missing"
grep -q '"categories": \[' "$APP_DIR/app/node_manager.py" || die "Node category payload missing"
grep -q 'category_order: Mapped\[str\]' "$APP_DIR/app/models.py" || die "Playlist category-order model missing"
grep -q 'data-main-playlist-hierarchy-form' "$APP_DIR/app/templates/playlist_order.html" || die "Main playlist hierarchy editor missing"
grep -q 'category_ranks' "$APP_DIR/node_agent/app.py" || die "Node user category hierarchy missing"

grep -q 'VIDEO_DIMENSION_RE' "$APP_DIR/app/ffmpeg.py" || die "Main live resolution detection missing"
grep -q 'pending_fps' "$APP_DIR/app/ffmpeg.py" || die "Main live FPS detection missing"
grep -q 'VIDEO_DIMENSION_RE' "$APP_DIR/node_agent/app.py" || die "Node live resolution detection missing"
grep -q 'node_channel_errors_json' "$APP_DIR/node_agent/app.py" || die "Node clickable error history API missing"
grep -q 'node-runtime-alert-dot.has-error' "$APP_DIR/node_agent/app.py" || die "Node orange error indicator styling missing"
grep -q 'data-channel-fps' "$APP_DIR/node_agent/app.py" || die "Node live FPS UI missing"
grep -q 'data-fps' "$APP_DIR/app/templates/channels.html" || die "Main live FPS UI missing"
grep -Fq '.stream-summary-cell [data-resolution]{color:#eef5fb;font-weight:800}' "$APP_DIR/app/static/style.css" && \
  grep -Fq '.stream-summary-cell [data-bitrate]{color:#9fb3c7}' "$APP_DIR/app/static/style.css" || die "Aligned Main metric styling missing"

grep -q 'def _probe_live_metadata' "$APP_DIR/app/ffmpeg.py" || die "Main source metadata probe missing"
grep -q 'def probe_runtime_metadata' "$APP_DIR/node_agent/app.py" || die "Node source metadata probe missing"
grep -q 'def source_endpoint' "$APP_DIR/app/main.py" || die "Main source-host formatter missing"
grep -q 'def _source_endpoint' "$APP_DIR/node_agent/app.py" || die "Node source-host formatter missing"
grep -q '.stream-summary-cell \[data-resolution\]' "$APP_DIR/app/static/style.css" || die "Main resolution styling missing"
install -m 0644 "$APP_DIR/deploy/streamforge-geoip-update.service" /etc/systemd/system/streamforge-geoip-update.service
install -m 0644 "$APP_DIR/deploy/streamforge-geoip-update.timer" /etc/systemd/system/streamforge-geoip-update.timer
# Adapt the packaged /opt path when the application uses a custom directory.
sed -i "s#/opt/streamforge#$APP_DIR#g" /etc/systemd/system/streamforge-geoip-update.service
grep -q 'def runtime_snapshots' "$APP_DIR/app/node_manager.py" || die "Bulk runtime status support missing"
grep -q '/api/v1/channel-statuses' "$APP_DIR/node_agent/app.py" || die "Node bulk status endpoint missing"
grep -q 'data-category-channel-order-form' "$APP_DIR/app/templates/category_channels.html" || die "Category channel order UI missing"
grep -q 'Custom playlist hierarchy' "$APP_DIR/app/templates/playlist_form.html" || die "Main playlist hierarchy shortcut missing"
grep -q '@app.get("/playlists/{playlist_id}/order"' "$APP_DIR/app/main.py" || die "Main playlist hierarchy GET route missing"
grep -q '@app.post("/playlists/{playlist_id}/order"' "$APP_DIR/app/main.py" || die "Main playlist hierarchy save route missing"
grep -q 'playlist.category_order' "$APP_DIR/app/main.py" || die "Playlist-specific category ordering missing"
grep -q 'playlist.channel_order' "$APP_DIR/app/main.py" || die "Playlist-specific channel ordering missing"
grep -q 'data-main-playlist-category-list' "$APP_DIR/app/templates/playlist_order.html" || die "Main playlist category list missing"
grep -q 'data-main-playlist-channel-groups' "$APP_DIR/app/templates/playlist_order.html" || die "Main playlist channel groups missing"
grep -q 'const mainPlaylistHierarchyForm' "$APP_DIR/app/static/app.js" || die "Main playlist hierarchy JavaScript missing"
grep -Fq '.main-playlist-category-order-list{' "$APP_DIR/app/static/style.css" && grep -Fq '.playlist-hierarchy-actions{' "$APP_DIR/app/static/style.css" || die "Main playlist hierarchy styling missing"
grep -q 'data-main-playlist-content-mode' "$APP_DIR/app/templates/playlist_form.html" || die "Main playlist content-mode selector missing"
grep -q 'All enabled Main channels' "$APP_DIR/app/templates/playlist_form.html" || die "All-enabled Main playlist option missing"
grep -q 'data-channel-search=' "$APP_DIR/app/templates/playlist_form.html" || die "Playlist search index missing"
grep -q 'channel-search-hidden' "$APP_DIR/app/static/app.js" || die "Playlist live-search visibility logic missing"
grep -Fq '.channel-checks>label[hidden],.channel-checks>label.channel-search-hidden{display:none!important}' "$APP_DIR/app/static/style.css" || die "Playlist live-search hidden-card CSS missing"
grep -Fq '[data-main-playlist-custom-channels][hidden]{display:none!important}' "$APP_DIR/app/static/style.css" || die "Main playlist content-mode styling missing"
grep -q 'all_enabled_channels: Mapped\[bool\]' "$APP_DIR/app/models.py" || die "Main playlist dynamic mode model missing"
grep -q 'def enabled_main_playlist_channels' "$APP_DIR/app/main.py" || die "Dynamic enabled Main catalogue helper missing"
grep -q 'syncMainPlaylistContentMode' "$APP_DIR/app/static/app.js" || die "Live Main playlist content-mode toggle missing"
grep -q '/playlists/{{ playlist.id }}/order' "$APP_DIR/app/templates/users.html" || die "Main playlist Edit order action missing"
grep -q 'ix_channels_sort_order' "$APP_DIR/app/db.py" || die "Channel order schema support missing"
grep -q 'NODE_MORE_MENU_SCRIPT' "$APP_DIR/node_agent/app.py" || die "Viewport-safe Node action menu script missing"
! grep -q '<span class=\"node-more-source\"' "$APP_DIR/node_agent/app.py" || die "Raw source URL remains in Node action menu"
grep -Eq "user[[:space:]]*=[[:space:]]*require_node_panel_user\(request,[[:space:]]*'logs\.view'\)" "$APP_DIR/node_agent/app.py" || die "Shared Node logs access missing"
grep -q 'channel-log-overlay' "$APP_DIR/app/static/style.css" || die "Main channel stream-log modal styling missing"
grep -q 'data-channel-log-pages' "$APP_DIR/app/static/app.js" || die "Main channel stream-log pagination missing"
grep -q 'channel_errors_clear' "$APP_DIR/app/main.py" || die "Main per-channel log clearing endpoint missing"
grep -q 'node-channel-log-overlay' "$APP_DIR/node_agent/app.py" || die "Node channel stream-log modal missing"
grep -q 'node_channel_errors_clear' "$APP_DIR/node_agent/app.py" || die "Node per-channel log clearing endpoint missing"
grep -q 'data-node-channel-log-pages' "$APP_DIR/node_agent/app.py" || die "Node channel stream-log pagination missing"
grep -q 'main-channel-control-table' "$APP_DIR/app/templates/channels.html" || die "Main fixed channel table missing"
grep -q 'main-col-channel' "$APP_DIR/app/templates/channels.html" || die "Main channel colgroup missing"
grep -Fq '.main-channel-control-table col.main-col-channel' "$APP_DIR/app/static/style.css" || die "Main channel alignment CSS missing"
grep -Fq '.node-channel-identity:not(:has(.node-channel-logo))' "$APP_DIR/node_agent/app.py" || die "Node no-logo alignment CSS missing"
grep -q 'def dedupe_channel_log_rows' "$APP_DIR/app/main.py" || die "Main channel-log deduplication missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_LOGS_LOCAL_ONLY_V65R9' "$APP_DIR/app/main.py" || die "Main local-only channel-log scope missing"
grep -q 'def channel_logs(self, node: Node' "$APP_DIR/app/node_manager.py" || die "Node channel-log client missing"
grep -q '/api/v1/channels/{key}/logs' "$APP_DIR/node_agent/app.py" || die "Node channel-specific log API missing"
grep -q 'server_name = manager.node_name' "$APP_DIR/node_agent/app.py" || die "Actual Node log attribution missing"
grep -Fq 'channel.last_error = aggregate["last_error"] or None' "$APP_DIR/app/node_manager.py" || die "Recovered runtime stale-error cleanup missing"
! grep -q '\[entry.message, entry.details\]' "$APP_DIR/app/static/app.js" || die "Duplicate Main expanded-log message remains"
! grep -q '\[entry.message,entry.details\]' "$APP_DIR/node_agent/app.py" || die "Duplicate Node expanded-log message remains"
grep -q 'collapse_aggregate_channel_log_rows' "$APP_DIR/app/main.py" || die "Aggregate channel log fallback missing"
grep -q 'data-channel-log-open' "$APP_DIR/app/templates/channels.html" || die "Main Stream logs menu action missing"
grep -q 'data-node-channel-log-open' "$APP_DIR/node_agent/app.py" || die "Node Stream logs menu action missing"
grep -q 'main-head-channel' "$APP_DIR/app/templates/channels.html" || die "Aligned Main channel headers missing"
grep -Fq 'STREAMFORGE_DASHBOARD_LIVE_CHANNEL_ID_ORDER_V65R10' "$APP_DIR/app/main.py" || die "Dashboard Channel-ID order missing"
grep -Fq 'STREAMFORGE_CHANNEL_TABLE_SORT_V65R10' "$APP_DIR/app/static/app.js" || die "Main Channels clickable table sorting missing"
grep -Fq 'STREAMFORGE_CHANNEL_TABLE_SORT_HEADERS_V65R10' "$APP_DIR/app/templates/channels.html" || die "Main Channels sortable headers missing"
grep -q 'channel-info-button icon-only' "$APP_DIR/app/templates/dashboard.html" || die "Icon-only Dashboard info action missing"
grep -q 'v1.11.54 exact Main channel header/data alignment' "$APP_DIR/app/static/style.css" || die "Main channel header alignment CSS missing"
grep -q 'v1.11.58 exact Node channel header/data alignment' "$APP_DIR/node_agent/app.py" || die "Node channel header alignment CSS missing"
! grep -q 'channel-source-preview' "$APP_DIR/app/templates/channels.html" || die "Raw Main source preview remains in channel menu"
grep -q 'channel_category_links = Table' "$APP_DIR/app/models.py" || die "Multi-category association model missing"
grep -q 'name="category_ids"' "$APP_DIR/app/templates/channel_form.html" || die "Main multi-category selector missing"
grep -q 'categories: list\[str\] = Field(default_factory=list)' "$APP_DIR/node_agent/app.py" || die "Node multi-category payload support missing"
grep -q 'v1.11.63 right-aligned source actions and multi-category picker' "$APP_DIR/app/static/style.css" || die "Main source alignment/category picker CSS missing"
grep -q 'v1.11.63 Node source actions and multi-category support' "$APP_DIR/node_agent/app.py" || die "Node source alignment/category support missing"
! grep -R -q 'Auto AAC — browser safe' "$APP_DIR/app" "$APP_DIR/node_agent/app.py" || die "Obsolete Auto AAC option remains"
! grep -q 'data-source-use' "$APP_DIR/app/static/app.js" || die "Main source Use button remains"
! grep -q 'data-source-use' "$APP_DIR/node_agent/app.py" || die "Node source Use button remains"
grep -q 'video_codec: Mapped\[str\].*default="copy"' "$APP_DIR/app/models.py" || die "Default video Copy/Passthrough missing"
grep -q 'audio_codec: Mapped\[str\].*default="copy"' "$APP_DIR/app/models.py" || die "Default audio Copy/Passthrough missing"
grep -q 'source_program_ids: Mapped\[Optional\[str\]\]' "$APP_DIR/app/models.py" || die "Per-source program model missing"
grep -q 'source_program_ids' "$APP_DIR/app/db.py" || die "Per-source program schema support missing"
grep -q 'name="source_program_ids"' "$APP_DIR/app/templates/channel_form.html" || die "Main per-source Program/Service selector missing"
! grep -q 'data-program-select' "$APP_DIR/app/templates/channel_form.html" || die "Obsolete channel-level program selector remains"
grep -q 'def active_program_id' "$APP_DIR/app/ffmpeg.py" || die "Main active-source program mapping missing"
grep -q 'input_program_ids: list\[int | None\]' "$APP_DIR/node_agent/app.py" || die "Node per-source program config missing"
grep -q 'def config_program_ids' "$APP_DIR/node_agent/app.py" || die "Node active-source program mapping missing"
grep -q 'name=source_program_ids' "$APP_DIR/node_agent/app.py" || die "Node-local per-source Program/Service selector missing"
grep -q 'v1.11.67 source-specific MPTS program selectors' "$APP_DIR/app/static/style.css" || die "Main per-source selector styling missing"
grep -q 'v1.11.67 Node source-specific MPTS program selectors' "$APP_DIR/node_agent/app.py" || die "Node per-source selector styling missing"

grep -q 'v1.11.67 unified dropdown styling, status filter, and bulk relay tools' "$APP_DIR/app/static/style.css" || die "Unified Main dropdown/status/relay CSS missing"
grep -Fq 'name="status"' "$APP_DIR/app/templates/channels.html" || die "Main server-side status filter missing"
grep -q 'value="relay_set"' "$APP_DIR/app/templates/channels.html" || die "Bulk Local relay action missing"
grep -q 'if action in {"relay_set", "relay_clear"}' "$APP_DIR/app/main.py" || die "Bulk Local relay backend missing"
grep -q 'data-node-status-filter' "$APP_DIR/node_agent/app.py" || die "Node Up/Down filter missing"
grep -q 'data-node-http-action' "$APP_DIR/node_agent/app.py" || die "Dynamic Node HTTP action missing"
grep -q 'v1.11.67 unified Node dropdowns, Up/Down filtering, and dynamic HTTP action' "$APP_DIR/node_agent/app.py" || die "Unified Node dropdown/status CSS missing"
grep -q 'v1.11.67 unified search fields and clean logout icon' "$APP_DIR/app/static/style.css" || die "Unified search CSS missing"
grep -q 'node-signout-icon' "$APP_DIR/node_agent/app.py" || die "Node logout icon missing"
grep -q 'filterUserChannels' "$APP_DIR/app/static/app.js" || die "Unified user/playlist search logic missing"
grep -q 'filterCategoryChannels' "$APP_DIR/app/templates/category_channels.html" || die "Category channel search logic missing"
grep -q 'nodes-head-actions' "$APP_DIR/app/templates/nodes.html" || die "Node header action row markup missing"
grep -q 'v1.11.68 single-line Node page header actions' "$APP_DIR/app/static/style.css" || die "Node header action alignment CSS missing"
! grep -q 'Playlist delivery' "$APP_DIR/app/templates/user_form.html" || die "Read-only Main playlist delivery field remains"
grep -q 'Main Panel load balancing' "$APP_DIR/app/templates/user_form.html" || die "Main per-user load-balancing section missing"
grep -q 'name="load_balance_enabled"' "$APP_DIR/app/templates/user_form.html" || die "Main load-balancing control missing"
grep -q 'name="node_ids"' "$APP_DIR/app/templates/user_form.html" || die "Main allowed-node controls missing"
grep -q '<option value="0" {% if not user or not user.playlist_id %}selected{% endif %}>Custom channel selection</option>' "$APP_DIR/app/templates/user_form.html" || die "Explicit Custom playlist-profile option missing"
grep -q 'data-custom-channel-options {% if user and user.playlist_id %}hidden aria-hidden="true"{% else %}aria-hidden="false"{% endif %}' "$APP_DIR/app/templates/user_form.html" || die "Server-rendered Custom channel visibility missing"
grep -q 'const isCustomProfile = () =>' "$APP_DIR/app/static/app.js" || die "Custom playlist-profile detector missing"
grep -q "value === '0'" "$APP_DIR/app/static/app.js" || die "Explicit Custom playlist-profile value is not handled"
grep -Fq "window.addEventListener('pageshow', updatePlaylistProfileChannels)" "$APP_DIR/app/static/app.js" || die "Playlist-profile browser restore synchronization missing"
grep -Fq 'customSection.hidden = !customSelected' "$APP_DIR/app/static/app.js" || die "Reliable custom/profile toggle missing"
grep -q 'def online_user_channels' "$APP_DIR/app/main.py" || die "Main online-only catalogue filter missing"
# STREAMFORGE_V100_R2_CATALOG_ASSERTION_COMPAT: v10.6 routes catalogue
# readiness through playback_candidate_nodes(). Load-balanced users still keep
# their configured allowed-node pool, while Static/Strict targets may fall back.
sed -n '/def online_user_channels/,/^def /p' "$APP_DIR/app/main.py" | grep -F 'permitted = playback_candidate_nodes(user, channel, pinned=None)' >/dev/null || die "Online catalogue candidate routing missing"
sed -n '/def playback_candidate_nodes/,/^def /p' "$APP_DIR/app/load_balancer.py" | grep -F 'elif bool(user.load_balance_enabled):' >/dev/null || die "Load-balanced user node restriction branch missing"
sed -n '/def playback_candidate_nodes/,/^def /p' "$APP_DIR/app/load_balancer.py" | grep -F 'add(user_allowed_nodes(user, channel))' >/dev/null || die "Load-balanced user allowed-node pool missing"
sed -n '/def online_user_channels/,/^def /p' "$APP_DIR/app/main.py" | grep 'runtime_snapshots' >/dev/null || die "Bulk Main online-state lookup missing"
grep -Fq 'STREAMFORGE_STATIC_ROUTE_CATALOG_FALLBACK_V100' "$APP_DIR/app/main.py" || die "Static-route catalogue fallback missing"
sed -n '/def online_user_channels/,/^def /p' "$APP_DIR/app/main.py" | grep 'bool(item.get("alive"))' >/dev/null || die "Online channel alive filter missing"
grep -q 'def _online_effective_user_channels' "$APP_DIR/node_agent/app.py" || die "Node online-only catalogue filter missing"
! grep -q 'delete from user_nodes' "$APP_DIR/scripts/migrate_v11197.py" || die "Destructive v1.11.97 migration remains"
grep -q 'user node restrictions preserved' "$APP_DIR/scripts/migrate_v11197.py" || die "Non-destructive v1.11.97 compatibility migration missing"
grep -q 'class NodePlaylistConfig' "$APP_DIR/node_agent/app.py" || die "Node playlist profile model missing"
grep -q 'playlist_profile_id: str = ""' "$APP_DIR/node_agent/app.py" || die "Node playlist-user profile assignment missing"
grep -q '@app.get("/panel/playlists"' "$APP_DIR/node_agent/app.py" || die "Node playlist management page missing"
grep -q '@app.post("/panel/playlists/new"' "$APP_DIR/node_agent/app.py" || die "Node playlist creation endpoint missing"
grep -q '/panel/manage/categories/reorder' "$APP_DIR/node_agent/app.py" || die "Node category reorder endpoint missing"
grep -q 'category_order_overrides' "$APP_DIR/node_agent/app.py" || die "Persistent Node category override missing"
grep -q 'Node order does not sync back to the Main Panel' "$APP_DIR/node_agent/app.py" || die "Node-only category order notice missing"
grep -q 'api_urls: Mapped\[Optional\[str\]\]' "$APP_DIR/app/models.py" || die "Multi-URL Node API model missing"
grep -q 'playlist_urls: Mapped\[Optional\[str\]\]' "$APP_DIR/app/models.py" || die "Multi-URL Node playlist model missing"
grep -q 'access_slug: Mapped\[Optional\[str\]\]' "$APP_DIR/app/models.py" || die "Node access slug model missing"
! grep -q '>Panel/API slug<' "$APP_DIR/app/templates/node_form.html" || die "Standalone Main Panel/API slug field remains"
! grep -q '>Playlist/App slug<' "$APP_DIR/app/templates/node_form.html" || die "Standalone Main Playlist/App slug field remains"
! grep -q 'name="playlist_access_slug"' "$APP_DIR/app/templates/node_form.html" || die "Standalone Main Playlist/App slug input remains"
! grep -q '<label>Panel/API slug' "$APP_DIR/node_agent/app.py" || die "Standalone Node Panel/API slug field remains"
! grep -q '<label>Playlist/App slug' "$APP_DIR/node_agent/app.py" || die "Standalone Node Playlist/App slug field remains"
python3 - "$APP_DIR/app/main.py" "$APP_DIR/node_agent/app.py" <<'PY_VALIDATE_ALIAS'
import ast
import sys
from pathlib import Path

main_path = Path(sys.argv[1])
node_path = Path(sys.argv[2])
main_tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
node_tree = ast.parse(node_path.read_text(encoding="utf-8"), filename=str(node_path))

def top_function(tree, name):
    return next((item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name), None)

main_normalizer = top_function(main_tree, "normalize_node_access_urls")
main_alias_router = top_function(main_tree, "route_and_enforce_main_access_aliases")
if main_normalizer is None or main_alias_router is None:
    raise SystemExit("Main per-alias URL normalization/routing functions are missing")
main_source = ast.get_source_segment(main_path.read_text(encoding="utf-8"), main_normalizer) or ""
if "explicit_slug" not in main_source or "urlsplit(urls[0]).path" not in main_source:
    raise SystemExit("Main per-alias URL normalizer is incomplete")

manager = next((item for item in node_tree.body if isinstance(item, ast.ClassDef) and item.name == "AgentManager"), None)
if manager is None:
    raise SystemExit("Node AgentManager class is missing")
node_normalizer = next((item for item in manager.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "normalize_access_urls"), None)
if node_normalizer is None:
    raise SystemExit("Node per-alias URL normalizer is missing")
node_text = node_path.read_text(encoding="utf-8")
node_source = ast.get_source_segment(node_text, node_normalizer) or ""
if "explicit_slug" not in node_source or "urlsplit(result[0]).path" not in node_source:
    raise SystemExit("Node per-alias URL normalizer is incomplete")
if "_CURRENT_PANEL_PREFIX" not in node_text:
    raise SystemExit("Per-request Node alias routing state is missing")
print("per-alias Main/Node URL validation ok")
PY_VALIDATE_ALIAS
grep -q 'playlist_access_slug: Mapped' "$APP_DIR/app/models.py" || die "Playlist/App slug model missing"
grep -q 'Playlist/App access URLs' "$APP_DIR/app/templates/node_form.html" || die "Main Playlist/App access URL form missing"
grep -q 'sync_node_registries_background' "$APP_DIR/app/main.py" || die "Background Node synchronization missing"
grep -q 'previous_access_urls' "$APP_DIR/app/main.py" || die "Previous Node access URL capture missing"
grep -q 'connect_urls: list\[str\]' "$APP_DIR/app/node_manager.py" || die "Old-to-new Node access URL bootstrap missing"
grep -q 'Access settings sync failed through every configured URL' "$APP_DIR/app/node_manager.py" || die "Node access URL fallback missing"
grep -q 'def node_url_candidates' "$APP_DIR/app/node_manager.py" || die "Direct Node listener recovery missing"
grep -q 'def health_at' "$APP_DIR/app/node_manager.py" || die "Exact configured URL verification missing"
grep -q 'fallback_port: int | None = None' "$APP_DIR/app/main.py" || die "Main listener-port preservation missing"
grep -q 'fallback_port: int = 0' "$APP_DIR/node_agent/app.py" || die "Node listener-port preservation missing"
grep -q 'panel_access_sync_pending' "$APP_DIR/app/main.py" || die "Fast Panel/API drift reporting missing"
grep -q 'stream_access_sync_pending' "$APP_DIR/app/main.py" || die "Fast Playlist/App drift reporting missing"
grep -q 'stream_slug: str' "$APP_DIR/node_agent/app.py" || die "Independent Node stream slug payload missing"
grep -q 'active_prefix = stream_prefix if kind == "stream" and stream_match else panel_prefix' "$APP_DIR/node_agent/app.py" || die "Independent per-alias Node route-prefix selection missing"
grep -Fq 'STREAMFORGE_NODE_SETTINGS_SAFE_RETURN_V53' "$APP_DIR/node_agent/app.py" || die "Safe Node settings redirect missing"
grep -Fq 'STREAMFORGE_NODE_LOCAL_SAVE_ACCESS_APPLY_V56' "$APP_DIR/node_agent/app.py" || die "Installed v5.6 Node local Settings listener reconcile missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_MAIN_WEB_ISOLATION_V57' "$APP_DIR/app/main.py" || die "Installed v5.7 Main Web/DB Node-update isolation missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_MAINTENANCE_LEASE_V57' "$APP_DIR/app/main.py" || die "Installed v5.7 Node update maintenance lease missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_MAINTENANCE_ISOLATION_V57' "$APP_DIR/app/node_manager.py" || die "Installed v5.7 Node maintenance routing state missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_HLS_FAIL_FAST_V57' "$APP_DIR/app/node_manager.py" || die "Installed v5.7 Node update HLS fail-fast missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_PLAYBACK_FALLBACK_V57' "$APP_DIR/app/load_balancer.py" || die "Installed v5.7 playback maintenance fallback missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_LOW_CPU_SSH_PACKAGE_V57' "$APP_DIR/app/ssh_installer.py" || die "Installed v5.7 low-CPU SSH package build missing"
grep -Fq 'STREAMFORGE_NODE_SSH_COMPLETE_PAYLOAD_V65R3' "$APP_DIR/app/ssh_installer.py" || die "Installed v6.5-r3 complete SSH Node payload fix missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_CONCISE_ERROR_V65R3' "$APP_DIR/app/main.py" || die "Installed v6.5-r3 concise Node update error handling missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_ERROR_SUMMARY_ONLY_V65R3' "$APP_DIR/app/templates/node_update.html" || die "Installed v6.5-r3 Node update summary-only error UI missing"
grep -Fq 'STREAMFORGE_NODE_SHARED_PORT_PUBLIC_POOL_V65R4' "$APP_DIR/node_agent/app.py" || die "Installed v6.5-r4 Node shared-port Public pool activation fix missing"
grep -Fq 'STREAMFORGE_NODE_ATOMIC_STATE_WRITE_V65R4' "$APP_DIR/node_agent/app.py" || die "Installed v6.5-r4 Node atomic state-write fix missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_LOW_CPU_PACKAGE_V57' "$APP_DIR/app/node_manager.py" || die "Installed v5.7 low-CPU API package build missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_SKIP_REDUNDANT_LOGO_SYNC_V57' "$APP_DIR/app/node_manager.py" || die "Installed v5.7 redundant post-update logo sync suppression missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_LIGHTWEIGHT_CATALOG_SYNC_V57' "$APP_DIR/app/main.py" || die "Installed v5.7 lightweight post-update catalogue sync missing"
grep -Fq 'STREAMFORGE_NODE_LOCAL_SAVE_CONTROL_SYNC_V58' "$APP_DIR/node_agent/app.py" || die "Installed v5.8 Node local-save native control sync missing"
grep -Fq 'background=BackgroundTask(manager.queue_control_access_reconcile_after_response)' "$APP_DIR/node_agent/app.py" || die "Installed v5.8 Node Settings control-sync handoff missing"
grep -Fq 'STREAMFORGE_NGINX_RELAY_FASTPATH_V60' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v6.0 Nginx relay fast path missing"
grep -Fq 'STREAMFORGE_NGINX_RELAY_FASTPATH_V60' "$APP_DIR/app/node_manager.py" || die "Installed v6.0 runtime relay-link maintenance missing"
grep -Fq 'STREAMFORGE_NGINX_RELAY_FASTPATH_V60' /etc/nginx/sites-available/streamforge || die "Installed v6.0 Nginx relay location missing"
grep -Fq 'STREAMFORGE_NODE_CROSS_PROCESS_ACCESS_RECONCILE_V59' "$APP_DIR/node_agent/app.py" || die "Installed v5.9 Node cross-process access reconcile missing"
grep -Fq 'STREAMFORGE_NODE_MULTI_ALIAS_PORT_ROUTING_V59' "$APP_DIR/node_agent/app.py" || die "Installed v5.9 Node multi-alias port routing missing"
grep -Fq 'watch_access_reconcile_requests' "$APP_DIR/node_agent/app.py" || die "Installed v5.9 Node native access watcher missing"
grep -Fq 'streamforge-node-access-reconcile' "$APP_DIR/node_agent/app.py" || die "Installed v5.9 Node access watcher startup missing"
grep -Fq 'STREAMFORGE_MAIN_AUTO_RESTART_VISIBLE_V54' "$APP_DIR/app/ffmpeg.py" || die "Installed v5.4 Main auto-restart visibility missing"
grep -Fq 'STREAMFORGE_MULTI_REMOTE_OUTPUT_URLS_V54' "$APP_DIR/app/main.py" || die "Installed v5.4 multi-output backend missing"
grep -Fq 'STREAMFORGE_MULTI_REMOTE_OUTPUT_TEE_V54' "$APP_DIR/app/ffmpeg.py" || die "Installed v5.4 Main tee output runtime missing"
grep -Fq 'STREAMFORGE_MULTI_REMOTE_OUTPUT_EDITOR_V54' "$APP_DIR/app/templates/channel_form.html" || die "Installed v5.4 multi-output editor missing"
grep -Fq 'STREAMFORGE_NODE_MULTI_REMOTE_OUTPUT_TEE_V54' "$APP_DIR/node_agent/app.py" || die "Installed v5.4 Node tee output runtime missing"
! grep -Fq 'v2.0.1 ultra-low-latency profile uses 1 second.' "$APP_DIR/app/templates/channel_form.html" || die "Installed legacy HLS helper text remains"
! grep -q '<label>Node Agent port' "$APP_DIR/app/templates/node_form.html" || die "Separate Node Agent port field remains"
! grep -q '<label>Playlist/API port' "$APP_DIR/app/templates/node_form.html" || die "Separate Playlist/API port field remains"
grep -q 'async def node_access_back_sync' "$APP_DIR/app/main.py" || die "Node access back-sync endpoint missing"
grep -q '/api/v1/node-access-back-sync/' "$APP_DIR/app/main.py" || die "Node-to-Main access settings endpoint missing"
grep -q 'panel_urls: list\[str\]' "$APP_DIR/node_agent/app.py" || die "Node multi-URL settings payload missing"
grep -q 'def back_sync_access_to_main' "$APP_DIR/node_agent/app.py" || die "Node settings back-sync client missing"
grep -q 'Use a configured Node Panel/API URL' "$APP_DIR/node_agent/app.py" || die "Strict Node access URL enforcement missing"
grep -q 'request.scope\["root_path"\]' "$APP_DIR/node_agent/app.py" || die "Node path slug routing missing"
grep -q 'Node Panel/API access URLs' "$APP_DIR/app/templates/node_form.html" || die "Main multi-URL Node form missing"
grep -q 'def native_node_control_ports' "$APP_DIR/app/node_manager.py" || die "Native Node listener recovery candidates missing"
grep -q 'include_connected_base: bool = False' "$APP_DIR/app/node_manager.py" || die "Connected listener reporting missing"
grep -q 'def normalized_native_control_port' "$APP_DIR/app/main.py" || die "Native/public port separation helper missing"
grep -q 'def urls_on_connected_listener' "$APP_DIR/app/main.py" || die "Broken default-port URL repair missing"
grep -q '"control_port": int(CONTROL_PORT)' "$APP_DIR/node_agent/app.py" || die "Node control-port reporting missing"
grep -q 'STREAMFORGE_NODE_PORT", "80"' "$APP_DIR/node_agent/app.py" || die "Node Agent native port-80 default missing"
grep -q '^PORT="80"$' "$APP_DIR/scripts/install_node_agent.sh" || die "Node installer port-80 default missing"
grep -q 'AmbientCapabilities=CAP_NET_BIND_SERVICE' "$APP_DIR/node_agent/deploy/streamforge-node.service" || die "Node low-port systemd capability missing"
grep -q 'systemctl restart streamforge-node' "$APP_DIR/scripts/install_node_agent.sh" || die "Node installer restart fix missing"
grep -q 'DEFAULT_NODE_CONTROL_PORT = 80' "$APP_DIR/app/node_manager.py" || die "Main Node port-80 default missing"
grep -q 'LEGACY_NODE_CONTROL_PORTS = (8810,)' "$APP_DIR/app/node_manager.py" || die "Legacy 8810 recovery candidate missing"
grep -q 'def ensure_panel_gateways' "$APP_DIR/node_agent/app.py" || die "Managed Node Panel/API listener activation missing"
grep -q 'native_token_control' "$APP_DIR/node_agent/app.py" || die "Native authenticated recovery route missing"
grep -q 'v1.11.82 native port-80 Panel/API default' "$APP_DIR/node_agent/app.py" || die "v1.11.82 Node listener marker missing"
grep -Fq 'If no public port is written, HTTP uses 80 and HTTPS uses 443.' "$APP_DIR/app/templates/node_form.html" || die "Public default-port help text missing"
grep -q 'managed-shared-panel-stream-listener' "$APP_DIR/node_agent/app.py" || die "Shared Panel/Playlist listener support missing"
grep -q 'The configured Panel/API URL is the dashboard itself' "$APP_DIR/node_agent/app.py" || die "Direct Panel URL routing marker missing"
grep -q 'href="{{ node_panel_urls.get(node.id) }}"' "$APP_DIR/app/templates/nodes.html" || die "Exact Open panel URL missing"
grep -Fq 'Public HTTP defaults to 80 and public HTTPS defaults to 443 when no port is written.' "$APP_DIR/app/templates/node_form.html" || die "Playlist/App public default-port help text missing"
grep -Fq '<input type="hidden" name="playlist_port" value="0">' "$APP_DIR/app/templates/node_form.html" || die "Legacy Node playlist-port field is still active"
grep -q 'desired_stream_urls' "$APP_DIR/app/main.py" || die "Automatic Playlist/App URL reconciliation missing"
grep -q 'def current_xtream_output_url' "$APP_DIR/node_agent/app.py" || die "Dynamic Xtream URL rebasing helper missing"
grep -q 'self.refresh_xtream_output_urls()' "$APP_DIR/node_agent/app.py" || die "Xtream URL persistence refresh missing"
grep -q 'playlist_url = manager.current_xtream_output_url(item)' "$APP_DIR/node_agent/app.py" || die "Playlist-user page still renders a stale stored URL"
grep -q 'Main Panel/API access URLs' "$APP_DIR/app/templates/node_form.html" || die "Main Panel/API URL box missing"
grep -q 'Main Playlist/App access URLs' "$APP_DIR/app/templates/node_form.html" || die "Main Playlist/App URL box missing"
grep -q 'def normalize_main_access_urls' "$APP_DIR/app/main.py" || die "Main access URL validation missing"
grep -q 'async def route_and_enforce_main_access_aliases' "$APP_DIR/app/main.py" || die "Exact Main access middleware missing"
grep -q 'def main_playlist_public_base' "$APP_DIR/app/main.py" || die "Main Playlist/App canonical base helper missing"
grep -q 'def local_node_for_public_urls' "$APP_DIR/app/main.py" || die "Read-only Main public URL lookup missing"
grep -A22 -q 'def local_node_for_public_urls' "$APP_DIR/app/main.py" || die "Read-only Main public URL helper is incomplete"
! sed -n '/def main_panel_public_base/,/def main_playlist_public_base/p' "$APP_DIR/app/main.py" | grep 'ensure_local_node' >/dev/null || die "Main Panel public URL lookup still mutates Local Node state"
! sed -n '/def main_playlist_public_base/,/MAX_CHANNEL_LOGO_BYTES/p' "$APP_DIR/app/main.py" | grep 'ensure_local_node' >/dev/null || die "Main Playlist public URL lookup still mutates Local Node state"
grep -q 'self.metrics_lock = threading.RLock()' "$APP_DIR/node_agent/app.py" || die "Node metrics sampler lock missing"
grep -q 'cached_network_metrics' "$APP_DIR/node_agent/app.py" || die "Node cached network-rate state missing"
grep -q 'def _select_network_interfaces' "$APP_DIR/node_agent/app.py" || die "Active Node interface selector missing"
grep -q 'if elapsed >= 0.75' "$APP_DIR/node_agent/app.py" || die "Node rate-sampling interval guard missing"
grep -q "const normalizeFilterText =" "$APP_DIR/app/static/app.js" || die "Filter text normalizer missing"
grep -q "const filterTokens =" "$APP_DIR/app/static/app.js" || die "Filter token helper missing"
grep -q "syncCustomChannels" "$APP_DIR/app/templates/user_form.html" || die "Independent live Playlist profile toggle missing"
grep -q "profile.addEventListener('change', syncCustomChannels)" "$APP_DIR/app/templates/user_form.html" || die "Playlist profile live change binding missing"
systemctl daemon-reload
systemctl enable --now streamforge-geoip-update.timer >/dev/null 2>&1 || true
# STREAMFORGE_PUBLIC_SERVICE_START_V62: prove the isolated worker pool is healthy
# before Nginx starts routing high-volume player/API paths to it.
systemctl enable streamforge-public >/dev/null 2>&1 || true
systemctl reset-failed streamforge-public >/dev/null 2>&1 || true
systemctl restart streamforge-public
public_health_ok=0
for attempt in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:8811/health 2>/dev/null | grep -qx ok; then
    public_health_ok=1
    break
  fi
  sleep 1
done
if [[ "$public_health_ok" -ne 1 ]]; then
  systemctl status streamforge-public --no-pager -l >&2 || true
  journalctl -u streamforge-public -n 100 --no-pager >&2 || true
  die "Public WebPlayer/API service failed health check"
fi
nginx -t
systemctl reload nginx

# v2.1.152: verify the actual deployed application imports before systemd restart.
# The old dependency-only check could pass even when app.main itself had a runtime import error.
(
  cd "$APP_DIR"
  python3 - <<'PY_APP_IMPORT_CHECK'
import app.main
print("deployed app.main import verified")
PY_APP_IMPORT_CHECK
) || {
  journalctl -u "$SERVICE_NAME" -n 80 --no-pager >&2 || true
  die "Deployed Main application import failed"
}

[[ -d /var/lib/streamforge/gunicorn-tmp ]] || die "Gunicorn worker temp directory disappeared before restart"
chown streamforge:streamforge /var/lib/streamforge/gunicorn-tmp
chmod 0750 /var/lib/streamforge/gunicorn-tmp

# STREAMFORGE_MAIN_SUPERVISOR_START_BEFORE_CONTROL_V1115:
# Start the lifecycle owner before Gunicorn so the HTTP worker never falls back
# to owning FFmpeg and desired channels begin restoring immediately.
systemctl daemon-reload
systemctl enable streamforge-channel-supervisor >/dev/null 2>&1 || true
systemctl reset-failed streamforge-channel-supervisor >/dev/null 2>&1 || true
systemctl restart streamforge-channel-supervisor
supervisor_ok=0
for attempt in $(seq 1 30); do
  if runuser -u streamforge -- python3 - "$DATA_DIR/main-channel-supervisor.sock" <<'PY_MAIN_SUPERVISOR_PING'
import json, socket, sys
path=sys.argv[1]
s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(1.0)
try:
    s.connect(path)
    s.sendall(b'{"action":"ping"}\n')
    data=s.recv(4096).split(b'\n',1)[0]
    payload=json.loads(data.decode('utf-8'))
    raise SystemExit(0 if payload.get('ok') and payload.get('pong') else 1)
except Exception:
    raise SystemExit(1)
finally:
    s.close()
PY_MAIN_SUPERVISOR_PING
  then
    supervisor_ok=1
    break
  fi
  sleep 1
done
if [[ "$supervisor_ok" -ne 1 ]]; then
  systemctl status streamforge-channel-supervisor --no-pager -l >&2 || true
  journalctl -u streamforge-channel-supervisor -n 120 --no-pager >&2 || true
  die "Main channel supervisor failed health check"
fi

systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
systemctl restart "$SERVICE_NAME"

health_ok=0
for attempt in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:8800/health 2>/dev/null | grep -qx ok; then
    health_ok=1
    break
  fi
  sleep 1
done

# A stale/failed Gunicorn unit can occasionally survive the first restart.
# Retry once cleanly before declaring the deployment unhealthy.
if [[ "$health_ok" -ne 1 ]]; then
  log "Initial health check did not become ready; retrying Main service once."
  systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
  sleep 2
  systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
  systemctl start "$SERVICE_NAME"
  for attempt in $(seq 1 45); do
    if curl -fsS --max-time 2 http://127.0.0.1:8800/health 2>/dev/null | grep -qx ok; then
      health_ok=1
      break
    fi
    sleep 1
  done
fi

if [[ "$health_ok" -ne 1 ]]; then
  systemctl status "$SERVICE_NAME" --no-pager -l >&2 || true
  journalctl -u "$SERVICE_NAME" -n 120 --no-pager >&2 || true
  die "Health check failed after clean restart retry"
fi

# STREAMFORGE_PUBLIC_POST_DEPLOY_VERIFY_V62
systemctl is-active --quiet streamforge-public || die "Public WebPlayer/API service is not active"
systemctl is-active --quiet streamforge-channel-supervisor || die "Main channel supervisor service is not active"
grep -Fq 'ExecStart=/usr/bin/python3 -m app.main_channel_supervisor' /etc/systemd/system/streamforge-channel-supervisor.service || die "Installed Main channel supervisor unit is stale"
grep -Fq 'streamforge-channel-supervisor.service' "/etc/systemd/system/$SERVICE_NAME.service" || die "Installed Main service supervisor ordering is missing"
curl -fsS --max-time 3 http://127.0.0.1:8811/health | grep -qx ok || die "Public WebPlayer/API health check failed after Main restart"
grep -Fq 'STREAMFORGE_PROCESS_ROLE=public' /etc/systemd/system/streamforge-public.service || die "Installed Public systemd role is missing"
grep -Fq 'STREAMFORGE_PUBLIC_PLANE_NGINX_SPLIT_V62' /etc/nginx/sites-available/streamforge || die "Active Nginx Public routing split is missing"
grep -Fq 'STREAMFORGE_MAIN_ALIAS_LOGO_CANONICAL_UPSTREAM_V1057' /etc/nginx/sites-available/streamforge || die "Active v10.68 Main alias-logo URI normalization is missing"
grep -Fq 'streamforge_public_backend' /etc/nginx/sites-available/streamforge || die "Active Nginx Public upstream is missing"
LIVE_VERSION="$(curl -fsS --max-time 3 http://127.0.0.1:8800/version)"
[[ "$LIVE_VERSION" == "12.12" ]] || die "Service is still running version $LIVE_VERSION"
# STREAMFORGE_MAIN_SUPERVISOR_PROCESS_OWNERSHIP_VERIFY_V1115:
# The Main Gunicorn worker must have zero direct FFmpeg children after migration.
MAIN_MASTER_PID="$(systemctl show "$SERVICE_NAME" -p MainPID --value 2>/dev/null || true)"
MAIN_WORKER_PID="$(pgrep -P "$MAIN_MASTER_PID" 2>/dev/null | head -1 || true)"
if [[ -n "$MAIN_WORKER_PID" ]] && pgrep -P "$MAIN_WORKER_PID" ffmpeg >/dev/null 2>&1; then
  ps -o pid,ppid,cmd -p "$MAIN_WORKER_PID" >&2 || true
  pgrep -a -P "$MAIN_WORKER_PID" ffmpeg >&2 || true
  die "Main Gunicorn worker still owns FFmpeg children after supervisor migration"
fi
grep -Fq 'STREAMFORGE_MAIN_DEDICATED_CHANNEL_SUPERVISOR_V1115' "$APP_DIR/app/ffmpeg.py" || die "Installed Main supervisor proxy missing"
grep -Fq 'STREAMFORGE_MAIN_DEDICATED_CHANNEL_SUPERVISOR_ENTRYPOINT_V1115' "$APP_DIR/app/main_channel_supervisor.py" || die "Installed Main supervisor entrypoint missing"
# STREAMFORGE_V1117_EXPLICIT_NVENC_PROFILE_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_NVENC_DEDICATED_FFMPEG_V1117' "$APP_DIR/app/config.py" || die "Installed v11.22 Main NVENC FFmpeg setting missing"
grep -Fq 'STREAMFORGE_MAIN_NVENC_DEDICATED_FFMPEG_V1117' "$APP_DIR/app/ffmpeg.py" || die "Installed v11.22 Main NVENC binary selector missing"
grep -Fq 'STREAMFORGE_MAIN_EXPLICIT_NVENC_PROFILE_V1117' "$APP_DIR/app/main.py" || die "Installed v11.22 explicit NVENC profile handling missing"
grep -Fq 'NVIDIA H.264 NVENC — dedicated GPU' "$APP_DIR/app/templates/channel_form.html" || die "Installed v11.22 Main NVIDIA profile option missing"
grep -Fq 'STREAMFORGE_NODE_NVENC_DEDICATED_FFMPEG_V1117' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node NVENC binary selector missing"
grep -qE '^STREAMFORGE_NVENC_FFMPEG_BIN=' "$ENV_FILE" || die "Installed v11.22 Main NVENC FFmpeg env setting missing"
! grep -R -n -E 'NVENC_MAX|nvenc.*limit|limit.*nvenc|GPU capacity reached' "$APP_DIR/app" "$APP_DIR/node_agent" >/dev/null 2>&1 || die "Installed v11.22 contains an unwanted NVENC channel limit"
# STREAMFORGE_V1118_CHANNEL_SAVE_ASYNC_SYNC_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_CHANNEL_SAVE_ASYNC_SYNC_V1118' "$APP_DIR/app/main.py" || die "Installed v11.22 non-blocking Main channel save queue missing"
grep -Fq 'STREAMFORGE_CHANNEL_SAVE_ASYNC_SYNC_V1118' "$APP_DIR/app/node_manager.py" || die "Installed v11.22 selective channel-logo sync control missing"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_SAVE_ASYNC_RESTART_V1118' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node-local non-blocking save/restart path missing"
# STREAMFORGE_V1119_BULK_PROFILE_ASYNC_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_BULK_PROFILE_ASYNC_APPLY_V1119' "$APP_DIR/app/main.py" || die "Installed v11.22 non-blocking Bulk Encoding Profile apply missing"
grep -Fq 'queue_bulk_profile_sync(background_targets)' "$APP_DIR/app/main.py" || die "Installed v11.22 Bulk Encoding Profile POST does not queue background apply"
grep -Fq 'STREAMFORGE_BULK_PROFILE_ASYNC_TARGETED_RESTART_COMPAT_V1120' "$APP_DIR/app/main.py" || die "Installed v11.22 async targeted bulk restart compatibility missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_LIVE_SEARCH_NO_RELOAD_V1122' "$APP_DIR/app/static/app.js" || die "Installed v11.22 Main Channels no-reload live search missing"
grep -Fq 'data-live-channel-search' "$APP_DIR/app/templates/channels.html" || die "Installed v11.22 Main Channels live search hook missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_TARGETED_RESTART_V1027' "$APP_DIR/app/main.py" || die "Installed historical targeted bulk restart guarantee missing"
grep -Fq 'STREAMFORGE_BULK_PROFILE_APPLY_RUNNING_V1025' "$APP_DIR/app/main.py" || die "Installed historical running bulk apply guarantee missing"
grep -Fq 'node_ids=node_scope' "$APP_DIR/app/main.py" || die "Installed v11.22 async bulk apply lost targeted node scope"
# STREAMFORGE_V1116_MAIN_FRONTEND_CRITICAL_PATH_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_PANEL_STATIC_SHELL_ASSETS_V1116' "$APP_DIR/app/templates/base.html" || die "Installed v11.22 cacheable Main shell assets missing"
grep -Fq 'STREAMFORGE_MAIN_PANEL_STATIC_SHELL_ASSETS_V1116' "$APP_DIR/app/static/panel_nav.js" || die "Installed v11.22 cacheable Main native-navigation runtime missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_STATIC_JS_V1116' "$APP_DIR/app/static/dashboard.js" || die "Installed v11.22 cacheable Dashboard runtime missing"
grep -Fq 'STREAMFORGE_MAIN_STATUS_ROW_CACHE_V1116' "$APP_DIR/app/static/app.js" || die "Installed v11.22 cached channel status DOM map missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_STATUS_FIRST_FRAME_V1116' "$APP_DIR/app/static/app.js" || die "Installed v11.22 first-frame Dashboard status refresh missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_METRICS_AFTER_5S_V1116' "$APP_DIR/app/static/app.js" || die "Installed v11.22 post-first-paint metrics cadence missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_HISTORY_IDLE_V1116' "$APP_DIR/app/static/dashboard.js" || die "Installed v11.22 idle Dashboard history loader missing"
grep -Fq 'STREAMFORGE_MAIN_BRANDING_HASH_CACHE_V1116' "$APP_DIR/app/main.py" || die "Installed v11.22 content-hashed branding cache fix missing"
grep -Fq '"Cache-Control": "public, max-age=31536000, immutable"' "$APP_DIR/app/main.py" || die "Installed v11.22 immutable branding cache header missing"
! grep -Fq 'setTimeout(refresh, 800)' "$APP_DIR/app/static/app.js" || die "Installed v11.22 still carries the obsolete 800ms Dashboard status hold"
grep -Fq 'STREAMFORGE_MAIN_DEDICATED_CHANNEL_SUPERVISOR_CONTROL_ISOLATION_V1115' "$APP_DIR/app/main.py" || die "Installed Main control-worker isolation missing"
grep -Fq 'STREAMFORGE_MAIN_PANEL_RESPONSE_BUFFERING_V1115' "$APP_DIR/scripts/apply_main_access.py" || die "Installed Main generated buffering logic missing"
grep -Fq 'STREAMFORGE_MAIN_PANEL_GZIP_V1115' "$APP_DIR/scripts/apply_main_access.py" || die "Installed Main generated gzip logic missing"
grep -Fq 'gzip on;' /etc/nginx/sites-available/streamforge || die "Active Main Nginx gzip is missing"
grep -Fq 'proxy_buffering on;' /etc/nginx/sites-available/streamforge || die "Active Main Nginx response buffering is missing"
grep -Fq 'STREAMFORGE_CANONICAL_PROTOCOL_REDIRECT_V35' "$APP_DIR/app/main.py" || die "Main canonical protocol redirect missing after restart"
grep -Fq 'STREAMFORGE_NODE_CANONICAL_PROTOCOL_REDIRECT_V35' "$APP_DIR/node_agent/app.py" || die "Node canonical protocol redirect missing after restart"
grep -q 'STREAMFORGE_IPINFO_COMPATIBLE_LOOKUP_V3059' "$APP_DIR/app/access_control.py" || die "Main IPinfo compatible lookup missing after service restart"
grep -q 'STREAMFORGE_NODE_IPINFO_COMPATIBLE_LOOKUP_V3059' "$APP_DIR/node_agent/app.py" || die "Node IPinfo compatible lookup missing after service restart"
grep -q 'STREAMFORGE_MAIN_ROOT_PANEL_PREFIX_EXCLUSION_V3059' "$APP_DIR/app/main.py" || die "Main root /panel exclusion missing after service restart"
grep -q 'STREAMFORGE_NODE_ROOT_PANEL_PREFIX_EXCLUSION_V3059' "$APP_DIR/node_agent/app.py" || die "Node root /panel exclusion missing after service restart"
grep -q 'STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_V49' "$APP_DIR/app/static/panel_nav.js" || die "Main native navigation missing after service restart"
grep -q 'STREAMFORGE_NODE_FIXED_PANEL_ADDRESS_BAR_V3059' "$APP_DIR/node_agent/app.py" || die "Node fixed Panel address bar missing after service restart"
grep -q 'STREAMFORGE_NODE_ZERO_FLASH_PANEL_NAV_V3060' "$APP_DIR/node_agent/app.py" || die "Node zero-flash Panel navigation missing after service restart"
grep -q 'STREAMFORGE_NODE_RELOAD_SAFE_HIDDEN_ROUTE_V3060R2' "$APP_DIR/node_agent/app.py" || die "Node reload-safe hidden Panel route missing after service restart"
grep -q 'STREAMFORGE_NODE_IPINFO_IPV4_TRANSPORT_FALLBACK_V3060' "$APP_DIR/node_agent/app.py" || die "Node IPinfo IPv4 transport fallback missing after service restart"
grep -q 'STREAMFORGE_NODE_ROOT_PANEL_ALIAS_V3058' "$APP_DIR/node_agent/app.py" || die "Root Node Panel alias canonicalization missing after service restart"
grep -q 'STREAMFORGE_NODE_PANEL_LOGIN_ROLE_ISOLATION_V3057' "$APP_DIR/node_agent/app.py" || die "Node Panel login role-isolation fix missing after service restart"
grep -q 'STREAMFORGE_WEBPLAYER_QUICK_USER_PRIVACY_V3056' "$APP_DIR/app/templates/web_player.html" || die "Main Quick Login privacy UI missing after service restart"
grep -q 'STREAMFORGE_NODE_WEBPLAYER_QUICK_USER_PRIVACY_V3056' "$APP_DIR/node_agent/app.py" || die "Node Quick Login privacy state missing after service restart"
grep -Fq 'ExecStart=/usr/bin/gunicorn app.main:app' "/etc/systemd/system/$SERVICE_NAME.service" || die "Installed Main service is not using /usr/bin/gunicorn"
grep -Fq -- '--workers 1 --worker-class uvicorn_worker.UvicornWorker' "/etc/systemd/system/$SERVICE_NAME.service" || die "Installed Main service is not using one ASGI worker"
! grep -Eq 'venv/bin|python3 -m uvicorn|/usr/bin/env python3' "/etc/systemd/system/$SERVICE_NAME.service" || die "Installed Main service still uses a venv/direct-Uvicorn runtime"
[[ "$(cat "$APP_DIR/RUNTIME_MODE" 2>/dev/null)" == "system-gunicorn-asgi" ]] || die "Main Gunicorn runtime marker missing"
python3 - <<'PY_GLOBAL_MAIN_VERIFY'
import cryptography, OpenSSL, fastapi, gunicorn, itsdangerous, jinja2, maxminddb, multipart, paramiko, redis, sqlalchemy, uvicorn, uvicorn_worker, yt_dlp, yt_dlp_ejs
from OpenSSL import crypto
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
assert hasattr(crypto, "X509Extension") and hasattr(crypto, "X509Req")
print('system Gunicorn/ASGI Main imports verified')
PY_GLOBAL_MAIN_VERIFY
[[ -x /opt/streamforge-certbot/bin/certbot ]] || die "Isolated StreamForge Certbot missing after update"
[[ "$(/opt/streamforge-certbot/bin/certbot --version 2>/dev/null)" == "certbot 5.7.0" ]] || die "Isolated StreamForge Certbot verification failed after update"

grep -q 'preserving each alias' "$APP_DIR/app/main.py" || die "Per-alias Main URL normalizer missing"
grep -q '_CURRENT_PANEL_PREFIX' "$APP_DIR/node_agent/app.py" || die "Per-request Node alias routing missing"
grep -q 'agent_version: Mapped' "$APP_DIR/app/models.py" || die "Persisted Node version model missing"
grep -q 'def quick_status(self, node: Node' "$APP_DIR/app/node_manager.py" || die "Main lightweight Node status client missing"
grep -q '@app.get("/api/v1/status/quick"' "$APP_DIR/node_agent/app.py" || die "Node lightweight status endpoint missing"
grep -q 'StreamForge-Node/' "$APP_DIR/app/main.py" || die "Heartbeat version capture missing"
grep -q 'controller.abort()' "$APP_DIR/app/static/app.js" || die "Browser status timeout missing"
grep -q "node.agent_version or 'checking…'" "$APP_DIR/app/templates/nodes.html" || die "Server-rendered Node version fallback missing"
grep -q 'def configured_main_access_aliases' "$APP_DIR/app/main.py" || die "Main authority/role parser missing"
grep -q 'def apply_main_access_runtime' "$APP_DIR/app/main.py" || die "Main runtime listener apply hook missing"
grep -q 'MAIN_ACCESS_REQUEST_FILE' "$APP_DIR/app/main.py" || die "Main listener request-file handoff missing"
! sed -n '/def apply_main_access_runtime/,/def normalized_native_control_port/p' "$APP_DIR/app/main.py" | grep '\["sudo"' >/dev/null || die "In-service sudo invocation remains"
grep -q 'PathChanged=.*/main-access-runtime/request.json' /etc/systemd/system/streamforge-main-access.path || die "Main listener path unit missing"
grep -q 'PathChanged=.*/main-system-runtime/request.json' /etc/systemd/system/streamforge-main-system.path || die "Main system-control path unit missing"
grep -q 'ExecStart=/usr/local/sbin/streamforge-main-system-control' /etc/systemd/system/streamforge-main-system.service || die "Main system-control root service missing"
systemctl is-active --quiet streamforge-main-system.path || die "Main system-control path watcher is not active"
cmp -s "$APP_DIR/scripts/main_system_control.py" /usr/local/sbin/streamforge-main-system-control || die "Live root restore helper differs from the installed package"
grep -q 'STREAMFORGE_RESTORE_ASSET_MEMBER_POLICY_V3019' /usr/local/sbin/streamforge-main-system-control || die "Live root restore helper logo policy is stale"
grep -q 'STREAMFORGE_RESTORE_LOGO_REFERENCE_REBASE_V3021' /usr/local/sbin/streamforge-main-system-control || die "Live root restore helper logo-reference rebase is stale"
grep -q 'STREAMFORGE_CANONICAL_LOGO_STORAGE_V3022' /usr/local/sbin/streamforge-main-system-control || die "Live root restore helper canonical logo storage is stale"
grep -q 'STREAMFORGE_RESTORE_ENV_SANDBOX_WRITE_V3030' /usr/local/sbin/streamforge-main-system-control || die "Live root restore helper environment sandbox fix is stale"
grep -q 'STREAMFORGE_RESTORE_EXACT_ROLE_POLICY_V3031' /usr/local/sbin/streamforge-main-system-control || die "Live root restore helper exact Main role policy is stale"
grep -q 'STREAMFORGE_RESTORE_ENV_SANDBOX_WRITE_V3030' /etc/systemd/system/streamforge-main-system.service || die "Live restore service environment sandbox contract is stale"
grep -q 'ExecStart=/usr/local/sbin/streamforge-apply-main-access' /etc/systemd/system/streamforge-main-access.service || die "Main listener root service missing"
grep -Fq 'STREAMFORGE_CERTBOT_SANDBOX_WRITES_V38' /etc/systemd/system/streamforge-main-access.service || die "Installed Certbot sandbox write policy missing"
grep -Fq 'TimeoutStartSec=300' /etc/systemd/system/streamforge-main-access.service || die "Installed TLS provisioning timeout is stale"
grep -Fq -- '-/etc/letsencrypt' /etc/systemd/system/streamforge-main-access.service || die "Installed Lets Encrypt config path is read-only"
[[ ! -e /etc/sudoers.d/streamforge-main-access ]] || die "Obsolete Main listener sudoers rule remains"
systemctl is-active --quiet streamforge-main-access.path || die "Main listener path watcher is not active"
systemctl is-active --quiet streamforge-main-tls.timer || die "Main DNS-01 retry timer is not active"
grep -q 'proxy_set_header Host \$http_host' "$APP_DIR/deploy/nginx.conf" || die "Nginx public port preservation missing"
grep -q 'def reject_server_block' "$APP_DIR/scripts/apply_main_access.py" || die "Silent unknown-host Nginx block generator missing"
grep -q 'return 444;' "$APP_DIR/scripts/apply_main_access.py" || die "Nginx silent connection close missing"
grep -q 'error_page 421 = @streamforge_silent_drop' "$APP_DIR/scripts/apply_main_access.py" || die "Invalid-slug HTTP error interception missing"
grep -q 'error_page 404 418 = @streamforge_silent_drop' /etc/nginx/sites-available/streamforge || die "Active Nginx unknown-path interception missing"
grep -q 'def parse_access_policy' "$APP_DIR/scripts/apply_main_access.py" || die "Configured-host listener policy parser missing"
grep -q 'MAIN_PLAYLIST_ROUTE_PREFIXES' "$APP_DIR/app/main.py" || die "Panel/playlist role separation missing"
grep -q 'def loopback_host_request' "$APP_DIR/app/main.py" || die "Loopback-only health exemption missing"
grep -q 'STREAMFORGE_MAIN_AUTOMATIC_ACCESS_POLICY_UI_V3031' "$APP_DIR/app/templates/node_form.html" || die "Automatic Main access policy UI missing after service restart"
grep -q 'STREAMFORGE_MAIN_RECOVERY_ALIAS_NO_ROLE_WIDEN_V3031' "$APP_DIR/app/main.py" || die "Main recovery alias role isolation missing after service restart"
grep -q 'STREAMFORGE_MAIN_EXACT_ALIAS_NO_REDIRECT_V3036' "$APP_DIR/app/main.py" || die "Exact Main alias no-redirect policy missing after service restart"
grep -q 'STREAMFORGE_UPDATE_ROLE_URL_DECONTAMINATION_V3036' "$APP_DIR/scripts/reset_domain.py" || die "Panel/Playlist URL role decontamination missing after service restart"
grep -q 'STREAMFORGE_UPDATE_PRESERVE_ALL_ACCESS_URLS_V3038' "$APP_DIR/scripts/reset_domain.py" || die "Update multi-URL preservation missing after service restart"
grep -q 'STREAMFORGE_UPDATE_DATABASE_AUTHORITY_FIRST_V3054' "$APP_DIR/scripts/reset_domain.py" || die "Database-authoritative update access selector missing after service restart"
grep -q 'STREAMFORGE_UPDATE_DATABASE_AUTHORITY_FIRST_V3054' "$LIVE_UPDATER" || die "Database-authoritative updater policy missing after service restart"
grep -q 'STREAMFORGE_WEBPLAYER_PERSISTENT_AUTO_RECONNECT_V3055' "$APP_DIR/app/templates/player.html" || die "Main Web Player persistent reconnect loop missing after service restart"
grep -q 'STREAMFORGE_WEBPLAYER_PERSISTENT_AUTO_RECONNECT_V3055' "$APP_DIR/node_agent/app.py" || die "Node Web Player persistent reconnect loop missing after service restart"
grep -q 'STREAMFORGE_CHANNEL_STRICT_HLS_UP_WAITING_V3055' "$APP_DIR/app/main.py" || die "Main strict HLS delivery-state backend missing after service restart"
grep -q 'freshness_window = max(20, segment_time \* 8)' "$APP_DIR/app/node_manager.py" || die "Main local HLS freshness gate missing after service restart"
grep -q 'bool(ready and fresh_hls and alive)' "$APP_DIR/node_agent/app.py" || die "Node HLS freshness gate missing after service restart"
grep -q 'STREAMFORGE_CHANNEL_STRICT_HLS_UP_WAITING_V3055' "$APP_DIR/app/static/app.js" || die "Main exact delivery-state filter missing after service restart"
grep -Fq 'const statusMatched = !state.status || runtimeStatus === state.status;' "$APP_DIR/app/static/app.js" || die "Main paginated exact delivery-state filter logic missing after service restart"
grep -q '<option value="waiting"' "$APP_DIR/app/templates/channels.html" || die "Main Waiting status filter missing after service restart"
grep -q '<option value="waiting">Waiting</option>' "$APP_DIR/node_agent/app.py" || die "Node Waiting status filter missing after service restart"
grep -q 'STREAMFORGE_RESTORE_PRESERVE_ALL_ACCESS_URLS_V3039' "$APP_DIR/scripts/main_system_control.py" || die "Restore complete Panel/Playlist URL preservation missing after service restart"
grep -q 'STREAMFORGE_RESTORE_FAIL_OPEN_KEEP_ACCESS_URLS_V3039' "$APP_DIR/scripts/main_system_control.py" || die "Restore fail-open URL preservation missing after service restart"
grep -q 'STREAMFORGE_PLAYBACK_CHILD_ALIAS_PREFIX_V3036' "$APP_DIR/app/main.py" || die "Playlist child alias-prefix propagation missing after service restart"
grep -q 'STREAMFORGE_MAIN_REQUEST_MATCHED_PLAYLIST_BASE_V3037' "$APP_DIR/app/main.py" || die "Request-matched Main playlist URL generation missing after service restart"
grep -q 'STREAMFORGE_NODE_LONGEST_PLAYLIST_ALIAS_V3040' "$APP_DIR/node_agent/app.py" || die "Node longest Playlist/App path-alias matching missing after service restart"
grep -q 'STREAMFORGE_WEBPLAYER_QUICK_OR_MANUAL_LOGIN_V3041' "$APP_DIR/app/main.py" || die "Main Quick User plus Manual Login missing after service restart"
grep -q 'value="quick"' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Web Player Quick User login mode UI missing after service restart"
grep -q 'STREAMFORGE_WEBPLAYER_QUICK_MODE_SYNC_V3041' "$APP_DIR/app/node_manager.py" || die "Remote Node Quick User login mode sync missing after service restart"
grep -q 'STREAMFORGE_NODE_WEBPLAYER_QUICK_OR_MANUAL_LOGIN_V3041' "$APP_DIR/node_agent/app.py" || die "Node Quick User plus Manual Login missing after service restart"
grep -q 'STREAMFORGE_WEBPLAYER_QUICK_LOGIN_LAYOUT_V3042' "$APP_DIR/app/templates/web_player.html" || die "Main Quick Login option ordering/display-name layout missing after service restart"
grep -q 'STREAMFORGE_WEBPLAYER_SELECTOR_HIDDEN_V3042' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Manual-mode selected-user visibility fix missing after service restart"
grep -q 'STREAMFORGE_NODE_WEBPLAYER_QUICK_LOGIN_LAYOUT_V3042' "$APP_DIR/node_agent/app.py" || die "Node Quick Login option ordering/display-name layout missing after service restart"
grep -q 'STREAMFORGE_OFFLINE_NODE_LOCAL_DELETE_V3043' "$APP_DIR/app/main.py" || die "Offline Node local-delete fallback missing after service restart"
grep -q 'STREAMFORGE_WEBPLAYER_QUICK_CREDENTIAL_STATE_V3043' "$APP_DIR/app/templates/web_player.html" || die "Main Quick Login credential/back state missing after service restart"
grep -q 'STREAMFORGE_NODE_WEBPLAYER_QUICK_CREDENTIAL_STATE_V3043' "$APP_DIR/node_agent/app.py" || die "Node Quick Login credential/back state missing after service restart"
grep -q 'STREAMFORGE_OFFLINE_NODE_DELETE_PREFLIGHT_V3043' "$LIVE_UPDATER" || die "Offline Node delete preflight correction missing after service restart"
grep -q 'STREAMFORGE_CHANNELS_CACHED_NODE_HEALTH_V3044' "$APP_DIR/app/node_manager.py" || die "Channels cached-only Node health support missing after service restart"
grep -q 'STREAMFORGE_OFFLINE_NODE_STATUS_BACKOFF_V3044' "$APP_DIR/app/node_manager.py" || die "Offline Node status backoff missing after service restart"
grep -q 'STREAMFORGE_CHANNELS_NO_BLOCKING_VIEWER_FETCH_V3044' "$APP_DIR/app/main.py" || die "Non-blocking Channels viewer aggregation missing after service restart"
grep -q 'STREAMFORGE_CHANNELS_FAST_OFFLINE_RENDER_V3044' "$APP_DIR/app/main.py" || die "Fast offline Channels initial render missing after service restart"
grep -q 'STREAMFORGE_OFFLINE_CHANNEL_SETTINGS_QUEUE_V3045' "$APP_DIR/app/node_manager.py" || die "Offline channel-settings queue missing after service restart"
grep -q 'STREAMFORGE_NODE_RECONNECT_AUTO_SYNC_V3045' "$APP_DIR/app/main.py" || die "Node reconnect auto-sync missing after service restart"
grep -q 'Test &amp; Sync' "$APP_DIR/app/templates/nodes.html" || die "Node Test and Sync control missing after service restart"
grep -q 'STREAMFORGE_WEBPLAYER_DOWNLOAD_MANAGE_UI_V3045' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Web Player download management UI missing after service restart"
grep -q 'STREAMFORGE_MAIN_WEBPLAYER_AUTHENTICATED_DOWNLOAD_V3045' "$APP_DIR/app/main.py" || die "Main Web Player authenticated download missing after service restart"
grep -q 'STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_SYNC_V3045' "$APP_DIR/node_agent/app.py" || die "Node managed download receiver missing after service restart"
grep -q 'STREAMFORGE_NODE_WEBPLAYER_AUTHENTICATED_DOWNLOAD_V3045' "$APP_DIR/node_agent/app.py" || die "Node authenticated Web Player download missing after service restart"
grep -q 'STREAMFORGE_MAIN_WEBPLAYER_INFO_DOWNLOAD_V3046' "$APP_DIR/app/templates/player.html" || die "Main watch-page Download info-row layout missing after service restart"
grep -q 'STREAMFORGE_NODE_WEBPLAYER_INFO_DOWNLOAD_V3046' "$APP_DIR/node_agent/app.py" || die "Node watch-page Download info-row layout missing after service restart"
grep -q 'STREAMFORGE_BULK_PROFILE_500_GUARD_V3046' "$APP_DIR/app/main.py" || die "Bulk encoding-profile error boundary missing after service restart"
grep -q 'STREAMFORGE_MAIN_INTERACTIVE_POINTER_CURSOR_V3047' "$APP_DIR/app/static/style.css" || die "Main Panel interactive pointer cursor policy missing after service restart"
grep -q 'STREAMFORGE_MAIN_WEBPLAYER_INTERACTIVE_POINTER_V3047' "$APP_DIR/app/templates/web_player.html" || die "Main Web Player interactive pointer cursor policy missing after service restart"
grep -q 'STREAMFORGE_NODE_PANEL_INTERACTIVE_POINTER_V3047' "$APP_DIR/node_agent/app.py" || die "Node Panel interactive pointer cursor policy missing after service restart"
grep -q 'STREAMFORGE_NODE_WEBPLAYER_INTERACTIVE_POINTER_V3047' "$APP_DIR/node_agent/app.py" || die "Node Web Player interactive pointer cursor policy missing after service restart"
grep -q 'STREAMFORGE_NODE_WATCH_INTERACTIVE_POINTER_V3047' "$APP_DIR/node_agent/app.py" || die "Node watch-page interactive pointer cursor policy missing after service restart"
grep -q 'STREAMFORGE_NEW_NODE_FAVICON_STATE_RESET_V3048' "$APP_DIR/app/main.py" || die "New Node favicon state reset missing after service restart"
grep -q 'STREAMFORGE_NODE_DELETE_FAVICON_STATE_CLEANUP_V3048' "$APP_DIR/app/main.py" || die "Deleted Node favicon state cleanup missing after service restart"
grep -q 'STREAMFORGE_STALE_NODE_FAVICON_SELF_HEAL_V3048' "$APP_DIR/app/node_manager.py" || die "Stale Node favicon self-heal missing after service restart"
grep -q 'STREAMFORGE_BULK_NODE_ASSIGNMENT_500_GUARD_V3048' "$APP_DIR/app/main.py" || die "Bulk Node assignment error boundary missing after service restart"
grep -q 'STREAMFORGE_MAIN_WEBPLAYER_MOBILE_INFO_LAYOUT_V3049' "$APP_DIR/app/templates/player.html" || die "Main Web Player mobile information layout missing after service restart"
grep -q 'STREAMFORGE_NODE_WEBPLAYER_MOBILE_INFO_LAYOUT_V3049' "$APP_DIR/node_agent/app.py" || die "Node Web Player mobile information layout missing after service restart"
grep -q 'STREAMFORGE_OFFLINE_CHANNEL_CONTROL_FAST_QUEUE_V3050' "$APP_DIR/app/node_manager.py" || die "Offline channel-control fast queue missing after service restart"
grep -q 'STREAMFORGE_OFFLINE_BULK_ACTION_FAST_QUEUE_V3050' "$APP_DIR/app/node_manager.py" || die "Offline bulk removal fast queue missing after service restart"
grep -q 'STREAMFORGE_OFFLINE_RUNTIME_SNAPSHOT_SHORT_CIRCUIT_V3050' "$APP_DIR/app/node_manager.py" || die "Offline runtime snapshot short-circuit missing after service restart"
python3 - "$APP_DIR/app/main.py" <<'PY_GUARD' || die "Bulk database-only live-state policy missing after service restart"
import sys
from pathlib import Path
s = Path(sys.argv[1]).read_text()
start = s.find('if action in {"relay_set", "relay_clear"}:')
end = s.find('if action in {"nodes_add", "nodes_remove", "nodes_set"}:', start + 1)
if start < 0 or end < 0:
    raise SystemExit(1)
block = s[start:end]
required = [
    'STREAMFORGE_BULK_RELAY_DB_ONLY_LIVE_STATE_V1138',
    'mark_node_channel_sync_pending(',
    'STREAMFORGE_BULK_RELAY_RESPONSE_FIRST_V1137',
    'db.commit()',
]
for item in required:
    if item not in block:
        raise SystemExit(1)
for forbidden in ('runtime_snapshot(', 'is_running(', 'channel_runtime(', 'sync_channel_to_nodes('):
    if forbidden in block:
        raise SystemExit(1)
PY_GUARD
grep -q 'STREAMFORGE_MAIN_WEBPLAYER_MOBILE_HEADER_LAYOUT_V3051' "$APP_DIR/app/templates/web_player.html" || die "Main signed-in Web Player mobile header layout missing after service restart"
grep -q 'STREAMFORGE_NODE_WEBPLAYER_MOBILE_HEADER_LAYOUT_V3051' "$APP_DIR/node_agent/app.py" || die "Node signed-in Web Player mobile header layout missing after service restart"
grep -q 'STREAMFORGE_DASHBOARD_FAST_RENDER_V3052' "$APP_DIR/app/main.py" || die "Fast Dashboard render policy missing after service restart"
grep -q 'STREAMFORGE_GOOGLE_BACKUP_CARD_LAYOUT_V3053' "$APP_DIR/app/templates/backups.html" || die "Google Drive backup card layout missing after service restart"
! grep -Fq 'Local relay hostname/IP' "$APP_DIR/app/templates/node_form.html" || die "Removed Local relay hostname/IP option returned after service restart"
! grep -Fq 'Relay scheme' "$APP_DIR/app/templates/node_form.html" || die "Removed Relay scheme option returned after service restart"
! grep -Fq 'Allow only configured Main access hostnames' "$APP_DIR/app/templates/node_form.html" || die "Removed Main hostname-lock option returned after service restart"
grep -q 'data-node-status-endpoint="{{ app_root }}/nodes/' "$APP_DIR/app/templates/nodes.html" || die "Exact-slug Node status endpoint missing"
grep -q 'const endpoint = card.dataset.nodeStatusEndpoint' "$APP_DIR/app/static/app.js" || die "Prefix-aware Node metrics polling missing"
grep -q 'def normalize_node_card_metrics' "$APP_DIR/app/main.py" || die "Node metric schema normalizer missing"
grep -q 'def cached_node_metrics' "$APP_DIR/app/main.py" || die "Heartbeat metric fallback cache missing"
grep -q 'X-Node-CPU-Percent' "$APP_DIR/node_agent/app.py" || die "Node heartbeat CPU metric missing"
grep -q 'X-Node-Download-Mbps' "$APP_DIR/node_agent/app.py" || die "Node heartbeat network metric missing"
grep -q 'data-endpoint="{{ app_root }}/system-metrics.json"' "$APP_DIR/app/templates/dashboard.html" || die "Exact-slug Main metrics endpoint missing"
grep -q 'class SilentInvalidNodeAccessMiddleware' "$APP_DIR/node_agent/app.py" || die "Node silent invalid-host middleware missing"
grep -q 'def _node_access_should_silently_drop' "$APP_DIR/node_agent/app.py" || die "Node alias/role silent-drop policy missing"
grep -q 'transport.abort()' "$APP_DIR/node_agent/app.py" || die "Node transport-level silent close missing"
grep -q '_valid_scope_control_token' "$APP_DIR/node_agent/app.py" || die "Authenticated Node recovery exemption missing"
grep -Fq 'NODE-ONLY Playlist users layout fix' "$APP_DIR/node_agent/app.py" || die "Node playlist-user responsive layout styling missing"
grep -q '@app.post("/panel/users/{token}/delete")' "$APP_DIR/node_agent/app.py" || die "Node playlist-user delete endpoint missing"
grep -q 'require_node_panel_user(request, "stream_users.delete")' "$APP_DIR/node_agent/app.py" || die "Node playlist-user delete permission guard missing"
grep -q 'Delete this playlist user? This cannot be undone.' "$APP_DIR/node_agent/app.py" || die "Node playlist-user delete confirmation missing"
grep -q 'manager.connection_reservations.pop(key, None)' "$APP_DIR/node_agent/app.py" || die "Deleted Node playlist-user connection cleanup missing"
grep -q 'node-playlist-user-table-wrap' "$APP_DIR/node_agent/app.py" || die "Node playlist-user horizontal table wrapper missing"
grep -q 'def _masked_saved_secret' "$APP_DIR/node_agent/app.py" || die "Node saved API credential masking helper missing"
grep -q 'GeoIP API information' "$APP_DIR/node_agent/app.py" || die "Main-style Node GeoIP API section missing"
! grep -q 'Saved IPinfo status' "$APP_DIR/node_agent/app.py" || die "Duplicate Node IPinfo status row remains"
! grep -q 'Saved MaxMind key status' "$APP_DIR/node_agent/app.py" || die "Duplicate Node MaxMind status row remains"
grep -q 'geo-provider-hidden' "$APP_DIR/node_agent/app.py" || die "Node provider-specific API field toggle missing"
grep -q 'ipinfo_saved = html.escape(_masked_saved_secret(manager.ipinfo_token))' "$APP_DIR/node_agent/app.py" || die "Node IPinfo secret masking guard missing"
grep -q 'maxmind_saved = html.escape(_masked_saved_secret(manager.maxmind_license_key))' "$APP_DIR/node_agent/app.py" || die "Node MaxMind secret masking guard missing"
! grep -Eq '\{manager\.(ipinfo_token|maxmind_license_key)\}' "$APP_DIR/node_agent/app.py" || die "Node settings page exposes a raw API credential"
grep -q 'def masked_saved_secret' "$APP_DIR/app/geoip_config.py" || die "Main saved API credential masking helper missing"
grep -q 'maxmind_saved_display' "$APP_DIR/app/geoip_config.py" || die "Main MaxMind masked status payload missing"
grep -q 'ipinfo_saved_display' "$APP_DIR/app/geoip_config.py" || die "Main IPinfo masked status payload missing"
! grep -q 'Saved IPinfo status' "$APP_DIR/app/templates/node_asn.html" || die "Duplicate Main IPinfo status row remains"
! grep -q 'Saved MaxMind key status' "$APP_DIR/app/templates/node_asn.html" || die "Duplicate Main MaxMind status row remains"
# STREAMFORGE_GEOIP_COMPACT_FORM_INSTALLED_GUARD_V99R13: CSS may also contain geo-credential-remove, so check the two real fields directly.
grep -Fq 'STREAMFORGE_GEOIP_COMPACT_PROVIDER_FORM_V99R12' "$APP_DIR/app/templates/node_asn.html" || die "Compact Main GeoIP provider form missing after deploy"
grep -Fq 'class="check geo-ipinfo-field geo-credential-remove"' "$APP_DIR/app/templates/node_asn.html" || die "Compact Main IPinfo credential row missing after deploy"
grep -Fq 'class="check geo-maxmind-field geo-credential-remove"' "$APP_DIR/app/templates/node_asn.html" || die "Compact Main MaxMind credential row missing after deploy"
! grep -Fq 'IPinfo token is configured (Saved' "$APP_DIR/app/templates/node_asn.html" || die "Removed Main GeoIP IPinfo status note returned after deploy"
grep -Fq 'STREAMFORGE_GEOIP_PROVIDER_CHECKBOX_NOWRAP_V99R15' "$APP_DIR/app/templates/node_asn.html" || die "v9.9 r15 GeoIP checkbox nowrap layout missing after deploy"
grep -Fq 'STREAMFORGE_GEOIP_TEST_SEPARATE_PANEL_V99R15' "$APP_DIR/app/templates/node_asn.html" || die "v9.9 r15 separate GeoIP test panel missing after deploy"
grep -q 'geo_settings.ipinfo_saved_display' "$APP_DIR/app/templates/node_asn.html" || die "Main masked IPinfo status display missing"
grep -q 'geo_settings.maxmind_saved_display' "$APP_DIR/app/templates/node_asn.html" || die "Main masked MaxMind status display missing"
! grep -q 'geo_settings.ipinfo_token' "$APP_DIR/app/templates/node_asn.html" || die "Main template exposes the saved IPinfo token"
! grep -q 'geo_settings.maxmind_license_key' "$APP_DIR/app/templates/node_asn.html" || die "Main template exposes the saved MaxMind key"
grep -Fq '.credential-card{display:flex' "$APP_DIR/app/static/style.css" && grep -Fq '.saved-credential{' "$APP_DIR/app/static/style.css" || die "Main saved API credential styling missing"
grep -q '"maxmind_saved_display": masked' "$APP_DIR/node_agent/app.py" || die "Remote GeoIP API masked MaxMind status missing"
grep -q '"ipinfo_saved_display": masked' "$APP_DIR/node_agent/app.py" || die "Remote GeoIP API masked IPinfo status missing"
grep -Fq '.node-category-table{min-width:940px;table-layout:fixed}' "$APP_DIR/node_agent/app.py" && grep -Fq '.node-category-row-actions{display:flex' "$APP_DIR/node_agent/app.py" || die "Compact Node category table styling missing"
grep -Fq '.node-playlist-channel-actions{display:flex' "$APP_DIR/node_agent/app.py" && grep -Fq '.node-playlist-search-hidden{display:none!important}' "$APP_DIR/node_agent/app.py" || die "Node playlist bulk-selection styling missing"
grep -q 'id="node-playlist-select-visible"' "$APP_DIR/node_agent/app.py" || die "Node playlist Select all control missing"
grep -q 'id="node-playlist-clear-visible"' "$APP_DIR/node_agent/app.py" || die "Node playlist Clear all control missing"
grep -q 'function setVisible(checked)' "$APP_DIR/node_agent/app.py" || die "Node playlist filtered bulk-selection logic missing"
grep -q 'node-category-row-actions' "$APP_DIR/node_agent/app.py" || die "Single-line Node category actions missing"
grep -q 'if _has_panel_permission(user, "categories.view")' "$APP_DIR/node_agent/app.py" || die "Node category navigation permission missing"
grep -Fq 'NODE-ONLY Playlist users layout fix' "$APP_DIR/node_agent/app.py" || die "Node playlist-user layout marker missing"
grep -q 'href="/panel/users/{urllib.parse.quote(item.token, safe="")}/edit">Edit user</a>' "$APP_DIR/node_agent/app.py" || die "Node playlist-user Edit user action missing"
grep -q '@app.get("/panel/users/{token}/edit"' "$APP_DIR/node_agent/app.py" || die "Node playlist-user edit page missing"
grep -q '@app.post("/panel/users/{token}/edit"' "$APP_DIR/node_agent/app.py" || die "Node playlist-user edit save endpoint missing"
grep -q 'def _resolve_node_user_playlist_selection' "$APP_DIR/node_agent/app.py" || die "Node playlist assignment resolver missing"

grep -Fq '.node-user-custom-channel-actions{display:flex' "$APP_DIR/node_agent/app.py" && grep -Fq '.node-user-channel-search-hidden{display:none!important}' "$APP_DIR/node_agent/app.py" || die "Node user custom-channel styling missing"
grep -q 'Dynamic "All enabled Main channels" profiles intentionally do not keep' "$APP_DIR/app/playback_keys.py" || die "Dynamic Main playlist playback authorization fix missing"
grep -q 'select(Channel.id).where' "$APP_DIR/app/playback_keys.py" || die "Dynamic Main playlist channel query missing"
grep -q 'Channel.output_type == "hls"' "$APP_DIR/app/playback_keys.py" || die "Dynamic Main playlist HLS restriction missing"
grep -q 'name="username" value="{html.escape(str(target.username or ""), quote=True)}"' "$APP_DIR/node_agent/app.py" || die "Editable Node playlist-user username field missing"
grep -q 'placeholder="Leave blank to keep current password"' "$APP_DIR/node_agent/app.py" || die "Optional Node playlist-user password field missing"
grep -q 'def _node_user_xtream_url_with_credentials' "$APP_DIR/node_agent/app.py" || die "Node playlist-user Xtream credential URL rebuilder missing"
grep -q 'current.password_hash = hash_scrypt_password(submitted_password)' "$APP_DIR/node_agent/app.py" || die "Node playlist-user password update missing"
grep -q 'Username already exists on this node' "$APP_DIR/node_agent/app.py" || die "Node playlist-user username uniqueness guard missing"
grep -q 'id="node-user-select-visible"' "$APP_DIR/node_agent/app.py" || die "Node playlist-user custom Select all control missing"
grep -q 'id="node-user-clear-visible"' "$APP_DIR/node_agent/app.py" || die "Node playlist-user custom Clear all control missing"
grep -q 'function setVisible(checked)' "$APP_DIR/node_agent/app.py" || die "Node playlist-user filtered bulk-selection logic missing"
grep -q 'Node-local playlist user credentials and playlist updated' "$APP_DIR/node_agent/app.py" || die "Node playlist-user credential update audit event missing"
python3 - "$APP_DIR/node_agent/app.py" <<'PY110'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index("def _panel_stream_users_page(")
end = text.index("\ndef _panel_stream_user_form", start)
page = text[start:end]
if "Playlist order</a>" in page or "Edit order</a>" in page:
    raise SystemExit("Legacy Node playlist-user order action is still rendered")
if ">Edit user</a>" not in page:
    raise SystemExit("Edit user action is not rendered")
PY110
grep -q 'compatible intersection instead of rejecting the whole playlist' "$APP_DIR/app/main.py" || die "Main playlist compatible-subset logic missing"
grep -q 'runtime_by_stream_id' "$APP_DIR/node_agent/app.py" || die "Stable Main channel-ID runtime map missing"
grep -q "'key': resolved_key" "$APP_DIR/node_agent/app.py" || die "Main playlist live runtime-key rewrite missing"
grep -q 'runtime_by_slug' "$APP_DIR/node_agent/app.py" || die "Main playlist slug fallback missing"
grep -q '"compatible": bool(compatible_channels)' "$APP_DIR/app/main.py" || die "Partial Main playlist compatibility flag missing"
grep -q '"main_channels": len(ordered)' "$APP_DIR/app/main.py" || die "Main playlist total-channel reporting missing"
grep -q 'of {main_count} channel(s) available' "$APP_DIR/node_agent/app.py" || die "Node Main-playlist available-count label missing"
# STREAMFORGE_WEB_PLAYER_CLEAN_INFO_VALIDATOR_V2211:
# The Web Player stream URL + Copy UI was intentionally removed in v2.1.210.
# Validate absence instead of requiring the old control.
! grep -R -q --exclude='*.pyc' -E 'Copy Xtream M3U|Copy Xtream URL|Copy HLS URL' "$APP_DIR/app/templates" "$APP_DIR/node_agent/app.py" || die "Legacy verbose copy labels remain"
grep -q '>Copy</button>' "$APP_DIR/app/templates/users.html" || die "Main user Copy label missing"
! grep -q 'id="stream-url"' "$APP_DIR/app/templates/player.html" || die "Removed Player stream URL box returned after deployment"
! grep -q '>Copy</button>' "$APP_DIR/app/templates/player.html" || die "Removed Player Copy control returned after deployment"
grep -q '>Copy</button>' "$APP_DIR/node_agent/app.py" || die "Node user Copy label missing"
grep -q '_SHORT_KEY_LENGTH = 8' "$APP_DIR/app/playback_keys.py" || die "Main eight-character playback key generator missing"
grep -q 'class PlaybackGrant' "$APP_DIR/app/models.py" || die "Persistent Main playback grant model missing"
grep -q 'streamforge_playback_grants_dirty' "$APP_DIR/app/db.py" || die "Playback grant commit hook missing"
grep -q '_NODE_PLAYBACK_KEY_LENGTH = 8' "$APP_DIR/node_agent/app.py" || die "Node eight-character playback key generator missing"
grep -q 'issue_node_playback_key(user, channel, request' "$APP_DIR/node_agent/app.py" || die "Node playlist short-key issuance missing"
grep -q 'resolve_node_playback_key(token, key, request)' "$APP_DIR/node_agent/app.py" || die "Node short-key playback verification missing"
grep -q 'return f"{prefix}{hours}h {minutes}m {seconds}s"' "$APP_DIR/app/main.py" || die "Main full uptime formatter missing"
grep -q 'return f"{prefix}{hours}h {minutes}m {seconds}s"' "$APP_DIR/node_agent/app.py" || die "Node full uptime formatter missing"
grep -q '`${seconds}s`' "$APP_DIR/app/static/app.js" || die "Main live full uptime rendering missing"
grep -q "filter(Boolean).join(' ')" "$APP_DIR/node_agent/app.py" || die "Node live full uptime rendering missing"

# STREAMFORGE_NODE_PANEL_SESSION_AUTH_UPDATER_GUARD_V1068
# v1.11.223 fail-closed live Node Panel authorization validation.
grep -q '@app.post("/api/v1/node-panel-live-auth/{node_slug}")' "$APP_DIR/app/main.py" || die "Main live Node-panel auth endpoint missing"
grep -q 'def _decrypt_panel_live_auth_envelope' "$APP_DIR/app/main.py" || die "Main encrypted live-auth request decoder missing"
grep -q 'AESGCM(_panel_live_auth_key(node.api_token))' "$APP_DIR/app/main.py" || die "Main AES-GCM live-auth envelope missing"
grep -q 'AdminUser.nodes.any(Node.id == node.id)' "$APP_DIR/app/main.py" || die "Live Node assignment authorization missing"
grep -q 'def _panel_admin_auth_version' "$APP_DIR/app/main.py" || die "Node session password-change revocation missing"
grep -q 'permissions": sorted(role_permission_set(admin.role))' "$APP_DIR/app/main.py" || die "Live Node role permission response missing"
grep -q 'def live_panel_authorize' "$APP_DIR/node_agent/app.py" || die "Node live Main authorization client missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_SESSION_AUTH_CACHE_V1066' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node panel bounded session-auth cache missing"
grep -Fq 'STREAMFORGE_NODE_YOUTUBE_SOURCE_SCAN_GUARDED_V1067' "$APP_DIR/node_agent/app.py" || die "Installed v10.68 Node guarded YouTube source-scan resolution missing"
grep -Fq 'status, user, detail = _cached_panel_session_authorize(claims[0], claims[1])' "$APP_DIR/node_agent/app.py" || die "Active Node session bounded live revalidation missing"
grep -Fq 'cleaned_username, action="session", auth_version=cleaned_version' "$APP_DIR/node_agent/app.py" || die "Node session cache miss does not revalidate live against Main"
# STREAMFORGE_V1070_NODE_PANEL_HLS_PREVIEW_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_PANEL_HLS_PREVIEW_V1070' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node HLS preview player missing"
grep -Fq '@app.get("/panel/channels/{key}/play", response_class=HTMLResponse)' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node HLS preview route missing"
grep -Fq 'X-StreamForge-Panel-Media-Grant' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node HLS preview sliding media grant missing"
grep -Fq 'href="/panel/channels/{escaped_key}/play"' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node HLS action still targets raw manifest"
# STREAMFORGE_V1077_NATIVE_BROWSER_HISTORY_VIDEO_ONLY_INSTALLED_GUARD:
grep -Fq 'STREAMFORGE_MAIN_NATIVE_BROWSER_HISTORY_V1077' "$APP_DIR/app/static/panel_nav.js" || die "Installed v11.22 Main native browser history missing"
grep -Fq 'history.pushState(panelHistoryState' "$APP_DIR/app/static/panel_nav.js" || die "Installed v11.22 Main history.pushState navigation missing"
grep -Fq 'STREAMFORGE_NODE_NATIVE_BROWSER_HISTORY_V1077' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node native browser history missing"
grep -Fq 'history.pushState(mk' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node history.pushState navigation missing"
grep -Fq 'STREAMFORGE_NODE_HLS_VIDEO_ONLY_V1076' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node video-only HLS preview missing"
# STREAMFORGE_V1078_CHANNEL_PAGINATION_INSTALLED_GUARD:
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_PAGINATION_V1078' "$APP_DIR/app/static/app.js" || die "Installed v11.22 Main Channels pagination logic missing"
grep -Fq 'data-channel-pagination' "$APP_DIR/app/templates/channels.html" || die "Installed v11.22 Main Channels pagination controls missing"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_PAGINATION_V1078' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node Channels pagination logic missing"
grep -Fq 'data-node-channel-pagination' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node Channels pagination controls missing"
grep -Fq 'STREAMFORGE_RESPONSIVE_METRIC_CHART_HEIGHT_V1071' "$APP_DIR/app/static/style.css" || die "Installed v11.22 Main responsive dashboard graph sizing missing"
grep -Fq 'STREAMFORGE_NODE_RESPONSIVE_METRIC_CHART_HEIGHT_V1071' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node responsive dashboard graph sizing missing"
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_LIVE_SESSION_ID_V1073' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node Live-session ID logging missing"
if grep -Fq 'generation_id = "sess-"' "$APP_DIR/node_agent/app.py"; then die "Installed v11.22 Node still generates a second log-only session ID"; fi
grep -Fq 'STREAMFORGE_NODE_CLIENT_LOG_LIVE_SID_EPOCH_BIND_V1073' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node SID+epoch Client-log binding missing"
grep -Fq "header_cells += '<th>Session ID</th>'" "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node Client-log Session ID column missing"
grep -Fq "def independent_logs_clear(request: Request, log_type: str = 'activity')" "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node tab-scoped Panel log clear missing"
grep -q '_session_cookie(user.username, user.auth_version)' "$APP_DIR/node_agent/app.py" || die "Node auth-version session cookie missing"
grep -q 'STREAMFORGE_NODE_BROWSER_COOKIE_FIX_V60R1' "$APP_DIR/node_agent/app.py" || die "Node stale-cookie cleanup missing after service restart"
grep -q 'STREAMFORGE_MAIN_CHANNEL_INITIAL_UPTIME_V60R2' "$APP_DIR/app/main.py" || die "Main channel initial uptime fix missing after service restart"
grep -q 'STREAMFORGE_MAIN_LOCAL_HLS_READY_RACE_FALLBACK_V60R2' "$APP_DIR/app/node_manager.py" || die "Local HLS readiness fallback missing after service restart"
grep -q 'STREAMFORGE_NODE_TEST_SYNC_BACKGROUND_V60R2' "$APP_DIR/app/main.py" || die "Background Node Test & Sync fix missing after service restart"
grep -q 'STREAMFORGE_NODE_STATUS_HEARTBEAT_FIRST_V60R2' "$APP_DIR/app/main.py" || die "Heartbeat-first Node status fix missing after service restart"
grep -q '_clear_panel_session_cookies(response, request)' "$APP_DIR/node_agent/app.py" || die "Node cookie cleanup helper not wired after service restart"
python3 - "$APP_DIR/node_agent/app.py" <<'PY121_DISCONNECT'
from pathlib import Path
import ast, sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
ast.parse(text)
required = {
    'current disconnected detail': 'MAIN_PANEL_DISCONNECTED_DETAIL = "Main panel is disconnected; Node panel access is disabled"',
    'browser exception split': 'return _main_panel_disconnected_page(request)',
    'offline login styled page': 'return _main_panel_disconnected_page(request, login_attempt=True)',
    'login-page live connectivity gate': 'if not manager.panel_connected(force=True):',
    'API JSON fallback': 'return JSONResponse({"detail": exc.detail}',
}
missing = [name for name, marker in required.items() if marker not in text]
if missing:
    raise SystemExit("Disconnected Node authorization validation missing: " + ", ".join(missing))
PY121_DISCONNECT
python3 - "$APP_DIR/node_agent/app.py" <<'PY122_LOGIN_GATE'
from pathlib import Path
import ast, sys
text = Path(sys.argv[1]).read_text(encoding="utf-8")
ast.parse(text)
start = text.index('@app.get("/panel/login", response_class=HTMLResponse)')
end = text.index('\n\n@app.post("/panel/login"', start)
route = text[start:end]
connection_gate = 'if not manager.panel_connected(force=True):'
disconnected_page = 'return _main_panel_disconnected_page(request, login_attempt=True)'
form_marker = "logo_html + '<h2>'"
if connection_gate not in route or disconnected_page not in route:
    raise SystemExit('Node GET login route is not fail-closed against Main disconnection')
if form_marker not in route:
    raise SystemExit('Node login name identity is missing')
if route.index(connection_gate) > route.index(form_marker):
    raise SystemExit('Node login form can render before the Main connectivity gate')
PY122_LOGIN_GATE
grep -q 'item.model_copy(update={"password_hash": "", "auth_version": ""})' "$APP_DIR/node_agent/app.py" || die "Legacy Node panel password-hash sanitization missing"
! grep -q '"password_hash": user.password_hash' "$APP_DIR/app/node_manager.py" || die "Main still synchronizes reusable panel password hashes"
grep -q 'Every login and active session is authorized live by the Main Panel' "$APP_DIR/app/templates/admin_user_form.html" || die "Panel-user live-auth UI information missing"
python3 - "$APP_DIR/node_agent/app.py" <<'PY118'
from pathlib import Path
import ast, sys
path = Path(sys.argv[1])
text = path.read_text(encoding='utf-8')
ast.parse(text)
start = text.index('def require_node_panel_user(')
end = text.index('\n\n\ndef require_independent_node_user', start)
block = text[start:end]
if 'manager.independent_mode' in block or 'verify_scrypt_password' in block or 'manager.panel_users.get' in block:
    raise SystemExit('Node protected routes still contain an offline/cached authorization bypass')
if '_cached_panel_session_authorize(claims[0], claims[1])' not in block or 'status == "unavailable"' not in block:
    raise SystemExit('Node protected routes are not fail-closed through bounded Main session authorization')
helper_start = text.index('def _cached_panel_session_authorize(')
helper_end = text.index('\n\n\ndef _session_user', helper_start)
helper = text[helper_start:helper_end]
required_helper = (
    'manager.live_panel_authorize(',
    'action="session"',
    'auth_version=cleaned_version',
    'PANEL_CACHE_SECONDS',
)
if any(marker not in helper for marker in required_helper):
    raise SystemExit('Node bounded session authorization helper does not revalidate cache misses live against Main')
PY118
if [[ -f "$LOCAL_NODE_DIR/app.py" ]]; then
  grep -Fq 'ExecStart=/usr/bin/gunicorn app:app' /etc/systemd/system/streamforge-node.service || die "Installed Node service is not using /usr/bin/gunicorn"
  grep -Fq -- '--workers 1 --worker-class uvicorn_worker.UvicornWorker' /etc/systemd/system/streamforge-node.service || die "Installed Node service is not using one ASGI worker"
  ! grep -Eq 'venv/bin|python3 -m uvicorn|/usr/bin/env python3' /etc/systemd/system/streamforge-node.service || die "Installed Node service still uses a venv/direct-Uvicorn runtime"
  [[ "$(cat "$LOCAL_NODE_DIR/RUNTIME_MODE" 2>/dev/null)" == "system-gunicorn-asgi" ]] || die "Node Gunicorn runtime marker missing"
  grep -q '^STREAMFORGE_NODE_BIND=127.0.0.1$' "$NODE_ENV_FILE" || die "Co-located Node is not loopback-bound"
  grep -q '^STREAMFORGE_NODE_PORT=8810$' "$NODE_ENV_FILE" || die "Co-located Node internal port is not 8810"
  grep -q '^STREAMFORGE_NODE_EXTERNAL_PROXY=1$' "$NODE_ENV_FILE" || die "Co-located Node external proxy mode is not enabled"
  python3 -c 'import fastapi, gunicorn, uvicorn_worker; from cryptography.hazmat.primitives.ciphers.aead import AESGCM' || die "System Node Gunicorn import verification failed"
fi
grep -Fq 'data-node-uptime' "$APP_DIR/app/templates/nodes.html" || die "Installed Node cluster Uptime column missing"
grep -Fq 'X-Node-Uptime-Seconds' "$APP_DIR/node_agent/app.py" || die "Remote Node uptime heartbeat missing"
grep -Fq 'def _write_main_system_request' "$APP_DIR/app/main.py" || die "Main Server privileged request writer missing"
grep -Fq 'action not in {"restart_service", "reboot_server", "restore_backup"}' "$APP_DIR/app/main.py" || die "Main Server action route allowlist missing"
# STREAMFORGE_V117_SERVER_PAGINATION_WATCH_FLOW_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_CHANNELS_SERVER_PAGINATION_V117' "$APP_DIR/app/main.py" || die "Installed v11.22 historical Channels pagination compatibility marker missing"
grep -Fq 'STREAMFORGE_MAIN_USERS_PLAYLISTS_SERVER_PAGINATION_V117' "$APP_DIR/app/main.py" || die "Installed v11.22 Users & Playlists server-side pagination missing"
grep -Fq 'STREAMFORGE_MAIN_USERS_PLAYLISTS_RUNTIME_FIX_V1110' "$APP_DIR/app/main.py" || die "Installed v11.22 Users & Playlists runtime 500 fix missing"
grep -Fq 'href="{{ prefix }}/web-player/watch/{{ channel.slug }}"' "$APP_DIR/app/templates/web_player.html" || die "Installed v11.22 Main WebPlayer separate watch-page flow missing"
! grep -Fq 'data-inline-player' "$APP_DIR/app/templates/web_player.html" || die "Installed v11.22 Main WebPlayer still contains inline catalogue player"
! grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_FIREFOX_GESTURE_PLAYER_V116' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node WebPlayer still contains inline catalogue player"
grep -Fq 'STREAMFORGE_MAIN_VIEWER_COUNT_MICROCACHE_V116' "$APP_DIR/app/main.py" || die "Installed v11.22 Main viewer-count microcache missing"
grep -Fq 'playlist_channel_counts.get' "$APP_DIR/app/templates/users.html" || die "Installed v11.22 Users & Playlists precomputed channel counts missing"
grep -Fq 'return /^(?:back(?:\s+to\b|\b)|go back\b)/i.test(text);' "$APP_DIR/app/static/panel_nav.js" || die "Installed v11.22 Backups navigation word-boundary fix missing"

# STREAMFORGE_V1122_CHANNEL_LIVE_SEARCH_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_LIVE_CATALOGUE_V1122' "$APP_DIR/app/main.py" || die "Installed v11.22 Main Channels live catalogue path missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_LIVE_SEARCH_NO_RELOAD_V1122' "$APP_DIR/app/static/app.js" || die "Installed v11.22 Main Channels no-reload search missing"
grep -Fq 'data-live-channel-filter-form' "$APP_DIR/app/templates/channels.html" || die "Installed v11.22 Main Channels live filter hook missing"
grep -Fq 'data-live-channel-search' "$APP_DIR/app/templates/channels.html" || die "Installed v11.22 Main Channels live search hook missing"
! grep -Fq 'data-channel-auto-search' "$APP_DIR/app/templates/channels.html" || die "Installed v11.22 still carries reloading search hook"
# STREAMFORGE_V1123_ONLINE_USERS_NETWORK_GRAPH_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_ONLINE_USERS_BACKGROUND_CACHE_V1123' "$APP_DIR/app/main.py" || die "Installed v11.23 Online Users background cache missing"
grep -Fq 'STREAMFORGE_MAIN_ONLINE_USERS_NONBLOCKING_PAGE_V1123' "$APP_DIR/app/main.py" || die "Installed v11.23 Online Users non-blocking page missing"
grep -Fq 'STREAMFORGE_MAIN_ONLINE_USERS_NONBLOCKING_ROUTE_V1123' "$APP_DIR/app/main.py" || die "Installed v11.23 Online Users non-blocking live-data missing"
grep -Fq 'STREAMFORGE_MAIN_ONLINE_USERS_NO_CACHE_COMMIT_V1123' "$APP_DIR/app/main.py" || die "Installed v11.23 Online Users cache-only ORM reload guard missing"
grep -Fq 'STREAMFORGE_MAIN_NODE_SESSION_BACKGROUND_TIMEOUT_V1123' "$APP_DIR/app/node_manager.py" || die "Installed v11.23 bounded viewer refresh timeout missing"
grep -Fq 'STREAMFORGE_MAIN_NETWORK_COUNTER_CHURN_GUARD_V1123' "$APP_DIR/app/system_metrics.py" || die "Installed v11.23 network counter-churn guard missing"
grep -Fq 'STREAMFORGE_MAIN_NETWORK_HISTORY_INTERVAL_AVERAGE_V1123' "$APP_DIR/app/metrics_history.py" || die "Installed v11.23 network history interval average missing"
grep -Fq 'STREAMFORGE_MAIN_NETWORK_HISTORY_STARTUP_CARRY_V1123' "$APP_DIR/app/metrics_history.py" || die "Installed v11.23 network startup carry guard missing"
# STREAMFORGE_V1124_BULK_NODE_ASSIGNMENT_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_ASYNC_V1124' "$APP_DIR/app/main.py" || die "Installed v11.50 Bulk Node background worker missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_FAST_RETURN_V1124' "$APP_DIR/app/main.py" || die "Installed v11.50 Bulk Node fast-return path missing"
grep -Fq 'STREAMFORGE_BULK_SELECTED_EAGER_V1124' "$APP_DIR/app/main.py" || die "Installed v11.50 selected-channel eager-load optimization missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_DURABLE_RETURN_V1137' "$APP_DIR/app/main.py" || die "Installed v11.50 durable Bulk Node Add queue missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_RESPONSE_FIRST_V1137' "$APP_DIR/app/main.py" || die "Installed v11.50 response-first Bulk Node Add path missing"
grep -Fq 'STREAMFORGE_BULK_RELAY_SELECTED_NODE_ONLY_V1137' "$APP_DIR/app/main.py" || die "Installed v11.50 selected-node-only relay path missing"
grep -Fq 'STREAMFORGE_BULK_RELAY_RESPONSE_FIRST_V1137' "$APP_DIR/app/main.py" || die "Installed v11.50 response-first relay queue missing"
grep -Fq 'queue_bulk_node_assignment_sync(removal_targets)' "$APP_DIR/app/main.py" || die "Installed v11.50 Bulk Node removal cleanup queue missing"
# STREAMFORGE_V1125_BULK_NODE_ASSIGNMENT_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_DIRECT_SQL_V1125' "$APP_DIR/app/main.py" || die "Installed v11.50 direct-SQL Bulk Node assignment missing"
grep -Fq 'STREAMFORGE_BULK_NODE_NO_ORM_HYDRATION_V1125' "$APP_DIR/app/main.py" || die "Installed v11.50 Bulk Node no-ORM hydration guard missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_AJAX_V1125' "$APP_DIR/app/main.py" || die "Installed v11.50 Bulk Node JSON response path missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_AJAX_UI_V1125' "$APP_DIR/app/static/app.js" || die "Installed v11.50 Bulk Node AJAX UI missing"
grep -Fq 'STREAMFORGE_BULK_NODE_ASSIGNMENT_NO_NATIVE_SPINNER_V1125' "$APP_DIR/app/static/panel_nav.js" || die "Installed v11.50 Bulk Node native spinner bypass missing"
# STREAMFORGE_V1126_LIVE_SESSIONS_GEO_TLS_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_VIEWER_GEO_BACKGROUND_V1126' "$APP_DIR/app/main.py" || die "Installed v11.50 Main non-blocking Live Sessions GeoIP cache missing"
grep -Fq 'STREAMFORGE_MAIN_VIEWER_HEARTBEAT_DETAIL_PREFETCH_V1126' "$APP_DIR/app/main.py" || die "Installed v11.50 heartbeat viewer detail prefetch missing"
grep -Fq 'STREAMFORGE_MAIN_LIVE_SESSION_FAST_DETAIL_RETRY_V1126' "$APP_DIR/app/templates/viewer_sessions.html" || die "Installed v11.50 Live Sessions fast detail retry missing"
grep -Fq 'STREAMFORGE_NODE_VIEWER_GEO_BACKGROUND_V1126' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node non-blocking viewer GeoIP cache missing"
grep -Fq 'STREAMFORGE_NODE_DNS01_TEST_AUTO_ISSUE_V1126' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node DNS-01 auto-issuance missing"
grep -Fq 'STREAMFORGE_MAIN_DNS01_TEST_AUTO_ISSUE_V1126' "$APP_DIR/app/main.py" || die "Installed v11.50 Main DNS-01 auto-issuance missing"
grep -Fq 'OnUnitActiveSec=2min' "$APP_DIR/node_agent/deploy/streamforge-node-tls.timer" || die "Installed v11.50 Node TLS retry timer missing"
grep -Fq 'OnUnitActiveSec=2min' "$APP_DIR/deploy/streamforge-main-tls.timer" || die "Installed v11.50 Main TLS retry timer missing"
grep -Fq 'STREAMFORGE_NODE_TARGETED_CHANNEL_PENDING_V1127' "$APP_DIR/app/sync_queue.py" || die "Installed v11.50 targeted channel pending queue missing"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_NO_FULL_SWEEP_V1127' "$APP_DIR/app/main.py" || die "Installed v11.50 heartbeat full-sweep guard missing"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_TARGETED_RETRY_V1127' "$APP_DIR/app/main.py" || die "Installed v11.50 targeted heartbeat retry worker missing"
grep -Fq 'node_ids={int(item) for item in remote_node_ids}' "$APP_DIR/app/main.py" || die "Installed v11.50 channel-save Node scope missing"
grep -Fq 'STREAMFORGE_BULK_NODE_TRUE_BATCH_V1127' "$APP_DIR/app/main.py" || die "Installed v11.50 Bulk Node true-batch worker missing"
grep -Fq 'STREAMFORGE_NODE_TRUE_BATCH_CONFIG_SYNC_V1127' "$APP_DIR/app/node_manager.py" || die "Installed v11.50 Main bulk Node config client missing"
grep -Fq 'STREAMFORGE_NODE_BULK_CONFIG_API_V1127' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node bulk config API missing"
grep -Fq 'STREAMFORGE_NODE_BULK_CONFIG_APPLY_V1127' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node supervisor bulk apply missing"
grep -Fq 'STREAMFORGE_NODE_PERIODIC_MAIN_HEARTBEAT_V1139' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 periodic Main heartbeat interval missing"
grep -Fq 'STREAMFORGE_NODE_PERIODIC_MAIN_HEARTBEAT_LOOP_V1139' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 periodic Main heartbeat loop missing"
grep -Fq 'STREAMFORGE_NODE_PERIODIC_MAIN_HEARTBEAT_START_V1139' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 periodic Main heartbeat startup missing"
grep -Fq 'STREAMFORGE_NODE_CHANNEL_LOG_CLEAR_PUBLIC_PREFIX_V1140' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node channel log clear public-prefix fix missing"
grep -Fq "f\"{(_CURRENT_PANEL_PREFIX.get() or '')}/channels/\"" "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node channel log clear prefix-aware implementation missing"

# STREAMFORGE_V1141_V1143_LOCAL_RELAY_UPTIME_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_LOCAL_RELAY_STICKY_RECONNECT_V1141' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 sticky Local-relay reconnect policy missing"
grep -Fq 'RELAY_RECONNECT_DELAY_MAX_SECONDS' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Local-relay reconnect window missing"
grep -Fq 'STREAMFORGE_NODE_LOCAL_RELAY_HLS_EOF_FIX_V1145' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Local-relay HLS EOF fix missing"
grep -Fq 'STREAMFORGE_NODE_HTTP_EOF_NON_HLS_ONLY_V1145' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 HLS-aware EOF reconnect guard missing"
grep -Fq 'STREAMFORGE_MAIN_TLS_RECONCILE_NO_LISTENER_FLAP_V1146' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v11.50 Main TLS listener-preservation fix missing"
grep -Fq 'STREAMFORGE_MAIN_NGINX_NOOP_RELOAD_SKIP_V1146' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v11.50 Main Nginx no-op reload suppression missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_RESPAWN_HEALTH_GATE_V1147' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Local-relay deferred respawn health gate missing"
grep -Fq 'STREAMFORGE_NODE_RELAY_WATCHDOG_HEALTH_GATE_V1147' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Local-relay watchdog health gate missing"
grep -Fq 'STREAMFORGE_NODE_UNKNOWN_ICON_QUIET_404_V1147' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node /icon quiet Python fallback missing"
grep -Fq 'STREAMFORGE_NODE_UNKNOWN_ICON_NGINX_QUIET_404_V1147' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v11.50 Node /icon Nginx quiet route missing"
# STREAMFORGE_V1150_NODE_REAL_CPU_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_REAL_CPU_PROC_STAT_V1148' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node real /proc/stat CPU sampler missing"
grep -Fq 'def _cpu_counters() -> tuple[int, int]:' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node CPU counter reader missing"
grep -Fq 'busy_delta / total_delta * 100.0' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node CPU busy-delta calculation missing"
grep -Fq '"cpu_metric_version": 2' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node real-CPU history version marker missing"
grep -Fq 'STREAMFORGE_NODE_REAL_CPU_HISTORY_V1148' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node legacy CPU-history filter missing"
# STREAMFORGE_V1150_NODE_ACCESS_ALIAS_PERSISTENCE_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_ACCESS_CROSS_PROCESS_WRITE_LOCK_V1149' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node cross-process access writer lock missing"
grep -Fq 'STREAMFORGE_NODE_MODE_SYNC_NO_ACCESS_ALIAS_CLOBBER_V1149' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node mode-sync alias clobber fix missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_SINGLE_OWNER_WRITES_V1150' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node mode-sync field-patch enforcement missing"
grep -Fq 'STREAMFORGE_NODE_NAME_PATCH_NO_ACCESS_ALIAS_CLOBBER_V1150' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Node-name field-patch enforcement missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_AUTHORITATIVE_FULL_WRITE_V1150' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 authoritative access full-write marker missing"
[[ "$(grep -Fc 'self.save_access()' "$APP_DIR/node_agent/app.py")" -eq 2 ]] || die "Installed v11.50 unrelated full access writers remain"
grep -Fq 'self.patch_access_file({"total_max_connections": int(self.total_max_connections)})' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 supervisor access merge path missing"
! grep -Fq 'reconnect_at_eof=relay_mode' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 regressed v11.45 HLS EOF behavior"
grep -Fq 'if reconnect_at_eof and not hls_http_input:' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 HLS manifests can still enter protocol EOF reconnect"
! grep -Fq 'reconnect_at_eof=relay_mode' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Local-relay still reconnects on normal HLS playlist EOF"
grep -Fq 'reconnect_at_eof=False' "$APP_DIR/node_agent/app.py" || die "Installed v11.50 Local-relay EOF reconnect disable missing"
grep -Fq 'STREAMFORGE_MAIN_RELAY_RUNTIME_STATE_HTTP_V1141' "$APP_DIR/app/main.py" || die "Installed v11.50 Main relay runtime-state response policy missing"
grep -Fq 'STREAMFORGE_MAIN_RELAY_LIVE_PID_GUARD_V1143' "$APP_DIR/app/main.py" || die "Installed v11.50 Main relay live-PID guard missing"
grep -Fq 'STREAMFORGE_MAIN_RELAY_RUNNING_STATE_RETRY_V1144' "$APP_DIR/app/main.py" || die "Installed v11.50 Main relay healthy-state retry guard missing"
grep -Fq 'def _local_relay_recent_media(channel: Channel) -> bool:' "$APP_DIR/app/main.py" || die "Installed v11.50 Main relay recent-media guard missing"
grep -Fq 'process_alive = local_channel_process_alive(channel)' "$APP_DIR/app/main.py" || die "Installed v11.50 Main relay live-PID evidence missing"
grep -Fq 'if state == "restarting" and not process_alive and not recent_media:' "$APP_DIR/app/main.py" || die "Installed v11.50 Main relay confirmed-restart gate missing"
grep -Fq 'STREAMFORGE_MAIN_RELAY_SEGMENT_ROTATION_404_V1143' "$APP_DIR/app/main.py" || die "Installed v11.50 relay segment rotation 404 policy missing"
! sed -n '/def local_relay_segment(/,/return Response(/p' "$APP_DIR/app/main.py" | grep -Fq '_local_relay_missing_exception' || die "Installed v11.50 relay segment miss still escalates to channel-level state"

# STREAMFORGE_V1128_V1129_TARGETED_PENDING_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_CATALOGUE_TARGETED_PENDING_V1128' "$APP_DIR/app/node_manager.py" || die "Installed v11.50 targeted catalogue pending fix missing"
grep -Fq 'STREAMFORGE_NODE_CATALOGUE_TRUE_BATCH_RECONCILE_V1135' "$APP_DIR/app/node_manager.py" || die "Installed v11.50 true-batch catalogue reconcile missing"
! sed -n '/def sync_node_channels(/,/def managed_tls_status(/p' "$APP_DIR/app/node_manager.py" | grep -Fq 'self.sync_channel_config(channel, current)' || die "Installed v11.50 catalogue reconcile still uses per-channel config requests"
grep -Fq 'STREAMFORGE_NODE_STALE_CATALOGUE_PENDING_CLEANUP_V1128' "$APP_DIR/app/main.py" || die "Installed v11.50 stale catalogue pending cleanup missing"
grep -Fq 'STREAMFORGE_NODE_TARGETED_LOGO_PENDING_V1129' "$APP_DIR/app/sync_queue.py" || die "Installed v11.50 targeted logo queue missing"
grep -Fq 'STREAMFORGE_NODE_HEARTBEAT_TARGETED_LOGO_RETRY_V1129' "$APP_DIR/app/main.py" || die "Installed v11.50 targeted logo heartbeat retry missing"
grep -Fq 'STREAMFORGE_NODE_LEGACY_GENERIC_PENDING_MIGRATION_V1129' "$APP_DIR/app/main.py" || die "Installed v11.50 legacy generic pending migration missing"
# STREAMFORGE_V1132_HEARTBEAT_AUTHORITATIVE_LIVENESS_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_HEARTBEAT_AUTHORITATIVE_LIVENESS_V1132' "$APP_DIR/app/node_manager.py" || die "Installed v11.50 heartbeat-authoritative controller liveness fix missing"
grep -Fq 'def node_liveness_recent(' "$APP_DIR/app/node_manager.py" || die "Installed v11.50 liveness grace helper missing"
grep -Fq 'def note_control_failure(' "$APP_DIR/app/node_manager.py" || die "Installed v11.50 control failure isolation helper missing"
grep -Fq 'recently_seen = node_controller.node_liveness_recent(node)' "$APP_DIR/app/main.py" || die "Installed v11.50 Nodes status heartbeat grace missing"
[[ "$(grep -Fc 'node.status = "offline"' "$APP_DIR/app/node_manager.py")" -eq 1 ]] || die "Installed v11.50 node_manager still writes offline outside heartbeat-aware helper"
! grep -Fq '.status = "offline"' "$APP_DIR/app/main.py" || die "Installed v11.50 Main still overwrites Node liveness directly"
grep -Fq '.stream-summary-cell [data-resolution]{color:#eef5fb;font-weight:800}' "$APP_DIR/app/static/style.css" && grep -Fq '.stream-summary-cell [data-bitrate]{color:#9fb3c7}' "$APP_DIR/app/static/style.css" || die "Installed v11.50 aligned Main metric styling implementation missing"

# STREAMFORGE_V112_WEBPLAYER_DEFAULT_AUDIO_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_CHANNELS_CATALOGUE_SQL_ORDER_V1111' "$APP_DIR/app/main.py" || die "Installed v11.22 Main Channels catalogue SQL order fix missing"
grep -Fq 'catalogue_order = channel_catalogue_sql_order()' "$APP_DIR/app/main.py" || die "Installed v11.22 Main Channels live catalogue does not use catalogue order"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_AUTOPLAY_MUTED_FALLBACK_V1111' "$APP_DIR/app/templates/player.html" || die "Installed v11.22 Main WebPlayer autoplay fallback missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_AUTOPLAY_MUTED_FALLBACK_V1111' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node WebPlayer autoplay fallback missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_BATCH_SETTINGS_V1112' "$APP_DIR/app/main.py" || die "Installed v11.22 Main WebPlayer batched settings lookup missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_PREFETCH_CATALOG_V1112' "$APP_DIR/app/main.py" || die "Installed v11.22 Main WebPlayer prefetched catalogue missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_DIRECT_MEMBERSHIP_LOOKUP_V1112' "$APP_DIR/app/main.py" || die "Installed v11.22 Main WebPlayer direct membership lookup missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_ASYNC_LOCAL_READY_V1112' "$APP_DIR/app/main.py" || die "Installed v11.22 Main WebPlayer async Local HLS readiness missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_WATCH_SETTINGS_ONCE_V1112' "$APP_DIR/app/main.py" || die "Installed v11.22 Main WebPlayer watch settings dedupe missing"
# STREAMFORGE_V1167_INSTALLED_SUPERSEDED_HLSJS_PRELOAD_GUARD_FIX:
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_SELF_HOSTED_HLSJS_V1163' "$APP_DIR/app/templates/player.html" || die "Installed v11.63 Main WebPlayer self-hosted HLS.js preload missing"
grep -Fq 'STREAMFORGE_MAIN_METRICS_TAIL_READ_V1113' "$APP_DIR/app/metrics_history.py" || die "Installed v11.22 metrics tail reader missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_LATEST_METRIC_TAIL_V1113' "$APP_DIR/app/main.py" || die "Installed v11.22 Dashboard metric tail path missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_FIRST_PAINT_POLL_DELAY_V1113' "$APP_DIR/app/static/app.js" || die "Installed v11.22 Dashboard first-paint poll delay missing"
grep -Fq 'STREAMFORGE_MAIN_CACHED_HLS_READY_STATE_V1113' "$APP_DIR/app/node_manager.py" || die "Installed v11.22 cached HLS readiness state missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_CACHED_READY_POOL_V1113' "$APP_DIR/app/load_balancer.py" || die "Installed v11.22 WebPlayer cached-ready pool missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_FAST_NODE_ROUTE_V1113' "$APP_DIR/app/main.py" || die "Installed v11.22 WebPlayer fast node route missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_FAST_CHANNEL_SWITCH_BUFFER_V1113' "$APP_DIR/app/templates/player.html" || die "Installed v11.22 WebPlayer fast switch buffer missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_DEFAULT_AUDIO_30_V112' "$APP_DIR/app/templates/player.html" || die "Installed v11.22 Main WebPlayer 30% default audio missing"
grep -Fq 'video.volume = 0.30;' "$APP_DIR/app/templates/player.html" || die "Installed v11.22 Main WebPlayer default volume is not 30%"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_DEFAULT_AUDIO_30_V112' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node WebPlayer 30% default audio missing"
grep -Fq 'video.defaultMuted=false;video.muted=false;video.volume=0.30;' "$APP_DIR/node_agent/app.py" || die "Installed v11.22 Node WebPlayer default volume is not 30%"
grep -Fq 'STREAMFORGE_WEBPLAYER_STABLE_EDGE_BUFFER_V77' "$APP_DIR/app/templates/player.html" || die "Installed v7.9 stable-edge player marker missing"
grep -Fq 'liveSyncDurationCount: 2' "$APP_DIR/app/templates/player.html" || die "Installed v7.9 two-segment live sync profile missing"
grep -Fq 'liveMaxLatencyDurationCount: 5' "$APP_DIR/app/templates/player.html" || die "Installed v7.9 normal live maximum latency missing"
grep -Fq 'liveMaxLatencyDurationCount: 6' "$APP_DIR/app/templates/player.html" || die "Installed v7.9 stable live maximum latency missing"
# STREAMFORGE_V1167_INSTALLED_SUPERSEDED_NORMAL_BUFFER_GUARD_FIX:
grep -Fq 'maxBufferLength: 12' "$APP_DIR/app/templates/player.html" || die "Installed v11.63 normal live buffer missing"
grep -Fq 'maxBufferLength: 12' "$APP_DIR/app/templates/player.html" || die "Installed v7.9 stable live buffer missing"
grep -Fq '"-tune", "zerolatency"' "$APP_DIR/app/ffmpeg.py" || die "Installed Main zero-latency encoder flags missing"
grep -Fq 'local_hls_list_size = 8 if youtube_source else 6' "$APP_DIR/app/ffmpeg.py" || die "Installed Main HLS window missing"
grep -Fq '"-tune", "zerolatency"' "$APP_DIR/node_agent/app.py" || die "Installed Node zero-latency encoder flags missing"
grep -Fq 'def _normalize_v200_state_payload' "$APP_DIR/node_agent/app.py" || die "Installed Node v2.0 state migration missing"
# STREAMFORGE_V1114_MAIN_PANEL_HOTPATH_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_ONLINE_ONLY_FIRST_PAINT_V1114' "$APP_DIR/app/main.py" || die "Installed v11.22 Dashboard online-only first paint missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_NODE_SQL_COUNTS_V1114' "$APP_DIR/app/main.py" || die "Installed v11.22 Dashboard SQL Node counts missing"
grep -Fq 'STREAMFORGE_MAIN_STATUS_NO_HISTORICAL_LOG_SCAN_V1114' "$APP_DIR/app/main.py" || die "Installed v11.22 status hot-path log-scan removal missing"
grep -Fq 'STREAMFORGE_MAIN_VIEWER_COUNTS_NO_NODE_RELATIONSHIPS_V1114' "$APP_DIR/app/main.py" || die "Installed v11.22 viewer-count relationship removal missing"
grep -Fq 'STREAMFORGE_MAIN_METRICS_CACHE_ONLY_VIEWERS_V1114' "$APP_DIR/app/main.py" || die "Installed v11.22 metrics cache-only viewer path missing"
grep -Fq 'STREAMFORGE_MAIN_METRICS_LOCAL_CACHE_ONLY_CHANNELS_V1114' "$APP_DIR/app/main.py" || die "Installed v11.22 metrics local/cache-only channel path missing"
grep -Fq 'STREAMFORGE_MAIN_USERS_ASSOCIATION_COUNT_ONLY_V1114' "$APP_DIR/app/main.py" || die "Installed v11.22 Users association-count path missing"
grep -Fq 'STREAMFORGE_MAIN_TEMPLATE_PERMISSION_CACHE_V1114' "$APP_DIR/app/main.py" || die "Installed v11.22 template permission cache missing"
grep -Fq 'STREAMFORGE_MAIN_DASHBOARD_METRICS_RELAXED_POLL_V1114' "$APP_DIR/app/static/app.js" || die "Installed v11.22 relaxed Dashboard metrics poll missing"
grep -Fq 'dashboard_http_outputs.get(channel.id)' "$APP_DIR/app/templates/dashboard.html" || die "Installed v11.22 Dashboard HLS action missing"
! sed -n '/def status_json(/,/return {"channels": payload/p' "$APP_DIR/app/main.py" | grep -Fq '_status_log_counts(db)' || die "Installed v11.22 status path still scans historical logs"
! sed -n '/def _metrics_channel_counts()/,/metrics_history = MetricsHistory/p' "$APP_DIR/app/main.py" | grep -Fq 'allow_remote_fetch=True' || die "Installed v11.22 metrics sampler still probes Remote Nodes"
rm -rf "$APP_DIR/venv"
if [[ -d "$LOCAL_NODE_DIR" ]]; then
  rm -rf "$LOCAL_NODE_DIR/venv"
fi
[[ ! -e "$APP_DIR/venv" ]] || die "Main venv directory could not be removed"
[[ ! -e "$LOCAL_NODE_DIR/venv" ]] || die "Node venv directory could not be removed"
grep -Fq 'STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_V49' "$APP_DIR/app/static/panel_nav.js" || die "Installed normal Main Panel navigation missing"
grep -Fq 'STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_HEAD_V49' "$APP_DIR/app/templates/base.html" || die "Installed early hidden-route guard missing"
grep -Fq 'STREAMFORGE_MAIN_HIDDEN_NATIVE_ROUTE_V49' "$APP_DIR/app/main.py" || die "Installed server hidden-route dispatch missing"
grep -Fq 'STREAMFORGE_MAIN_REQUEST_ADMIN_CACHE_V48' "$APP_DIR/app/auth.py" || die "Installed per-request admin cache missing"
grep -Fq 'STREAMFORGE_MAIN_CHANNEL_URL_BASE_ONCE_V48' "$APP_DIR/app/main.py" || die "Installed channel URL optimization missing"
grep -Fq 'STREAMFORGE_MAIN_MULTI_REMOTE_OUTPUT_EDITOR_EXEC_V55' "$APP_DIR/app/templates/channel_form.html" || die "Installed v5.5 Main multi-output editor fix missing"
grep -Fq 'STREAMFORGE_NODE_HIDE_MAIN_SYNC_SOURCE_V55' "$APP_DIR/node_agent/app.py" || die "Installed v5.5 Node source privacy fix missing"
grep -Fq 'STREAMFORGE_NODE_HIDE_MAIN_SYNC_SOURCE_LIST_V55' "$APP_DIR/node_agent/app.py" || die "Installed v5.5 Node source-list privacy fix missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_OUTER_ACCESS_RELOAD_V1151' "$APP_DIR/node_agent/app.py" || die "Installed v11.51 outer access-policy reload missing"
grep -Fq 'STREAMFORGE_NODE_GATEWAY_ERROR_PATH_PRIVACY_V1151' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v11.51 Node gateway error path privacy page missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_LOCAL_FAST_PATH_V55' "$APP_DIR/app/main.py" || die "Installed v5.5 Web Player fast path missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_ZERO_NETWORK_CATALOG_V55' "$APP_DIR/app/main.py" || die "Installed v5.5 Web Player zero-network catalogue missing"
grep -Fq 'STREAMFORGE_MAIN_LOCAL_HLS_READY_CACHE_V55' "$APP_DIR/app/node_manager.py" || die "Installed v5.5 HLS readiness cache missing"
! grep -Fq 'STREAMFORGE_MAIN_ZERO_FLASH_PANEL_NAV_V3060' "$APP_DIR/app/templates/base.html" || die "Installed legacy fetch navigation still present"
verify_webplayer_downloads
UPDATE_COMMITTED=1
trap - EXIT INT TERM
grep -Fq 'STREAMFORGE_DELEGATED_DNS01_V41' "$APP_DIR/scripts/apply_main_access.py" || die "Installed Main delegated DNS-01 flow missing"
grep -Fq 'STREAMFORGE_NODE_DNS01_CNAME_PREFLIGHT_V41' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed Node delegated DNS-01 CNAME flow missing"
[[ -x "$APP_DIR/scripts/acme_dns_hook.py" ]] || die "Installed Main DNS-01 hook missing"
[[ -x /usr/local/libexec/streamforge/acme_dns_hook.py ]] || die "Installed root-owned Main DNS-01 hook missing"
[[ -f /usr/local/libexec/streamforge/acme_dns_client.py ]] || die "Installed root-owned Main DNS-01 client missing"
[[ -f /etc/systemd/system/streamforge-main-tls.timer ]] || die "Installed Main DNS-01 retry timer missing"

grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_LIVE_ALIAS_ASSET_DOWNLOAD_V1158' "$APP_DIR/app/main.py" || die "Installed v11.58 Main brand live-alias/download runtime missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_ASSET_TRANSPORT_V1158' "$APP_DIR/app/node_manager.py" || die "Installed v11.58 Remote Node brand asset transport missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_BRAND_ASSET_SYNC_V1158' "$APP_DIR/node_agent/app.py" || die "Installed v11.58 Node brand asset receiver missing"
grep -Fq 'STREAMFORGE_NODE_UPDATE_JSON_MULTI_BRAND_THEME_V1158' "$APP_DIR/node_agent/app.py" || die "Installed v11.58 Node brand update.json theme missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_MULTI_BRAND_V1155' "$APP_DIR/app/main.py" || die "v11.55 Main multi-brand Web Player runtime missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_MULTI_BRAND_UI_V1155' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "v11.55 Web Player brand management UI missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_MULTI_BRAND_V1155' "$APP_DIR/node_agent/app.py" || die "v11.55 Node multi-brand Web Player runtime missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_FULL_SETTINGS_PARITY_V1159' "$APP_DIR/app/main.py" || die "Installed v11.59 Main brand settings parity missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_TRANSPORT_DERIVED_FIELDS_V1159' "$APP_DIR/app/node_manager.py" || die "Installed v11.59 brand transport normalization missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_BRAND_EFFECTIVE_CONTROLS_V1159' "$APP_DIR/node_agent/app.py" || die "Installed v11.59 Node brand effective controls missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_FULL_SETTINGS_UI_V1159' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed v11.59 brand full settings UI missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_ASSET_SINGLE_ROW_LAYOUT_V1163' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed v11.63 single-row brand asset UI missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_ASSET_SINGLE_ROW_LAYOUT_CSS_V1163' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed v11.63 single-row brand asset styling missing"
! grep -Fq 'name="brand_logo_url"' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed v11.63 brand logo URL input still present"
! grep -Fq 'name="brand_favicon_url"' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed v11.63 brand favicon URL input still present"
grep -Fq 'brand-assets-inline-grid' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed v11.63 brand logo/favicon single-row grid missing"
grep -Fq 'brand-asset-current' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed v11.63 current brand asset preview missing"
grep -Fq 'Remove current favicon' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed v11.63 current brand favicon remove control missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_VIEWER_INFO_CARD_PARITY_V1163' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed v11.63 brand viewer info card parity markup missing"
grep -Fq 'brand-viewer-info-option' "$APP_DIR/app/templates/node_webplayer_manage.html" || die "Installed v11.63 brand viewer info option styling missing"
grep -Fq 'STREAMFORGE_MAIN_WEBPLAYER_SELF_HOSTED_HLSJS_V1163' "$APP_DIR/app/templates/player.html" || die "Installed v11.63 Main Web Player local HLS.js preload missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_HLSJS_LOCAL_CDN_FALLBACK_V1163' "$APP_DIR/app/templates/player.html" || die "Installed v11.63 Main Web Player HLS.js fallback loader missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_PROGRESS_WATCHDOG_V1163' "$APP_DIR/app/templates/player.html" || die "Installed v11.63 Main Web Player progress watchdog missing"
grep -Fq 'STREAMFORGE_NODE_SELF_HOSTED_HLSJS_V1163' "$APP_DIR/node_agent/app.py" || die "Installed v11.63 Node local HLS.js support missing"
grep -Fq 'STREAMFORGE_NODE_SELF_HOSTED_HLSJS_BACKGROUND_FETCH_V1163' "$APP_DIR/node_agent/app.py" || die "Installed v11.63 Node background HLS.js cache fetch missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_HLSJS_LOCAL_CDN_FALLBACK_V1163' "$APP_DIR/node_agent/app.py" || die "Installed v11.63 Node Web Player HLS.js fallback loader missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_PROGRESS_WATCHDOG_V1163' "$APP_DIR/node_agent/app.py" || die "Installed v11.63 Node Web Player progress watchdog missing"
grep -Fq 'STREAMFORGE_NODE_HLSJS_UPDATE_PAYLOAD_V1163' "$APP_DIR/app/node_manager.py" || die "Installed v11.63 Node HLS.js update payload missing"
# STREAMFORGE_V1167_WEBPLAYER_NO_FALSE_RECONNECT_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_STALL_GUARD_HLS_FRESHNESS_V1167' "$APP_DIR/app/ffmpeg.py" || die "Installed v11.67 Main HLS-freshness stall guard missing"
grep -Fq 'STREAMFORGE_NODE_STALL_GUARD_HLS_FRESHNESS_V1167' "$APP_DIR/node_agent/app.py" || die "Installed v11.67 Node HLS-freshness stall guard missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_NON_DESTRUCTIVE_STALL_RECOVERY_V1167' "$APP_DIR/app/templates/player.html" || die "Installed v11.67 Main/Brand non-destructive stall recovery missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_NON_DESTRUCTIVE_STALL_RECOVERY_V1167' "$APP_DIR/node_agent/app.py" || die "Installed v11.67 Node non-destructive stall recovery missing"
grep -Fq 'STREAMFORGE_MAIN_HLS_BROWSER_SEGMENT_GRACE_V1167' "$APP_DIR/app/ffmpeg.py" || die "Installed v11.67 Main browser HLS segment grace missing"
grep -Fq 'STREAMFORGE_NODE_HLS_BROWSER_SEGMENT_GRACE_V1167' "$APP_DIR/node_agent/app.py" || die "Installed v11.67 Node browser HLS segment grace missing"
grep -Fq 'local_hls_delete_threshold = max(3, (30 + local_hls_time - 1) // local_hls_time)' "$APP_DIR/app/ffmpeg.py" || die "Installed v11.67 Main 30-second HLS retention missing"
grep -Fq 'local_hls_delete_threshold = max(3, (30 + local_hls_time - 1) // local_hls_time)' "$APP_DIR/node_agent/app.py" || die "Installed v11.67 Node 30-second HLS retention missing"
! grep -Fq "scheduleReconnect('No fresh HLS segment received. Reconnecting automatically…')" "$APP_DIR/app/templates/player.html" || die "Installed v11.67 destructive Main stalled-event reconnect still present"
! grep -Fq "retrySoon('No fresh HLS segment received. Reconnecting automatically…')" "$APP_DIR/node_agent/app.py" || die "Installed v11.67 destructive Node stalled-event reconnect still present"
# STREAMFORGE_V121_RUNTIME_AUDIT_FIX_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_MAIN_PUBLIC_DESIRED_FFMPEG_RESERVE_V121' "$APP_DIR/scripts/streamforge-public-start" || die "Installed v12.1 Main desired-FFmpeg public worker reserve missing"
grep -Fq 'STREAMFORGE_MAIN_FFMPEG_STDERR_EXIT_PRESERVE_V121' "$APP_DIR/app/ffmpeg.py" || die "Installed v12.1 Main FFmpeg stderr preservation missing"
grep -Fq 'STREAMFORGE_MAIN_CLOCK_SAFE_HLS_FRESHNESS_V121' "$APP_DIR/app/ffmpeg.py" || die "Installed v12.1 Main clock-safe HLS freshness missing"
grep -Fq 'STREAMFORGE_NODE_CLOCK_SAFE_HLS_FRESHNESS_V121' "$APP_DIR/node_agent/app.py" || die "Installed v12.1 Node clock-safe HLS freshness missing"
grep -Fq 'STREAMFORGE_NODE_FFMPEG_EXIT_REASON_PRESERVE_V121' "$APP_DIR/node_agent/app.py" || die "Installed v12.1 Node FFmpeg exit-reason preservation missing"
grep -Fq 'STREAMFORGE_MAIN_SUPERVISOR_CLIENT_DISCONNECT_SAFE_V121' "$APP_DIR/app/main_channel_supervisor.py" || die "Installed v12.1 Main supervisor BrokenPipe protection missing"
grep -Fq 'STREAMFORGE_MAIN_NGINX_PUBLIC_STATIC_CACHE_V122' "$APP_DIR/scripts/apply_main_access.py" || die "Installed v12.3 Main Nginx readable static-cache path missing"
grep -Fq 'STREAMFORGE_MAIN_LOGIN_CLEAN_REDIRECT_V122' "$APP_DIR/app/main.py" || die "Installed v12.3 clean login redirect missing"
grep -Fq 'STREAMFORGE_MAIN_LOGIN_ADDRESS_BAR_HIDE_V122' "$APP_DIR/app/templates/base.html" || die "Installed v12.3 inline login address-bar hiding missing"
# STREAMFORGE_V123_NODE_RUNTIME_CONSISTENCY_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_CROSS_PROCESS_SYNC_V123' "$APP_DIR/node_agent/app.py" || die "Installed v12.3 Node playlist-user cross-process persistence missing"
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_SIGNATURE_RELOAD_V123' "$APP_DIR/node_agent/app.py" || die "Installed v12.3 Node playlist-user signature reload missing"
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_AUTH_RELOAD_V123' "$APP_DIR/node_agent/app.py" || die "Installed v12.3 Node Xtream/WebPlayer user pre-auth reload missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_NO_STALE_USER_WRITE_V123' "$APP_DIR/node_agent/app.py" || die "Installed v12.3 Node public stale-user overwrite guard missing"
grep -Fq 'STREAMFORGE_NODE_LIVE_PLAYLIST_REOPEN_RESERVATION_V123' "$APP_DIR/node_agent/app.py" || die "Installed v12.3 Node live-playlist reservation recovery missing"
grep -Fq 'STREAMFORGE_NODE_PUBLIC_DESIRED_FFMPEG_RESERVE_V123' "$APP_DIR/node_agent/public_start.py" || die "Installed v12.3 Node desired-FFmpeg public worker reserve missing"
grep -Fq 'STREAMFORGE_NODE_V123_PUBLIC_START_ROOT_GUARD' "$APP_DIR/node_agent/app.py" || die "Installed v12.3 Node API partial-update root guard missing"
# STREAMFORGE_V124_WEBPLAYER_BRAND_RUNTIME_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_ALIAS_LIFECYCLE_V124' "$APP_DIR/app/main.py" || die "Installed v12.4 Web Player brand alias lifecycle fix missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_SIGNATURE_RELOAD_V124' "$APP_DIR/node_agent/app.py" || die "Installed v12.4 Node access snapshot signature reload missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_BRAND_CROSS_PROCESS_RELOAD_V124' "$APP_DIR/node_agent/app.py" || die "Installed v12.4 Node brand cross-process reload missing"
# STREAMFORGE_V125_AUTHORITATIVE_RUNTIME_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_TEST_SYNC_LOCAL_USER_AUTHORITATIVE_V125' "$APP_DIR/node_agent/app.py" || die "Installed v12.5 Test & Sync Node-local user preservation missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_SYNC_USER_IMMUTABLE_V125' "$APP_DIR/node_agent/app.py" || die "Installed v12.5 access sync user-registry isolation missing"
grep -Fq 'STREAMFORGE_NODE_ACCESS_WATCHER_USER_IMMUTABLE_V125' "$APP_DIR/node_agent/app.py" || die "Installed v12.5 access watcher user-registry isolation missing"
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_NO_READ_SIDE_WRITE_V125' "$APP_DIR/node_agent/app.py" || die "Installed v12.5 user read-side write removal missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_BRAND_DISK_AUTHORITATIVE_V125' "$APP_DIR/node_agent/app.py" || die "Installed v12.5 Node brand disk-authoritative lookup missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_SCHEME_INHERIT_V125' "$APP_DIR/app/main.py" || die "Installed v12.5 Web Player brand scheme inheritance missing"
grep -Fq 'STREAMFORGE_WEBPLAYER_BRAND_HOST_RETIRE_V125' "$APP_DIR/app/main.py" || die "Installed v12.5 Web Player brand host retirement missing"
# STREAMFORGE_V127_NODE_RUNTIME_OWNERSHIP_AND_PUBLIC_ROUTE_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_TEST_SYNC_MODE_USER_IMMUTABLE_V127' "$APP_DIR/node_agent/app.py" || die "Installed v12.7 Test & Sync mode user-registry isolation missing"
grep -Fq 'STREAMFORGE_NODE_PANEL_USER_SYNC_STREAM_USER_IMMUTABLE_V127' "$APP_DIR/node_agent/app.py" || die "Installed v12.7 panel-user sync user-registry isolation missing"
grep -Fq 'STREAMFORGE_NODE_CATEGORY_STATE_SEPARATE_WRITER_V127' "$APP_DIR/node_agent/app.py" || die "Installed v12.7 separate category writer missing"
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_DOWNLOAD_ACCESS_PATCH_V127' "$APP_DIR/node_agent/app.py" || die "Installed v12.7 Web Player download access patch missing"
grep -Fq 'STREAMFORGE_NODE_LEGACY_XTREAM_DIRECT_ROUTE_V127' "$APP_DIR/node_agent/app.py" || die "Installed v12.7 legacy Xtream route classification missing"
grep -Fq 'STREAMFORGE_NODE_LEGACY_XTREAM_DIRECT_ENDPOINT_V127' "$APP_DIR/node_agent/app.py" || die "Installed v12.7 legacy Xtream endpoint missing"
grep -Fq 'STREAMFORGE_NODE_STREAM_ROOT_PUBLIC_BACKEND_V127' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v12.7 stream-root public backend routing missing"
grep -Fq 'STREAMFORGE_NODE_LEGACY_XTREAM_DIRECT_NGINX_V127' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v12.7 legacy Xtream Nginx public routing missing"
# STREAMFORGE_V128_HTTP_BRAND_ROOT_INSTALLED_GUARD:
grep -Fq 'STREAMFORGE_NODE_HTTP_HOST_ROOT_PUBLIC_BACKEND_V128' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v12.8 HTTP Web Player Brand root public routing missing"
grep -Fq 'def nginx_http_front_blocks(' "$APP_DIR/node_agent/apply_node_tls.py" || die "Installed v12.8 host-aware HTTP frontend builder missing"
# STREAMFORGE_V129_NODE_PLAYLIST_USER_EXPIRY_EDIT_INSTALLED_GUARD:
grep -Fq 'STREAMFORGE_NODE_PLAYLIST_USER_EDIT_EXPIRY_V129' "$APP_DIR/node_agent/app.py" || die "Installed v12.12 Node Playlist User expiry edit support missing"
grep -Fq 'current.expires_at = submitted_expires_at' "$APP_DIR/node_agent/app.py" || die "Installed v12.12 Node Playlist User expiry save path missing"
# STREAMFORGE_V1210_NODE_PLAY_START_HOT_PATH_INSTALLED_GUARDS:
grep -Fq 'STREAMFORGE_NODE_PUBLIC_SHARED_PANEL_CONNECTIVITY_V1210' "$APP_DIR/node_agent/app.py" || die "Installed v12.12 shared Main-connectivity playback gate missing"
grep -Fq 'PANEL_CONNECTIVITY_FILE' "$APP_DIR/node_agent/app.py" || die "Installed v12.12 shared Main-connectivity state file missing"
grep -Fq 'STREAMFORGE_NODE_SHARED_HEARTBEAT_FAST_PRIME_V1210' "$APP_DIR/node_agent/app.py" || die "Installed v12.12 shared heartbeat startup prime missing"
grep -Fq 'if NODE_MODE == "public":' "$APP_DIR/node_agent/app.py" || die "Installed v12.12 public workers can still run foreground Main heartbeat"
grep -Fq 'STREAMFORGE_NODE_DIRECT_STREAM_SINGLE_READY_V1210' "$APP_DIR/node_agent/app.py" || die "Installed v12.12 single-channel direct readiness lookup missing"
grep -Fq 'STREAMFORGE_NODE_DIRECT_STREAM_HLS_FAST_READY_V1210' "$APP_DIR/node_agent/app.py" || die "Installed v12.12 direct stream local-HLS readiness fast path missing"
grep -Fq 'if channel is None or not _direct_channel_hls_ready(channel):' "$APP_DIR/node_agent/app.py" || die "Installed v12.12 direct stream lookup does not validate only selected HLS channel"
# STREAMFORGE_V1212_NODE_WEBPLAYER_IDENTITY_INSTALLED_GUARD:
grep -Fq 'STREAMFORGE_NODE_WEBPLAYER_IDENTITY_DISK_AUTHORITATIVE_V1212' "$APP_DIR/node_agent/app.py" || die "Installed v12.12 Node Web Player live logo identity reload missing"
[[ -r /var/cache/streamforge/main-static/style.css ]] || die "Installed v12.3 published style.css missing"
[[ -r /var/cache/streamforge/main-static/panel_nav.js ]] || die "Installed v12.3 published panel_nav.js missing"
log "SUCCESS — StreamForge v12.12 is live."
log "ASN database: $ASN_FILE"
log "Country database: $COUNTRY_FILE"
log "Backup: $BACKUP_DIR"
log "Managed HTTPS repaired: StreamForge now uses isolated Certbot 5.7.0, independent from the global application Python runtime; Node certificate Retry still clears only StreamForge local backoff."
