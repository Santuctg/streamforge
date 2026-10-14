#!/usr/bin/env python3
from __future__ import annotations

import ast
import sys
from pathlib import Path

MARKER = "STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251"

RUNTIME_OLD = '''        self.relay_health_checked_at = 0.0\n        self.relay_health_ready = False\n        self.relay_health_detail = ""\n        self.relay_health_log_at = 0.0\n'''
RUNTIME_NEW = '''        # STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251:\n        # A channel may have only one Main-relay health request in flight.\n        self.relay_health_probe_in_flight = False\n        self.relay_health_checked_at = 0.0\n        self.relay_health_ready = False\n        self.relay_health_detail = ""\n        self.relay_health_log_at = 0.0\n'''

START_OLD = '''            rt.relay_health_checked_at = 0.0\n            rt.relay_health_ready = False\n            rt.relay_health_detail = ""\n            rt.relay_health_log_at = 0.0\n'''
START_NEW = '''            rt.relay_health_probe_in_flight = False\n            rt.relay_health_checked_at = 0.0\n            rt.relay_health_ready = False\n            rt.relay_health_detail = ""\n            rt.relay_health_log_at = 0.0\n'''

STALL_SELECT_OLD = '''                    if now - rt.relay_health_checked_at < RELAY_HEALTH_PROBE_INTERVAL_SECONDS:\n                        # The last probe still says Main relay is unavailable, so\n                        # a local process restart would only add recovery backoff.\n                        if not rt.relay_health_ready:\n                            continue\n                    else:\n                        relay_probe_url = inputs[active_index]\n'''
STALL_SELECT_NEW = '''                    if now - rt.relay_health_checked_at < RELAY_HEALTH_PROBE_INTERVAL_SECONDS:\n                        # The last probe still says Main relay is unavailable, so\n                        # a local process restart would only add recovery backoff.\n                        if not rt.relay_health_ready:\n                            continue\n                    else:\n                        # STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251:\n                        # The stall watchdog and recovery timer share one per-channel\n                        # probe reservation so they can never hammer Main in parallel.\n                        if rt.relay_health_probe_in_flight:\n                            continue\n                        rt.relay_health_probe_in_flight = True\n                        relay_probe_url = inputs[active_index]\n'''

STALL_RESULT_OLD = '''                with self.lock:\n                    rt = self.channels.get(key)\n                    if not rt or rt.process is not process or process.poll() is not None or rt.intentional_stop:\n                        return\n                    now = time.monotonic()\n'''
STALL_RESULT_NEW = '''                with self.lock:\n                    rt = self.channels.get(key)\n                    if rt:\n                        rt.relay_health_probe_in_flight = False\n                    if not rt or rt.process is not process or process.poll() is not None or rt.intentional_stop:\n                        return\n                    now = time.monotonic()\n'''

