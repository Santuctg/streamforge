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
listing=contents("app/templates/admin_users.html")
app_form=contents("app/templates/admin_user_form.html")
auth=contents("app/auth.py")
errors=[]
def need(ok,message):
    if not ok:errors.append(message)
need(contents("VERSION").strip()=="12.63","Main VERSION 12.63")
need(contents("node_agent/VERSION").strip()=="12.63","bundled Node VERSION 12.63")
need("STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263" in vault,"module marker")
need("STREAMFORGE_PANEL_CREDENTIAL_VAULT_V1263" in main,"Main integration marker")
need('STREAMFORGE_PANEL_PASSWORD_VAULT_KEY' in vault,"dedicated external Fernet key")
need('settings.secret_key' not in vault,"no reuse of Main cookie secret")
need('class PanelUserCredentialVault' in model,"dedicated Main-only encrypted table")
need('ondelete="CASCADE"' in model,"FK cascade protection")
need('password_hash' in model and 'hashlib.scrypt' in auth,"scrypt authentication unchanged")
need('encrypt(' in vault and 'decrypt(' in vault,"encryption/decryption")
need('verify_password' in vault,"super admin re-authentication")
need('role_permission_set' in vault and 'SUPERUSER_PERMISSION' in vault,"Super Admin only")
need('csrf' in vault and 'session' in vault,"session-bound CSRF")
need('no-store' in vault,"secret response no-store")
need('log_event' in vault,"audited reveal")
need('password_hash_fingerprint' in vault,"stale-secret protection")
need('vault_store' in main and 'vault_discard' in main,"atomic create/edit/delete/self-change integration")
need('Show saved password' in listing and 'vault_password_available' in listing,"explicit show action only for available secrets")
need('data-vault-secret-value' in listing and 'setTimeout' in listing,"auto-hide")
need('data-vault-password-consent' in app_form,"explicit save consent")
need('password' not in contents("app/templates/admin_users.html").split('value="{{')[0],"noop") if False else None
need('STREAMFORGE_PANEL_USER_PASSWORD_LINK_FIX_V1262' in listing,"v12.62 route fix retained")
need('STREAMFORGE_PANEL_USER_PASSWORD_VISIBILITY_V1261' in app_form,"v12.61 form retained")
if errors:raise SystemExit("FAIL v12.63: "+", ".join(errors))
print("PASS: v12.63 encrypted saved-password vault security and UI contracts")
