"""v13.88 active input link label contract (render-context fix).

The Main channels table shows the active input link number next to the local
server name ("Main Server #1"). The number comes from active_input_index
(0 -> #1) and stays in sync on live failover.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

template = (ROOT / "app/templates/channels.html").read_text(encoding="utf-8")
assert "STREAMFORGE_MAIN_ACTIVE_LINK_LABEL_V1387" in template
assert "'Main Server #' ~ (((channel.active_input_index or 0) | int) + 1)" in template
assert 'active_input_index + 1' not in template  # must not rely on JSON-only context key
# cluster summary, cluster menu entry, single link and no-node fallback
assert template.count("active_link_label if") == 3
assert template.count("<b>{{ active_link_label }}</b>") == 1

app_js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
assert app_js.count("STREAMFORGE_MAIN_ACTIVE_LINK_LABEL_V1387") == 2
assert "Number(channel.active_input_index) || 0) + 1" in app_js
assert "label.textContent.replace(/#\\d+$/, '') + ' #' + activeLinkNo" in app_js
assert "textContent?.match(/#\\d+$/)" in app_js

main_py = (ROOT / "app/main.py").read_text(encoding="utf-8")
assert '"active_input_index": int(runtime.get("active_input_index", channel.active_input_index or 0) or 0)' in main_py

print("PASS: v13.88 active link label contract")
