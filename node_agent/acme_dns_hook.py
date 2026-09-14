#!/usr/bin/env python3
from __future__ import annotations
import os, sys
from pathlib import Path
from acme_dns_client import update_txt, wait_txt, account_for

ACCOUNT_FILE = Path(os.getenv("STREAMFORGE_ACME_DNS_ACCOUNT_FILE", "/var/lib/streamforge-node/acme-dns/accounts.json"))

def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "auth").strip().lower()
    if mode == "cleanup":
        return 0
    host = str(os.getenv("CERTBOT_DOMAIN") or "").strip().lower().rstrip(".")
    validation = str(os.getenv("CERTBOT_VALIDATION") or "").strip()
    if not host or not validation:
        print("Missing CERTBOT_DOMAIN/CERTBOT_VALIDATION", file=sys.stderr)
        return 2
    try:
        update_txt(host, validation, ACCOUNT_FILE)
        account = account_for(host, ACCOUNT_FILE)
        ok, detail = wait_txt(account["fulldomain"], validation, timeout=35)
        if not ok:
            raise RuntimeError(detail)
        print(detail)
        return 0
    except Exception as exc:
        print(f"StreamForge Node DNS-01 hook failed: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
