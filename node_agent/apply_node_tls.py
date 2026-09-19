#!/usr/bin/env python3
"""StreamForge Remote Node managed TLS reconciler.

Runs as root from streamforge-node-tls.service.  The Node Agent keeps its
native authenticated/public HTTP listener independent from TLS.  This helper
issues/renews Let's Encrypt certificates through the Agent's webroot ACME
route and publishes TLS-only Nginx listeners that proxy to the native Agent.
"""
from __future__ import annotations

import ipaddress
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# STREAMFORGE_NODE_DELEGATED_DNS01_V41
for _candidate in (Path(__file__).resolve().parent, Path("/usr/local/libexec/streamforge-node"), Path("/opt/streamforge-node")):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
from acme_dns_client import account_for, api_url as acme_dns_api_url, cname_matches, cname_name, ensure_account

ACCESS_FILE = Path(os.getenv("STREAMFORGE_NODE_ACCESS_FILE", "/var/lib/streamforge-node/access.json"))
ENV_FILE = Path("/etc/streamforge-node.env")
ACME_WEBROOT = Path("/var/lib/streamforge-node/acme-webroot")
TLS_ROOT = Path("/var/lib/streamforge-node/tls")
ACME_DNS_ACCOUNT_FILE = Path(os.getenv("STREAMFORGE_ACME_DNS_ACCOUNT_FILE", "/var/lib/streamforge-node/acme-dns/accounts.json"))
STATUS_FILE = TLS_ROOT / "status.json"
REQUEST_FILE = Path("/var/lib/streamforge-node/tls-reconcile.request")
SITE_AVAILABLE = Path("/etc/nginx/sites-available/streamforge-node-tls")
SITE_ENABLED = Path("/etc/nginx/sites-enabled/streamforge-node-tls")
AUTH_CACHE_CONF = Path("/etc/nginx/conf.d/streamforge-node-auth-cache.conf")
AUTH_CACHE_DIR = Path("/var/cache/nginx/streamforge-node-auth")
NGINX_RUNTIME_FINGERPRINT = TLS_ROOT / "nginx-runtime.sha256"
LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")
STREAMFORGE_CERTBOT_BIN = Path("/opt/streamforge-certbot/bin/certbot")
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.I)


def log(message: str) -> None:
    print(f"[StreamForge Node TLS] {message}", flush=True)


# STREAMFORGE_NODE_ISOLATED_CERTBOT_V1030: never run distro Certbot inside
# StreamForge's global Python site-packages.  v10.29 proved that a modern
# cryptography/pyOpenSSL application runtime can make an older distro Certbot
# fail at import time.  Prefer the dedicated, dependency-isolated Certbot venv.
def certbot_binary() -> str | None:
    if STREAMFORGE_CERTBOT_BIN.is_file() and os.access(STREAMFORGE_CERTBOT_BIN, os.X_OK):
        return str(STREAMFORGE_CERTBOT_BIN)
    return shutil.which("certbot")


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        rows = ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return values
    for row in rows:
        raw = row.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def safe_hostname(value: str) -> str:
    host = str(value or "").strip().lower().rstrip(".")
    if not host or not HOST_RE.fullmatch(host):
        return ""
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        return host


def safe_access_host(value: str) -> str:
    """Return a normalized DNS hostname or IP suitable for HTTP Host matching."""
    host = str(value or "").strip().lower().rstrip(".")
    if not host:
        return ""
    try:
        return ipaddress.ip_address(host).compressed.lower()
    except ValueError:
        return host if HOST_RE.fullmatch(host) else ""


