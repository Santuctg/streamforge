from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from fastapi import Request

from .redis_state import redis_state

_SESSION_RE = re.compile(r"[^A-Za-z0-9._~-]+")


@dataclass(frozen=True)
class ViewerSession:
    session_id: str
    user_id: int
    client_ip: str
    node_id: int
    channel_id: int
    first_seen_monotonic: float
    last_seen_monotonic: float
    user_agent: str


@dataclass(frozen=True)
class ViewerSnapshot:
    # For operator pages, online counts represent active playback sessions.
    # This deliberately lets two devices behind the same public IP appear as
    # two sessions while still allowing a separate unique-account count.
    total_users: int
    unique_accounts: int
    by_node: dict[int, int]
    by_channel: dict[int, int]
    sessions: tuple[ViewerSession, ...]
    captured_monotonic: float


class ViewerTracker:
    """Track recent playlist/segment activity without database writes.

    STREAMFORGE_REDIS_VIEWER_TRACKER_V61: active viewer state is mirrored into
    Redis when available so future Public workers can share one session view.
    The original in-process tracker remains a fail-open compatibility mirror,
    so a Redis restart does not freeze playback.
    """

    _TOUCH_LUA = r"""
local session_key = KEYS[1]
local global_key = KEYS[2]
local member = ARGV[1]
local now = tonumber(ARGV[2])
local cutoff = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])
-- STREAMFORGE_MAIN_VIEWER_RECONNECT_FIRST_RESET_V1062:
-- The detail hash deliberately outlives the active zset entry by 10 seconds.
-- If playback resumes after the viewer timeout, do not inherit the old
-- generation's first timestamp from that grace-period hash.
local first = redis.call('HGET', session_key, 'first')
local previous_last = tonumber(redis.call('HGET', session_key, 'last') or '0')
if not first or (previous_last > 0 and now - previous_last > ttl) then first = ARGV[2] end
redis.call('HSET', session_key,
  'sid', ARGV[5], 'uid', ARGV[6], 'ip', ARGV[7],
  'node', ARGV[8], 'channel', ARGV[9], 'first', first,
  'last', ARGV[2], 'ua', ARGV[10])
redis.call('EXPIRE', session_key, ttl + 10)
redis.call('ZADD', global_key, now, member)
redis.call('ZREMRANGEBYSCORE', global_key, '-inf', cutoff)
redis.call('EXPIRE', global_key, math.max(60, ttl * 3))
return 1
"""

    # STREAMFORGE_MAIN_CLIENT_SESSION_HISTORY_ASYNC_V1060:
    # Session-age history is persisted only by the single Main control worker,
    # never by Public playback workers.  The short-lived current-generation
    # pointer splits reconnects into separate generations while the retained hash
    # keeps the final age available after the active viewer TTL expires.
    _HISTORY_TOUCH_LUA = r"""
local now = tonumber(ARGV[1])
local active_ttl = math.max(5, tonumber(ARGV[2]))
local history_ttl = math.max(300, tonumber(ARGV[3]))
local killed_at = tonumber(redis.call('GET', KEYS[3]) or '0')
if killed_at > 0 then
  if now <= killed_at then
    return ''
  end
  redis.call('DEL', KEYS[1])
  redis.call('DEL', KEYS[3])
end
local generation = redis.call('GET', KEYS[1])
if not generation or generation == '' then
  generation = ARGV[4]
  redis.call('SET', KEYS[1], generation, 'EX', active_ttl)
else
  redis.call('EXPIRE', KEYS[1], active_ttl)
end
local payload = cjson.encode({last=ARGV[1], ip=ARGV[5], ua=ARGV[6]})
redis.call('HSET', KEYS[2], generation, payload)
redis.call('EXPIRE', KEYS[2], history_ttl)
redis.call('SADD', KEYS[4], ARGV[7])
redis.call('EXPIRE', KEYS[4], history_ttl)
return generation
"""

    def __init__(self, ttl_seconds: int = 5) -> None:
        self.ttl_seconds = max(5, ttl_seconds)
        self._sessions: dict[tuple[str, int, str, int, int], ViewerSession] = {}
        self._session_index: dict[tuple[str, int], tuple[str, int, str, int, int]] = {}
        self._next_prune = 0.0
        self._redis_touch_after: dict[tuple[int, str], float] = {}
        self._lock = threading.RLock()

    @staticmethod
    def client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        return request.client.host if request.client else "unknown"

    @staticmethod
    def _normalise_session_id(value: str) -> str:
        return _SESSION_RE.sub("", str(value or "").strip())[:96]

    def request_session_id(self, request: Request, explicit: str = "") -> str:
        candidate = (
            explicit
            or request.query_params.get("sid", "")
            or request.headers.get("x-streamforge-session", "")
            or request.headers.get("x-playback-session", "")
            or request.headers.get("x-device-id", "")
        )
        cleaned = self._normalise_session_id(candidate)
        if cleaned:
            return cleaned
        fingerprint = "|".join(
            [
                self.client_ip(request),
                request.headers.get("user-agent", ""),
                request.headers.get("accept", ""),
                request.headers.get("origin", ""),
            ]
        )
        return "fp-" + hashlib.sha256(fingerprint.encode("utf-8", errors="ignore")).hexdigest()[:24]

    def _touch_local(
        self,
        user_id: int,
        node_id: int,
        channel_id: int,
        request: Request,
        *,
        session_id: str,
    ) -> None:
        now = time.monotonic()
        client_ip = self.client_ip(request)
        key = (session_id, int(user_id), client_ip, int(node_id), int(channel_id))
        user_agent = request.headers.get("user-agent", "")[:300]
        with self._lock:
            index_key = (session_id, int(user_id))
            old_key = self._session_index.get(index_key)
            previous = self._sessions.get(old_key) if old_key else None
            # STREAMFORGE_MAIN_VIEWER_RECONNECT_FIRST_RESET_V1062:
            # Match Redis generation semantics without forcing a full local
            # session scan on every media request. A stale same-SID entry is a
            # new playback generation after the viewer timeout.
            if previous is not None and now - previous.last_seen_monotonic > self.ttl_seconds:
                self._sessions.pop(old_key, None)
                if self._session_index.get(index_key) == old_key:
                    self._session_index.pop(index_key, None)
                self._redis_touch_after.pop((previous.user_id, previous.session_id), None)
                previous = None
                old_key = None
            if old_key and old_key != key:
                self._sessions.pop(old_key, None)
            self._sessions[key] = ViewerSession(
                session_id=session_id,
                user_id=key[1],
                client_ip=key[2],
                node_id=key[3],
                channel_id=key[4],
                first_seen_monotonic=previous.first_seen_monotonic if previous else now,
                last_seen_monotonic=now,
                user_agent=user_agent or (previous.user_agent if previous else ""),
            )
            self._session_index[index_key] = key
            self._prune(now)

    def _redis_touch_interval(self) -> float:
        from .config import settings
        return max(0.25, float(settings.redis_touch_interval_ms) / 1000.0)

    def _shared_touch_due(self, user_id: int, session_id: str) -> bool:
        now = time.monotonic()
        key = (int(user_id), session_id)
        with self._lock:
            due = now >= self._redis_touch_after.get(key, 0.0)
            if due:
                self._redis_touch_after[key] = now + self._redis_touch_interval()
            return due

    def touch(
        self,
        user_id: int,
        node_id: int,
        channel_id: int,
        request: Request,
        *,
        session_id: str = "",
    ) -> None:
        resolved_session_id = self.request_session_id(request, session_id)
        # STREAMFORGE_REDIS_HOT_PATH_THROTTLE_V61: keep every segment on the
        # process-local hot path and refresh Redis only on a short heartbeat.
        self._touch_local(
            user_id,
            node_id,
            channel_id,
            request,
            session_id=resolved_session_id,
        )
        if not self._shared_touch_due(int(user_id), resolved_session_id):
            return
        client_ip = self.client_ip(request)
        user_agent = request.headers.get("user-agent", "")[:300]
        now_epoch = time.time()
        ttl = max(5, int(self.ttl_seconds))
        member = f"{int(user_id)}:{resolved_session_id}"
        session_key = redis_state.key("viewer", int(user_id), resolved_session_id)
        global_key = redis_state.key("viewers")
        redis_state.call(
            lambda client: client.eval(
                self._TOUCH_LUA,
                2,
                session_key,
                global_key,
                member,
                now_epoch,
                now_epoch - ttl,
                ttl,
                resolved_session_id,
                int(user_id),
                client_ip,
                int(node_id),
                int(channel_id),
                user_agent,
            )
        )

    def _prune(self, now: float | None = None, force: bool = False) -> None:
        current = time.monotonic() if now is None else now
        if not force and current < self._next_prune:
            return
        stale = [key for key, session in self._sessions.items() if current - session.last_seen_monotonic > self.ttl_seconds]
        for key in stale:
            session = self._sessions.pop(key, None)
            if session:
                index_key = (session.session_id, session.user_id)
                if self._session_index.get(index_key) == key:
                    self._session_index.pop(index_key, None)
                self._redis_touch_after.pop((session.user_id, session.session_id), None)
        self._next_prune = current + 2.0

    def _kill_local(self, session_id: str, user_id: int | None = None) -> int:
        with self._lock:
            keys = [
                key for key, item in self._sessions.items()
                if item.session_id == session_id and (user_id is None or item.user_id == int(user_id))
            ]
            for key in keys:
                item = self._sessions.pop(key, None)
                if item:
                    index_key = (item.session_id, item.user_id)
                    if self._session_index.get(index_key) == key:
                        self._session_index.pop(index_key, None)
                    self._redis_touch_after.pop((item.user_id, item.session_id), None)
            return len(keys)

    def kill(self, session_id: str, user_id: int | None = None) -> int:
        cleaned = self._normalise_session_id(session_id)
        if not cleaned:
            return 0
        global_key = redis_state.key("viewers")

        def redis_kill(client):
            if user_id is not None:
                uid = int(user_id)
                member = f"{uid}:{cleaned}"
                pipe = client.pipeline(transaction=True)
                pipe.delete(redis_state.key("viewer", uid, cleaned))
                pipe.zrem(global_key, member)
                pipe.delete(self._history_current_key(uid, cleaned))
                pipe.set(self._history_killed_key(uid, cleaned), f"{time.time():.6f}", ex=max(300, int(self.ttl_seconds) * 4))
                result = pipe.execute()
                return int(result[1] or 0)
            members = list(client.zscan_iter(global_key, match=f"*:{cleaned}", count=200))
            if not members:
                return 0
            pipe = client.pipeline(transaction=True)
            removed = 0
            for member in members:
                uid_text = str(member).split(":", 1)[0]
                if uid_text.isdigit():
                    uid = int(uid_text)
                    pipe.delete(redis_state.key("viewer", uid, cleaned))
                    pipe.zrem(global_key, member)
                    pipe.delete(self._history_current_key(uid, cleaned))
                    pipe.set(self._history_killed_key(uid, cleaned), f"{time.time():.6f}", ex=max(300, int(self.ttl_seconds) * 4))
                    removed += 1
            pipe.execute()
            return removed

        ok, redis_removed = redis_state.call(redis_kill)
        local_removed = self._kill_local(cleaned, user_id)
        return max(int(redis_removed or 0), local_removed) if ok else local_removed

    @staticmethod
    def _history_current_key(user_id: int, session_id: str) -> str:
        return redis_state.key("viewer-history-current", int(user_id), session_id)

    @staticmethod
    def _history_key(user_id: int, session_id: str) -> str:
        return redis_state.key("viewer-history", int(user_id), session_id)

    @staticmethod
    def _history_killed_key(user_id: int, session_id: str) -> str:
        return redis_state.key("viewer-history-killed", int(user_id), session_id)

    @staticmethod
    def _history_user_key(user_id: int) -> str:
        return redis_state.key("viewer-history-user", int(user_id))

    def touch_history_batch(
        self,
        rows: list[tuple[int, str, float, float, str, str]],
        *,
        history_ttl: int,
    ) -> bool:
        """Best-effort retained Session age, sampled off the playback path."""
        if not rows:
            return False
        active_ttl = max(5, int(self.ttl_seconds))
        retained_ttl = max(300, int(history_ttl))

        def write(client):
            pipe = client.pipeline(transaction=False)
            queued = 0
            for user_id, sid, first_epoch, last_epoch, client_ip, user_agent in rows:
                clean_sid = self._normalise_session_id(sid)
                if not clean_sid:
                    continue
                try:
                    uid = int(user_id)
                    first = float(first_epoch)
                    last = float(last_epoch)
                except (TypeError, ValueError):
                    continue
                if uid <= 0 or first <= 0 or last < first:
                    continue
                pipe.eval(
                    self._HISTORY_TOUCH_LUA,
                    4,
                    self._history_current_key(uid, clean_sid),
                    self._history_key(uid, clean_sid),
                    self._history_killed_key(uid, clean_sid),
                    self._history_user_key(uid),
                    last,
                    active_ttl,
                    retained_ttl,
                    f"{first:.6f}",
                    str(client_ip or "")[:120],
                    str(user_agent or "")[:300],
                    clean_sid,
                )
                queued += 1
            if queued:
                pipe.execute()
            return queued

        ok, queued = redis_state.best_effort_call(write)
        return bool(ok and int(queued or 0) > 0)

    def touch_client_log_sessions_batch(self, rows: list[tuple[int, str]], *, reset_ttl: int) -> set[tuple[int, str]]:
        # STREAMFORGE_MAIN_CLIENT_SESSION_DEDUPE_HEARTBEAT_V1081: control-plane
        # heartbeat only; never executed on the playback request hot path. SET NX
        # identifies a genuinely new logical session; EXPIRE then slides the
        # boundary while playback remains active.
        if not rows:
            return set()
        ttl = max(60, min(10080 * 60, int(reset_ttl)))
        normalized: list[tuple[int, str]] = []
        def write(client):
            pipe = client.pipeline(transaction=False)
            for user_id, sid in rows:
                clean_sid = self._normalise_session_id(sid)
                try:
                    uid = int(user_id)
                except (TypeError, ValueError):
                    continue
                if uid <= 0 or not clean_sid:
                    continue
                key = redis_state.key("client-log-session", uid, clean_sid)
                pipe.set(key, "1", nx=True, ex=ttl)
                pipe.expire(key, ttl)
                normalized.append((uid, clean_sid))
            return pipe.execute() if normalized else []
        ok, results = redis_state.best_effort_call(write)
        if not ok or not isinstance(results, list):
            return set()
        acquired: set[tuple[int, str]] = set()
        for index, identity in enumerate(normalized):
            result_index = index * 2
            if result_index < len(results) and bool(results[result_index]):
                acquired.add(identity)
        return acquired

    def history_for_users(self, user_ids: list[int]) -> list[dict[str, object]]:
        """Return retained viewer generations for the requested Main users only."""
        wanted = sorted({int(value) for value in user_ids if int(value) > 0})
        if not wanted:
            return []

        def read(client):
            index_pipe = client.pipeline(transaction=False)
            for uid in wanted:
                index_pipe.smembers(self._history_user_key(uid))
            sid_sets = index_pipe.execute()
            pairs: list[tuple[int, str]] = []
            for uid, raw_sids in zip(wanted, sid_sets):
                for sid in list(raw_sids or []):
                    clean_sid = self._normalise_session_id(str(sid or ""))
                    if clean_sid:
                        pairs.append((uid, clean_sid))
            if not pairs:
                return []
            history_pipe = client.pipeline(transaction=False)
            for uid, sid in pairs:
                history_pipe.hgetall(self._history_key(uid, sid))
            raw_histories = history_pipe.execute()
            result: list[dict[str, object]] = []
            stale_pairs: list[tuple[int, str]] = []
            for (uid, sid), generations in zip(pairs, raw_histories):
                if not isinstance(generations, dict) or not generations:
                    stale_pairs.append((uid, sid))
                    continue
                for first_raw, payload_raw in generations.items():
                    try:
                        first = float(first_raw)
                        payload = json.loads(str(payload_raw or "{}"))
                        last = float(payload.get("last") or 0.0)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if first <= 0 or last < first:
                        continue
                    result.append({
                        "user_id": uid,
                        "sid": sid,
                        "first_seen_epoch": first,
                        "last_seen_epoch": last,
                        "ip": str(payload.get("ip") or "")[:120],
                        "user_agent": str(payload.get("ua") or "")[:300],
                    })
            if stale_pairs:
                cleanup = client.pipeline(transaction=False)
                for uid, sid in stale_pairs:
                    cleanup.srem(self._history_user_key(uid), sid)
                cleanup.execute()
            return result

        ok, rows = redis_state.best_effort_call(read)
        return list(rows or []) if ok else []

    def _redis_snapshot(self) -> ViewerSnapshot | None:
        captured_mono = time.monotonic()
        now_epoch = time.time()
        ttl = max(5, int(self.ttl_seconds))
        global_key = redis_state.key("viewers")

        def read(client):
            pipe = client.pipeline(transaction=False)
            pipe.zremrangebyscore(global_key, "-inf", now_epoch - ttl)
            pipe.zrangebyscore(global_key, now_epoch - ttl, "+inf")
            _, members = pipe.execute()
            if not members:
                return []
            detail_pipe = client.pipeline(transaction=False)
            valid_members: list[tuple[int, str]] = []
            for member in members:
                raw = str(member)
                uid_text, separator, sid = raw.partition(":")
                if not separator or not uid_text.isdigit() or not sid:
                    continue
                uid = int(uid_text)
                valid_members.append((uid, sid))
                detail_pipe.hgetall(redis_state.key("viewer", uid, sid))
            rows = detail_pipe.execute() if valid_members else []
            return list(zip(valid_members, rows))

        ok, rows = redis_state.call(read)
        if not ok:
            return None
        sessions: list[ViewerSession] = []
        for (uid, sid), raw in rows or []:
            if not raw:
                continue
            try:
                last_epoch = float(raw.get("last") or 0)
                if now_epoch - last_epoch > ttl:
                    continue
                first_epoch = float(raw.get("first") or last_epoch)
                sessions.append(
                    ViewerSession(
                        session_id=str(raw.get("sid") or sid),
                        user_id=int(raw.get("uid") or uid),
                        client_ip=str(raw.get("ip") or "unknown"),
                        node_id=int(raw.get("node") or 0),
                        channel_id=int(raw.get("channel") or 0),
                        first_seen_monotonic=captured_mono - max(0.0, now_epoch - first_epoch),
                        last_seen_monotonic=captured_mono - max(0.0, now_epoch - last_epoch),
                        user_agent=str(raw.get("ua") or ""),
                    )
                )
            except (TypeError, ValueError):
                continue
        node_sessions: dict[int, set[str]] = defaultdict(set)
        channel_sessions: dict[int, set[str]] = defaultdict(set)
        for session in sessions:
            node_sessions[session.node_id].add(session.session_id)
            channel_sessions[session.channel_id].add(session.session_id)
        return ViewerSnapshot(
            total_users=len({session.session_id for session in sessions}),
            unique_accounts=len({session.user_id for session in sessions}),
            by_node={key: len(value) for key, value in node_sessions.items()},
            by_channel={key: len(value) for key, value in channel_sessions.items()},
            sessions=tuple(sessions),
            captured_monotonic=captured_mono,
        )

    def snapshot(self) -> ViewerSnapshot:
        shared = self._redis_snapshot()
        if shared is not None:
            return shared
        now = time.monotonic()
        with self._lock:
            self._prune(now, True)
            sessions = tuple(self._sessions.values())
        node_sessions: dict[int, set[str]] = defaultdict(set)
        channel_sessions: dict[int, set[str]] = defaultdict(set)
        for session in sessions:
            node_sessions[session.node_id].add(session.session_id)
            channel_sessions[session.channel_id].add(session.session_id)
        return ViewerSnapshot(
            total_users=len({session.session_id for session in sessions}),
            unique_accounts=len({session.user_id for session in sessions}),
            by_node={key: len(value) for key, value in node_sessions.items()},
            by_channel={key: len(value) for key, value in channel_sessions.items()},
            sessions=sessions,
            captured_monotonic=now,
        )


viewer_tracker = ViewerTracker()
