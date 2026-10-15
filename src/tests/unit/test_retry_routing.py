import uuid
from typing import Any, cast

import pytest
from faststream.rabbit import RabbitBroker, RabbitMessage
from pydantic import ValidationError

from app.core.configs.settings import (
    AppSettings,
    ConsumerSettings,
    EmulationSettings,
    PostgresSettings,
    RabbitMQSettings,
    RelaySettings,
    Settings,
    WebhookSettings,
)
from app.domains.payments.services.processing import PaymentProcessor
from app.infra.messaging.consumer import (
    RETRY_COUNT_HEADER,
    handle_payment_new,
    retry_or_dead_letter,
)
from app.infra.messaging.events import EVENT_VERSION, PaymentNewEvent
from app.infra.messaging.topology import build_topology


class FakeBroker:
    def __init__(self) -> None:
        self.published: list[tuple[Any, dict[str, Any]]] = []

    async def publish(self, message: Any, **kwargs: Any) -> None:
        self.published.append((message, kwargs))


class FailingBroker(FakeBroker):
    """Broker whose publishes always fail, like an unreachable RabbitMQ."""

    async def publish(self, message: Any, **kwargs: Any) -> None:
        raise ConnectionError("broker unavailable")


class FakeMessage:
    def __init__(self, body: bytes, headers: dict[str, Any] | None = None) -> None:
        self.body = body
        self.headers = headers or {}
        self.rejected: list[bool] = []

    async def reject(self, requeue: bool = False) -> None:
        self.rejected.append(requeue)


class StubProcessor:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.processed: list[uuid.UUID] = []

    async def process(self, payment_id: uuid.UUID) -> None:
        self.processed.append(payment_id)
        if self._error is not None:
            raise self._error


def make_settings() -> Settings:
    return Settings(
        app=AppSettings(),
        postgres=PostgresSettings(),
        rabbitmq=RabbitMQSettings(),
        emulation=EmulationSettings(),
        relay=RelaySettings(),
        consumer=ConsumerSettings(max_retries=2, retry_delays_seconds=[2.0, 4.0]),
        webhook=WebhookSettings(),
    )


async def _handle(
    broker: FakeBroker,
    body: bytes,
    *,
    headers: dict[str, Any] | None = None,
    processor_error: Exception | None = None,
) -> tuple[FakeMessage, StubProcessor]:
    settings = make_settings()
    topology = build_topology(settings)
    processor = StubProcessor(error=processor_error)
    msg = FakeMessage(body, headers)
    await handle_payment_new(
        cast(RabbitBroker, broker), topology, settings.consumer,
        cast(PaymentProcessor, processor), cast(RabbitMessage, msg),
    )
    return msg, processor


async def test_first_failure_goes_to_2s_retry_queue() -> None:
    broker = FakeBroker()
    settings = make_settings()
    topology = build_topology(settings)
    event = PaymentNewEvent(payment_id=uuid.uuid4())

    await retry_or_dead_letter(cast(RabbitBroker, broker), topology, settings.consumer, event, 0)

    message, kwargs = broker.published[0]
    assert message == event
    assert kwargs["queue"].name == "payments.new.retry.2s"
    assert kwargs["headers"] == {"x-retry-count": 1}
    assert kwargs["persist"] is True


async def test_second_failure_goes_to_4s_retry_queue() -> None:
    broker = FakeBroker()
    settings = make_settings()
    topology = build_topology(settings)
    event = PaymentNewEvent(payment_id=uuid.uuid4())

    await retry_or_dead_letter(cast(RabbitBroker, broker), topology, settings.consumer, event, 1)

    _, kwargs = broker.published[0]
    assert kwargs["queue"].name == "payments.new.retry.4s"
    assert kwargs["headers"] == {"x-retry-count": 2}


async def test_third_failure_goes_to_dlq() -> None:
    broker = FakeBroker()
    settings = make_settings()
    topology = build_topology(settings)
    event = PaymentNewEvent(payment_id=uuid.uuid4())

    await retry_or_dead_letter(cast(RabbitBroker, broker), topology, settings.consumer, event, 2)

    _, kwargs = broker.published[0]
    assert kwargs["queue"].name == "payments.new.dlq"
    assert kwargs["exchange"].name == "payments.dlx"
    assert kwargs["routing_key"] == "payments.new"
    assert kwargs["headers"] == {"x-retry-count": 2}


