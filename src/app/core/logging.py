"""Central logging configuration.

The full dictConfig lives in core/configs/logging_{prod,dev}.json; the file
is selected by the current environment (ENV): production/staging use JSON
logs, development/test use console logs. Every logger in the service
(application, uvicorn, faststream, sqlalchemy, alembic) is routed to a single
root handler, so all log lines share one format. Library loggers are pinned
to propagate with no handlers of their own, which guarantees exactly one
formatted line per log record.
"""

import contextvars
import json
import logging
import logging.config
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.configs.settings import PRODUCTION_ENVIRONMENTS

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ColoredFormatter(logging.Formatter):
    """Console formatter with ANSI colors for the dev environment.

    Colorizes the level name (DEBUG cyan, INFO green, WARNING yellow,
    ERROR red, CRITICAL bold red); the rest of the line stays plain for
    readability.

    Coloring is controlled by the `LOG_COLORS` environment variable:
    `always`/`1`/`true` forces colors on (useful in `docker compose logs`,
    where stdout is not a TTY), `never`/`0`/`false` forces them off; the
    default `auto` enables colors only when stdout is a terminal.
    """

    RESET: str = "\033[0m"
    LEVEL_COLORS: dict[str, str] = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[1;31m",
    }

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        use_colors: bool | None = None,
    ) -> None:
        super().__init__(fmt, datefmt)
        self._use_colors = use_colors if use_colors is not None else _colors_enabled()

    def format(self, record: logging.LogRecord) -> str:
        if self._use_colors and (color := self.LEVEL_COLORS.get(record.levelname)):
            # Format a copy so the colored level name never leaks into other
            # handlers sharing the same record.
            record = logging.makeLogRecord(record.__dict__)
            record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


def _colors_enabled() -> bool:
    configured = os.environ.get("LOG_COLORS", "auto").strip().lower()
    if configured in {"always", "1", "true", "yes", "on"}:
        return True
    if configured in {"never", "0", "false", "no", "off"}:
        return False
    return sys.stdout.isatty()


def _config_filename(environment: str) -> str:
    if environment.lower() in PRODUCTION_ENVIRONMENTS:
        return "logging_prod.json"
    return "logging_dev.json"


def _load_config(environment: str, log_level: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "configs" / _config_filename(environment)
    config = json.loads(path.read_text(encoding="utf-8"))
    config["root"]["level"] = log_level.upper()
    return config


def setup_logging(
    log_level: str,
    environment: str,
    *,
    log_colors: str | None = None,
) -> None:
    if log_colors is not None:
        # The formatter factory resolves LOG_COLORS from the environment at
        # dictConfig time; expose the setting to it (pydantic-settings reads
        # .env, but os.environ may not contain the value, e.g. local runs).
        os.environ["LOG_COLORS"] = log_colors
    logging.config.dictConfig(_load_config(environment, log_level))
