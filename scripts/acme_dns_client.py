#!/usr/bin/env python3
"""Minimal acme-dns REST client used by StreamForge DNS-01 TLS automation.

The account file contains one restricted credential per certificate hostname.
Each credential can update only the unique acme-dns subdomain returned by the
registration endpoint.  The user's authoritative DNS needs only a one-time
_acme-challenge CNAME pointing at that fulldomain.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_API = "https://auth.acme-dns.io"


def api_url() -> str:
    return str(os.getenv("STREAMFORGE_ACME_DNS_API", DEFAULT_API) or DEFAULT_API).strip().rstrip("/")


def _read_accounts(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"accounts": {}}
    if not isinstance(raw, dict):
        return {"accounts": {}}
    if not isinstance(raw.get("accounts"), dict):
        raw["accounts"] = {}
    return raw


def _write_accounts(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".acme-dns-", dir=str(path.parent))
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        temp.replace(path)
        os.chmod(path, 0o600)
    finally:
        temp.unlink(missing_ok=True)


def _json_request(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None, timeout: float = 15.0) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    final_headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "StreamForge-ACME-DNS/4.1"}
    final_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=final_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
            if int(getattr(response, "status", 200) or 200) not in {200, 201}:
                raise RuntimeError(f"acme-dns returned HTTP {getattr(response, 'status', '?')}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(f"acme-dns HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"acme-dns request failed: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("acme-dns returned invalid JSON")
    return data


def ensure_account(host: str, account_file: Path) -> dict[str, str]:
    host = str(host or "").strip().lower().rstrip(".")
    service = api_url()
    store = _read_accounts(account_file)
    current = store["accounts"].get(host)
    if isinstance(current, dict) and str(current.get("api_url") or "").rstrip("/") == service:
        required = ("username", "password", "subdomain", "fulldomain")
        if all(str(current.get(key) or "").strip() for key in required):
            return {key: str(current.get(key) or "") for key in (*required, "api_url")}
    data = _json_request(service + "/register", {})
    required = ("username", "password", "subdomain", "fulldomain")
    if not all(str(data.get(key) or "").strip() for key in required):
        raise RuntimeError("acme-dns registration response is incomplete")
    account = {key: str(data[key]).strip() for key in required}
    account["api_url"] = service
    store["accounts"][host] = account
    store["api_url"] = service
    _write_accounts(account_file, store)
    return account


def account_for(host: str, account_file: Path) -> dict[str, str]:
    host = str(host or "").strip().lower().rstrip(".")
    current = _read_accounts(account_file).get("accounts", {}).get(host)
    if not isinstance(current, dict):
        raise RuntimeError(f"No acme-dns account is registered for {host}")
    required = ("username", "password", "subdomain", "fulldomain")
    if not all(str(current.get(key) or "").strip() for key in required):
        raise RuntimeError(f"Stored acme-dns account is incomplete for {host}")
    return {key: str(current.get(key) or "") for key in (*required, "api_url")}


def update_txt(host: str, validation: str, account_file: Path) -> dict[str, Any]:
    account = account_for(host, account_file)
    service = str(account.get("api_url") or api_url()).rstrip("/")
    return _json_request(
        service + "/update",
        {"subdomain": account["subdomain"], "txt": str(validation or "").strip()},
        headers={"X-Api-User": account["username"], "X-Api-Key": account["password"]},
    )


def cname_name(host: str) -> str:
    return "_acme-challenge." + str(host or "").strip().lower().rstrip(".")


def _dig(record_type: str, name: str) -> list[str]:
    dig = shutil_which("dig")
    if not dig:
        raise RuntimeError("dig is not installed (install dnsutils)")
    completed = subprocess.run([dig, "+time=3", "+tries=1", "+short", record_type, name], text=True, capture_output=True, timeout=6, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "dig failed").strip()[-800:])
    return [line.strip().strip('"').rstrip(".") for line in completed.stdout.splitlines() if line.strip()]


def shutil_which(name: str) -> str | None:
    # Local helper avoids importing a large dependency and is easy to mock.
    from shutil import which
    return which(name)


def cname_matches(host: str, target: str) -> tuple[bool, str]:
    expected = str(target or "").strip().lower().rstrip(".")
    try:
        answers = _dig("CNAME", cname_name(host))
    except Exception as exc:
        return False, str(exc)
    found = answers[0].lower().rstrip(".") if answers else ""
    if found == expected:
        return True, found
    if not found:
        return False, "CNAME not found yet"
    return False, f"CNAME points to {found}, expected {expected}"


def wait_txt(target: str, validation: str, *, timeout: int = 30) -> tuple[bool, str]:
    deadline = time.monotonic() + max(5, int(timeout))
    expected = str(validation or "").strip()
    last = ""
    while time.monotonic() < deadline:
        try:
            answers = _dig("TXT", target)
            normalized = [item.replace('" "', '').replace('"', '') for item in answers]
            if expected in normalized:
                return True, "delegated TXT is visible"
            last = ", ".join(normalized)
        except Exception as exc:
            last = str(exc)
        time.sleep(2)
    return False, f"delegated TXT did not become visible: {last or 'no TXT answer'}"
