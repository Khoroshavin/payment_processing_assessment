"""Outbox relay entrypoint: a separate service polling the outbox table."""

import asyncio
import signal

from app.core.configs.settings import get_settings
from app.core.logging import setup_logging
from app.core.orm.session_factory import configure_database, get_session_factory
from app.domains.payments.services.outbox_relay import OutboxRelay
from app.infra.messaging.broker import build_rabbit_broker
from app.infra.messaging.topology import build_topology, declare_topology


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.app.log_level, settings.app.env, log_colors=settings.app.log_colors)
    configure_database(settings.postgres.dsn)

    broker = build_rabbit_broker(settings.rabbitmq.dsn)
    topology = build_topology(settings)
    relay = OutboxRelay(broker, topology, get_session_factory(), settings.relay)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, relay.stop)

    await broker.start()
    await declare_topology(broker, topology)
    try:
        await relay.run()
    finally:
        await broker.stop()


if __name__ == "__main__":
    asyncio.run(main())
