#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
MARKER = "STREAMFORGE_MAIN_WAITING_RECOVERY_V1253"


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def rep(rel: str, old: str, new: str, *, expected: int | None = None) -> None:
    text = read(rel)
    count = text.count(old)
    if count < 1:
        raise SystemExit(f"{rel}: patch anchor not found: {old[:120]!r}")
    if expected is not None and count != expected:
        raise SystemExit(f"{rel}: expected {expected} anchors, found {count}: {old[:120]!r}")
    write(rel, text.replace(old, new, 1 if expected is None else expected))


def rep_all(rel: str, old: str, new: str, expected: int) -> None:
    text = read(rel)
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{rel}: expected {expected} anchors, found {count}: {old[:120]!r}")
    write(rel, text.replace(old, new))


def rex(rel: str, pattern: str, replacement: str) -> None:
    text = read(rel)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{rel}: regex anchor matched {count} time(s): {pattern[:120]!r}")
    write(rel, updated)


required = {
    "app/recovery_policy.py": "MAIN_WAITING_RETRY_SECONDS = 60",
    "app/models.py": MARKER,
    "app/db.py": MARKER,
    "app/ffmpeg.py": MARKER,
    "app/main.py": MARKER,
    "app/static/app.js": MARKER,
    "app/templates/channels.html": MARKER,
}
if all(token in read(path) for path, token in required.items()):
    print("ALREADY_FIXED")
    raise SystemExit(0)

rep(
    "app/recovery_policy.py",
    "MAX_DEAD_INPUT_RECOVERY_MINUTES = 1440\n",
    "MAX_DEAD_INPUT_RECOVERY_MINUTES = 1440\n\n"
    "# STREAMFORGE_MAIN_WAITING_RECOVERY_V1253:\n"
    "# Main full-input recovery retries once per minute until delivery is Up.\n"
    "MAIN_WAITING_RETRY_SECONDS = 60\n",
    expected=1,
)

rep(
    "app/models.py",
    "    # STREAMFORGE_DEAD_INPUT_RECOVERY_INTERVAL_V1242: minutes to wait after every configured input has failed once.\n"
    "    dead_input_recovery_interval: Mapped[int] = mapped_column(Integer, default=30)\n"
    "    logo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)\n",
    "    # STREAMFORGE_DEAD_INPUT_RECOVERY_INTERVAL_V1242: retained for configuration/backward compatibility.\n"
    "    dead_input_recovery_interval: Mapped[int] = mapped_column(Integer, default=30)\n"
    "    # STREAMFORGE_MAIN_WAITING_RECOVERY_V1253:\n"
    "    waiting_since: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)\n"
    "    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)\n"
    "    logo_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)\n",
    expected=1,
)

rep(
    "app/db.py",
    "        # STREAMFORGE_DEAD_INPUT_RECOVERY_INTERVAL_V1242: additive per-channel long recovery interval, stored in minutes.\n"
    "        if \"dead_input_recovery_interval\" not in columns:\n"
    "            statements.append(\"ALTER TABLE channels ADD COLUMN dead_input_recovery_interval INTEGER NOT NULL DEFAULT 30\")\n",
    "        # STREAMFORGE_DEAD_INPUT_RECOVERY_INTERVAL_V1242: additive legacy/configuration field.\n"
    "        if \"dead_input_recovery_interval\" not in columns:\n"
    "            statements.append(\"ALTER TABLE channels ADD COLUMN dead_input_recovery_interval INTEGER NOT NULL DEFAULT 30\")\n"
    "        # STREAMFORGE_MAIN_WAITING_RECOVERY_V1253: persistent Main outage/retry clocks.\n"
    "        if \"waiting_since\" not in columns:\n"
    "            statements.append(\"ALTER TABLE channels ADD COLUMN waiting_since DATETIME\" if dialect == \"sqlite\" else \"ALTER TABLE channels ADD COLUMN waiting_since TIMESTAMP WITH TIME ZONE\")\n"
    "        if \"next_retry_at\" not in columns:\n"
    "            statements.append(\"ALTER TABLE channels ADD COLUMN next_retry_at DATETIME\" if dialect == \"sqlite\" else \"ALTER TABLE channels ADD COLUMN next_retry_at TIMESTAMP WITH TIME ZONE\")\n",
    expected=1,
)

