"""RabbitMQ topology: exchange, work queue, TTL retry queues, DLX and DLQ."""

from dataclasses import dataclass

from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue

from app.core.configs.settings import Settings


@dataclass(frozen=True)
class Topology:
    exchange: RabbitExchange
    queue: RabbitQueue
    retry_queues: tuple[RabbitQueue, ...]
    dlx: RabbitExchange
    dlq: RabbitQueue


def build_topology(settings: Settings) -> Topology:
    rabbitmq = settings.rabbitmq
    exchange = RabbitExchange(rabbitmq.exchange_name, type=ExchangeType.DIRECT, durable=True)
    # RabbitMQ-native dead-lettering for the work queue: rejected messages
    # (requeue=False) and any unhandled consumer errors land in the DLQ via
    # the DLX instead of being requeued into a busy loop or dropped.
    queue = RabbitQueue(
        rabbitmq.queue_name,
        durable=True,
        routing_key=rabbitmq.queue_name,
        arguments={
            "x-dead-letter-exchange": rabbitmq.dlx_name,
            "x-dead-letter-routing-key": rabbitmq.dlq_routing_key,
        },
    )

    retry_queues = tuple(
        RabbitQueue(
            f"{rabbitmq.queue_name}.retry.{delay:g}s",
            durable=True,
            arguments={
                "x-message-ttl": int(delay * 1000),
                "x-dead-letter-exchange": rabbitmq.exchange_name,
                "x-dead-letter-routing-key": rabbitmq.queue_name,
            },
        )
        for delay in settings.consumer.retry_delays_seconds
    )

    dlx = RabbitExchange(rabbitmq.dlx_name, type=ExchangeType.DIRECT, durable=True)
    dlq = RabbitQueue(rabbitmq.dlq_name, durable=True, routing_key=rabbitmq.dlq_routing_key)
    return Topology(exchange=exchange, queue=queue, retry_queues=retry_queues, dlx=dlx, dlq=dlq)


async def declare_topology(broker: RabbitBroker, topology: Topology) -> None:
    """Declare exchanges, queues and bindings (idempotent, requires started broker)."""
    exchange = await broker.declare_exchange(topology.exchange)
    queue = await broker.declare_queue(topology.queue)
    await queue.bind(exchange, routing_key=topology.queue.routing())

    for retry_queue in topology.retry_queues:
        await broker.declare_queue(retry_queue)

    dlx = await broker.declare_exchange(topology.dlx)
    dlq = await broker.declare_queue(topology.dlq)
    await dlq.bind(dlx, routing_key=topology.dlq.routing())
