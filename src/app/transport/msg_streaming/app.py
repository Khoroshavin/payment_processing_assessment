"""FastStream consumer application consuming payments.new."""

import logging

from faststream import FastStream

from app.core.configs.settings import Settings
from app.core.orm.session_factory import get_session_factory
from app.domains.payments.adapters.emulated_gateway import PaymentGateway
from app.domains.payments.adapters.webhook import WebhookClient
from app.domains.payments.services.processing import PaymentProcessor
from app.infra.messaging.broker import build_rabbit_broker
from app.infra.messaging.consumer import register_consumer
from app.infra.messaging.topology import build_topology, declare_topology


def build_consumer_app(settings: Settings) -> FastStream:
    broker = build_rabbit_broker(
        settings.rabbitmq.dsn, logger=logging.getLogger("app.consumer")
    )
    topology = build_topology(settings)
    webhook = WebhookClient(settings.webhook)
    processor = PaymentProcessor(
        get_session_factory(), PaymentGateway(settings.emulation), webhook
    )
    register_consumer(broker, topology, settings.consumer, processor)

    app = FastStream(broker)

    @app.after_startup
    async def declare_queues() -> None:
        await declare_topology(broker, topology)

    @app.after_shutdown
    async def close_webhook() -> None:
        await webhook.close()

    return app
