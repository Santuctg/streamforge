#!/usr/bin/env python3
"""v12.75: Main session cookie is Secure on HTTPS requests only; HTTP logins keep working."""
import os, sys, tempfile
from pathlib import Path
d = tempfile.mkdtemp()
os.environ.update(STREAMFORGE_DATABASE_URL=f"sqlite:///{d}/t.db", STREAMFORGE_HLS_ROOT=f"{d}/h",
    STREAMFORGE_LOGO_ROOT=f"{d}/l", STREAMFORGE_NODE_LOGO_ROOT=f"{d}/n", STREAMFORGE_SECRET_KEY="x"*64)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.main as m
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
names = [mw.cls.__name__ for mw in m.app.user_middleware]
print("Main middleware (outermost first):", names)
assert names.index("SecureSessionCookieMiddleware") < names.index("SessionMiddleware")
t = FastAPI()
@t.get("/set")
def set_(request: Request): request.session["admin_id"] = 1; return {"ok": True}
@t.get("/get")
def get_(request: Request): return {"admin_id": request.session.get("admin_id")}
@t.get("/clear")
def clear_(request: Request): request.session.clear(); return {"ok": True}
@t.get("/other")
def other():
    from fastapi.responses import JSONResponse
    r = JSONResponse({}); r.set_cookie("sessionx", "1"); r.set_cookie("other", "1"); return r
t.add_middleware(SessionMiddleware, secret_key="x"*64, same_site="lax", https_only=False, max_age=60)
t.add_middleware(m.SecureSessionCookieMiddleware)
def run(label, base, headers, expect):
    with TestClient(t, base_url=base) as c:
        r = c.get("/set", headers=headers)
        sc = [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie"]
        secure = any("secure" in v.lower() for v in sc)
        c.cookies.set("session", r.cookies.get("session"))
        back = c.get("/get", headers=headers).json()["admin_id"]
        cl = [v for k, v in c.get("/clear", headers=headers).headers.multi_items() if k.lower() == "set-cookie"]
        other = [v for k, v in c.get("/other", headers=headers).headers.multi_items() if k.lower() == "set-cookie"]
        print(f"{label}: secure={secure} session_works={back == 1} clear_secure={any('secure' in v.lower() for v in cl)} other_cookies_untouched={not any('secure' in v.lower() for v in other)}")
        assert secure == expect and back == 1 and not any('secure' in v.lower() for v in other)
        assert any('secure' in v.lower() for v in cl) == expect
run("X-Forwarded-Proto: https", "http://testserver", {"x-forwarded-proto": "https"}, True)
run("X-Forwarded-Proto: https, http (CDN chain)", "http://testserver", {"x-forwarded-proto": "https, http"}, True)
run("X-Forwarded-Proto: http", "http://testserver", {"x-forwarded-proto": "http"}, False)
run("no header, https scheme", "https://testserver", {}, True)
run("no header, http scheme", "http://testserver", {}, False)
print("PASS: v12.75 session cookie Secure on HTTPS only, session usable over HTTP and HTTPS, other cookies untouched")
