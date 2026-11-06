#!/usr/bin/env python3
"""v13.17 GitHub API rate-limit hardening contract.

Production evidence (server.chitrobox.net, v13.15): the scheduled Main update
job failed with "HTTP Error 403: rate limit exceeded" from the unauthenticated
api.github.com listing request. Anonymous GitHub API access is capped at 60
requests/hour per server IP. v13.17 adds tag-scoped single-release planning,
optional token auth, a 5-minute web feed cache with stale-cache fallback and
clear rate-limit errors.
"""
from pathlib import Path
import importlib.util
import json
import os


ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text(encoding="utf-8").strip() != "13.17":
    print("SKIP: v13.17 GitHub rate limit contract")
    raise SystemExit(0)

helper = importlib.util.spec_from_file_location(
    "sf_msc_v1317", ROOT / "scripts/main_system_control.py")
msc = importlib.util.module_from_spec(helper)
helper.loader.exec_module(msc)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return json.dumps(self._payload).encode("utf-8")


CALLS: list[str] = []
AUTH: list[dict[str, str]] = []


def _fake_urlopen(request, timeout=0):
    CALLS.append(request.full_url)
    AUTH.append(dict(request.header_items()))
    url = request.full_url
    if "/releases/tags/" in url:
        version = url.rsplit("/", 1)[-1].lstrip("v")
        return _FakeResponse({
            "tag_name": f"v{version}",
            "name": f"StreamForge v{version}",
            "assets": [{
                "name": f"streamforge-{version}.zip",
                "browser_download_url": (
                    "https://github.com/Santuctg/streamforge/releases/download/"
                    f"v{version}/streamforge-{version}.zip"),
                "digest": "sha256:" + "0" * 63 + "a",
            }],
        })
    return _FakeResponse([
        {"tag_name": "v13.16", "assets": [{
            "name": "streamforge-13.16.zip",
            "browser_download_url": "https://github.com/Santuctg/streamforge/releases/download/v13.16/streamforge-13.16.zip",
            "digest": "sha256:" + "0" * 63 + "a"}]},
        {"tag_name": "v13.17", "assets": [{
            "name": "streamforge-13.17.zip",
            "browser_download_url": "https://github.com/Santuctg/streamforge/releases/download/v13.17/streamforge-13.17.zip",
            "digest": "sha256:" + "0" * 63 + "b"}]},
    ])


msc.urllib.request.urlopen = _fake_urlopen

# Sequential minor update: exactly one tag-scoped API request.
CALLS.clear(); AUTH.clear()
plan = msc._github_update_plan("13.16", "13.17")
assert [item["version"] for item in plan] == ["13.17"], plan
assert len(CALLS) == 1 and CALLS[0].endswith("/releases/tags/v13.17"), CALLS
assert not any("Authorization" in headers for headers in AUTH), AUTH

# Multi-release gap: the full listing still plans every intermediate release.
CALLS.clear(); AUTH.clear()
plan = msc._github_update_plan("13.15", "13.17")
assert [item["version"] for item in plan] == ["13.16", "13.17"], plan
assert "/releases/tags/" not in CALLS[0] and len(CALLS) == 1, CALLS
print("PASS: v13.17 helper plans updates with the fewest GitHub API requests")

# Optional token is sent as Bearer auth for both request styles.
os.environ["STREAMFORGE_GITHUB_TOKEN"] = "ghs_regression_token"
CALLS.clear(); AUTH.clear()
msc._github_update_plan("13.16", "13.17")
assert any(h.get("Authorization") == "Bearer ghs_regression_token" for h in AUTH), AUTH
os.environ.pop("STREAMFORGE_GITHUB_TOKEN")

# Rate-limit responses produce an actionable message, not a bare HTTP 403.
class _RateLimited:
    code = 403

    def __exit__(self, *args):
        return False

msc._LOADED_ENV_FOR_TEST = False
os.environ.pop("GITHUB_TOKEN", None)


def _rate_limit_urlopen(request, timeout=0):
    raise msc.urllib.error.HTTPError(request.full_url, 403, "rate limit exceeded", {}, None)


msc.urllib.request.urlopen = _rate_limit_urlopen
try:
    msc._github_update_plan("13.16", "13.17")
except RuntimeError as exc:
    assert "rate limit" in str(exc) and "STREAMFORGE_GITHUB_TOKEN" in str(exc), str(exc)
else:
    raise AssertionError("rate limit must fail loudly with guidance")
os.environ["STREAMFORGE_GITHUB_TOKEN"] = "ghs_regression_token"

# Static replacement markers for the updater walk.
helper_text = (ROOT / "scripts/main_system_control.py").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_MAIN_GITHUB_API_TOKEN_V1317",
    "STREAMFORGE_MAIN_TAG_SCOPED_RELEASE_PLAN_V1317",
    "STREAMFORGE_GITHUB_TOKEN",
    "/releases/tags/",
):
    assert marker in helper_text, marker

main = (ROOT / "app/main.py").read_text(encoding="utf-8")
for marker in (
    "STREAMFORGE_MAIN_GITHUB_RATE_LIMIT_V1317",
    "STREAMFORGE_GITHUB_TOKEN",
):
    assert marker in main, marker
assert "< 60:" not in main.split("def _github_latest_release", 1)[1].split("try:", 1)[0]

print("PASS: v13.17 GitHub rate-limit hardening contract")
