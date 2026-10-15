"""Outbox relay: publishes pending outbox events to RabbitMQ with publisher confirms.

Guarantees at-least-once delivery: a row is marked published only after the
broker confirmed the publish. Failed publishes are retried forever with
capped exponential backoff — the relay never gives up on an event, because
the outbox pattern promises publication. `max_attempts` only escalates the
log level for alerting.

The one exception is a poisoned row: a payload that cannot be parsed back
into an event can never be published, and retrying it would keep re-claiming
the same row and stall the relay. Such rows are marked `dead` (manual
operator action) with the parse error recorded in `last_error`.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from faststream.rabbit import RabbitBroker
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.configs.settings import RelaySettings
from app.domains.payments.enums import OutboxStatus
from app.domains.payments.models.outbox import OutboxEvent
from app.domains.payments.repos.outbox_repo import OutboxRepo
from app.infra.messaging.events import PAYMENT_NEW_EVENT, PaymentNewEvent
from app.infra.messaging.topology import Topology

logger = logging.getLogger(__name__)


class OutboxRelay:
    def __init__(
        self,
        broker: RabbitBroker,
        topology: Topology,
        session_factory: async_sessionmaker[AsyncSession],
        settings: RelaySettings,
    ) -> None:
        self._broker = broker
        self._topology = topology
        self._session_factory = session_factory
        self._settings = settings
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        logger.info("outbox relay started")
        while not self._stop.is_set():
            try:
                processed = await self._process_batch()
                if processed:
                    logger.info("relay published %d outbox event(s)", processed)
            except Exception:
                logger.exception("outbox relay iteration failed")
            await self._sleep(self._settings.poll_interval_seconds)
        logger.info("outbox relay stopped")

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    async def _process_batch(self) -> int:
        async with self._session_factory() as session:
            repo = OutboxRepo(session)
            rows = await repo.claim_pending(limit=self._settings.batch_size)
            for row in rows:
                await self._publish_one(row)
            await session.commit()
            return len(rows)

    async def _publish_one(self, row: OutboxEvent) -> None:
        try:
            event = PaymentNewEvent.model_validate(row.payload)
        except ValidationError as exc:
            # Poisoned row: the payload can never be parsed, so retrying is
            # pointless and would stall the relay on this row forever. Move
            # it to a controlled terminal state for manual operator action.
            row.attempts += 1
            row.last_error = str(exc)[:1000]
            row.status = OutboxStatus.DEAD
            logger.error(
                "outbox event %s payload is not a valid %s event, marked dead: %s",
                row.id,
                PAYMENT_NEW_EVENT,
                row.last_error,
            )
            return
        try:
            await self._broker.publish(
                event,
                queue=self._topology.queue,
                exchange=self._topology.exchange,
                routing_key=self._topology.queue.routing(),
                persist=True,
            )
        except Exception as exc:
            row.attempts += 1
            row.last_error = str(exc)[:1000]
            delay = self._backoff_seconds(row.attempts)
            row.available_at = datetime.now(UTC) + timedelta(seconds=delay)
            if row.attempts >= self._settings.max_attempts:
                # Escalated log level for alerting; the event is still retried.
                logger.error(
                    "outbox event %s failed %s times, still retrying forever: %s",
                    row.id,
                    row.attempts,
                    row.last_error,
                )
            else:
                logger.warning(
                    "outbox event %s publish failed (attempt %s), retrying in %.1fs",
                    row.id,
                    row.attempts,
                    delay,
                )
        else:
            row.status = OutboxStatus.PUBLISHED
            row.published_at = datetime.now(UTC)

    def _backoff_seconds(self, attempts: int) -> float:
        base = self._settings.backoff_base_seconds
        cap = self._settings.backoff_cap_seconds
        return min(base * (2 ** (attempts - 1)), cap)
