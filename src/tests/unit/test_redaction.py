"""Tests for log redaction helpers."""

from app.modules.security.redaction import redact_url


def test_plain_url_is_unchanged() -> None:
    url = "https://example.com/hooks/notify"
    assert redact_url(url) == url


def test_userinfo_is_masked() -> None:
    assert redact_url("https://alice:secret@example.com/hook") == (
        "https://***:***@example.com/hook"
    )


def test_query_is_masked() -> None:
    assert redact_url("https://example.com/hook?token=abc123&id=1") == (
        "https://example.com/hook?***"
    )


def test_fragment_is_dropped() -> None:
    assert redact_url("https://example.com/hook#section") == "https://example.com/hook"
