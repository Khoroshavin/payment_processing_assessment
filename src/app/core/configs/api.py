"""FastAPI application settings."""

from dataclasses import dataclass

from app import __version__


@dataclass(frozen=True)
class ApiConfig:
    title: str = "Payment Processing Service"
    version: str = __version__
    docs_url: str = "/docs"
    openapi_url: str = "/openapi.json"
