from __future__ import annotations

# STREAMFORGE_DEAD_INPUT_RECOVERY_INTERVAL_V1242
DEFAULT_DEAD_INPUT_RECOVERY_MINUTES = 30
MIN_DEAD_INPUT_RECOVERY_MINUTES = 1
MAX_DEAD_INPUT_RECOVERY_MINUTES = 1440


def dead_input_recovery_minutes(value: object) -> int:
    try:
        minutes = int(value or DEFAULT_DEAD_INPUT_RECOVERY_MINUTES)
    except (TypeError, ValueError):
        minutes = DEFAULT_DEAD_INPUT_RECOVERY_MINUTES
    return max(MIN_DEAD_INPUT_RECOVERY_MINUTES, min(MAX_DEAD_INPUT_RECOVERY_MINUTES, minutes))


def dead_input_recovery_seconds(value: object) -> int:
    return dead_input_recovery_minutes(value) * 60


def dead_input_cycle_exhausted(failure_attempt: int, source_count: int) -> bool:
    """True once every configured input has failed in the current outage cycle.

    StreamManager resets its consecutive failure counter after a stable run, so
    this remains correct even when the healthy runtime began on a backup input.
    """
    try:
        attempt = max(1, int(failure_attempt))
    except (TypeError, ValueError):
        attempt = 1
    try:
        count = max(1, int(source_count))
    except (TypeError, ValueError):
        count = 1
    return attempt >= count
