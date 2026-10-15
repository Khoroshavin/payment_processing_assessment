"""Payments REST API: thin HTTP handlers over PaymentService."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.orm.session_factory import get_session
from app.domains.payments.dto import (
    PaymentCreateRequest,
    PaymentCreateResponse,
    PaymentDetail,
)
from app.domains.payments.enums import PaymentStatus
from app.domains.payments.services.payment_service import PaymentService
from app.modules.security.api_key import ApiKeyAuth

router: APIRouter = APIRouter(prefix="/payments", tags=["payments"])

type SessionDep = Annotated[AsyncSession, Depends(get_session)]

type IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        description=(
            "Ключ идемпотентности (1–255 символов). Повторный запрос с тем же ключом "
            "вернёт уже созданный платёж (200 + заголовок Idempotency-Replayed: true)"
        ),
    ),
]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PaymentCreateResponse,
    summary="Создать платёж",
    response_description="Платёж создан и поставлен в очередь на асинхронную обработку",
    responses={
        status.HTTP_200_OK: {
            "description": (
                "Повторный запрос с тем же Idempotency-Key и тем же телом: "
                "возвращён существующий платёж"
            ),
            "model": PaymentCreateResponse,
        },
        status.HTTP_202_ACCEPTED: {"description": "Платёж создан, статус pending"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Отсутствует заголовок X-API-Key"},
        status.HTTP_403_FORBIDDEN: {"description": "Неверный API-ключ"},
        status.HTTP_409_CONFLICT: {
            "description": (
                "Idempotency-Key уже использован с другим телом запроса "
                "(код idempotency_conflict)"
            ),
        },
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "Ошибка валидации запроса (включая небезопасный webhook_url)"
        },
    },
)
async def create_payment(
    payload: PaymentCreateRequest,
    session: SessionDep,
    idempotency_key: IdempotencyKey,
    _auth: ApiKeyAuth,
) -> PaymentCreateResponse | JSONResponse:
    """Создаёт платёж со статусом `pending` и публикует событие `payments.new`
    для асинхронной обработки (outbox-паттерн).

    Требования:
    * заголовок `X-API-Key` — статический ключ сервиса;
    * заголовок `Idempotency-Key` — уникальный ключ идемпотентности;
    * `webhook_url` — публичный http(s) адрес; на него будет отправлено
      уведомление `{"payment_id", "status"}` после обработки.

    Повторный запрос с тем же `Idempotency-Key`:
    * тело совпадает → **200** + заголовок `Idempotency-Replayed: true`;
    * тело отличается → **409** `idempotency_conflict`.

    Транспортная валидация тела (Pydantic, **422**) выполняется до проверки
    идемпотентности: 200/409 возможны только для корректного тела. Повторный
    запрос не проходит повторную проверку безопасности `webhook_url`
    (сервисная проверка после идемпотентной сверки).
    """
    result = await PaymentService(session).create_payment(payload, idempotency_key)

    body = PaymentCreateResponse(
        payment_id=result.payment.id,
        status=PaymentStatus(result.payment.status),
        created_at=result.payment.created_at,
    )
    if result.replayed:
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(body),
            headers={"Idempotency-Replayed": "true"},
        )
    return body


@router.get(
    "/{payment_id}",
    response_model=PaymentDetail,
    summary="Получить информацию о платеже",
    response_description="Детальная информация о платеже",
    responses={
        status.HTTP_200_OK: {"description": "Платёж найден"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Отсутствует заголовок X-API-Key"},
        status.HTTP_403_FORBIDDEN: {"description": "Неверный API-ключ"},
        status.HTTP_404_NOT_FOUND: {"description": "Платёж с таким идентификатором не найден"},
    },
)
async def get_payment(
    payment_id: Annotated[uuid.UUID, Path(description="Идентификатор платежа (UUIDv7)")],
    session: SessionDep,
    _auth: ApiKeyAuth,
) -> PaymentDetail:
    """Возвращает сумму, валюту, описание, метаданные, статус, webhook-адрес
    и даты создания/обработки платежа.
    """
    return await PaymentService(session).get_detail(payment_id)
