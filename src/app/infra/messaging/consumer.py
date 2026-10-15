"""Consumer of the payments.new queue.

One handler does everything: emulate gateway processing, update the payment
status and notify the client webhook. Failures are re-routed through TTL
retry queues (exponential backoff) and finally to the dead-letter queue.
Messages with an unsupported event version are dead-lettered immediately:
retrying them cannot help.

The body is parsed manually inside the handler instead of relying on the
FastStream declared payload type: a malformed message (invalid JSON, missing
`payment_id`, wrong types) would otherwise fail decoding *before* the handler
and be nacked straight back into the work queue forever. The handler
dead-letters such messages directly, preserving the raw body.

Unexpected escapes use `AckPolicy.REJECT_ON_ERROR`: the work queue declares
`x-dead-letter-exchange`, so a rejected message is dead-lettered by RabbitMQ
itself (original body and headers preserved) rather than requeued into a
busy loop — this also covers a failed retry/DLQ re-publication.
"""

import logging
from typing import Any

from faststream import AckPolicy
from faststream.rabbit import RabbitBroker, RabbitMessage
from pydantic import ValidationError

from app.core.configs.settings import ConsumerSettings
from app.domains.payments.services.processing import PaymentProcessor
from app.infra.messaging.events import EVENT_VERSION, PAYMENT_NEW_EVENT, PaymentNewEvent
from app.infra.messaging.topology import Topology

logger = logging.getLogger(__name__)

RETRY_COUNT_HEADER: str = "x-retry-count"
ERROR_TYPE_HEADER: str = "x-error-type"
ERROR_MESSAGE_HEADER: str = "x-error-message"

MAX_ERROR_MESSAGE_LENGTH: int = 500


def _read_retry_count(msg: RabbitMessage) -> int:
    headers = getattr(msg, "headers", None) or {}
    try:
        return int(headers.get(RETRY_COUNT_HEADER, 0))
    except (TypeError, ValueError):
        return 0


def _error_headers(error: Exception, retry_count: int) -> dict[str, Any]:
    """Headers describing a processing failure for DLQ inspection."""
    return {
        RETRY_COUNT_HEADER: retry_count,
        ERROR_TYPE_HEADER: type(error).__name__,
        ERROR_MESSAGE_HEADER: str(error)[:MAX_ERROR_MESSAGE_LENGTH],
    }


async def _publish_to_dlq(
    broker: RabbitBroker,
    topology: Topology,
    event: PaymentNewEvent,
    headers: dict[str, Any],
) -> None:
    await broker.publish(
        event,
        queue=topology.dlq,
        exchange=topology.dlx,
        routing_key=topology.dlq.routing(),
        persist=True,
        headers=headers,
    )


async def _publish_raw_to_dlq(
    broker: RabbitBroker,
    topology: Topology,
    body: bytes,
    headers: dict[str, Any],
) -> None:
    """Publish a raw (possibly malformed) body to the DLQ for inspection.

    The raw body is preserved verbatim so an operator can see exactly what
    could not be parsed.
    """
    await broker.publish(
        body,
        queue=topology.dlq,
        exchange=topology.dlx,
        routing_key=topology.dlq.routing(),
        persist=True,
        headers=headers,
        content_type="application/json",
    )


async def retry_or_dead_letter(
    broker: RabbitBroker,
    topology: Topology,
    settings: ConsumerSettings,
    event: PaymentNewEvent,
    retry_count: int,
    error: Exception | None = None,
) -> None:
    if retry_count < settings.max_retries:
        retry_queue = topology.retry_queues[retry_count]
        logger.warning(
            "payment %s attempt %s failed, rescheduling via queue %s",
            event.payment_id,
            retry_count + 1,
            retry_queue.name,
        )
        await broker.publish(
            event,
            queue=retry_queue,
            headers={RETRY_COUNT_HEADER: retry_count + 1},
            persist=True,
        )
    else:
        headers: dict[str, Any] = {RETRY_COUNT_HEADER: retry_count}
        if error is not None:
            headers.update(_error_headers(error, retry_count))
        logger.error(
            "payment %s failed after %s attempts, moving to dead-letter queue",
            event.payment_id,
            retry_count + 1,
        )
        await _publish_to_dlq(broker, topology, event, headers)


async def handle_payment_new(
    broker: RabbitBroker,
    topology: Topology,
    settings: ConsumerSettings,
    processor: PaymentProcessor,
    msg: RabbitMessage,
) -> None:
    retry_count = _read_retry_count(msg)
    try:
        event = PaymentNewEvent.model_validate_json(msg.body)
    except ValidationError as exc:
        logger.error(
            "malformed %s message rejected, raw body moved to dead-letter queue: %s",
            PAYMENT_NEW_EVENT,
            exc,
        )
        await _publish_raw_to_dlq(
            broker,
            topology,
            msg.body,
            headers={
                RETRY_COUNT_HEADER: retry_count,
                ERROR_TYPE_HEADER: type(exc).__name__,
                ERROR_MESSAGE_HEADER: str(exc)[:MAX_ERROR_MESSAGE_LENGTH],
            },
        )
        return
    if event.event_version != EVENT_VERSION:
        logger.error(
            "payment %s carries unsupported event_version %s (supported: %s), "
            "moving to dead-letter queue",
            event.payment_id,
            event.event_version,
            EVENT_VERSION,
        )
        await _publish_to_dlq(
            broker,
            topology,
            event,
            headers={
                RETRY_COUNT_HEADER: retry_count,
                ERROR_TYPE_HEADER: "unsupported_event_version",
                ERROR_MESSAGE_HEADER: (
                    f"event_version={event.event_version}, supported={EVENT_VERSION}"
                ),
            },
        )
        return
    try:
        await processor.process(event.payment_id)
    except Exception as exc:
        logger.exception("failed to process payment %s", event.payment_id)
        try:
            await retry_or_dead_letter(broker, topology, settings, event, retry_count, error=exc)
        except Exception:
            # The broker is unreachable for re-publication: requeueing the
            # original message would spin it through the work queue without
            # any retry delay. Reject it instead — the work queue's
            # dead-letter exchange routes it to the DLQ.
            logger.exception(
                "reroute publish failed for payment %s, rejecting the original "
                "message for queue dead-lettering",
                event.payment_id,
            )
            await msg.reject(requeue=False)


def register_consumer(
    broker: RabbitBroker,
    topology: Topology,
    settings: ConsumerSettings,
    processor: PaymentProcessor,
) -> None:
    @broker.subscriber(
        topology.queue,
        exchange=topology.exchange,
        ack_policy=AckPolicy.REJECT_ON_ERROR,
    )
    async def on_payment_new(msg: RabbitMessage) -> None:
        await handle_payment_new(broker, topology, settings, processor, msg)
