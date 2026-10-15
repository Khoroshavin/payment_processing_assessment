import ipaddress
import socket

import pytest

from app.modules.security.webhook_url import (
    UnsafeWebhookUrlError,
    _is_blocked_ip,
    is_public_ip,
    validate_webhook_url_safety,
)


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",
        "127.0.0.1",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
    ],
)
def test_blocked_ips(ip: str) -> None:
    assert _is_blocked_ip(ipaddress.ip_address(ip)) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700::1111", "93.184.216.34"])
def test_public_ips(ip: str) -> None:
    assert _is_blocked_ip(ipaddress.ip_address(ip)) is False


def test_is_public_ip_helper() -> None:
    assert is_public_ip("8.8.8.8") is True
    assert is_public_ip("127.0.0.1") is False


async def test_literal_private_ip_rejected() -> None:
    with pytest.raises(UnsafeWebhookUrlError):
        await validate_webhook_url_safety("http://127.0.0.1:8000/hook")


async def test_literal_public_ip_allowed() -> None:
    await validate_webhook_url_safety("http://8.8.8.8:8080/hook")


async def test_localhost_hostname_rejected() -> None:
    with pytest.raises(UnsafeWebhookUrlError):
        await validate_webhook_url_safety("http://localhost/hook")


async def test_non_http_scheme_rejected() -> None:
    with pytest.raises(UnsafeWebhookUrlError):
        await validate_webhook_url_safety("file:///etc/passwd")


async def test_hostname_resolving_to_private_ip_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(host: str) -> list[tuple]:
        assert host == "internal.example.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr("app.modules.security.webhook_url._resolve", fake_resolve)
    with pytest.raises(UnsafeWebhookUrlError):
        await validate_webhook_url_safety("https://internal.example.com/hook")


async def test_hostname_resolving_to_public_ip_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(host: str) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]

    monkeypatch.setattr("app.modules.security.webhook_url._resolve", fake_resolve)
    await validate_webhook_url_safety("https://public.example.com/hook")
