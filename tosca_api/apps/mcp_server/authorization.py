"""Who may run the write tools (publishing, styles, geostories).

OPEN — NOT DECIDED YET
======================

The rules "which role, in which organization, may do what through the
assistant" have not been decided. Until they are, every write tool goes
through :func:`authorize` and only two things are enforced:

1. the general MCP gate of ``principal.current_principal`` (at least
   ``MCP_MIN_ROLE_LEVEL`` in some organization), and
2. the organization boundary the REST API already applies to the same
   writes (``organizations.permissions.validate_workspace_organization`` /
   ``validate_campaign_organization``): a workspace or campaign must belong to
   the caller's organization; platform admins (``DJANGO_SUPERADMIN``) pass.

There is no role ladder here yet (a READER may publish). The MCP server is not
public; before it is, the authorization layer is added in this one function,
based on the user's role (``principal.level`` / ``principal.org_roles``) and
the ``action``. Every write tool already names its action, so the rules can be
written without touching the tools.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from allauth.socialaccount.models import SocialAccount
from mcp.server.mcpserver.exceptions import ToolError

from tosca_api.apps.authentication.identity import KEYCLOAK_PROVIDER

from .principal import McpPrincipal

logger = logging.getLogger(__name__)


class WriteAction(StrEnum):
    PUBLISH_LAYER = "publish_layer"
    UNPUBLISH_LAYER = "unpublish_layer"
    UPDATE_LAYER = "update_layer"
    UPDATE_STYLE = "update_style"
    SET_LAYER_DEFAULT_STYLE = "set_layer_default_style"
    PUBLISH_LAYER_GROUP = "publish_layer_group"
    UNPUBLISH_LAYER_GROUP = "unpublish_layer_group"
    WRITE_GEOSTORY = "write_geostory"


def _org_slug(owner) -> str | None:
    organization = getattr(owner, "organization", None)
    return organization.slug if organization is not None else None


def authorize(principal: McpPrincipal, action: WriteAction, *, workspace=None, campaign=None) -> None:
    """Refuse the write unless the caller may do it. See the module docstring: role rules are OPEN."""
    owner = workspace if workspace is not None else campaign
    if owner is None or principal.exempt:
        return
    if principal.org_slug is None or _org_slug(owner) != principal.org_slug:
        logger.info(
            "MCP write refused: other organization",
            extra={"sub": principal.sub, "action": str(action), "org": principal.org_slug},
        )
        kind = "workspace" if workspace is not None else "campaign"
        raise ToolError(f"This {kind} does not belong to your organization.")


def django_user(principal: McpPrincipal):
    """The Django user behind the Keycloak identity, for ``created_by`` / ``author`` fields.

    The MCP server never creates users (see ``principal``). A user who never
    signed in to the TOSCA web app has no Django account yet and cannot write.
    """
    account = (
        SocialAccount.objects.select_related("user")
        .filter(provider=KEYCLOAK_PROVIDER, uid=principal.sub)
        .first()
    )
    if account is None or not account.user.is_active:
        raise ToolError(
            "Your TOSCA account is not set up for changes yet. Sign in to the TOSCA web app once, then try again."
        )
    return account.user
