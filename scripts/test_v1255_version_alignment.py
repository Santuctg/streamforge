#!/usr/bin/env python3
from __future__ import annotations
# Historical release check: runs only on its own release.
if (__import__("pathlib").Path(__file__).resolve().parents[1] / "VERSION").read_text().strip() != "12.55":
    print("SKIP: v12.55-only regression check (superseded by later releases)")
    raise SystemExit(0)

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def version(path: str) -> str:
    return read(path).strip()


def require(needle: str, haystack: str, message: str) -> None:
    if needle not in haystack:
        raise SystemExit(message)


def main() -> None:
    if version("VERSION") != "12.55":
        raise SystemExit("Main VERSION must be 12.55")
    if version("node_agent/VERSION") != "12.55":
        raise SystemExit("bundled Node Agent VERSION must be 12.55")

    updater_path = ROOT / "scripts/force_update_v1255.sh"
    if not updater_path.is_file():
        raise SystemExit("scripts/force_update_v1255.sh is missing")

    updater = updater_path.read_text(encoding="utf-8")
    update_wrapper = read("scripts/update.sh")

    require("force_update_v1255.sh", update_wrapper, "scripts/update.sh must select v12.55 updater")
    require("STREAMFORGE_VERSION_ALIGNMENT_V1255", updater, "v12.55 alignment marker missing")
    require('TARGET_VERSION="12.55"', updater, "Main target version is not 12.55")
    require('NODE_TARGET_VERSION="12.55"', updater, "Node target version is not 12.55")
    require("STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254", updater, "v12.54 static-cache carry guard missing")
    require("STREAMFORGE_MAIN_WAITING_RECOVERY_V1253", updater, "v12.53 Waiting carry guard missing")
    require("STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251", updater, "v12.51 Node carry guard missing")
    require("12.54", updater, "updater must accept installed Main v12.54")
    require("12.52", updater, "updater must accept installed bundled Node v12.52")

    node_app = read("node_agent/app.py")
    require(
        "STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251",
        node_app,
        "Node relay-health dedupe marker missing",
    )

    print("PASS: v12.55 Main/Node version alignment contract verified")


if __name__ == "__main__":
    main()
