from fastapi import HTTPException


class BaseAppException(HTTPException):
    """Base class for application errors, carrying an API error code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
