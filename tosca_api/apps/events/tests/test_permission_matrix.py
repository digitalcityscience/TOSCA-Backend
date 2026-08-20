"""Event DRF permission-matrix tests (security tickets ticket 10).

Event is a **public-read** resource, same matrix as GeoStory (ticket 09):

- Gate A (capability: add/change/delete) -- ``DjangoModelPermissionsOrAnonReadOnly``
  -> ``has_perm()`` -> ``OrgRolePermissionBackend`` (ticket 06) for writes,
  gated by entitlement (gate B).
- Gate C (org membership + object scope) -- ``CampaignScopedPermission``
  (SAFE_METHODS always pass; writes require *some* role in the owning
  campaign's org).

Unlike GeoStory, ``EventViewSet._apply_visibility_scope`` gates the *read*
queryset on ``user.is_staff`` only -- there is no "own org draft visible"
branch (that quirk is pre-existing and out of scope for tickets 09/10; see
the ticket's note that ``Event.effective_visibility`` derives from the
owning Campaign). So non-staff org members -- including WRITER/ADMIN in the
event's own org -- cannot even retrieve a DRAFT event; the role-matrix
below therefore exercises writes against a **published** event, which *is*
queryset-visible to everyone. That also means a cross-org write against a
published event is rejected by ``CampaignScopedPermission`` as a 403 (the
object was fetched -- it's public), not a 404 -- 404 is reserved for rows
the queryset itself excludes.

Exercised end-to-end through the real DRF stack (``APIClient``), not the
permission classes in isolation.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import Event
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def dcs_org(db):
    # Pre-seeded by organizations migration 0002 + entitled to every
    # TOSCA_ENTITLEABLE_APPS app (incl. "events") by migration 0005.
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def gq_org(db):
    org, _ = Organization.objects.get_or_create(slug="gq", defaults={"name": "GQ"})
    OrganizationAppEntitlement.objects.get_or_create(organization=org, app_label="events")
    return org


@pytest.fixture
def unentitled_org(db):
    """An org whose members hold a real role but "events" isn't entitled
    (gate B failure) -- deliberately **no** ``OrganizationAppEntitlement`` row."""
    org, _ = Organization.objects.get_or_create(slug="noent-ev", defaults={"name": "No Entitlement EV"})
    return org


@pytest.fixture
def dcs_campaign(dcs_org):
    creator = User.objects.create_user(username="dcs-owner-ev")
    return Campaign.objects.create(title="DCS Campaign", organization=dcs_org, created_by=creator)


@pytest.fixture
def gq_campaign(gq_org):
    creator = User.objects.create_user(username="gq-owner-ev")
    return Campaign.objects.create(title="GQ Campaign", organization=gq_org, created_by=creator)


def _published_event(campaign, title):
    organizer = User.objects.create_user(username=f"{title}-organizer")
    return Event.objects.create(
        campaign=campaign,
        title=title,
        summary="summary",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=organizer,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
        provider_phone="+49 89 12345",
    )


@pytest.fixture
def dcs_event(dcs_campaign):
    return _published_event(dcs_campaign, "DCS Event")


@pytest.fixture
def gq_event(gq_campaign):
    return _published_event(gq_campaign, "GQ Event")


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
def test_anon_can_read_published_event(api_client, dcs_event):
    response = api_client.get(f"/api/v1/events/{dcs_event.id}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_anon_cannot_write(api_client, dcs_campaign):
    response = api_client.post(
        "/api/v1/events/",
        {
            "campaign": str(dcs_campaign.id),
            "title": "Anon Event",
            "start_datetime": timezone.now() + timedelta(days=1),
            "end_datetime": timezone.now() + timedelta(days=1, hours=1),
        },
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Role-matrix: READER / WRITER / ADMIN, own org (against a published event --
# see module docstring for why drafts aren't used here)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reader_can_read_own_org_event(api_client, dcs_event):
    _authenticate(api_client, "reader-own-ev", "READER")
    response = api_client.get(f"/api/v1/events/{dcs_event.id}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_reader_cannot_write_own_org_event(api_client, dcs_event):
    _authenticate(api_client, "reader-write-ev", "READER")
    response = api_client.patch(f"/api/v1/events/{dcs_event.id}/", {"title": "Hacked"})
    assert response.status_code == 403


@pytest.mark.django_db
def test_writer_can_change_own_org_event(api_client, dcs_event):
    _authenticate(api_client, "writer-change-ev", "WRITER")
    response = api_client.patch(f"/api/v1/events/{dcs_event.id}/", {"title": "Updated"}, format="json")
    assert response.status_code == 200, response.data
    dcs_event.refresh_from_db()
    assert dcs_event.title == "Updated"


@pytest.mark.django_db
def test_writer_can_create_own_org_event(api_client, dcs_campaign):
    _authenticate(api_client, "writer-create-ev", "WRITER")
    response = api_client.post(
        "/api/v1/events/",
        {
            "campaign": str(dcs_campaign.id),
            "title": "New Event",
            "summary": "summary",
            "start_datetime": timezone.now() + timedelta(days=1),
            "end_datetime": timezone.now() + timedelta(days=1, hours=1),
            "location": {"type": "Point", "coordinates": [10.0, 53.5]},
            "status": Event.Status.DRAFT,
            "provider_phone": "+49 89 12345",
        },
        format="json",
    )
    assert response.status_code == 201


@pytest.mark.django_db
def test_writer_cannot_delete_own_org_event(api_client, dcs_event):
    _authenticate(api_client, "writer-delete-ev", "WRITER")
    response = api_client.delete(f"/api/v1/events/{dcs_event.id}/")
    assert response.status_code == 403
    assert Event.objects.filter(pk=dcs_event.pk).exists()


@pytest.mark.django_db
def test_admin_can_delete_own_org_event(api_client, dcs_event):
    _authenticate(api_client, "admin-delete-ev", "ADMIN")
    response = api_client.delete(f"/api/v1/events/{dcs_event.id}/")
    assert response.status_code == 204
    assert not Event.objects.filter(pk=dcs_event.pk).exists()


# ---------------------------------------------------------------------------
# Cross-org isolation (gate C)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cross_org_write_to_published_event_is_403(api_client, gq_event):
    """The published GQ event is queryset-visible to a DCS writer (public
    read), so this is CampaignScopedPermission's object-permission check
    (403), not the queryset excluding the row (404)."""
    _authenticate(api_client, "dcs-writer-crossorg-ev", "WRITER", org_slug="dcs")
    response = api_client.patch(f"/api/v1/events/{gq_event.id}/", {"title": "Hacked"})
    assert response.status_code == 403


@pytest.mark.django_db
def test_cross_org_create_rejected(api_client, gq_campaign):
    """Create-time cross-org campaign attach is rejected by the serializer's
    ``validate_campaign_organization`` call, not by ``CampaignScopedPermission``
    (no object exists yet on create)."""
    _authenticate(api_client, "dcs-writer-create-crossorg-ev", "WRITER", org_slug="dcs")
    response = api_client.post(
        "/api/v1/events/",
        {
            "campaign": str(gq_campaign.id),
            "title": "Cross-org Event",
            "summary": "summary",
            "start_datetime": timezone.now() + timedelta(days=1),
            "end_datetime": timezone.now() + timedelta(days=1, hours=1),
            "status": Event.Status.DRAFT,
            "provider_phone": "+49 89 12345",
        },
        format="json",
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Entitlement (gate B) -- role present, app not entitled to the org
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_entitlement_missing_denies_write(api_client, unentitled_org):
    creator = User.objects.create_user(username="noent-owner-ev")
    campaign = Campaign.objects.create(
        title="No-entitlement Campaign", organization=unentitled_org, created_by=creator
    )
    event = _published_event(campaign, "Noent Event")
    _authenticate(api_client, "noent-writer-ev", "WRITER", org_slug="noent-ev")

    response = api_client.patch(f"/api/v1/events/{event.id}/", {"title": "Hacked"})
    assert response.status_code == 403