rep("app/ffmpeg.py", "from collections import deque\nfrom pathlib import Path\n", "from collections import deque\nfrom datetime import datetime, timedelta, timezone\nfrom pathlib import Path\n", expected=1)
rep("app/ffmpeg.py", "from .recovery_policy import dead_input_cycle_exhausted, dead_input_recovery_seconds\n", "from .recovery_policy import MAIN_WAITING_RETRY_SECONDS, dead_input_cycle_exhausted\n", expected=1)
rep(
    "app/ffmpeg.py",
    "        self._restart_attempts: dict[int, int] = {}\n        self._last_auto_restart_monotonic: dict[int, float] = {}\n",
    "        self._restart_attempts: dict[int, int] = {}\n"
    "        # STREAMFORGE_MAIN_WAITING_RECOVERY_V1253: in-memory hint; DB timestamps remain authoritative.\n"
    "        self._waiting_channels: set[int] = set()\n"
    "        self._last_auto_restart_monotonic: dict[int, float] = {}\n",
    expected=1,
)
rep(
    "app/ffmpeg.py",
    "            self._restart_attempts.clear()\n            self._last_auto_restart_monotonic.clear()\n",
    "            self._restart_attempts.clear()\n            self._waiting_channels.clear()\n            self._last_auto_restart_monotonic.clear()\n",
    expected=1,
)

schedule = r'''    # STREAMFORGE_MAIN_WAITING_RECOVERY_V1253:
    # Keep source-to-source failover fast. After all inputs fail, start a new
    # full cycle every 60 seconds. waiting_since is write-once per outage.
    def _schedule_auto_restart(self, channel_id: int, reason: str | None = None) -> None:
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel or not channel.enabled or not channel.auto_restart:
                return
            source_count = max(1, len(self.input_urls(channel)))

        with self._lock:
            if channel_id in self._intentional_stops:
                return
            if self._restart_timers.get(channel_id) is not None:
                return
            attempt = self._restart_attempts.get(channel_id, 0) + 1
            full_cycle_exhausted = dead_input_cycle_exhausted(attempt, source_count)
            delay = MAIN_WAITING_RETRY_SECONDS if full_cycle_exhausted else self._restart_delay(attempt)
            self._restart_attempts[channel_id] = 0 if full_cycle_exhausted else attempt
            timer = threading.Timer(delay, self._run_auto_restart, args=(channel_id,))
            timer.daemon = True
            self._restart_timers[channel_id] = timer

        now_utc = datetime.now(timezone.utc)
        retry_at = now_utc + timedelta(seconds=int(delay))
        with SessionLocal() as db:
            channel = db.get(Channel, channel_id)
            if not channel or not channel.enabled or not channel.auto_restart:
                with self._lock:
                    self._cancel_restart_timer_locked(channel_id)
                return
            with self._lock:
                if channel_id in self._intentional_stops:
                    self._cancel_restart_timer_locked(channel_id)
                    return
                self._waiting_channels.add(channel_id)
            if full_cycle_exhausted:
                channel.active_input_index = 0
            channel.status = "restarting"
            channel.pid = None
            channel.live_bitrate_kbps = 0
            if channel.waiting_since is None:
                channel.waiting_since = now_utc
            channel.next_retry_at = retry_at
            if reason and not channel.last_error:
                channel.last_error = reason[-4000:]
            if full_cycle_exhausted:
                detail = str(channel.last_error or reason or "All configured inputs are unavailable").strip()
                channel.last_error = (
                    f"{detail} | all {source_count} input(s) failed; "
                    f"next full input recovery in {MAIN_WAITING_RETRY_SECONDS} second(s)"
                )[-4000:]
            db.commit()

        timer.start()

    def _run_auto_restart'''
