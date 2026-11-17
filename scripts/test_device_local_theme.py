#!/usr/bin/env python3
"""Device-local Dark/Light theme contracts across all browser surfaces."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
base = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")
main_css = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
app_js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
dashboard_js = (ROOT / "app/static/dashboard.js").read_text(encoding="utf-8")
node = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")
webplayer = (ROOT / "app/templates/web_player.html").read_text(encoding="utf-8")
login = (ROOT / "app/templates/login.html").read_text(encoding="utf-8")
light_css = (ROOT / "app/static/theme-light.css").read_text(encoding="utf-8")

for source, name in (
    (base, "Main template"),
    (app_js, "Main JS"),
    (node, "Node"),
    (webplayer, "Web Player"),
):
    assert "streamforge.theme" in source, f"{name} does not use the shared device theme key"

assert "data-theme-toggle" in base, "Main theme toggle markup is missing"
assert "dataset.theme='light'" in base, "Main light theme bootstrap is missing"
assert "dataset.theme=t" in node, "Node light theme bootstrap is missing"
assert "data-theme-toggle" in node, "Node theme toggle is missing"
assert "data-theme-toggle" in webplayer, "Web Player theme toggle is missing"
assert "[data-theme=\"light\"]" in main_css, "Main light variable overrides are missing"
assert "topbar-actions" in base, "Main topbar theme toggle group is missing"
assert "login-theme-toggle" in login, "Main login screen theme toggle is missing"
# The signed-in theme switch sits inside the name card and stays usable on phones.
assert "STREAMFORGE_MAIN_USER_CARD_THEME_TOGGLE_V1350" in main_css, (
    "name-card theme toggle styling is missing"
)
assert 'class="admin-chip-id"' in base and "admin-chip-id" in main_css
card = base.split('class="topbar-actions"', 1)[1].split("</header>", 1)[0]
assert "admin-chip" in card and "data-theme-toggle" in card, (
    "theme toggle must live inside the name card"
)
assert ".topbar .admin-chip-id{display:none}" in main_css, (
    "phones must hide the name text but keep the theme switch"
)
assert "STREAMFORGE_LIGHT_LOGIN_AUTOFILL_V1348" in light_css, "light autofill override is missing"

# --- Node panel: hardcoded (non-variable) dark colours get their own light pass.
assert "STREAMFORGE_NODE_LIGHT_THEME_HARDCODED_COLORS_V1348" in node, "Node hardcoded light overrides are missing"
assert ':root[data-theme="light"] .panel-user{' in node, "Node panel-user light override is missing"
assert 'querySelectorAll("[data-theme-toggle]")' in node, "Node toggle must paint every theme button"

# --- v13.53: the Node light pass is global, the light checkbox tick stays dark
# --- and visible, and every Node public surface carries the device theme.
assert (
    node.index("def _node_panel_css()")
    < node.index("STREAMFORGE_NODE_LIGHT_THEME_HARDCODED_COLORS_V1348")
    < node.index("def _node_panel_document(")
), "Node light overrides must live in the global _node_panel_css() stylesheet"
assert (
    ':root[data-theme="light"] input[type=checkbox]:checked::after{background:#071c18!important}' in node
), "Node light checkbox tick must stay dark and visible"
assert "STREAMFORGE_NODE_LIGHT_SURFACES_V1353" in node, "Node panel light surface overrides are missing"
assert "STREAMFORGE_NODE_DEVICE_THEME_SHARED_SCRIPT_V1353" in node, "shared Node device theme script is missing"
assert "STREAMFORGE_NODE_DEVICE_THEME_TOGGLE_CSS_V1353" in node, "Node theme toggle styling is missing"
assert "STREAMFORGE_NODE_LOGIN_DEVICE_THEME_LIGHT_V1353" in node, "Node panel login light theme is missing"
assert "STREAMFORGE_NODE_WEBPLAYER_DEVICE_THEME_LIGHT_V1353" in node, "Node Web Player login/home light theme is missing"
assert "STREAMFORGE_NODE_WATCH_DEVICE_THEME_LIGHT_V1353" in node, "Node Web Player watch light theme is missing"
assert "STREAMFORGE_NODE_EXPIRED_DEVICE_THEME_LIGHT_V1353" in node, "Node expired page light theme is missing"
assert "STREAMFORGE_NODE_DISCONNECTED_DEVICE_THEME_LIGHT_V1353" in node, "Node disconnected page light theme is missing"
# bootstrap runs in the head of: web login, web home, watch, expired, panel
# login, main-disconnected (panel document keeps its own inline copy).
assert node.count("_node_device_theme_script()") >= 6, (
    "every Node public page must bootstrap the device theme in <head>"
)
assert node.count("_node_theme_toggle_button(") >= 7, "every Node public page must offer the theme toggle"
assert (
    "_node_webplayer_theme_css(request) + _node_webplayer_device_theme_css()" in node
), "device light CSS must load after the managed theme tokens"
# --- v13.54/v13.56: light select popups follow the element color-scheme on both planes.
assert (
    ':root[data-theme="light"] body select:not([multiple]):not(#streamforge-dropdown-never),:root[data-theme="light"] body select[multiple]:not(#streamforge-dropdown-never){color-scheme:light!important}' in light_css
), "Main light theme must force light color-scheme on selects (id-guarded to beat the dark rule)"
assert (
    ':root[data-theme="light"] body select:not([multiple]):not(#streamforge-dropdown-never) option,:root[data-theme="light"] body select[multiple]:not(#streamforge-dropdown-never) option{background:#f2f6fa!important;color:#16232f!important}' in light_css
), "Main light theme must recolour select options"
assert "STREAMFORGE_LIGHT_SELECT_POPUP_V1354" in light_css, "light select popup marker missing"
assert "STREAMFORGE_LIGHT_SELECTOR_MATCH_V1356" in light_css, "light selector fix marker missing"
assert (
    ':root[data-theme="light"] body select:not([multiple]):not(#streamforge-dropdown-never),:root[data-theme="light"] body select[multiple]:not(#streamforge-dropdown-never){color-scheme:light!important}' in node
), "Node light theme must force light color-scheme on selects"
assert "STREAMFORGE_NODE_LIGHT_SELECT_POPUP_V1354" in node, "Node light select popup marker missing"
assert "STREAMFORGE_NODE_LIGHT_SELECTOR_MATCH_V1356" in node, "Node light selector fix marker missing"
# v13.56: ":root[data-theme=light] html ..." can never match (html is not inside
# :root), so no light rule may use that impossible prefix.
assert ':root[data-theme="light"] html' not in light_css, "impossible :root html light selector in theme-light.css"
assert ':root[data-theme="light"] html' not in node, "impossible :root html light selector in the Node panel CSS"
# --- v13.55: route decorators must bind to their real handler. v13.53-v13.54
# --- shipped the watch-device-CSS helper wedged between the watch route
# --- decorator and node_web_player_watch, so every channel click returned the
# --- CSS text instead of the player page.
_app_lines = node.splitlines()
for _i, _line in enumerate(_app_lines):
    _stripped = _line.strip()
    if _stripped.startswith("@app.") or _stripped.startswith("@router."):
        _j = _i + 1
        while _j < len(_app_lines) and (
            not _app_lines[_j].strip() or _app_lines[_j].strip().startswith("#")
        ):
            _j += 1
        _next = _app_lines[_j].strip() if _j < len(_app_lines) else ""
        if _next and not (
            _next.startswith("def ") or _next.startswith("async def ") or _next.startswith("@")
        ):
            raise AssertionError(
                f"route decorator at source line {_i + 1} is not bound to a handler: {_stripped[:70]}"
            )
assert node.index('@app.get("/web-player/watch/{channel_key}", response_class=HTMLResponse)') < node.index(
    "def node_web_player_watch(channel_key: str, request: Request):"
) and node.index("def _node_watch_device_theme_css") < node.index(
    '@app.get("/web-player/watch/{channel_key}", response_class=HTMLResponse)'
), "the watch route decorator must bind node_web_player_watch"

# --- Page-level <style> blocks load after style.css, so the light overrides must
# --- be a late stylesheet linked after {% block content %}.
assert "STREAMFORGE_LIGHT_THEME_LATE_STYLESHEET_V1348" in base, "late light stylesheet link is missing"
assert base.index("STREAMFORGE_LIGHT_THEME_LATE_STYLESHEET_V1348") > base.index(
    "{% block content %}"
), "late light stylesheet must load after the page content block"
assert "theme-light.css" in base, "late light stylesheet is not linked"

# --- Every rule is scoped: no light rule may leak into the dark theme.
for line in light_css.splitlines():
    line = line.strip()
    if not line or line.startswith("/*") or line.startswith("*") or line.startswith("}"):
        continue
    assert line.startswith(':root[data-theme="light"] '), f"unscoped light rule: {line[:80]}"

required = (
    ".metrics-legend [data-legend-value]",
    ".main-update-release-box",
    ".notification-summary-grid article",
    ".live-refresh-control",
    ".sf-vault-dialog",
    ".backup-edit-modal-dialog",
    ".log-filter-bar .log-search-field input",
    ".notification-template-help",
    ".google-oauth-credentials>.google-oauth-field input",
    ".geoip-status-grid>div",
    ".user-notification-settings",
    ".login-card input[name=\"username\"]",
)
for selector in required:
    assert selector in light_css, f"light override missing for {selector}"

# --- Coverage contract: every admin template that hardcodes a dark panel surface
# --- or a near-white text colour must appear in the late light stylesheet.
skip_files = {"player.html", "stream_error.html", "web_player.html"}
def luminance(hx):
    h = hx.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None
    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

uncovered = {}
for path in sorted((ROOT / "app/templates").glob("*.html")):
    if path.name in skip_files:
        continue
    text = path.read_text(encoding="utf-8")
    for block in re.findall(r"<style[^>]*>(.*?)</style>", text, re.S | re.I):
        block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
            selector = " ".join(match.group(1).split())
            if not selector or selector.startswith("@") or "gradient(" in match.group(2):
                continue
            decls = match.group(2)
            dark_bg = light_text = False
            for prop, value in re.findall(r"(?:^|;)\s*([a-z-]+)\s*:\s*([^;]+)", decls):
                if prop.split("-")[0] in ("background",) and "var(" not in value:
                    dark_bg = any((luminance(h) or 1) < 0.12 for h in re.findall(r"#[0-9a-fA-F]{3,6}\b", value))
                if prop == "color" and "var(" not in value:
                    light_text = any((luminance(h) or 0) > 0.78 for h in re.findall(r"#[0-9a-fA-F]{3,6}\b", value))
            if not (dark_bg or light_text):
                continue
            parts = [p.strip() for p in selector.split(",") if p.strip()]
            for part in parts:
                if part not in light_css:
                    uncovered.setdefault(path.name, set()).add(part)
assert not uncovered, f"light override coverage gaps: {dict((k, sorted(v)[:4]) for k, v in uncovered.items())}"

# --- Main dashboard canvas charts follow the active theme.
assert "dataset.theme==='light'" in dashboard_js, "Main dashboard charts are not theme-aware"
assert "streamforge:themechange" in app_js and "streamforge:themechange" in dashboard_js, (
    "theme changes must repaint the dashboard charts"
)

# --- Web Player: the light block must win over saved appearance !important rules,
# --- and the toggle script must run on the login screen as well.
assert webplayer.index("WEBPLAYER_DEVICE_THEME_LIGHT_V1344") > webplayer.index(
    "STREAMFORGE_MAIN_WEBPLAYER_THEME_CORE_V2229"
), "Web Player light block must load after saved appearance styles"
assert webplayer.index("DEVICE_LOCAL_THEME_TOGGLE_V1344") > webplayer.index(
    'style="position:fixed;right:10px'
), "Web Player toggle script must be outside the logged-in-only branch"
assert "querySelectorAll('[data-theme-toggle]')" in webplayer, (
    "Web Player must wire every theme toggle (login and header)"
)

print("PASS: device-local theme contract")
