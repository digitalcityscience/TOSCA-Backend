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


def _load_valid_snapshot(user):
    """Return ``user``'s :class:`UserAuthorizationSnapshot` if one exists, or
    ``None`` (security tickets ticket 04 TTL seam).

    No expiry check yet -- any persisted snapshot is "valid" for the PoC.
    This is the single seam a future TTL (e.g. re-sync after N hours) hooks
    into, so callers never need to know whether staleness is enforced.
    """
    from .models import UserAuthorizationSnapshot

    return UserAuthorizationSnapshot.objects.filter(user=user).first()


def invalidate_snapshot(user) -> None:
    """Drop ``user``'s persisted authorization snapshot (security tickets
    ticket 04 invalidation seam).

    No caller yet -- this is the seam the demotion remedy (a user's org role
    is downgraded/revoked in Keycloak) will hook into once ticket 06's
    ``has_perm()`` backend actually reads snapshots, so a stale grant can't
    outlive the next login.
    """
    from .models import UserAuthorizationSnapshot

    UserAuthorizationSnapshot.objects.filter(user=user).delete()


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
