import json
import logging
import sys
from pathlib import Path

import pytest

from app.core.logging import ColoredFormatter, JsonFormatter, _config_filename, setup_logging

CONFIGS_DIR = Path(__file__).resolve().parents[3] / "src" / "app" / "core" / "configs"


def _load_config(filename: str) -> dict:
    return json.loads((CONFIGS_DIR / filename).read_text(encoding="utf-8"))


def test_production_environment_selects_prod_config() -> None:
    assert _config_filename("production") == "logging_prod.json"
    assert _config_filename("staging") == "logging_prod.json"
    assert _config_filename("PRODUCTION") == "logging_prod.json"


def test_non_production_environment_selects_dev_config() -> None:
    assert _config_filename("development") == "logging_dev.json"
    assert _config_filename("dev") == "logging_dev.json"
    assert _config_filename("test") == "logging_dev.json"


def test_prod_config_routes_everything_through_root_json_handler() -> None:
    config = _load_config("logging_prod.json")
    assert config["handlers"]["default"]["formatter"] == "json"
    for logger_name, logger_config in config["loggers"].items():
        assert logger_config["handlers"] == [], logger_name
        assert logger_config["propagate"] is True, logger_name


def test_dev_config_uses_console_formatter() -> None:
    config = _load_config("logging_dev.json")
    assert config["handlers"]["default"]["formatter"] == "console"
    assert "fmt" in config["formatters"]["console"]
    assert config["formatters"]["console"]["()"] == "app.core.logging.ColoredFormatter"


def test_colored_formatter_wraps_levelname_in_ansi_codes() -> None:
    formatter = ColoredFormatter("%(levelname)s %(message)s", use_colors=True)
    record = logging.LogRecord("app.test", logging.ERROR, "f.py", 1, "boom", None, None)
    assert formatter.format(record) == "\033[31mERROR\033[0m boom"


def test_colored_formatter_output_is_plain_when_disabled() -> None:
    formatter = ColoredFormatter("%(levelname)s %(message)s", use_colors=False)
    record = logging.LogRecord("app.test", logging.INFO, "f.py", 1, "ok", None, None)
    assert formatter.format(record) == "INFO ok"


def test_colored_formatter_respects_log_colors_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOG_COLORS", "always")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)  # не TTY
    formatter = ColoredFormatter("%(levelname)s")
    record = logging.LogRecord("app.test", logging.INFO, "f.py", 1, "ok", None, None)
    assert formatter.format(record) == "\033[32mINFO\033[0m"


def test_colored_formatter_defaults_to_plain_outside_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOG_COLORS", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    formatter = ColoredFormatter("%(levelname)s")
    record = logging.LogRecord("app.test", logging.INFO, "f.py", 1, "ok", None, None)
    assert formatter.format(record) == "INFO"


def test_json_formatter_output_shape() -> None:
    record = logging.LogRecord("app.test", logging.INFO, "f.py", 1, "hello", None, None)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert "timestamp" in payload
    assert "exception" not in payload


def test_setup_logging_single_emission_without_duplicates() -> None:
    class RecordingHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    setup_logging("INFO", "production")
    root = logging.getLogger()
    probe = RecordingHandler()
    root.addHandler(probe)
    try:
        for name in ("uvicorn.error", "faststream.access.rabbit", "app.worker"):
            logging.getLogger(name).info("line from %s", name)
        messages = [record.getMessage() for record in probe.records]
        assert messages == [
            "line from uvicorn.error",
            "line from faststream.access.rabbit",
            "line from app.worker",
        ]
    finally:
        root.removeHandler(probe)
