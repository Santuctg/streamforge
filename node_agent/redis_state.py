from __future__ import annotations

import hashlib
import json
import time
from typing import Any

try:
    import redis  # type: ignore
except Exception:  # pragma: no cover
    redis = None


class NodeRedisState:
    """Redis-backed shared runtime state for multi-worker Node public traffic.

    Redis is optional at runtime. Callers must retain a single-worker/local
    fallback when ``available`` is false.

    STREAMFORGE_NODE_REDIS_VIEWER_TTL_EXACT_V90R2: Redis active-session
    cutoffs honor the configured Online session timeout (minimum 5 seconds).
    """

    # STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_V1046: retained compatibility marker.
    # STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_ASYNC_V1047: Session-age history
    # is best-effort bookkeeping and must never sit in the latency-sensitive
    # playback heartbeat transaction. v10.49 runs this script only from the
    # single control-plane session observer after active viewer state is shared.
    HISTORY_TOUCH_LUA = r'''
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
redis.call('HSET', KEYS[2], generation, ARGV[1])
redis.call('EXPIRE', KEYS[2], history_ttl)
return generation
'''

    # STREAMFORGE_NODE_PLAYBACK_CRITICAL_RESERVE_V1049: connection-limit
    # reservation is intentionally limited to the two existing active-session
    # sorted sets. Client-log/session-age bookkeeping must never add keys or
    # extra work to this latency-sensitive authorization transaction.
    RESERVE_LUA = r'''
local now = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local sid = ARGV[3]
local global_member = ARGV[4]
local user_limit = tonumber(ARGV[5])
local total_limit = tonumber(ARGV[6])
local playback_start = tonumber(ARGV[7])
local cutoff = now - ttl
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', cutoff)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', cutoff)
if redis.call('ZSCORE', KEYS[2], sid) then
  redis.call('ZADD', KEYS[1], now, global_member)
  redis.call('ZADD', KEYS[2], now, sid)
  redis.call('EXPIRE', KEYS[1], math.max(ttl * 4, 300))
  redis.call('EXPIRE', KEYS[2], math.max(ttl * 4, 300))
  return 1
end
if playback_start ~= 1 then
  return 0
end
if user_limit > 0 and redis.call('ZCARD', KEYS[2]) >= user_limit then
  return -1
end
if total_limit > 0 and redis.call('ZCARD', KEYS[1]) >= total_limit then
  return -2
end
redis.call('ZADD', KEYS[1], now, global_member)
redis.call('ZADD', KEYS[2], now, sid)
redis.call('EXPIRE', KEYS[1], math.max(ttl * 4, 300))
redis.call('EXPIRE', KEYS[2], math.max(ttl * 4, 300))
return 1
'''

    def __init__(self, url: str, prefix: str, enabled: bool = True) -> None:
        self.url = str(url or '').strip()
        self.prefix = str(prefix or 'streamforge:node').strip(':') or 'streamforge:node'
        self.enabled = bool(enabled and self.url and redis is not None)
        self.client: Any | None = None
        self._last_probe = 0.0
        self._online = False
        if self.enabled:
            try:
                self.client = redis.Redis.from_url(
                    self.url,
                    decode_responses=True,
                    socket_connect_timeout=0.15,
                    socket_timeout=0.15,
                    health_check_interval=30,
                    retry_on_timeout=False,
                )
            except Exception:
                self.client = None
                self.enabled = False

    @property
    def available(self) -> bool:
        if not self.enabled or self.client is None:
            return False
        now = time.monotonic()
        if now - self._last_probe < 2.0:
            return self._online
        self._last_probe = now
        try:
            self._online = bool(self.client.ping())
        except Exception:
            self._online = False
        return self._online

    @staticmethod
    def _hash(value: str, length: int = 24) -> str:
        return hashlib.sha256(str(value).encode('utf-8', errors='ignore')).hexdigest()[:length]

    def _k(self, suffix: str) -> str:
        return f'{self.prefix}:{suffix}'

    def _user_sessions_key(self, token: str) -> str:
        return self._k(f'user:{self._hash(token)}:sessions')

    def _viewer_key(self, token: str, sid: str) -> str:
        return self._k(f'viewer:{self._hash(token + "|" + sid, 32)}')

    # STREAMFORGE_NODE_SESSION_KILL_INDEX_V1043: keep a short-lived SID -> viewer-key
    # index so a panel Kill does not have to SCAN the complete viewer namespace.
    # A SID can theoretically be shared by more than one account, so the index is
    # a Redis set rather than a single scalar key.
    def _viewer_sid_key(self, sid: str) -> str:
        return self._k(f'viewer-sid:{self._hash(sid, 32)}')

    def _viewer_member_index_key(self, member: str) -> str:
        # STREAMFORGE_NODE_VIEWER_ACTIVE_INDEX_V1049: one expiring scalar per
        # active zset member -> viewer hash key. Live Sessions resolves only
        # current viewers and stale index entries disappear with the viewer TTL.
        return self._k(f'viewer-active:{self._hash(member, 40)}')

    def _viewer_history_current_key(self, sid: str) -> str:
        return self._k(f'viewer-history-current:{self._hash(sid, 32)}')

    def _viewer_history_key(self, sid: str) -> str:
        return self._k(f'viewer-history:{self._hash(sid, 32)}')

    def _viewer_history_killed_key(self, sid: str) -> str:
        return self._k(f'viewer-history-killed:{self._hash(sid, 32)}')

    def reserve_connection(
        self,
        token: str,
        sid: str,
        *,
        user_limit: int,
        total_limit: int,
        ttl: int,
        playback_start: bool,
    ) -> bool:
        if not self.available:
            raise RuntimeError('redis unavailable')
        now = time.time()
        global_key = self._k('connections')
        user_key = self._user_sessions_key(token)
        member = f'{self._hash(token)}|{sid}'
        try:
            result = int(self.client.eval(
                self.RESERVE_LUA,
                2,
                global_key,
                user_key,
                now,
                max(5, int(ttl)),
                sid,
                member,
                max(0, int(user_limit)),
                max(0, int(total_limit)),
                1 if playback_start else 0,
            ))
            return result == 1
        except Exception as exc:
            self._online = False
            raise RuntimeError('redis reserve failed') from exc

    def touch_viewer(
        self,
        token: str,
        sid: str,
        channel_key: str,
        ip: str,
        user_agent: str,
        *,
        ttl: int,
    ) -> None:
        # STREAMFORGE_NODE_PLAYBACK_HEARTBEAT_SAFE_V1047:
        # STREAMFORGE_NODE_PLAYBACK_HEARTBEAT_ISOLATED_V1049: keep this critical
        # transaction limited to active connection/viewer state. Historical
        # Client-log age and reconnect logging are control-plane observer work.
        if not self.available:
            raise RuntimeError('redis unavailable')
        now = time.time()
        expire = max(int(ttl) * 4, 300)
        global_key = self._k('connections')
        user_key = self._user_sessions_key(token)
        member = f'{self._hash(token)}|{sid}'
        viewer_key = self._viewer_key(token, sid)
        sid_key = self._viewer_sid_key(sid)
        try:
            pipe = self.client.pipeline(transaction=False)
            pipe.zremrangebyscore(global_key, '-inf', now - max(5, int(ttl)))
            pipe.zremrangebyscore(user_key, '-inf', now - max(5, int(ttl)))
            pipe.zadd(global_key, {member: now})
            pipe.zadd(user_key, {sid: now})
            pipe.hsetnx(viewer_key, 'first_seen_epoch', f'{now:.6f}')
            pipe.hset(viewer_key, mapping={
                'token': token,
                'sid': sid,
                'channel_key': channel_key,
                'ip': ip,
                'user_agent': str(user_agent or '')[:300],
                'last_seen_epoch': f'{now:.6f}',
                'member': member,
            })
            pipe.expire(global_key, expire)
            pipe.expire(user_key, expire)
            pipe.expire(viewer_key, expire)
            pipe.sadd(sid_key, viewer_key)
            pipe.expire(sid_key, expire)
            # STREAMFORGE_NODE_VIEWER_ACTIVE_INDEX_V1049: one expiring scalar
            # lets the control plane fetch only active viewer metadata without SCAN.
            index_key = self._viewer_member_index_key(member)
            pipe.set(index_key, viewer_key, ex=expire)
            pipe.execute()
        except Exception as exc:
            self._online = False
            raise RuntimeError('redis viewer touch failed') from exc

    def touch_viewer_history_batch(
        self,
        rows: list[tuple[str, str, float]],
        *,
        ttl: int,
        history_ttl: int,
    ) -> bool:
        # STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_BATCH_V1047: best-effort only.
        # A timeout/error here deliberately does NOT set _online=False because
        # log history must never make subsequent playback auth fail closed.
        if not self.enabled or self.client is None or not rows:
            return False
        active_ttl = max(5, int(ttl))
        retained_ttl = max(300, int(history_ttl))
        try:
            pipe = self.client.pipeline(transaction=False)
            for token, sid, heartbeat_epoch in rows:
                clean_sid = str(sid or '').strip()[:96]
                if not clean_sid:
                    continue
                now = float(heartbeat_epoch or time.time())
                generation = f'{now:.6f}'
                pipe.eval(
                    self.HISTORY_TOUCH_LUA,
                    3,
                    self._viewer_history_current_key(clean_sid),
                    self._viewer_history_key(clean_sid),
                    self._viewer_history_killed_key(clean_sid),
                    now,
                    active_ttl,
                    retained_ttl,
                    generation,
                )
            pipe.execute()
            return True
        except Exception:
            return False

    def touch_client_log_sessions_batch(self, rows: list[tuple[str, str]], *, reset_ttl: int) -> bool:
        # STREAMFORGE_NODE_CLIENT_SESSION_DEDUPE_HEARTBEAT_V1081: control-plane
        # observer refreshes this key while playback is active. Public workers
        # only perform a single SET NX when a login/catalog request arrives.
        if not self.enabled or self.client is None or not rows:
            return False
        ttl = max(60, min(10080 * 60, int(reset_ttl)))
        try:
            pipe = self.client.pipeline(transaction=False)
            queued = 0
            for token, sid in rows:
                clean_sid = str(sid or '').strip()[:96]
                if not clean_sid:
                    continue
                digest = hashlib.sha256(f"{token}|{clean_sid}".encode('utf-8', errors='ignore')).hexdigest()[:40]
                pipe.set(self._k(f"client-log-session:{digest}"), '1', ex=ttl)
                queued += 1
            if queued:
                pipe.execute()
            return bool(queued)
        except Exception:
            return False

    def total_active(self, ttl: int) -> int:
        if not self.available:
            raise RuntimeError('redis unavailable')
        now = time.time()
        key = self._k('connections')
        try:
            pipe = self.client.pipeline(transaction=False)
            pipe.zremrangebyscore(key, '-inf', now - max(5, int(ttl)))
            pipe.zcard(key)
            return int(pipe.execute()[-1] or 0)
        except Exception as exc:
            self._online = False
            raise RuntimeError('redis count failed') from exc

    def active_for_user(self, token: str, ttl: int) -> int:
        if not self.available:
            raise RuntimeError('redis unavailable')
        now = time.time()
        key = self._user_sessions_key(token)
        try:
            pipe = self.client.pipeline(transaction=False)
            pipe.zremrangebyscore(key, '-inf', now - max(5, int(ttl)))
            pipe.zcard(key)
            return int(pipe.execute()[-1] or 0)
        except Exception as exc:
            self._online = False
            raise RuntimeError('redis user count failed') from exc

    def viewer_sessions(self, ttl: int) -> list[dict[str, Any]]:
        # STREAMFORGE_NODE_VIEWER_ACTIVE_INDEX_V1049: Main/Node Live Sessions
        # refreshes must not SCAN every historical viewer hash. Resolve current
        # zset members through expiring member->viewer-key scalar indexes. A
        # bounded legacy SCAN is used only for members that predate v10.49 and
        # disappears naturally after their next heartbeat populates the index.
        if not self.available:
            raise RuntimeError('redis unavailable')
        now = time.time()
        global_key = self._k('connections')
        cutoff = now - max(5, int(ttl))
        try:
            pipe = self.client.pipeline(transaction=False)
            pipe.zremrangebyscore(global_key, '-inf', cutoff)
            pipe.zrangebyscore(global_key, cutoff, '+inf')
            members = list(pipe.execute()[-1] or [])
            if not members:
                return []

            index_pipe = self.client.pipeline(transaction=False)
            for member in members:
                index_pipe.get(self._viewer_member_index_key(str(member)))
            indexed_keys = list(index_pipe.execute() or [])
            member_to_key: dict[str, str] = {}
            missing_members: set[str] = set()
            for member, viewer_key in zip(members, indexed_keys):
                clean_member = str(member)
                key = str(viewer_key or '')
                if key:
                    member_to_key[clean_member] = key
                else:
                    missing_members.add(clean_member)

            if missing_members:
                # Upgrade compatibility only. Current v10.49 heartbeats write
                # the scalar index, so this path drains without becoming steady-state.
                legacy_keys = list(self.client.scan_iter(match=self._k('viewer:*'), count=1000))
                if legacy_keys:
                    legacy_pipe = self.client.pipeline(transaction=False)
                    for viewer_key in legacy_keys:
                        legacy_pipe.hgetall(viewer_key)
                    for viewer_key, row in zip(legacy_keys, legacy_pipe.execute()):
                        if not isinstance(row, dict):
                            continue
                        member = str(row.get('member') or '')
                        if member in missing_members:
                            member_to_key[member] = str(viewer_key)
                repair_pipe = self.client.pipeline(transaction=False)
                repair_count = 0
                repair_ttl = max(int(ttl) * 4, 300)
                for member in missing_members:
                    viewer_key = member_to_key.get(member)
                    if viewer_key:
                        repair_pipe.set(self._viewer_member_index_key(member), viewer_key, ex=repair_ttl)
                        repair_count += 1
                if repair_count:
                    repair_pipe.execute()

            ordered_pairs = [(str(member), member_to_key.get(str(member), '')) for member in members]
            ordered_pairs = [(member, key) for member, key in ordered_pairs if key]
            if not ordered_pairs:
                return []
            row_pipe = self.client.pipeline(transaction=False)
            for _member, viewer_key in ordered_pairs:
                row_pipe.hgetall(viewer_key)
            raw_rows = row_pipe.execute()
            result: list[dict[str, Any]] = []
            stale_index_members: list[str] = []
            for (member, _viewer_key), row in zip(ordered_pairs, raw_rows):
                if not isinstance(row, dict) or str(row.get('member') or '') != member:
                    stale_index_members.append(member)
                    continue
                try:
                    last_seen = float(row.get('last_seen_epoch') or 0.0)
                except (TypeError, ValueError):
                    last_seen = 0.0
                if last_seen < cutoff:
                    stale_index_members.append(member)
                    continue
                result.append(dict(row))
            if stale_index_members:
                cleanup = self.client.pipeline(transaction=False)
                for member in stale_index_members:
                    cleanup.delete(self._viewer_member_index_key(member))
                cleanup.execute()
            return result
        except Exception as exc:
            self._online = False
            raise RuntimeError('redis viewer list failed') from exc

    # STREAMFORGE_NODE_CLIENT_LOG_DURATION_LOOKUP_V1045: Resolve only the
    # requested SIDs through the existing SID index instead of scanning every
    # viewer key. This keeps live Client-log duration cheap even on busy Nodes.
    def viewer_sessions_by_sid(self, sids: list[str], ttl: int) -> list[dict[str, Any]]:
        if not self.available:
            raise RuntimeError('redis unavailable')
        wanted = [str(sid or '').strip()[:96] for sid in sids if str(sid or '').strip()]
        if not wanted:
            return []
        now = time.time()
        cutoff = now - max(5, int(ttl))
        global_key = self._k('connections')
        try:
            members = set(self.client.zrangebyscore(global_key, cutoff, '+inf') or [])
            if not members:
                return []
            pipe = self.client.pipeline(transaction=False)
            for sid in wanted:
                pipe.smembers(self._viewer_sid_key(sid))
            sid_sets = pipe.execute()
            viewer_keys: list[str] = []
            for values in sid_sets:
                for viewer_key in list(values or []):
                    key = str(viewer_key)
                    if key and key not in viewer_keys:
                        viewer_keys.append(key)
            if not viewer_keys:
                return []
            pipe = self.client.pipeline(transaction=False)
            for viewer_key in viewer_keys:
                pipe.hgetall(viewer_key)
            rows = pipe.execute()
            result: list[dict[str, Any]] = []
            wanted_set = set(wanted)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                sid = str(row.get('sid') or '')
                member = str(row.get('member') or '')
                if sid not in wanted_set or member not in members:
                    continue
                try:
                    last_seen = float(row.get('last_seen_epoch') or 0.0)
                except (TypeError, ValueError):
                    last_seen = 0.0
                if last_seen < cutoff:
                    continue
                result.append(dict(row))
            return result
        except Exception as exc:
            self._online = False
            raise RuntimeError('redis viewer SID lookup failed') from exc

    def viewer_session_history_by_sid(self, sids: list[str]) -> list[dict[str, Any]]:
        # STREAMFORGE_NODE_CLIENT_SESSION_HISTORY_LOOKUP_V1046: unlike the
        # active viewer lookup, this intentionally returns completed generations
        # too so Client-log Session age freezes instead of disappearing offline.
        if not self.available:
            raise RuntimeError('redis unavailable')
        wanted = [str(sid or '').strip()[:96] for sid in sids if str(sid or '').strip()]
        if not wanted:
            return []
        try:
            pipe = self.client.pipeline(transaction=False)
            for sid in wanted:
                pipe.hgetall(self._viewer_history_key(sid))
            raw = pipe.execute()
            result: list[dict[str, Any]] = []
            for sid, generations in zip(wanted, raw):
                if not isinstance(generations, dict):
                    continue
                for first_raw, last_raw in generations.items():
                    try:
                        first = float(first_raw)
                        last = float(last_raw)
                    except (TypeError, ValueError):
                        continue
                    if first <= 0 or last < first:
                        continue
                    result.append({
                        'sid': sid,
                        'first_seen_epoch': first,
                        'last_seen_epoch': last,
                    })
            return result
        except Exception as exc:
            self._online = False
            raise RuntimeError('redis viewer history lookup failed') from exc

    def kill_session(self, sid: str) -> int:
        if not self.available:
            raise RuntimeError('redis unavailable')
        killed = 0
        sid_key = self._viewer_sid_key(sid)
        try:
            # STREAMFORGE_NODE_SESSION_KILL_INDEX_V1043: normal v10.43 traffic
            # maintains this direct index on every viewer heartbeat.  During a
            # rolling update, fall back to the legacy bounded SCAN only when no
            # index exists yet, so pre-v10.43 sessions remain killable.
            viewer_keys = list(self.client.smembers(sid_key) or [])
            if not viewer_keys:
                viewer_keys = list(self.client.scan_iter(match=self._k('viewer:*'), count=1000))
            matched_keys: list[str] = []
            rows: list[tuple[str, dict[str, Any]]] = []
            if viewer_keys:
                pipe = self.client.pipeline(transaction=False)
                for viewer_key in viewer_keys:
                    pipe.hgetall(viewer_key)
                raw_rows = pipe.execute()
                for viewer_key, row in zip(viewer_keys, raw_rows):
                    if not isinstance(row, dict) or str(row.get('sid') or '') != sid:
                        continue
                    matched_keys.append(str(viewer_key))
                    rows.append((str(viewer_key), row))
            for viewer_key, row in rows:
                token = str(row.get('token') or '')
                member = str(row.get('member') or '')
                pipe = self.client.pipeline(transaction=False)
                if member:
                    pipe.zrem(self._k('connections'), member)
                    pipe.delete(self._viewer_member_index_key(member))
                if token:
                    pipe.zrem(self._user_sessions_key(token), sid)
                pipe.delete(viewer_key)
                pipe.srem(sid_key, viewer_key)
                pipe.execute()
                killed += 1
            # The SID index is only a helper; remove it even when a legacy SCAN
            # found nothing so stale index members cannot accumulate.
            self.client.delete(sid_key)
            # STREAMFORGE_NODE_CLIENT_SESSION_KILL_MARKER_V1047: a delayed
            # asynchronous pre-kill history flush must not reopen the completed
            # generation or merge the next reconnect into it.
            history_current_key = self._viewer_history_current_key(sid)
            history_killed_key = self._viewer_history_killed_key(sid)
            pipe = self.client.pipeline(transaction=False)
            pipe.delete(history_current_key)
            pipe.set(history_killed_key, f'{time.time():.6f}', ex=max(300, 4 * 60))
            pipe.execute()
            return killed
        except Exception as exc:
            self._online = False
            raise RuntimeError('redis session kill failed') from exc

    def issue_playback(self, index_key: str, grant: dict[str, Any], ttl: int, alphabet: str, length: int) -> str:
        if not self.available:
            raise RuntimeError('redis unavailable')
        index_digest = self._hash(index_key, 48)
        index_redis_key = self._k(f'playback-index:{index_digest}')
        try:
            existing = self.client.get(index_redis_key)
            if existing:
                raw = self.client.get(self._k(f'playback:{existing}'))
                if raw:
                    data = json.loads(raw)
                    if int(data.get('exp') or 0) > int(time.time()) + 60:
                        return str(existing)
            import secrets
            for _ in range(128):
                short_key = ''.join(secrets.choice(alphabet) for _ in range(length))
                grant_key = self._k(f'playback:{short_key}')
                payload = json.dumps(grant, separators=(',', ':'))
                if self.client.set(grant_key, payload, nx=True, ex=max(60, int(ttl))):
                    self.client.set(index_redis_key, short_key, ex=max(60, int(ttl)))
                    return short_key
            raise RuntimeError('unable to allocate playback key')
        except Exception as exc:
            self._online = False
            raise RuntimeError('redis playback issue failed') from exc

    def get_playback(self, short_key: str) -> dict[str, Any] | None:
        if not self.available:
            raise RuntimeError('redis unavailable')
        try:
            raw = self.client.get(self._k(f'playback:{short_key}'))
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception as exc:
            self._online = False
            raise RuntimeError('redis playback lookup failed') from exc
