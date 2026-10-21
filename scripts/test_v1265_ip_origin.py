#!/usr/bin/env python3
"""Functional persistent IP-block source-server provenance and compatibility tests."""
import os
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix="sf-block-source-test-") as temp:
    os.environ["STREAMFORGE_PANEL_LOGIN_GUARD_DB"] = os.path.join(temp, "guard.sqlite3")
    from app import panel_login_guard as g
    ip = "198.51.100.25"
    ts = 2_000_000_000
    with patch.object(g.time, "time", return_value=ts):
        for i in range(4):
            assert not g.record_ip_failure(ip, origin="main")
        # The fifth failure is on a Node. Earlier failures may be elsewhere.
        assert g.record_ip_failure(ip, origin="node", node_id=12, node_name="CNBOX")
        entry = g.list_locked_ips()[0]
        assert entry["ip"] == ip
        assert entry["blocked_by_server"] == "Remote Node: CNBOX"
        assert entry["blocked_by_node_id"] == 12
        assert entry["failed_attempts"] == 5
        # More wrong logins while blocked cannot overwrite provenance or deadline.
        assert g.record_ip_failure(ip, origin="main")
        assert g.list_locked_ips()[0] == entry
        g.clear_ip_failures(ip)
        assert g.list_locked_ips()[0] == entry
        assert g.unlock_ip(ip)
        assert not g.list_locked_ips()
        # Unknown username attempts at Main still get Main provenance.
        for _ in range(5):
            g.record_ip_failure(ip, origin="main")
        entry = g.list_locked_ips()[0]
        assert entry["blocked_by_server"] == "Main Server"
        assert entry["blocked_by_node_id"] is None
        assert g.unlock_ip(ip)

        # Existing v12.64 lock without origin metadata cannot be reconstructed.
        with g._connection() as c:
            c.execute(
                "INSERT INTO ip_failures VALUES(?,?,?,?)",
                ("198.51.100.77", ts, 5, ts + 10800),
            )
        old = g.list_locked_ips()[0]
        assert old["blocked_by_server"] == "Unknown (blocked before tracking)"
        assert old["blocked_by_node_id"] is None
        assert g.unlock_ip("198.51.100.77")
        # Invalid Node origin cannot spoof a provenance row.
        try:
            g.record_ip_failure(ip, origin="node", node_id=None, node_name="CNBOX")
            raise AssertionError("Expected invalid origin to fail closed")
        except g.LoginGuardUnavailable:
            pass

    with patch.object(g.time, "time", return_value=ts + 2 * 10800):
        for _ in range(5):
            g.record_ip_failure(ip, origin="node", node_id=6, node_name="Air Play")
        assert g.list_locked_ips()[0]["blocked_by_server"] == "Remote Node: Air Play"
        with patch.object(g.time, "time", return_value=ts + 3 * 10800):
            # Expired lock is gone; new window resets and must not display
            # obsolete source metadata when later reblocked.
            assert not g.record_ip_failure(ip, origin="main")
            for _ in range(4):
                g.record_ip_failure(ip, origin="main")
            assert g.list_locked_ips()[0]["blocked_by_server"] == "Main Server"
print("PASS: v12.65 fifth-failure origin, Main/Node attribution, legacy blocks, unlock, expiry")
