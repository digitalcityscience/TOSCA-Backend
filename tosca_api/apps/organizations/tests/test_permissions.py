"""Unit tests for org-scoped DRF permission, queryset scoping, and Django
admin scoping (epic-11 ticket 06, covering permissions.py from ticket 05).

No Keycloak/GeoServer -- token claims are simulated via a fake ``request.auth``
dict, exactly what ``KeycloakTokenAuthentication.authenticate`` returns as the
DRF auth context on a real request.
"""

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

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
# ---------------------------------------------------------------------------

@pytest.fixture
def campaign_admin():
    return CampaignAdmin(Campaign, AdminSite())


@pytest.mark.django_db
def test_admin_get_queryset_scopes_non_superuser(django_user_model, orgs, campaign_admin):
    dcs, gq = orgs
    staff_user = django_user_model.objects.create_user(username="dcs-admin-staff", is_staff=True)
    dcs_campaign = Campaign.objects.create(organization=dcs, title="DCS campaign", created_by=staff_user)
    Campaign.objects.create(organization=gq, title="GQ campaign", created_by=staff_user)

    request = _request(staff_user, auth=_token("ROLE_DCS_ADMIN", default_organization="dcs"))
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

    request = _request(writer_user, auth=_token("ROLE_DCS_WRITER", default_organization="dcs"))

    assert campaign_admin.has_change_permission(request, campaign) is True
    assert campaign_admin.has_delete_permission(request, campaign) is False


@pytest.mark.django_db
def test_admin_delete_permission_denied_across_orgs(django_user_model, orgs, campaign_admin):
    dcs, gq = orgs
    dcs_admin_user = django_user_model.objects.create_user(username="dcs-admin-staff2", is_staff=True)
    gq_campaign = Campaign.objects.create(organization=gq, title="GQ campaign", created_by=dcs_admin_user)

    request = _request(dcs_admin_user, auth=_token("ROLE_DCS_ADMIN", default_organization="dcs"))

    assert campaign_admin.has_delete_permission(request, gq_campaign) is False
