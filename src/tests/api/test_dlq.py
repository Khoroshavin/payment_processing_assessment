import uuid
from decimal import Decimal

import aio_pika
import pytest

from app.core.configs.settings import Settings
from app.core.orm.session_factory import get_session_factory
from app.domains.payments.dto import NewPayment
from app.domains.payments.models import Payment
from app.domains.payments.services.payment_service import PaymentService
from tests.fixtures.helpers import MockWebhookServer, WaitUntil

pytestmark = pytest.mark.integration


async def _get_queue_message_count(amqp_url: str, queue_name: str) -> int:
    connection = await aio_pika.connect_robust(amqp_url)
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue(queue_name, passive=True)
        return queue.declaration_result.message_count or 0


async def _create_payment(webhook_url: str, idempotency_key: str) -> Payment:
    async with get_session_factory()() as session:
        result = await PaymentService(session).create(
            NewPayment(
                amount=Decimal("10.00"),
                currency="EUR",
                description="dlq test payment",
                metadata={},
                webhook_url=webhook_url,
                idempotency_key=idempotency_key,
                request_hash="integration-test-hash",
            )
        )
        return result.payment


async def test_webhook_failure_moves_message_to_dlq_after_three_attempts(
    test_settings: Settings,
    running_consumer: None,
    running_relay: None,
    failing_webhook_server: MockWebhookServer,
    wait_until: WaitUntil,
) -> None:
    payment = await _create_payment(failing_webhook_server.url, f"dlq-{uuid.uuid4()}")

    async def dlq_has_message() -> bool:
        return (
            await _get_queue_message_count(
                test_settings.rabbitmq.dsn, test_settings.rabbitmq.dlq_name
            )
            == 1
        )

    await wait_until(dlq_has_message)

    assert len(failing_webhook_server.requests) == 3

    async with get_session_factory()() as session:
        updated = await session.get(Payment, payment.id)
        assert updated is not None
        assert updated.status == "succeeded"


async def test_malformed_message_moves_to_dlq_without_requeueing(
    test_settings: Settings,
    running_consumer: None,
    wait_until: WaitUntil,
) -> None:
    """A body that cannot be parsed into PaymentNewEvent must be dead-lettered
    once (raw body preserved) instead of being requeued into the work queue
    forever."""
    baseline_dlq = await _get_queue_message_count(
        test_settings.rabbitmq.dsn, test_settings.rabbitmq.dlq_name
    )

    connection = await aio_pika.connect_robust(test_settings.rabbitmq.dsn)
    async with connection:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            test_settings.rabbitmq.exchange_name, aio_pika.ExchangeType.DIRECT, durable=True
        )
        await exchange.publish(
            aio_pika.Message(
                body=b'{"payment_id": "not-a-uuid", "event_version": 1}',
                content_type="application/json",
            ),
            routing_key=test_settings.rabbitmq.queue_name,
        )

    async def dlq_gained_one_message() -> bool:
        return (
            await _get_queue_message_count(
                test_settings.rabbitmq.dsn, test_settings.rabbitmq.dlq_name
            )
            >= baseline_dlq + 1
        )

    await wait_until(dlq_gained_one_message)

    # The malformed message must not be spinning through the work queue.
    work_queue_count = await _get_queue_message_count(
        test_settings.rabbitmq.dsn, test_settings.rabbitmq.queue_name
    )
    assert work_queue_count == 0
