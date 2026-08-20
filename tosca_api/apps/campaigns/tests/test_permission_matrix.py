"""Campaign DRF permission-matrix tests (security tickets ticket 08).

Campaign is the tracer resource for the new gate A + gate C split:

- Gate A (capability: view/add/change/delete) -- ``ViewGatedModelPermissions``
  (``DjangoModelPermissions`` variant that also gates GET/HEAD) ->
  ``has_perm()`` -> ``OrgRolePermissionBackend`` (ticket 06), itself gated by
  entitlement (``OrganizationAppEntitlement``, gate B).
- Gate C (org membership + object/row scope) -- ``OrgScopedPermission`` +
  ``org_scoped_queryset``, unchanged in spirit but stripped of its former
  action->level ladder (that capability check now lives in gate A only).

Exercised end-to-end through the real DRF stack (``APIClient``), not the
permission classes in isolation -- this is the "does the whole enforcement
chain compose" suite the ticket asks for.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def dcs_org(db):
    # Pre-seeded by organizations migration 0002 + entitled to every
    # TOSCA_ENTITLEABLE_APPS app (incl. "campaigns") by migration 0005.
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def gq_org(db):
    org, _ = Organization.objects.get_or_create(slug="gq", defaults={"name": "GQ"})
    OrganizationAppEntitlement.objects.get_or_create(organization=org, app_label="campaigns")
    return org


@pytest.fixture
def unentitled_org(db):
    """An org whose members hold a real role but "campaigns" isn't entitled
    (gate B failure) -- deliberately **no** ``OrganizationAppEntitlement`` row."""
    org, _ = Organization.objects.get_or_create(slug="noent", defaults={"name": "No Entitlement"})
    return org


@pytest.fixture
def dcs_campaign(dcs_org):
    creator = User.objects.create_user(username="dcs-owner")
    return Campaign.objects.create(title="DCS Campaign", organization=dcs_org, created_by=creator)


@pytest.fixture
def gq_campaign(gq_org):
    creator = User.objects.create_user(username="gq-owner")
    return Campaign.objects.create(title="GQ Campaign", organization=gq_org, created_by=creator)


def _token(*roles, org_slug="dcs"):
    return {"realm_access": {"roles": list(roles)}, "default_organization": org_slug}


def _authenticate(api_client, username, level, org_slug="dcs"):
    """Log ``username`` in as ``level`` (READER/WRITER/ADMIN/None) in
    ``org_slug``, wiring both gate C's token source (``request.auth``) and
    gate A's ``has_perm()`` source (``user._auth_claims``) -- see
    ``campaigns/tests/test_api.py`` for why both are required with
    ``force_authenticate``.
    """
    user = User.objects.create_user(username=username)
    roles = [f"ROLE_{org_slug.upper()}_{level}"] if level else []
    if level:
        user._auth_claims = AuthClaims(
            org_roles={org_slug: level}, default_org=org_slug, authoritative=True
        )
    api_client.force_authenticate(user=user, token=_token(*roles, org_slug=org_slug))
    return user


# ---------------------------------------------------------------------------
# Role-matrix: READER / WRITER / ADMIN, own org
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reader_can_read_own_org_campaign(api_client, dcs_campaign):
    _authenticate(api_client, "reader-own", "READER")
    response = api_client.get(f"/api/v1/campaigns/{dcs_campaign.id}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_reader_cannot_write_own_org_campaign(api_client, dcs_campaign):
    _authenticate(api_client, "reader-write", "READER")
    response = api_client.patch(f"/api/v1/campaigns/{dcs_campaign.id}/", {"title": "Hacked"})
    assert response.status_code == 403


@pytest.mark.django_db
def test_writer_can_change_own_org_campaign(api_client, dcs_campaign):
    _authenticate(api_client, "writer-change", "WRITER")
    response = api_client.patch(f"/api/v1/campaigns/{dcs_campaign.id}/", {"title": "Updated"})
    assert response.status_code == 200
    dcs_campaign.refresh_from_db()
    assert dcs_campaign.title == "Updated"


@pytest.mark.django_db
def test_writer_cannot_delete_own_org_campaign(api_client, dcs_campaign):
    _authenticate(api_client, "writer-delete", "WRITER")
    response = api_client.delete(f"/api/v1/campaigns/{dcs_campaign.id}/")
    assert response.status_code == 403
    assert Campaign.objects.filter(pk=dcs_campaign.pk).exists()


@pytest.mark.django_db
def test_admin_can_delete_own_org_campaign(api_client, dcs_campaign):
    _authenticate(api_client, "admin-delete", "ADMIN")
    response = api_client.delete(f"/api/v1/campaigns/{dcs_campaign.id}/")
    assert response.status_code == 204
    assert not Campaign.objects.filter(pk=dcs_campaign.pk).exists()


@pytest.mark.django_db
def test_no_role_denied(api_client, dcs_campaign):
    _authenticate(api_client, "no-role", None)
    response = api_client.get(f"/api/v1/campaigns/{dcs_campaign.id}/")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Cross-org isolation (gate C)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cross_org_retrieve_is_404(api_client, gq_campaign):
    """A DCS admin must not even see a GQ campaign exists -- queryset
    scoping (gate C), not a 403."""
    _authenticate(api_client, "dcs-admin-crossorg", "ADMIN", org_slug="dcs")
    response = api_client.get(f"/api/v1/campaigns/{gq_campaign.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_cross_org_write_is_404(api_client, gq_campaign):
    _authenticate(api_client, "dcs-writer-crossorg", "WRITER", org_slug="dcs")
    response = api_client.patch(f"/api/v1/campaigns/{gq_campaign.id}/", {"title": "Hacked"})
    assert response.status_code == 404


@pytest.mark.django_db
def test_cross_org_delete_is_404(api_client, gq_campaign):
    _authenticate(api_client, "dcs-admin-crossorg-del", "ADMIN", org_slug="dcs")
    response = api_client.delete(f"/api/v1/campaigns/{gq_campaign.id}/")
    assert response.status_code == 404
    assert Campaign.objects.filter(pk=gq_campaign.pk).exists()


@pytest.mark.django_db
def test_list_excludes_other_org_campaigns(api_client, dcs_campaign, gq_campaign):
    _authenticate(api_client, "dcs-reader-list", "READER", org_slug="dcs")
    response = api_client.get("/api/v1/campaigns/")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data["results"]}
    assert str(dcs_campaign.id) in ids
    assert str(gq_campaign.id) not in ids


# ---------------------------------------------------------------------------
# Entitlement (gate B) -- role present, app not entitled to the org
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_entitlement_missing_denies_read(api_client, unentitled_org):
    creator = User.objects.create_user(username="noent-owner")
    campaign = Campaign.objects.create(
        title="No-entitlement Campaign", organization=unentitled_org, created_by=creator
    )
    _authenticate(api_client, "noent-admin", "ADMIN", org_slug="noent")

    response = api_client.get(f"/api/v1/campaigns/{campaign.id}/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_entitlement_missing_denies_write(api_client, unentitled_org):
    creator = User.objects.create_user(username="noent-owner2")
    campaign = Campaign.objects.create(
        title="No-entitlement Campaign 2", organization=unentitled_org, created_by=creator
    )
    _authenticate(api_client, "noent-writer", "WRITER", org_slug="noent")

    response = api_client.patch(f"/api/v1/campaigns/{campaign.id}/", {"title": "Hacked"})
    assert response.status_code == 403
