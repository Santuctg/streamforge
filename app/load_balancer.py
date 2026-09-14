from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from typing import Iterable

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Channel, Node, StreamUser
from .access_control import ip_matches, evaluate_access
from .node_manager import node_controller, node_maintenance_active


class NodeSelectionError(RuntimeError):
    pass


_PREFIX_SPLIT_RE = re.compile(r"[\s,;]+")


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    value = forwarded.split(",", 1)[0].strip() if forwarded else (request.client.host if request.client else "")
    # X-Forwarded-For can contain a port in some proxy configurations.
    if value.startswith("[") and "]" in value:
        value = value[1:value.index("]")]
    elif value.count(":") == 1 and "." in value:
        host, maybe_port = value.rsplit(":", 1)
        if maybe_port.isdigit():
            value = host
    return value or "0.0.0.0"


def normalize_prefixes(value: str | None) -> str | None:
    tokens = [item for item in _PREFIX_SPLIT_RE.split((value or "").strip()) if item]
    networks: list[ipaddress._BaseNetwork] = []
    for token in tokens:
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError as exc:
            raise ValueError(f"Invalid client IP prefix: {token}") from exc
    unique = sorted({str(item) for item in networks}, key=lambda item: (ipaddress.ip_network(item).version, -ipaddress.ip_network(item).prefixlen, item))
    return "\n".join(unique) or None


def node_networks(node: Node) -> list[ipaddress._BaseNetwork]:
    result: list[ipaddress._BaseNetwork] = []
    for token in _PREFIX_SPLIT_RE.split((node.client_prefixes or "").strip()):
        if not token:
            continue
        try:
            result.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            continue
    return result


def node_ip_whitelist_allows(node: Node, ip_text: str) -> bool:
    """Return whether a Node's playback IP whitelist permits this client.

    STREAMFORGE_CATALOG_IP_WHITELIST_V97: the same inexpensive CIDR match is
    shared by Main catalogue visibility and load-balanced playback selection.
    Empty Node whitelists remain unrestricted; ASN/blacklist policy continues
    to be enforced by the normal playback access layer.
    """
    rules = str(getattr(node, "ip_whitelist", None) or "").strip()
    return True if not rules else bool(ip_matches(rules, str(ip_text or "").strip()))


# STREAMFORGE_FULL_PLAYBACK_POLICY_ROUTING_V99R18:
def node_playback_access_allows(node: Node, request: Request) -> bool:
    """Use the Node's complete playback policy when building/routing Main catalogues.

    Main playlist/WebPlayer requests are catalogue entry points, not Local-Node
    playback requests.  Eligibility therefore belongs to the actual candidate
    playback Node and must include IP blacklist/whitelist plus ASN rules.
    """
    return bool(evaluate_access(
        request,
        ip_whitelist=getattr(node, "ip_whitelist", None),
        ip_blacklist=getattr(node, "ip_blacklist", None),
        asn_whitelist=getattr(node, "asn_whitelist", None),
        asn_blacklist=getattr(node, "asn_blacklist", None),
    ).allowed)


def pinned_node_for_ip(db: Session, ip_text: str) -> tuple[Node | None, str | None]:
    try:
        address = ipaddress.ip_address(ip_text)
    except ValueError:
        return None, None
    matches: list[tuple[int, Node, str]] = []
    for node in db.scalars(select(Node).where(Node.enabled.is_(True))).all():
        for network in node_networks(node):
            if network.version == address.version and address in network:
                matches.append((network.prefixlen, node, str(network)))
    if not matches:
        return None, None
    matches.sort(key=lambda item: (-item[0], item[1].id))
    best_prefix = matches[0][0]
    best = [item for item in matches if item[0] == best_prefix]
    node_ids = {item[1].id for item in best}
    if len(node_ids) > 1:
        raise NodeSelectionError(f"Client IP matches conflicting node prefixes with /{best_prefix}")
    return best[0][1], best[0][2]


def assigned_nodes(channel: Channel) -> list[Node]:
    return node_controller.assigned_nodes(channel)


def user_allowed_nodes(user: StreamUser | None, channel: Channel) -> list[Node]:
    candidates = assigned_nodes(channel)
    if user is None or not user.nodes:
        return candidates
    allowed_ids = {node.id for node in user.nodes if node.enabled}
    return [node for node in candidates if node.id in allowed_ids]


