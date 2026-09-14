#!/usr/bin/env python3
"""Generate and atomically apply Main/Local Nginx listeners as root.

The StreamForge web process is intentionally confined by systemd with
``NoNewPrivileges=true``.  It writes an unprivileged request file; a root-owned
systemd path unit starts this helper.  The helper accepts no URL or shell
arguments, reads the validated state from SQLite, applies Nginx atomically, and
writes a request-correlated result for the web process.
"""
from __future__ import annotations

import grp
import hashlib
import hmac
import ipaddress
import stat
import json
import os
import pwd
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import shutil
from pathlib import Path
from typing import Any, NoReturn

# STREAMFORGE_AUTO_LETSENCRYPT_V37: compatibility marker; v4.1 keeps automatic certificate management through DNS-01.
# STREAMFORGE_DELEGATED_DNS01_V41: certificate issuance no longer depends on
# the managed host being reachable from the public Internet.  A one-time
# _acme-challenge CNAME delegates validation to the restricted acme-dns API.
for _candidate in (Path("/usr/local/libexec/streamforge"), Path(__file__).resolve().parent, Path("/opt/streamforge/scripts")):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
from acme_dns_client import account_for, api_url as acme_dns_api_url, cname_matches, cname_name, ensure_account
from urllib.parse import urlsplit


class ApplyError(RuntimeError):
    pass


def env_file_value(key: str) -> str:
    env_file = Path("/etc/streamforge.env")
    if not env_file.exists():
        return ""
    prefix = f"{key}="
    for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        if raw.startswith(prefix):
            return raw.split("=", 1)[1].strip()
    return ""


def configured_database_path() -> Path:
    explicit = os.environ.get("STREAMFORGE_DATABASE_PATH", "").strip()
    if explicit:
        return Path(explicit)
    value = os.environ.get("STREAMFORGE_DATABASE_URL", "").strip() or env_file_value(
        "STREAMFORGE_DATABASE_URL"
    )
    if value.startswith("sqlite:///"):
        path = value[len("sqlite:///"):]
        return Path(path if path.startswith("/") else f"/opt/streamforge/{path}")
    return Path("/var/lib/streamforge/streamforge.db")


def file_env_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    prefix = f"{key}="
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if raw.startswith(prefix):
            return raw.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def configured_runtime_dir() -> Path:
    value = os.environ.get("STREAMFORGE_MAIN_ACCESS_RUNTIME_DIR", "").strip()
    value = value or env_file_value("STREAMFORGE_MAIN_ACCESS_RUNTIME_DIR")
    return Path(value or "/var/lib/streamforge/main-access-runtime")


DB_PATH = configured_database_path()
RUNTIME_DIR = configured_runtime_dir()
REQUEST_FILE = RUNTIME_DIR / "request.json"
RESULT_FILE = RUNTIME_DIR / "result.json"
NGINX_SITE = Path("/etc/nginx/sites-available/streamforge")
NGINX_LINK = Path("/etc/nginx/sites-enabled/streamforge")
NODE_ENV_FILE = Path("/etc/streamforge-node.env")
NODE_APP_FILE = Path("/opt/streamforge-node/app.py")
ACME_WEBROOT = Path("/var/lib/streamforge/acme-webroot")
TLS_STATE_DIR = Path("/var/lib/streamforge/tls")
ACME_DNS_ACCOUNT_FILE = Path(os.getenv("STREAMFORGE_ACME_DNS_ACCOUNT_FILE", "/var/lib/streamforge/acme-dns/accounts.json"))
TLS_STATUS_FILE = TLS_STATE_DIR / "status.json"
CUSTOM_TLS_ROOT = Path("/etc/streamforge/tls")
LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")
STREAMFORGE_CERTBOT_BIN = Path("/opt/streamforge-certbot/bin/certbot")
HLS_ROOT = Path(os.environ.get("STREAMFORGE_HLS_ROOT", "").strip() or env_file_value("STREAMFORGE_HLS_ROOT") or "/var/lib/streamforge/hls")
RELAY_STATIC_ROOT = HLS_ROOT.parent / "relay"


def fail(message: str) -> NoReturn:
    raise ApplyError(message)


def url_rows(value: object) -> list[str]:
    return [line.strip().rstrip("/") for line in str(value or "").replace("\r", "").split("\n") if line.strip()]


def safe_server_name(value: object) -> str:
    """Return one Nginx-safe exact hostname/IP from a configured URL."""
    raw = str(value or "").strip().lower().rstrip(".")
    if not raw:
        fail("empty Main access hostname")
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        pass
    try:
        ascii_host = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        fail(f"invalid Main access hostname {raw!r}: {exc}")
    if len(ascii_host) > 253:
        fail(f"Main access hostname is too long: {raw!r}")
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        fail(f"invalid Main access hostname {raw!r}")
    return ascii_host


