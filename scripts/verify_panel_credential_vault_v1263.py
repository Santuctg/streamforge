#!/usr/bin/env python3
"""v12.63 saved credential vault security contract (no password recovery from hashes)."""
from pathlib import Path
R=Path(__file__).resolve().parents[1]
def contents(path):
    file=R/path
    return file.read_text(encoding="utf-8") if file.exists() else ""
model=contents("app/models.py")
main=contents("app/main.py")
vault=contents("app/panel_password_vault.py")
login_guard=contents("app/panel_login_guard.py")
listing=contents("app/templates/admin_users.html")
app_form=contents("app/templates/admin_user_form.html")
auth=contents("app/auth.py")
errors=[]
def need(ok,message):
    if not ok:errors.append(message)
need(contents("VERSION").strip()=="12.70","Main VERSION 12.70")
need(contents("node_agent/VERSION").strip()=="12.70","bundled Node VERSION 12.70")
need("STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263" in vault,"module marker")
need("STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263" in contents("scripts/apply_v1263_vault.py"),"Main integration patch marker")
need('STREAMFORGE_PANEL_PASSWORD_VAULT_KEY' in vault,"dedicated external Fernet key")
need('settings.secret_key' not in vault,"no reuse of Main cookie secret")
need('class PanelUserCredentialVault' in model,"dedicated Main-only encrypted table")
need('ondelete="CASCADE"' in model,"FK cascade protection")
need('password_hash' in model and 'hashlib.scrypt' in auth,"scrypt authentication unchanged")
need('encrypt(' in vault and 'decrypt(' in vault,"encryption/decryption")
need('verify_password' in vault,"super admin re-authentication")
need('has_permission' in vault and 'panel_users.password_reveal' in vault,"role-permission reveal check")
need('vault_target_visible' in vault and 'SUPERUSER_PERMISSION' in vault,"hierarchy and Super Admin protection")
need('can_vault_manage' in vault,"independent vault management permission")
need('panel_users.password_reveal' in contents("app/permissions.py"),"role permissions published")
need('panel_users.password_vault_manage' in contents("app/permissions.py"),"vault management permission published")
need('Super Admin-exclusive sensitive grant' in contents("scripts/apply_v1263_vault.py"),"sensitive permissions Super Admin-grant only")
need('csrf' in vault and 'session' in vault,"session-bound CSRF")
need('no-store' in vault,"secret response no-store")
need('list_panel_blocked_ips' in vault and 'unblock_panel_ip' in vault,
     "Super Admin Panel IP block status and manual-unblock APIs")
need('LOGIN_LOCK_SECONDS = 3 * 60 * 60' in login_guard and 'FAILED_ATTEMPTS = 5' in login_guard,
     "five bad Panel login attempts / three-hour lockout")
need('record_ip_failure' in contents("scripts/apply_v1264_ip_lock.py"),
     "Main and Node shared-IP login throttling integration")
need('unlock_failures' in vault and 'purpose="unlock"' in vault,
     "separate persistent unlock reauthentication throttle")
need('data-vault-lock-console' in listing and 'data-vault-unlock-confirm' in listing,
     "Super Admin Panel Login Blocked IPs web console")
need('log_event' in vault,"audited reveal")
need('password_hash_fingerprint' in vault,"stale-secret protection")
need('vault_store' in contents("scripts/apply_v1263_vault.py") and 'vault_discard' in contents("scripts/apply_v1263_vault.py"),"atomic create/edit/self-change integration")
need('Show saved password' in listing and 'item.id in vault_saved_ids' in listing,"explicit show action only for available secrets")
need('data-vault-secret-value' in listing and 'setTimeout' in listing,"auto-hide")
need('STREAMFORGE_V1270_REVEAL_AUTH_STEP' in listing and 'authStep.hidden = true;' in listing,
     "hide Super Admin reauthentication after success")
need('const expiresAt = performance.now() + 10000;' in listing and
     'let remaining = 15;' not in listing, "ten-second exact countdown")
need('STREAMFORGE_V1269_VAULT_REVEAL_TEXT_DARK' in listing and
     '.sf-vault-dialog input[data-vault-secret-value]' in listing,
     "Show/Hide text input retains dark colors")
need('STREAMFORGE_V1270_NODE_PASSWORD_FORM_CLEANUP' in contents("node_agent/app.py"),
     "new Node compact disclosure")
need('data-vault-password-consent' not in app_form and 'name="vault_remember"' not in app_form,
     "automatic managed credential retention without checkbox")
need('STREAMFORGE_V1266_AUTO_VAULT' in contents("scripts/apply_v1266_auto_vault.py"),
     "bounded auto-retain patch")
need('Managed password retention' in app_form and
     'Managed password retention' in contents("app/templates/account_password.html"),
     "main-managed password disclosure")
need('managed_password_notice' in contents("node_agent/app.py") and
     'New passwords are encrypted on Main and can be viewed by authorized administrators.' not in contents("node_agent/app.py") and
     'As a managed account, authorized administrators can recover changed passwords.' in contents("node_agent/app.py"),
     "updated Node self-service disclosure and authenticated notification")
need('STREAMFORGE_PANEL_USER_PASSWORD_LINK_FIX_V1262' in listing,"v12.62 route fix retained")
need('STREAMFORGE_PANEL_USER_PASSWORD_VISIBILITY_V1261' in app_form,"v12.61 form retained")
need('ip_failures' in login_guard and 'normalize_client_ip' in login_guard,"durable IP not account login policy")
need('Blocked IPs' in listing,"blocked IP web listing")
need('ip_block_origins' in login_guard and 'blocked_by_server' in login_guard,
     "tracked source server for blocking request")
need('Triggered by server' in listing, "trigger server UI heading")
need('STREAMFORGE_V1265_BLOCK_TRIGGER_SERVER' in contents("scripts/apply_v1265_ip_origin.py"),
     "Main and Node origin patcher")
if errors: raise SystemExit("FAIL v12.64: "+", ".join(errors))
print("PASS: v12.70 correct reveal flow, Node managed disclosure and IP security contracts")