RECOVER_NEW = '''    def _recover(self, key: str) -> None:\n        relay_probe_url = ""\n        relay_mode = False\n        timer_to_start: threading.Timer | None = None\n        current_timer = threading.current_thread()\n        config: ChannelConfig | None = None\n\n        with self.lock:\n            rt = self.channels.get(key)\n            if not rt or rt.intentional_stop:\n                return\n            # STREAMFORGE_NODE_RELAY_HEALTH_PROBE_SINGLE_OWNER_V1251:\n            # Every scheduled recovery owns the exact Timer object stored on the\n            # Runtime. A stale Timer must never clear or replace a newer owner's\n            # reservation; that race previously multiplied health probes.\n            if rt.restart_timer is not current_timer:\n                return\n            config = rt.config\n            relay_mode = str(config.input_mode or "").strip().lower() == "local_relay"\n            if relay_mode:\n                inputs = self.config_inputs(config)\n                if inputs:\n                    active_index = max(0, min(int(rt.active_input_index or 0), len(inputs) - 1))\n                    relay_probe_url = inputs[active_index]\n                if relay_probe_url:\n                    if rt.relay_health_probe_in_flight:\n                        timer_to_start = threading.Timer(\n                            RELAY_RECOVERY_PROBE_SECONDS, self._recover, args=(key,)\n                        )\n                        timer_to_start.daemon = True\n                        rt.restart_timer = timer_to_start\n                    else:\n                        rt.relay_health_probe_in_flight = True\n\n        if timer_to_start is not None:\n            timer_to_start.start()\n            return\n\n        if relay_mode and relay_probe_url:\n            relay_ready, relay_detail = self.relay_playlist_health(relay_probe_url)\n            now = time.monotonic()\n            should_log = False\n            with self.lock:\n                rt = self.channels.get(key)\n                if rt:\n                    rt.relay_health_probe_in_flight = False\n                if not rt or rt.intentional_stop or not rt.desired_running or not rt.config.enabled:\n                    return\n                # A manual action or newer recovery may have replaced this owner\n                # while the HTTP request was in flight. Ignore the stale result.\n                if rt.restart_timer is not current_timer:\n                    return\n                rt.relay_health_checked_at = now\n                rt.relay_health_ready = relay_ready\n                rt.relay_health_detail = relay_detail\n                if not relay_ready:\n                    rt.status = "waiting"\n                    if rt.waiting_since <= 0:\n                        rt.waiting_since = now\n                    if now - rt.relay_health_log_at >= 60.0:\n                        rt.relay_health_log_at = now\n                        should_log = True\n                    timer = threading.Timer(RELAY_RECOVERY_PROBE_SECONDS, self._recover, args=(key,))\n                    timer.daemon = True\n                    rt.restart_timer = timer\n                else:\n                    timer = None\n                    rt.restart_timer = None\n            if not relay_ready:\n                if should_log:\n                    self.log(\n                        "Main relay unavailable; automatic FFmpeg respawn deferred",\n                        scope="channel", level="warning", key=key, details=relay_detail,\n                    )\n                self.save()\n                timer.start()\n                return\n        else:\n            # Non-relay recovery, or a relay without a probe URL, can release the\n            # Timer reservation immediately before entering the normal start path.\n            with self.lock:\n                rt = self.channels.get(key)\n                if not rt or rt.intentional_stop or rt.restart_timer is not current_timer:\n                    return\n                rt.restart_timer = None\n\n        assert config is not None\n        try:\n            # STREAMFORGE_NODE_GLOBAL_AUTO_START_PACER_V1052:\n            # Synchronized retry timers share the same automatic-start budget.\n            self._paced_automatic_start(key, config)\n        except Exception:\n            with self.lock:\n                rt = self.channels.get(key)\n                if rt and not rt.intentional_stop:\n                    rt.status = "restarting"\n                    rt.restart_attempt += 1\n                    if str(rt.config.input_mode or "").strip().lower() == "local_relay":\n                        delay = RELAY_RECOVERY_PROBE_SECONDS\n                    else:\n                        delay = [3, 5, 10, 20, 30, 60][min(rt.restart_attempt, 6) - 1]\n                    rt.restart_timer = threading.Timer(delay, self._recover, args=(key,))\n                    rt.restart_timer.daemon = True\n                    rt.restart_timer.start()\n'''


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def apply(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return False

    tree = ast.parse(text)
    recover = next(
        (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_recover"),
        None,
    )
    if recover is None:
        raise RuntimeError("_recover function missing")

    text = _replace_once(text, RUNTIME_OLD, RUNTIME_NEW, "Runtime relay health state")
    text = _replace_once(text, START_OLD, START_NEW, "start relay health reset")
    text = _replace_once(text, STALL_SELECT_OLD, STALL_SELECT_NEW, "watch_stall probe reservation")
    text = _replace_once(text, STALL_RESULT_OLD, STALL_RESULT_NEW, "watch_stall probe release")

    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    recover = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_recover"
    )
    start = recover.lineno - 1
    end = int(recover.end_lineno or recover.lineno)
    replacement = RECOVER_NEW.splitlines(keepends=True)
    if replacement and not replacement[-1].endswith("\n"):
        replacement[-1] += "\n"
    lines[start:end] = replacement
    updated = "".join(lines)
    ast.parse(updated)
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} PATH_TO_NODE_APP_PY", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2
    try:
        changed = apply(path)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("CHANGED" if changed else "ALREADY_FIXED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