def load_access() -> dict[str, Any]:
    try:
        data = json.loads(ACCESS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


# STREAMFORGE_NODE_HTTP_CANONICAL_REDIRECT_CERT_V42: even an http:// canonical
# Node alias gets a DNS-01 certificate on 443, allowing a warning-free TLS
# handshake followed by the Node application's redirect back to HTTP.
def desired_hosts(access: dict[str, Any]) -> dict[str, set[int]]:
    """Return hostname -> TLS listener ports.

    Port 443 is deliberately enabled for every DNS alias, including an
    http:// canonical alias, because an HTTPS -> HTTP redirect can only happen
    after a successful TLS handshake.  Explicit non-standard HTTPS ports are
    added as additional TLS listeners.
    """
    result: dict[str, set[int]] = {}
    for key in ("panel_urls", "stream_urls"):
        values = access.get(key) or []
        if not isinstance(values, list):
            values = str(values or "").replace("\r", "").split("\n")
        for raw in values:
            try:
                parsed = urllib.parse.urlsplit(str(raw or "").strip())
            except ValueError:
                continue
            if parsed.scheme not in {"http", "https"}:
                continue
            host = safe_hostname(parsed.hostname or "")
            if not host:
                continue
            ports = result.setdefault(host, {443})
            if parsed.scheme == "https" and parsed.port:
                ports.add(max(1, min(65535, int(parsed.port))))
    return result


def desired_http_ports(access: dict[str, Any], control_port: int) -> set[int]:
    # STREAMFORGE_NODE_HTTP_FRONT_V65R5: Nginx owns native HTTP too, so the
    # single-worker control backend and multi-worker public backend can share
    # the same external port without sharing a Python process.
    ports: set[int] = {max(1, min(65535, int(control_port)))}
    for key in ("panel_urls", "stream_urls"):
        values = access.get(key) or []
        if not isinstance(values, list):
            values = str(values or "").replace("\r", "").split("\n")
        for raw in values:
            try:
                parsed = urllib.parse.urlsplit(str(raw or "").strip())
            except ValueError:
                continue
            if parsed.scheme != "http" or not parsed.hostname:
                continue
            ports.add(max(1, min(65535, int(parsed.port or 80))))
    return ports


def desired_http_host_ports(access: dict[str, Any], control_port: int) -> dict[str, set[int]]:
    # STREAMFORGE_NODE_HTTP_HOST_ROOT_PUBLIC_BACKEND_V128:
    # Native HTTP historically used one catch-all server block, so an exact
    # root Web Player/Playlist alias still fell through to the 8810 control
    # worker even after v12.7 fixed the per-host TLS server. Build host-aware
    # HTTP listeners as well. Every configured alias is reachable on the
    # native HTTP control port; explicit http:// aliases may add another port.
    result: dict[str, set[int]] = {}
    native_port = max(1, min(65535, int(control_port)))
    for key in ("panel_urls", "stream_urls"):
        values = access.get(key) or []
        if not isinstance(values, list):
            values = str(values or "").replace("\r", "").split("\n")
        for raw in values:
            try:
                parsed = urllib.parse.urlsplit(str(raw or "").strip())
            except ValueError:
                continue
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            host = safe_access_host(parsed.hostname or "")
            if not host:
                continue
            ports = result.setdefault(host, {native_port})
            if parsed.scheme == "http":
                ports.add(max(1, min(65535, int(parsed.port or 80))))
    return result


def cert_paths(host: str) -> tuple[Path, Path]:
    root = LETSENCRYPT_LIVE / host
    return root / "fullchain.pem", root / "privkey.pem"


def certificate_ready(host: str) -> bool:
    fullchain, privkey = cert_paths(host)
    return fullchain.is_file() and privkey.is_file()


# STREAMFORGE_NODE_ISOLATED_CERTBOT_RENEWAL_V1030: StreamForge's own TLS
# timer renews the isolated-Certbot lineage when fewer than 30 days remain, so
# future renewal never depends on a distro certbot.timer that may import the
# application's global Python packages.
def certificate_needs_renewal(host: str, *, within_seconds: int = 30 * 24 * 60 * 60) -> bool:
    fullchain, _ = cert_paths(host)
    if not fullchain.is_file():
        return True
    try:
        checked = subprocess.run(
            ["openssl", "x509", "-checkend", str(max(0, int(within_seconds))), "-noout", "-in", str(fullchain)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return checked.returncode != 0


def renewal_uses_delegated_dns01(host: str) -> bool:
    conf = Path("/etc/letsencrypt/renewal") / f"{host}.conf"
    try:
        text = conf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return (
        "authenticator = manual" in text
        and "acme_dns_hook.py auth" in text
        and "manual_auth_hook" in text
    )


def retry_after_path(host: str) -> Path:
    return TLS_ROOT / f"{host}.retry-after"


def read_retry_after(host: str) -> int:
    try:
        return max(0, int(retry_after_path(host).read_text(encoding="utf-8").strip()))
    except (OSError, ValueError):
        return 0


def write_retry_after(host: str, epoch: int) -> None:
    TLS_ROOT.mkdir(parents=True, exist_ok=True)
    retry_after_path(host).write_text(str(max(0, int(epoch))) + "\n", encoding="utf-8")


def failure_retry_epoch(detail: str) -> int:
    # STREAMFORGE_NODE_ACME_RATE_LIMIT_BACKOFF_V40: honor Certbot/CA retry-after
    # messages and otherwise avoid repeatedly consuming authorization attempts.
    now = int(time.time())
    text = str(detail or "")
    match = re.search(r"retry after (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC", text, re.I)
    if match:
        try:
            parsed = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return max(now + 60, int(parsed.timestamp()) + 30)
        except ValueError:
            pass
    lowered = text.lower()
    if "no valid a records" in lowered or "no valid aaaa records" in lowered or "nxdomain" in lowered:
        return now + 3600
    if "unauthorized" in lowered or "invalid response" in lowered or "challenge" in lowered:
        return now + 900
    return now + 600


def issue_certificate(host: str, *, control_port: int, delegation: dict[str, Any], force_retry: bool = False) -> tuple[bool, str]:
    # STREAMFORGE_NODE_DNS01_CNAME_PREFLIGHT_V41: register a restricted
    # delegated TXT account first, then wait for the operator's one-time CNAME.
    existing = certificate_ready(host)
    if existing and renewal_uses_delegated_dns01(host):
        retry_after_path(host).unlink(missing_ok=True)
        # STREAMFORGE_NODE_DNS01_REGISTRATION_METADATA_PERSIST_V43:
        # Keep the original one-time CNAME visible after issue/restart/update.
        # Read the persisted restricted account; never register a replacement
        # target merely because the certificate is already active.
        try:
            account = account_for(host, ACME_DNS_ACCOUNT_FILE)
        except Exception as exc:
            delegation.update({
                "state": "registration_state_missing",
                "certificate_ready": True,
                "cname_ready": False,
                "dns01_managed": False,
                "error": f"Stored DNS-01 registration is unavailable: {exc}",
            })
            return True, "existing certificate active; stored DNS-01 registration unavailable"
        target = str(account.get("fulldomain") or "").rstrip(".")
        cname_ok, cname_detail = cname_matches(host, target)
        delegation.update({
            "provider": "acme-dns",
            "api_url": str(account.get("api_url") or acme_dns_api_url()),
            "cname_name": cname_name(host),
            "cname_target": target + ".",
            "cname_check": cname_detail,
            "state": "certificate_ready" if cname_ok else "waiting_cname_migration",
            "certificate_ready": True,
            "cname_ready": bool(cname_ok),
            "dns01_managed": True,
        })
        if not certificate_needs_renewal(host):
            return True, "existing delegated DNS-01 certificate"
        if not cname_ok:
            return True, f"current certificate active; renewal pending: {cname_detail}"
        log(f"{host}: certificate is within the 30-day renewal window; renewing with isolated Certbot")
    try:
        account = ensure_account(host, ACME_DNS_ACCOUNT_FILE)
    except Exception as exc:
        delegation.update({"state": "registration_failed", "certificate_ready": False, "error": str(exc)})
        return False, str(exc)
    delegation.update({
        "provider": "acme-dns",
        "api_url": str(account.get("api_url") or acme_dns_api_url()),
        "cname_name": cname_name(host),
        "cname_target": str(account.get("fulldomain") or "").rstrip(".") + ".",
        "certificate_ready": bool(existing),
        "dns01_managed": False,
    })
    cname_ok, cname_detail = cname_matches(host, str(account.get("fulldomain") or ""))
    delegation["cname_ready"] = bool(cname_ok)
    delegation["cname_check"] = cname_detail
    if not cname_ok:
        delegation["state"] = "waiting_cname_migration" if existing else "waiting_cname"
        return (True, f"current certificate active; {cname_detail}") if existing else (False, cname_detail)

    # STREAMFORGE_NODE_CERTBOT_FORCE_RETRY_V1028: an explicit admin Retry
    # request may bypass only StreamForge's local retry-deferred file. It does
    # not bypass or alter any retry/rate-limit response returned by the CA.
    if force_retry:
        retry_after_path(host).unlink(missing_ok=True)
    now = int(time.time())
    retry_after = read_retry_after(host)
    if retry_after > now:
        remaining = retry_after - now
        delegation["state"] = "retry_deferred"
        return False, f"ACME retry deferred for {remaining}s after the previous authorization failure"
    certbot = certbot_binary()
    hook = Path("/usr/local/libexec/streamforge-node/acme_dns_hook.py")
    if not certbot or not hook.is_file():
        delegation["state"] = "unavailable"
        return False, "certbot or the DNS-01 hook is not installed on the Node"
    command = [
        certbot, "certonly", "--manual", "--preferred-challenges", "dns",
        "--manual-auth-hook", f"/usr/bin/python3 {hook} auth",
        "--manual-cleanup-hook", f"/usr/bin/python3 {hook} cleanup",
        "--non-interactive", "--agree-tos", "--register-unsafely-without-email",
        "--cert-name", host, "-d", host,
    ]
    command.append("--force-renewal" if existing else "--keep-until-expiring")
    delegation["state"] = "migrating_renewal" if existing else "issuing"
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=300, check=False)
    except subprocess.TimeoutExpired:
        delegation["state"] = "migration_failed" if existing else "issue_failed"
        write_retry_after(host, int(time.time()) + 600)
        return (True, "current certificate active; DNS-01 migration timed out") if existing else (False, "certbot timed out after 300 seconds")
    detail = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())[-4000:]
    if completed.returncode != 0 or not certificate_ready(host):
        failure = detail or f"certbot exited {completed.returncode}"
        delegation.update({"state": "migration_failed" if existing else "issue_failed", "error": failure[-1200:]})
        write_retry_after(host, failure_retry_epoch(failure))
        return (True, f"current certificate active; DNS-01 migration failed: {failure}") if existing else (False, failure)
    retry_after_path(host).unlink(missing_ok=True)
    delegation.update({"state": "certificate_ready", "certificate_ready": True, "dns01_managed": True})
    return True, detail or "certificate issued"

# STREAMFORGE_NGINX_SSL_REJECT_COMPAT_V1033: ssl_reject_handshake was added in
# Nginx 1.19.4. Ubuntu 22.04 can still ship Nginx 1.18, where emitting the
# directive makes the entire managed HTTPS config fail nginx -t. Keep strict
# unknown-SNI handshake rejection when the installed Nginx supports it; on
# older builds omit only the optional reject vhost so the real certificate
# vhost can still publish HTTPS.
def nginx_supports_ssl_reject_handshake() -> bool:
    nginx = shutil.which("nginx")
    if not nginx:
        return False
    try:
        completed = subprocess.run([nginx, "-v"], text=True, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    text = f"{completed.stdout}\n{completed.stderr}"
    match = re.search(r"(?:nginx|openresty)/(\d+)\.(\d+)\.(\d+)", text, re.I)
    if not match:
        return False
    return tuple(int(part) for part in match.groups()) >= (1, 19, 4)


# STREAMFORGE_NODE_UNKNOWN_TLS_SNI_REJECT_V44: reject unknown TLS SNI so
# a browser never receives another configured Node hostname's certificate.
def nginx_reject_server(port: int) -> str:
    return "\n".join([
        "server {",
        f"    listen {port} ssl default_server;",
        f"    listen [::]:{port} ssl default_server;",
        "    server_name _;",
        "    ssl_reject_handshake on;",
        "    access_log off;",
        "    log_not_found off;",
        "}",
        "",
    ])


def nginx_gateway_error_lines() -> list[str]:
    # STREAMFORGE_NODE_GATEWAY_ERROR_PATH_PRIVACY_V1151:
    # Nginx owns 502/504 responses when a Node control/public backend closes or
    # times out.  Return a tiny local page (no Nginx version banner) and let the
    # browser replace the visible URL with the configured one-segment alias/root.
    # This never touches media auth or successful playback traffic.
    body = (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Service unavailable</title><style>html,body{margin:0;min-height:100%;background:#05090e;color:#edf4fb;font-family:system-ui,-apple-system,Segoe UI,sans-serif}"
        "body{min-height:100vh;display:grid;place-items:center}.c{width:min(390px,calc(100vw - 28px));box-sizing:border-box;padding:26px 22px;border:1px solid #233244;border-radius:16px;background:#0b131d;text-align:center}"
        "h2{margin:0 0 8px;font-size:21px}p{margin:0;color:#a9b9c8}</style></head><body><div class='c'><h2>Service temporarily unavailable</h2>"
        "<p>Please try again in a moment.</p></div><script>(function(){try{var p=location.pathname||'/';var a=p.split('/').filter(Boolean);var r=['web-player','playlist','node-play','_sf-media','player_api.php','get.php','live','channel-logos'];var i=a.findIndex(function(x){return r.indexOf(x)>=0;});var b='/';if(i>0)b='/'+a.slice(0,i).join('/');else if(i<0&&a.length>1)b='/'+a[0];else if(i<0&&a.length===1)b='/'+a[0];history.replaceState(null,'',b);}catch(e){}})();</script></body></html>"
    )
    escaped = body.replace('\\', '\\\\').replace('$', '\\$').replace('"', '\\"')
    return [
        "    location @streamforge_gateway_error {",
        "        internal;",
        "        default_type text/html;",
        "        add_header Cache-Control \"no-store, no-cache, must-revalidate\" always;",
        "        add_header Pragma \"no-cache\" always;",
        "        add_header X-StreamForge-Gateway-Error \"1\" always;",
        f'        return 503 "{escaped}";',
        "    }",
    ]


def nginx_http_front_server(
    port: int,
    control_backend_port: int,
    public_backend_port: int,
    auth_cache_seconds: int,
    *,
    server_name: str = "_",
    default_server: bool = True,
    public_root: bool = False,
) -> str:
    default_suffix = " default_server" if default_server else ""
    return "\n".join([
        "server {",
        f"    listen {port}{default_suffix};",
        f"    listen [::]:{port}{default_suffix};",
        f"    server_name {server_name};",
        "    client_max_body_size 1g;",
        "    proxy_connect_timeout 10s;",
        "    proxy_read_timeout 900s;",
        "    proxy_send_timeout 900s;",
        "    # STREAMFORGE_NODE_UNKNOWN_ICON_NGINX_QUIET_404_V1147: unknown /icon/* probes never reach Python or logs.",
        "    location ^~ /icon/ {",
        "        access_log off;",
        "        log_not_found off;",
        "        return 404;",
        "    }",
        "    # STREAMFORGE_NODE_HTTP_FRONT_V65R5",
        "    location ^~ /_streamforge_node_hls/ {",
        "        internal;",
        "        alias /var/lib/streamforge-node/hls/;",
        "        sendfile on;",
        "        tcp_nopush on;",
        "        access_log off;",
        "        add_header Cache-Control \"no-store\" always;",
        "        add_header Access-Control-Allow-Origin \"*\" always;",
        "    }",
        "    # STREAMFORGE_NODE_LIVE_PLAYLIST_NO_OPEN_FILE_CACHE_V77: FFmpeg writes index.m3u8 via temp-file + rename.",
        "    # Never keep the playlist file descriptor in open_file_cache, otherwise Nginx can serve the old inode",
        "    # while new ~2s HLS segments are already present on disk. Segments keep the high-concurrency file cache.",
        "    location ~ \"^/_sf-node-media/(?<sf_playback>[A-Za-z0-9_-]{8,96})/(?<sf_sid>[A-Za-z0-9._~-]{1,96})/(?<sf_channel_ref>[A-Za-z0-9_-]{1,96})/(?<sf_hls_key>[A-Za-z0-9_-]{1,96})/(?<sf_file>index\\.m3u8)$\" {",
        "        set $sf_auth_token $sf_playback;",
        "        set $sf_auth_sid $sf_sid;",
        "        set $sf_auth_channel_ref $sf_channel_ref;",
        "        set $sf_auth_hls_key $sf_hls_key;",
        "        set $sf_auth_ip $remote_addr;",
        "        auth_request /_streamforge_node_media_auth_live;",
        "        alias /var/lib/streamforge-node/hls/$sf_hls_key/$sf_file;",
        "        types { application/vnd.apple.mpegurl m3u8; }",
        "        default_type application/vnd.apple.mpegurl;",
        "        sendfile off;",
        "        open_file_cache off;",
        "        access_log off;",
        "        log_not_found off;",
        "        add_header Cache-Control \"no-store, no-cache, must-revalidate, max-age=0\" always;",
        "        add_header Pragma \"no-cache\" always;",
        "        add_header Expires \"0\" always;",
        "        add_header Access-Control-Allow-Origin \"*\" always;",
        "        add_header X-StreamForge-Media-Path \"node-nginx-live-playlist-v77\" always;",
        "    }",
        "    location ~ \"^/_sf-node-media/(?<sf_playback>[A-Za-z0-9_-]{8,96})/(?<sf_sid>[A-Za-z0-9._~-]{1,96})/(?<sf_channel_ref>[A-Za-z0-9_-]{1,96})/(?<sf_hls_key>[A-Za-z0-9_-]{1,96})/(?<sf_file>[A-Za-z0-9_.-]+\\.(?:ts|m4s|aac|mp3|key))$\" {",
        "        set $sf_auth_token $sf_playback;",
        "        set $sf_auth_sid $sf_sid;",
        "        set $sf_auth_channel_ref $sf_channel_ref;",
        "        set $sf_auth_hls_key $sf_hls_key;",
        "        set $sf_auth_ip $remote_addr;",
        "        auth_request /_streamforge_node_media_auth;",
        "        alias /var/lib/streamforge-node/hls/$sf_hls_key/$sf_file;",
        "        types { application/vnd.apple.mpegurl m3u8; video/mp2t ts; video/iso.segment m4s; audio/aac aac; audio/mpeg mp3; }",
        "        default_type application/octet-stream;",
        "        sendfile on;",
        "        aio threads;",
        "        tcp_nopush on;",
        "        open_file_cache max=100000 inactive=30s;",
        "        open_file_cache_valid 15s;",
        "        open_file_cache_min_uses 2;",
        "        open_file_cache_errors off;",
        "        access_log off;",
        "        log_not_found off;",
        "        add_header Cache-Control \"no-store\" always;",
        "        add_header Access-Control-Allow-Origin \"*\" always;",
        "        add_header X-StreamForge-Media-Path \"node-nginx-direct-v65\" always;",
        "    }",
        "    # STREAMFORGE_NODE_LIVE_SESSION_HEARTBEAT_NGINX_V90: live playlist auth uses a separate cache key.",
        "    # STREAMFORGE_NODE_LAST_ACTIVITY_2S_V91: cap positive live-playlist auth caching at 2s",
        "    # so Redis last_seen and the Live Sessions Last Activity column advance with the 2s UI refresh.",
        "    location = /_streamforge_node_media_auth_live {",
        "        internal;",
        f"        proxy_pass http://127.0.0.1:{public_backend_port}/_internal/node-media-auth;",
        "        proxy_pass_request_body off;",
        "        proxy_set_header Content-Length \"\";",
        "        proxy_set_header Host $host;",
        "        proxy_set_header X-StreamForge-Media-Auth \"1\";",
        "        proxy_set_header X-StreamForge-Viewer-Heartbeat \"1\";",
        "        proxy_set_header X-StreamForge-Playback-Key $sf_auth_token;",
        "        proxy_set_header X-StreamForge-Session $sf_auth_sid;",
        "        proxy_set_header X-StreamForge-Channel-Ref $sf_auth_channel_ref;",
        "        proxy_set_header X-StreamForge-HLS-Key $sf_auth_hls_key;",
        "        proxy_set_header X-Forwarded-For \"\";",
        "        proxy_set_header X-StreamForge-Forwarded-Client-IP $http_x_forwarded_for;",
        "        proxy_set_header X-StreamForge-Client-IP $sf_auth_ip;",
        "        proxy_cache streamforge_node_auth;",
        "        proxy_cache_key \"$sf_auth_ip|$sf_auth_token|$sf_auth_sid|$sf_auth_channel_ref|$sf_auth_hls_key|live\";",
        "        proxy_cache_valid 200 2s;",
        "        proxy_cache_valid 401 403 404 2s;",
        "        proxy_cache_lock on;",
        "        proxy_cache_lock_timeout 2s;",
        "        # STREAMFORGE_NODE_LIVE_AUTH_BACKGROUND_REFRESH_V1231:",
        "        # Never hold a live playlist reload behind the 2s heartbeat refresh.",
        "        # A previously authorized request is served stale while Nginx updates",
        "        # authorization/Redis activity in the background. Initial auth remains synchronous.",
        "        proxy_cache_background_update on;",
        "        proxy_cache_use_stale updating;",
        "    }",
        "    location = /_streamforge_node_media_auth {",
        "        internal;",
        f"        proxy_pass http://127.0.0.1:{public_backend_port}/_internal/node-media-auth;",
        "        proxy_pass_request_body off;",
        "        proxy_set_header Content-Length \"\";",
        "        proxy_set_header Host $host;",
        "        proxy_set_header X-StreamForge-Media-Auth \"1\";",
        "        proxy_set_header X-StreamForge-Playback-Key $sf_auth_token;",
        "        proxy_set_header X-StreamForge-Session $sf_auth_sid;",
        "        proxy_set_header X-StreamForge-Channel-Ref $sf_auth_channel_ref;",
        "        proxy_set_header X-StreamForge-HLS-Key $sf_auth_hls_key;",
        "        # STREAMFORGE_NODE_MEDIA_AUTH_PROXY_IP_V67: keep the auth subrequest peer loopback.",
        "        proxy_set_header X-Forwarded-For \"\";",
        "        # STREAMFORGE_NODE_MULTI_URL_PROXY_CLIENT_IP_V68: preserve an upstream proxy/CDN viewer chain.",
        "        proxy_set_header X-StreamForge-Forwarded-Client-IP $http_x_forwarded_for;",
        "        proxy_set_header X-StreamForge-Client-IP $sf_auth_ip;",
        "        proxy_cache streamforge_node_auth;",
        "        proxy_cache_key \"$sf_auth_ip|$sf_auth_token|$sf_auth_sid|$sf_auth_channel_ref|$sf_auth_hls_key\";",
        f"        proxy_cache_valid 200 {max(5, min(300, int(auth_cache_seconds)))}s;",
        "        proxy_cache_valid 401 403 404 2s;",
        "        proxy_cache_lock on;",
        "        proxy_cache_lock_timeout 2s;",
        "        proxy_cache_use_stale updating;",
        "    }",
        "    # STREAMFORGE_NODE_CONTROL_API_PRECEDENCE_V65R8: API control routes must win before public substring routes.",
        "    location ~ \"(?:^|/)api/v1/\" {",
        f"        proxy_pass http://127.0.0.1:{control_backend_port};",
        "        proxy_http_version 1.1;",
        "        proxy_buffering off;",
        "        proxy_request_buffering off;",
        "        proxy_set_header Host $http_host;",
        "        proxy_set_header X-Forwarded-Host $http_host;",
        "        proxy_set_header X-Forwarded-Port $server_port;",
        "        proxy_set_header X-Forwarded-Proto http;",
        "        proxy_set_header X-StreamForge-Node-HTTP-Proxy 1;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_set_header Connection \"\";",
        "    }",
        "    # STREAMFORGE_NODE_LEGACY_XTREAM_DIRECT_NGINX_V127: old /user/pass/id.m3u8|ts belongs to public workers.",
        "    location ~ \"^/[^/]+/[^/]+/[0-9]+\\.(?:m3u8|ts)$\" {",
        f"        proxy_pass http://127.0.0.1:{public_backend_port};",
        "        proxy_intercept_errors on;",
        "        error_page 502 504 = @streamforge_gateway_error;",
        "        proxy_http_version 1.1;",
        "        proxy_buffering off;",
        "        proxy_request_buffering off;",
        "        proxy_set_header Host $http_host;",
        "        proxy_set_header X-Forwarded-Host $http_host;",
        "        proxy_set_header X-Forwarded-Port $server_port;",
        "        proxy_set_header X-Forwarded-Proto http;",
        "        proxy_set_header X-StreamForge-Node-HTTP-Proxy 1;",
        "        proxy_set_header X-StreamForge-Node-Media-XAccel 1;",
        "        proxy_set_header X-StreamForge-Node-Media-FastPath 1;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_set_header Connection \"\";",
        "    }",
        "    location ~ \"(?:^|/)(?:web-player(?:/|$)|playlist/|node-play/|_sf-media/|player_api\\.php$|get\\.php$|live/|channel-logos/)\" {",
        f"        proxy_pass http://127.0.0.1:{public_backend_port};",
        "        proxy_intercept_errors on;",
        "        error_page 502 504 = @streamforge_gateway_error;",
        "        proxy_http_version 1.1;",
        "        proxy_buffering off;",
        "        proxy_request_buffering off;",
        "        proxy_set_header Host $http_host;",
        "        proxy_set_header X-Forwarded-Host $http_host;",
        "        proxy_set_header X-Forwarded-Port $server_port;",
        "        proxy_set_header X-Forwarded-Proto http;",
        "        proxy_set_header X-StreamForge-Node-HTTP-Proxy 1;",
        "        proxy_set_header X-StreamForge-Node-Media-XAccel 1;",
        "        proxy_set_header X-StreamForge-Node-Media-FastPath 1;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_set_header Connection \"\";",
        "    }",
        *(
            [
                "    # STREAMFORGE_NODE_HTTP_HOST_ROOT_PUBLIC_BACKEND_V128: exact HTTP Playlist/App root uses public workers.",
                "    location = / {",
                f"        proxy_pass http://127.0.0.1:{public_backend_port};",
                "        proxy_intercept_errors on;",
                "        error_page 502 504 = @streamforge_gateway_error;",
                "        proxy_http_version 1.1;",
                "        proxy_buffering off;",
                "        proxy_request_buffering off;",
                "        proxy_set_header Host $http_host;",
                "        proxy_set_header X-Forwarded-Host $http_host;",
                "        proxy_set_header X-Forwarded-Port $server_port;",
                "        proxy_set_header X-Forwarded-Proto http;",
                "        proxy_set_header X-StreamForge-Node-HTTP-Proxy 1;",
                "        proxy_set_header X-StreamForge-Node-Media-XAccel 1;",
                "        proxy_set_header X-Real-IP $remote_addr;",
                "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
                "        proxy_set_header Connection \"\";",
                "    }",
            ] if public_root else []
        ),
        "    location / {",
        f"        proxy_pass http://127.0.0.1:{control_backend_port};",
        "        proxy_intercept_errors on;",
        "        error_page 502 504 = @streamforge_gateway_error;",
        "        proxy_http_version 1.1;",
        "        proxy_buffering off;",
        "        proxy_request_buffering off;",
        "        proxy_set_header Host $http_host;",
        "        proxy_set_header X-Forwarded-Host $http_host;",
        "        proxy_set_header X-Forwarded-Port $server_port;",
        "        proxy_set_header X-Forwarded-Proto http;",
        "        proxy_set_header X-StreamForge-Node-HTTP-Proxy 1;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_set_header Connection \"\";",
        "    }",
        *nginx_gateway_error_lines(),
        "}",
        "",
    ])


def host_has_root_stream_alias(access: dict[str, Any], host: str) -> bool:
    # STREAMFORGE_NODE_STREAM_ROOT_PUBLIC_BACKEND_V127:
    # The application already gives an exact Playlist/App root priority over a
    # Panel/API root on the same authority. Mirror that decision in Nginx so a
    # branded/root Web Player never enters the single control worker first.
    wanted = safe_access_host(host)
    if not wanted:
        return False
    values = access.get("stream_urls") or []
    if not isinstance(values, list):
        values = str(values or "").replace("\r", "").split("\n")
    for raw in values:
        try:
            parsed = urllib.parse.urlsplit(str(raw or "").strip())
        except ValueError:
            continue
        if safe_access_host(parsed.hostname or "") != wanted:
            continue
        if not parsed.path.rstrip("/"):
            return True
    return False


def nginx_server(host: str, ports: set[int], control_backend_port: int, public_backend_port: int, auth_cache_seconds: int, *, public_root: bool = False) -> str:
    fullchain, privkey = cert_paths(host)
    listeners: list[str] = []
    for port in sorted(ports):
        listeners.append(f"    listen {port} ssl;")
        listeners.append(f"    listen [::]:{port} ssl;")
    return "\n".join([
        "server {",
        *listeners,
        f"    server_name {host};",
        f"    ssl_certificate {fullchain};",
        f"    ssl_certificate_key {privkey};",
        "    ssl_protocols TLSv1.2 TLSv1.3;",
        "    client_max_body_size 1g;",
        "    proxy_connect_timeout 10s;",
        "    proxy_read_timeout 900s;",
        "    proxy_send_timeout 900s;",
        "    # STREAMFORGE_NODE_UNKNOWN_ICON_NGINX_QUIET_404_V1147: unknown /icon/* probes never reach Python or logs.",
        "    location ^~ /icon/ {",
        "        access_log off;",
        "        log_not_found off;",
        "        return 404;",
        "    }",
        "    # STREAMFORGE_NODE_HLS_XACCEL_LOCATION_V63",
        "    location ^~ /_streamforge_node_hls/ {",
        "        internal;",
        "        alias /var/lib/streamforge-node/hls/;",
        "        sendfile on;",
        "        tcp_nopush on;",
        "        access_log off;",
        "        add_header Cache-Control \"no-store\" always;",
        "        add_header Access-Control-Allow-Origin \"*\" always;",
        "    }",
        "    # STREAMFORGE_NODE_NGINX_DIRECT_MEDIA_V65: media playlist and",
        "    # segments stay entirely inside Nginx after cached authorization.",
        "    # STREAMFORGE_NODE_LIVE_PLAYLIST_NO_OPEN_FILE_CACHE_V77: FFmpeg writes index.m3u8 via temp-file + rename.",
        "    # Never keep the playlist file descriptor in open_file_cache, otherwise Nginx can serve the old inode",
        "    # while new ~2s HLS segments are already present on disk. Segments keep the high-concurrency file cache.",
        "    location ~ \"^/_sf-node-media/(?<sf_playback>[A-Za-z0-9_-]{8,96})/(?<sf_sid>[A-Za-z0-9._~-]{1,96})/(?<sf_channel_ref>[A-Za-z0-9_-]{1,96})/(?<sf_hls_key>[A-Za-z0-9_-]{1,96})/(?<sf_file>index\\.m3u8)$\" {",
        "        set $sf_auth_token $sf_playback;",
        "        set $sf_auth_sid $sf_sid;",
        "        set $sf_auth_channel_ref $sf_channel_ref;",
        "        set $sf_auth_hls_key $sf_hls_key;",
        "        set $sf_auth_ip $remote_addr;",
        "        auth_request /_streamforge_node_media_auth_live;",
        "        alias /var/lib/streamforge-node/hls/$sf_hls_key/$sf_file;",
        "        types { application/vnd.apple.mpegurl m3u8; }",
        "        default_type application/vnd.apple.mpegurl;",
        "        sendfile off;",
        "        open_file_cache off;",
        "        access_log off;",
        "        log_not_found off;",
        "        add_header Cache-Control \"no-store, no-cache, must-revalidate, max-age=0\" always;",
        "        add_header Pragma \"no-cache\" always;",
        "        add_header Expires \"0\" always;",
        "        add_header Access-Control-Allow-Origin \"*\" always;",
        "        add_header X-StreamForge-Media-Path \"node-nginx-live-playlist-v77\" always;",
        "    }",
        "    location ~ \"^/_sf-node-media/(?<sf_playback>[A-Za-z0-9_-]{8,96})/(?<sf_sid>[A-Za-z0-9._~-]{1,96})/(?<sf_channel_ref>[A-Za-z0-9_-]{1,96})/(?<sf_hls_key>[A-Za-z0-9_-]{1,96})/(?<sf_file>[A-Za-z0-9_.-]+\\.(?:ts|m4s|aac|mp3|key))$\" {",
        "        set $sf_auth_token $sf_playback;",
        "        set $sf_auth_sid $sf_sid;",
        "        set $sf_auth_channel_ref $sf_channel_ref;",
        "        set $sf_auth_hls_key $sf_hls_key;",
        "        set $sf_auth_ip $remote_addr;",
        "        auth_request /_streamforge_node_media_auth;",
        "        alias /var/lib/streamforge-node/hls/$sf_hls_key/$sf_file;",
        "        types { application/vnd.apple.mpegurl m3u8; video/mp2t ts; video/iso.segment m4s; audio/aac aac; audio/mpeg mp3; }",
        "        default_type application/octet-stream;",
        "        sendfile on;",
        "        aio threads;",
        "        tcp_nopush on;",
        "        open_file_cache max=100000 inactive=30s;",
        "        open_file_cache_valid 15s;",
        "        open_file_cache_min_uses 2;",
        "        open_file_cache_errors off;",
        "        access_log off;",
        "        log_not_found off;",
        "        add_header Cache-Control \"no-store\" always;",
        "        add_header Access-Control-Allow-Origin \"*\" always;",
        "        add_header X-StreamForge-Media-Path \"node-nginx-direct-v65\" always;",
        "    }",
        "    # STREAMFORGE_NODE_LIVE_SESSION_HEARTBEAT_NGINX_V90: live playlist auth uses a separate cache key.",
        "    # STREAMFORGE_NODE_LAST_ACTIVITY_2S_V91: cap positive live-playlist auth caching at 2s",
        "    # so Redis last_seen and the Live Sessions Last Activity column advance with the 2s UI refresh.",
        "    location = /_streamforge_node_media_auth_live {",
        "        internal;",
        f"        proxy_pass http://127.0.0.1:{public_backend_port}/_internal/node-media-auth;",
        "        proxy_pass_request_body off;",
        "        proxy_set_header Content-Length \"\";",
        "        proxy_set_header Host $host;",
        "        proxy_set_header X-StreamForge-Media-Auth \"1\";",
        "        proxy_set_header X-StreamForge-Viewer-Heartbeat \"1\";",
        "        proxy_set_header X-StreamForge-Playback-Key $sf_auth_token;",
        "        proxy_set_header X-StreamForge-Session $sf_auth_sid;",
        "        proxy_set_header X-StreamForge-Channel-Ref $sf_auth_channel_ref;",
        "        proxy_set_header X-StreamForge-HLS-Key $sf_auth_hls_key;",
        "        proxy_set_header X-Forwarded-For \"\";",
        "        proxy_set_header X-StreamForge-Forwarded-Client-IP $http_x_forwarded_for;",
        "        proxy_set_header X-StreamForge-Client-IP $sf_auth_ip;",
        "        proxy_cache streamforge_node_auth;",
        "        proxy_cache_key \"$sf_auth_ip|$sf_auth_token|$sf_auth_sid|$sf_auth_channel_ref|$sf_auth_hls_key|live\";",
        "        proxy_cache_valid 200 2s;",
        "        proxy_cache_valid 401 403 404 2s;",
        "        proxy_cache_lock on;",
        "        proxy_cache_lock_timeout 2s;",
        "        # STREAMFORGE_NODE_LIVE_AUTH_BACKGROUND_REFRESH_V1231:",
        "        # Never hold a live playlist reload behind the 2s heartbeat refresh.",
        "        # A previously authorized request is served stale while Nginx updates",
        "        # authorization/Redis activity in the background. Initial auth remains synchronous.",
        "        proxy_cache_background_update on;",
        "        proxy_cache_use_stale updating;",
        "    }",
        "    location = /_streamforge_node_media_auth {",
        "        internal;",
        f"        proxy_pass http://127.0.0.1:{public_backend_port}/_internal/node-media-auth;",
        "        proxy_pass_request_body off;",
        "        proxy_set_header Content-Length \"\";",
        "        proxy_set_header Host $host;",
        "        proxy_set_header X-StreamForge-Media-Auth \"1\";",
        "        proxy_set_header X-StreamForge-Playback-Key $sf_auth_token;",
        "        proxy_set_header X-StreamForge-Session $sf_auth_sid;",
        "        proxy_set_header X-StreamForge-Channel-Ref $sf_auth_channel_ref;",
        "        proxy_set_header X-StreamForge-HLS-Key $sf_auth_hls_key;",
        "        # STREAMFORGE_NODE_MEDIA_AUTH_PROXY_IP_V67: keep the auth subrequest peer loopback.",
        "        proxy_set_header X-Forwarded-For \"\";",
        "        # STREAMFORGE_NODE_MULTI_URL_PROXY_CLIENT_IP_V68: preserve an upstream proxy/CDN viewer chain.",
        "        proxy_set_header X-StreamForge-Forwarded-Client-IP $http_x_forwarded_for;",
        "        proxy_set_header X-StreamForge-Client-IP $sf_auth_ip;",
        "        proxy_cache streamforge_node_auth;",
        "        proxy_cache_key \"$sf_auth_ip|$sf_auth_token|$sf_auth_sid|$sf_auth_channel_ref|$sf_auth_hls_key\";",
        f"        proxy_cache_valid 200 {max(5, min(300, int(auth_cache_seconds)))}s;",
        "        proxy_cache_valid 401 403 404 2s;",
        "        proxy_cache_lock on;",
        "        proxy_cache_lock_timeout 2s;",
        "        proxy_cache_use_stale updating;",
        "    }",
        "    # Public Playlist/App/API requests use the private multi-worker pool.",
        "    # STREAMFORGE_NODE_CONTROL_API_PRECEDENCE_V65R8: API control routes must win before public substring routes.",
        "    location ~ \"(?:^|/)api/v1/\" {",
        f"        proxy_pass http://127.0.0.1:{control_backend_port};",
        "        proxy_http_version 1.1;",
        "        proxy_buffering off;",
        "        proxy_request_buffering off;",
        "        proxy_set_header Host $http_host;",
        "        proxy_set_header X-Forwarded-Host $http_host;",
        "        proxy_set_header X-Forwarded-Port $server_port;",
        "        proxy_set_header X-Forwarded-Proto https;",
        "        proxy_set_header X-StreamForge-Node-TLS-Proxy 1;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_set_header Connection \"\";",
        "    }",
        "    # STREAMFORGE_NODE_LEGACY_XTREAM_DIRECT_NGINX_V127: old /user/pass/id.m3u8|ts belongs to public workers.",
        "    location ~ \"^/[^/]+/[^/]+/[0-9]+\\.(?:m3u8|ts)$\" {",
        f"        proxy_pass http://127.0.0.1:{public_backend_port};",
        "        proxy_intercept_errors on;",
        "        error_page 502 504 = @streamforge_gateway_error;",
        "        proxy_http_version 1.1;",
        "        proxy_buffering off;",
        "        proxy_request_buffering off;",
        "        proxy_set_header Host $http_host;",
        "        proxy_set_header X-Forwarded-Host $http_host;",
        "        proxy_set_header X-Forwarded-Port $server_port;",
        "        proxy_set_header X-Forwarded-Proto https;",
        "        proxy_set_header X-StreamForge-Node-TLS-Proxy 1;",
        "        proxy_set_header X-StreamForge-Node-Media-XAccel 1;",
        "        proxy_set_header X-StreamForge-Node-Media-FastPath 1;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_set_header Connection \"\";",
        "    }",
        "    location ~ \"(?:^|/)(?:web-player(?:/|$)|playlist/|node-play/|_sf-media/|player_api\\.php$|get\\.php$|live/|channel-logos/)\" {",
        f"        proxy_pass http://127.0.0.1:{public_backend_port};",
        "        proxy_intercept_errors on;",
        "        error_page 502 504 = @streamforge_gateway_error;",
        "        proxy_http_version 1.1;",
        "        proxy_buffering off;",
        "        proxy_request_buffering off;",
        "        proxy_set_header Host $http_host;",
        "        proxy_set_header X-Forwarded-Host $http_host;",
        "        proxy_set_header X-Forwarded-Port $server_port;",
        "        proxy_set_header X-Forwarded-Proto https;",
        "        proxy_set_header X-StreamForge-Node-TLS-Proxy 1;",
        "        proxy_set_header X-StreamForge-Node-Media-XAccel 1;",
        "        proxy_set_header X-StreamForge-Node-Media-FastPath 1;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_set_header Connection \"\";",
        "    }",
        *(
            [
                "    # STREAMFORGE_NODE_STREAM_ROOT_PUBLIC_BACKEND_V127: exact Playlist/App root uses public workers.",
                "    location = / {",
                f"        proxy_pass http://127.0.0.1:{public_backend_port};",
                "        proxy_intercept_errors on;",
                "        error_page 502 504 = @streamforge_gateway_error;",
                "        proxy_http_version 1.1;",
                "        proxy_buffering off;",
                "        proxy_request_buffering off;",
                "        proxy_set_header Host $http_host;",
                "        proxy_set_header X-Forwarded-Host $http_host;",
                "        proxy_set_header X-Forwarded-Port $server_port;",
                "        proxy_set_header X-Forwarded-Proto https;",
                "        proxy_set_header X-StreamForge-Node-TLS-Proxy 1;",
                "        proxy_set_header X-StreamForge-Node-Media-XAccel 1;",
                "        proxy_set_header X-Real-IP $remote_addr;",
                "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
                "        proxy_set_header Connection \"\";",
                "    }",
            ] if public_root else []
        ),
        "    location / {",
        f"        proxy_pass http://127.0.0.1:{control_backend_port};",
        "        proxy_intercept_errors on;",
        "        error_page 502 504 = @streamforge_gateway_error;",
        "        proxy_http_version 1.1;",
        "        proxy_buffering off;",
        "        proxy_request_buffering off;",
        "        proxy_set_header Host $http_host;",
        "        proxy_set_header X-Forwarded-Host $http_host;",
        "        proxy_set_header X-Forwarded-Port $server_port;",
        "        proxy_set_header X-Forwarded-Proto https;",
        "        proxy_set_header X-StreamForge-Node-TLS-Proxy 1;",
        "        proxy_set_header X-StreamForge-Node-Media-XAccel 1;",
        "        proxy_set_header X-Real-IP $remote_addr;",
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "        proxy_set_header Connection \"\";",
        "    }",
        *nginx_gateway_error_lines(),
        "}",
        "",
    ])


def _nginx_runtime_fingerprint(config_text: str, cache_text: str) -> str:
    """Track config and certificate contents so the 2-minute timer is a no-op."""
    digest = hashlib.sha256()
    digest.update(config_text.encode("utf-8"))
    digest.update(b"\0")
    digest.update(cache_text.encode("utf-8"))
    certificate_paths = sorted(set(re.findall(
        r"(?m)^\s*ssl_certificate(?:_key)?\s+([^;]+);", config_text
    )))
    for raw_path in certificate_paths:
        path = Path(raw_path.strip().strip('"\''))
        digest.update(b"\0")
        digest.update(str(path).encode("utf-8", errors="replace"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()


def _nginx_master_count() -> int:
    count = 0
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = cmdline.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="ignore")
        except OSError:
            continue
        if command.startswith("nginx: master process"):
            count += 1
    return count


def install_nginx_config(blocks: list[str]) -> tuple[bool, str]:
    nginx = shutil.which("nginx")
    if not nginx:
        return False, "nginx is not installed on the Node"
    SITE_AVAILABLE.parent.mkdir(parents=True, exist_ok=True)
    SITE_ENABLED.parent.mkdir(parents=True, exist_ok=True)
    previous = SITE_AVAILABLE.read_bytes() if SITE_AVAILABLE.exists() else None
    previous_link = SITE_ENABLED.is_symlink() or SITE_ENABLED.exists()
    previous_cache = AUTH_CACHE_CONF.read_bytes() if AUTH_CACHE_CONF.exists() else None
    cache_text = (
        "# STREAMFORGE_NODE_AUTH_CACHE_V65\n"
        "proxy_cache_path /var/cache/nginx/streamforge-node-auth levels=1:2 "
        "keys_zone=streamforge_node_auth:32m max_size=256m inactive=10m use_temp_path=off;\n"
    ) if blocks else ""
    config_text = "# Managed by StreamForge Remote Node TLS.\n# Native Node HTTP listener remains separate.\n\n" + "\n".join(blocks) if blocks else ""
    previous_fingerprint = NGINX_RUNTIME_FINGERPRINT.read_text(encoding="utf-8", errors="ignore").strip() if NGINX_RUNTIME_FINGERPRINT.exists() else ""
    desired_fingerprint = _nginx_runtime_fingerprint(config_text, cache_text)
    if blocks:
        AUTH_CACHE_CONF.parent.mkdir(parents=True, exist_ok=True)
        AUTH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            shutil.chown(AUTH_CACHE_DIR, user="www-data", group="www-data")
        except Exception:
            pass
        AUTH_CACHE_CONF.write_text(cache_text, encoding="utf-8")
        os.chmod(AUTH_CACHE_CONF, 0o644)
        temp = SITE_AVAILABLE.with_suffix(".tmp")
        temp.write_text(config_text, encoding="utf-8")
        os.chmod(temp, 0o644)
        temp.replace(SITE_AVAILABLE)
        if SITE_ENABLED.exists() or SITE_ENABLED.is_symlink():
            SITE_ENABLED.unlink()
        SITE_ENABLED.symlink_to(SITE_AVAILABLE)
    else:
        SITE_ENABLED.unlink(missing_ok=True)
        SITE_AVAILABLE.unlink(missing_ok=True)
        AUTH_CACHE_CONF.unlink(missing_ok=True)
    tested = subprocess.run([nginx, "-t"], text=True, capture_output=True, check=False)
    if tested.returncode != 0:
        if previous is None:
            SITE_AVAILABLE.unlink(missing_ok=True)
        else:
            SITE_AVAILABLE.write_bytes(previous)
        if previous_link and SITE_AVAILABLE.exists():
            SITE_ENABLED.unlink(missing_ok=True)
            SITE_ENABLED.symlink_to(SITE_AVAILABLE)
        elif not previous_link:
            SITE_ENABLED.unlink(missing_ok=True)
        if previous_cache is None:
            AUTH_CACHE_CONF.unlink(missing_ok=True)
        else:
            AUTH_CACHE_CONF.write_bytes(previous_cache)
        return False, (tested.stderr or tested.stdout or "nginx -t failed")[-3000:]
    subprocess.run(["systemctl", "enable", "nginx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    active = subprocess.run(["systemctl", "is-active", "--quiet", "nginx"], check=False).returncode == 0
    # STREAMFORGE_NODE_NGINX_RELOAD_DEDUP_V1232: the TLS timer runs every two
    # minutes. Reload only for an actual config/certificate change. Repeated
    # no-op reloads otherwise strand old HLS keep-alive worker generations.
    if active and previous_fingerprint == desired_fingerprint:
        return True, "nginx HTTP/TLS listener configuration unchanged"
    # Clean up already-accumulated generations once; future no-op timer runs
    # are skipped by the fingerprint above.
    action = "restart" if _nginx_master_count() > 1 else "reload-or-restart"
    reloaded = subprocess.run(["systemctl", action, "nginx"], text=True, capture_output=True, check=False)
    if reloaded.returncode != 0:
        return False, (reloaded.stderr or reloaded.stdout or "nginx reload failed")[-3000:]
    NGINX_RUNTIME_FINGERPRINT.parent.mkdir(parents=True, exist_ok=True)
    NGINX_RUNTIME_FINGERPRINT.write_text(desired_fingerprint + "\n", encoding="utf-8")
    os.chmod(NGINX_RUNTIME_FINGERPRINT, 0o600)
    return True, "nginx HTTP/TLS listener configuration applied"


# STREAMFORGE_NODE_HTTP_FRONT_BOOTSTRAP_V1032:
# Keep the public HTTP entrypoint independent from ACME work.  SSH/fresh
# installs restart the loopback control/public backends first; this fast path
# ensures Nginx is listening on the native HTTP port before certificate
# issuance, DNS propagation checks, or CA backoff can delay TLS reconciliation.
def tcp_listener_ready(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1.0):
            return True
    except OSError:
        return False


# STREAMFORGE_NODE_FRESH_NGINX_HEALTH_BOOTSTRAP_V1084:
# A fresh distro Nginx may keep its old/default port-80 worker alive briefly
# after a graceful reload.  A TCP connect can therefore succeed even though
# the StreamForge proxy server block has not been published yet.  Verify the
# authenticated Node health route itself before accepting an existing front.
def http_front_health_ready(port: int, token: str) -> bool:
    token = str(token or "").strip()
    if not token:
        return False
    request = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}/api/v1/health",
        headers={"X-Node-Token": token, "Connection": "close"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            if int(getattr(response, "status", 0) or 0) != 200:
                return False
            payload = json.loads(response.read(1024 * 1024).decode("utf-8", errors="replace"))
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        return False
    return bool(isinstance(payload, dict) and payload.get("ok") is True and str(payload.get("version") or "").strip())


def nginx_http_front_blocks(
    access: dict[str, Any],
    control_port: int,
    control_backend_port: int,
    public_backend_port: int,
    auth_cache_seconds: int,
) -> list[str]:
    # STREAMFORGE_NODE_HTTP_HOST_ROOT_PUBLIC_BACKEND_V128:
    # Keep one catch-all control-oriented frontend for unknown hosts/API health,
    # then add exact host blocks only where a root Playlist/App alias must use
    # the public pool. Panel roots remain on the control worker.
    blocks = [
        nginx_http_front_server(
            port, control_backend_port, public_backend_port, auth_cache_seconds,
            server_name="_", default_server=True, public_root=False,
        )
        for port in sorted(desired_http_ports(access, control_port))
    ]
    for host, ports in sorted(desired_http_host_ports(access, control_port).items()):
        if not host_has_root_stream_alias(access, host):
            continue
        for port in sorted(ports):
            blocks.append(nginx_http_front_server(
                port, control_backend_port, public_backend_port, auth_cache_seconds,
                server_name=host, default_server=False, public_root=True,
            ))
    return blocks


def bootstrap_http_front(access: dict[str, Any], control_port: int, control_backend_port: int, public_backend_port: int, auth_cache_seconds: int, node_token: str) -> tuple[bool, str]:
    nginx = shutil.which("nginx")
    if not nginx:
        return False, "nginx is not installed on the Node"

    # Preserve an already-working StreamForge HTTP/TLS configuration whenever
    # possible.  This avoids dropping a valid 443 listener during a Node update.
    tested = subprocess.run([nginx, "-t"], text=True, capture_output=True, check=False)
    if tested.returncode == 0:
        subprocess.run(["systemctl", "enable", "nginx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        restarted = subprocess.run(["systemctl", "reload-or-restart", "nginx"], text=True, capture_output=True, check=False)
        if restarted.returncode == 0 and http_front_health_ready(control_port, node_token):
            return True, "existing StreamForge nginx HTTP/TLS frontend is healthy"

    # No usable public listener exists (typical first install, or a previous
    # interrupted update).  Publish the HTTP front immediately.  The normal
    # reconciliation run that follows will add any ready TLS server blocks.
    blocks = nginx_http_front_blocks(
        access, control_port, control_backend_port, public_backend_port, auth_cache_seconds
    )
    ok, detail = install_nginx_config(blocks)
    if ok:
        # The managed config may have replaced a still-draining distro/default
        # worker.  Wait briefly for the actual authenticated proxy route, not
        # merely for a socket listener.
        ready = False
        for _attempt in range(20):
            if http_front_health_ready(control_port, node_token):
                ready = True
                break
            time.sleep(0.25)
        if not ready:
            return False, f"{detail}; authenticated StreamForge health route did not open on HTTP port {control_port}"
    return ok, detail


def write_status(payload: dict[str, Any]) -> None:
    TLS_ROOT.mkdir(parents=True, exist_ok=True)
    temp = STATUS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(temp, 0o644)
    temp.replace(STATUS_FILE)


def read_reconcile_request() -> dict[str, Any]:
    # STREAMFORGE_NODE_TLS_FORCE_RETRY_V1028: request files written by older
    # agents contained only an epoch; treat those as ordinary non-forcing runs.
    try:
        raw = REQUEST_FILE.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return {}
    if not raw.startswith("{"):
        return {}
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    if os.geteuid() != 0:
        print("apply_node_tls.py must run as root", file=sys.stderr)
        return 1
    reconcile_request = read_reconcile_request()
    requested_host = safe_hostname(str(reconcile_request.get("host") or ""))
    force_retry_requested = bool(reconcile_request.get("force_retry"))
    env = read_env()
    external_proxy = str(env.get("STREAMFORGE_NODE_EXTERNAL_PROXY", "0")).lower() in {"1", "true", "yes", "on"}
    try:
        control_port = max(1, min(65535, int(env.get("STREAMFORGE_NODE_PORT", "80") or 80)))
    except ValueError:
        control_port = 80
    try:
        control_backend_port = max(1024, min(65535, int(env.get("STREAMFORGE_NODE_CONTROL_BACKEND_PORT", "8810") or 8810)))
        public_backend_port = max(1024, min(65535, int(env.get("STREAMFORGE_NODE_PUBLIC_BACKEND_PORT", "8821") or 8821)))
        if public_backend_port == control_port:
            public_backend_port = 8822 if control_port != 8822 else 8823
        auth_cache_seconds = max(5, min(300, int(env.get("STREAMFORGE_NODE_MEDIA_AUTH_CACHE_SECONDS", "60") or 60)))
    except ValueError:
        control_backend_port = 8810
        public_backend_port = 8821
        auth_cache_seconds = 60
    if control_backend_port == control_port:
        control_backend_port = 8810 if control_port != 8810 else 8811
    if public_backend_port in {control_port, control_backend_port}:
        public_backend_port = 8821 if 8821 not in {control_port, control_backend_port} else 8822
    if external_proxy:
        write_status({"ok": True, "mode": "external-proxy", "message": "Co-located/Main proxy owns TLS", "updated_at": int(time.time())})
        REQUEST_FILE.unlink(missing_ok=True)
        return 0
    access = load_access()
    # STREAMFORGE_NODE_HTTP_FRONT_BOOTSTRAP_CLI_V1032: installer-only fast path.
    # It intentionally does not consume the TLS request file or rewrite TLS
    # status; the full oneshot reconciliation runs immediately afterwards.
    if "--bootstrap-http" in sys.argv[1:]:
        node_token = str(env.get("STREAMFORGE_NODE_TOKEN", "") or "").strip()
        ok, detail = bootstrap_http_front(access, control_port, control_backend_port, public_backend_port, auth_cache_seconds, node_token)
        log(f"HTTP frontend bootstrap: {detail}")
        return 0 if ok else 1
    desired = desired_hosts(access)
    TLS_ROOT.mkdir(parents=True, exist_ok=True)
    errors: dict[str, str] = {}
    ready_hosts: dict[str, list[int]] = {}
    delegations: dict[str, dict[str, Any]] = {}
    blocks: list[str] = nginx_http_front_blocks(
        access, control_port, control_backend_port, public_backend_port, auth_cache_seconds
    )
    ready_tls_ports: set[int] = set()
    for host, ports in sorted(desired.items()):
        delegation: dict[str, Any] = {"hostname": host}
        delegations[host] = delegation
        force_this_host = bool(force_retry_requested and (not requested_host or requested_host == host))
        ok, detail = issue_certificate(host, control_port=control_port, delegation=delegation, force_retry=force_this_host)
        error_file = TLS_ROOT / f"{host}.last-error"
        if not ok:
            # Waiting for the one-time CNAME is a setup state, not a failed CA
            # authorization. Keep it visible without treating the Node as broken.
            if delegation.get("state") not in {"waiting_cname", "retry_deferred"}:
                errors[host] = detail[-4000:]
            error_file.write_text(detail[-4000:] + "\n", encoding="utf-8")
            log(f"{host}: certificate pending: {detail.splitlines()[-1] if detail else 'unknown state'}")
            continue
        error_file.unlink(missing_ok=True)
        ready_hosts[host] = sorted(ports)
        ready_tls_ports.update(int(port) for port in ports)
        blocks.append(nginx_server(
            host, ports, control_backend_port, public_backend_port, auth_cache_seconds,
            public_root=host_has_root_stream_alias(access, host),
        ))
        log(f"{host}: TLS ready on {','.join(str(p) for p in sorted(ports))}")
    strict_sni_reject = nginx_supports_ssl_reject_handshake()
    if ready_tls_ports and strict_sni_reject:
        blocks = [nginx_reject_server(port) for port in sorted(ready_tls_ports)] + blocks
    nginx_ok, nginx_detail = install_nginx_config(blocks)
    if nginx_ok and ready_tls_ports and not strict_sni_reject:
        nginx_detail += "; compatibility mode: ssl_reject_handshake unavailable on installed Nginx"
    if not nginx_ok:
        errors["nginx"] = nginx_detail
        log(f"Nginx TLS apply failed: {nginx_detail}")
    payload = {
        "ok": bool(nginx_ok and not errors),
        "mode": "delegated-dns01",
        "provider": "acme-dns",
        "api_url": acme_dns_api_url(),
        "control_port": control_port,
        "control_backend_port": control_backend_port,
        "desired_hosts": {key: sorted(value) for key, value in desired.items()},
        "delegations": delegations,
        "ready_hosts": ready_hosts if nginx_ok else {},
        "tls_ready": bool(desired) and bool(nginx_ok) and len(ready_hosts) == len(desired),
        "pending_hosts": sorted(host for host in desired if host not in ready_hosts),
        "errors": errors,
        "nginx": nginx_detail,
        "updated_at": int(time.time()),
    }
    write_status(payload)
    REQUEST_FILE.unlink(missing_ok=True)
    # TLS provisioning is intentionally best-effort.  HTTP control must remain
    # online even when DNS/ACME is not ready yet; PathChanged/manual retry can
    # reconcile later without making Node Agent installation fail.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
