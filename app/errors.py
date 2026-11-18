"""统一的业务错误。"""

from __future__ import annotations


class ApiError(Exception):
    def __init__(self, message: str, code: str = "ERROR", status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class NotFound(ApiError):
    def __init__(self, message: str, code: str = "NOT_FOUND") -> None:
        super().__init__(message, code, 404)


class Conflict(ApiError):
    def __init__(self, message: str, code: str = "CONFLICT") -> None:
        super().__init__(message, code, 409)


class BadPath(ApiError):
    def __init__(self, message: str = "路径逃出了项目目录") -> None:
        super().__init__(message, "BAD_PATH", 400)
