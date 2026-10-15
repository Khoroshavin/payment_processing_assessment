"""Cross-cutting (non-domain) application errors."""

from fastapi import status

from app.core.exceptions.base import BaseAppException


class ForbiddenError(BaseAppException):
    def __init__(self, message: str = "Forbidden") -> None:
        super().__init__(status.HTTP_403_FORBIDDEN, "forbidden", message)


class ServiceUnavailableError(BaseAppException):
    def __init__(self, message: str = "service unavailable") -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, "service_unavailable", message)
