from __future__ import annotations

import json
from collections import OrderedDict
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import AdminUser, Role

# Ordered groups are used by the role editor and provide the complete supported
# permission catalogue. Permission keys are intentionally stable because they
# are persisted in the database.
PERMISSION_GROUPS: "OrderedDict[str, list[tuple[str, str, str]]]" = OrderedDict(
    [
        (
            "Dashboard",
            [
                ("dashboard.view", "View dashboard", "Open the overview page and see channel/user totals."),
                ("system_metrics.view", "View server metrics", "See live CPU, memory, network and uptime data."),
            ],
        ),
        (
            "Main / Local Server",
            [
                ("main_access.view", "View Main/Local access", "See Main Panel/API, Playlist/App public URLs, aliases, access paths and playback IP/ASN rules."),
                ("main_access.edit", "Edit Main/Local access", "Change Main Panel/API, Playlist/App public URLs, aliases, access paths and playback IP/ASN rules."),
                ("settings.view", "View Main settings", "Open Main Server branding, metrics, viewer-session and panel settings."),
                ("settings.edit", "Edit Main settings", "Change Main Server branding, metrics, viewer-session and panel settings."),
                ("backups.view", "View backups", "Open backup settings, destinations and archive lists."),
                ("backups.add", "Add backup destinations", "Add Google Drive, local, mounted, SMB or SSH/SFTP backup targets."),
                ("backups.edit", "Edit backup destinations", "Change backup credentials, schedules, rotation and destination settings."),
                ("backups.run", "Run backups", "Run Main or destination backups on demand."),
                ("backups.download", "Download backups", "Download available backup archives."),
                ("backups.restore", "Restore backups", "Restore the Main system from a backup archive."),
                ("backups.delete", "Remove backups", "Delete backup destinations or stored backup archives."),
                ("main_system.service_restart", "Restart Main service", "Restart the StreamForge Main service."),
                ("main_system.reboot", "Reboot Main Server", "Reboot the complete Main Server."),
            ],
        ),
        (
            "Nodes",
            [
                ("nodes.view", "View nodes", "Open the encoding-node list and see online state and resource metrics."),
                ("nodes.create", "Add nodes", "Add new remote encoding nodes manually."),
                ("nodes.edit", "Edit nodes", "Edit, enable or disable remote encoding nodes and their access/Web Player settings."),
                ("nodes.delete", "Remove nodes", "Delete remote encoding nodes."),
                ("nodes.test", "Test node connections", "Run authenticated health and capability checks against a node agent."),
                ("nodes.install", "Install nodes over SSH", "Use one-time SSH credentials to install a remote node agent."),
                ("nodes.update", "Update remote nodes", "Push the current Node Agent from the Main Panel or reinstall it over SSH."),
                ("nodes.service_restart", "Restart Node service", "Restart the StreamForge Node service on a Remote Node."),
                ("nodes.reboot", "Reboot Node server", "Reboot a complete Remote Node server."),
                ("channels.assign_node", "Assign channels to nodes", "Choose the local or remote encoding node in channel Add/Edit and import."),
            ],
        ),
        (
            "Channels",
            [
                ("channels.view", "View channels", "Open the channel list and see stream status."),
                ("channels.info", "View stream information", "Open Info and run input/output ffprobe checks."),
                ("channels.playback", "Open direct HTTP playback", "See and open the playback-ready HTTP HLS action."),
                ("channels.create", "Create channels", "Add new encoder channels."),
                ("channels.edit", "Edit channels", "Change input, codecs, output, category, logo and auto-restart."),
                ("channels.start", "Start channels", "Start one or multiple channels."),
                ("channels.stop", "Stop channels", "Stop one or multiple channels."),
                ("channels.restart", "Restart channels", "Restart one or multiple live channels."),
                ("channels.delete", "Delete channels", "Permanently delete channels and their local logo when unused."),
            ],
        ),
        (
            "Categories & Import",
            [
                ("categories.view", "View categories", "Open category lists and filtered channels."),
                ("categories.create", "Add categories", "Create new channel categories."),
                ("categories.edit", "Edit categories", "Rename channel categories."),
                ("categories.reorder", "Reorder categories", "Change category order and channel order inside a category."),
                ("categories.delete", "Remove categories", "Delete channel categories."),
                ("imports.view", "View stream importer", "Open the M3U/CSV import page."),
                ("imports.execute", "Import streams", "Upload, paste or fetch playlists and create/update channels."),
            ],
        ),
        (
            "Streaming Users & Playlists",
            [
                ("stream_users.view", "View streaming users", "See subscriber accounts and channel assignments."),
                ("stream_users.playlist", "View and copy playlist links", "See M3U/player URLs and open the browser player."),
                ("stream_users.create", "Create streaming users", "Create token-based playlist accounts."),
                ("stream_users.edit", "Edit streaming users", "Change expiry, limits, notes and channel access."),
                ("stream_users.token", "Regenerate playlist tokens", "Invalidate an old token and issue a new one."),
                ("stream_users.delete", "Delete streaming users", "Permanently remove playlist accounts."),
            ],
        ),
        (
            "Logs",
            [
                ("logs.view", "View logs", "Open combined system, channel and remote-node logs."),
                ("logs.clear", "Clear logs", "Clear local logs, channel error logs or a selected Remote Node log buffer."),
            ],
        ),
        (
            "Panel Administration",
            [
                ("panel_users.view", "View panel users", "See administrator/operator login accounts. Non-Super Admin roles cannot see Super Admin accounts."),
                ("panel_users.create", "Add panel users", "Create administrator/operator login accounts. Super Admin role assignment remains Super Admin-only."),
                ("panel_users.edit", "Edit panel users", "Edit username, password, status, access locations and role for visible non-Super Admin accounts."),
                ("panel_users.delete", "Remove panel users", "Delete visible non-Super Admin panel accounts."),
                ("roles.view", "View roles", "See role definitions at or below your own permission level."),
                ("roles.create", "Add roles", "Create custom roles using only permissions you already have."),
                ("roles.edit", "Edit roles", "Edit visible custom roles at or below your own permission level."),
                ("roles.delete", "Remove roles", "Delete visible unused custom roles at or below your own permission level, except your own assigned role."),
            ],
        ),
    ]
)

