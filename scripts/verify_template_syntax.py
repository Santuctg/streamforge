#!/usr/bin/env python3
"""STREAMFORGE_TEMPLATE_SYNTAX_GUARD_V1351: fail fast on broken Jinja templates.

v13.48 shipped ``app/templates/web_player.html`` with a stray ``{% endif %}``
left behind when the device-theme ``<style>`` block moved.  Jinja only compiles a
template on first use, so the panel kept starting and every Playlist/Web Player
alias returned HTTP 500 "Internal Server Error" until the template was fixed.

This guard parses every HTML template and renders the public Web Player in both
the signed-out and signed-in branches.  It also renders the watch screen
(``player.html``) in its default and saved-appearance branches and asserts that
the device-theme bootstrap, light-theme block and toggle are still present.  It
runs before a release ZIP is built and inside the v13.51+ updater, so a template
that cannot compile — or a watch screen that silently dropped its Dark/Light
switch — can never ship again.

Usage: python3 scripts/verify_template_syntax.py [project-root]
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

try:
    from jinja2 import ChainableUndefined, Environment
except ImportError as exc:  # pragma: no cover - release hosts always ship Jinja2
    raise SystemExit(f"FAIL: Jinja2 is required to verify templates: {exc}")


def _environment() -> Environment:
    env = Environment(undefined=ChainableUndefined, autoescape=True)
    # The application registers this filter in app/main.py; rendering must not
    # depend on importing the full FastAPI app.
    env.filters.setdefault("versioned_asset", lambda value: value)
    return env


def _logged_in_context() -> dict[str, object]:
    return {
        "app_version": "test",
        "prefix": "/123",
        "branding": SimpleNamespace(
            name="StreamForge", logo_url="", favicon_url=""
        ),
        "user": SimpleNamespace(xtream_username="viewer", expires_at=None),
        "webplayer_settings": SimpleNamespace(
            page_rgba="rgba(8,16,25,.9)",
            panel_rgba="rgba(17,27,39,.9)",
            accent_rgba="rgba(75,140,255,1)",
            text_rgba="rgba(238,245,251,1)",
            show_user_info=True,
            show_connection_info=True,
            download_available=True,
            login_mode="manual",
            hide_hover_urls=True,
        ),
        "hide_quick_user_info": False,
        "header_max_connections": 1,
        "header_online_use": 1,
        "categories": ["News"],
        "channels": [
            SimpleNamespace(
                slug="sf-1-test",
                name="Test Channel",
                width=1920,
                height=1080,
                logo_url="",
                category=None,
                categories=[SimpleNamespace(name="News")],
            )
        ],
    }


def _player_context(settings: bool = True) -> dict[str, object]:
    channel = SimpleNamespace(
        slug="sf-1-test",
        name="Test Channel",
        logo_url="",
        input_url="https://example.test/live.m3u8",
        backup_inputs="",
        category=None,
        categories=[SimpleNamespace(name="News")],
    )
    return {
        "app_version": "test",
        "prefix": "/123",
        "token": "viewer-token",
        "branding": SimpleNamespace(
            name="StreamForge", logo_url="", favicon_url=""
        ),
        "channel": channel,
        "channels": [channel],
        "back_url": "/123",
        "watch_prefix": "/123/web-player/watch",
        "stream_url": "/123/play/viewer-token/sf-1-test/master.m3u8?wp=1",
        "canonical_player_url": "/123",
        "player_favicon_url": "",
        "player_username": "viewer",
        "player_expires": "Never",
        "player_max_connections": 1,
        "player_online_use": 1,
        "hide_quick_user_info": False,
        "webplayer_settings": _logged_in_context()["webplayer_settings"]
        if settings
        else None,
    }


def verify(root: Path) -> None:
    templates = root / "app" / "templates"
    files = sorted(templates.glob("*.html"))
    if not files:
        raise SystemExit(f"FAIL: no templates found under {templates}")

    env = _environment()
    failures: list[str] = []
    for path in files:
        try:
            env.parse(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - report every broken template
            failures.append(f"{path.name}: {type(exc).__name__}: {exc}")

    # Render smoke tests cover structure the parser accepts but runtime rejects
    # (for example an out-of-place else/endfor or an unknown filter).
    web_player = templates / "web_player.html"
    if not failures:
        source = web_player.read_text(encoding="utf-8")
        try:
            template = env.from_string(source)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"web_player.html: {type(exc).__name__}: {exc}")
            template = None
        if template is not None:
            for label, context in (
                ("signed-out", {}),
                ("signed-in", _logged_in_context()),
            ):
                try:
                    rendered = template.render(**context)
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        f"web_player.html ({label}): {type(exc).__name__}: {exc}"
                    )
                    continue
                if label == "signed-out" and "Sign in to watch" not in rendered:
                    failures.append(
                        "web_player.html (signed-out): login form missing"
                    )
                if label == "signed-in" and 'class="card"' not in rendered:
                    failures.append(
                        "web_player.html (signed-in): channel grid missing"
                    )

    player = templates / "player.html"
    if not failures:
        source = player.read_text(encoding="utf-8")
        try:
            template = env.from_string(source)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"player.html: {type(exc).__name__}: {exc}")
            template = None
        if template is not None:
            # v13.52: the watch screen must keep the device theme and its toggle
            # in both the saved-appearance and default branches.
            for label, context in (
                ("default", _player_context(settings=False)),
                ("themed", _player_context(settings=True)),
            ):
                try:
                    rendered = template.render(**context)
                except Exception as exc:  # noqa: BLE001
                    failures.append(
                        f"player.html ({label}): {type(exc).__name__}: {exc}"
                    )
                    continue
                if 'class="player-body"' not in rendered:
                    failures.append(
                        f"player.html ({label}): watch shell missing"
                    )
                if "STREAMFORGE_DEVICE_LOCAL_THEME_BOOTSTRAP_V1344" not in rendered:
                    failures.append(
                        f"player.html ({label}): device theme bootstrap missing"
                    )
                if "STREAMFORGE_PLAYER_DEVICE_THEME_LIGHT_V1352" not in rendered:
                    failures.append(
                        f"player.html ({label}): light theme block missing"
                    )
                if "STREAMFORGE_DEVICE_LOCAL_THEME_TOGGLE_V1344" not in rendered:
                    failures.append(
                        f"player.html ({label}): theme toggle script missing"
                    )
                if 'data-theme-toggle' not in rendered:
                    failures.append(
                        f"player.html ({label}): theme toggle button missing"
                    )
                # v13.54: the self-hosted HLS.js copy must load from the host-root
                # /static/ cache; the old {{ prefix }}-prefixed URL is silently
                # dropped behind playlist aliases.
                if 'href="/static/vendor/hls.min.js?v=' not in rendered:
                    failures.append(
                        f"player.html ({label}): root /static/ HLS.js preload missing"
                    )
                if any("prefix" in line for line in rendered.splitlines() if "static/vendor/hls.min.js" in line):
                    failures.append(
                        f"player.html ({label}): HLS.js still uses the prefixed aliased path"
                    )

    if failures:
        print("FAIL: template verification failed", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)

    print(
        "PASS: STREAMFORGE_TEMPLATE_SYNTAX_GUARD_V1351 "
        f"parsed {len(files)} templates, rendered web_player.html twice "
        "and player.html twice"
    )


if __name__ == "__main__":
    verify(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve())
