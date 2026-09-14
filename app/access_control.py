from __future__ import annotations

import ipaddress
import re
import threading
import time
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request

from .config import settings
from .geoip_config import load_geoip_settings

try:  # Optional at runtime; policies fail closed when an ASN list is configured.
    import maxminddb  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    maxminddb = None

_ASN_RE = re.compile(r"^(?:AS)?(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    client_ip: str
    asn: int | None
    reason: str = ""


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    try:
        peer_address = ipaddress.ip_address(peer)
        trust_forwarded = peer_address.is_loopback
    except ValueError:
        trust_forwarded = False
    forwarded = request.headers.get("x-forwarded-for", "") if trust_forwarded else ""
    if forwarded:
        candidate = forwarded.split(",", 1)[0].strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return peer


def normalize_ip_rules(value: str | None) -> str:
    rules: list[str] = []
    for raw in re.split(r"[\s,]+", value or ""):
        item = raw.strip()
        if not item:
            continue
        try:
            if "/" in item:
                normalized = str(ipaddress.ip_network(item, strict=False))
            else:
                address = ipaddress.ip_address(item)
                normalized = f"{address}/{32 if address.version == 4 else 128}"
        except ValueError as exc:
            raise ValueError(f"Invalid IP/CIDR rule: {item}") from exc
        if normalized not in rules:
            rules.append(normalized)
    return "\n".join(rules)


def normalize_asn_rules(value: str | None) -> str:
    values: list[str] = []
    for raw in re.split(r"[\s,]+", value or ""):
        item = raw.strip()
        if not item:
            continue
        match = _ASN_RE.fullmatch(item)
        if not match:
            raise ValueError(f"Invalid ASN: {item}. Use AS13335 or 13335")
        normalized = str(int(match.group(1)))
        if normalized not in values:
            values.append(normalized)
    return "\n".join(values)


def ip_matches(value: str | None, ip_text: str) -> bool:
    try:
        address = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    for raw in (value or "").splitlines():
        item = raw.strip()
        if not item:
            continue
        try:
            if address in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


def asn_matches(value: str | None, asn: int | None) -> bool:
    if asn is None:
        return False
    return str(int(asn)) in {
        item.strip().upper().removeprefix("AS")
        for item in (value or "").splitlines()
        if item.strip()
    }


class GeoIPResolver:
    """Resolve ASN organisation and country from optional MaxMind databases."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._asn_reader: Any | None = None
        self._asn_path = ""
        self._country_reader: Any | None = None
        self._country_path = ""
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def _reader(self, path: Path, *, country: bool = False) -> Any | None:
        if maxminddb is None or not path.is_file():
            return None
        path_text = str(path)
        with self._lock:
            current_reader = self._country_reader if country else self._asn_reader
            current_path = self._country_path if country else self._asn_path
            if current_reader is not None and current_path == path_text:
                return current_reader
            if current_reader is not None:
                try:
                    current_reader.close()
                except Exception:
                    pass
            reader = maxminddb.open_database(path_text)
            if country:
                self._country_reader = reader
                self._country_path = path_text
            else:
                self._asn_reader = reader
                self._asn_path = path_text
            self._cache.clear()
            return reader

    @staticmethod
    def _ipinfo_details(ip_text: str, token: str) -> dict[str, Any]:
        if not token:
            return {}
        # STREAMFORGE_IPINFO_COMPATIBLE_LOOKUP_V3059:
        # Prefer IPinfo's current Lite endpoint, then try its legacy endpoint.
        # Some Node networks allow one hostname but block the other.  Normalize
        # Lite, Core/nested and legacy response shapes and retain a useful
        # token/network/rate-limit error instead of silently returning No match.
        quoted_ip = urllib.parse.quote(ip_text, safe="")
        quoted_token = urllib.parse.quote(token, safe="")
        urls = (
            # STREAMFORGE_MAIN_IPINFO_IPV4_TRANSPORT_FALLBACK_V3060: IPinfo officially provides an IPv4 transport hostname.
            # Try it before the legacy endpoint for hosts with broken outbound IPv6.
            f"https://api.ipinfo.io/lite/{quoted_ip}?token={quoted_token}",
            f"https://v4.api.ipinfo.io/lite/{quoted_ip}?token={quoted_token}",
            f"https://ipinfo.io/{quoted_ip}/json?token={quoted_token}",
        )
        errors: list[str] = []
        for url in urls:
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "StreamForge-GeoIP/3.2"},
            )
            try:
                with urllib.request.urlopen(request, timeout=6.0) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
                if not isinstance(payload, dict):
                    raise ValueError("IPinfo returned a non-object response")
                as_record = payload.get("as") if isinstance(payload.get("as"), dict) else {}
                geo_record = payload.get("geo") if isinstance(payload.get("geo"), dict) else {}
                org_text = str(payload.get("org") or "").strip()
                asn_value = payload.get("asn") or as_record.get("asn") or ""
                if not asn_value and org_text:
                    match = re.match(r"^AS(\d+)(?:\s+|$)", org_text, re.IGNORECASE)
                    asn_value = match.group(1) if match else ""
                asn_text = str(asn_value or "").upper().removeprefix("AS")
                asn = int(asn_text) if asn_text.isdigit() else None
                business = str(
                    payload.get("as_name")
                    or as_record.get("name")
                    or re.sub(r"^AS\d+\s*", "", org_text, flags=re.IGNORECASE)
                    or org_text
                    or ""
                ).strip()
                country_name = str(payload.get("country_name") or geo_record.get("country") or "").strip()
                country_value = str(payload.get("country") or "").strip()
                country_code = str(payload.get("country_code") or geo_record.get("country_code") or "").strip()
                if not country_code and len(country_value) == 2:
                    country_code = country_value.upper()
                elif not country_name and country_value and len(country_value) != 2:
                    country_name = country_value
                result = {
                    "asn": asn,
                    "business_name": business,
                    "country_name": country_name,
                    "country_code": country_code,
                    "provider": "ipinfo",
                    "ipinfo_error": "",
                }
                if any(result.get(key) not in (None, "") for key in ("asn", "business_name", "country_name", "country_code")):
                    return result
                errors.append("IPinfo returned no GeoIP fields")
            except urllib.error.HTTPError as exc:
                if exc.code in {401, 403}:
                    errors.append(f"IPinfo rejected the saved token (HTTP {exc.code})")
                elif exc.code == 429:
                    errors.append("IPinfo rate limit reached (HTTP 429)")
                else:
                    errors.append(f"IPinfo HTTP {exc.code}")
            except urllib.error.URLError as exc:
                reason = str(getattr(exc, "reason", "") or exc)
                errors.append(f"IPinfo connection failed: {reason[:180]}")
            except TimeoutError:
                errors.append("IPinfo request timed out")
            except (ValueError, OSError) as exc:
                errors.append(f"IPinfo response error: {str(exc)[:180]}")
        unique_errors = list(dict.fromkeys(item for item in errors if item))
        return {"ipinfo_error": "; ".join(unique_errors) or "IPinfo lookup failed"}

    def details(self, ip_text: str) -> dict[str, Any]:
        try:
            address = ipaddress.ip_address(ip_text)
            normalized = str(address)
        except ValueError:
            return {
                "ip": ip_text, "asn": None, "business_name": "",
                "country_name": "", "country_code": "", "provider": "",
            }
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(normalized)
            cache_ttl = 30 if cached is not None and cached[1].get("ipinfo_error") else 21600
            if cached is not None and now - cached[0] < cache_ttl:
                return dict(cached[1])
        result: dict[str, Any] = {
            "ip": normalized, "asn": None, "business_name": "",
            "country_name": "", "country_code": "", "provider": "",
        }
        config = load_geoip_settings(include_secrets=True)
        provider = str(config.get("provider") or "auto")

        # Provider order:
        #   auto     -> IPinfo first, then MaxMind only for missing fields
        #   ipinfo   -> IPinfo only
        #   maxmind  -> MaxMind databases only
        if provider in {"auto", "ipinfo"} and not address.is_private and not address.is_loopback:
            token = str(config.get("ipinfo_token") or "")
            remote = self._ipinfo_details(normalized, token) if token else {}
            if remote.get("ipinfo_error"):
                result["ipinfo_error"] = str(remote.get("ipinfo_error") or "")
            if remote:
                for key in ("asn", "business_name", "country_name", "country_code"):
                    if remote.get(key) not in (None, ""):
                        result[key] = remote.get(key)
                if any(result.get(key) not in (None, "") for key in ("asn", "business_name", "country_name", "country_code")):
                    result["provider"] = "ipinfo"

        if provider in {"auto", "maxmind"}:
            fallback_used = False
            asn_reader = self._reader(settings.asn_db_path)
            if asn_reader is not None:
                try:
                    record = asn_reader.get(normalized) or {}
                    local_asn = int(record["autonomous_system_number"]) if record.get("autonomous_system_number") else None
                    local_business = str(record.get("autonomous_system_organization") or "")
                    for key, value in (("asn", local_asn), ("business_name", local_business)):
                        if provider == "maxmind" or result.get(key) in (None, ""):
                            if value not in (None, ""):
                                result[key] = value
                                fallback_used = True
                except Exception:
                    pass
            country_reader = self._reader(settings.country_db_path, country=True)
            if country_reader is not None:
                try:
                    record = country_reader.get(normalized) or {}
                    country = record.get("country") or record.get("registered_country") or {}
                    names = country.get("names") or {}
                    local_country = str(names.get("en") or "")
                    local_code = str(country.get("iso_code") or "")
                    for key, value in (("country_name", local_country), ("country_code", local_code)):
                        if provider == "maxmind" or result.get(key) in (None, ""):
                            if value not in (None, ""):
                                result[key] = value
                                fallback_used = True
                except Exception:
                    pass
            if fallback_used:
                result["provider"] = "maxmind" if provider == "maxmind" or not result.get("provider") else "ipinfo+maxmind"
        with self._lock:
            if len(self._cache) > 10000:
                self._cache.clear()
            self._cache[normalized] = (now, dict(result))
        return result

    def lookup(self, ip_text: str) -> int | None:
        value = self.details(ip_text).get("asn")
        return int(value) if value is not None else None


asn_resolver = GeoIPResolver()


def clear_geo_cache() -> None:
    with asn_resolver._lock:
        asn_resolver._cache.clear()
        for reader_name in ("_asn_reader", "_country_reader"):
            reader = getattr(asn_resolver, reader_name, None)
            if reader is not None:
                try:
                    reader.close()
                except Exception:
                    pass
            setattr(asn_resolver, reader_name, None)
        asn_resolver._asn_path = ""
        asn_resolver._country_path = ""


def geo_details(ip_text: str) -> dict[str, Any]:
    return asn_resolver.details(ip_text)


def evaluate_access(
    request: Request,
    *,
    ip_whitelist: str | None = None,
    ip_blacklist: str | None = None,
    asn_whitelist: str | None = None,
    asn_blacklist: str | None = None,
) -> AccessDecision:
    ip_text = client_ip(request)
    if ip_matches(ip_blacklist, ip_text):
        return AccessDecision(False, ip_text, None, "Client IP is blacklisted")
    if (ip_whitelist or "").strip() and not ip_matches(ip_whitelist, ip_text):
        return AccessDecision(False, ip_text, None, "Client IP is not in the whitelist")

    needs_asn = bool((asn_whitelist or "").strip() or (asn_blacklist or "").strip())
    asn = asn_resolver.lookup(ip_text) if needs_asn else None
    if needs_asn and asn is None:
        return AccessDecision(False, ip_text, None, "ASN policy is configured but the ASN database could not identify this IP")
    if asn_matches(asn_blacklist, asn):
        return AccessDecision(False, ip_text, asn, f"ASN AS{asn} is blacklisted")
    if (asn_whitelist or "").strip() and not asn_matches(asn_whitelist, asn):
        return AccessDecision(False, ip_text, asn, f"ASN AS{asn} is not in the whitelist")
    return AccessDecision(True, ip_text, asn)


def _database_health(path: Path, missing_message: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "module_loaded": maxminddb is not None,
        "loaded": False,
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "error": "",
    }
    if maxminddb is None:
        result["error"] = "Python maxminddb module is not installed"
        return result
    if not path.is_file():
        result["error"] = missing_message
        return result
    try:
        reader = maxminddb.open_database(str(path))
        metadata = reader.metadata()
        result["loaded"] = True
        result["database_type"] = getattr(metadata, "database_type", "")
        result["build_epoch"] = int(getattr(metadata, "build_epoch", 0) or 0)
        result["ip_version"] = int(getattr(metadata, "ip_version", 0) or 0)
        reader.close()
    except Exception as exc:
        result["error"] = str(exc)
    return result


def asn_database_status() -> dict[str, Any]:
    """Return ASN and Country database health in one operator-friendly object."""
    asn = _database_health(settings.asn_db_path, "GeoLite2-ASN.mmdb was not found")
    country = _database_health(settings.country_db_path, "GeoLite2-Country.mmdb was not found")
    result = dict(asn)
    marker = settings.asn_db_path.parent / ".geoip-last-update"
    result.update(
        {
            "country_path": country["path"],
            "country_exists": country["exists"],
            "country_loaded": country["loaded"],
            "country_size_bytes": country["size_bytes"],
            "country_database_type": country.get("database_type", ""),
            "country_error": country.get("error", ""),
            "auto_update_enabled": bool(load_geoip_settings().get("auto_update") or settings.geoip_auto_update),
            "auto_update_configured": bool(load_geoip_settings().get("maxmind_configured") or (settings.maxmind_account_id and settings.maxmind_license_key)),
            "provider": str(load_geoip_settings().get("provider") or "auto"),
            "ipinfo_configured": bool(load_geoip_settings().get("ipinfo_configured")),
            "last_auto_update": marker.read_text(encoding="utf-8").strip() if marker.is_file() else "",
        }
    )
    return result
