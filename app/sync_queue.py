from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AppSetting, Node


def node_sync_pending_key(node_id: int) -> str:
    return f"node_{int(node_id)}_sync_pending"


def node_sync_pending_reason(db: Session, node_id: int) -> str:
    row = db.get(AppSetting, node_sync_pending_key(node_id))
    return str(row.value or "").strip() if row else ""


def node_sync_pending(db: Session, node_id: int) -> bool:
    return bool(node_sync_pending_reason(db, node_id))


def mark_node_sync_pending(db: Session, node: Node | int, reason: object = "sync queued") -> None:
    """Persist an explicit full-reconcile request.

    v11.27 keeps this legacy/full-reconcile key only for operations that really
    require a complete Node settings/users/catalogue sync (for example Manual
    Test & Sync). Per-channel config retries use dedicated channel keys below so
    a single failed channel can never turn the next heartbeat into an all-channel
    sweep.
    """
    node_id = int(node.id if isinstance(node, Node) else node)
    key = node_sync_pending_key(node_id)
    value = str(reason or "sync queued").strip()[-2000:] or "sync queued"
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def clear_node_sync_pending(db: Session, node_id: int) -> None:
    row = db.get(AppSetting, node_sync_pending_key(node_id))
    if row is not None:
        db.delete(row)


# STREAMFORGE_NODE_TARGETED_CHANNEL_PENDING_V1127:
# Channel-config delivery failures are scoped to (node, channel).  They are
# intentionally separate from node_<id>_sync_pending, because the old shared key
# made a single timeout cause heartbeat -> full settings/users/catalogue replay.
def node_channel_sync_pending_key(node_id: int, channel_id: int) -> str:
    return f"node_{int(node_id)}_channel_{int(channel_id)}_sync_pending"


def mark_node_channel_sync_pending(
    db: Session,
    node: Node | int,
    channel_id: int,
    reason: object = "channel config queued",
) -> None:
    node_id = int(node.id if isinstance(node, Node) else node)
    key = node_channel_sync_pending_key(node_id, channel_id)
    value = str(reason or "channel config queued").strip()[-2000:] or "channel config queued"
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def clear_node_channel_sync_pending(db: Session, node_id: int, channel_id: int) -> None:
    row = db.get(AppSetting, node_channel_sync_pending_key(node_id, channel_id))
    if row is not None:
        db.delete(row)


def node_channel_sync_pending_ids(db: Session, node_id: int, *, limit: int = 500) -> list[int]:
    prefix = f"node_{int(node_id)}_channel_"
    suffix = "_sync_pending"
    rows = db.scalars(
        select(AppSetting)
        .where(AppSetting.key.like(prefix + "%" + suffix))
        .order_by(AppSetting.key)
        .limit(max(1, min(2000, int(limit or 500))))
    ).all()
    channel_ids: list[int] = []
    seen: set[int] = set()
    for row in rows:
        key = str(row.key or "")
        if not key.startswith(prefix) or not key.endswith(suffix):
            continue
        raw_id = key[len(prefix):-len(suffix)]
        try:
            channel_id = int(raw_id)
        except ValueError:
            continue
        if channel_id > 0 and channel_id not in seen:
            seen.add(channel_id)
            channel_ids.append(channel_id)
    return channel_ids


def clear_node_channel_sync_pending_many(db: Session, node_id: int, channel_ids: set[int] | list[int]) -> None:
    for channel_id in {int(item) for item in channel_ids if int(item) > 0}:
        clear_node_channel_sync_pending(db, int(node_id), channel_id)


# STREAMFORGE_NODE_TARGETED_LOGO_PENDING_V1129:
# Logo delivery has its own scoped queue. A failed logo copy must never reuse
# node_<id>_sync_pending because that legacy key is reserved for explicit full
# reconciliation only.
def node_channel_logo_pending_key(node_id: int, channel_id: int) -> str:
    return f"node_{int(node_id)}_channel_{int(channel_id)}_logo_pending"


def mark_node_channel_logo_pending(
    db: Session,
    node: Node | int,
    channel_id: int,
    reason: object = "channel logo queued",
) -> None:
    node_id = int(node.id if isinstance(node, Node) else node)
    key = node_channel_logo_pending_key(node_id, channel_id)
    value = str(reason or "channel logo queued").strip()[-2000:] or "channel logo queued"
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def clear_node_channel_logo_pending(db: Session, node_id: int, channel_id: int) -> None:
    row = db.get(AppSetting, node_channel_logo_pending_key(node_id, channel_id))
    if row is not None:
        db.delete(row)


def node_channel_logo_pending_ids(db: Session, node_id: int, *, limit: int = 500) -> list[int]:
    prefix = f"node_{int(node_id)}_channel_"
    suffix = "_logo_pending"
    rows = db.scalars(
        select(AppSetting)
        .where(AppSetting.key.like(prefix + "%" + suffix))
        .order_by(AppSetting.key)
        .limit(max(1, min(2000, int(limit or 500))))
    ).all()
    channel_ids: list[int] = []
    seen: set[int] = set()
    for row in rows:
        key = str(row.key or "")
        if not key.startswith(prefix) or not key.endswith(suffix):
            continue
        raw_id = key[len(prefix):-len(suffix)]
        try:
            channel_id = int(raw_id)
        except ValueError:
            continue
        if channel_id > 0 and channel_id not in seen:
            seen.add(channel_id)
            channel_ids.append(channel_id)
    return channel_ids


def clear_node_channel_logo_pending_many(db: Session, node_id: int, channel_ids: set[int] | list[int]) -> None:
    for channel_id in {int(item) for item in channel_ids if int(item) > 0}:
        clear_node_channel_logo_pending(db, int(node_id), channel_id)
