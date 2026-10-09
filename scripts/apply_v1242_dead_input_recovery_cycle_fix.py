#!/usr/bin/env python3
"""Refine v12.42 dead-input recovery to count consecutive failed sources.

This follows the first v12.42 materializer. It is safe on both freshly patched
source and an already-materialized hotfix branch.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "app/ffmpeg.py"


def replace_once(old: str, new: str) -> None:
    text = FFMPEG.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{FFMPEG}: expected one refinement anchor, found {count}: {old[:100]!r}")
    FFMPEG.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    'from .source_resolver import is_youtube_url, resolve_stream_source\nfrom .audit_log import log_event\n',
    'from .source_resolver import is_youtube_url, resolve_stream_source\n'
    'from .audit_log import log_event\n'
    'from .recovery_policy import dead_input_cycle_exhausted, dead_input_recovery_seconds\n',
)

old_schedule = '''    # STREAMFORGE_DEAD_INPUT_RECOVERY_INTERVAL_V1242: quick failover stays fast; only a complete dead-input cycle gets the long timer.\n    @staticmethod\n    def _dead_input_recovery_delay(channel: Channel) -> int:\n        try:\n            minutes = int(getattr(channel, "dead_input_recovery_interval", 30) or 30)\n        except (TypeError, ValueError):\n            minutes = 30\n        return max(1, min(1440, minutes)) * 60\n\n    def _schedule_auto_restart(\n        self, channel_id: int, reason: str | None = None, *, full_cycle_exhausted: bool = False\n    ) -> None:\n        """Schedule recovery; use the per-channel long delay after all inputs fail."""\n        with self._lock:\n            if channel_id in self._intentional_stops:\n                return\n            existing = self._restart_timers.get(channel_id)\n            if existing and existing.is_alive():\n                return\n            attempt = self._restart_attempts.get(channel_id, 0) + 1\n            self._restart_attempts[channel_id] = attempt\n\n        with SessionLocal() as db:\n            channel = db.get(Channel, channel_id)\n            if not channel or not channel.enabled or not channel.auto_restart:\n                return\n            delay = self._dead_input_recovery_delay(channel) if full_cycle_exhausted else self._restart_delay(attempt)\n            channel.status = "restarting"\n            channel.pid = None\n            channel.live_bitrate_kbps = 0\n            if reason and not channel.last_error:\n                channel.last_error = reason[-4000:]\n            if full_cycle_exhausted:\n                detail = str(channel.last_error or reason or "All configured inputs are unavailable").strip()\n                channel.last_error = (f"{detail} | next full input recovery in {delay // 60} minute(s)")[-4000:]\n            db.commit()\n\n        timer = threading.Timer(delay, self._run_auto_restart, args=(channel_id,))\n        timer.daemon = True\n        with self._lock:\n            if channel_id in self._intentional_stops:\n                return\n            self._restart_timers[channel_id] = timer\n        timer.start()\n'''

new_schedule = '''    # STREAMFORGE_DEAD_INPUT_RECOVERY_INTERVAL_V1242:\n    # Keep normal source-to-source failover fast. After every configured input\n    # has failed consecutively, reset to primary and wait the per-channel long\n    # recovery interval before beginning a fresh input cycle.\n    def _schedule_auto_restart(self, channel_id: int, reason: str | None = None) -> None:\n        with SessionLocal() as db:\n            channel = db.get(Channel, channel_id)\n            if not channel or not channel.enabled or not channel.auto_restart:\n                return\n            source_count = max(1, len(self.input_urls(channel)))\n            configured_recovery = getattr(channel, "dead_input_recovery_interval", 30)\n\n        with self._lock:\n            if channel_id in self._intentional_stops:\n                return\n            # Presence reserves the slot even before Timer.start(), preventing\n            # concurrent FFmpeg waiter/start-error threads from double-scheduling.\n            if self._restart_timers.get(channel_id) is not None:\n                return\n            attempt = self._restart_attempts.get(channel_id, 0) + 1\n            full_cycle_exhausted = dead_input_cycle_exhausted(attempt, source_count)\n            delay = (\n                dead_input_recovery_seconds(configured_recovery)\n                if full_cycle_exhausted\n                else self._restart_delay(attempt)\n            )\n            self._restart_attempts[channel_id] = 0 if full_cycle_exhausted else attempt\n            timer = threading.Timer(delay, self._run_auto_restart, args=(channel_id,))\n            timer.daemon = True\n            self._restart_timers[channel_id] = timer\n\n        with SessionLocal() as db:\n            channel = db.get(Channel, channel_id)\n            if not channel or not channel.enabled or not channel.auto_restart:\n                with self._lock:\n                    self._cancel_restart_timer_locked(channel_id)\n                return\n            with self._lock:\n                if channel_id in self._intentional_stops:\n                    self._cancel_restart_timer_locked(channel_id)\n                    return\n            if full_cycle_exhausted:\n                channel.active_input_index = 0\n            channel.status = "restarting"\n            channel.pid = None\n            channel.live_bitrate_kbps = 0\n            if reason and not channel.last_error:\n                channel.last_error = reason[-4000:]\n            if full_cycle_exhausted:\n                detail = str(channel.last_error or reason or "All configured inputs are unavailable").strip()\n                channel.last_error = (\n                    f"{detail} | all {source_count} input(s) failed; "\n                    f"next full input recovery in {delay // 60} minute(s)"\n                )[-4000:]\n            db.commit()\n\n        timer.start()\n'''
replace_once(old_schedule, new_schedule)

replace_once(
    '''        if should_restart:\n            advanced = self._advance_input(channel_id)\n            # A single-input channel has no next input; a multi-input channel\n            # completes one dead cycle when failover wraps back to input #1.\n            full_cycle_exhausted = advanced is None or int(advanced[0]) == 0\n            self._schedule_auto_restart(\n                channel_id,\n                failure_reason or f"FFmpeg exited with code {return_code}",\n                full_cycle_exhausted=full_cycle_exhausted,\n            )\n''',
    '''        if should_restart:\n            self._advance_input(channel_id)\n            self._schedule_auto_restart(\n                channel_id, failure_reason or f"FFmpeg exited with code {return_code}"\n            )\n''',
)

replace_once(
    '''            self._schedule_auto_restart(channel_id, str(exc))\n''',
    '''            self._advance_input(channel_id)\n            self._schedule_auto_restart(channel_id, str(exc))\n''',
)

print("Applied v12.42 consecutive dead-input cycle refinement")