def parse_access_policy(rows: list[str], strict_requested: bool) -> tuple[list[int], dict[int, list[str]], bool]:
    """Return managed plain-HTTP listeners, exact hosts and strict mode.

    Every configured hostname is admitted on port 80 as a wrong-protocol/ACME
    ingress. Canonical HTTPS is terminated separately by managed TLS listeners;
    canonical HTTP custom ports remain available on their configured port.
    """
    ports: set[int] = {80}
    hosts_by_port: dict[int, set[str]] = {80: set()}
    for raw in rows:
        try:
            parsed = urlsplit(raw)
            explicit_port = parsed.port
        except ValueError as exc:
            fail(f"invalid Main access URL {raw!r}: {exc}")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            fail(f"invalid Main access URL {raw!r}")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            fail(f"credentials, query and fragments are not allowed in {raw!r}")
        slug = parsed.path.strip("/")
        if "/" in slug or (slug and not re.fullmatch(r"[a-zA-Z0-9_-]+", slug)):
            fail(f"only one simple access-path segment is allowed in {raw!r}")
        host = safe_server_name(parsed.hostname)
        # Port 80 always exists for ACME validation and HTTP->HTTPS canonical redirect.
        hosts_by_port.setdefault(80, set()).add(host)
        if parsed.scheme == "http":
            listener_port = int(explicit_port or 80)
            if not 1 <= listener_port <= 65535:
                fail(f"invalid Main access port in {raw!r}")
            ports.add(listener_port)
            hosts_by_port.setdefault(listener_port, set()).add(host)

    normalized_hosts = {port: sorted(values) for port, values in hosts_by_port.items()}
    strict = bool(strict_requested and any(normalized_hosts.values()))
    return sorted(ports), normalized_hosts, strict


# STREAMFORGE_HTTP_CANONICAL_REDIRECT_CERT_V42: HTTP aliases intentionally
# receive a DNS-01 certificate on 443 so https:// mistakes can complete TLS
# before the application sends the canonical redirect back to http://.
def parse_tls_policy(rows: list[str]) -> dict[int, list[str]]:
    """Return TLS ingress ports/hosts needed for both canonical directions.

    HTTPS aliases use their configured HTTPS port (443 by default). HTTP aliases
    get a 443 ingress so an https:// request can complete TLS and be redirected
    by the application back to the canonical HTTP URL.
    """
    hosts_by_port: dict[int, set[str]] = {}
    for raw in rows:
        try:
            parsed = urlsplit(raw)
            explicit_port = parsed.port
        except ValueError as exc:
            fail(f"invalid Main access URL {raw!r}: {exc}")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        host = safe_server_name(parsed.hostname)
        tls_port = int(explicit_port or 443) if parsed.scheme == "https" else 443
        if not 1 <= tls_port <= 65535:
            fail(f"invalid TLS access port in {raw!r}")
        hosts_by_port.setdefault(tls_port, set()).add(host)
    return {port: sorted(values) for port, values in hosts_by_port.items()}


