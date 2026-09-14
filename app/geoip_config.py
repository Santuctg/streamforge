from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .config import settings
from .secrets_store import decrypt_secret, encrypt_secret

GEOIP_SETTINGS_FILE = Path(os.getenv("STREAMFORGE_GEOIP_SETTINGS_FILE", "/opt/streamforge/geoip-settings.json"))

DEFAULTS: dict[str, Any] = {
    "provider": "auto",
    "auto_update": False,
    "maxmind_account_id": "",
    "maxmind_license_key_enc": "",
    "ipinfo_token_enc": "",
}


def masked_saved_secret(value: str) -> str:
    """Return a safe saved-secret hint without exposing the complete value."""
    normalized = str(value or "").strip()
    if not normalized:
        return "Not saved"
    suffix = normalized[-4:] if len(normalized) >= 4 else normalized
    return f"Saved — ••••{suffix}"


def load_geoip_settings(*, include_secrets: bool = False) -> dict[str, Any]:
    data = dict(DEFAULTS)
    try:
        raw = json.loads(GEOIP_SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update(raw)
    except (OSError, ValueError, TypeError):
        pass
    provider = str(data.get("provider") or "auto").strip().lower()
    if provider not in {"auto", "maxmind", "ipinfo"}:
        provider = "auto"
    data["provider"] = provider
    data["auto_update"] = bool(data.get("auto_update"))
    data["maxmind_account_id"] = str(data.get("maxmind_account_id") or "").strip()
    maxmind_secret = decrypt_secret(str(data.get("maxmind_license_key_enc") or "")) or ""
    ipinfo_secret = decrypt_secret(str(data.get("ipinfo_token_enc") or "")) or ""
    data["maxmind_configured"] = bool(data["maxmind_account_id"] and maxmind_secret)
    data["ipinfo_configured"] = bool(ipinfo_secret)
    data["maxmind_saved_display"] = masked_saved_secret(maxmind_secret)
    data["ipinfo_saved_display"] = masked_saved_secret(ipinfo_secret)
    if include_secrets:
        data["maxmind_license_key"] = maxmind_secret
        data["ipinfo_token"] = ipinfo_secret
    data.pop("maxmind_license_key_enc", None)
    data.pop("ipinfo_token_enc", None)
    return data


def save_geoip_settings(
    *,
    provider: str,
    auto_update: bool,
    maxmind_account_id: str,
    maxmind_license_key: str = "",
    ipinfo_token: str = "",
    remove_maxmind_key: bool = False,
    remove_ipinfo_token: bool = False,
) -> dict[str, Any]:
    existing = dict(DEFAULTS)
    try:
        raw = json.loads(GEOIP_SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            existing.update(raw)
    except (OSError, ValueError, TypeError):
        pass
    provider = str(provider or "auto").strip().lower()
    if provider not in {"auto", "maxmind", "ipinfo"}:
        provider = "auto"
    payload = {
        "provider": provider,
        "auto_update": bool(auto_update),
        "maxmind_account_id": str(maxmind_account_id or "").strip(),
        "maxmind_license_key_enc": "" if remove_maxmind_key else str(existing.get("maxmind_license_key_enc") or ""),
        "ipinfo_token_enc": "" if remove_ipinfo_token else str(existing.get("ipinfo_token_enc") or ""),
    }
    if maxmind_license_key.strip():
        payload["maxmind_license_key_enc"] = encrypt_secret(maxmind_license_key.strip()) or ""
    if ipinfo_token.strip():
        payload["ipinfo_token_enc"] = encrypt_secret(ipinfo_token.strip()) or ""
    GEOIP_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = GEOIP_SETTINGS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(GEOIP_SETTINGS_FILE)
    return load_geoip_settings()


def run_maxmind_update(timeout: int = 180) -> tuple[bool, str]:
    script = settings.asn_db_path.parent / "scripts" / "update_geoip_databases.sh"
    if not script.is_file():
        script = Path("/opt/streamforge/scripts/update_geoip_databases.sh")
    if not script.is_file():
        return False, "GeoIP update script is missing"
    try:
        completed = subprocess.run(
            [str(script), "--force"],
            text=True,
            capture_output=True,
            timeout=max(30, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "GeoIP update timed out"
    output = "\n".join(part.strip() for part in [completed.stdout, completed.stderr] if part.strip())[-8000:]
    return completed.returncode == 0, output or ("Update completed" if completed.returncode == 0 else "Update failed")
