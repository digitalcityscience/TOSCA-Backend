"""Unit tests for org-scoped DRF permission, queryset scoping, and Django
admin scoping (epic-11 ticket 06, covering permissions.py from ticket 05;
admin has_*_permission migration is ticket 07).

No Keycloak/GeoServer. DRF (Bearer) requests are simulated via a fake
``request.auth`` dict, exactly what ``KeycloakTokenAuthentication.authenticate``
returns as the DRF auth context on a real request -- ``get_request_org_context``
reads that directly. Admin/session requests never populate ``request.auth``
(that's Bearer-only), so those tests attach ``user._auth_claims`` instead (see
``_grant`` in the admin section below) -- the same request-local resolver
input both ``get_request_org_context``'s browser fallback and ``has_perm()``
consult, and the in-memory stand-in for what a real login persists to
``UserAuthorizationSnapshot`` (ticket 05).
"""

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.campaigns.admin import CampaignAdmin
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.organizations.models import Organization
from tosca_api.apps.organizations.permissions import (
    OrgScopedPermission,
    get_request_org_context,
    org_scoped_queryset,
)


def _token(*roles, default_organization=None):
    payload = {"realm_access": {"roles": list(roles)}}
    if default_organization is not None:
        payload["default_organization"] = default_organization
    return payload


def _request(user, auth=None, method="GET"):
    factory = RequestFactory()
    request = getattr(factory, method.lower())("/fake/")
    request.user = user
    request.auth = auth
    return request


@pytest.fixture
def orgs(db):
    # 'dcs' is pre-seeded by organizations migration 0002; reuse it rather
    # than colliding with the unique slug constraint.
    dcs, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    gq, _ = Organization.objects.get_or_create(slug="gq", defaults={"name": "GQ"})
    return dcs, gq


# ---------------------------------------------------------------------------
# get_request_org_context
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_get_request_org_context_reads_token_auth(django_user_model):
    user = django_user_model.objects.create_user(username="reader")
    request = _request(user, auth=_token("ROLE_DCS_READER", default_organization="dcs"))

    roles, org_slug, exempt = get_request_org_context(request)

    assert roles == {"ROLE_DCS_READER"}
    assert org_slug == "dcs"
    assert exempt is False


@pytest.mark.django_db
def test_get_request_org_context_exempt_for_superadmin(django_user_model):
    user = django_user_model.objects.create_user(username="superadmin-user")
    request = _request(user, auth=_token("DJANGO_SUPERADMIN"))

    _roles, _org_slug, exempt = get_request_org_context(request)

    assert exempt is True


# ---------------------------------------------------------------------------
# OrgScopedPermission.has_permission
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_reader_can_list_but_not_write(django_user_model):
    user = django_user_model.objects.create_user(username="reader2")
    permission = OrgScopedPermission()

    get_request = _request(user, auth=_token("ROLE_DCS_READER", default_organization="dcs"), method="GET")
    post_request = _request(user, auth=_token("ROLE_DCS_READER", default_organization="dcs"), method="POST")

    assert permission.has_permission(get_request, None) is True
    assert permission.has_permission(post_request, None) is False


@pytest.mark.django_db
def test_writer_can_create_but_not_delete(django_user_model):
    user = django_user_model.objects.create_user(username="writer1")
    permission = OrgScopedPermission()

    post_request = _request(user, auth=_token("ROLE_DCS_WRITER", default_organization="dcs"), method="POST")
    delete_request = _request(user, auth=_token("ROLE_DCS_WRITER", default_organization="dcs"), method="DELETE")

    assert permission.has_permission(post_request, None) is True
    assert permission.has_permission(delete_request, None) is False


@pytest.mark.django_db
def test_admin_can_delete(django_user_model):
    user = django_user_model.objects.create_user(username="admin1")
    permission = OrgScopedPermission()

    delete_request = _request(user, auth=_token("ROLE_DCS_ADMIN", default_organization="dcs"), method="DELETE")

    assert permission.has_permission(delete_request, None) is True


@pytest.mark.django_db
def test_no_org_role_denies_all(django_user_model):
    user = django_user_model.objects.create_user(username="no-role")
    permission = OrgScopedPermission()

    get_request = _request(user, auth=_token(default_organization="dcs"), method="GET")

    assert permission.has_permission(get_request, None) is False


