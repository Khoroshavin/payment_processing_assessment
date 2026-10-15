"""Webhook URL safety validation (basic SSRF protection)."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeWebhookUrlError(ValueError):
    pass


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_blocked_ip(ipaddress.IPv4Address(ip.ipv4_mapped))
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_public_ip(ip_string: str) -> bool:
    return not _is_blocked_ip(ipaddress.ip_address(ip_string))


async def _resolve(host: str) -> list[tuple]:
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)


async def validate_webhook_url_safety(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeWebhookUrlError("only http/https schemes are allowed")

    host = parsed.hostname
    if not host:
        raise UnsafeWebhookUrlError("invalid host")
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeWebhookUrlError("localhost is not allowed")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        if _is_blocked_ip(literal):
            raise UnsafeWebhookUrlError("private and reserved addresses are not allowed")
        return

    try:
        infos = await _resolve(host)
    except socket.gaierror as exc:
        raise UnsafeWebhookUrlError("host does not resolve") from exc

    for info in infos:
        resolved = ipaddress.ip_address(info[4][0])
        if _is_blocked_ip(resolved):
            raise UnsafeWebhookUrlError("host resolves to a non-public address")
