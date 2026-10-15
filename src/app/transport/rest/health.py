"""Liveness/readiness endpoint (unauthenticated, standard practice)."""

import logging

from fastapi import APIRouter, status
from sqlalchemy import text

from app.core.exceptions.common import ServiceUnavailableError
from app.core.orm.session_factory import get_session_factory

logger = logging.getLogger(__name__)

router: APIRouter = APIRouter(tags=["health"])


@router.get(
    "/healthz",
    include_in_schema=False,
    summary="Проверка доступности",
    responses={
        status.HTTP_200_OK: {"description": "Сервис и база данных доступны"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "База данных недоступна"},
    },
)
async def healthz() -> dict[str, str]:
    """Возвращает 200, если сервис жив и подключение к базе данных работает."""
    try:
        async with get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("healthcheck failed: database is unavailable")
        raise ServiceUnavailableError("database unavailable") from None
    return {"status": "ok"}
