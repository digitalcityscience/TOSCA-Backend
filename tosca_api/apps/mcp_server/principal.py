"""Who is calling the MCP server.

The caller is identified by a Keycloak access token, verified with the same
function the REST API uses. Roles and organization are read from the token on
every request (never stored), exactly like ``KeycloakTokenAuthentication``.

Unlike the REST API, a token never creates or changes a Django user here: the
MCP server only needs the claims.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import anyio.to_thread
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.db import close_old_connections
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from rest_framework.exceptions import AuthenticationFailed

from tosca_api.apps.authentication.identity import KEYCLOAK_PROVIDER
from tosca_api.apps.authentication.role_sync import (
    LEVEL_RANK,
    ORG_CHECK_EXEMPT_ROLES,
    extract_org_from_token,
    extract_roles_from_token,
    normalize_org_roles,
    org_role_level,
)
from tosca_api.apps.catalog_api.services.v1.visibility_service import CatalogViewer
from tosca_api.apps.core.jwt_utils import verify_and_decode_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class McpPrincipal:
    """The Keycloak user behind an MCP request or a map bridge connection."""

    sub: str
    username: str
    org_slug: str | None
    level: str | None
    """Highest role in ``org_slug``: READER, WRITER, ADMIN, or None."""
    exempt: bool
    """Holds DJANGO_SUPERADMIN (sees every organization, as in the API)."""
    client_id: str = ""
    org_roles: tuple[tuple[str, str], ...] = ()
    """Every ``ROLE_<ORG>_<LEVEL>`` of the token as (org, highest level),
    independent of the organization claim."""

    @property
    def viewer(self) -> CatalogViewer:
        """The catalog visibility of this caller (same object the API builds)."""
        return CatalogViewer(org_slug=self.org_slug, exempt=self.exempt)

    def has_level(self, required: str) -> bool:
        if self.exempt:
            return True
        return self.level is not None and LEVEL_RANK[self.level] >= LEVEL_RANK[required]

    @property
    def may_use_mcp(self) -> bool:
        """At least ``MCP_MIN_ROLE_LEVEL`` in some organization, read from the
        role name. Tokens without an organization claim pass too; what such a
        caller sees in the catalog still follows the claim, as in the API
        (no claim: public layers only)."""
        required = LEVEL_RANK[settings.MCP_MIN_ROLE_LEVEL]
        return self.has_level(settings.MCP_MIN_ROLE_LEVEL) or any(
            LEVEL_RANK[level] >= required for _org, level in self.org_roles
        )


def principal_from_claims(claims: dict) -> McpPrincipal:
    """Build the principal from verified access-token claims."""
    roles = extract_roles_from_token(claims).roles
    org_slug = extract_org_from_token(claims).default_slug or None
    return McpPrincipal(
        sub=str(claims.get("sub") or ""),
        username=str(claims.get("preferred_username") or ""),
        org_slug=org_slug,
        level=org_role_level(roles, org_slug),
        exempt=bool(roles & ORG_CHECK_EXEMPT_ROLES),
        client_id=str(claims.get("azp") or ""),
        org_roles=tuple(sorted(normalize_org_roles(roles).items())),
    )


def _user_is_deactivated(sub: str) -> bool:
    """True when this Keycloak identity is linked to a Django user that an
    administrator switched off. Unknown identities are not created here."""
    account = (
        SocialAccount.objects.select_related("user")
        .filter(provider=KEYCLOAK_PROVIDER, uid=sub)
        .first()
    )
    return account is not None and not account.user.is_active


def verify_token_claims(token: str) -> dict | None:
    """Verified claims of a Keycloak access token, or None when it is refused."""
    try:
        claims = verify_and_decode_token(token, audiences=list(settings.MCP_TOKEN_AUDIENCES))
    except AuthenticationFailed:
        return None
    if _user_is_deactivated(str(claims.get("sub") or "")):
        logger.warning("MCP token refused: user is deactivated", extra={"sub": claims.get("sub")})
        return None
    return claims


def _verify_in_worker_thread(token: str) -> dict | None:
    # Worker threads are reused: take a usable database connection and hand it
    # back afterwards, as Django does around a request.
    close_old_connections()
    try:
        return verify_token_claims(token)
    finally:
        close_old_connections()


async def verify_token_claims_async(token: str) -> dict | None:
    """``verify_token_claims`` for async code (it reads the database and may fetch Keycloak's keys)."""
    return await anyio.to_thread.run_sync(_verify_in_worker_thread, token)


class KeycloakTokenVerifier:
    """``mcp`` SDK token verifier backed by the existing Keycloak verification."""

    async def verify_token(self, token: str) -> AccessToken | None:
        claims = await verify_token_claims_async(token)
        if claims is None:
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or ""),
            scopes=str(claims.get("scope") or "").split(),
            expires_at=int(claims["exp"]),
            subject=str(claims["sub"]),
            claims=claims,
        )


def current_principal() -> McpPrincipal:
    """The caller of the running tool. Refuses callers below the minimum role."""
    access_token = get_access_token()
    if access_token is None or not access_token.claims:
        raise ToolError("Not signed in.")
    principal = principal_from_claims(access_token.claims)
    if not principal.may_use_mcp:
        logger.info(
            "MCP call refused: role below minimum",
            extra={"sub": principal.sub, "org": principal.org_slug, "level": principal.level},
        )
        raise ToolError(
            "Your TOSCA account has no role that allows using the assistant connection "
            f"(needs at least {settings.MCP_MIN_ROLE_LEVEL} in your organization). "
            "Ask your organization's TOSCA administrator."
        )
    return principal
