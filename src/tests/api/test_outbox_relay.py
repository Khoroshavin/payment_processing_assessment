"""Outbox relay delivery-guarantee tests."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from faststream.rabbit import RabbitBroker
from sqlalchemy import select

from app.core.configs.settings import Settings
from app.core.orm.session_factory import get_session_factory
from app.domains.payments.dto import NewPayment
from app.domains.payments.enums import OutboxStatus
from app.domains.payments.models import OutboxEvent
from app.domains.payments.services.outbox_relay import OutboxRelay
from app.domains.payments.services.payment_service import PaymentService
from app.infra.messaging.topology import build_topology

pytestmark = pytest.mark.integration


class BrokenBroker:
    """Broker whose publishes always fail, like an unreachable RabbitMQ."""

    async def publish(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("broker unavailable")


class RecordingBroker:
    """Broker that records successful publishes."""

    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, message: Any, **kwargs: Any) -> None:
        self.published.append(message)


async def _create_outbox_row(idempotency_key: str) -> uuid.UUID:
    async with get_session_factory()() as session:
        result = await PaymentService(session).create(
            NewPayment(
                amount=Decimal("5.00"),
                currency="RUB",
                description="relay test payment",
                metadata={},
                webhook_url="http://8.8.8.8/hook",
                idempotency_key=idempotency_key,
                request_hash="relay-test-hash",
            )
        )
        return result.payment.id


async def _fetch_outbox_row(aggregate_id: uuid.UUID) -> OutboxEvent:
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == aggregate_id)
            )
        ).scalar_one()
        return row


async def test_failed_publish_is_retried_forever_and_never_marked_dead(
    test_settings: Settings,
) -> None:
    payment_id = await _create_outbox_row(f"relay-{uuid.uuid4()}")
    relay = OutboxRelay(
        cast(RabbitBroker, BrokenBroker()),
        build_topology(test_settings),
        get_session_factory(),
        test_settings.relay,
    )

    # First failing attempt: row stays pending and is rescheduled.
    await relay._process_batch()
    row = await _fetch_outbox_row(payment_id)
    assert row.status == OutboxStatus.PENDING
    assert row.attempts == 1
    assert row.available_at > datetime.now(UTC)

    # Force the row far beyond the log-escalation threshold: it must still be
    # retried, never marked dead.
    async with get_session_factory()() as session:
        row = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == payment_id)
            )
        ).scalar_one()
        row.attempts = test_settings.relay.max_attempts + 10
        row.available_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    await relay._process_batch()
    row = await _fetch_outbox_row(payment_id)
    assert row.status == OutboxStatus.PENDING
    assert row.attempts == test_settings.relay.max_attempts + 11
    assert row.published_at is None


async def test_poisoned_payload_is_marked_dead_and_does_not_block_relay(
    test_settings: Settings,
) -> None:
    """A row whose payload cannot be parsed must not stall the relay: it is
    moved to a controlled `dead` state and the following healthy rows are
    still published."""
    healthy_payment_id = await _create_outbox_row(f"relay-{uuid.uuid4()}")
    poisoned = OutboxEvent(
        event_type="payments.new",
        aggregate_id=uuid.uuid4(),
        payload={"payment_id": "not-a-uuid", "event_version": "not-an-int"},
    )
    async with get_session_factory()() as session:
        session.add(poisoned)
        await session.commit()

    relay = OutboxRelay(
        cast(RabbitBroker, RecordingBroker()),
        build_topology(test_settings),
        get_session_factory(),
        test_settings.relay,
    )
    await relay._process_batch()

    async with get_session_factory()() as session:
        rows = (
            await session.execute(
                select(OutboxEvent).order_by(OutboxEvent.id)
            )
        ).scalars().all()
    by_aggregate_id = {row.aggregate_id: row for row in rows}

    dead = by_aggregate_id[poisoned.aggregate_id]
    assert dead.status == OutboxStatus.DEAD
    assert dead.attempts == 1
    assert dead.last_error is not None
    assert dead.published_at is None

    healthy = by_aggregate_id[healthy_payment_id]
    assert healthy.status == OutboxStatus.PUBLISHED
    assert healthy.published_at is not None
