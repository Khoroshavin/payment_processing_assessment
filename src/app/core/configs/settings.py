"""Service settings: small pydantic-settings groups per concern.

Each group reads its own environment variables (see .env.example); DSNs are
derived properties, so host/port/user/password are never duplicated between
config and compose.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Self
from urllib.parse import quote

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

PRODUCTION_ENVIRONMENTS: frozenset[str] = frozenset({"production", "staging"})
INSECURE_DEFAULT_API_KEY: str = "dev-secret-change-me"
MIN_API_KEY_LENGTH: int = 16

# Field type helpers reused by several settings groups.
PositiveFloat = Annotated[float, Field(gt=0)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"
    # Цветной консольный логгер (dev): always | never | auto
    log_colors: str = "auto"

    api_key: str = INSECURE_DEFAULT_API_KEY
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 1

    @model_validator(mode="after")
    def _check_production_api_key(self) -> Self:
        if self.env.lower() not in PRODUCTION_ENVIRONMENTS:
            return self
        if self.api_key == INSECURE_DEFAULT_API_KEY:
            raise ValueError(
                "API_KEY must be set to a strong secret when ENV is "
                f"{self.env!r} (the insecure default is not allowed in production)"
            )
        if len(self.api_key) < MIN_API_KEY_LENGTH:
            raise ValueError(
                f"API_KEY must be at least {MIN_API_KEY_LENGTH} characters when ENV is "
                f"{self.env!r} (got {len(self.api_key)})"
            )
        return self


class PostgresSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="POSTGRES_", extra="ignore"
    )

    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = "postgres"
    db: str = "payments"

    @property
    def dsn(self) -> str:
        """URL-encoded DSN: credentials with special characters stay intact."""
        return URL.create(
            "postgresql+asyncpg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.db,
        ).render_as_string(hide_password=False)


class RabbitMQSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="RABBITMQ_", extra="ignore"
    )

    host: str = "localhost"
    port: int = 5672
    user: str = "guest"
    password: str = "guest"

    exchange_name: str = "payments"
    queue_name: str = "payments.new"
    dlx_name: str = "payments.dlx"
    dlq_name: str = "payments.new.dlq"
    dlq_routing_key: str = "payments.new"

    @property
    def dsn(self) -> str:
        """Percent-encoded DSN: credentials with reserved characters stay intact."""
        user = quote(self.user, safe="")
        password = quote(self.password, safe="")
        host = quote(self.host, safe="")
        return f"amqp://{user}:{password}@{host}:{self.port}/"


class EmulationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="EMULATION_", extra="ignore"
    )

    min_delay_seconds: float = Field(default=2.0, ge=0)
    max_delay_seconds: float = Field(default=5.0, ge=0)
    success_rate: float = Field(default=0.9, ge=0, le=1)

    @model_validator(mode="after")
    def _check_delay_bounds(self) -> Self:
        if self.min_delay_seconds > self.max_delay_seconds:
            raise ValueError(
                "EMULATION_MIN_DELAY_SECONDS must not exceed EMULATION_MAX_DELAY_SECONDS"
            )
        return self


class RelaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="RELAY_", extra="ignore"
    )

    poll_interval_seconds: PositiveFloat = 0.5
    batch_size: PositiveInt = 100
    max_attempts: NonNegativeInt = 10  # log-escalation threshold, not a give-up limit
    backoff_base_seconds: PositiveFloat = 1.0
    backoff_cap_seconds: PositiveFloat = 60.0

    @model_validator(mode="after")
    def _check_backoff_bounds(self) -> Self:
        if self.backoff_cap_seconds < self.backoff_base_seconds:
            raise ValueError(
                "RELAY_BACKOFF_CAP_SECONDS must not be less than RELAY_BACKOFF_BASE_SECONDS"
            )
        return self


class ConsumerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="CONSUMER_", extra="ignore"
    )

    max_retries: NonNegativeInt = 2
    retry_delays_seconds: list[PositiveFloat] = [2.0, 4.0]

    @model_validator(mode="after")
    def _check_retry_delays(self) -> Self:
        if len(self.retry_delays_seconds) < self.max_retries:
            raise ValueError(
                "CONSUMER_RETRY_DELAYS_SECONDS must contain at least "
                "CONSUMER_MAX_RETRIES entries (one delay per retry), "
                f"got {len(self.retry_delays_seconds)} delays for {self.max_retries} retries"
            )
        return self


class WebhookSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="WEBHOOK_", extra="ignore"
    )

    timeout_seconds: PositiveFloat = 5.0


@dataclass(frozen=True)
class Settings:
    app: AppSettings
    postgres: PostgresSettings
    rabbitmq: RabbitMQSettings
    emulation: EmulationSettings
    relay: RelaySettings
    consumer: ConsumerSettings
    webhook: WebhookSettings


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app=AppSettings(),
        postgres=PostgresSettings(),
        rabbitmq=RabbitMQSettings(),
        emulation=EmulationSettings(),
        relay=RelaySettings(),
        consumer=ConsumerSettings(),
        webhook=WebhookSettings(),
    )


def reset_settings() -> None:
    get_settings.cache_clear()
