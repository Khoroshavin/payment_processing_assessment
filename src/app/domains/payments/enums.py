"""Domain enums."""

from enum import StrEnum


def enum_values(enum_class: type[StrEnum]) -> str:
    """Читаемое перечисление значений enum (для документации)."""
    return ", ".join(member.value for member in enum_class)


class Currency(StrEnum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL_PAYMENT_STATUSES: frozenset[str] = frozenset(
    {PaymentStatus.SUCCEEDED, PaymentStatus.FAILED}
)


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    # Terminal state for poisoned rows (payload that can never be parsed):
    # the relay marks them dead for manual operator action. Failed *publishes*
    # are still retried forever and never become dead.
    DEAD = "dead"
