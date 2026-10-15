"""Payment creation and retrieval, including idempotency handling."""

import hashlib
import json
import logging
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.domains.payments.dto import (
    CreatePaymentResult,
    NewPayment,
    PaymentCreateRequest,
    PaymentDetail,
)
from app.domains.payments.exceptions import (
    IdempotencyConflictError,
    PaymentNotFoundError,
    WebhookUrlValidationError,
)
from app.domains.payments.models.outbox import OutboxEvent
from app.domains.payments.models.payment import Payment
from app.domains.payments.repos.outbox_repo import OutboxRepo
from app.domains.payments.repos.payment_repo import PaymentRepo
from app.infra.messaging.events import EVENT_VERSION, PAYMENT_NEW_EVENT
from app.modules.security.redaction import redact_url
from app.modules.security.webhook_url import UnsafeWebhookUrlError, validate_webhook_url_safety

logger = logging.getLogger(__name__)


def request_body_hash(payload: PaymentCreateRequest) -> str:
    """Canonical hash of the request body for idempotency conflict detection.

    Keys are sorted recursively (`sort_keys=True`), so the hash depends only
    on the JSON value: the same object with a different key order inside
    `metadata` (or any nested object) hashes identically and replays instead
    of failing with 409.
    """
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._payments = PaymentRepo(session)
        self._outbox = OutboxRepo(session)

    def _replay_or_conflict(
        self, existing: Payment, idempotency_key: str, request_hash: str
    ) -> CreatePaymentResult:
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError(idempotency_key, existing.id)
        logger.info("payment %s replayed for idempotency key %s", existing.id, idempotency_key)
        return CreatePaymentResult(payment=existing, replayed=True)

    async def create(self, data: NewPayment) -> CreatePaymentResult:
        existing = await self._payments.find_by_idempotency_key(
            data.idempotency_key, for_update=True
        )
        if existing is not None:
            return self._replay_or_conflict(existing, data.idempotency_key, data.request_hash)

        payment = Payment(
            id=uuid7(),
            amount=data.amount,
            currency=data.currency,
            description=data.description,
            metadata_=data.metadata,
            webhook_url=data.webhook_url,
            idempotency_key=data.idempotency_key,
            request_hash=data.request_hash,
        )
        self._payments.add(payment)
        self._outbox.add(
            OutboxEvent(
                event_type=PAYMENT_NEW_EVENT,
                aggregate_id=payment.id,
                payload={"payment_id": str(payment.id), "event_version": EVENT_VERSION},
            )
        )
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._payments.find_by_idempotency_key(
                data.idempotency_key, for_update=True
            )
            if existing is None:
                raise
            return self._replay_or_conflict(existing, data.idempotency_key, data.request_hash)
        logger.info(
            "payment %s created (idempotency key %s)", payment.id, data.idempotency_key
        )
        return CreatePaymentResult(payment=payment, replayed=False)

    async def create_payment(
        self, payload: PaymentCreateRequest, idempotency_key: str
    ) -> CreatePaymentResult:
        """API use case: idempotency lookup before service-level checks, then create.

        FastAPI validates the request body (422) before this method is called,
        so a replay with a structurally invalid body is rejected at the
        transport level. Within the service, the existing payment is looked up
        before the webhook URL is validated, so a replayed request with a valid
        body is never rejected for an unsafe URL — it either replays (identical
        body) or fails with 409 (different body).
        """
        request_hash = request_body_hash(payload)
        existing = await self._payments.find_by_idempotency_key(
            idempotency_key, for_update=True
        )
        if existing is not None:
            return self._replay_or_conflict(existing, idempotency_key, request_hash)

        await self._validate_webhook_url(str(payload.webhook_url))
        return await self.create(
            NewPayment(
                amount=payload.amount,
                currency=payload.currency.value,
                description=payload.description,
                metadata=payload.metadata,
                webhook_url=str(payload.webhook_url),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        )

    async def get_detail(self, payment_id: UUID) -> PaymentDetail:
        payment = await self._payments.get(payment_id)
        if payment is None:
            raise PaymentNotFoundError()
        return PaymentDetail.model_validate(payment, from_attributes=True)

    async def _validate_webhook_url(self, url: str) -> None:
        try:
            await validate_webhook_url_safety(url)
        except UnsafeWebhookUrlError as exc:
            logger.warning("unsafe webhook_url rejected: %s (%s)", redact_url(url), exc)
            raise WebhookUrlValidationError(f"Invalid webhook_url: {exc}") from exc