# STREAMFORGE_STATIC_STRICT_FALLBACK_V100:
def playback_candidate_nodes(
    user: StreamUser | None,
    channel: Channel,
    *,
    pinned: Node | None = None,
) -> list[Node]:
    """Return routing candidates in preference order with safe fallback.

    Strict client-prefix and static/fixed-user routing are preferences, not
    single points of failure. A matching strict-prefix Node is tried first. A
    non-load-balanced user's selected Node is tried next. When either preferred
    Node is unavailable, the remaining assigned Nodes become fallback targets.
    Load-balanced users keep their configured allowed-node pool after a strict
    preference fails.
    """
    assigned = assigned_nodes(channel)
    assigned_ids = {int(node.id) for node in assigned}
    ordered: list[Node] = []

    def add(nodes: Iterable[Node]) -> None:
        seen = {int(item.id) for item in ordered}
        for node in nodes:
            node_id = int(node.id)
            if node_id in assigned_ids and node_id not in seen and node.enabled:
                ordered.append(node)
                seen.add(node_id)

    if pinned is not None:
        add([pinned])

    if user is None:
        add(assigned)
    elif bool(user.load_balance_enabled):
        add(user_allowed_nodes(user, channel))
    else:
        # Static/fixed routing stores one selected Node. Keep it first, then
        # allow every other assigned Node to rescue playback when it is down.
        add(user_allowed_nodes(user, channel))
        add(assigned)

    return ordered


