"""Consumer entrypoint: runs the FastStream application."""

import asyncio

from faststream import FastStream

from app.core.configs.settings import get_settings
from app.core.logging import setup_logging
from app.core.orm.session_factory import configure_database
from app.transport.msg_streaming.app import build_consumer_app


def main() -> None:
    settings = get_settings()
    setup_logging(settings.app.log_level, settings.app.env, log_colors=settings.app.log_colors)
    configure_database(settings.postgres.dsn)
    app: FastStream = build_consumer_app(settings)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
