"""Canonical request-body hash tests: the idempotency hash must depend only
on the JSON value, not on the key order inside nested objects."""

from decimal import Decimal
from typing import Any

from app.domains.payments.dto import PaymentCreateRequest
from app.domains.payments.services.payment_service import request_body_hash

BASE: dict[str, Any] = {
    "amount": Decimal("1500.50"),
    "currency": "RUB",
    "description": "hash test payment",
    "webhook_url": "https://example.com/hook",
}


def make_payload(**overrides: Any) -> PaymentCreateRequest:
    return PaymentCreateRequest(**{**BASE, **overrides})


def test_metadata_key_order_does_not_change_hash() -> None:
    first = make_payload(metadata={"order_id": "1", "note": "x"})
    second = make_payload(metadata={"note": "x", "order_id": "1"})

    assert request_body_hash(first) == request_body_hash(second)


def test_nested_metadata_key_order_does_not_change_hash() -> None:
    first = make_payload(metadata={"outer": {"a": 1, "b": {"x": 1, "y": 2}}})
    second = make_payload(metadata={"outer": {"b": {"y": 2, "x": 1}, "a": 1}})

    assert request_body_hash(first) == request_body_hash(second)


def test_different_metadata_values_change_hash() -> None:
    first = make_payload(metadata={"order_id": "1"})
    second = make_payload(metadata={"order_id": "2"})

    assert request_body_hash(first) != request_body_hash(second)


def test_different_amounts_change_hash() -> None:
    first = make_payload()
    second = make_payload(amount=Decimal("1500.51"))

    assert request_body_hash(first) != request_body_hash(second)


def test_hash_is_deterministic() -> None:
    payload = make_payload(metadata={"a": [1, 2, {"x": "y"}]})

    assert request_body_hash(payload) == request_body_hash(payload)