rex("app/ffmpeg.py", r"    # STREAMFORGE_DEAD_INPUT_RECOVERY_INTERVAL_V1242:.*?\n    def _run_auto_restart", schedule)

rep(
    "app/ffmpeg.py",
    "                channel.status = \"starting\"\n                channel.pid = process.pid\n                channel.live_bitrate_kbps = 0\n                channel.last_error = None\n                db.commit()\n",
    "                # STREAMFORGE_MAIN_WAITING_RECOVERY_V1253: retries stay in one Waiting episode.\n"
    "                if channel.waiting_since is None:\n"
    "                    channel.waiting_since = datetime.now(timezone.utc)\n"
    "                channel.next_retry_at = None\n"
    "                self._waiting_channels.add(channel_id)\n"
    "                channel.status = \"starting\"\n"
    "                channel.pid = process.pid\n"
    "                channel.live_bitrate_kbps = 0\n"
    "                channel.last_error = None\n"
    "                db.commit()\n",
    expected=1,
)

rep(
    "app/ffmpeg.py",
    "            # STREAMFORGE_MAIN_RUNTIME_DB_WRITE_THROTTLE_V34:\n            # The panel reads bitrate/speed/fps from memory. Persist bitrate at\n",
    "            # STREAMFORGE_MAIN_WAITING_RECOVERY_V1253:\n"
    "            with self._lock:\n"
    "                waiting_tracked = channel_id in self._waiting_channels\n"
    "            if waiting_tracked and value == \"continue\":\n"
    "                with SessionLocal() as waiting_db:\n"
    "                    waiting_channel = waiting_db.get(Channel, channel_id)\n"
    "                    if waiting_channel:\n"
    "                        delivery_ready = (str(waiting_channel.output_type or \"\").strip().lower() != \"hls\" or self._local_hls_output_fresh(waiting_channel))\n"
    "                        if delivery_ready:\n"
    "                            waiting_channel.waiting_since = None\n"
    "                            waiting_channel.next_retry_at = None\n"
    "                            waiting_db.commit()\n"
    "                            with self._lock:\n"
    "                                self._waiting_channels.discard(channel_id)\n\n"
    "            # STREAMFORGE_MAIN_RUNTIME_DB_WRITE_THROTTLE_V34:\n            # The panel reads bitrate/speed/fps from memory. Persist bitrate at\n",
    expected=1,
)

# The concrete StreamManager stop is identified by its intentional-stop body;
# the later RPC proxy has the same method signature and must stay untouched.
rep(
    "app/ffmpeg.py",
    "    def stop(self, channel_id: int) -> None:\n        with self._lock:\n            self._intentional_stops.add(channel_id)\n",
    "    def stop(self, channel_id: int, *, clear_waiting: bool = True) -> None:\n        with self._lock:\n            self._intentional_stops.add(channel_id)\n",
    expected=1,
)
rep(
    "app/ffmpeg.py",
    "            self._last_auto_restart_monotonic.pop(channel_id, None)\n            process = self._processes.get(channel_id)\n",
    "            self._last_auto_restart_monotonic.pop(channel_id, None)\n"
    "            if clear_waiting:\n"
    "                self._waiting_channels.discard(channel_id)\n"
    "            process = self._processes.get(channel_id)\n",
    expected=1,
)
rep(
    "app/ffmpeg.py",
    "                channel.live_bitrate_kbps = 0\n                db.commit()\n                self._clear_local_hls_output(channel)\n\n\n    def restart(self, channel_id: int) -> None:\n",
    "                channel.live_bitrate_kbps = 0\n"
    "                if clear_waiting:\n"
    "                    channel.waiting_since = None\n"
    "                    channel.next_retry_at = None\n"
    "                else:\n"
    "                    channel.next_retry_at = None\n"
    "                db.commit()\n"
    "                self._clear_local_hls_output(channel)\n\n\n"
    "    def restart(self, channel_id: int) -> None:\n",
    expected=1,
)
rep(
    "app/ffmpeg.py",
    "        self.stop(channel_id)\n        with SessionLocal() as db:\n",
    "        # STREAMFORGE_MAIN_WAITING_RECOVERY_V1253: Restart preserves the outage clock.\n"
    "        self.stop(channel_id, clear_waiting=False)\n"
    "        with SessionLocal() as db:\n",
    expected=1,
)

