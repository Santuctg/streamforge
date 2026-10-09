#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.recovery_policy import (  # noqa: E402
    dead_input_cycle_exhausted,
    dead_input_recovery_minutes,
    dead_input_recovery_seconds,
)

for minutes in (5, 10, 15, 30, 60):
    assert dead_input_recovery_minutes(minutes) == minutes
    assert dead_input_recovery_seconds(minutes) == minutes * 60

assert dead_input_recovery_minutes(None) == 30
assert dead_input_recovery_minutes("bad") == 30
assert dead_input_recovery_minutes(0) == 30
assert dead_input_recovery_minutes(-10) == 1
assert dead_input_recovery_minutes(2000) == 1440

assert not dead_input_cycle_exhausted(1, 3)
assert not dead_input_cycle_exhausted(2, 3)
assert dead_input_cycle_exhausted(3, 3)
assert dead_input_cycle_exhausted(1, 1)

# A healthy runtime can begin on any backup. The decision depends on the number
# of consecutive failures since stability, not whether the index wrapped to 0.
assert not dead_input_cycle_exhausted(1, 4)
assert dead_input_cycle_exhausted(4, 4)

print("OK: v12.42 dead-input recovery policy")
