"""Authorization policy skeleton (security tickets ticket 03, phase 2.1-2.5).

Additive foundation for gates A (capability) and B (entitlement) -- nothing
in this module is consulted by any view or admin yet. ``organizations.permissions``
still owns request-time enforcement (gate A ∩ C via ``OrgScopedPermission`` /
``CampaignScopedPermission``); ticket 06's dynamic ``has_perm()`` backend is
the first real caller of ``enabled_apps_for``/``LEVEL_ACTIONS``.
"""

from __future__ import annotations

from django.conf import settings

from tosca_api.apps.authentication.role_sync import ORG_ROLE_LEVELS

# Role -> allowed CRUD actions (security tickets ticket 06, Layer A). Only
# view/add/change/delete -- no custom `manage_*` verbs.
LEVEL_ACTIONS = {
    "READER": {"view"},
    "WRITER": {"view", "add", "change"},
    "ADMIN": {"view", "add", "change", "delete"},
}

assert set(LEVEL_ACTIONS) == set(ORG_ROLE_LEVELS)


def user_claims(user):
    """Return the ``(roles, default_org)`` claims for ``user``.

    Skeleton for ticket 05's unified resolver (live claims -> persisted
    snapshot -> fail closed). Not wired to any request-time check yet.
    """
    raise NotImplementedError("wired in security tickets ticket 05")


def enabled_apps_for(organization) -> set[str]:
    """Return the set of app labels ``organization`` is entitled to (gate B).

    Backed by :class:`~tosca_api.apps.organizations.models.OrganizationAppEntitlement`.
    """
    from .models import OrganizationAppEntitlement

    return set(
        OrganizationAppEntitlement.objects.filter(organization=organization).values_list(
            "app_label", flat=True
        )
    )


def role_controlled_models_for_app(app_label: str) -> set[str]:
    """Return the model names role-controlled for ``app_label``, per the
    ``TOSCA_PERMISSION_MODELS`` single source of truth.
    """
    return set(settings.TOSCA_PERMISSION_MODELS.get(app_label, ()))
