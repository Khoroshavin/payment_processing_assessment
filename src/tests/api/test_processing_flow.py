import asyncio
import dataclasses
import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.configs.settings import Settings
from app.core.orm.session_factory import get_session_factory
from app.domains.payments.dto import NewPayment
from app.domains.payments.models import OutboxEvent, Payment
from app.domains.payments.services.payment_service import PaymentService
from tests.fixtures.helpers import MockWebhookServer, WaitUntil

pytestmark = pytest.mark.integration


async def _create_payment(webhook_url: str, idempotency_key: str) -> Payment:
    async with get_session_factory()() as session:
        result = await PaymentService(session).create(
            NewPayment(
                amount=Decimal("99.99"),
                currency="USD",
                description="integration test payment",
                metadata={"source": "integration-test"},
                webhook_url=webhook_url,
                idempotency_key=idempotency_key,
                request_hash="integration-test-hash",
            )
        )
        return result.payment


async def test_happy_path_processing_and_webhook(
    running_consumer: None,
    running_relay: None,
    webhook_server: MockWebhookServer,
    wait_until: WaitUntil,
) -> None:
    payment = await _create_payment(webhook_server.url, f"flow-{uuid.uuid4()}")

    async def webhook_received() -> bool:
        return len(webhook_server.requests) == 1

    await wait_until(webhook_received)

    assert webhook_server.requests[0] == {
        "payment_id": str(payment.id),
        "status": "succeeded",
    }

    async with get_session_factory()() as session:
        updated = await session.get(Payment, payment.id)
        assert updated is not None
        assert updated.status == "succeeded"
        assert updated.processed_at is not None

        outbox_row = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == payment.id)
            )
        ).scalar_one()
        assert outbox_row.status == "published"
        assert outbox_row.published_at is not None


async def test_gateway_failure_marks_payment_failed(
    test_settings: Settings,
    running_relay: None,
    webhook_server: MockWebhookServer,
    wait_until: WaitUntil,
) -> None:
    from app.core.orm.session_factory import get_session_factory
    from app.domains.payments.adapters.emulated_gateway import PaymentGateway
    from app.domains.payments.adapters.webhook import WebhookClient
    from app.domains.payments.services.processing import PaymentProcessor
    from app.infra.messaging.broker import build_rabbit_broker
    from app.infra.messaging.consumer import register_consumer
    from app.infra.messaging.topology import build_topology, declare_topology

    failing_emulation = test_settings.emulation.model_copy(update={"success_rate": 0.0})
    failing_settings = dataclasses.replace(test_settings, emulation=failing_emulation)
    broker = build_rabbit_broker(
        failing_settings.rabbitmq.dsn, logger=logging.getLogger("app.consumer")
    )
    webhook = WebhookClient(failing_settings.webhook)
    processor = PaymentProcessor(
        get_session_factory(), PaymentGateway(failing_settings.emulation), webhook
    )
    topology = build_topology(failing_settings)
    register_consumer(broker, topology, failing_settings.consumer, processor)
    await broker.start()
    await declare_topology(broker, topology)
    try:
        payment = await _create_payment(webhook_server.url, f"gateway-fail-{uuid.uuid4()}")

        async def webhook_received() -> bool:
            return len(webhook_server.requests) == 1

        await wait_until(webhook_received)

        assert webhook_server.requests[0] == {
            "payment_id": str(payment.id),
            "status": "failed",
        }

        async with get_session_factory()() as session:
            updated = await session.get(Payment, payment.id)
            assert updated is not None
            assert updated.status == "failed"
            assert updated.processed_at is not None
    finally:
        await broker.stop()
        await webhook.close()


async def test_concurrent_duplicate_messages_call_gateway_once(
    test_settings: Settings,
    webhook_server: MockWebhookServer,
) -> None:
    """Two concurrent deliveries of the same event must not double-process.

    The row lock (SELECT FOR UPDATE) serializes processors: the second one
    sees the terminal status and only resends the webhook.
    """
    from app.domains.payments.adapters.emulated_gateway import PaymentGateway
    from app.domains.payments.adapters.webhook import WebhookClient
    from app.domains.payments.enums import PaymentStatus
    from app.domains.payments.ports import ProcessingResult
    from app.domains.payments.services.processing import PaymentProcessor

    calls = 0

    class SlowCountingGateway(PaymentGateway):
        async def process(self, payment_id: uuid.UUID) -> ProcessingResult:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.3)
            return ProcessingResult(status=PaymentStatus.SUCCEEDED)

    webhook = WebhookClient(test_settings.webhook)
    processor = PaymentProcessor(
        get_session_factory(), SlowCountingGateway(test_settings.emulation), webhook
    )
    try:
        payment = await _create_payment(webhook_server.url, f"race-{uuid.uuid4()}")
        await asyncio.gather(
            processor.process(payment.id), processor.process(payment.id)
        )

        assert calls == 1
        assert len(webhook_server.requests) >= 1
        assert all(r["status"] == "succeeded" for r in webhook_server.requests)

        async with get_session_factory()() as session:
            updated = await session.get(Payment, payment.id)
            assert updated is not None
            assert updated.status == "succeeded"
    finally:
        await webhook.close()