async def test_dlq_carries_error_context() -> None:
    broker = FakeBroker()
    settings = make_settings()
    topology = build_topology(settings)
    event = PaymentNewEvent(payment_id=uuid.uuid4())
    error = RuntimeError("webhook delivery failed")

    await retry_or_dead_letter(
        cast(RabbitBroker, broker), topology, settings.consumer, event, 2, error=error
    )

    _, kwargs = broker.published[0]
    assert kwargs["headers"] == {
        "x-retry-count": 2,
        "x-error-type": "RuntimeError",
        "x-error-message": "webhook delivery failed",
    }


async def test_dlq_error_message_is_truncated() -> None:
    broker = FakeBroker()
    settings = make_settings()
    topology = build_topology(settings)
    event = PaymentNewEvent(payment_id=uuid.uuid4())
    error = RuntimeError("x" * 1000)

    await retry_or_dead_letter(
        cast(RabbitBroker, broker), topology, settings.consumer, event, 2, error=error
    )

    _, kwargs = broker.published[0]
    assert len(kwargs["headers"]["x-error-message"]) == 500


def test_more_retries_than_delays_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ConsumerSettings(max_retries=3, retry_delays_seconds=[2.0, 4.0])


def test_consistent_retry_settings_are_accepted() -> None:
    settings = ConsumerSettings(max_retries=2, retry_delays_seconds=[2.0, 4.0])
    assert settings.max_retries == 2
    assert settings.retry_delays_seconds == [2.0, 4.0]


async def test_malformed_json_is_dead_lettered_with_raw_body() -> None:
    broker = FakeBroker()
    msg, processor = await _handle(broker, b"this is not json")

    message, kwargs = broker.published[0]
    assert message == b"this is not json"  # raw body preserved for inspection
    assert kwargs["queue"].name == "payments.new.dlq"
    assert kwargs["exchange"].name == "payments.dlx"
    assert kwargs["routing_key"] == "payments.new"
    assert kwargs["headers"]["x-error-type"] == "ValidationError"
    assert processor.processed == []
    assert msg.rejected == []  # the original message is acked, not rejected


async def test_missing_payment_id_is_dead_lettered() -> None:
    broker = FakeBroker()
    msg, processor = await _handle(broker, b'{"event_version": 1}')

    _, kwargs = broker.published[0]
    assert kwargs["queue"].name == "payments.new.dlq"
    assert kwargs["headers"]["x-error-type"] == "ValidationError"
    assert processor.processed == []
    assert msg.rejected == []


async def test_unsupported_event_version_is_dead_lettered() -> None:
    broker = FakeBroker()
    event = PaymentNewEvent(payment_id=uuid.uuid4(), event_version=EVENT_VERSION + 1)
    msg, processor = await _handle(broker, event.model_dump_json().encode())

    _, kwargs = broker.published[0]
    assert kwargs["queue"].name == "payments.new.dlq"
    assert kwargs["headers"]["x-error-type"] == "unsupported_event_version"
    assert processor.processed == []
    assert msg.rejected == []


async def test_processing_failure_goes_to_retry_queue() -> None:
    broker = FakeBroker()
    event = PaymentNewEvent(payment_id=uuid.uuid4())
    msg, processor = await _handle(
        broker, event.model_dump_json().encode(), processor_error=RuntimeError("boom")
    )

    message, kwargs = broker.published[0]
    assert message == event
    assert kwargs["queue"].name == "payments.new.retry.2s"
    assert kwargs["headers"] == {RETRY_COUNT_HEADER: 1}
    assert len(processor.processed) == 1
    assert msg.rejected == []


async def test_reroute_publish_failure_rejects_without_requeue() -> None:
    """When the retry publish itself fails, the original message must not be
    requeued (that would spin it through the work queue with no retry delay);
    it is rejected instead so the queue dead-letter exchange routes it to the
    DLQ."""
    broker = FailingBroker()
    event = PaymentNewEvent(payment_id=uuid.uuid4())
    msg, processor = await _handle(
        broker, event.model_dump_json().encode(), processor_error=RuntimeError("boom")
    )

    assert broker.published == []  # reroute publish failed
    assert len(processor.processed) == 1
    assert msg.rejected == [False]  # rejected, not requeued