rep(
    "app/ffmpeg.py",
    "            process = self._processes.get(channel_id)\n            started = self._started_monotonic.get(channel_id)\n",
    "            process = self._processes.get(channel_id)\n"
    "            restart_pending = self._restart_timers.get(channel_id) is not None\n"
    "            started = self._started_monotonic.get(channel_id)\n",
    expected=1,
)
rep(
    "app/ffmpeg.py",
    "                \"managed\": process is not None,\n                \"alive\": alive,\n",
    "                \"managed\": process is not None or restart_pending,\n"
    "                \"restart_pending\": restart_pending,\n"
    "                \"alive\": alive,\n",
    expected=1,
)
rep(
    "app/ffmpeg.py",
    "        \"managed\": False,\n        \"alive\": False,\n",
    "        \"managed\": False,\n        \"restart_pending\": False,\n        \"alive\": False,\n",
)

rep(
    "app/main_channel_supervisor.py",
    "    with manager._lock:  # one bounded lock acquisition for the complete UI poll\n        channel_ids = list(manager._processes)\n",
    "    with manager._lock:  # one bounded lock acquisition for the complete UI poll\n"
    "        # STREAMFORGE_MAIN_WAITING_RECOVERY_V1253: include PID-less scheduled recovery.\n"
    "        channel_ids = sorted(set(manager._processes) | set(manager._restart_timers))\n",
    expected=1,
)

rep(
    "app/main.py",
    "# STREAMFORGE_MAIN_LOCAL_RUNTIME_ONLY_V33:\ndef main_channel_runtime(channel: Channel) -> dict[str, object]:\n",
    "# STREAMFORGE_MAIN_WAITING_RECOVERY_V1253:\n"
    "def main_channel_waiting_timing(channel: Channel) -> tuple[int, int]:\n"
    "    now = datetime.now(timezone.utc)\n"
    "    waiting_since = getattr(channel, \"waiting_since\", None)\n"
    "    next_retry_at = getattr(channel, \"next_retry_at\", None)\n"
    "    if waiting_since is not None and waiting_since.tzinfo is None:\n"
    "        waiting_since = waiting_since.replace(tzinfo=timezone.utc)\n"
    "    if next_retry_at is not None and next_retry_at.tzinfo is None:\n"
    "        next_retry_at = next_retry_at.replace(tzinfo=timezone.utc)\n"
    "    waiting_seconds = max(0, int((now - waiting_since).total_seconds())) if waiting_since else 0\n"
    "    restart_in_seconds = max(0, int(math.ceil((next_retry_at - now).total_seconds()))) if next_retry_at else 0\n"
    "    return waiting_seconds, restart_in_seconds\n\n\n"
    "# STREAMFORGE_MAIN_LOCAL_RUNTIME_ONLY_V33:\n"
    "def main_channel_runtime(channel: Channel) -> dict[str, object]:\n",
    expected=1,
)
rep(
    "app/main.py",
    "    last_error = str(channel.last_error or \"\").strip() if status in {\"error\", \"starting\", \"restarting\"} else \"\"\n    item.update({\n",
    "    waiting_seconds, restart_in_seconds = main_channel_waiting_timing(channel)\n"
    "    last_error = str(channel.last_error or \"\").strip() if status in {\"error\", \"starting\", \"restarting\"} else \"\"\n"
    "    item.update({\n",
    expected=1,
)
rep(
    "app/main.py",
    "        \"hls_ready\": hls_ready,\n        \"node_id\": int(local.id),\n",
    "        \"hls_ready\": hls_ready,\n"
    "        \"waiting_seconds\": waiting_seconds,\n"
    "        \"restart_in_seconds\": restart_in_seconds,\n"
    "        \"node_id\": int(local.id),\n",
    expected=1,
)