def _rendezvous_score(key: str, node: Node) -> int:
    digest = hashlib.sha256(f"{key}|{node.id}|{node.slug}".encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big")


@dataclass(frozen=True)
class NodeChoice:
    node: Node
    client_ip: str
    pinned_prefix: str | None = None
    strict: bool = False


def choose_playback_node(
    db: Session,
    channel: Channel,
    request: Request,
    *,
    user: StreamUser | None = None,
    require_ready: bool = True,
    prefer_cached_ready: bool = False,
) -> NodeChoice:
    ip_text = client_ip(request)
    pinned, prefix = pinned_node_for_ip(db, ip_text)
    candidates = playback_candidate_nodes(user, channel, pinned=pinned)
    if not candidates:
        raise NodeSelectionError("No permitted node is assigned to this channel")

    # Every preferred/fallback target must pass its own playback access policy.
    policy_ok = [node for node in candidates if node_playback_access_allows(node, request)]
    if not policy_ok:
        raise NodeSelectionError("No assigned playback node allows this client")

    pinned_id = int(pinned.id) if pinned is not None else 0
    static_primary_id = 0
    if user is not None and not bool(user.load_balance_enabled):
        selected = user_allowed_nodes(user, channel)
        if selected:
            static_primary_id = int(selected[0].id)

    # Preferred order: strict-prefix Node, then static/fixed primary. A Node in
    # planned maintenance is treated as unavailable when an alternative exists.
    preferred_ids: list[int] = []
    if pinned_id:
        preferred_ids.append(pinned_id)
    if static_primary_id and static_primary_id not in preferred_ids:
        preferred_ids.append(static_primary_id)

    candidate_by_id = {int(node.id): node for node in policy_ok}
    preferred_nodes = [candidate_by_id[node_id] for node_id in preferred_ids if node_id in candidate_by_id]
    fallback_nodes = [node for node in policy_ok if int(node.id) not in set(preferred_ids)]

    # STREAMFORGE_MAIN_WEBPLAYER_CACHED_READY_POOL_V1113:
    # A Web Player switch is latency-sensitive. A channel commonly has Main +
    # many replicas; probing every replica serially can multiply a 4s Node
    # timeout. Prefer recent cached readiness (plus a cheap Local check). If no
    # candidate is known ready, the legacy authoritative probe below remains.
    if prefer_cached_ready and require_ready:
        ready_cached: list[Node] = []
        for node in policy_ok:
            try:
                state = node_controller.cached_hls_ready_state(channel, node, max_age=20.0)
            except Exception:
                state = None
            if state is True:
                ready_cached.append(node)
        if ready_cached:
            ready_by_id = {int(node.id): node for node in ready_cached}
            for preferred_id in preferred_ids:
                selected = ready_by_id.get(preferred_id)
                if selected is not None and not node_maintenance_active(int(selected.id)):
                    is_pinned = bool(pinned_id and int(selected.id) == pinned_id)
                    return NodeChoice(
                        node=selected, client_ip=ip_text,
                        pinned_prefix=prefix if is_pinned else None, strict=is_pinned,
                    )
            active_ready = [node for node in ready_cached if not node_maintenance_active(int(node.id))] or ready_cached
            if user is not None and not bool(user.load_balance_enabled):
                selected = active_ready[0]
            else:
                identity = user.token if user is not None else "direct"
                key = f"{identity}|{ip_text}|{channel.id}|{channel.slug}"
                selected = max(active_ready, key=lambda node: _rendezvous_score(key, node))
            return NodeChoice(node=selected, client_ip=ip_text)

    # STREAMFORGE_PLAYBACK_READY_RELEASE_DB_V32
    # STREAMFORGE_NODE_UPDATE_PLAYBACK_FALLBACK_V57
    # STREAMFORGE_PLAYBACK_IP_WHITELIST_ELIGIBLE_POOL_V97
    # Relationships are loaded. Do not hold the request DB connection while a
    # Remote HLS readiness probe waits on the network.
    db.commit()

    for node in preferred_nodes:
        if node_maintenance_active(int(node.id)) and fallback_nodes:
            continue
        if not require_ready or node_controller.hls_ready(channel, node):
            is_pinned = bool(pinned_id and int(node.id) == pinned_id)
            return NodeChoice(
                node=node,
                client_ip=ip_text,
                pinned_prefix=prefix if is_pinned else None,
                strict=is_pinned,
            )

    # STREAMFORGE_STATIC_STRICT_READY_FALLBACK_V100: preferred Static/Strict
    # targets are allowed to fail over to another assigned, policy-permitted,
    # playback-ready Node instead of hard-failing the stream.
    active_fallbacks = [node for node in fallback_nodes if not node_maintenance_active(int(node.id))]
    if not active_fallbacks:
        active_fallbacks = list(fallback_nodes)
    ready = [node for node in active_fallbacks if not require_ready or node_controller.hls_ready(channel, node)]
    if not ready:
        # If there was only a preferred Node and it was in maintenance, still
        # allow it when no fallback exists and it is actually ready.
        for node in preferred_nodes:
            if not require_ready or node_controller.hls_ready(channel, node):
                is_pinned = bool(pinned_id and int(node.id) == pinned_id)
                return NodeChoice(node=node, client_ip=ip_text, pinned_prefix=prefix if is_pinned else None, strict=is_pinned)
        raise NodeSelectionError("No preferred or fallback playback node is ready")

    if user is not None and not bool(user.load_balance_enabled):
        # assigned_nodes() is Local-first, then Remote name/id, so a static
        # route naturally falls back to Local when Local is available.
        selected = ready[0]
    else:
        identity = user.token if user is not None else "direct"
        key = f"{identity}|{ip_text}|{channel.id}|{channel.slug}"
        selected = max(ready, key=lambda node: _rendezvous_score(key, node))
    return NodeChoice(node=selected, client_ip=ip_text)


def validate_routed_node(
    db: Session,
    channel: Channel,
    request: Request,
    node_id: int,
    *,
    user: StreamUser | None = None,
) -> Node:
    """Validate a previously selected media Node without re-imposing hard pins.

    STREAMFORGE_ROUTED_FALLBACK_VALIDATION_V100: master-playlist selection may
    legitimately choose a fallback Node when a Strict-prefix or Static primary
    is down. The child playlist/segment URL must therefore accept that fallback
    while still enforcing channel assignment, user pool semantics for normal
    load-balanced users, and the target Node's own playback policy.
    """
    node = db.get(Node, node_id)
    if not node or not node.enabled:
        raise NodeSelectionError("Selected node is unavailable")
    assigned_ids = {int(item.id) for item in assigned_nodes(channel)}
    if int(node.id) not in assigned_ids:
        raise NodeSelectionError("Selected node is not assigned to this channel")

    ip_text = client_ip(request)
    pinned, _prefix = pinned_node_for_ip(db, ip_text)
    pinned_id = int(pinned.id) if pinned is not None else 0

    # A load-balanced account keeps its configured node pool, except that a
    # matching strict-prefix target may override that pool as before. Static
    # accounts allow other assigned Nodes only as failover targets.
    if user is not None and bool(user.load_balance_enabled) and user.nodes:
        allowed_ids = {int(item.id) for item in user.nodes if item.enabled}
        if int(node.id) not in allowed_ids and int(node.id) != pinned_id:
            raise NodeSelectionError("Selected node is not allowed for this user")

    if not node_playback_access_allows(node, request):
        raise NodeSelectionError(f"Selected node {node.name} does not allow this client")
    return node

