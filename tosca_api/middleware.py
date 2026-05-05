"""Project-level HTTP middleware."""
from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers


class CorsMiddleware:
    """Minimal configurable CORS support for browser-based frontend clients."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._is_preflight(request):
            response = HttpResponse(status=200)
        else:
            response = self.get_response(request)

        self._add_cors_headers(request, response)
        return response

    @staticmethod
    def _is_preflight(request) -> bool:
        return (
            request.method == "OPTIONS"
            and "HTTP_ORIGIN" in request.META
            and "HTTP_ACCESS_CONTROL_REQUEST_METHOD" in request.META
        )

    def _add_cors_headers(self, request, response) -> None:
        origin = request.META.get("HTTP_ORIGIN")
        if not origin or not self._origin_allowed(origin):
            return

        response["Access-Control-Allow-Origin"] = origin
        patch_vary_headers(response, ("Origin",))

        if getattr(settings, "CORS_ALLOW_CREDENTIALS", False):
            response["Access-Control-Allow-Credentials"] = "true"

        if request.method == "OPTIONS":
            response["Access-Control-Allow-Methods"] = ", ".join(
                getattr(settings, "CORS_ALLOWED_METHODS", ())
            )
            response["Access-Control-Allow-Headers"] = ", ".join(
                getattr(settings, "CORS_ALLOWED_HEADERS", ())
            )
            response["Access-Control-Max-Age"] = str(
                getattr(settings, "CORS_PREFLIGHT_MAX_AGE", 86400)
            )

    @staticmethod
    def _origin_allowed(origin: str) -> bool:
        allowed_origins = getattr(settings, "CORS_ALLOWED_ORIGINS", ())
        return "*" in allowed_origins or origin in allowed_origins
