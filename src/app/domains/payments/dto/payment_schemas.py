"""Transport-facing DTOs (Pydantic request/response schemas)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.domains.payments.enums import Currency, PaymentStatus, enum_values


class PaymentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=2,
        description="Сумма платежа, максимум 2 знака после запятой",
        examples=["1500.50"],
    )
    currency: Currency = Field(
        description=f"Валюта платежа (допустимые значения: {enum_values(Currency)})",
        examples=[currency.value for currency in Currency],
    )
    description: str = Field(
        min_length=1, max_length=255, description="Описание платежа", examples=["Заказ #123"]
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Дополнительная информация (произвольный JSON)",
        examples=[{"order_id": "123"}],
    )
    webhook_url: HttpUrl = Field(
        description="Публичный http(s) URL, куда будет отправлено уведомление о результате",
        examples=["https://example.com/webhook"],
    )


class PaymentCreateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: uuid.UUID = Field(description="Идентификатор платежа (UUIDv7)")
    status: PaymentStatus = Field(
        description=f"Текущий статус платежа (допустимые значения: {enum_values(PaymentStatus)})"
    )
    created_at: datetime = Field(description="Время создания (UTC)")


class PaymentDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: uuid.UUID = Field(validation_alias="id", description="Идентификатор платежа")
    amount: Decimal = Field(description="Сумма платежа")
    currency: Currency = Field(
        description=f"Валюта платежа (допустимые значения: {enum_values(Currency)})"
    )
    description: str = Field(description="Описание платежа")
    metadata: dict[str, Any] = Field(
        validation_alias="metadata_", description="Дополнительная информация"
    )
    status: PaymentStatus = Field(
        description=f"Текущий статус платежа (допустимые значения: {enum_values(PaymentStatus)})"
    )
    idempotency_key: str = Field(description="Ключ идемпотентности, с которым создан платёж")
    webhook_url: str = Field(description="URL для webhook-уведомления")
    created_at: datetime = Field(description="Время создания (UTC)")
    processed_at: datetime | None = Field(
        description="Время завершения обработки (UTC), пусто пока платёж не обработан"
    )
