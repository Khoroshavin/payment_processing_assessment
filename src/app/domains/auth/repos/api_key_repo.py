"""API key storage.

Keys are never stored or compared in plain text: the service compares
SHA-256 hashes. `StaticApiKeyRepo` validates against a single configured
key; a database-backed implementation (e.g. PostgresApiKeyRepo) implements
the same protocol, so only the repository changes.
"""

import hashlib
import hmac
from typing import Protocol

from app.domains.auth.models.api_key import ApiKey


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class ApiKeyRepo(Protocol):
    async def get_by_hash(self, key_hash: str) -> ApiKey | None: ...


class StaticApiKeyRepo:
    def __init__(self, api_key: str) -> None:
        self._expected_hash = hash_api_key(api_key)

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        if not hmac.compare_digest(key_hash, self._expected_hash):
            return None
        return ApiKey(id="static", name="default", key_hash=self._expected_hash)