@pytest.mark.django_db
def test_superadmin_bypasses_level_check(django_user_model):
    user = django_user_model.objects.create_user(username="superadmin2")
    permission = OrgScopedPermission()

    delete_request = _request(user, auth=_token("DJANGO_SUPERADMIN"), method="DELETE")

    assert permission.has_permission(delete_request, None) is True


# ---------------------------------------------------------------------------
# org_scoped_queryset -- the actual cross-org-404 mechanism
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_org_scoped_queryset_only_returns_own_org(django_user_model, orgs):
    dcs, gq = orgs
    user = django_user_model.objects.create_user(username="dcs-reader")
    dcs_campaign = Campaign.objects.create(organization=dcs, title="DCS campaign", created_by=user)
    gq_campaign = Campaign.objects.create(organization=gq, title="GQ campaign", created_by=user)

    request = _request(user, auth=_token("ROLE_DCS_READER", default_organization="dcs"))
    scoped = org_scoped_queryset(request, Campaign.objects.all())

    assert list(scoped) == [dcs_campaign]
    # Cross-org access resolves to an empty queryset -> DRF's generics.get_object
    # raises Http404, never a 403 (canonical §10a).
    assert not scoped.filter(pk=gq_campaign.pk).exists()


@pytest.mark.django_db
def test_org_scoped_queryset_unscoped_for_exempt_roles(django_user_model, orgs):
    dcs, gq = orgs
    user = django_user_model.objects.create_user(username="staff-user")
    Campaign.objects.create(organization=dcs, title="DCS campaign", created_by=user)
    Campaign.objects.create(organization=gq, title="GQ campaign", created_by=user)

    request = _request(user, auth=_token("DJANGO_STAFF"))
    scoped = org_scoped_queryset(request, Campaign.objects.all())

    assert scoped.count() == 2


# ---------------------------------------------------------------------------
# Django admin scoping (OrgScopedAdminMixin via CampaignAdmin)
#
# Admin/session requests never populate `request.auth` (that's the Bearer/API
# path only) -- both `get_request_org_context`'s browser fallback and
# `has_perm()` (security tickets ticket 07) read the caller's claims via
# `organizations.policy.user_claims(user)`, which consults `user._auth_claims`
# first. `_grant` below attaches that directly, the same technique
# `test_auth_backend.py` uses for `has_perm()`, so `get_queryset` and
# `has_*_permission` see a consistent claims source within each test --
# exactly like a real admin request after login (ticket 05's persisted
# `UserAuthorizationSnapshot` is the production equivalent of this attribute).
# ---------------------------------------------------------------------------

def _grant(user, org_slug, level):
    user._auth_claims = AuthClaims(org_roles={org_slug: level}, default_org=org_slug, authoritative=True)
    return user


@pytest.fixture
def campaign_admin():
    return CampaignAdmin(Campaign, AdminSite())


@pytest.mark.django_db
def test_admin_get_queryset_scopes_non_superuser(django_user_model, orgs, campaign_admin):
    dcs, gq = orgs
    staff_user = django_user_model.objects.create_user(username="dcs-admin-staff", is_staff=True)
    dcs_campaign = Campaign.objects.create(organization=dcs, title="DCS campaign", created_by=staff_user)
    Campaign.objects.create(organization=gq, title="GQ campaign", created_by=staff_user)

    request = _request(_grant(staff_user, "dcs", "ADMIN"))
    qs = campaign_admin.get_queryset(request)

    assert list(qs) == [dcs_campaign]


@pytest.mark.django_db
def test_admin_get_queryset_unscoped_for_superuser(django_user_model, orgs, campaign_admin):
    dcs, gq = orgs
    superuser = django_user_model.objects.create_superuser(username="root", email="root@example.com", password="x")
    Campaign.objects.create(organization=dcs, title="DCS campaign", created_by=superuser)
    Campaign.objects.create(organization=gq, title="GQ campaign", created_by=superuser)

    request = _request(superuser)
    qs = campaign_admin.get_queryset(request)

    assert qs.count() == 2


