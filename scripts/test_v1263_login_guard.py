#!/usr/bin/env python3
"""Functional 5-attempt/3-hour persistent per-Panel-user login throttle."""
import os
import stat
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix="sf-login-guard-test-") as tmp:
    os.environ["STREAMFORGE_PANEL_LOGIN_GUARD_DB"] = os.path.join(tmp, "guard.sqlite3")
    from app import panel_login_guard as guard
    assert guard.FAILED_ATTEMPTS == 5
    assert guard.ATTEMPT_WINDOW_SECONDS == 15 * 60
    assert guard.LOGIN_LOCK_SECONDS == 3 * 60 * 60
    actor = 47
    now = 2_000_000_000

    with patch.object(guard.time, "time", return_value=now):
        assert not guard.login_locked(actor)
        for attempt in range(1, 5):
            assert not guard.record_login_failure(actor), attempt
            assert not guard.login_locked(actor)
        # Main and remote Node login use this same actor ID.
        assert guard.record_login_failure(actor) is True
        assert guard.login_locked(actor)
        assert not guard.login_locked(actor + 1)
        guard.clear_login_failures(actor)  # success cannot clear an active lock
        assert guard.login_locked(actor)
        with guard._connection() as c:
            row = c.execute(
                "SELECT failures, blocked_until FROM login_failures WHERE actor_id=?", (actor,)
            ).fetchone()
            assert row == (5, now + 10800)
        assert stat.S_IMODE(os.stat(os.environ["STREAMFORGE_PANEL_LOGIN_GUARD_DB"]).st_mode) == 0o600
    with patch.object(guard.time, "time", return_value=now + 10799):
        assert guard.login_locked(actor)
        assert guard.record_login_failure(actor) is True
    with patch.object(guard.time, "time", return_value=now + 10800):
        assert not guard.login_locked(actor)
        guard.clear_login_failures(actor)
        assert not guard.login_locked(actor)
        assert not guard.record_login_failure(actor)
        assert not guard.login_locked(actor)
        assert guard.unlock_login_user(actor) is False
    with patch.object(guard.time, "time", return_value=now + 10801):
        # The previous test recorded one failure immediately after lock expiry.
        assert not guard.record_login_failure(actor)  # second
        assert not guard.record_login_failure(actor)  # third
        assert not guard.record_login_failure(actor)  # fourth
        assert guard.record_login_failure(actor) is True  # fifth
        assert guard.unlock_login_user(actor) is True
        assert not guard.login_locked(actor)
        assert not guard.unlock_login_user(actor)
    # The 15-minute failure window resets when spread-out attempts age out.
    with patch.object(guard.time, "time", return_value=now + 30000):
        guard.clear_login_failures(actor)
        for _ in range(4):
            assert not guard.record_login_failure(actor)
    with patch.object(guard.time, "time", return_value=now + 30901):
        assert not guard.record_login_failure(actor)
    # A modified world-readable guard DB must fail closed.
    os.chmod(os.environ["STREAMFORGE_PANEL_LOGIN_GUARD_DB"], 0o644)
    try:
        guard.login_locked(actor)
        raise AssertionError("unsafe guard file should reject login check")
    except guard.LoginGuardUnavailable:
        pass
print("PASS: Main/Node shared Panel login guard, 5 attempts, 3-hour lock, expiry, unlock, file permissions")
