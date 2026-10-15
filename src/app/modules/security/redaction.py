"""Helpers to redact sensitive values before they reach logs."""

from urllib.parse import SplitResult, urlsplit, urlunsplit

USERINFO_REDACTED: str = "***:***@"
QUERY_REDACTED: str = "***"


def redact_url(url: str) -> str:
    """Return *url* safe for logging.

    Keeps scheme, host, port and path so the line stays useful for tracing,
    but never leaks embedded credentials (`https://user:pass@host/...`) or
    query parameters, which often carry tokens (`?token=...`).
    """
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        hostname = parts.hostname or ""
        netloc = USERINFO_REDACTED + hostname
        if parts.port is not None:
            netloc += f":{parts.port}"
    else:
        netloc = parts.netloc
    query = QUERY_REDACTED if parts.query else ""
    return urlunsplit(SplitResult(parts.scheme, netloc, parts.path, query, ""))
