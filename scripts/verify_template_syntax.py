#!/usr/bin/env python3
"""STREAMFORGE_TEMPLATE_SYNTAX_GUARD_V1351: fail fast on broken Jinja templates.

v13.48 shipped ``app/templates/web_player.html`` with a stray ``{% endif %}``
left behind when the device-theme ``<style>`` block moved.  Jinja only compiles a
template on first use, so the panel kept starting and every Playlist/Web Player
alias returned HTTP 500 "Internal Server Error" until the template was fixed.

This guard parses every HTML template and renders the public Web Player in both
the signed-out and signed-in branches.  It runs before a release ZIP is built
and inside the v13.51 updater, so a template that cannot compile can never ship
again.

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

    if failures:
        print("FAIL: template verification failed", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)

    print(
        "PASS: STREAMFORGE_TEMPLATE_SYNTAX_GUARD_V1351 "
        f"parsed {len(files)} templates and rendered web_player.html twice"
    )


if __name__ == "__main__":
    verify(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve())
