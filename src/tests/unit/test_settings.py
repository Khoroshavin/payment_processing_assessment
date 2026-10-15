"""Settings validation tests."""

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.configs.settings import (
    AppSettings,
    ConsumerSettings,
    EmulationSettings,
    PostgresSettings,
    RabbitMQSettings,
    RelaySettings,
    WebhookSettings,
)

STRONG_API_KEY: str = "a-strong-api-key-0123456789abcdef"


def test_production_rejects_insecure_default_api_key() -> None:
    with pytest.raises(ValidationError, match="API_KEY"):
        AppSettings(env="production", api_key="dev-secret-change-me")


def test_production_rejects_short_api_key() -> None:
    with pytest.raises(ValidationError, match="API_KEY"):
        AppSettings(env="production", api_key="short-key")


def test_production_accepts_explicit_strong_api_key() -> None:
    settings = AppSettings(env="production", api_key=STRONG_API_KEY)
    assert settings.api_key == STRONG_API_KEY


def test_staging_rejects_insecure_default_api_key() -> None:
    with pytest.raises(ValidationError, match="API_KEY"):
        AppSettings(env="staging", api_key="dev-secret-change-me")


def test_dev_keeps_default_api_key() -> None:
    settings = AppSettings(env="dev", api_key="dev-secret-change-me")
    assert settings.api_key == "dev-secret-change-me"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_retries": -1, "retry_delays_seconds": [2.0, 4.0]},
        {"max_retries": 2, "retry_delays_seconds": [2.0, -4.0]},
        {"max_retries": 2, "retry_delays_seconds": [0.0, 4.0]},
    ],
)
def test_consumer_rejects_invalid_retry_values(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        ConsumerSettings(**kwargs)


def test_zero_retries_with_empty_delays_is_allowed() -> None:
    settings = ConsumerSettings(max_retries=0, retry_delays_seconds=[])
    assert settings.max_retries == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"success_rate": 1.5},
        {"success_rate": -0.1},
        {"min_delay_seconds": -1.0},
        {"max_delay_seconds": -1.0},
        {"min_delay_seconds": 5.0, "max_delay_seconds": 2.0},
    ],
)
def test_emulation_rejects_invalid_values(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        EmulationSettings(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"poll_interval_seconds": -0.5},
        {"batch_size": 0},
        {"backoff_base_seconds": -1.0},
        {"backoff_cap_seconds": -1.0},
        {"backoff_base_seconds": 60.0, "backoff_cap_seconds": 1.0},
    ],
)
def test_relay_rejects_invalid_values(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        RelaySettings(**kwargs)


def test_webhook_rejects_negative_timeout() -> None:
    with pytest.raises(ValidationError):
        WebhookSettings(timeout_seconds=-1.0)


def test_postgres_dsn_encodes_special_characters() -> None:
    settings = PostgresSettings(user="user", password="p@ss:word/x#1", host="db", db="payments")
    url = make_url(settings.dsn)
    assert url.password == "p@ss:word/x#1"
    assert url.host == "db"
    assert url.database == "payments"


def test_rabbitmq_dsn_encodes_special_characters() -> None:
    settings = RabbitMQSettings(user="user", password="p@ss:word/x#1")
    assert settings.dsn == "amqp://user:p%40ss%3Aword%2Fx%231@localhost:5672/"
