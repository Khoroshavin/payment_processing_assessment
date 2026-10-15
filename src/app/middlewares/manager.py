"""Middleware manager: single explicit point wiring app-wide middlewares."""

from fastapi import FastAPI

from app.middlewares.error_handlers import install_error_handlers
from app.middlewares.request_id import RequestIdMiddleware


def install_middlewares(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
