"""Payment message processing orchestration.

Idempotency guard: payment processing runs exactly once. When a message is
redelivered for a payment that is already in a terminal state, only the
webhook delivery is retried.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.payments.adapters.webhook import WebhookClient
from app.domains.payments.enums import TERMINAL_PAYMENT_STATUSES
from app.domains.payments.ports import PaymentGatewayPort
from app.domains.payments.repos.payment_repo import PaymentRepo

logger = logging.getLogger(__name__)


class PaymentProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: PaymentGatewayPort,
        webhook: WebhookClient,
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway
        self._webhook = webhook

    async def process(self, payment_id: UUID) -> None:
        async with self._session_factory() as session:
            repo = PaymentRepo(session)
            # Row lock across the gateway call: concurrent duplicate messages
            # serialize here — the second one sees the terminal status written
            # by the first and only resends the webhook.
            payment = await repo.get_for_update(payment_id)
            if payment is None:
                logger.warning("payment %s not found, skipping event", payment_id)
                return

            if payment.status not in TERMINAL_PAYMENT_STATUSES:
                result = await self._gateway.process(payment.id)
                payment.status = result.status
                payment.processed_at = datetime.now(UTC)
                await session.commit()
                logger.info(
                    "payment %s processed with status %s", payment.id, payment.status
                )
            else:
                logger.info(
                    "payment %s already in terminal state %s, resending webhook only",
                    payment.id,
                    payment.status,
                )

            await self._webhook.notify(payment.webhook_url, payment.id, payment.status)