def load_policy() -> tuple[list[int], dict[int, list[str]], bool]:
    if not DB_PATH.exists():
        fail(f"database not found: {DB_PATH}")
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    try:
        row = connection.execute(
            "SELECT api_urls, api_url, playlist_urls, playlist_url, dns_only "
            "FROM nodes WHERE node_type='local' ORDER BY id LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return [80], {}, False
    rows = url_rows(row[0] or row[1]) + url_rows(row[2] or row[3])
    return parse_access_policy(rows, bool(row[4]))


def load_tls_policy() -> dict[int, list[str]]:
    if not DB_PATH.exists():
        return {}
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    try:
        row = connection.execute(
            "SELECT api_urls, api_url, playlist_urls, playlist_url "
            "FROM nodes WHERE node_type='local' ORDER BY id LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return {}
    rows = url_rows(row[0] or row[1]) + url_rows(row[2] or row[3])
    return parse_tls_policy(rows)


def load_colocated_node_routes() -> tuple[list[tuple[int, str, str]], list[tuple[int, str, str]], int]:
    """Return plain/TLS public Node aliases proxied to the co-located listener."""
    if not NODE_APP_FILE.exists() or not NODE_ENV_FILE.exists():
        return [], [], 0
    if file_env_value(NODE_ENV_FILE, "STREAMFORGE_NODE_EXTERNAL_PROXY").lower() not in {"1", "true", "yes", "on"}:
        return [], [], 0
    token = file_env_value(NODE_ENV_FILE, "STREAMFORGE_NODE_TOKEN")
    try:
        backend_port = int(file_env_value(NODE_ENV_FILE, "STREAMFORGE_NODE_PORT") or "8810")
    except ValueError:
        backend_port = 8810
    if not token or not 1 <= backend_port <= 65535 or not DB_PATH.exists():
        return [], [], 0
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    try:
        row = connection.execute(
            "SELECT api_urls, api_url, playlist_urls, playlist_url "
            "FROM nodes WHERE node_type='remote' AND enabled=1 AND api_token=? ORDER BY id LIMIT 1",
            (token,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return [], [], backend_port
    plain_routes: list[tuple[int, str, str]] = []
    tls_routes: list[tuple[int, str, str]] = []
    for raw in url_rows(row[0] or row[1]) + url_rows(row[2] or row[3]):
        try:
            parsed = urlsplit(raw)
            explicit_port = parsed.port
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        host = safe_server_name(parsed.hostname)
        slug = parsed.path.strip("/")
        prefix = f"/{slug}" if slug else "/"
        # Port 80 always accepts ACME/wrong-protocol requests.
        for item in {(80, host, prefix)}:
            if item not in plain_routes:
                plain_routes.append(item)
        if parsed.scheme == "http":
            canonical_plain = (int(explicit_port or 80), host, prefix)
            if canonical_plain not in plain_routes:
                plain_routes.append(canonical_plain)
            tls_item = (443, host, prefix)
        else:
            tls_item = (int(explicit_port or 443), host, prefix)
        if tls_item not in tls_routes:
            tls_routes.append(tls_item)
    return plain_routes, tls_routes, backend_port


# STREAMFORGE_NGINX_RELAY_FASTPATH_V60: Remote Nodes that use Local-Node relay
# must not pull 1-second HLS playlists/segments through the single Main ASGI
# worker. A keyed symlink namespace lets Nginx validate the existing relay key
# by filesystem presence and serve media directly with sendfile.
def _relay_key(channel_id: int, slug: str, secret: str) -> str:
    message = f"streamforge-channel:{int(channel_id)}:{slug}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()[:32]


def _add_other_access(path: Path, bits: int) -> None:
    try:
        current = stat.S_IMODE(path.stat().st_mode)
        os.chmod(path, current | bits)
    except OSError:
        pass


def sync_local_relay_links() -> int:
    """Seed the signed Nginx relay namespace from current enabled HLS channels."""
    if not DB_PATH.exists():
        return 0
    secret = os.environ.get("STREAMFORGE_SECRET_KEY", "").strip() or file_env_value(Path("/etc/streamforge.env"), "STREAMFORGE_SECRET_KEY")
    if not secret:
        fail("STREAMFORGE_SECRET_KEY is unavailable for Nginx relay fast path")
    if not HLS_ROOT.is_absolute() or not RELAY_STATIC_ROOT.is_absolute():
        fail("STREAMFORGE_HLS_ROOT must be an absolute path")
    try:
        HLS_ROOT.mkdir(parents=True, exist_ok=True)
        RELAY_STATIC_ROOT.mkdir(parents=True, exist_ok=True)
        _add_other_access(HLS_ROOT.parent, 0o001)
        _add_other_access(HLS_ROOT, 0o005)
        _add_other_access(RELAY_STATIC_ROOT, 0o005)
    except OSError as exc:
        fail(f"cannot prepare Nginx relay fast path: {exc}")

    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    try:
        rows = connection.execute(
            "SELECT id, slug FROM channels WHERE enabled=1 AND output_type='hls' ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    desired: set[tuple[str, str]] = set()
    created = 0
    for channel_id, raw_slug in rows:
        slug = str(raw_slug or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
            continue
        key = _relay_key(int(channel_id), slug, secret)
        desired.add((key, slug))
        key_dir = RELAY_STATIC_ROOT / key
        key_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        _add_other_access(key_dir, 0o005)
        target = HLS_ROOT / slug
        if target.exists():
            _add_other_access(target, 0o005)
        link = key_dir / slug
        try:
            if link.is_symlink():
                try:
                    if link.resolve(strict=False) == target.resolve(strict=False):
                        continue
                except OSError:
                    pass
                link.unlink()
            elif link.exists():
                continue
            link.symlink_to(target, target_is_directory=True)
            created += 1
        except OSError as exc:
            fail(f"cannot prepare relay link for {slug}: {exc}")

    # Remove only stale symlinks created inside the signed relay namespace.
    try:
        for key_dir in RELAY_STATIC_ROOT.iterdir():
            if not key_dir.is_dir() or not re.fullmatch(r"[0-9a-f]{32}", key_dir.name):
                continue
            for link in key_dir.iterdir():
                if link.is_symlink() and (key_dir.name, link.name) not in desired:
                    link.unlink(missing_ok=True)
            try:
                key_dir.rmdir()
            except OSError:
                pass
    except OSError:
        pass
    return created


def main_hls_internal_location() -> str:
    # STREAMFORGE_MAIN_HLS_XACCEL_LOCATION_V63: internal-only target for
    # X-Accel-Redirect responses emitted after Public-plane authorization.
    root = str(HLS_ROOT).rstrip("/")
    return f"""    location ^~ /_streamforge_hls/ {{
        internal;
        alias {root}/;
        sendfile on;
        tcp_nopush on;
        types {{
            video/mp2t ts;
            video/iso.segment m4s;
            audio/aac aac;
            audio/mpeg mp3;
            application/octet-stream key;
        }}
        default_type application/octet-stream;
        add_header Cache-Control \"no-store\" always;
        add_header Access-Control-Allow-Origin \"*\" always;
        add_header Accept-Ranges \"bytes\" always;
        add_header X-StreamForge-Media-Path \"nginx-x-accel\" always;
    }}
"""


def relay_static_location() -> str:
    data_root = str(HLS_ROOT.parent).replace('"', r'\"')
    return f"""    # STREAMFORGE_NGINX_RELAY_FASTPATH_V60: signed Local-Node relay bypasses Gunicorn.
    location ~ "^/relay/[0-9a-f]{{32}}/[A-Za-z0-9_-]+/(?:index\\.m3u8|[A-Za-z0-9_.-]+\\.(?:ts|m4s|aac|mp3|key))$" {{
        root \"{data_root}\";
        sendfile on;
        tcp_nopush on;
        # STREAMFORGE_RELAY_MISS_PUBLIC_FALLBACK_V111: direct relay hits stay
        # in Nginx; a missing symlink/file falls back to the validated FastAPI
        # relay endpoint instead of exposing a transient 404 to Node FFmpeg.
        try_files $uri @streamforge_relay_fallback;
        access_log /var/log/nginx/access.log combined if=$streamforge_relay_access_loggable;
        types {{
            application/vnd.apple.mpegurl m3u8;
            video/mp2t ts;
            video/iso.segment m4s;
            audio/aac aac;
            audio/mpeg mp3;
            application/octet-stream key;
        }}
        default_type application/octet-stream;
        add_header Cache-Control \"no-store\" always;
        add_header Access-Control-Allow-Origin \"*\" always;
        add_header X-StreamForge-Relay \"nginx-direct\" always;
    }}

    # STREAMFORGE_RELAY_MISS_PUBLIC_FALLBACK_V111: named miss handler keeps the
    # original /relay URI and deliberately does not intercept FastAPI 503/403.
    location @streamforge_relay_fallback {{
        proxy_pass http://streamforge_public_backend;
{proxy_header_lines()}
        proxy_set_header X-StreamForge-Internal-Relay-Fallback 1;
    }}
"""


# STREAMFORGE_MAIN_NGINX_PUBLIC_STATIC_CACHE_V122:
def main_static_location() -> str:
    """Serve Main UI assets from an Nginx-readable published cache.

    /opt/streamforge is intentionally private to the StreamForge service user on
    many installations, so Nginx must not alias directly into that tree.  The
    installer/updater mirrors app/static into /var/cache/streamforge/main-static
    with 0755 directories and 0644 files before this listener is generated.
    """
    static_root = "/var/cache/streamforge/main-static"
    return f"""    location ^~ /static/ {{
        alias {static_root}/;
        access_log off;
        log_not_found off;
        etag on;
        expires 5m;
        add_header Cache-Control "public, max-age=300" always;
        add_header X-StreamForge-Static-Path "nginx-direct-cache" always;
    }}
"""


def proxy_header_lines(indent: str = "        ", *, response_buffering: bool = False) -> str:
    # STREAMFORGE_MAIN_PANEL_RESPONSE_BUFFERING_V1115:
    # Main Panel HTML/JSON is finite control-plane traffic. Let Nginx drain the
    # single Gunicorn worker quickly and serve slow WAN browsers from its own
    # buffers. Public HLS/player/relay callers keep streaming buffering off.
    buffering_lines = [f"{indent}proxy_buffering {'on' if response_buffering else 'off'};"]
    if response_buffering:
        buffering_lines.extend([
            f"{indent}proxy_buffer_size 16k;",
            f"{indent}proxy_buffers 16 32k;",
            f"{indent}proxy_busy_buffers_size 64k;",
            f"{indent}proxy_max_temp_file_size 0;",
        ])
    return "\n".join([
        f"{indent}proxy_http_version 1.1;",
        *buffering_lines,
        f"{indent}proxy_request_buffering off;",
        f"{indent}proxy_set_header Host $http_host;",
        # STREAMFORGE_CHAINED_PROXY_PROTOCOL_V35 / STREAMFORGE_NATIVE_TLS_HEADERS_V37:
        # Preserve upstream proxy authority when present, otherwise describe
        # the protocol/host/port actually accepted by this managed Nginx.
        f"{indent}proxy_set_header X-Forwarded-Host $streamforge_forwarded_host;",
        f"{indent}proxy_set_header X-Forwarded-Port $streamforge_forwarded_port;",
        f"{indent}proxy_set_header X-Real-IP $remote_addr;",
        f"{indent}proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        f"{indent}proxy_set_header X-Forwarded-Proto $streamforge_forwarded_proto;",
        f'{indent}proxy_set_header Connection "";',
    ])


def proxy_location(prefix: str, backend_port: int) -> str:
    # STREAMFORGE_MAIN_UNKNOWN_PATH_SILENT_DROP_V3014:
    # Intercept both an ordinary upstream 404 and the application's internal
    # 418 drop trigger. Nginx maps either result to its named 444 handler, so
    # invalid public links never expose FastAPI's JSON Not Found response.
    cleaned = prefix if prefix.startswith("/") else f"/{prefix}"
    backend = "http://streamforge_main_backend" if int(backend_port) == 8800 else f"http://127.0.0.1:{int(backend_port)}"
    if cleaned == "/":
        return f"""    location / {{
        proxy_pass {backend};
        proxy_intercept_errors on;
        error_page 404 418 = @streamforge_silent_drop;
{proxy_header_lines(response_buffering=(int(backend_port) == 8800))}
    }}
"""
    return f"""    location = {cleaned} {{
        proxy_pass {backend};
        proxy_intercept_errors on;
        error_page 404 418 = @streamforge_silent_drop;
{proxy_header_lines(response_buffering=(int(backend_port) == 8800))}
    }}
    location ^~ {cleaned}/ {{
        proxy_pass {backend};
        proxy_intercept_errors on;
        error_page 404 418 = @streamforge_silent_drop;
{proxy_header_lines(response_buffering=(int(backend_port) == 8800))}
    }}
"""


def public_proxy_location() -> str:
    """Route high-volume Main public/player endpoints to the isolated Public plane."""
    return f"""    # STREAMFORGE_PUBLIC_PLANE_NGINX_SPLIT_V62
    # STREAMFORGE_MAIN_SHARED_ASSETS_PUBLIC_PLANE_V1055
    # Access aliases are one path segment, so the optional leading segment keeps
    # /get.php and /stream/get.php on the same Public worker pool. Exact alias
    # roots remain on Main for compatibility; all high-volume child requests are
    # isolated from Admin/Logs/Node-control work.
    location ~ "^/(?:[A-Za-z0-9_-]+/)?(?:web-player(?:/|$)|player(?:/|$)|watch(?:/|$)|playlist(?:/|$)|play(?:/|$)|live(?:/|$)|ek(?:/|$)|relay(?:/|$)|channel-logos(?:/|$)|node-logos(?:/|$)|branding-assets(?:/|$)|get\\.php$|player_api\\.php$|update\\.json$|[^/]+\\.apk$)" {{
        # STREAMFORGE_MAIN_ALIAS_LOGO_CANONICAL_UPSTREAM_V1057:
        # /123/channel-logos/file.png is a public alias URL, but the mounted
        # StaticFiles route is canonical /channel-logos/file.png. Normalize only
        # this shared-asset child path before proxying; query strings are kept.
        rewrite ^/[A-Za-z0-9_-]+(/channel-logos/[A-Za-z0-9._-]+)$ $1 break;
        proxy_pass http://streamforge_public_backend;
        proxy_intercept_errors on;
        error_page 404 418 = @streamforge_silent_drop;
{proxy_header_lines()}
        proxy_set_header X-StreamForge-Internal-Relay-Fallback "";
    }}
"""


def acme_location() -> str:
    return """    # STREAMFORGE_MANAGED_ACME_WEBROOT_V37: Lets Encrypt HTTP-01 bypasses app redirects.
    location ^~ /.well-known/acme-challenge/ {
        root /var/lib/streamforge/acme-webroot;
        default_type text/plain;
        try_files $uri =404;
    }
"""


def certificate_paths(host: str) -> tuple[Path, Path] | None:
    for root in (CUSTOM_TLS_ROOT / host, LETSENCRYPT_LIVE / host):
        cert = root / "fullchain.pem"
        key = root / "privkey.pem"
        if cert.is_file() and key.is_file():
            return cert, key
    return None


def _custom_certificate_paths(host: str) -> tuple[Path, Path] | None:
    root = CUSTOM_TLS_ROOT / host
    cert = root / "fullchain.pem"
    key = root / "privkey.pem"
    return (cert, key) if cert.is_file() and key.is_file() else None


def _renewal_uses_delegated_dns01(host: str) -> bool:
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


# STREAMFORGE_MAIN_ISOLATED_CERTBOT_RENEWAL_V1030: renew through the same
# dedicated Certbot venv used for issuance instead of depending on the distro
# certbot.timer Python environment.
def _certificate_needs_renewal(pair: tuple[Path, Path], *, within_seconds: int = 30 * 24 * 60 * 60) -> bool:
    cert, _ = pair
    try:
        checked = subprocess.run(
            ["openssl", "x509", "-checkend", str(max(0, int(within_seconds))), "-noout", "-in", str(cert)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return checked.returncode != 0


def certifiable_hostname(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return "." in host and host != "localhost"


def _write_tls_status(payload: dict[str, Any]) -> None:
    TLS_STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp = TLS_STATUS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(temp, 0o644)
    temp.replace(TLS_STATUS_FILE)


# STREAMFORGE_MAIN_ISOLATED_CERTBOT_V1030: use the dedicated Certbot venv so
# application-level Python upgrades cannot break the ACME client imported by
# the operating-system certbot executable.
def _certbot_binary() -> str | None:
    if STREAMFORGE_CERTBOT_BIN.is_file() and os.access(STREAMFORGE_CERTBOT_BIN, os.X_OK):
        return str(STREAMFORGE_CERTBOT_BIN)
    return shutil.which("certbot")


def _certbot_dns01(host: str, *, force_renewal: bool = False) -> tuple[Path, Path] | None:
    hook = Path("/usr/local/libexec/streamforge/acme_dns_hook.py")
    certbot = _certbot_binary()
    if not hook.is_file() or certbot is None:
        return None
    try:
        args = [
            certbot, "certonly", "--manual", "--preferred-challenges", "dns",
            "--manual-auth-hook", f"/usr/bin/python3 {hook} auth",
            "--manual-cleanup-hook", f"/usr/bin/python3 {hook} cleanup",
            "--non-interactive", "--agree-tos", "--register-unsafely-without-email",
            "--cert-name", host, "-d", host,
        ]
        args.append("--force-renewal" if force_renewal else "--keep-until-expiring")
        issued = run(*args, timeout=240)
    except (OSError, subprocess.SubprocessError) as exc:
        (TLS_STATE_DIR / f"{host}.last-error").write_text(str(exc)[-3000:], encoding="utf-8")
        return None
    if issued.returncode != 0:
        detail = (issued.stderr or issued.stdout or "certbot failed").strip()[-3000:]
        (TLS_STATE_DIR / f"{host}.last-error").write_text(detail, encoding="utf-8")
        return None
    (TLS_STATE_DIR / f"{host}.last-error").unlink(missing_ok=True)
    return certificate_paths(host)


def ensure_certificate(host: str, delegation: dict[str, Any]) -> tuple[Path, Path] | None:
    """Provision/convert certificates through delegated DNS-01 only.

    Existing custom certificates remain untouched. Existing Let's Encrypt
    certificates stay live while StreamForge waits for the one-time CNAME;
    once it is present, the renewal lineage is converted to the DNS hook.
    """
    custom = _custom_certificate_paths(host)
    if custom:
        delegation.update({
            "state": "custom_certificate", "certificate_ready": True,
            "cname_ready": False, "dns01_managed": False,
        })
        return custom
    existing = certificate_paths(host)
    if existing and _renewal_uses_delegated_dns01(host):
        # STREAMFORGE_DNS01_REGISTRATION_METADATA_PERSIST_V43:
        # A valid DNS-01 renewal must keep surfacing the original one-time
        # delegation. Never create a replacement registration just because the
        # certificate is already ready; read the persisted restricted account
        # and publish only its safe CNAME metadata.
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
            return existing
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
        if not _certificate_needs_renewal(existing):
            return existing
        if not cname_ok:
            return existing
    if not certifiable_hostname(host) or _certbot_binary() is None:
        delegation.update({"state": "unavailable", "certificate_ready": bool(existing), "dns01_managed": False})
        return existing
    TLS_STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        account = ensure_account(host, ACME_DNS_ACCOUNT_FILE)
    except Exception as exc:
        delegation.update({
            "state": "registration_failed", "certificate_ready": bool(existing),
            "dns01_managed": False, "error": str(exc),
        })
        return existing
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
        return existing
    delegation["state"] = "migrating_renewal" if existing else "issuing"
    pair = _certbot_dns01(host, force_renewal=bool(existing))
    if pair:
        delegation.update({
            "state": "certificate_ready", "certificate_ready": True,
            "dns01_managed": True,
        })
        return pair
    delegation["state"] = "migration_failed" if existing else "issue_failed"
    try:
        delegation["error"] = (TLS_STATE_DIR / f"{host}.last-error").read_text(encoding="utf-8", errors="replace")[-1200:]
    except OSError:
        delegation["error"] = "Certificate issuance failed"
    return existing

def panel_compression_lines(indent: str = "    ") -> str:
    # STREAMFORGE_MAIN_PANEL_GZIP_V1115: authenticated panel responses are
    # highly compressible (Dashboard was ~386 KB uncompressed in diagnostics).
    # HLS media types are deliberately absent from gzip_types.
    return "\n".join([
        f"{indent}gzip on;",
        f"{indent}gzip_vary on;",
        f"{indent}gzip_proxied any;",
        f"{indent}gzip_min_length 1024;",
        f"{indent}gzip_comp_level 5;",
        f"{indent}gzip_types text/plain text/css application/json application/javascript application/xml image/svg+xml;",
    ]) + "\n"


def tls_server_block(port: int, host: str, node_prefixes: list[str], node_backend: int,
                     fallback_main: bool, cert: Path, key: Path) -> str:
    locations: list[str] = []
    if fallback_main:
        locations.append(main_hls_internal_location())
        locations.append(relay_static_location())
        locations.append(main_static_location())
    unique_prefixes = sorted(set(node_prefixes), key=lambda item: (item == "/", -len(item), item))
    root_owned = "/" in unique_prefixes
    for prefix in unique_prefixes:
        locations.append(proxy_location(prefix, node_backend))
    if fallback_main:
        locations.append(public_proxy_location())
    if not root_owned:
        if fallback_main:
            locations.append(proxy_location("/", 8800))
        else:
            locations.append("    location / { return 444; }\n")
    compression = panel_compression_lines() if fallback_main else ""
    return f"""server {{
    listen {port} ssl;
    listen [::]:{port} ssl;
    server_name {host};
    ssl_certificate {cert};
    ssl_certificate_key {key};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:STREAMFORGE_SSL:10m;
    ssl_session_timeout 1d;
    client_max_body_size 1g;
    proxy_connect_timeout 15s;
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
    proxy_intercept_errors on;
    error_page 421 = @streamforge_silent_drop;

{compression}{''.join(locations)}    location @streamforge_silent_drop {{ return 444; }}
}}
"""


def reject_server_block(port: int) -> str:
    """Silently close unknown-host connections so browsers show a native error."""
    return f"""server {{
    listen {port} default_server;
    listen [::]:{port} default_server;
    server_name _;
    access_log off;
    log_not_found off;
    return 444;
}}
"""


# STREAMFORGE_MAIN_NGINX_SSL_REJECT_COMPAT_V1033: Nginx only gained
# ssl_reject_handshake in 1.19.4. Older distro Nginx builds must not receive
# that directive or all managed TLS listeners fail nginx -t.
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


# STREAMFORGE_UNKNOWN_TLS_SNI_REJECT_V44: reject unknown TLS SNI before
# Nginx can present the certificate of the first configured HTTPS vhost.
def tls_reject_server_block(port: int) -> str:
    return f"""server {{
    listen {port} ssl default_server;
    listen [::]:{port} ssl default_server;
    server_name _;
    ssl_reject_handshake on;
    access_log off;
    log_not_found off;
}}
"""


def routed_server_block(port: int, host: str, node_prefixes: list[str], node_backend: int, fallback_main: bool) -> str:
    locations: list[str] = []
    if fallback_main:
        locations.append(main_hls_internal_location())
        locations.append(relay_static_location())
        locations.append(main_static_location())
    unique_prefixes = sorted(set(node_prefixes), key=lambda item: (item == "/", -len(item), item))
    root_owned = "/" in unique_prefixes
    for prefix in unique_prefixes:
        locations.append(proxy_location(prefix, node_backend))
    if fallback_main:
        locations.append(public_proxy_location())
    if not root_owned:
        if fallback_main:
            locations.append(proxy_location("/", 8800))
        else:
            locations.append("    location / { return 444; }\n")
    acme = acme_location() if int(port) == 80 else ""
    compression = panel_compression_lines() if fallback_main else ""
    return f"""server {{
    listen {port};
    listen [::]:{port};
    server_name {host};
    client_max_body_size 1g;
    proxy_connect_timeout 15s;
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
    proxy_intercept_errors on;
    error_page 421 = @streamforge_silent_drop;

{compression}{acme}{''.join(locations)}    location @streamforge_silent_drop {{ return 444; }}
}}
"""


def open_main_server_block(port: int) -> str:
    acme = acme_location() if int(port) == 80 else ""
    return f"""server {{
    listen {port} default_server;
    listen [::]:{port} default_server;
    server_name _;
    client_max_body_size 1g;
    proxy_connect_timeout 15s;
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
    proxy_intercept_errors on;
    error_page 421 = @streamforge_silent_drop;

{panel_compression_lines()}{acme}{main_hls_internal_location()}{relay_static_location()}{main_static_location()}{public_proxy_location()}{proxy_location('/', 8800)}    location @streamforge_silent_drop {{ return 444; }}
}}
"""


def render_config(
    ports: list[int], hosts_by_port: dict[int, list[str]], strict: bool,
    node_routes: list[tuple[int, str, str]] | None = None, node_backend: int = 0,
    tls_hosts_by_port: dict[int, list[str]] | None = None,
    node_tls_routes: list[tuple[int, str, str]] | None = None,
    certificates: dict[str, tuple[Path, Path]] | None = None,
) -> str:
    header = (
        "# Managed by StreamForge. Changes are regenerated from Main/Local and co-located Node access URLs.\n"
        "# STREAMFORGE_NATIVE_TLS_TERMINATION_V37\n"
        "map $http_x_forwarded_proto $streamforge_forwarded_proto {\n"
        "    default $http_x_forwarded_proto;\n"
        "    \"\" $scheme;\n}\n"
        "map $http_x_forwarded_host $streamforge_forwarded_host {\n"
        "    default $http_x_forwarded_host;\n"
        "    \"\" $http_host;\n}\n"
        "map $http_x_forwarded_port $streamforge_forwarded_port {\n"
        "    default $http_x_forwarded_port;\n"
        "    \"\" $server_port;\n}\n\n"
        "# STREAMFORGE_RELAY_ERROR_ONLY_ACCESS_LOG_V1036: keep only 4xx/5xx relay requests in the normal access log.\n"
        "map $status $streamforge_relay_access_loggable {\n"
        "    ~^[45] 1;\n"
        "    default 0;\n}\n\n"
        "upstream streamforge_main_backend {\n    server 127.0.0.1:8800;\n    keepalive 512;\n}\n\n"
        "upstream streamforge_public_backend {\n    server 127.0.0.1:8811;\n    keepalive 1024;\n}\n\n"
    )
    route_map: dict[tuple[int, str], list[str]] = {}
    for port, host, prefix in node_routes or []:
        route_map.setdefault((port, host), []).append(prefix)
        if port not in ports:
            ports.append(port)
    ports = sorted(set(ports))
    blocks: list[str] = []
    for port in ports:
        main_hosts = set(hosts_by_port.get(port, []))
        node_hosts = {host for route_port, host in route_map if route_port == port}
        if strict:
            blocks.append(reject_server_block(port))
            for host in sorted(main_hosts | node_hosts):
                blocks.append(routed_server_block(
                    port, host, route_map.get((port, host), []), node_backend,
                    fallback_main=host in main_hosts,
                ))
        else:
            blocks.append(open_main_server_block(port))
            for host in sorted(node_hosts):
                blocks.append(routed_server_block(
                    port, host, route_map.get((port, host), []), node_backend,
                    fallback_main=True,
                ))

    tls_route_map: dict[tuple[int, str], list[str]] = {}
    for port, host, prefix in node_tls_routes or []:
        tls_route_map.setdefault((port, host), []).append(prefix)
    certs = certificates or {}
    for port in sorted(set((tls_hosts_by_port or {}).keys()) | {p for p, _h in tls_route_map}):
        main_hosts = set((tls_hosts_by_port or {}).get(port, []))
        node_hosts = {host for route_port, host in tls_route_map if route_port == port}
        # STREAMFORGE_UNKNOWN_TLS_SNI_REJECT_V44 / STREAMFORGE_MAIN_NGINX_SSL_REJECT_COMPAT_V1033
        if nginx_supports_ssl_reject_handshake():
            blocks.append(tls_reject_server_block(port))
        for host in sorted(main_hosts | node_hosts):
            pair = certs.get(host)
            if not pair:
                continue
            cert, key = pair
            blocks.append(tls_server_block(
                port, host, tls_route_map.get((port, host), []), node_backend,
                fallback_main=host in main_hosts, cert=cert, key=key,
            ))
    return header + "\n".join(blocks)


def run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)


def request_id() -> str:
    try:
        payload = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
        value = str(payload.get("request_id") or "").strip() if isinstance(payload, dict) else ""
        if value:
            return value[:160]
    except (OSError, ValueError, TypeError):
        pass
    return f"direct-{int(time.time() * 1000)}"


def ensure_runtime_dir() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(RUNTIME_DIR, 0o750)
    try:
        account = pwd.getpwnam("streamforge")
        group_id = grp.getgrnam("streamforge").gr_gid
        os.chown(RUNTIME_DIR, account.pw_uid, group_id)
    except (KeyError, PermissionError, OSError):
        pass


def write_result(current_request_id: str, ok: bool, message: str) -> None:
    ensure_runtime_dir()
    payload = {
        "request_id": current_request_id,
        "ok": bool(ok),
        "message": str(message or "")[-4000:],
        "finished_at": time.time(),
    }
    fd, temp_name = tempfile.mkstemp(prefix=".result-", dir=str(RUNTIME_DIR))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o640)
        try:
            os.chown(temp_path, 0, grp.getgrnam("streamforge").gr_gid)
        except (KeyError, PermissionError, OSError):
            pass
        os.replace(temp_path, RESULT_FILE)
    finally:
        temp_path.unlink(missing_ok=True)


# STREAMFORGE_MAIN_NGINX_NOOP_RELOAD_SKIP_V1146:
# The Main TLS timer runs frequently.  A listener apply must not reload Nginx
# when the generated configuration is byte-for-byte unchanged; needless reloads
# can interrupt long-lived Local Relay clients on some kernels/Nginx builds.
def _atomic_apply(config: str, *, force_reload: bool = False) -> bool:
    NGINX_SITE.parent.mkdir(parents=True, exist_ok=True)
    NGINX_LINK.parent.mkdir(parents=True, exist_ok=True)
    previous = NGINX_SITE.read_bytes() if NGINX_SITE.exists() else None
    rendered = config.encode("utf-8")
    config_changed = previous != rendered

    # Keep the managed symlink correct even on a no-op reconciliation.
    if NGINX_LINK.is_symlink() or NGINX_LINK.exists():
        if NGINX_LINK.resolve() != NGINX_SITE.resolve():
            NGINX_LINK.unlink()
    if not NGINX_LINK.exists():
        NGINX_LINK.symlink_to(NGINX_SITE)

    if not config_changed and not force_reload:
        return False

    fd, temp_name = tempfile.mkstemp(prefix="streamforge-nginx-", dir=str(NGINX_SITE.parent))
    temp_path = Path(temp_name)
    try:
        if config_changed:
            with os.fdopen(fd, "wb") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o644)
            os.replace(temp_path, NGINX_SITE)
        else:
            os.close(fd)
        tested = run("nginx", "-t")
        if tested.returncode != 0:
            raise ApplyError((tested.stderr or tested.stdout).strip() or "nginx -t failed")
        reloaded = run("systemctl", "reload", "nginx")
        if reloaded.returncode != 0:
            raise ApplyError((reloaded.stderr or reloaded.stdout).strip() or "nginx reload failed")
        active = run("systemctl", "is-active", "--quiet", "nginx")
        if active.returncode != 0:
            raise ApplyError("nginx is not active after reload")
        return True
    except Exception as exc:
        if config_changed:
            if previous is None:
                NGINX_SITE.unlink(missing_ok=True)
            else:
                NGINX_SITE.write_bytes(previous)
                os.chmod(NGINX_SITE, 0o644)
            run("nginx", "-t")
            run("systemctl", "reload", "nginx")
        if isinstance(exc, ApplyError):
            raise
        raise ApplyError(str(exc)) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _certificate_material_signature(pair: tuple[Path, Path] | None) -> tuple[tuple[int, int, int], ...] | None:
    if not pair:
        return None
    result: list[tuple[int, int, int]] = []
    try:
        for item in pair:
            stat = item.resolve(strict=True).stat()
            result.append((int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns)))
    except OSError:
        return None
    return tuple(result)


def apply_nginx() -> str:
    if os.geteuid() != 0:
        fail("must run as root through streamforge-main-access.service")
    ACME_WEBROOT.mkdir(parents=True, exist_ok=True)
    relay_links_created = sync_local_relay_links()
    ports, hosts_by_port, strict = load_policy()
    tls_hosts_by_port = load_tls_policy()
    node_routes, node_tls_routes, node_backend = load_colocated_node_routes()

    wanted_tls_hosts = sorted(
        {host for hosts in tls_hosts_by_port.values() for host in hosts}
        | {host for _port, host, _prefix in node_tls_routes}
    )

    # STREAMFORGE_MAIN_TLS_RECONCILE_NO_LISTENER_FLAP_V1146:
    # Never render the preliminary DNS-01 configuration with TLS removed.  The
    # old two-phase flow briefly removed every 443 listener on each periodic
    # TLS reconciliation, producing TCP ECONNREFUSED on Local Relay Nodes.
    # Keep every already-usable certificate in the preliminary config; hosts
    # still waiting for a certificate retain the TLS reject listener instead of
    # closing the port. DNS-01 issuance itself does not require HTTP-01.
    existing_certificates: dict[str, tuple[Path, Path]] = {}
    certificate_signatures_before: dict[str, tuple[tuple[int, int, int], ...] | None] = {}
    for host in wanted_tls_hosts:
        pair = certificate_paths(host)
        if pair:
            existing_certificates[host] = pair
        certificate_signatures_before[host] = _certificate_material_signature(pair)

    preliminary = render_config(
        ports, hosts_by_port, strict, node_routes, node_backend,
        tls_hosts_by_port=tls_hosts_by_port, node_tls_routes=node_tls_routes,
        certificates=existing_certificates,
    )
    _atomic_apply(preliminary)

    certificates: dict[str, tuple[Path, Path]] = {}
    missing: list[str] = []
    delegations: dict[str, dict[str, Any]] = {}
    for host in wanted_tls_hosts:
        delegation: dict[str, Any] = {"hostname": host}
        delegations[host] = delegation
        pair = ensure_certificate(host, delegation)
        if pair:
            certificates[host] = pair
        else:
            missing.append(host)

    final_config = render_config(
        ports, hosts_by_port, strict, node_routes, node_backend,
        tls_hosts_by_port=tls_hosts_by_port,
        node_tls_routes=node_tls_routes,
        certificates=certificates,
    )
    certificate_material_changed = any(
        _certificate_material_signature(certificates.get(host)) != certificate_signatures_before.get(host)
        for host in wanted_tls_hosts
    )
    _atomic_apply(final_config, force_reload=certificate_material_changed)
    mode = "strict configured-host mode" if strict else "open-host mode"
    node_note = f"; co-located Node proxied to 127.0.0.1:{node_backend}" if node_routes else ""
    plain_ports = sorted(set(ports + [route[0] for route in node_routes]))
    tls_ports = sorted({port for port, hosts in tls_hosts_by_port.items() if any(h in certificates for h in hosts)} |
                       {port for port, host, _prefix in node_tls_routes if host in certificates})
    tls_note = f"; TLS port(s): {', '.join(str(p) for p in tls_ports)}" if tls_ports else "; TLS: no certificate ready"
    missing_note = f"; certificate pending: {', '.join(missing)}" if missing else ""
    _write_tls_status({
        "ok": not bool(missing),
        "mode": "delegated-dns01",
        "provider": "acme-dns",
        "api_url": acme_dns_api_url(),
        "delegations": delegations,
        "ready_hosts": {host: sorted([port for port, hosts in tls_hosts_by_port.items() if host in hosts]) for host in certificates},
        "updated_at": int(time.time()),
    })
    relay_note = f"; Nginx relay fast path ready ({relay_links_created} new link(s))"
    return "Main listeners applied on HTTP port(s): " + ", ".join(str(item) for item in plain_ports) + f" ({mode}{node_note}{tls_note}{missing_note}{relay_note})"


def main() -> int:
    current_request_id = request_id()
    try:
        message = apply_nginx()
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        write_result(current_request_id, False, message)
        print(f"streamforge-apply-main-access: {message}", file=sys.stderr)
        return 1
    write_result(current_request_id, True, message)
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
