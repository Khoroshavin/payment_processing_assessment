"""Identifier generation helpers."""

import uuid

import uuid6


def uuid7() -> uuid.UUID:
    """Generate a time-ordered UUIDv7."""
    return uuid6.uuid7()
