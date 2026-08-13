"""
Minimal Keycloak Admin API client for the role registry (Epic 11 Phase 1, §5).

Auth is the clean, verified path (canonical §3 decision 3/4, §6): a
``client_credentials`` grant on the existing ``django-dev`` login client, whose
service account was granted ``realm-management`` -> ``view-realm``. No extra
username/password -- the existing ``KEYCLOAK_CLIENT_SECRET`` is reused.

Endpoints mirror the probe proven live in §5:
- token:  ``POST {server}/realms/{realm}/protocol/openid-connect/token``
- roles:  ``GET  {server}/admin/realms/{realm}/roles?briefRepresentation=true&max=1000``
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10
_MAX_ROLES = 1000


class KeycloakAdminError(Exception):
    """Raised when the Keycloak Admin API cannot be reached or authenticated."""


def _server() -> str:
    return settings.KEYCLOAK_SERVER_URL.rstrip("/")


def get_admin_token(*, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Obtain an access token via ``client_credentials`` (service account)."""
    url = f"{_server()}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/token"
    try:
        resp = requests.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.KEYCLOAK_CLIENT_ID,
                "client_secret": settings.KEYCLOAK_CLIENT_SECRET,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
    except requests.RequestException as exc:
        raise KeycloakAdminError(f"Keycloak token request failed: {exc}") from exc
    if not token:
        raise KeycloakAdminError("Keycloak token response contained no access_token")
    return token


def list_realm_roles(*, token: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    """Return every realm role *name* (unfiltered -- callers apply the ROLE_ rule)."""
    if token is None:
        token = get_admin_token(timeout=timeout)
    url = f"{_server()}/admin/realms/{settings.KEYCLOAK_REALM}/roles"
    try:
        resp = requests.get(
            url,
            params={"briefRepresentation": "true", "max": _MAX_ROLES},
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise KeycloakAdminError(f"Keycloak roles request failed: {exc}") from exc
    return [role["name"] for role in payload if role.get("name")]
