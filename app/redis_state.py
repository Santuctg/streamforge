from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, TypeVar

from .config import settings

try:
    import redis
except Exception:  # pragma: no cover - dependency/runtime safety fallback
    redis = None  # type: ignore[assignment]

T = TypeVar("T")
_LOG = logging.getLogger("streamforge.redis")


class RedisStateManager:
    """Small fail-open Redis runtime-state adapter.

    StreamForge v6.1 keeps SQLite as the source of truth for persistent panel
    data. Redis is used only for short-lived playback/session state. If the
    local Redis service is temporarily unavailable, callers immediately fall
    back to their in-process compatibility state instead of stalling playback.
    """

    def __init__(self) -> None:
        self.enabled = bool(settings.redis_enabled)
        self.url = str(settings.redis_url or "redis://127.0.0.1:6379/0").strip()
        self.prefix = str(settings.redis_prefix or "streamforge").strip(": ") or "streamforge"
        self._client: Any | None = None
        self._lock = threading.RLock()
        self._disabled_until = 0.0
        self._last_warning = 0.0

    def key(self, *parts: object) -> str:
        cleaned = [str(part).strip(":") for part in parts if str(part) != ""]
        return ":".join([self.prefix, *cleaned])

    def _mark_failed(self, exc: BaseException) -> None:
        now = time.monotonic()
        with self._lock:
            self._client = None
            self._disabled_until = now + max(1.0, float(settings.redis_failure_backoff_seconds))
            if now - self._last_warning >= 60.0:
                _LOG.warning("Redis runtime state unavailable; using local compatibility fallback: %s", exc)
                self._last_warning = now

    def _get_client(self) -> Any | None:
        if not self.enabled or redis is None:
            return None
        now = time.monotonic()
        if now < self._disabled_until:
            return None
        with self._lock:
            if self._client is not None:
                return self._client
            try:
                timeout = max(0.05, float(settings.redis_timeout_ms) / 1000.0)
                client = redis.Redis.from_url(
                    self.url,
                    decode_responses=True,
                    socket_connect_timeout=timeout,
                    socket_timeout=timeout,
                    health_check_interval=15,
                    retry_on_timeout=False,
                )
                client.ping()
                self._client = client
                self._disabled_until = 0.0
                return client
            except Exception as exc:  # Redis must never block the playback fallback path.
                self._mark_failed(exc)
                return None

    def call(self, operation: Callable[[Any], T]) -> tuple[bool, T | None]:
        client = self._get_client()
        if client is None:
            return False, None
        try:
            return True, operation(client)
        except Exception as exc:
            self._mark_failed(exc)
            return False, None

    def best_effort_call(self, operation: Callable[[Any], T]) -> tuple[bool, T | None]:
        """Run non-critical Redis bookkeeping without poisoning playback state.

        STREAMFORGE_MAIN_CLIENT_SESSION_HISTORY_ASYNC_V1060: Client-log session
        history is sampled by the single control worker and is never part of a
        playback authorization/segment request.  A history timeout must not put
        the shared Redis adapter into failure backoff, because that could make an
        optional Logs feature influence the media hot path.
        """
        client = self._get_client()
        if client is None:
            return False, None
        try:
            return True, operation(client)
        except Exception:
            return False, None

    def ping(self) -> bool:
        ok, value = self.call(lambda client: bool(client.ping()))
        return bool(ok and value)


redis_state = RedisStateManager()
