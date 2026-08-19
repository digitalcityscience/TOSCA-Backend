"""Org-scoped DRF permission + admin scoping helpers (epic-11 §2b, §5a/b, §10a).

There is no separate authorization table: level/permission mapping is derived
on every request from the (roles, default_organization) claims Keycloak put in
the token, per canonical §4b/§11.3. ``get_request_org_context`` is the single
place that reads those claims, so API (Bearer token) and Django admin
(session/social login) requests are scoped the same way.
"""

from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission

from tosca_api.apps.authentication.role_sync import (
    LEVEL_RANK,
    ORG_CHECK_EXEMPT_ROLES,
    denormalize_org_roles,
    extract_org_from_token,
    extract_roles_from_token,
    org_role_level,
)

from .policy import user_claims


def get_request_org_context(request):
    """Return ``(roles, org_slug, exempt)`` for ``request``.

    Bearer/API requests read the decoded Keycloak token DRF authentication
    backends attach as ``request.auth`` (``KeycloakTokenAuthentication``) --
    always the freshest source, never persisted. Session-based requests
    (Django admin), which never populate ``request.auth``, fall through to
    the ticket-05 unified resolver (``organizations.policy.user_claims``):
    the current request-local live claims if this is the login request
    itself, else the persisted ``UserAuthorizationSnapshot`` from the user's
    last successful browser login (fail closed if neither exists). This
    resolver is the sole consumer of ``SocialAccount.extra_data`` for
    authorization purposes as of ticket 05 -- the two must not be left live
    side by side, or they can silently drift apart.

    Cached on the request object since a single request can consult
    permissions (queryset scoping + `has_permission` +
    `has_object_permission`) more than once.
    """
    cached = getattr(request, "_org_context", None)
    if cached is not None:
        return cached

    roles: set[str] = set()
    org_slug = None

    auth = getattr(request, "auth", None)
    if isinstance(auth, dict):
        roles = extract_roles_from_token(auth).roles
        org_slug = extract_org_from_token(auth).default_slug
    else:
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            org_roles, org_slug = user_claims(user)
            roles = denormalize_org_roles(org_roles)
            if user.is_superuser:
                roles = roles | {"DJANGO_SUPERADMIN"}
            elif user.is_staff:
                roles = roles | {"DJANGO_STAFF"}

    context = (roles, org_slug, bool(roles & ORG_CHECK_EXEMPT_ROLES))
    request._org_context = context
    return context


class OrgScopedPermission(BasePermission):
    """SAFE_METHODS require READER+, writes require WRITER+, DELETE requires ADMIN.

    ``DJANGO_SUPERADMIN``/``DJANGO_STAFF`` bypass entirely (canonical §2b platform
    roles are never org-scoped). Cross-org access is turned into a 404, not a
    403, by queryset scoping (see :func:`org_scoped_queryset`) -- by the time
    ``has_object_permission`` runs, an object from another org was never
    fetched, so it never gets here at all.
    """

    def has_permission(self, request, view):
        roles, org_slug, exempt = get_request_org_context(request)
        if exempt:
            return True

        level = org_role_level(roles, org_slug)
        if level is None:
            return False

        if request.method in SAFE_METHODS:
            required = "READER"
        elif request.method == "DELETE":
            required = "ADMIN"
        else:
            required = "WRITER"

        return LEVEL_RANK[level] >= LEVEL_RANK[required]

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view)


def org_scoped_queryset(request, queryset, *, org_field="organization__slug"):
    """Scope ``queryset`` to the caller's org (or return it unscoped when exempt).

    This is the actual mechanism behind cross-org access returning 404
    (canonical §10a): a row from another org is simply absent from the
    queryset DRF's generic views use for both ``list`` and ``get_object``.
    """
    roles, org_slug, exempt = get_request_org_context(request)
    if exempt:
        return queryset
    if not org_slug:
        return queryset.none()
    return queryset.filter(**{org_field: org_slug})


def resolve_write_organization(request):
    """Resolve which :class:`Organization` a create should be attached to.

    Org-scoped members can only ever write into their own org, so it is
    derived from the token rather than trusted client input. Exempt callers
    (``DJANGO_SUPERADMIN``/``DJANGO_STAFF``, who may have no ``default_organization``
    of their own) may pass ``organization`` (slug or id) in the request body.
    Returns ``None`` if no organization could be resolved.
    """
    from .models import Organization

    _roles, org_slug, exempt = get_request_org_context(request)
    if org_slug:
        organization = Organization.objects.filter(slug=org_slug).first()
        if organization is not None:
            return organization

    if exempt:
        org_param = request.data.get("organization") if hasattr(request, "data") else None
        if org_param:
            return (
                Organization.objects.filter(pk=org_param).first()
                or Organization.objects.filter(slug=org_param).first()
            )

    return None


def _org_slug_of(obj, *, org_attr="organization"):
    """Resolve the owning Organization's slug, walking a dotted attribute path.

    ``org_attr`` may be a single attribute (``"organization"``,
    ``"owner_org"``) or a Django-lookup-style dotted path
    (``"campaign__organization"`` for Event/GeoStory, whose own FK is to
    Campaign, not directly to Organization).
    """
    target = obj
    for part in org_attr.split("__"):
        if target is None:
            return None
        target = getattr(target, part, None)
    return target.slug if target is not None else None


