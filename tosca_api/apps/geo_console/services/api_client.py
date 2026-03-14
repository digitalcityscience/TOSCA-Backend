"""
GeoConsoleAPIClient — thin wrapper over the internal DRF API.

Rules:
- Never imports from geodata_engine.models
- All network errors are converted to typed exceptions before leaving this module
- Sync operations use a longer timeout (30s) than read operations (10s)

Usage in views:
    from rest_framework.authtoken.models import Token
    from tosca_api.apps.geo_console.services.api_client import GeoConsoleAPIClient
    from tosca_api.apps.geo_console.exceptions import APIError, APITimeoutError

    token, _ = Token.objects.get_or_create(user=request.user)
    client = GeoConsoleAPIClient(token=token.key)
    engines = client.list_engines()
"""
from __future__ import annotations

import logging
from typing import Any

import requests
from django.conf import settings

from tosca_api.apps.geo_console.exceptions import APIError, APINotFoundError, APITimeoutError

logger = logging.getLogger(__name__)

# Default timeouts: (connect_seconds, read_seconds)
_DEFAULT_TIMEOUT = (5, 10)
_SYNC_TIMEOUT = (5, 30)


class GeoConsoleAPIClient:
    """
    Calls the internal DRF API on behalf of a console user.
    Never touches Django models or the ORM directly.
    """

    def __init__(self, token: str):
        """
        Args:
            token: DRF token string for the logged-in user.
                   Obtain via: Token.objects.get_or_create(user=request.user)[0].key
        """
        base = getattr(settings, "INTERNAL_API_BASE_URL", "http://localhost:8000/api/geoengine")
        # Normalise: strip trailing slash so all path joins are consistent
        self._base = base.rstrip("/")

        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        """Build a full URL from a path relative to the API base."""
        return f"{self._base}/{path.lstrip('/')}"

    def _handle_response(self, response: requests.Response) -> Any:
        """
        Convert a requests.Response into a Python dict/list.
        Raises a typed exception on any non-2xx status.
        """
        if response.status_code == 404:
            raise APINotFoundError()

        if not response.ok:
            # Try to extract DRF's own error message before falling back
            try:
                body = response.json()
                detail = body.get("detail") or body.get("error") or str(body)
            except Exception:
                detail = response.text or f"HTTP {response.status_code}"
            logger.error(
                "Internal API error: %s %s → %s",
                response.request.method,
                response.url,
                detail,
            )
            raise APIError(detail=detail, status_code=response.status_code)

        if response.status_code == 204 or not response.content:
            return {}

        return response.json()

    def _get(self, path: str, timeout: tuple = _DEFAULT_TIMEOUT) -> Any:
        url = self._url(path)
        try:
            response = self._session.get(url, timeout=timeout)
        except requests.Timeout:
            logger.warning("GET %s timed out", url)
            raise APITimeoutError()
        except requests.RequestException as exc:
            raise APIError(detail=str(exc))
        return self._handle_response(response)

    def _post(
        self,
        path: str,
        data: dict | None = None,
        timeout: tuple = _DEFAULT_TIMEOUT,
    ) -> Any:
        url = self._url(path)
        try:
            response = self._session.post(url, json=data or {}, timeout=timeout)
        except requests.Timeout:
            logger.warning("POST %s timed out", url)
            raise APITimeoutError()
        except requests.RequestException as exc:
            raise APIError(detail=str(exc))
        return self._handle_response(response)

    # ------------------------------------------------------------------
    # Engine methods
    # ------------------------------------------------------------------

    def list_engines(self) -> list[dict]:
        """
        GET /api/geoengine/engines/
        Returns a list of all GeodataEngine objects (active + inactive).
        """
        result = self._get("engines/")
        # DRF list responses may be plain lists or paginated {"results": [...]}
        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return result

    def get_engine(self, engine_id: str) -> dict:
        """
        GET /api/geoengine/engines/{engine_id}/
        Returns a single GeodataEngine dict.
        Raises APINotFoundError if the engine does not exist.
        """
        return self._get(f"engines/{engine_id}/")

    def sync_engine(self, engine_id: str) -> dict:
        """
        POST /api/geoengine/engines/{engine_id}/sync/
        Triggers a full pull-sync: GeoServer → Django.
        Uses a longer 30-second read timeout.

        Returns the sync result dict, e.g.:
            {
              "success": True,
              "workspaces": {"synced": 3, "created": 1, "deleted": 0, "errors": []},
              "stores": {...},
              "layers": {...},
            }
        """
        return self._post(f"engines/{engine_id}/sync/", timeout=_SYNC_TIMEOUT)

    def validate_engine(self, engine_id: str) -> dict:
        """
        POST /api/geoengine/engines/{engine_id}/validate/
        Checks connectivity to the engine (GeoServer ping).

        Returns:
            {"success": True, "message": "Connection validated"}
            or raises APIError on failure.
        """
        return self._post(f"engines/{engine_id}/validate/")

    def push_engine(self, engine_id: str) -> dict:
        """
        POST /api/geoengine/engines/{engine_id}/push/
        Pushes Django metadata intent → GeoServer (workspaces for now).
        Uses a longer 30-second read timeout.

        Returns the push result dict, e.g.:
            {
              "success": True,
              "pushed": 2,
              "already_exists": 1,
              "errors": [],
            }
        """
        return self._post(f"engines/{engine_id}/push/", timeout=_SYNC_TIMEOUT)

    def create_engine(self, data: dict) -> dict:
        """
        POST /api/geoengine/engines/
        Creates a new GeodataEngine. Returns the created engine dict including its new UUID.
        """
        return self._post("engines/", data=data)

    def update_engine(self, engine_id: str, data: dict) -> dict:
        """
        PATCH /api/geoengine/engines/{engine_id}/
        Partial update — only sends fields present in `data`.
        Password is omitted from `data` if it was left blank by the user.
        """
        return self._patch(f"engines/{engine_id}/", data=data)

    def delete_engine(self, engine_id: str) -> dict:
        """
        DELETE /api/geoengine/engines/{engine_id}/
        Deletes the engine. Returns {} on success (204 No Content).
        """
        return self._delete(f"engines/{engine_id}/")

    # ------------------------------------------------------------------
    # Workspace methods  (Phase 2)
    # ------------------------------------------------------------------

    def list_workspaces(self, engine_id: str | None = None) -> list[dict]:
        """
        GET /api/geoengine/workspaces/
        Returns all workspaces, optionally filtered by engine UUID.
        """
        path = "workspaces/"
        if engine_id:
            path = f"workspaces/?geodata_engine={engine_id}"
        result = self._get(path)
        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return result

    def get_workspace(self, workspace_id: str) -> dict:
        """
        GET /api/geoengine/workspaces/{workspace_id}/
        Returns a single Workspace dict.
        Raises APINotFoundError if workspace does not exist.
        """
        return self._get(f"workspaces/{workspace_id}/")

    def create_workspace(self, data: dict) -> dict:
        """
        POST /api/geoengine/workspaces/
        Creates a workspace in Django + GeoServer.

        Expected keys: geodata_engine (UUID str), name, description (optional).
        Returns: {"workspace": {...}, "result": {"success": True, ...}}
        """
        return self._post("workspaces/", data=data)

    def delete_workspace(self, workspace_id: str) -> dict:
        """
        DELETE /api/geoengine/workspaces/{workspace_id}/
        Deletes workspace from GeoServer first (verify), then from Django.
        Returns {"success": True, ...} or raises APIError on failure.
        """
        return self._delete(f"workspaces/{workspace_id}/")

    def sync_workspace(self, workspace_id: str) -> dict:
        """
        Fetches the workspace to discover its engine, then triggers a full
        engine pull-sync (GeoServer → Django).  There is no per-workspace
        sync endpoint — syncing at engine level is the correct granularity.

        Returns the same sync result dict as sync_engine().
        Raises APINotFoundError if workspace doesn't exist.
        Raises APIError / APITimeoutError on network/engine failures.
        """
        workspace = self.get_workspace(workspace_id)
        engine_id = workspace.get("geodata_engine")
        if not engine_id:
            raise APIError(detail="Workspace has no associated engine — cannot sync.")
        return self._post(f"engines/{engine_id}/sync/", timeout=_SYNC_TIMEOUT)

    # ------------------------------------------------------------------
    # Low-level write helpers
    # ------------------------------------------------------------------

    def _patch(
        self,
        path: str,
        data: dict | None = None,
        timeout: tuple = _DEFAULT_TIMEOUT,
    ) -> dict:
        url = self._url(path)
        try:
            response = self._session.patch(url, json=data or {}, timeout=timeout)
        except requests.Timeout:
            logger.warning("PATCH %s timed out", url)
            raise APITimeoutError()
        except requests.RequestException as exc:
            raise APIError(detail=str(exc))
        return self._handle_response(response)

    def _delete(
        self,
        path: str,
        timeout: tuple = _DEFAULT_TIMEOUT,
    ) -> dict:
        url = self._url(path)
        try:
            response = self._session.delete(url, timeout=timeout)
        except requests.Timeout:
            logger.warning("DELETE %s timed out", url)
            raise APITimeoutError()
        except requests.RequestException as exc:
            raise APIError(detail=str(exc))
        return self._handle_response(response)
