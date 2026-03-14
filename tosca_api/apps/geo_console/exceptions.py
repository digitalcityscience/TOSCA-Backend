"""
geo_console exceptions — typed errors for API client layer.
Views catch these; raw HTTP errors never reach templates.
"""


class APIError(Exception):
    """
    Raised when the internal DRF API returns a non-2xx response.
    Always carries a human-readable detail string.
    """

    def __init__(self, detail: str, status_code: int | None = None):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)

    def __str__(self) -> str:
        if self.status_code:
            return f"[HTTP {self.status_code}] {self.detail}"
        return self.detail


class APITimeoutError(APIError):
    """
    Raised when the internal API call exceeds the timeout threshold.
    The view should show "Engine unreachable — connection timed out."
    """

    def __init__(self, detail: str = "Connection timed out"):
        super().__init__(detail=detail, status_code=None)


class APINotFoundError(APIError):
    """
    Raised when the internal API returns 404.
    The view should treat this as a missing resource, not a crash.
    """

    def __init__(self, detail: str = "Resource not found"):
        super().__init__(detail=detail, status_code=404)
