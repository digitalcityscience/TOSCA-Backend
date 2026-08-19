"""GeoStory DRF permission-matrix tests (security tickets ticket 09).

GeoStory is a **public-read** resource (unlike Campaign, an org-private one):

- Gate A (capability: add/change/delete) -- ``DjangoModelPermissionsOrAnonReadOnly``
  (a plain ``DjangoModelPermissions`` variant that lets SAFE_METHODS through
  for *everyone*, including anonymous callers, since GET/HEAD map to no
  required permission at all) -> ``has_perm()`` -> ``OrgRolePermissionBackend``
  (ticket 06) for writes, itself gated by entitlement (gate B).
- Gate C (org membership + object/row scope) -- ``CampaignScopedPermission``
  (SAFE_METHODS always pass; writes require *some* role in the owning
  campaign's org) + the view's own visibility-based queryset scoping
  (``GeoStoryViewSet._scope_by_visibility``, ticket 02's S1 fix), which is
  the actual tenant boundary for reads: cross-org drafts are excluded from
  the queryset entirely, so retrieve is a 404, never a 403.

Exercised end-to-end through the real DRF stack (``APIClient``), not the
permission classes in isolation.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geostories.models import GeoStory
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def dcs_org(db):
    # Pre-seeded by organizations migration 0002 + entitled to every
    # TOSCA_ENTITLEABLE_APPS app (incl. "geostories") by migration 0005.
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def gq_org(db):
    org, _ = Organization.objects.get_or_create(slug="gq", defaults={"name": "GQ"})
    OrganizationAppEntitlement.objects.get_or_create(organization=org, app_label="geostories")
    return org


@pytest.fixture
def unentitled_org(db):
    """An org whose members hold a real role but "geostories" isn't entitled
    (gate B failure) -- deliberately **no** ``OrganizationAppEntitlement`` row."""
    org, _ = Organization.objects.get_or_create(slug="noent-gs", defaults={"name": "No Entitlement GS"})
    return org


@pytest.fixture
def dcs_campaign(dcs_org):
    creator = User.objects.create_user(username="dcs-owner-gs")
    return Campaign.objects.create(title="DCS Campaign", organization=dcs_org, created_by=creator)


@pytest.fixture
def gq_campaign(gq_org):
    creator = User.objects.create_user(username="gq-owner-gs")
    return Campaign.objects.create(title="GQ Campaign", organization=gq_org, created_by=creator)


def _story(campaign, title, status=GeoStory.Status.DRAFT):
    author = User.objects.create_user(username=f"{title}-author")
    return GeoStory.objects.create(
        title=title, campaign=campaign, author=author, status=status
    )


@pytest.fixture
def dcs_draft(dcs_campaign):
    return _story(dcs_campaign, "DCS Draft")


@pytest.fixture
def dcs_published(dcs_campaign):
    return _story(dcs_campaign, "DCS Published", status=GeoStory.Status.PUBLISHED)


@pytest.fixture
def gq_draft(gq_campaign):
    return _story(gq_campaign, "GQ Draft")


def _token(*roles, org_slug="dcs"):
    return {"realm_access": {"roles": list(roles)}, "default_organization": org_slug}


def _authenticate(api_client, username, level, org_slug="dcs"):
    """Log ``username`` in as ``level`` (READER/WRITER/ADMIN/None) in
    ``org_slug``, wiring both gate C's token source (``request.auth``) and
    gate A's ``has_perm()`` source (``user._auth_claims``) -- see
    ``campaigns/tests/test_permission_matrix.py`` for why both are required
    with ``force_authenticate``.
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
# Anonymous public reads (gate A: DjangoModelPermissionsOrAnonReadOnly)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_anon_can_read_published_story(api_client, dcs_published):
    response = api_client.get(f"/api/v1/stories/{dcs_published.id}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_anon_cannot_read_draft_story(api_client, dcs_draft):
    """Cross-org/anon draft visibility is the queryset's job (ticket 02) --
    a 404, not a 403."""
    response = api_client.get(f"/api/v1/stories/{dcs_draft.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_anon_cannot_write(api_client, dcs_campaign):
    response = api_client.post(
        "/api/v1/stories/", {"title": "Anon Story", "campaign": str(dcs_campaign.id)}
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Role-matrix: READER / WRITER / ADMIN, own org
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reader_can_read_own_org_draft(api_client, dcs_draft):
    _authenticate(api_client, "reader-own-gs", "READER")
    response = api_client.get(f"/api/v1/stories/{dcs_draft.id}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_reader_cannot_write_own_org_story(api_client, dcs_draft):
    _authenticate(api_client, "reader-write-gs", "READER")
    response = api_client.patch(f"/api/v1/stories/{dcs_draft.id}/", {"title": "Hacked"})
    assert response.status_code == 403


@pytest.mark.django_db
def test_writer_can_change_own_org_story(api_client, dcs_draft):
    _authenticate(api_client, "writer-change-gs", "WRITER")
    response = api_client.patch(f"/api/v1/stories/{dcs_draft.id}/", {"title": "Updated"})
    assert response.status_code == 200
    dcs_draft.refresh_from_db()
    assert dcs_draft.title == "Updated"


@pytest.mark.django_db
def test_writer_can_create_own_org_story(api_client, dcs_campaign):
    _authenticate(api_client, "writer-create-gs", "WRITER")
    response = api_client.post(
        "/api/v1/stories/", {"title": "New Story", "campaign": str(dcs_campaign.id)}
    )
    assert response.status_code == 201


@pytest.mark.django_db
def test_writer_cannot_delete_own_org_story(api_client, dcs_draft):
    _authenticate(api_client, "writer-delete-gs", "WRITER")
    response = api_client.delete(f"/api/v1/stories/{dcs_draft.id}/")
    assert response.status_code == 403
    assert GeoStory.objects.filter(pk=dcs_draft.pk).exists()


@pytest.mark.django_db
def test_admin_can_delete_own_org_story(api_client, dcs_draft):
    _authenticate(api_client, "admin-delete-gs", "ADMIN")
    response = api_client.delete(f"/api/v1/stories/{dcs_draft.id}/")
    assert response.status_code == 204
    assert not GeoStory.objects.filter(pk=dcs_draft.pk).exists()


# ---------------------------------------------------------------------------
# Cross-org isolation (gate C / ticket 02)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cross_org_draft_retrieve_is_404(api_client, gq_draft):
    _authenticate(api_client, "dcs-admin-crossorg-gs", "ADMIN", org_slug="dcs")
    response = api_client.get(f"/api/v1/stories/{gq_draft.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_cross_org_write_is_404(api_client, gq_draft):
    _authenticate(api_client, "dcs-writer-crossorg-gs", "WRITER", org_slug="dcs")
    response = api_client.patch(f"/api/v1/stories/{gq_draft.id}/", {"title": "Hacked"})
    assert response.status_code == 404


@pytest.mark.django_db
def test_cross_org_create_rejected(api_client, gq_campaign):
    """Create-time cross-org campaign attach is rejected by the serializer's
    ``validate_campaign_organization`` call, not by ``CampaignScopedPermission``
    (no object exists yet on create)."""
    _authenticate(api_client, "dcs-writer-create-crossorg-gs", "WRITER", org_slug="dcs")
    response = api_client.post(
        "/api/v1/stories/", {"title": "Cross-org Story", "campaign": str(gq_campaign.id)}
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Entitlement (gate B) -- role present, app not entitled to the org
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_entitlement_missing_denies_write(api_client, unentitled_org):
    creator = User.objects.create_user(username="noent-owner-gs")
    campaign = Campaign.objects.create(
        title="No-entitlement Campaign", organization=unentitled_org, created_by=creator
    )
    story = _story(campaign, "Noent Story")
    _authenticate(api_client, "noent-writer-gs", "WRITER", org_slug="noent-gs")

    response = api_client.patch(f"/api/v1/stories/{story.id}/", {"title": "Hacked"})
    assert response.status_code == 403