@pytest.mark.django_db
def test_admin_delete_permission_requires_admin_level(django_user_model, orgs, campaign_admin):
    dcs, _gq = orgs
    writer_user = django_user_model.objects.create_user(username="dcs-writer-staff", is_staff=True)
    campaign = Campaign.objects.create(organization=dcs, title="DCS campaign", created_by=writer_user)

    request = _request(_grant(writer_user, "dcs", "WRITER"))

    assert campaign_admin.has_change_permission(request, campaign) is True
    assert campaign_admin.has_delete_permission(request, campaign) is False


@pytest.mark.django_db
def test_admin_change_delete_permission_no_longer_object_scoped(django_user_model, orgs, campaign_admin):
    """Security tickets ticket 07: `OrgScopedAdminMixin` dropped its object-level
    org check from has_change_permission/has_delete_permission -- capability
    is purely `has_perm()` (model-level, ticket 06), row scope is purely
    `get_queryset`. Passing a cross-org object directly (bypassing
    get_queryset) therefore no longer denies by itself; see
    test_admin_get_object_404s_for_cross_org_id below for the actual tenant
    gate a real admin request goes through."""
    dcs, gq = orgs
    dcs_admin_user = django_user_model.objects.create_user(username="dcs-admin-staff2", is_staff=True)
    gq_campaign = Campaign.objects.create(organization=gq, title="GQ campaign", created_by=dcs_admin_user)

    request = _request(_grant(dcs_admin_user, "dcs", "ADMIN"))

    assert campaign_admin.has_delete_permission(request, gq_campaign) is True


@pytest.mark.django_db
def test_admin_get_object_404s_for_cross_org_id(django_user_model, orgs, campaign_admin):
    """The real tenant gate: `get_object` (used by the changeform/delete
    views) filters through `get_queryset` first, so a cross-org id is never
    fetched in the first place -- matches the DRF `OrgScopedPermission`/
    `CampaignScopedPermission` pattern (queryset, not has_object_permission,
    is what turns cross-org access into a 404)."""
    dcs, gq = orgs
    dcs_admin_user = django_user_model.objects.create_user(username="dcs-admin-staff3", is_staff=True)
    gq_campaign = Campaign.objects.create(organization=gq, title="GQ campaign", created_by=dcs_admin_user)

    request = _request(_grant(dcs_admin_user, "dcs", "ADMIN"))

    assert campaign_admin.get_object(request, str(gq_campaign.pk)) is None


@pytest.mark.django_db
def test_admin_add_permission_granted_to_org_writer(django_user_model, orgs, campaign_admin):
    # Historical regression note (pre-ticket-06): before OrgRolePermissionBackend
    # existed, Django's default has_perm() was always False for non-superusers
    # (no Permission/Group rows ever synced from Keycloak), which would have
    # made "+ Add" unreachable for every org-scoped WRITER/ADMIN. As of ticket
    # 07 this now flows through has_add_permission -> super() -> has_perm(),
    # which OrgRolePermissionBackend (ticket 06) makes meaningful.
    dcs, _gq = orgs
    writer_user = django_user_model.objects.create_user(username="dcs-writer-add", is_staff=True)
    request = _request(_grant(writer_user, "dcs", "WRITER"))

    assert campaign_admin.has_add_permission(request) is True


@pytest.mark.django_db
def test_admin_add_permission_denied_below_writer(django_user_model, orgs, campaign_admin):
    dcs, _gq = orgs
    reader_user = django_user_model.objects.create_user(username="dcs-reader-add", is_staff=True)
    request = _request(_grant(reader_user, "dcs", "READER"))

    assert campaign_admin.has_add_permission(request) is False


@pytest.mark.django_db
def test_admin_view_permission_denied_when_not_staff(django_user_model, orgs, campaign_admin):
    """`is_staff` is the entry gate (ticket 07 split of responsibilities) --
    a non-staff user is denied even with a sufficient org role."""
    dcs, _gq = orgs
    non_staff_user = django_user_model.objects.create_user(username="dcs-admin-not-staff", is_staff=False)
    request = _request(_grant(non_staff_user, "dcs", "ADMIN"))

    assert campaign_admin.has_view_permission(request) is False
    assert campaign_admin.has_add_permission(request) is False
