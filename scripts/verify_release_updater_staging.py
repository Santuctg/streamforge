#!/usr/bin/env python3
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
version = (root / "VERSION").read_text(encoding="utf-8").strip()
compact = version.replace(".", "")
updater = root / "scripts" / f"force_update_v{compact}.sh"

errors = []
cleanup_markers = [
    "find \"$STAGE/scripts\" -maxdepth 1 -type f -name 'force_update_v*.sh'",
    "! -name 'force_update_v1236.sh' -delete",
]


def has_full_cleanup(text: str) -> bool:
    return all(marker in text for marker in cleanup_markers)


if not updater.is_file():
    errors.append(f"missing current updater: {updater.name}")
else:
    text = updater.read_text(encoding="utf-8")

    # STREAMFORGE_RELEASE_SOURCE_NO_PRESTAGE_BYTECODE_V1252:
    stage_copy_pos = text.find('cp -a "$SOURCE_DIR/." "$STAGE/"')
    source_pycompile = re.search(r'python3\s+-m\s+py_compile\s+"?\$SOURCE_DIR/', text)
    if source_pycompile and (stage_copy_pos < 0 or source_pycompile.start() < stage_copy_pos):
        errors.append(
            f"{updater.name} runs py_compile in SOURCE_DIR before staging and can create __pycache__"
        )

    # STREAMFORGE_RELEASE_BOUNDED_PANEL_VAULT_V1263:
    # Main API/schema/UI changed; bundled Node runtime bytes must remain identical.
    bounded_panel_vault_v1263 = "STREAMFORGE_V1263_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT" in text

    # STREAMFORGE_RELEASE_BOUNDED_PANEL_PASSWORD_NAV_V1262:
    # v12.62 is limited to the Panel user templates plus version metadata.
    bounded_password_nav_v1262 = "STREAMFORGE_V1262_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT" in text

    # STREAMFORGE_RELEASE_BOUNDED_PANEL_PASSWORD_UI_V1261:
    # v12.61 changes only two Main Panel User templates and aligned metadata.
    bounded_password_ui_v1261 = "STREAMFORGE_V1261_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT" in text

    # STREAMFORGE_RELEASE_BOUNDED_NODE_INLINE_LOG_V1260:
    # v12.60 changes only the Main Node Updates template plus VERSION metadata.
    # Node runtime bytes must match the installed proven v12.59 payload.
    bounded_inline_log_v1260 = "STREAMFORGE_V1260_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT" in text

    # STREAMFORGE_RELEASE_BOUNDED_NODE_UPDATE_UI_V1259:
    # v12.59 changes only the Main Node Updates template plus VERSION metadata.
    # Node runtime bytes must match the installed proven v12.58 payload.
    bounded_ui_v1259 = "STREAMFORGE_V1259_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT" in text

    # STREAMFORGE_RELEASE_BOUNDED_NODE_UPDATE_CENTER_V1258:
    bounded_center_v1258 = "STREAMFORGE_V1258_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT" in text

    # STREAMFORGE_RELEASE_BOUNDED_NODE_BULK_UPDATE_V1257:
    bounded_bulk_v1257 = "STREAMFORGE_V1257_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT" in text

    # STREAMFORGE_RELEASE_BOUNDED_PLAYER_UPDATE_V1256:
    bounded_player_v1256 = "STREAMFORGE_V1256_BOUNDED_PLAYER_UPDATE" in text

    # STREAMFORGE_RELEASE_BOUNDED_VERSION_ALIGNMENT_V1255:
    alignment_v1255 = "STREAMFORGE_VERSION_ALIGNMENT_V1255" in text

    # STREAMFORGE_RELEASE_BOUNDED_MAIN_HOTFIX_V1254:
    bounded_marker = next(
        (
            marker
            for marker in (
                "STREAMFORGE_MAIN_ONLY_HOTFIX_V1253",
                "STREAMFORGE_MAIN_ONLY_HOTFIX_V1254",
            )
            if marker in text
        ),
        None,
    )

    if bounded_panel_vault_v1263:
        if 'cp -a "$SOURCE_DIR/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.63 vault updater unexpectedly copies full release tree")
        for marker in (
            'TARGET_VERSION="12.63"',
            'NODE_TARGET_VERSION="12.63"',
            '12.61|12.62|12.63',
            'STREAMFORGE_PANEL_USER_SAVED_PASSWORD_V1263',
            'STREAMFORGE_V1263_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT',
            'STREAMFORGE_NODE_UPDATE_UI_SPACING_V1259',
            'STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258',
            'STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256',
            'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251',
            'verify_panel_saved_password_v1263.py',
            'app/main.py',
            'app/models.py',
            'app/db.py',
            'app/panel_password_vault.py',
            'app/templates/admin_users.html',
            'app/templates/admin_user_form.html',
            'Node runtime payload differs for:',
            'atomic_copy',
            'compile(path.read_text(encoding="utf-8"), str(path), "exec")',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.63 bounded encrypted vault contract missing {marker}")
    elif bounded_password_nav_v1262:
        if 'cp -a "$SOURCE_DIR/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.62 bounded navigation fix copies full release tree")
        for marker in (
            'TARGET_VERSION="12.62"',
            'NODE_TARGET_VERSION="12.62"',
            '12.61|12.62',
            'STREAMFORGE_PANEL_USER_PASSWORD_LINK_FIX_V1262',
            'STREAMFORGE_V1262_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT',
            'STREAMFORGE_NODE_UPDATE_UI_SPACING_V1259',
            'STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258',
            'STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256',
            'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251',
            'verify_password_navigation_v1262.py',
            'app/templates/admin_users.html',
            'app/templates/admin_user_form.html',
            'Node runtime payload differs for:',
            'atomic_copy',
            'compile(path.read_text(encoding="utf-8"), str(path), "exec")',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.62 bounded navigation contract missing {marker}")
    elif bounded_password_ui_v1261:
        if 'cp -a "$SOURCE_DIR/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.61 bounded password UI unexpectedly copies full release tree")
        for marker in (
            'TARGET_VERSION="12.61"',
            'NODE_TARGET_VERSION="12.61"',
            '12.60|12.61',
            'STREAMFORGE_PANEL_USER_PASSWORD_VISIBILITY_V1261',
            'STREAMFORGE_V1261_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT',
            'STREAMFORGE_NODE_UPDATE_UI_SPACING_V1259',
            'STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258',
            'STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256',
            'STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251',
            'verify_panel_user_password_visibility_v1261.py',
            'app/templates/admin_users.html',
            'app/templates/admin_user_form.html',
            'Node runtime payload differs for:',
            'atomic_copy',
            'compile(path.read_text(encoding="utf-8"), str(path), "exec")',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.61 bounded password UI contract missing {marker}")
    elif bounded_inline_log_v1260:
        if 'cp -a "$SOURCE_DIR/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.60 bounded inline-log release unexpectedly copies the full release tree")
        for marker in (
            'TARGET_VERSION="12.60"',
            'NODE_TARGET_VERSION="12.60"',
            "STREAMFORGE_NODE_INLINE_UPDATE_LOG_V1260",
            "STREAMFORGE_V1260_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT",
            "STREAMFORGE_NODE_UPDATE_UI_SPACING_V1259",
            "STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258",
            "STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257",
            "STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256",
            "STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251",
            "verify_node_inline_update_log_v1260.py",
            "Node runtime payload differs for:",
            "atomic_copy",
            "compile(path.read_text(encoding=\"utf-8\"), str(path), \"exec\")",
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.60 bounded inline-log contract missing {marker}")
    elif bounded_ui_v1259:
        if 'cp -a "$SOURCE_DIR/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.59 bounded UI release unexpectedly copies the full release tree")
        for marker in (
            'TARGET_VERSION="12.59"',
            'NODE_TARGET_VERSION="12.59"',
            "STREAMFORGE_NODE_UPDATE_UI_SPACING_V1259",
            "STREAMFORGE_V1259_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT",
            "STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258",
            "STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257",
            "STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256",
            "STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251",
            "verify_node_update_ui_spacing_v1259.py",
            "Node runtime payload differs for:",
            "atomic_copy",
            "compile(path.read_text(encoding=\"utf-8\"), str(path), \"exec\")",
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.59 bounded UI contract missing {marker}")
    elif bounded_center_v1258:
        if 'cp -a "$SOURCE_DIR/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.58 bounded Node-update release unexpectedly copies the full release tree")
        for marker in (
            'TARGET_VERSION="12.58"',
            'NODE_TARGET_VERSION="12.58"',
            "STREAMFORGE_NODE_UPDATE_CENTER_CLEANUP_V1258",
            "STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257",
            "STREAMFORGE_V1258_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT",
            "STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256",
            "STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251",
            "verify_node_update_center_cleanup_v1258.py",
            "Node runtime payload differs for:",
            "atomic_copy",
            "compile(path.read_text(encoding=\"utf-8\"), str(path), \"exec\")",
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.58 bounded Node-update contract missing {marker}")
    elif bounded_bulk_v1257:
        if 'cp -a "$SOURCE_DIR/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.57 bounded bulk-update release unexpectedly copies the full release tree")
        for marker in (
            'TARGET_VERSION="12.57"',
            'NODE_TARGET_VERSION="12.57"',
            "STREAMFORGE_NODE_BULK_UPDATE_CENTER_V1257",
            "STREAMFORGE_V1257_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT",
            "STREAMFORGE_V1257_ROLLBACK_GUARD",
            "STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256",
            "STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251",
            "verify_node_bulk_update_center_v1257.py",
            "Node runtime payload differs for:",
            "atomic_copy",
            "compile(path.read_text(encoding=\"utf-8\"), str(path), \"exec\")",
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.57 bounded bulk-update contract missing {marker}")
    elif bounded_player_v1256:
        if 'cp -a "$SOURCE_DIR/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.56 bounded player update unexpectedly copies the full release tree")
        for marker in (
            'TARGET_VERSION="12.56"',
            'NODE_TARGET_VERSION="12.56"',
            "STREAMFORGE_WEBPLAYER_HLSJS_FIRST_V1256",
            "STREAMFORGE_MAIN_WAITING_RECOVERY_V1253",
            "STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251",
            "STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254",
            "verify_webplayer_hlsjs_first_v1256.py",
            "atomic_copy",
            "compile(path.read_text(encoding=\"utf-8\"), str(path), \"exec\")",
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.56 bounded player contract missing {marker}")
    elif alignment_v1255:
        if 'cp -a "$SOURCE_DIR/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.55 alignment unexpectedly copies the full release tree")
        for marker in (
            'TARGET_VERSION="12.55"',
            'NODE_TARGET_VERSION="12.55"',
            "STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254",
            "STREAMFORGE_MAIN_WAITING_RECOVERY_V1253",
            "STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251",
            "installed Node payload differs from alignment package",
            "sync_main_static_cache.py",
            "STREAMFORGE_MAIN_STATIC_CACHE_ROOT",
            "compile(path.read_text(encoding=\"utf-8\"), str(path), \"exec\")",
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.55 bounded alignment contract missing {marker}")
    elif bounded_marker:
        if 'cp -a "$SOURCE_DIR/." "$STAGE/"' in text:
            errors.append(f"{updater.name} declares Main-only scope but copies the full release tree")
        expected_no_bytecode = (
            "STREAMFORGE_V1254_NO_SOURCE_BYTECODE"
            if bounded_marker.endswith("V1254")
            else "STREAMFORGE_V1253_NO_SOURCE_BYTECODE"
        )
        if expected_no_bytecode not in text:
            errors.append(f"{updater.name} Main-only scope lacks no-bytecode verification marker")
        if 'NODE_TARGET_VERSION="12.52"' not in text:
            errors.append(f"{updater.name} Main-only scope does not pin the unchanged Node Agent version")
        if "Remote Node Agent is unchanged" not in text:
            errors.append(f"{updater.name} Main-only scope does not explicitly preserve Remote Node Agent")
        if bounded_marker.endswith("V1254"):
            for marker in (
                "STREAMFORGE_MAIN_STATIC_CACHE_SYNC_V1254",
                "sync_main_static_cache.py",
                "STREAMFORGE_MAIN_STATIC_CACHE_ROOT",
            ):
                if marker not in text:
                    errors.append(f"{updater.name} v12.54 static-cache contract missing {marker}")
    elif not has_full_cleanup(text):
        # STREAMFORGE_RELEASE_STAGING_DELEGATED_CLEANUP_V1251:
        bridge_match = re.search(
            r'BASE_WRAPPER="\$SOURCE_DIR/scripts/(force_update_v\d+\.sh)"',
            text,
        )
        if not bridge_match:
            for marker in cleanup_markers:
                if marker not in text:
                    errors.append(f"{updater.name} missing staging cleanup marker: {marker}")
        else:
            bridge_name = bridge_match.group(1)
            bridge = root / "scripts" / bridge_name
            bridge_text = bridge.read_text(encoding="utf-8") if bridge.is_file() else ""
            if not bridge_text:
                errors.append(f"{updater.name} delegates staging cleanup to missing {bridge_name}")
            elif not has_full_cleanup(bridge_text):
                errors.append(f"delegated updater {bridge_name} lacks wildcard staging cleanup")
            if f'bash "$STAGE/scripts/{bridge_name}" "$@"' not in text:
                errors.append(f"{updater.name} does not execute staged delegated updater {bridge_name}")

    if re.search(r'rm -f \"\$STAGE/scripts/force_update_v\d+\.sh\"', text):
        errors.append(f"{updater.name} uses single-file cleanup and can leave stale force updaters")

if errors:
    raise SystemExit("\n".join(f"FAIL: {e}" for e in errors))
print("Force-updater staging regression guard: PASS")
