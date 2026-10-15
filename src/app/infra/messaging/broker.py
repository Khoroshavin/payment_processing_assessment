import logging

from faststream.rabbit import RabbitBroker


def build_rabbit_broker(url: str, logger: logging.Logger | None = None) -> RabbitBroker:
    """RabbitMQ broker.

    Publishes use publisher confirms (aio-pika default). Passing an explicit
    plain stdlib logger prevents FastStream from installing its own
    formatter/handler, so all broker logs flow through the service-wide
    logging config.
    """
    return RabbitBroker(url, logger=logger)
