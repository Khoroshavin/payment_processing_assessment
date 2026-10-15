"""Infrastructure helpers for API tests: containers, settings parsing, migrations."""

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

from app.core.configs.settings import PostgresSettings, RabbitMQSettings

ROOT_DIR = Path(__file__).resolve().parents[3]


def start_test_containers() -> Iterator[tuple[str, str]]:
    """Yield (database_url, amqp_url) for PostgreSQL and RabbitMQ test containers."""
    from testcontainers.community.postgres import PostgresContainer
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import (
        CompositeWaitStrategy,
        LogMessageWaitStrategy,
        PortWaitStrategy,
    )

    # Plain DockerContainer + structured wait strategies instead of the
    # deprecated RabbitMqContainer (its @wait_container_is_ready decorator
    # raises DeprecationWarning on import in testcontainers >= 4.15).
    # PortWaitStrategy alone is not enough: RabbitMQ opens the AMQP listener
    # before the broker is fully booted and resets early connections, so we
    # additionally wait for the startup log line.
    rabbitmq = (
        DockerContainer("rabbitmq:4.1.2-management")
        .with_env("RABBITMQ_DEFAULT_USER", "guest")
        .with_env("RABBITMQ_DEFAULT_PASS", "guest")
        .with_exposed_ports(5672)
        .waiting_for(
            CompositeWaitStrategy(
                PortWaitStrategy(5672),
                LogMessageWaitStrategy("Server startup complete"),
            )
        )
    )

    with PostgresContainer("postgres:18-alpine") as postgres, rabbitmq:
        database_url = postgres.get_connection_url()
        amqp_url = (
            f"amqp://guest:guest@{rabbitmq.get_container_host_ip()}:"
            f"{rabbitmq.get_exposed_port(5672)}/"
        )
        yield database_url, amqp_url


def parse_postgres_url(url: str) -> PostgresSettings:
    parsed = urlparse(url)
    return PostgresSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "",
        db=(parsed.path or "/payments").lstrip("/"),
    )


def parse_rabbitmq_url(url: str) -> RabbitMQSettings:
    parsed = urlparse(url)
    return RabbitMQSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5672,
        user=parsed.username or "guest",
        password=parsed.password or "guest",
    )


def run_migrations(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT_DIR / "src" / "alembic.ini"))
    command.upgrade(cfg, "head")
