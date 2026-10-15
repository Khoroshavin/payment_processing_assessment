"""Request-id middleware (pure ASGI so contextvars propagate to handlers)."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.datastructures import Headers, MutableHeaders

from app.core.ids import uuid7
from app.core.logging import request_id_var

logger = logging.getLogger("app.api.access")


class RequestIdMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self._app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id") or str(uuid7())
        token = request_id_var.set(request_id)
        started = time.perf_counter()

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(scope=message)
                mutable["x-request-id"] = request_id
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)

        logger.info(
            "%s %s %.3fs",
            scope.get("method"),
            scope.get("path"),
            time.perf_counter() - started,
        )
