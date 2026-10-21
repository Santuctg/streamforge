#!/usr/bin/env python3
"""Apply bounded v12.63 Panel Credential Vault integration to Main app/main.py.

Used during Main upgrade, not on live source before updater backup.
Runs idempotently and aborts if known v12.62 anchors are absent.
"""
from __future__ import annotations
from pathlib import Path
import sys

MARKER = "STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263"


def transform(source: str) -> str:
    if MARKER in source:
        return source

    def replace_once(src: str, old: str, new: str, label: str) -> str:
        count = src.count(old)
        if count != 1:
            raise ValueError(f"{label}: expected one anchor, found {count}")
        return src.replace(old, new)

    source = replace_once(
        source,
        "from .secrets_store import encrypt_secret, decrypt_secret",
        "from .secrets_store import encrypt_secret, decrypt_secret\n"
        "# STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263: Main-only opt-in encrypted credentials.\n"
        "from .panel_password_vault import (\n"
        "    router as panel_vault_router, can_vault_reveal, can_vault_manage, vault_enabled, vault_csrf_token,\n"
        "    vault_password_available, vault_store, vault_discard,\n"
        ")",
        "vault import",
    )
    source = replace_once(
        source,
        "from .secrets_store import encrypt_secret, decrypt_secret\n"
        "# STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263: Main-only opt-in encrypted credentials.\n"
        "from .panel_password_vault import (\n"
        "    router as panel_vault_router, can_vault_reveal, can_vault_manage, vault_enabled, vault_csrf_token,\n"
        "    vault_password_available, vault_store, vault_discard,\n"
        ")",
        "from .secrets_store import encrypt_secret, decrypt_secret\n"
        "# STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263: Main-only opt-in encrypted credentials.\n"
        "from .panel_password_vault import (\n"
        "    router as panel_vault_router, can_vault_reveal, can_vault_manage, vault_enabled, vault_csrf_token,\n"
        "    vault_password_available, vault_store, vault_discard,\n"
        ")\n"
        "# STREAMFORGE_V1263_PANEL_LOGIN_LOCK_GUARD: cross-Main/Node login failures.\n"
        "from .panel_login_guard import (\n"
        "    LoginGuardUnavailable, login_locked, record_login_failure, clear_login_failures,\n"
        ")",
        "panel login guard import",
    )
    source = replace_once(
        source, 'app = FastAPI(title="StreamForge", lifespan=lifespan)',
        'app = FastAPI(title="StreamForge", lifespan=lifespan)\n'
        'app.include_router(panel_vault_router)  # protected Main-only vault POST',
        "vault router",
    )
    source = replace_once(
        source,
        '    admin.password_hash = hash_password(new_password)\n    affected_node_ids = {item.id for item in admin.nodes}',
        '    admin.password_hash = hash_password(new_password)\n'
        '    vault_discard(db, admin.id)  # self-service changes invalidate old vault value\n'
        '    affected_node_ids = {item.id for item in admin.nodes}',
        "self password reset",
    )
    start = source.index('@app.get("/admin-users",')
    finish = source.index('@app.get("/admin-users/new",', start)
    listing = source[start:finish]
    listing = replace_once(
        listing, '        panel_users=panel_users,\n        error=error,',
        '        panel_users=panel_users,\n'
        '        vault_active=can_vault_reveal(actor) and vault_enabled(),\n'
        '        vault_saved_ids=(\n'
        '            {item.id for item in panel_users if vault_password_available(db, item)}\n'
        '            if can_vault_reveal(actor) and vault_enabled() else set()\n'
        '        ),\n'
        '        vault_csrf_token=(vault_csrf_token(request) if admin_is_super_admin(actor) or (can_vault_reveal(actor) and vault_enabled()) else ""),\n'
        '        error=error,',
        "listing render",
    )
    source = source[:start] + listing + source[finish:]
    start = source.index('@app.get("/admin-users/new",')
    finish = source.index('@app.post("/admin-users/{admin_user_id}/delete",', start)
    routes = source[start:finish]
    if routes.count('roles=roles, nodes=nodes, error=None)') != 2:
        raise ValueError("expected two create/edit GET render calls")
    routes = routes.replace(
        'roles=roles, nodes=nodes, error=None)',
        'roles=roles, nodes=nodes, error=None, vault_active=can_vault_manage(actor) and vault_enabled())',
    )
    if routes.count('roles=roles, nodes=nodes, error=error)') != 2:
        raise ValueError("expected two create/edit error render calls")
    routes = routes.replace(
        'roles=roles, nodes=nodes, error=error)',
        'roles=roles, nodes=nodes, error=error, vault_active=can_vault_manage(actor) and vault_enabled())',
    )
    if routes.count('    node_ids: list[int] = Form(default=[]),\n    db: Session = Depends(get_db),') != 2:
        raise ValueError("expected two create/edit Form signatures")
    routes = routes.replace(
        '    node_ids: list[int] = Form(default=[]),\n    db: Session = Depends(get_db),',
        '    node_ids: list[int] = Form(default=[]),\n'
        '    vault_remember: Optional[str] = Form(None),\n'
        '    db: Session = Depends(get_db),',
    )
    if routes.count('    elif password != password_confirm:\n        error = "Password confirmation does not match."') != 2:
        raise ValueError("expected two form validation anchors")
    routes = routes.replace(
        '    elif password != password_confirm:\n        error = "Password confirmation does not match."',
        '    elif password != password_confirm:\n'
        '        error = "Password confirmation does not match."\n'
        '    elif as_bool(vault_remember) and not (can_vault_manage(actor) and vault_enabled()):\n'
        '        error = "Vault is not configured or you are not permitted to save credentials."',
    )
    routes = replace_once(
        routes,
        '    db.add(panel_user)\n    db.commit()\n    sync_errors = sync_panel_users_for_node_ids(',
        '    db.add(panel_user)\n'
        '    db.flush()\n'
        '    if as_bool(vault_remember):\n'
        '        if not vault_store(db, panel_user, password):\n'
        '            raise HTTPException(503, "Credential vault is not configured")\n'
        '    db.commit()\n'
        '    sync_errors = sync_panel_users_for_node_ids(',
        "create stored password",
    )
    routes = replace_once(
        routes,
        '    if password:\n        panel_user.password_hash = hash_password(password)\n    db.commit()',
        '    if password:\n'
        '        panel_user.password_hash = hash_password(password)\n'
        '        if as_bool(vault_remember):\n'
        '            if not vault_store(db, panel_user, password):\n'
        '                raise HTTPException(503, "Credential vault is not configured")\n'
        '        else:\n'
        '            vault_discard(db, panel_user.id)  # never show stale saved passwords\n'
        '    db.commit()',
        "reset stored password",
    )
    source = source[:start] + routes + source[finish:]
    # Node live-auth changes the authoritative hash, so an escrow is now stale.
    source = replace_once(
        source,
        '        admin.password_hash = hash_password(new_password)\n'
        '        db.commit()\n'
        '        db.refresh(admin)\n'
        '        auth_version = _panel_admin_auth_version(admin)',
        '        admin.password_hash = hash_password(new_password)\n'
        '        vault_discard(db, admin.id)  # Node password changes revoke the saved secret\n'
        '        db.commit()\n'
        '        db.refresh(admin)\n'
        '        auth_version = _panel_admin_auth_version(admin)',
        "Node self-service password change",
    )
    # Force deletion even if SQLite FK cascade is not enabled on a connection.
    source = replace_once(
        source,
        '    old_node_ids = {item.id for item in panel_user.nodes}\n'
        '    db.delete(panel_user)\n'
        '    db.commit()',
        '    old_node_ids = {item.id for item in panel_user.nodes}\n'
        '    vault_discard(db, panel_user.id)  # panel-user delete\n'
        '    db.delete(panel_user)\n'
        '    db.commit()',
        "panel-user deletion",
    )
    # Sensitive permissions may be assigned only by an unrestricted Super Admin,
    # even when a delegated account itself holds reveal/management rights.
    source = replace_once(
        source,
        '    return {\n'
        '        key\n'
        '        for key in role_permission_set(admin.role)\n'
        '        if key in ALL_PERMISSION_KEYS\n'
        '    }\n\n\ndef permission_groups_for_role_editor',
        '    return {\n'
        '        key\n'
        '        for key in role_permission_set(admin.role)\n'
        '        if key in ALL_PERMISSION_KEYS and key not in {\n'
        '            "panel_users.password_reveal", "panel_users.password_vault_manage"\n'
        '        }\n'
        '    }\n\n\ndef permission_groups_for_role_editor',
        "Super Admin-exclusive sensitive grant",
    )
    # Audit granting/revoking sensitive permissions, with no credential values.
    source = replace_once(
        source,
        '    db.add(role)\n'
        '    db.commit()\n'
        '    return panel_admin_redirect("/roles", message="Role created")',
        '    db.add(role)\n'
        '    db.commit()\n'
        '    sensitive = {"panel_users.password_reveal", "panel_users.password_vault_manage"}\n'
        '    if sensitive & set(decode_permissions(role.permissions)):\n'
        '        log_event("Vault permissions granted to role", scope="auth", actor=actor.username,\n'
        '                  details={"role_id": role.id, "permissions": sorted(sensitive & set(decode_permissions(role.permissions)))})\n'
        '    return panel_admin_redirect("/roles", message="Role created")',
        "role creation audit",
    )
    source = replace_once(
        source,
        '    role.permissions = encode_permissions(submitted_permissions & allowed_permissions)\n'
        '    db.commit()\n'
        '    return panel_admin_redirect("/roles", message="Role updated")',
        '    sensitive = {"panel_users.password_reveal", "panel_users.password_vault_manage"}\n'
        '    old_sensitive = sensitive & set(decode_permissions(role.permissions))\n'
        '    role.permissions = encode_permissions(submitted_permissions & allowed_permissions)\n'
        '    db.commit()\n'
        '    new_sensitive = sensitive & set(decode_permissions(role.permissions))\n'
        '    if old_sensitive != new_sensitive:\n'
        '        log_event("Vault role permissions changed", scope="auth", actor=actor.username,\n'
        '                  details={"role_id": role.id, "granted": sorted(new_sensitive - old_sensitive),\n'
        '                           "revoked": sorted(old_sensitive - new_sensitive)})\n'
        '    return panel_admin_redirect("/roles", message="Role updated")',
        "role update audit",
    )
    # MAIN + live-auth Node panel logins share one persistent per-user lockout.
    # Node sessions and streaming-account APIs do not use this login guard.
    source = replace_once(source, "    admin = db.scalar(select(AdminUser).where(AdminUser.username == username.strip()))\n    if not admin or not admin.is_active or not admin.main_panel_access or not verify_password(password, admin.password_hash):\n        # STREAMFORGE_ACCESS_LOG_CLIENT_IP_V73: Access logs always retain the real browser/client IP.\n        log_event(f\"Failed panel login for {username.strip() or 'unknown'}\", scope=\"auth\", level=\"warning\", actor=username.strip() or None, details={\"ip\": client_ip(request)})\n        return render(request, \"login.html\", db, error=\"Invalid username or password\")\n    request.session[\"admin_id\"] = admin.id", "    admin = db.scalar(select(AdminUser).where(AdminUser.username == username.strip()))\n    allowed = bool(admin and admin.is_active and admin.main_panel_access)\n    try:\n        if allowed and login_locked(admin.id):\n            log_event(\"Panel login denied: account locked\", scope=\"auth\", level=\"warning\",\n                      actor=admin.username, details={\"ip\": client_ip(request)})\n            return render(request, \"login.html\", db, error=\"Invalid username or password\")\n        correct = bool(allowed and verify_password(password, admin.password_hash))\n        if not correct:\n            # Unknown/disabled accounts do not create persistent per-account rows.\n            if allowed and record_login_failure(admin.id):\n                log_event(\"Panel login blocked after failed passwords\", scope=\"auth\", level=\"warning\",\n                          actor=admin.username, details={\"ip\": client_ip(request)})\n            log_event(f\"Failed panel login for {username.strip() or 'unknown'}\", scope=\"auth\",\n                      level=\"warning\", actor=username.strip() or None, details={\"ip\": client_ip(request)})\n            return render(request, \"login.html\", db, error=\"Invalid username or password\")\n        # Recheck to block concurrent incorrect requests that activated a lock.\n        if login_locked(admin.id):\n            return render(request, \"login.html\", db, error=\"Invalid username or password\")\n        clear_login_failures(admin.id)\n    except LoginGuardUnavailable:\n        log_event(\"Panel login guard unavailable\", scope=\"auth\", level=\"error\",\n                  actor=username.strip() or None)\n        return render(request, \"login.html\", db, error=\"Login temporarily unavailable\")\n    request.session[\"admin_id\"] = admin.id", "Main login lockout")
    source = replace_once(source, "    auth_version = _panel_admin_auth_version(admin)\n    if action == \"login\":\n        password = str(payload.get(\"password\") or \"\")\n        if not verify_password(password, admin.password_hash):\n            log_event(\n                f\"Failed live Node panel login for {username or 'unknown'} on {node.name}\",\n                scope=\"auth\", level=\"warning\", node_id=node.id, actor=username or None, details={\"ip\": submitted_client_ip} if submitted_client_ip else None,\n            )\n            raise HTTPException(401, \"Invalid username or password\")\n        log_event(\n            f\"Live Node panel login succeeded on {node.name}\",\n            scope=\"auth\", node_id=node.id, actor=admin.username, details={\"ip\": submitted_client_ip} if submitted_client_ip else None,\n        )", "    auth_version = _panel_admin_auth_version(admin)\n    if action == \"login\":\n        try:\n            if login_locked(admin.id):\n                log_event(\"Live Node Panel login denied: account locked\", scope=\"auth\",\n                          level=\"warning\", node_id=node.id, actor=admin.username)\n                raise HTTPException(401, \"Invalid username or password\")\n            password = str(payload.get(\"password\") or \"\")\n            if not verify_password(password, admin.password_hash):\n                if record_login_failure(admin.id):\n                    log_event(\"Panel login blocked after failed Node passwords\", scope=\"auth\",\n                              level=\"warning\", node_id=node.id, actor=admin.username)\n                log_event(\n                    f\"Failed live Node panel login for {username or 'unknown'} on {node.name}\",\n                    scope=\"auth\", level=\"warning\", node_id=node.id, actor=username or None, details={\"ip\": submitted_client_ip} if submitted_client_ip else None,\n                )\n                raise HTTPException(401, \"Invalid username or password\")\n            if login_locked(admin.id):\n                raise HTTPException(401, \"Invalid username or password\")\n            clear_login_failures(admin.id)\n        except LoginGuardUnavailable as exc:\n            log_event(\"Live Node Panel login guard unavailable\", scope=\"auth\", level=\"error\",\n                      node_id=node.id, actor=username or None)\n            raise HTTPException(503, \"Panel login temporarily unavailable\") from exc\n        log_event(\n            f\"Live Node panel login succeeded on {node.name}\",\n            scope=\"auth\", node_id=node.id, actor=admin.username, details={\"ip\": submitted_client_ip} if submitted_client_ip else None,\n        )", "Node live login lockout")
    compile(source, "app/main.py", "exec")
    return source


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: apply_v1263_vault.py /path/to/app/main.py")
    path = Path(sys.argv[1])
    old = path.read_text(encoding="utf-8")
    new = transform(old)
    if new != old:
        path.write_text(new, encoding="utf-8")
    print("PASS: v12.63 Main credential vault patch applied or already present")


if __name__ == "__main__":
    main()
