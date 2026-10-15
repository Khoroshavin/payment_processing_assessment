"""Domain data transfer objects (service-level inputs/outputs)."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.domains.payments.models.payment import Payment


@dataclass(frozen=True)
class NewPayment:
    amount: Decimal
    currency: str
    description: str
    metadata: dict[str, Any]
    webhook_url: str
    idempotency_key: str
    request_hash: str


@dataclass(frozen=True)
class CreatePaymentResult:
    payment: Payment
    replayed: bool
