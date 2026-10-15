"""Auth domain errors."""

from fastapi import status

from app.core.exceptions.base import BaseAppException


class UnauthorizedError(BaseAppException):
    def __init__(self, message: str = "Missing X-API-Key header") -> None:
        super().__init__(status.HTTP_401_UNAUTHORIZED, "unauthorized", message)
