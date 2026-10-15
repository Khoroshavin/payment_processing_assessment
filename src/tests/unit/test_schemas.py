from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.domains.payments.dto import PaymentCreateRequest
from app.domains.payments.enums import Currency

BASE: dict[str, Any] = {
    "amount": "99.99",
    "currency": "USD",
    "description": "test payment",
    "webhook_url": "https://example.com/hook",
}


def test_valid_request() -> None:
    request = PaymentCreateRequest.model_validate(BASE)
    assert request.amount == Decimal("99.99")
    assert request.currency is Currency.USD
    assert request.metadata == {}


@pytest.mark.parametrize(
    "amount",
    ["0", "-1", "-0.01", "0.001", "1.999", "999999999999999999.99", "abc"],
)
def test_invalid_amount(amount: str) -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate({**BASE, "amount": amount})


def test_invalid_currency() -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate({**BASE, "currency": "GBP"})


@pytest.mark.parametrize("currency", [currency.value for currency in Currency])
def test_all_currency_values_accepted(currency: str) -> None:
    request = PaymentCreateRequest.model_validate({**BASE, "currency": currency})
    assert request.currency is Currency(currency)


def test_currency_schema_documents_all_values() -> None:
    schema = PaymentCreateRequest.model_json_schema()
    currency_schema = schema["$defs"]["Currency"]
    assert currency_schema["enum"] == [currency.value for currency in Currency]
    assert "Валюта платежа" in schema["properties"]["currency"]["description"]


def test_missing_description() -> None:
    payload = {key: value for key, value in BASE.items() if key != "description"}
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate(payload)


def test_empty_description() -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate({**BASE, "description": ""})


def test_too_long_description() -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate({**BASE, "description": "x" * 256})


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate({**BASE, "unexpected": 1})


def test_webhook_url_must_be_http() -> None:
    with pytest.raises(ValidationError):
        PaymentCreateRequest.model_validate({**BASE, "webhook_url": "ftp://example.com/hook"})


def test_metadata_accepts_nested_json() -> None:
    request = PaymentCreateRequest.model_validate({**BASE, "metadata": {"nested": {"a": 1}}})
    assert request.metadata == {"nested": {"a": 1}}
