#!/usr/bin/env python3
"""Apply the v12.42 per-channel dead-input recovery interval source changes.

This patcher is intentionally idempotent. It is used by CI to materialize the
source changes on the hotfix branch and by the release updater as an additional
safety check for staged packages.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
MARKER = "STREAMFORGE_DEAD_INPUT_RECOVERY_INTERVAL_V1242"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one anchor, found {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all(path: Path, old: str, new: str, expected: int) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text and old not in text:
        return
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{path}: expected {expected} anchors, found {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")


models = ROOT / "app/models.py"
replace_once(
    models,
    '    failback_interval: Mapped[int] = mapped_column(Integer, default=30)\n',
    '    failback_interval: Mapped[int] = mapped_column(Integer, default=30)\n'
    f'    # {MARKER}: minutes to wait after every configured input has failed once.\n'
    '    dead_input_recovery_interval: Mapped[int] = mapped_column(Integer, default=30)\n',
)

db = ROOT / "app/db.py"
replace_once(
    db,
    '        if "failback_interval" not in columns:\n'
    '            statements.append("ALTER TABLE channels ADD COLUMN failback_interval INTEGER NOT NULL DEFAULT 30")\n',
    '        if "failback_interval" not in columns:\n'
    '            statements.append("ALTER TABLE channels ADD COLUMN failback_interval INTEGER NOT NULL DEFAULT 30")\n'
    f'        # {MARKER}: additive per-channel long recovery interval, stored in minutes.\n'
    '        if "dead_input_recovery_interval" not in columns:\n'
    '            statements.append("ALTER TABLE channels ADD COLUMN dead_input_recovery_interval INTEGER NOT NULL DEFAULT 30")\n',
)

main = ROOT / "app/main.py"
replace_all(
    main,
    '    failback_interval: int = Form(30),\n',
    '    failback_interval: int = Form(30),\n'
    '    dead_input_recovery_interval: int = Form(30),\n',
    2,
)
replace_once(
    main,
    'name=name.strip(), slug=final_slug, input_url=input_url.strip(), backup_inputs=backup_inputs.strip() or None, active_input_index=0, failback_enabled=as_bool(failback_enabled), failback_interval=max(10, min(3600, int(failback_interval or 30))), logo_url=final_logo,',
    'name=name.strip(), slug=final_slug, input_url=input_url.strip(), backup_inputs=backup_inputs.strip() or None, active_input_index=0, failback_enabled=as_bool(failback_enabled), failback_interval=max(10, min(3600, int(failback_interval or 30))), dead_input_recovery_interval=max(1, min(1440, int(dead_input_recovery_interval or 30))), logo_url=final_logo,',
)
replace_once(
    main,
    '        channel.failback_interval = max(10, min(3600, int(failback_interval or 30)))\n        channel.logo_url = final_logo\n',
    '        channel.failback_interval = max(10, min(3600, int(failback_interval or 30)))\n'
    '        channel.dead_input_recovery_interval = max(1, min(1440, int(dead_input_recovery_interval or 30)))\n'
    '        channel.logo_url = final_logo\n',
)

template = ROOT / "app/templates/channel_form.html"
replace_once(
    template,
    '  <label class="compact-field interval-field">Primary-input check interval<input type="number" name="failback_interval" min="10" max="3600" value="{{ channel.failback_interval if channel else 30 }}"></label>\n',
    '  <label class="compact-field interval-field">Primary-input check interval<input type="number" name="failback_interval" min="10" max="3600" value="{{ channel.failback_interval if channel else 30 }}"><small class="field-help">Seconds between primary failback probes while a backup is live.</small></label>\n'
    f'  <!-- {MARKER}: long retry window after every configured input is unavailable. -->\n'
    '  <label class="compact-field interval-field">Dead-input recovery interval (minutes)<input type="number" name="dead_input_recovery_interval" min="1" max="1440" step="1" list="dead-input-recovery-presets" value="{{ channel.dead_input_recovery_interval if channel else 30 }}"><datalist id="dead-input-recovery-presets"><option value="5"><option value="10"><option value="15"><option value="30"><option value="60"></datalist><small class="field-help">After all inputs fail once, retry the cycle after this delay. Default 30 minutes.</small></label>\n',
)

ffmpeg = ROOT / "app/ffmpeg.py"
replace_once(
    ffmpeg,
    '    def _schedule_auto_restart(self, channel_id: int, reason: str | None = None) -> None:\n'
    '        """Schedule one recovery attempt with bounded exponential backoff."""\n'
    '        with self._lock:\n'
    '            if channel_id in self._intentional_stops:\n'
    '                return\n'
    '            existing = self._restart_timers.get(channel_id)\n'
    '            if existing and existing.is_alive():\n'
    '                return\n'
    '            attempt = self._restart_attempts.get(channel_id, 0) + 1\n'
    '            self._restart_attempts[channel_id] = attempt\n'
    '            delay = self._restart_delay(attempt)\n'
    '            timer = threading.Timer(delay, self._run_auto_restart, args=(channel_id,))\n'
    '            timer.daemon = True\n'
    '            self._restart_timers[channel_id] = timer\n\n'
    '        with SessionLocal() as db:\n'
    '            channel = db.get(Channel, channel_id)\n'
    '            if not channel or not channel.enabled or not channel.auto_restart:\n'
    '                with self._lock:\n'
    '                    self._cancel_restart_timer_locked(channel_id)\n'
    '                return\n'
    '            channel.status = "restarting"\n'
    '            channel.pid = None\n'
    '            channel.live_bitrate_kbps = 0\n'
    '            if reason and not channel.last_error:\n'
    '                channel.last_error = reason[-4000:]\n'
    '            db.commit()\n'
    '        timer.start()\n',
    f'    # {MARKER}: quick failover stays fast; only a complete dead-input cycle gets the long timer.\n'
    '    @staticmethod\n'
    '    def _dead_input_recovery_delay(channel: Channel) -> int:\n'
    '        try:\n'
    '            minutes = int(getattr(channel, "dead_input_recovery_interval", 30) or 30)\n'
    '        except (TypeError, ValueError):\n'
    '            minutes = 30\n'
    '        return max(1, min(1440, minutes)) * 60\n\n'
    '    def _schedule_auto_restart(\n'
    '        self, channel_id: int, reason: str | None = None, *, full_cycle_exhausted: bool = False\n'
    '    ) -> None:\n'
    '        """Schedule recovery; use the per-channel long delay after all inputs fail."""\n'
    '        with self._lock:\n'
    '            if channel_id in self._intentional_stops:\n'
    '                return\n'
    '            existing = self._restart_timers.get(channel_id)\n'
    '            if existing and existing.is_alive():\n'
    '                return\n'
    '            attempt = self._restart_attempts.get(channel_id, 0) + 1\n'
    '            self._restart_attempts[channel_id] = attempt\n\n'
    '        with SessionLocal() as db:\n'
    '            channel = db.get(Channel, channel_id)\n'
    '            if not channel or not channel.enabled or not channel.auto_restart:\n'
    '                return\n'
    '            delay = self._dead_input_recovery_delay(channel) if full_cycle_exhausted else self._restart_delay(attempt)\n'
    '            channel.status = "restarting"\n'
    '            channel.pid = None\n'
    '            channel.live_bitrate_kbps = 0\n'
    '            if reason and not channel.last_error:\n'
    '                channel.last_error = reason[-4000:]\n'
    '            if full_cycle_exhausted:\n'
    '                detail = str(channel.last_error or reason or "All configured inputs are unavailable").strip()\n'
    '                channel.last_error = (f"{detail} | next full input recovery in {delay // 60} minute(s)")[-4000:]\n'
    '            db.commit()\n\n'
    '        timer = threading.Timer(delay, self._run_auto_restart, args=(channel_id,))\n'
    '        timer.daemon = True\n'
    '        with self._lock:\n'
    '            if channel_id in self._intentional_stops:\n'
    '                return\n'
    '            self._restart_timers[channel_id] = timer\n'
    '        timer.start()\n',
)
replace_once(
    ffmpeg,
    '        if should_restart:\n'
    '            self._advance_input(channel_id)\n'
    '            self._schedule_auto_restart(channel_id, failure_reason or f"FFmpeg exited with code {return_code}")\n',
    '        if should_restart:\n'
    '            advanced = self._advance_input(channel_id)\n'
    '            # A single-input channel has no next input; a multi-input channel\n'
    '            # completes one dead cycle when failover wraps back to input #1.\n'
    '            full_cycle_exhausted = advanced is None or int(advanced[0]) == 0\n'
    '            self._schedule_auto_restart(\n'
    '                channel_id,\n'
    '                failure_reason or f"FFmpeg exited with code {return_code}",\n'
    '                full_cycle_exhausted=full_cycle_exhausted,\n'
    '            )\n',
)

print(f"Applied {MARKER} to {ROOT}")
