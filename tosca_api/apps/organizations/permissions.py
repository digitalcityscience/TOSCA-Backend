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
    ORG_CHECK_EXEMPT_ROLES,
    extract_org_from_social_data,
    extract_org_from_token,
    extract_roles_from_social_data,
    extract_roles_from_token,
    org_role_level,
)

LEVEL_RANK = {"READER": 0, "WRITER": 1, "ADMIN": 2}


def get_request_org_context(request):
    """Return ``(roles, org_slug, exempt)`` for ``request``.

    Reads the decoded Keycloak token DRF authentication backends attach as
    ``request.auth`` (``KeycloakTokenAuthentication``). Falls back to the
    user's linked Keycloak ``SocialAccount.extra_data`` for session-based
    requests (Django admin), which never populate ``request.auth``. Cached on
    the request object since a single request can consult permissions
    (queryset scoping + `has_permission` + `has_object_permission`) more than
    once.
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
            social_account = user.socialaccount_set.filter(provider="keycloak").first()
            if social_account is not None:
                roles = extract_roles_from_social_data(social_account.extra_data).roles
                org_slug = extract_org_from_social_data(social_account.extra_data).default_slug

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
    roles, org_slug, exempt = get_request_org_context(request)
    if exempt:
        return True
    organization = getattr(obj, "organization", None)
    obj_slug = organization.slug if organization is not None else None
    if obj_slug != org_slug:
        return False
    level = org_role_level(roles, org_slug)
    return level is not None and LEVEL_RANK[level] >= LEVEL_RANK[required]


class OrgScopedAdminMixin:
    """Restrict a ``ModelAdmin`` to the caller's org (canonical §5b).

    Superusers and ``DJANGO_SUPERADMIN``/``DJANGO_STAFF`` token holders see every
    org's rows. Everyone else sees only their own org's rows; changing
    requires WRITER, deleting requires ADMIN (§2b level table).
    """

    org_lookup = "organization__slug"

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

    def _org_slug_of(self, obj):
        organization = getattr(obj, "organization", None)
        return organization.slug if organization is not None else None

    def _has_org_level(self, request, obj, required):
        if request.user.is_superuser:
            return True
        roles, org_slug, exempt = get_request_org_context(request)
        if exempt:
            return True
        if obj is not None and self._org_slug_of(obj) != org_slug:
            return False
        level = org_role_level(roles, org_slug)
        return level is not None and LEVEL_RANK[level] >= LEVEL_RANK[required]

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
        return self._is_active_staff(request) and self._has_org_level(request, None, "WRITER")

    def has_change_permission(self, request, obj=None):
        return self._is_active_staff(request) and self._has_org_level(request, obj, "WRITER")

    def has_delete_permission(self, request, obj=None):
        return self._is_active_staff(request) and self._has_org_level(request, obj, "ADMIN")
