#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: migrate_v33_control_scope.py /path/to/streamforge.db", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print("v3.3 control-scope migration skipped: database not found")
        return 0
    con = sqlite3.connect(str(path))
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"channels", "nodes", "channel_nodes"}.issubset(tables):
            print("v3.3 control-scope migration skipped: required tables missing")
            return 0
        channel_cols = {row[1] for row in con.execute("PRAGMA table_info(channels)")}
        if "desired_running" not in channel_cols:
            print("v3.3 control-scope migration skipped: desired_running missing")
            return 0
        # Main desired_running now means Main Local FFmpeg only. Remote Node
        # desired state lives in each Node state.json and is intentionally not
        # touched here. Clear stale Main intent for remote-only channels.
        assignments = "SELECT cn.channel_id FROM channel_nodes cn JOIN nodes n ON n.id=cn.node_id WHERE n.node_type='local'"
        legacy_primary = "SELECT c.id FROM channels c JOIN nodes n ON n.id=c.node_id WHERE n.node_type='local'" if "node_id" in channel_cols else "SELECT -1"
        sets = ["desired_running=0"]
        if "status" in channel_cols:
            sets.append("status='stopped'")
        if "pid" in channel_cols:
            sets.append("pid=NULL")
        if "live_bitrate_kbps" in channel_cols:
            sets.append("live_bitrate_kbps=0")
        cur = con.execute(
            f"UPDATE channels SET {', '.join(sets)} WHERE id NOT IN ({assignments}) AND id NOT IN ({legacy_primary}) AND desired_running<>0"
        )
        con.commit()
        print(f"v3.3 control-scope migration complete; cleared stale Main intent on {cur.rowcount} remote-only channel(s)")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
