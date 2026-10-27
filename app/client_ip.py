"""Rightmost X-Forwarded-For resolution behind the local Nginx (v12.83).

Nginx appends the peer address it actually accepted the connection from to the
end of the client-supplied X-Forwarded-For chain, so the RIGHTMOST hop is the
real client and cannot be forged by prepending values. The older per-module
helpers read the leftmost entry instead, which let any viewer choose any source
IP they liked: access-policy bypass, per-IP limit evasion and viewer-count
inflation with a single forged header.

Uvicorn (through the Gunicorn UvicornWorker) rewrites ``request.client`` from
the LEFTMOST hop whenever the socket peer is trusted, so the socket peer itself
must never be read from ``request.client``: the trust gate uses the ASGI server
bind address (``scope["server"]``), which cannot be rewritten. When a CDN or
another proxy sits in front of Nginx, grant that proxy real_ip trust in
``deploy/nginx.conf`` first, so the rightmost hop stays the true client.
"""
from __future__ import annotations

import ipaddress

from fastapi import Request


def strip_port(value: str) -> str:
    """Drop a surrounding IPv6 bracket pair or a trailing ``:port`` suffix."""
    value = str(value or "").strip()
    if value.startswith("[") and "]" in value:
        return value[1:value.index("]")]
    if value.count(":") == 1 and "." in value:
        host, maybe_port = value.rsplit(":", 1)
        if maybe_port.isdigit():
            return host
    return value


def forwarded_hops(header: str) -> list[str]:
    """X-Forwarded-For entries in order, port-stripped and without blanks."""
    return [hop for hop in (strip_port(item) for item in str(header or "").split(",")) if hop]


def loopback_backend(request: Request) -> bool:
    """True when the ASGI server itself is bound to loopback (the Nginx host).

    ``request.client`` cannot be used for this: Uvicorn rewrites it from an
    untrusted first XFF hop, while the server bind address is immutable.
    """
    scope = getattr(request, "scope", None)
    server = scope.get("server") if scope else None
    if server:
        try:
            return ipaddress.ip_address(server[0]).is_loopback
        except (ValueError, TypeError, IndexError):
            return False
    peer = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(peer).is_loopback
    except (ValueError, TypeError):
        return False


def forwarded_client_ip(request: Request) -> str | None:
    """The real client behind local Nginx, or None when XFF must not be trusted.

    Trust requires the backend bind (not the rewritable socket peer) to be
    loopback; then the RIGHTMOST hop is returned, canonicalized, with
    IPv4-mapped IPv6 collapsed to IPv4. An absent header, a public bind or a
    malformed rightmost hop all return None so callers fall back to the socket
    peer.
    """
    if not loopback_backend(request):
        return None
    header = request.headers.get("x-forwarded-for", "")
    if not str(header or "").strip():
        return None
    hops = forwarded_hops(header)
    if not hops:
        return None
    try:
        parsed = ipaddress.ip_address(hops[-1])
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        return str(parsed.ipv4_mapped)
    return str(parsed)
