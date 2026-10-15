"""Shared test helpers."""

import asyncio
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp.web
from aiohttp import web

type WaitUntil = Callable[..., Awaitable[None]]


async def wait_for(
    condition: Callable[[], Awaitable[bool]],
    timeout_seconds: float = 30.0,
    interval: float = 0.1,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if await condition():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition was not met within timeout")


class MockWebhookServer:
    def __init__(self, response_status: int = 200) -> None:
        self.response_status = response_status
        self.requests: list[dict[str, Any]] = []
        self.port: int | None = None
        self._runner: aiohttp.web.AppRunner | None = None

    @property
    def url(self) -> str:
        assert self.port is not None
        return f"http://127.0.0.1:{self.port}/hook"

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/hook", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await site.start()

    async def _handle(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        self.requests.append(await request.json())
        return web.json_response({"ok": True}, status=self.response_status)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
