"""API key authentication."""

from app.core.exceptions.common import ForbiddenError
from app.domains.auth.models.api_key import ApiKey
from app.domains.auth.repos.api_key_repo import ApiKeyRepo, hash_api_key


class AuthService:
    def __init__(self, repo: ApiKeyRepo) -> None:
        self._repo = repo

    async def authenticate(self, raw_key: str) -> ApiKey:
        api_key = await self._repo.get_by_hash(hash_api_key(raw_key))
        if api_key is None:
            raise ForbiddenError("Invalid API key")
        if not api_key.is_active:
            raise ForbiddenError("API key is inactive")
        return api_key
