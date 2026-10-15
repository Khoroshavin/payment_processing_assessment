"""API/end-to-end test fixtures."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from app.core.configs.settings import (
    AppSettings,
    ConsumerSettings,
    EmulationSettings,
    RelaySettings,
    Settings,
    WebhookSettings,
    reset_settings,
)
from app.core.orm.session_factory import configure_database, get_session_factory
from tests.fixtures.helpers import MockWebhookServer, WaitUntil, wait_for
from tests.fixtures.infrastructure import (
    parse_postgres_url,
    parse_rabbitmq_url,
    run_migrations,
    start_test_containers,
)


@pytest.fixture(scope="session")
def infrastructure() -> Iterator[tuple[str, str]]:
    """PostgreSQL and RabbitMQ test containers.

    These tests prove the real delivery guarantees, so a missing Docker
    daemon is a hard failure, not a silent skip.
    """
    try:
        yield from start_test_containers()
    except ImportError:
        pytest.fail("testcontainers is not installed (run `uv sync --all-groups`)")
    except Exception as exc:
        pytest.fail(
            "docker is not available; integration tests require a running "
            f"docker daemon: {exc}"
        )


@pytest.fixture(scope="session")
def test_settings(infrastructure: tuple[str, str]) -> Settings:
    database_url, amqp_url = infrastructure
    return Settings(
        app=AppSettings(api_key="test-api-key", log_level="WARNING"),
        postgres=parse_postgres_url(database_url),
        rabbitmq=parse_rabbitmq_url(amqp_url),
        emulation=EmulationSettings(
            min_delay_seconds=0.0, max_delay_seconds=0.05, success_rate=1.0
        ),
        relay=RelaySettings(
            poll_interval_seconds=0.05,
            batch_size=50,
            max_attempts=5,
            backoff_base_seconds=0.1,
            backoff_cap_seconds=1.0,
        ),
        consumer=ConsumerSettings(max_retries=2, retry_delays_seconds=[0.2, 0.4]),
        webhook=WebhookSettings(timeout_seconds=2.0),
    )


@pytest.fixture(scope="session", autouse=True)
def database(test_settings: Settings) -> Iterator[None]:
    os.environ.update(
        {
            "POSTGRES_HOST": test_settings.postgres.host,
            "POSTGRES_PORT": str(test_settings.postgres.port),
            "POSTGRES_USER": test_settings.postgres.user,
            "POSTGRES_PASSWORD": test_settings.postgres.password,
            "POSTGRES_DB": test_settings.postgres.db,
        }
    )
    reset_settings()
    configure_database(test_settings.postgres.dsn)
    run_migrations(test_settings.postgres.dsn)
    yield


@pytest.fixture
def wait_until() -> WaitUntil:
    return wait_for


@pytest.fixture
async def api_client(database: None) -> AsyncIterator[httpx.AsyncClient]:
    from app.transport.rest.app import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def running_consumer(test_settings: Settings, database: None) -> AsyncIterator[None]:
    from app.domains.payments.adapters.emulated_gateway import PaymentGateway
    from app.domains.payments.adapters.webhook import WebhookClient
    from app.domains.payments.services.processing import PaymentProcessor
    from app.infra.messaging.broker import build_rabbit_broker
    from app.infra.messaging.consumer import register_consumer
    from app.infra.messaging.topology import build_topology, declare_topology

    broker = build_rabbit_broker(
        test_settings.rabbitmq.dsn, logger=logging.getLogger("app.consumer")
    )
    webhook = WebhookClient(test_settings.webhook)
    processor = PaymentProcessor(
        get_session_factory(), PaymentGateway(test_settings.emulation), webhook
    )
    topology = build_topology(test_settings)
    register_consumer(broker, topology, test_settings.consumer, processor)
    await broker.start()
    await declare_topology(broker, topology)
    try:
        yield
    finally:
        await broker.stop()
        await webhook.close()


@pytest.fixture
async def running_relay(test_settings: Settings, database: None) -> AsyncIterator[None]:
    from app.domains.payments.services.outbox_relay import OutboxRelay
    from app.infra.messaging.broker import build_rabbit_broker
    from app.infra.messaging.topology import build_topology, declare_topology

    broker = build_rabbit_broker(test_settings.rabbitmq.dsn)
    topology = build_topology(test_settings)
    relay = OutboxRelay(broker, topology, get_session_factory(), test_settings.relay)
    await broker.start()
    await declare_topology(broker, topology)
    task = asyncio.create_task(relay.run())
    try:
        yield
    finally:
        relay.stop()
        await task
        await broker.stop()


@pytest.fixture
async def webhook_server() -> AsyncIterator[MockWebhookServer]:
    server = MockWebhookServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def failing_webhook_server() -> AsyncIterator[MockWebhookServer]:
    server = MockWebhookServer(response_status=500)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()
