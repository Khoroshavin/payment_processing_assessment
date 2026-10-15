"""API entrypoint: Granian ASGI server with application-controlled logging."""

import logging

from fastapi import FastAPI
from granian import Granian
from granian.constants import Interfaces

from app.core.configs.settings import get_settings
from app.core.logging import setup_logging
from app.transport.rest.app import create_app

logger = logging.getLogger("app.api")

app: FastAPI = create_app()


def main() -> None:
    settings = get_settings().app
    setup_logging(settings.log_level, settings.env, log_colors=settings.log_colors)
    logger.info("api listening on %s:%s", settings.api_host, settings.api_port)
    Granian(
        "app.entrypoints.api:app",
        address=settings.api_host,
        port=settings.api_port,
        workers=settings.api_workers,
        interface=Interfaces.ASGI,
        log_enabled=False,
        log_access=False,
    ).serve()


if __name__ == "__main__":
    main()