rep_all(
    "app/main.py",
    "        else:\n            initial_status = \"down\"\n        initial_channel_runtime[int(channel.id)] = {\n",
    "        else:\n            initial_status = \"down\"\n"
    "        waiting_seconds, restart_in_seconds = main_channel_waiting_timing(channel)\n"
    "        initial_channel_runtime[int(channel.id)] = {\n",
    2,
)
rep_all(
    "app/main.py",
    "            \"uptime_seconds\": int(snapshot.get(\"uptime_seconds\") or 0),\n",
    "            \"uptime_seconds\": int(snapshot.get(\"uptime_seconds\") or 0),\n"
    "            \"waiting_seconds\": waiting_seconds,\n"
    "            \"restart_in_seconds\": restart_in_seconds,\n",
    2,
)
rep(
    "app/main.py",
    "                \"runtime_status\": effective_status,\n",
    "                \"runtime_status\": effective_status,\n"
    "                # STREAMFORGE_MAIN_WAITING_RECOVERY_V1253\n"
    "                \"waiting_seconds\": int(runtime.get(\"waiting_seconds\") or 0),\n"
    "                \"restart_in_seconds\": int(runtime.get(\"restart_in_seconds\") or 0),\n",
    expected=1,
)

rep(
    "app/templates/channels.html",
    "  {% set initial_uptime_seconds = initial_runtime.get('uptime_seconds', 0) %}\n",
    "  {# STREAMFORGE_MAIN_WAITING_RECOVERY_V1253 #}\n"
    "  {% set initial_uptime_seconds = initial_runtime.get('uptime_seconds', 0) %}\n"
    "  {% set initial_waiting_seconds = initial_runtime.get('waiting_seconds', 0) %}\n"
    "  {% set initial_restart_in_seconds = initial_runtime.get('restart_in_seconds', 0) %}\n",
    expected=1,
)
rep(
    "app/templates/channels.html",
    "<b class=\"runtime-uptime\" data-uptime>{{ human_duration(initial_uptime_seconds) if initial_uptime_seconds else '—' }}</b>",
    "<b class=\"runtime-uptime\" data-uptime{% if initial_delivery_status == 'waiting' and initial_restart_in_seconds > 0 %} title=\"Retry in {{ human_duration(initial_restart_in_seconds) }}\"{% endif %}>{{ human_duration(initial_waiting_seconds) if initial_delivery_status == 'waiting' else (human_duration(initial_uptime_seconds) if initial_uptime_seconds else '—') }}</b>",
    expected=1,
)
rep(
    "app/static/app.js",
    "          if (uptime) uptime.textContent = formatUptime(channel.uptime_seconds);\n",
    "          // STREAMFORGE_MAIN_WAITING_RECOVERY_V1253: Waiting uses the continuous outage clock.\n"
    "          if (uptime) {\n"
    "            const waitingSeconds = Number(channel.waiting_seconds || 0);\n"
    "            const restartInSeconds = Number(channel.restart_in_seconds || 0);\n"
    "            const isWaiting = String(channel.status || '').toLowerCase() === 'waiting';\n"
    "            uptime.textContent = isWaiting ? formatUptime(waitingSeconds) : formatUptime(channel.uptime_seconds);\n"
    "            uptime.title = isWaiting && restartInSeconds > 0 ? `Retry in ${formatUptime(restartInSeconds)}` : '';\n"
    "          }\n",
    expected=1,
)

print("PATCHED")