ALL_PERMISSION_KEYS: tuple[str, ...] = tuple(
    key
    for entries in PERMISSION_GROUPS.values()
    for key, _label, _description in entries
)
ALL_PERMISSION_SET = frozenset(ALL_PERMISSION_KEYS)
SUPERUSER_PERMISSION = "*"

PERMISSION_DEPENDENCIES: dict[str, set[str]] = {
    "system_metrics.view": {"dashboard.view"},
    "main_access.view": {"nodes.view"},
    "main_access.edit": {"main_access.view", "nodes.view"},
    "settings.edit": {"settings.view"},
    "backups.add": {"backups.view"},
    "backups.edit": {"backups.view"},
    "backups.run": {"backups.view"},
    "backups.download": {"backups.view"},
    "backups.restore": {"backups.view"},
    "backups.delete": {"backups.view"},
    "main_system.service_restart": {"nodes.view"},
    "main_system.reboot": {"nodes.view"},
    "nodes.create": {"nodes.view"},
    "nodes.edit": {"nodes.view"},
    "nodes.delete": {"nodes.view"},
    "nodes.service_restart": {"nodes.view"},
    "nodes.reboot": {"nodes.view"},
    "nodes.test": {"nodes.view"},
    "nodes.install": {"nodes.view"},
    "nodes.update": {"nodes.view"},
    "channels.assign_node": {"channels.view", "nodes.view"},
    "channels.info": {"channels.view"},
    "channels.playback": {"channels.view"},
    "channels.create": {"channels.view"},
    "channels.edit": {"channels.view"},
    "channels.start": {"channels.view"},
    "channels.stop": {"channels.view"},
    "channels.restart": {"channels.view"},
    "channels.delete": {"channels.view"},
    "imports.execute": {"imports.view"},
    "stream_users.playlist": {"stream_users.view"},
    "stream_users.create": {"stream_users.view"},
    "stream_users.edit": {"stream_users.view"},
    "stream_users.token": {"stream_users.view"},
    "stream_users.delete": {"stream_users.view"},
    "panel_users.create": {"panel_users.view"},
    "panel_users.edit": {"panel_users.view"},
    "panel_users.delete": {"panel_users.view"},
    "roles.create": {"roles.view"},
    "roles.edit": {"roles.view"},
    "roles.delete": {"roles.view"},
}


def normalize_permissions(values: Iterable[str]) -> list[str]:
    """Return a sorted, unique and supported permission list.

    The wildcard is reserved for the built-in Super Admin role. Custom roles
    submitted from the UI can only contain published permission keys.
    """
    cleaned = {str(value).strip() for value in values if str(value).strip() in ALL_PERMISSION_SET}
    changed = True
    while changed:
        changed = False
        for permission in tuple(cleaned):
            for dependency in PERMISSION_DEPENDENCIES.get(permission, set()):
                if dependency not in cleaned:
                    cleaned.add(dependency)
                    changed = True
    return sorted(cleaned)


def encode_permissions(values: Iterable[str], *, allow_wildcard: bool = False) -> str:
    raw = {str(value).strip() for value in values}
    if allow_wildcard and SUPERUSER_PERMISSION in raw:
        return json.dumps([SUPERUSER_PERMISSION], separators=(",", ":"))
    return json.dumps(normalize_permissions(raw), separators=(",", ":"))


def decode_permissions(value: str | None) -> set[str]:
    if not value:
        return set()
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(parsed, list):
        return set()
    result = {str(item) for item in parsed if isinstance(item, str)}
    if SUPERUSER_PERMISSION in result:
        return {SUPERUSER_PERMISSION}
    return result & ALL_PERMISSION_SET


def role_permission_set(role: "Role | None") -> set[str]:
    if role is None:
        return set()
    return decode_permissions(role.permissions)


def has_permission(admin: "AdminUser | None", permission: str) -> bool:
    if admin is None or not admin.is_active:
        return False
    permissions = role_permission_set(admin.role)
    return SUPERUSER_PERMISSION in permissions or permission in permissions


def has_any_permission(admin: "AdminUser | None", permissions: Iterable[str]) -> bool:
    return any(has_permission(admin, key) for key in permissions)
