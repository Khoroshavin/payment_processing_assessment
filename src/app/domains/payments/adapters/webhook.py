"""Webhook delivery adapter."""

import logging
from uuid import UUID

import httpx

from app.core.configs.settings import WebhookSettings
from app.modules.security.redaction import redact_url

logger = logging.getLogger(__name__)


class WebhookClient:
    def __init__(self, settings: WebhookSettings) -> None:
        self._timeout = settings.timeout_seconds
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def notify(self, url: str, payment_id: UUID, status: str) -> None:
        payload = {"payment_id": str(payment_id), "status": status}
        logger.info("sending webhook to %s for payment %s", redact_url(url), payment_id)
        response = await self._get_client().post(url, json=payload)
        response.raise_for_status()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
