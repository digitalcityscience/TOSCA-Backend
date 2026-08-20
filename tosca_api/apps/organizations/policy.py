"""Authorization policy (security tickets ticket 03 foundation, ticket 05 resolver).

``enabled_apps_for``/``LEVEL_ACTIONS`` (gate B foundation) are still additive
-- nothing consults them yet; ticket 06's dynamic ``has_perm()`` backend is
their first real caller. ``user_claims``/``sync_snapshot`` are live as of
ticket 05: ``organizations.permissions.get_request_org_context`` calls
``user_claims`` for every session-based (browser/admin) request, and
``authentication.backends`` calls ``sync_snapshot`` on browser login.
"""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from tosca_api.apps.authentication.role_sync import ORG_ROLE_LEVELS, AuthClaims

# Role -> allowed CRUD actions (security tickets ticket 06, Layer A). Only
# view/add/change/delete -- no custom `manage_*` verbs.
LEVEL_ACTIONS = {
    "READER": {"view"},
    "WRITER": {"view", "add", "change"},
    "ADMIN": {"view", "add", "change", "delete"},
}

assert set(LEVEL_ACTIONS) == set(ORG_ROLE_LEVELS)


def _resolve_claims(user) -> AuthClaims:
    """Resolve ``user``'s effective claims (security tickets ticket 05's
    unified resolver). Precedence (fail closed):

    1. Request-local live claims (``user._auth_claims``) -- set by
       ``KeycloakTokenAuthentication`` on every Bearer request, and by
       ``KeycloakAdapter`` on the login request itself.
    2. The persisted :class:`~.models.UserAuthorizationSnapshot` -- what a
       later browser/admin request (no live claims of its own) falls back to.
    3. No permissions when neither source has anything.

    No implicit decoding of a stale/expired stored ID token, and no hidden
    in-memory "last known good" fallback beyond what's spelled out above.
    Single shared precedence chain -- :func:`user_claims` and
    :func:`is_platform_exempt` are thin views over the same
    :class:`AuthClaims` this returns, so the precedence rule lives in
    exactly one place.
    """
    claims = getattr(user, "_auth_claims", None)
    if claims is not None:
        return claims

    snapshot = _load_valid_snapshot(user)
    if snapshot is not None:
        return AuthClaims(
            org_roles=snapshot.org_roles,
            default_org=snapshot.default_org or None,
            authoritative=True,
            platform_exempt=snapshot.platform_exempt,
        )

    return AuthClaims(org_roles={}, default_org=None, authoritative=False, platform_exempt=False)


def user_claims(user) -> tuple[dict[str, str], str | None]:
    """Return the ``(org_roles, default_org)`` claims for ``user`` -- see
    :func:`_resolve_claims` for the precedence rule.
    """
    claims = _resolve_claims(user)
    return claims.org_roles, claims.default_org


def is_platform_exempt(user) -> bool:
    """Whether ``user`` actually held a role in ``ORG_CHECK_EXEMPT_ROLES``
    (``DJANGO_SUPERADMIN`` only) at last sync -- same precedence
    as :func:`user_claims` (see :func:`_resolve_claims`).

    Deliberately **not** ``user.is_staff``/``user.is_superuser``: those
    Django columns can be toggled independently of Keycloak (e.g. via the
    admin's own ``UserAdmin`` "Permissions" fieldset), so they are not a
    faithful proxy for "Keycloak actually granted a platform-exempt role" --
    using them here would let an incidental ``is_staff`` grant silently also
    bypass org scoping everywhere ``get_request_org_context`` is consulted.
    """
    return _resolve_claims(user).platform_exempt


def sync_snapshot(user, claims: AuthClaims) -> None:
    """Persist ``claims`` as ``user``'s :class:`~.models.UserAuthorizationSnapshot`,
    respecting the authoritative write rule (security tickets ticket 05):

    * authoritative + non-empty -> write/update snapshot
    * authoritative + empty     -> write empty snapshot (Keycloak really returned none)
    * non-authoritative/missing -> DO NOT overwrite a previous snapshot

    A missing mapper/claim (``authoritative is False``) must not silently
    destroy a previously valid snapshot -- mirrors the demotion guard in
    ``role_sync.sync_user_permissions_from_roles``. Only the browser/admin
    login path should call this; Bearer/API claims are never persisted.
    """
    if not claims.authoritative:
        return

    from .models import UserAuthorizationSnapshot

    UserAuthorizationSnapshot.objects.update_or_create(
        user=user,
        defaults={
            "org_roles": claims.org_roles,
            "default_org": claims.default_org or "",
            "platform_exempt": claims.platform_exempt,
            "synced_at": timezone.now(),
        },
    )


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