def check_org_level(request, obj, required, *, org_attr="organization"):
    """Shared org-ownership + role-level gate (used by the admin mixin and the
    standalone ``has_org_write_access`` helper, so the rule lives in one place).

    Superusers and exempt platform roles pass unconditionally. Otherwise the
    object (when given) must belong to the caller's org *and* the caller must
    hold at least ``required`` for it. Does **not** check ``is_staff``/
    ``is_active`` -- callers that need that gate on it separately.

    ``org_attr`` names the FK attribute that points at the owning
    Organization -- ``"organization"`` for Campaign/Workspace, but e.g.
    ``"owner_org"`` for MediaAsset, which can't reuse that name (it already
    has an unrelated meaning were it ever added).
    """
    if request.user.is_superuser:
        return True
    roles, org_slug, exempt = get_request_org_context(request)
    if exempt:
        return True
    if obj is not None and _org_slug_of(obj, org_attr=org_attr) != org_slug:
        return False
    level = org_role_level(roles, org_slug)
    return level is not None and LEVEL_RANK[level] >= LEVEL_RANK[required]


def has_org_write_access(request, obj, required="WRITER"):
    """Standalone version of ``OrgScopedAdminMixin``'s change-permission rule.

    For plain admin AJAX views (e.g. a change-form action button) that sit
    outside a ``ModelAdmin`` and so can't call ``self.has_change_permission``.
    ``obj`` must have an ``organization`` FK.
    """
    if request.user.is_superuser:
        return True
    if not (request.user and request.user.is_active and request.user.is_staff):
        return False
    return check_org_level(request, obj, required)


class OrgScopedAdminMixin:
    """Restrict a ``ModelAdmin`` to the caller's org (canonical §5b).

    Superusers and ``DJANGO_SUPERADMIN``/``DJANGO_STAFF`` token holders see every
    org's rows. Everyone else sees only their own org's rows; changing
    requires WRITER, deleting requires ADMIN (§2b level table).
    """

    org_lookup = "organization__slug"

    @property
    def _org_attr(self) -> str:
        """The model's owning-Organization FK attribute, derived from ``org_lookup``.

        ``org_lookup`` is a queryset-filter path (``"organization__slug"``,
        ``"owner_org__slug"``); this strips the ``__slug`` suffix to get the
        plain attribute name ``check_org_level`` needs for ``getattr(obj, ...)``.
        """
        return self.org_lookup.rsplit("__", 1)[0]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        _roles, org_slug, exempt = get_request_org_context(request)
        if exempt:
            return qs
        if not org_slug:
            return qs.none()
        return qs.filter(**{self.org_lookup: org_slug})

    def _is_active_staff(self, request):
        # Deliberately not `super().has_*_permission()` (Django's default
        # `user.has_perm(...)` check): this app never syncs Django
        # Permission/Group objects from Keycloak (canonical §11.3 -- no
        # separate authorization DB), so that check would always be False
        # for a non-superuser and make org-scoped staff access unreachable.
        return bool(request.user and request.user.is_active and request.user.is_staff)

    def has_add_permission(self, request):
        # No `obj` yet to check org ownership against -- WRITER+ in *some*
        # org (or exempt) is enough; the actual owning org is resolved at
        # save time (see `resolve_write_organization`).
        return self._is_active_staff(request) and check_org_level(request, None, "WRITER")

    def has_change_permission(self, request, obj=None):
        return self._is_active_staff(request) and check_org_level(
            request, obj, "WRITER", org_attr=self._org_attr
        )

    def has_delete_permission(self, request, obj=None):
        return self._is_active_staff(request) and check_org_level(
            request, obj, "ADMIN", org_attr=self._org_attr
        )


def _org_slug_of_campaign_owned(obj):
    campaign = getattr(obj, "campaign", None)
    return _org_slug_of(campaign) if campaign is not None else None


class CampaignScopedPermission(BasePermission):
    """Write-gate for models FK'd to Campaign (Event, GeoStory, MediaAsset).

    Unlike :class:`OrgScopedPermission` (used by ``Campaign`` itself, which
    has no separate public-visibility axis), these models are meant to be
    publicly *readable* -- their own view-level visibility/status scoping
    (``EventViewSet._apply_visibility_scope``, ``GeoStoryViewSet.get_queryset``
    published-only filtering) already handles that. So SAFE_METHODS always
    pass here; this class only gates writes, requiring WRITER+ (DELETE:
    ADMIN) in the *owning campaign's* organization -- derived through
    ``obj.campaign.organization``, not the object's own (nonexistent)
    ``organization`` FK.

    On create, there is no ``obj`` yet (the payload's ``campaign`` hasn't
    been validated as belonging to the caller's org) -- that check belongs
    in the view/serializer (see epic-11 PR1 §3.3), this only confirms the
    caller holds WRITER+ in *some* org (or is exempt), same pattern as
    ``OrgScopedAdminMixin.has_add_permission``.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        roles, org_slug, exempt = get_request_org_context(request)
        if exempt:
            return True
        required = "ADMIN" if request.method == "DELETE" else "WRITER"
        level = org_role_level(roles, org_slug)
        return level is not None and LEVEL_RANK[level] >= LEVEL_RANK[required]

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        required = "ADMIN" if request.method == "DELETE" else "WRITER"
        roles, org_slug, exempt = get_request_org_context(request)
        if exempt:
            return True
        if _org_slug_of_campaign_owned(obj) != org_slug:
            return False
        level = org_role_level(roles, org_slug)
        return level is not None and LEVEL_RANK[level] >= LEVEL_RANK[required]


def validate_campaign_organization(request, campaign) -> bool:
    """True when ``campaign`` belongs to the caller's org (or caller is exempt).

    Called from a serializer's ``validate()`` on create/update of a
    Campaign-owned resource (Event, GeoStory) to reject cross-org writes
    *before* they hit the DB -- ``CampaignScopedPermission`` alone can't
    catch this on create, since the object (and therefore its campaign)
    doesn't exist yet when ``has_permission`` runs.
    """
    if campaign is None:
        return True
    _roles, org_slug, exempt = get_request_org_context(request)
    if exempt:
        return True
    return _org_slug_of(campaign) == org_slug
