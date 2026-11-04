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

    # STREAMFORGE_RELEASE_STATIC_CACHE_V1279: Nginx serves /static/ from
    # /var/cache/streamforge/main-static, so an updater that changes installed
    # app/static files must republish that cache (v12.78 did not).
    static_cache_v1279 = "STREAMFORGE_V1279_STATIC_CACHE_UPDATER" in text
    # STREAMFORGE_RELEASE_NODE_LIVE_CHARTS_V1281:
    # STREAMFORGE_RELEASE_HARDENING_V1282:
    hardening_v1282 = "STREAMFORGE_V1282_HARDENING_UPDATER" in text
    # STREAMFORGE_RELEASE_HARDENING_V1283:
    hardening_v1283 = "STREAMFORGE_V1283_HARDENING_UPDATER" in text
    node_live_charts_v1281 = "STREAMFORGE_V1281_NODE_LIVE_CHARTS_UPDATER" in text
    # STREAMFORGE_RELEASE_LIVE_CHARTS_V1280:
    live_charts_v1280 = "STREAMFORGE_V1280_LIVE_CHARTS_UPDATER" in text
    touches_static = re.search(r'PATCH_FILES=\([^)]*app/static/', text) is not None
    publishes_static = "sync_main_static_cache.py" in text or "main-static" in text
    if touches_static and not publishes_static and "STREAMFORGE_V1278_DASHBOARD_UPDATER" not in text:
        errors.append(f"{updater.name} patches app/static but does not refresh the Nginx static cache")

    # STREAMFORGE_RELEASE_DASHBOARD_V1278:
    dashboard_v1278 = "STREAMFORGE_V1278_DASHBOARD_UPDATER" in text

    # STREAMFORGE_RELEASE_PASSWORD_POLICY_V1277:
    password_policy_v1277 = "STREAMFORGE_V1277_PASSWORD_POLICY_UPDATER" in text

    # STREAMFORGE_RELEASE_SECURITY_HARDENING_V1276:
    security_hardening_v1276 = "STREAMFORGE_V1276_SECURITY_HARDENING_UPDATER" in text

    # STREAMFORGE_RELEASE_SECURE_SESSION_COOKIE_V1275:
    secure_cookie_v1275 = "STREAMFORGE_V1275_SECURE_SESSION_COOKIE_UPDATER" in text

    # STREAMFORGE_RELEASE_INTEGRATED_MAIN_SOURCE_V1274:
    integrated_main_v1274 = "STREAMFORGE_V1274_INTEGRATED_MAIN_SOURCE_UPDATER" in text

    # STREAMFORGE_RELEASE_MAIN_HOTFIXES_V1273:
    bounded_main_hotfixes_v1273 = "STREAMFORGE_V1273_MAIN_HOTFIXES_UPDATER" in text

    # STREAMFORGE_RELEASE_NODE_LOGIN_DARK_RESPONSIVE_V1272:
    bounded_node_login_v1272 = "STREAMFORGE_V1272_NODE_LOGIN_DARK_UI_UPDATER" in text

    # STREAMFORGE_RELEASE_MAIN_LOGIN_AUTOFILL_BLOCKED_IP_CARD_V1271:
    bounded_ui_v1271 = "STREAMFORGE_V1271_LOGIN_AUTOFILL_BLOCKED_IP_CARD" in text

    # STREAMFORGE_RELEASE_VAULT_UI_TIMEOUT_NODE_COPY_V1270:
    bounded_reveal_ui_v1270 = "STREAMFORGE_V1270_REVEAL_TIMEOUT_AND_NODE_COPY" in text

    # STREAMFORGE_RELEASE_VAULT_UI_NODE_NOTICE_V1269:
    bounded_ui_notice_v1269 = "STREAMFORGE_V1269_THEME_AND_NODE_FORM_SCOPED" in text

    # STREAMFORGE_RELEASE_MAIN_PW_MODAL_V1268:
    bounded_main_pw_modal_v1268 = "STREAMFORGE_V1268_MAIN_PASSWORD_MODAL_SCOPED" in text

    # STREAMFORGE_RELEASE_NODE_SELF_PASSWORD_V1267:
    bounded_self_pw_v1267 = "STREAMFORGE_V1267_NODE_SELF_PASSWORD_SESSION_ONLY" in text

    # STREAMFORGE_RELEASE_BOUNDED_MANAGED_VAULT_V1266:
    # Staged Main patches plus a single versioned Node Agent app.py change.
    bounded_managed_v1266 = "STREAMFORGE_V1266_BOUNDED_AUTO_VAULT" in text

    # STREAMFORGE_RELEASE_BOUNDED_PANEL_IP_ORIGIN_V1265:
    # 12.65 adds server provenance with a bounded Main/Node login patch.
    bounded_ip_origin_v1265 = "STREAMFORGE_V1265_MAIN_ONLY_IP_BLOCK_ORIGIN_NODE_PARITY" in text

    # STREAMFORGE_RELEASE_BOUNDED_PANEL_IP_GUARD_V1264:
    # Strictly scoped Main IP lockout hotfix; never stage the whole source tree.
    bounded_ip_v1264 = "STREAMFORGE_V1264_MAIN_ONLY_IP_LOCK_NODE_PARITY" in text

    # STREAMFORGE_RELEASE_BOUNDED_PANEL_CREDENTIAL_VAULT_V1263:
    # Main-only additive model/module/template update with staged patch and rollback.
    bounded_vault_v1263 = "STREAMFORGE_V1263_MAIN_ONLY_VAULT_UPDATE" in text

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

    # STREAMFORGE_RELEASE_NODE_DASHBOARD_EXPIRY_V1289:
    node_expiry_v1289 = "STREAMFORGE_V1289_NODE_DASHBOARD_EXPIRY_UPDATER" in text
    # STREAMFORGE_RELEASE_NODE_EXPIRY_ENFORCEMENT_V1290:
    node_expiry_v1290 = "STREAMFORGE_V1290_EXPIRY_ENFORCEMENT_UPDATER" in text
    # STREAMFORGE_RELEASE_NODE_EXPIRY_BD_WALL_TIME_V1291:
    node_expiry_v1291 = "STREAMFORGE_V1291_BD_WALL_TIME_UPDATER" in text
    # STREAMFORGE_RELEASE_NODE_EXPIRY_PAYMENT_NOTICE_V1292:
    node_expiry_v1292 = "STREAMFORGE_V1292_PAYMENT_NOTICE_UPDATER" in text
    # STREAMFORGE_RELEASE_NODE_EXPIRY_CENTERED_NOTICE_V1293:
    node_expiry_v1293 = "STREAMFORGE_V1293_PAYMENT_NOTICE_UPDATER" in text
    # STREAMFORGE_RELEASE_NODE_WEBPLAYER_EXPIRY_V1294:
    node_expiry_v1294 = "STREAMFORGE_V1294_EXPIRED_WEBPLAYER_UPDATER" in text
    # STREAMFORGE_RELEASE_MAIN_NODE_SUMMARY_V1295:
    node_summary_v1295 = "STREAMFORGE_V1295_DASHBOARD_NODE_SUMMARY_UPDATER" in text
    # STREAMFORGE_RELEASE_WEBPLAYER_COPY_AND_SUMMARY_V1296:
    webplayer_summary_v1296 = "STREAMFORGE_V1296_EXPIRED_BRANDING_UPDATER" in text
    # STREAMFORGE_RELEASE_MAIN_ALL_CLEAR_STATUS_V1297:
    all_clear_v1297 = "STREAMFORGE_V1297_ALL_CLEAR_NODE_STATUS_UPDATER" in text
    # STREAMFORGE_RELEASE_EXPIRY_RESTORE_RETRY_V1298:
    expiry_retry_v1298 = "STREAMFORGE_V1298_RETRY_EXPIRY_RESTORE_UPDATER" in text
    # STREAMFORGE_RELEASE_MAIN_WEB_UPDATE_V1299:
    main_web_update_v1299 = "STREAMFORGE_V1299_MAIN_WEB_UPDATE_UPDATER" in text

    if "STREAMFORGE_V1284_ROLE_MANAGER_UPDATER" in text:
        for marker in (
            '12.83|12.84',
            'OLD_NODE_BLOB="2d9595392e713068cc4c0c8f5243ef55b4f5472b"',
            'scripts/apply_role_manager_fix.py',
            'role patch preflight failed',
            'STREAMFORGE_V1284_TEST_FAIL_AFTER_WRITE',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge',
            'systemctl is-active --quiet streamforge-public',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.84 contract missing {marker}")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.84 must not restart Nginx or the channel supervisor")
        if not (root / "scripts/apply_role_manager_fix.py").is_file():
            errors.append("missing role patcher")
    elif node_expiry_v1289:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.89 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.89 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1289_NODE_DASHBOARD_EXPIRY_UPDATER',
            '12.88|12.89',
            'OLD_NODE_BLOB="2d9595392e713068cc4c0c8f5243ef55b4f5472b"',
            'NEW_NODE_BLOB="6b0b1bcebbf3a25adad2542ed87866f480870777"',
            'STREAMFORGE_V1289_LOCAL_NODE_AGENT_OPTIONAL',
            'STREAMFORGE_NODE_EXPIRY_HEARTBEAT_V1289',
            'STREAMFORGE_V1289_TEST_FAIL_AFTER_WRITE',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.89 contract missing {marker}")
    elif node_expiry_v1290:
        if 'cp -a "$SRC/.\" "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.90 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.90 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1290_EXPIRY_ENFORCEMENT_UPDATER',
            '12.89|12.90',
            'OLD_NODE_BLOB="6b0b1bcebbf3a25adad2542ed87866f480870777"',
            'NEW_NODE_BLOB="f7fcc98e56ad0ea88e804a39d19cc5f2c359fde8"',
            'STREAMFORGE_V1290_LOCAL_NODE_AGENT_OPTIONAL',
            'STREAMFORGE_NODE_EXPIRY_SERVICE_LOCK_V1290',
            'BANGLADESH_TZ',
            '_node_expiry_enforcement_loop',
            'node_expiry_bd_display',
            'STREAMFORGE_V1290_TEST_FAIL_AFTER_WRITE',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.90 contract missing {marker}")
    elif node_expiry_v1291:
        if 'cp -a "$SRC/.\" "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.91 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.91 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1291_BD_WALL_TIME_UPDATER',
            '12.90|12.91',
            'OLD_NODE_BLOB="f7fcc98e56ad0ea88e804a39d19cc5f2c359fde8"',
            'NEW_NODE_BLOB="0d4a773a7f176d1dba6f3bcb958c82463572c54c"',
            'STREAMFORGE_V1291_LOCAL_NODE_AGENT_OPTIONAL',
            'STREAMFORGE_NODE_EXPIRY_BD_WALL_TIME_V1291',
            'BANGLADESH_TZ',
            '_node_expiry_enforcement_loop',
            'node_expiry_bd_display',
            'STREAMFORGE_V1291_TEST_FAIL_AFTER_WRITE',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.91 contract missing {marker}")
    elif node_expiry_v1292:
        if 'cp -a "$SRC/.\" "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.92 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.92 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1292_PAYMENT_NOTICE_UPDATER',
            'app/templates/node_form.html',
            'STREAMFORGE_NODE_EXPIRY_PAYMENT_NOTICE_V1292',
            'STREAMFORGE_NODE_EXPIRY_BACKDATE_ALLOWED_V1292',
            '12.91|12.92',
            'OLD_NODE_BLOB="0d4a773a7f176d1dba6f3bcb958c82463572c54c"',
            'NEW_NODE_BLOB="f2cd77a1df1a0efb77dc6352728a5e9143b10133"',
            'STREAMFORGE_V1292_LOCAL_NODE_AGENT_OPTIONAL',
            'BANGLADESH_TZ',
            '_node_expiry_enforcement_loop',
            'node_expiry_bd_display',
            'STREAMFORGE_V1292_TEST_FAIL_AFTER_WRITE',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.92 contract missing {marker}")
    elif node_expiry_v1293:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.93 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.93 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1293_PAYMENT_NOTICE_UPDATER',
            'app/templates/nodes.html',
            'STREAMFORGE_NODE_EXPIRY_PAYMENT_NOTICE_V1293',
            '12.92|12.93',
            'OLD_NODE_BLOB="f2cd77a1df1a0efb77dc6352728a5e9143b10133"',
            'NEW_NODE_BLOB="84629d588b01fa88319e28ddaf94ceb77949d56c"',
            'STREAMFORGE_V1293_LOCAL_NODE_AGENT_OPTIONAL',
            'STREAMFORGE_V1293_TEST_FAIL_AFTER_WRITE',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge-public',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.93 contract missing {marker}")
    elif node_expiry_v1294:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.94 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.94 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1294_EXPIRED_WEBPLAYER_UPDATER',
            'app/templates/dashboard.html',
            'app/templates/nodes.html',
            'STREAMFORGE_NODE_WEBPLAYER_SUBSCRIPTION_EXPIRED_V1294',
            'STREAMFORGE_MAIN_DASHBOARD_EXPIRED_NODE_COUNT_V1294',
            '12.93|12.94',
            'OLD_NODE_BLOB="84629d588b01fa88319e28ddaf94ceb77949d56c"',
            'NEW_NODE_BLOB="47795a19932ea3de0a4f159ad31c3dd51195a1d0"',
            'STREAMFORGE_V1294_LOCAL_NODE_AGENT_OPTIONAL',
            'STREAMFORGE_V1294_TEST_FAIL_AFTER_WRITE',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge-public',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.94 contract missing {marker}")
    elif node_summary_v1295:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.95 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.95 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1295_DASHBOARD_NODE_SUMMARY_UPDATER',
            'STREAMFORGE_MAIN_DASHBOARD_NODE_SUMMARY_V1295',
            'app/templates/dashboard.html',
            'offline_node_count',
            'expired_node_count',
            '12.94|12.95',
            'OLD_NODE_BLOB="47795a19932ea3de0a4f159ad31c3dd51195a1d0"',
            'NEW_NODE_BLOB="47795a19932ea3de0a4f159ad31c3dd51195a1d0"',
            'STREAMFORGE_V1295_LOCAL_NODE_AGENT_OPTIONAL',
            'STREAMFORGE_V1295_TEST_FAIL_AFTER_WRITE',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge-public',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.95 contract missing {marker}")
    elif webplayer_summary_v1296:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.96 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.96 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1296_EXPIRED_BRANDING_UPDATER',
            'STREAMFORGE_NODE_WEBPLAYER_EXPIRED_BRANDING_V1296',
            'STREAMFORGE_MAIN_DASHBOARD_NODE_STATUS_ROW_V1296',
            'app/templates/dashboard.html',
            '12.95|12.96',
            'OLD_NODE_BLOB="47795a19932ea3de0a4f159ad31c3dd51195a1d0"',
            'NEW_NODE_BLOB="9d256646b58147b00a0e57c54e4da382421b0e99"',
            'STREAMFORGE_V1296_LOCAL_NODE_AGENT_OPTIONAL',
            'STREAMFORGE_V1296_TEST_FAIL_AFTER_WRITE',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge-public',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.96 contract missing {marker}")
    elif all_clear_v1297:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.97 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.97 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1297_ALL_CLEAR_NODE_STATUS_UPDATER',
            'STREAMFORGE_MAIN_DASHBOARD_ALL_CLEAR_STATUS_V1297',
            'All nodes online',
            'All nodes active',
            'offline_node_count',
            'expired_node_count',
            '12.96|12.97',
            'OLD_NODE_BLOB="9d256646b58147b00a0e57c54e4da382421b0e99"',
            'NEW_NODE_BLOB="9d256646b58147b00a0e57c54e4da382421b0e99"',
            'STREAMFORGE_V1297_LOCAL_NODE_AGENT_OPTIONAL',
            'STREAMFORGE_V1297_TEST_FAIL_AFTER_WRITE',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge-public',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.97 contract missing {marker}")
    elif expiry_retry_v1298:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.98 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.98 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1298_RETRY_EXPIRY_RESTORE_UPDATER',
            'STREAMFORGE_NODE_EXPIRY_RESTORE_RETRY_V1298',
            '12.97|12.98',
            'OLD_NODE_BLOB="9d256646b58147b00a0e57c54e4da382421b0e99"',
            'NEW_NODE_BLOB="85c42f3419a8584752e5e166230f508b6675d6a6"',
            'STREAMFORGE_V1298_LOCAL_NODE_AGENT_OPTIONAL',
            'STREAMFORGE_V1298_TEST_FAIL_AFTER_WRITE',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge-public',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.98 contract missing {marker}")
    elif main_web_update_v1299:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.99 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.99 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1299_MAIN_WEB_UPDATE_UPDATER',
            'STREAMFORGE_V1299_INSTALL_PRIVILEGED_UPDATE_HELPER',
            'app/templates/main_updates.html',
            'scripts/main_system_control.py',
            'deploy/streamforge-main-system.service',
            '12.98|12.99',
            'OLD_NODE_BLOB="85c42f3419a8584752e5e166230f508b6675d6a6"',
            'NEW_NODE_BLOB="85c42f3419a8584752e5e166230f508b6675d6a6"',
            'STREAMFORGE_V1299_LOCAL_NODE_AGENT_OPTIONAL',
            'STREAMFORGE_V1299_TEST_FAIL_AFTER_WRITE',
            'systemctl enable --now streamforge-main-system.path',
            '/usr/local/sbin/streamforge-main-system-control',
            'ROLLBACK=1',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'systemctl is-active --quiet streamforge-public',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.99 contract missing {marker}")
    elif hardening_v1283:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.83 must not copy the entire tree")
        # A reload is required for the real_ip site patch; a restart of Nginx
        # or the channel supervisor is never acceptable.
        if re.search(r'systemctl[ \t]+restart[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.83 must not restart Nginx or the channel supervisor")
        if not (root / "scripts" / "apply_v1283_hardening.py").is_file():
            errors.append("missing scripts/apply_v1283_hardening.py")
        if not (root / "scripts" / "apply_v1283_hardening.json").is_file():
            errors.append("missing scripts/apply_v1283_hardening.json")
        for marker in (
            'STREAMFORGE_V1283_HARDENING_UPDATER',
            '12.82|12.83',
            'OLD_NODE_BLOB="2d9595392e713068cc4c0c8f5243ef55b4f5472b"',
            'scripts/apply_v1283_hardening.py',
            'STREAMFORGE_V1283_TEST_FAIL_AFTER_WRITE',
            'STREAMFORGE_V1283_SECRET_KEY_ENV_REPAIR_V1',
            'STREAMFORGE_V1283_NEW_CLIENT_IP_MODULE',
            'STREAMFORGE_V1283_NGINX_REAL_IP_V1',
            'nginx -t',
            'systemctl reload nginx',
            'Restarting Main HTTP workers only (channel supervisor and streams keep running)',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.83 contract missing {marker}")
    elif hardening_v1282:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.82 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.82 must not restart Nginx or the channel supervisor")
        if not (root / "scripts" / "apply_v1282_hardening.py").is_file():
            errors.append("missing scripts/apply_v1282_hardening.py")
        if not (root / "scripts" / "apply_v1282_hardening.json").is_file():
            errors.append("missing scripts/apply_v1282_hardening.json")
        for marker in (
            'STREAMFORGE_V1282_HARDENING_UPDATER',
            '12.81|12.82',
            'OLD_NODE_BLOB="2d9595392e713068cc4c0c8f5243ef55b4f5472b"',
            'scripts/apply_v1282_hardening.py',
            'STREAMFORGE_V1282_TEST_FAIL_AFTER_WRITE',
            'Restarting Main HTTP workers only (channel supervisor and streams keep running)',
            'RESTARTED=1',
            'systemctl restart streamforge',
            'systemctl restart streamforge-public',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.82 contract missing {marker}")
    elif node_live_charts_v1281:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.81 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.81 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1281_NODE_LIVE_CHARTS_UPDATER',
            '12.80|12.81',
            'OLD_NODE_BLOB="83521def749210e025af9a979a77405983f0a43e"',
            'STREAMFORGE_V1281_NODE_APP_ONLY_PARITY',
            'STREAMFORGE_NODE_DASHBOARD_LIVE_CHARTS_V1281',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.81 contract missing {marker}")
    elif live_charts_v1280:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.80 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.80 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1280_LIVE_CHARTS_UPDATER',
            '12.79|12.80',
            'STREAMFORGE_V1280_MAIN_ONLY_NODE_PARITY',
            'scripts/apply_v1280_live_charts.py',
            'scripts/sync_main_static_cache.py',
            'STREAMFORGE_V1280_STATIC_CACHE_VERIFIED',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.80 contract missing {marker}")
    elif static_cache_v1279:
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.79 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1279_STATIC_CACHE_UPDATER',
            '12.78|12.79',
            'STREAMFORGE_V1279_MAIN_ONLY_NODE_PARITY',
            'scripts/sync_main_static_cache.py',
            'STREAMFORGE_V1279_STATIC_CACHE_VERIFIED',
            'Rolling back from',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.79 contract missing {marker}")
    elif dashboard_v1278:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.78 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.78 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1278_DASHBOARD_UPDATER',
            '12.77|12.78',
            'STREAMFORGE_V1278_MAIN_ONLY_NODE_PARITY',
            'scripts/apply_v1278_dashboard.py',
            'STREAMFORGE_NODE_SSH_HLSJS_PAYLOAD_V1278',
            'absent-files.txt',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.78 contract missing {marker}")
    elif password_policy_v1277:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.77 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.77 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1277_PASSWORD_POLICY_UPDATER',
            '12.76|12.77',
            'OLD_NODE_BLOB="0f466c3fc32240997e5acec508a5cf5aa904d8ec"',
            'STREAMFORGE_V1277_NODE_APP_ONLY_PARITY',
            'scripts/apply_v1277_security.py',
            'STREAMFORGE_NODE_SECURITY_HEADERS_V1277',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.77 contract missing {marker}")
    elif security_hardening_v1276:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.76 must not copy the entire tree")
        if re.search(r'systemctl[ \t]+(restart|reload)[ \t]+(nginx|streamforge-channel-supervisor)', text):
            errors.append(f"{updater.name} v12.76 must not restart Nginx or the channel supervisor")
        for marker in (
            'STREAMFORGE_V1276_SECURITY_HARDENING_UPDATER',
            '12.75|12.76',
            'STREAMFORGE_V1276_MAIN_ONLY_NODE_PARITY',
            'scripts/apply_v1276_security.py',
            'app/stream_keys.py',
            'streamforge-apply-main-access',
            'absent-files.txt',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.76 security contract missing {marker}")
    elif secure_cookie_v1275:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.75 must not copy the entire tree")
        for marker in (
            'STREAMFORGE_V1275_SECURE_SESSION_COOKIE_UPDATER',
            '12.74|12.75',
            'STREAMFORGE_V1275_MAIN_ONLY_NODE_PARITY',
            'scripts/apply_main_hotfixes.py',
            'STREAMFORGE_MAIN_SESSION_COOKIE_SECURE_ON_HTTPS',
            'app/main.py',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.75 Secure-cookie contract missing {marker}")
    elif integrated_main_v1274:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.74 must not copy the entire tree")
        if 'atomic_copy' in text or '/app/main.py" "$ROOT' in text:
            errors.append(f"{updater.name} v12.74 must not replace installed Main source")
        for marker in (
            'STREAMFORGE_V1274_INTEGRATED_MAIN_SOURCE_UPDATER',
            '12.73|12.74',
            'STREAMFORGE_V1274_MAIN_ONLY_NODE_PARITY',
            'STREAMFORGE_V1274_INSTALLED_MAIN_VERIFIED',
            'STREAMFORGE_ADMIN_SEED_FRESH_INSTALL_ONLY',
            'Rolling back from',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.74 metadata-only contract missing {marker}")
    elif bounded_main_hotfixes_v1273:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.73 must not copy the entire tree")
        for marker in (
            'STREAMFORGE_V1273_MAIN_HOTFIXES_UPDATER',
            '12.72|12.73',
            'STREAMFORGE_V1273_MAIN_ONLY_NODE_PARITY',
            'scripts/apply_main_hotfixes.py',
            'STREAMFORGE_ADMIN_SEED_FRESH_INSTALL_ONLY',
            'app/main.py',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.73 Main hotfix contract missing {marker}")
    elif bounded_node_login_v1272:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.72 must not copy the entire tree")
        for marker in (
            'STREAMFORGE_V1272_NODE_LOGIN_DARK_UI_UPDATER',
            '12.70|12.71|12.72',
            'STREAMFORGE_V1272_NODE_LOGIN_ONLY_PARITY',
            'OLD_NODE_BLOB="b023c2fd96c46bbeec1c16aa42c786bcf7a5544e"',
            'STREAMFORGE_V1272_NODE_LOGIN_DARK_RESPONSIVE',
            'node_agent/app.py',
            'test_v1272_node_login.py',
            'Rolling back from',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.72 Node login design contract missing {marker}")
    elif bounded_ui_v1271:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.71 unexpectedly copied full release tree")
        for marker in (
            'STREAMFORGE_V1271_LOGIN_AUTOFILL_BLOCKED_IP_CARD',
            '12.70|12.71',
            'STREAMFORGE_V1271_MAIN_ONLY_NODE_PARITY',
            'app/templates/login.html',
            'app/templates/admin_users.html',
            'test_v1271_login_blocked_ui.py',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.71 scope contract missing {marker}")
    elif bounded_reveal_ui_v1270:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.70 unexpectedly copied full release")
        for marker in (
            'STREAMFORGE_V1270_REVEAL_TIMEOUT_AND_NODE_COPY',
            '12.69|12.70',
            'STREAMFORGE_V1270_NODE_FORM_ONLY_PARITY',
            'OLD_NODE_BLOB="a65a53a4dc8f071cb4f906d18b242ae11b864a97"',
            'STREAMFORGE_V1270_NODE_PASSWORD_FORM_CLEANUP',
            'STREAMFORGE_V1270_REVEAL_AUTH_STEP',
            'test_v1270_reveal_ui.py',
            'app/templates/admin_users.html',
            'node_agent/app.py',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.70 scope contract missing {marker}")
    elif bounded_ui_notice_v1269:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.69 unexpectedly copied full release")
        for marker in (
            'STREAMFORGE_V1269_THEME_AND_NODE_FORM_SCOPED',
            '12.68|12.69',
            'STREAMFORGE_V1269_NODE_FORM_ONLY_PARITY',
            'OLD_NODE_BLOB="256faac6acf0cee0f3ee223063d222881fb7a824"',
            'STREAMFORGE_V1269_VAULT_REVEAL_TEXT_DARK',
            'STREAMFORGE_V1269_COMPACT_NODE_PASSWORD_NOTICE',
            'app/templates/admin_users.html',
            'node_agent/app.py',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.69 scope contract missing {marker}")
    elif bounded_main_pw_modal_v1268:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.68 unexpectedly copies full release tree")
        for marker in (
            'STREAMFORGE_V1268_MAIN_PASSWORD_MODAL_SCOPED',
            '12.67|12.68',
            'STREAMFORGE_V1268_MAIN_ONLY_NODE_PARITY',
            'scripts/apply_v1268_password_modal.py',
            'STREAMFORGE_V1268_PW_MODAL_CSRF',
            'STREAMFORGE_V1268_PASSWORD_RESET_MODAL_API',
            'app/templates/admin_users.html',
            'atomic_copy',
            'Rolling back from',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.68 Main popup contract missing {marker}")
    elif bounded_self_pw_v1267:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.67 unexpectedly copies full release")
        for marker in (
            'TARGET="12.67"',
            '12.66|12.67',
            'STREAMFORGE_V1267_NODE_APP_ONLY_PARITY',
            'STREAMFORGE_V1267_SELF_PASSWORD_PERMISSION_FIX',
            'node_agent/app.py',
            'test_v1267_node_self_password.py',
            'PREVIOUS_NODE_BLOB="a071e851db49804c413a6d4db9de792a20adb69a"',
            'Rolling back from',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.67 bounded Node permission contract missing {marker}")
    elif bounded_managed_v1266:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.66 unexpectedly copies full release source")
        for marker in (
            'TARGET_VERSION="12.66"',
            'NODE_TARGET_VERSION="12.66"',
            '12.64|12.65|12.66',
            'STREAMFORGE_V1266_BOUNDED_AUTO_VAULT',
            'STREAMFORGE_V1266_NODE_SOURCE_PARITY',
            'STREAMFORGE_V1266_AUTO_VAULT',
            'STREAMFORGE_V1266_NODE_VAULT_DISCLOSURE',
            'scripts/apply_v1264_ip_lock.py',
            'scripts/apply_v1265_ip_origin.py',
            'scripts/apply_v1266_auto_vault.py',
            'node_agent/app.py',
            'app/templates/admin_user_form.html',
            'app/templates/account_password.html',
            'Rolling back from',
            'atomic_copy',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.66 managed vault contract missing {marker}")
    elif bounded_ip_origin_v1265:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.65 source tracking copied full release tree")
        for marker in (
            'TARGET_VERSION="12.65"',
            'NODE_TARGET_VERSION="12.65"',
            '12.64|12.65',
            'STREAMFORGE_V1265_MAIN_ONLY_IP_BLOCK_ORIGIN_NODE_PARITY',
            'STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER',
            'scripts/apply_v1264_ip_lock.py',
            'scripts/apply_v1265_ip_origin.py',
            'app/panel_login_guard.py',
            'app/templates/admin_users.html',
            'Node runtime payload differs for:',
            'compile(p.read_text(encoding="utf-8"),str(p),"exec")',
            'atomic_copy',
            'Rolling back from',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.65 bounded origin contract missing {marker}")
    elif bounded_ip_v1264:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.64 IP lockout copies full release tree")
        for marker in (
            'TARGET_VERSION="12.64"',
            'NODE_TARGET_VERSION="12.64"',
            '12.63|12.64',
            'STREAMFORGE_V1264_MAIN_ONLY_IP_LOCK_NODE_PARITY',
            'STREAMFORGE_V1264_IP_GUARD',
            'app/panel_login_guard.py',
            'app/panel_password_vault.py',
            'app/templates/admin_users.html',
            'scripts/apply_v1264_ip_lock.py',
            'Node runtime payload differs for:',
            'compile(p.read_text(encoding="utf-8"),str(p),"exec")',
            'atomic_copy',
            'Rolling back from',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.64 IP-guard contract missing {marker}")
    elif bounded_vault_v1263:
        if 'cp -a "$SRC/." "$STAGE/"' in text:
            errors.append(f"{updater.name} v12.63 vault unexpectedly copies full release tree")
        for marker in (
            'TARGET_VERSION="12.63"',
            'NODE_TARGET_VERSION="12.63"',
            '12.62|12.63',
            'STREAMFORGE_V1263_MAIN_ONLY_VAULT_UPDATE',
            'STREAMFORGE_V1263_MAIN_UI_ONLY_NODE_VERSION_ALIGNMENT',
            'STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263',
            'app/panel_password_vault.py',
            'app/models.py',
            'app/templates/admin_users.html',
            'app/templates/admin_user_form.html',
            'scripts/apply_v1263_vault.py',
            'Node runtime payload differs for:',
            'compile(p.read_text(encoding="utf-8"),str(p),"exec")',
            'atomic_copy',
            'Rolling back from',
        ):
            if marker not in text:
                errors.append(f"{updater.name} v12.63 vault contract missing {marker}")
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
