from typing import Any


class AppException(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class ResourceNotFoundError(AppException):
    def __init__(self, message: str = "resource not found", *, code: str = "RESOURCE_NOT_FOUND") -> None:
        super().__init__(code, message, status_code=404)


class ConflictError(AppException):
    def __init__(self, message: str, *, code: str = "CONFLICT") -> None:
        super().__init__(code, message, status_code=409)


class RateLimitError(AppException):
    def __init__(self, message: str = "rate limit exceeded") -> None:
        super().__init__("RATE_LIMITED", message, status_code=429)
