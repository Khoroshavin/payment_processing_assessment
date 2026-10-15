import pytest

from app.core.exceptions.common import ForbiddenError
from app.domains.auth.models.api_key import ApiKey
from app.domains.auth.repos.api_key_repo import StaticApiKeyRepo, hash_api_key
from app.domains.auth.services.auth_service import AuthService


def test_hash_is_stable_and_different_for_different_keys() -> None:
    assert hash_api_key("secret") == hash_api_key("secret")
    assert hash_api_key("secret") != hash_api_key("other")


async def test_authenticate_returns_api_key_for_valid_credentials() -> None:
    service = AuthService(StaticApiKeyRepo("secret-key"))

    api_key = await service.authenticate("secret-key")

    assert isinstance(api_key, ApiKey)
    assert api_key.is_active is True
    assert api_key.key_hash == hash_api_key("secret-key")


async def test_authenticate_raises_forbidden_for_invalid_credentials() -> None:
    service = AuthService(StaticApiKeyRepo("secret-key"))

    with pytest.raises(ForbiddenError):
        await service.authenticate("wrong-key")


async def test_authenticate_raises_forbidden_for_inactive_key() -> None:
    class InactiveRepo:
        async def get_by_hash(self, key_hash: str) -> ApiKey | None:
            return ApiKey(id="k1", name="inactive", key_hash=key_hash, is_active=False)

    service = AuthService(InactiveRepo())

    with pytest.raises(ForbiddenError):
        await service.authenticate("any-key")
