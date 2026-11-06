#!/usr/bin/env python3
"""v13.16 WebPlayer duplicate stream_id reference contract.

Production evidence (c3box.tv, v13.15): two channels ("t" = sf-144-t and
"Sony Ten Cricket" = sf-76-sony-ten-cricket) shared stream_id 185. The web
player media URL repeated the numeric stream_id and the later duplicate
overwrote the session grant's channel map entry, so "t" resolved to the other
channel and failed node media auth with 403 "Playback path mismatch" while the
same channel still played through /node-play/ key-based resolution.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "VERSION").read_text(encoding="utf-8").strip() != "13.16":
    print("SKIP: v13.16 webplayer stream ref contract")
    raise SystemExit(0)

app = (ROOT / "node_agent/app.py").read_text(encoding="utf-8")

for marker in (
    "STREAMFORGE_NODE_WEBPLAYER_UNIQUE_STREAM_REF_V1316",
    "STREAMFORGE_NODE_WEBPLAYER_KEY_REF_V1316",
    "STREAMFORGE_NODE_PLAYBACK_RESOLVE_KEY_FIRST_V1316",
):
    assert marker in app, marker

watch = app.split('def node_web_player_watch', 1)[1]
watch = watch[:watch.find('\ndef ', 1) if '\ndef ' in watch else len(watch)]

# Grant aliases must never overwrite an earlier claim on duplicate stream_id.
assert 'authorized_channel_map.setdefault(authorized_channel.key, authorized_channel.key)' in app
assert 'authorized_channel_map.setdefault(stream_ref, authorized_channel.key)' in app
assert 'authorized_channel_map[str(_node_stream_id(authorized_channel))] = authorized_channel.key' not in app

# The media URL carries the unambiguous channel key as the channel_ref.
assert 'urllib.parse.quote(channel.key, safe=\'\')}/' in app
assert 'urllib.parse.quote(item.key, safe=\'\')}/' in app
assert 'urllib.parse.quote(str(item_stream_id), safe=\'\')/' not in app
assert 'urllib.parse.quote(str(stream_id), safe=\'\')/' not in app

# The watch-page cards must not build per-card stream_id URLs anymore.
assert 'item_stream_id' not in watch

# Fallback resolution prefers an exact key match before any stream_id alias.
resolve = app.split('def resolve_node_playback_key', 1)[1]
assert 'catalog_channels = _effective_user_channels(user)' in resolve
assert 'item.key == str(channel_ref)' in resolve
assert 'str(_node_stream_id(item)) == str(channel_ref)' in resolve

# The channel key character class already matches the Nginx sf_channel_ref
# capture group, so key-based URLs must still route through Nginx media auth.
assert 're.compile(r"^[A-Za-z0-9_-]{1,96}$")' in app


def build_map(channels: list[tuple[str, int]]) -> dict[str, str]:
    """Mirror of the watch-page grant map builder (first claim wins)."""
    authorized_channel_map: dict[str, str] = {}
    for key, stream_id in channels:
        authorized_channel_map.setdefault(key, key)
        authorized_channel_map.setdefault(str(stream_id), key)
    return authorized_channel_map


def resolve(map: dict[str, str], channel_ref: str, catalog: list[tuple[str, int]]) -> str:
    """Mirror of resolve_node_playback_key's wildcard grant branch."""
    mapped_key = str(map.get(channel_ref) or "")
    if mapped_key:
        return mapped_key
    channel = next((key for key, _sid in catalog if key == channel_ref), None)
    if channel is None:
        channel = next((key for key, sid in catalog if str(sid) == channel_ref), None)
    return channel or ""


catalog = [("sf-75-star-sports-2", 184), ("sf-144-t", 185), ("sf-76-sony-ten-cricket", 185)]
mapping = build_map(catalog)

# Duplicate stream_id 185 must still grant "t" and "Sony Ten Cricket" their own
# keys, and both key-based URLs must resolve to themselves.
assert mapping["sf-144-t"] == "sf-144-t"
assert mapping["sf-76-sony-ten-cricket"] == "sf-76-sony-ten-cricket"
assert resolve(mapping, "sf-144-t", catalog) == "sf-144-t"
assert resolve(mapping, "sf-76-sony-ten-cricket", catalog) == "sf-76-sony-ten-cricket"
# A patrol-client stream_id URL resolves to the first claimant instead of the
# last duplicate, and cannot silently serve another channel's key.
assert resolve(mapping, "185", catalog) == "sf-144-t"

# Old builder for contrast: the last duplicate used to steal 185 and break "t".
old_map: dict[str, str] = {}
for key, sid in catalog:
    old_map[key] = key
    old_map[str(sid)] = key
assert old_map["185"] == "sf-76-sony-ten-cricket"  # the production 403 cause

print("PASS: v13.16 web players resolve duplicate stream ids by unique channel key")
