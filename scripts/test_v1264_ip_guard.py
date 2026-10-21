#!/usr/bin/env python3
"""Main + Node share IP-based 5-bad-login/3-hour guard; no account lock."""
from __future__ import annotations
import os
import stat
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="sf-v1264-ip-guard-") as tmp:
    os.environ["STREAMFORGE_PANEL_LOGIN_GUARD_DB"] = os.path.join(tmp, "guard.sqlite3")
    from app import panel_login_guard as guard
    assert (guard.FAILED_ATTEMPTS, guard.ATTEMPT_WINDOW_SECONDS,
            guard.LOGIN_LOCK_SECONDS) == (5, 900, 10800)
    now = 2_000_000_000

    def request(peer, headers, server=None):
        return SimpleNamespace(
            client=SimpleNamespace(host=peer), headers=headers,
            scope={"server": server} if server else {},
        )

    # Browser-forged first XFF is ignored. Only the last hop sent from loopback
    # Nginx is used. Direct remote clients cannot supply a trusted XFF value.
    a = "198.51.100.18"
    b = "198.51.100.19"
    assert guard.main_panel_client_ip(
        request("127.0.0.1", {"x-forwarded-for": "203.0.113.4, " + a})
    ) == a
    try:
        guard.main_panel_client_ip(request(a, {"x-forwarded-for": b}))
        raise AssertionError("untrusted direct-XFF request must fail closed")
    except guard.LoginGuardUnavailable:
        pass
    # Uvicorn may already have changed request.client to the untrusted FIRST
    # XFF hop; the backend loopback bind still identifies the Nginx proxy.
    assert guard.main_panel_client_ip(
        request("203.0.113.4", {"x-forwarded-for": "203.0.113.4, " + a},
                ("127.0.0.1", 8800))
    ) == a
    try:
        guard.main_panel_client_ip(
            request(a, {"x-forwarded-for": b}, ("0.0.0.0", 8800))
        )
        raise AssertionError("publicly bound backend must reject forwarded spoofing")
    except guard.LoginGuardUnavailable:
        pass
    assert guard.normalize_client_ip("::ffff:" + a) == a
    assert guard.normalize_client_ip("2001:0db8::2") == "2001:db8::2"
    try:
        guard.normalize_client_ip("unknown")
        raise AssertionError("invalid source should fail closed")
    except guard.LoginGuardUnavailable:
        pass

    # First 4 failures from the same IP, including unknown usernames, are
    # ordinary invalid logins; the fifth blocks only the source IP.
    with patch.object(guard.time, "time", return_value=now):
        assert not guard.ip_locked(a)
        for attempt in range(1, 5):
            assert guard.record_ip_failure(a) is False, attempt
        assert guard.record_ip_failure(a) is True
        assert guard.ip_locked(a)
        assert not guard.ip_locked(b)
        assert guard.list_locked_ips() == [{
            "ip": a, "failed_attempts": 5,
            "blocked_until": now + 10800, "remaining_seconds": 10800,
        }]
        guard.clear_ip_failures(a)
        assert guard.ip_locked(a)
        assert guard.record_ip_failure(a)
        assert guard.list_locked_ips()[0]["blocked_until"] == now + 10800
        assert stat.S_IMODE(os.stat(os.environ["STREAMFORGE_PANEL_LOGIN_GUARD_DB"]).st_mode) == 0o600

        # Separate IP can still authenticate and clear its own failed attempts.
        guard.record_ip_failure(b)
        guard.clear_ip_failures(b)
        assert not guard.ip_locked(b)

    with patch.object(guard.time, "time", return_value=now + 10799):
        assert guard.ip_locked(a)
    with patch.object(guard.time, "time", return_value=now + 10800):
        assert not guard.ip_locked(a)
        assert guard.list_locked_ips() == []
        guard.clear_ip_failures(a)
        assert not guard.record_ip_failure(a)
        assert not guard.unlock_ip(a)
    with patch.object(guard.time, "time", return_value=now + 10801):
        for _ in range(3):
            assert not guard.record_ip_failure(a)
        assert guard.record_ip_failure(a)
        assert guard.ip_locked(a)
        assert guard.unlock_ip(a)
        assert not guard.ip_locked(a)
        assert not guard.unlock_ip(a)

    # IPv6 counters and canonicalization cannot bypass an IP block.
    with patch.object(guard.time, "time", return_value=now + 13000):
        for _ in range(5):
            guard.record_ip_failure("2001:0db8::2")
        assert guard.ip_locked("2001:db8::2")
        assert guard.unlock_ip("2001:db8::2")
        assert not guard.ip_locked("2001:db8::2")

    # Existing v12.63 actor-ID counters are not inherited or deleted.
    with guard._connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS login_failures (actor_id INTEGER PRIMARY KEY, window_start INTEGER, failures INTEGER, blocked_until INTEGER)")
        conn.execute("INSERT INTO login_failures VALUES (7, 1, 5, 2147483647)")
    assert not guard.ip_locked(b)
    os.chmod(os.environ["STREAMFORGE_PANEL_LOGIN_GUARD_DB"], 0o644)
    try:
        guard.ip_locked(a)
        raise AssertionError("unsafe guard file should fail closed")
    except guard.LoginGuardUnavailable:
        pass
print("PASS: IP-based 5/15min/3h lock, proxy trust, IPv6, unlock, NAT-independent accounts and legacy DB")
