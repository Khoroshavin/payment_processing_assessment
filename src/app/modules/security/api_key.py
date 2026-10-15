"""Static API key authentication guard (X-API-Key header)."""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from fastapi.security import APIKeyHeader

from app.core.configs.settings import get_settings
from app.core.exceptions.common import ForbiddenError
from app.domains.auth.exceptions import UnauthorizedError
from app.domains.auth.repos.api_key_repo import StaticApiKeyRepo
from app.domains.auth.services.auth_service import AuthService

logger = logging.getLogger(__name__)

api_key_header: APIKeyHeader = APIKeyHeader(
    name="X-API-Key",
    description="Статический API-ключ сервиса (см. API_KEY в конфигурации)",
    auto_error=False,
)


@lru_cache
def get_auth_service() -> AuthService:
    return AuthService(StaticApiKeyRepo(get_settings().app.api_key))


async def require_api_key(
    x_api_key: Annotated[str | None, Depends(api_key_header)] = None,
) -> None:
    if x_api_key is None:
        raise UnauthorizedError()
    try:
        await get_auth_service().authenticate(x_api_key)
    except ForbiddenError:
        logger.warning("request rejected with invalid API key")
        raise


type ApiKeyAuth = Annotated[None, Depends(require_api_key)]
