import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.core.orm.session_factory import get_session_factory
from app.domains.payments.models import OutboxEvent, Payment

pytestmark = pytest.mark.integration

AUTH_HEADERS: dict[str, str] = {"X-API-Key": "test-api-key"}

BODY: dict[str, Any] = {
    "amount": "1500.50",
    "currency": "RUB",
    "description": "api test payment",
    "metadata": {"order_id": "42"},
    "webhook_url": "http://8.8.8.8:8080/hook",
}


async def test_create_payment_returns_202_and_writes_outbox(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.post(
        "/api/v1/payments",
        json=BODY,
        headers={**AUTH_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "pending"
    assert data["created_at"]

    async with get_session_factory()() as session:
        payment = await session.get(Payment, uuid.UUID(data["payment_id"]))
        assert payment is not None
        assert str(payment.amount) == "1500.50"
        assert payment.currency == "RUB"
        assert payment.metadata_ == {"order_id": "42"}

        outbox_row = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.aggregate_id == payment.id)
            )
        ).scalar_one()
        assert outbox_row.event_type == "payments.new"
        assert outbox_row.status == "pending"
        assert outbox_row.payload["payment_id"] == data["payment_id"]


async def test_duplicate_idempotency_key_returns_200_with_replayed_header(
    api_client: httpx.AsyncClient,
) -> None:
    headers = {**AUTH_HEADERS, "Idempotency-Key": str(uuid.uuid4())}
    first = await api_client.post("/api/v1/payments", json=BODY, headers=headers)
    second = await api_client.post("/api/v1/payments", json=BODY, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.headers["idempotency-replayed"] == "true"
    assert second.json() == first.json()


async def test_same_idempotency_key_with_different_body_returns_409(
    api_client: httpx.AsyncClient,
) -> None:
    headers = {**AUTH_HEADERS, "Idempotency-Key": str(uuid.uuid4())}
    first = await api_client.post("/api/v1/payments", json=BODY, headers=headers)
    second = await api_client.post(
        "/api/v1/payments", json={**BODY, "amount": "999.99"}, headers=headers
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_conflict"


async def test_replay_lookup_precedes_webhook_url_validation(
    api_client: httpx.AsyncClient,
) -> None:
    """Same key + different body with an unsafe URL must give 409, not 422:
    idempotency lookup happens before webhook_url validation."""
    headers = {**AUTH_HEADERS, "Idempotency-Key": str(uuid.uuid4())}
    first = await api_client.post("/api/v1/payments", json=BODY, headers=headers)
    second = await api_client.post(
        "/api/v1/payments",
        json={**BODY, "webhook_url": "http://127.0.0.1:9999/hook"},
        headers=headers,
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_conflict"


async def test_missing_idempotency_key_returns_422(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post("/api/v1/payments", json=BODY, headers=AUTH_HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_empty_idempotency_key_returns_422(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/payments",
        json=BODY,
        headers={**AUTH_HEADERS, "Idempotency-Key": ""},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_too_long_idempotency_key_returns_422(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/payments",
        json=BODY,
        headers={**AUTH_HEADERS, "Idempotency-Key": "k" * 256},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_missing_api_key_returns_401(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/payments",
        json=BODY,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_invalid_api_key_returns_403(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(
        f"/api/v1/payments/{uuid.uuid4()}", headers={"X-API-Key": "wrong-key"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_get_payment_returns_404_for_unknown_id(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(
        f"/api/v1/payments/{uuid.uuid4()}", headers=AUTH_HEADERS
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_get_payment_returns_full_detail(api_client: httpx.AsyncClient) -> None:
    idempotency_key = str(uuid.uuid4())
    created = await api_client.post(
        "/api/v1/payments",
        json=BODY,
        headers={**AUTH_HEADERS, "Idempotency-Key": idempotency_key},
    )
    payment_id = created.json()["payment_id"]

    response = await api_client.get(f"/api/v1/payments/{payment_id}", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["payment_id"] == payment_id
    assert data["amount"] == "1500.50"
    assert data["currency"] == "RUB"
    assert data["description"] == "api test payment"
    assert data["metadata"] == {"order_id": "42"}
    assert data["status"] == "pending"
    assert data["idempotency_key"] == idempotency_key
    assert data["webhook_url"] == "http://8.8.8.8:8080/hook"
    assert data["processed_at"] is None


async def test_invalid_amount_rejected(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/payments",
        json={**BODY, "amount": "-5"},
        headers={**AUTH_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_invalid_currency_rejected(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/payments",
        json={**BODY, "currency": "GBP"},
        headers={**AUTH_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 422


async def test_ssrf_private_webhook_url_rejected(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/payments",
        json={**BODY, "webhook_url": "http://127.0.0.1:9999/hook"},
        headers={**AUTH_HEADERS, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 422
    assert "webhook_url" in response.json()["error"]["message"]
