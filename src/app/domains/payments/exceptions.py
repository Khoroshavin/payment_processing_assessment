"""Domain-specific application errors."""

import uuid

from fastapi import status

from app.core.exceptions.base import BaseAppException


class PaymentNotFoundError(BaseAppException):
    def __init__(self) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, "not_found", "Payment not found")


class IdempotencyConflictError(BaseAppException):
    def __init__(self, idempotency_key: str, payment_id: uuid.UUID) -> None:
        super().__init__(
            status.HTTP_409_CONFLICT,
            "idempotency_conflict",
            (
                f"Idempotency key {idempotency_key!r} was already used for payment "
                f"{payment_id} with a different request body"
            ),
        )


class WebhookUrlValidationError(BaseAppException):
    def __init__(self, message: str) -> None:
        super().__init__(status.HTTP_422_UNPROCESSABLE_CONTENT, "validation_error", message)
