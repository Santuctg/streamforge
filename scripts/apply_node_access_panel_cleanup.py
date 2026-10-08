#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "node_agent" / "app.py"
text = path.read_text(encoding="utf-8")
original = text

replacements = {
    '<div class="panel-head"><div><h2>Node access settings</h2><p>Configure the public Panel/API and Playlist/App aliases synchronized with the Main Server.</p></div></div>':
        '<div class="panel-head"><div><h2>Node access settings</h2><p>Configure Node operational, monitoring and security settings.</p></div></div>',
    '<label class="wide">Node Panel/API access URLs<textarea name="panel_urls" required>{panel_urls_value}</textarea><small class="field-help">One full URL per line. Without an explicit public port, HTTP uses 80 and HTTPS uses 443; the internal Node listener is separate. The saved scheme is canonical and opposite-protocol requests redirect automatically. Standalone Nodes use delegated DNS-01 managed TLS; even an HTTP-canonical hostname keeps a certificate on 443 so HTTPS mistakes can redirect to HTTP without a certificate warning.</small></label>\n': '',
    '<label class="wide">Playlist/App access URLs<textarea name="stream_urls" required>{stream_urls_value}</textarea><small class="field-help">One full URL per line. Public HTTP defaults to 80 and HTTPS to 443 when no port is written; backend listener ports remain separate. The saved scheme is canonical and opposite-protocol requests redirect automatically. Delegated DNS-01 certificates are independent of whether the hostname resolves to a public or private/local IP.</small></label>\n': '',
    "            panel_urls=str(f.get('panel_urls') or '').replace('\\r','').split('\\n'),":
        "            # STREAMFORGE_NODE_ACCESS_PANEL_PRESERVE_CONFIG_V1240: these values are Main-managed;\n            # the standalone Node Settings page no longer exposes or clears them.\n            panel_urls=list(manager.panel_urls),",
    "            stream_urls=str(f.get('stream_urls') or '').replace('\\r','').split('\\n'),":
        "            stream_urls=list(manager.stream_urls),",
    "            panel_ip_whitelist=str(f.get('panel_ip_whitelist') or ''),":
        "            panel_ip_whitelist=manager.panel_ip_whitelist,",
    "            panel_ip_blacklist=str(f.get('panel_ip_blacklist') or ''),":
        "            panel_ip_blacklist=manager.panel_ip_blacklist,",
    "            panel_asn_whitelist=str(f.get('panel_asn_whitelist') or ''),":
        "            panel_asn_whitelist=manager.panel_asn_whitelist,",
    "            panel_asn_blacklist=str(f.get('panel_asn_blacklist') or ''),":
        "            panel_asn_blacklist=manager.panel_asn_blacklist,",
    "            ip_whitelist=str(f.get('ip_whitelist') or ''), ip_blacklist=str(f.get('ip_blacklist') or ''),":
        "            ip_whitelist=manager.ip_whitelist, ip_blacklist=manager.ip_blacklist,",
    "            asn_whitelist=str(f.get('asn_whitelist') or ''), asn_blacklist=str(f.get('asn_blacklist') or ''),":
        "            asn_whitelist=manager.asn_whitelist, asn_blacklist=manager.asn_blacklist,",
}

for old, new in replacements.items():
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one match, found {count}: {old[:100]}")
    text = text.replace(old, new, 1)

rules_block = '''<div class="wide settings-divider"><h3>Panel access rules</h3><p>Independent browser Panel policy. All configured rules must pass.</p></div>
<div class="wide access-rule-row">
<label>Panel IP whitelist<textarea name="panel_ip_whitelist">{html.escape(manager.panel_ip_whitelist)}</textarea></label>
<label>Panel IP blacklist<textarea name="panel_ip_blacklist">{html.escape(manager.panel_ip_blacklist)}</textarea></label>
<label>Panel ASN whitelist<textarea name="panel_asn_whitelist">{html.escape(manager.panel_asn_whitelist)}</textarea></label>
<label>Panel ASN blacklist<textarea name="panel_asn_blacklist">{html.escape(manager.panel_asn_blacklist)}</textarea></label>
</div>
<div class="wide settings-divider"><h3>Playback & catalog access rules</h3><p>Playlist, Xtream, Web Player catalogue and playback policy. All configured rules must pass.</p></div>
<div class="wide access-rule-row">
<label>Playback IP whitelist<textarea name="ip_whitelist">{html.escape(manager.ip_whitelist)}</textarea></label>
<label>Playback IP blacklist<textarea name="ip_blacklist">{html.escape(manager.ip_blacklist)}</textarea></label>
<label>ASN whitelist<textarea name="asn_whitelist">{html.escape(manager.asn_whitelist)}</textarea></label>
<label>ASN blacklist<textarea name="asn_blacklist">{html.escape(manager.asn_blacklist)}</textarea></label>
</div>
'''

count = text.count(rules_block)
if count != 1:
    raise SystemExit(f"expected one access-rule UI block, found {count}")
text = text.replace(rules_block, '', 1)

if text == original:
    raise SystemExit("no changes made")

path.write_text(text, encoding="utf-8")
print("Node Access Panel cleanup applied")
