"""EventSeries DRF permission-matrix tests (security tickets 2026-08-19
ticket 04).

Before this ticket ``EventSeriesViewSet`` used
``permissions.IsAuthenticatedOrReadOnly`` -- any authenticated user, even a
plain READER, could create/update a series. The admin already requires
WRITER+ via ``has_perm()`` (``TOSCA_PERMISSION_MODELS["events"]`` includes
``eventseries``); this closes the DRF/admin disagreement by switching to
``DjangoModelPermissionsOrAnonReadOnly`` (matching ``EventViewSet``), so
writes now go through the same capability ladder.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import EventSeries, EventType
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def dcs_org(db):
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def gq_org(db):
    org, _ = Organization.objects.get_or_create(slug="gq", defaults={"name": "GQ"})
    OrganizationAppEntitlement.objects.get_or_create(organization=org, app_label="events")
    return org


@pytest.fixture
def dcs_campaign(dcs_org):
    creator = User.objects.create_user(username="dcs-owner-series")
    return Campaign.objects.create(title="DCS Campaign", organization=dcs_org, created_by=creator)


@pytest.fixture
def gq_campaign(gq_org):
    creator = User.objects.create_user(username="gq-owner-series")
    return Campaign.objects.create(title="GQ Campaign", organization=gq_org, created_by=creator)


@pytest.fixture
def event_type(db):
    return EventType.objects.create(code="series-matrix", label="Series Matrix Event")


def _token(*roles, org_slug="dcs"):
    return {"realm_access": {"roles": list(roles)}, "default_organization": org_slug}


def _authenticate(api_client, username, level, org_slug="dcs"):
    user = User.objects.create_user(username=username)
    roles = [f"ROLE_{org_slug.upper()}_{level}"] if level else []
    if level:
        user._auth_claims = AuthClaims(
            org_roles={org_slug: level}, default_org=org_slug, authoritative=True
        )
    api_client.force_authenticate(user=user, token=_token(*roles, org_slug=org_slug))
    return user


def _series_payload(campaign, event_type, **overrides):
    payload = {
        "campaign": str(campaign.id),
        "event_type": str(event_type.id),
        "name": "Matrix Series",
        "series_mode": "recurring",
        "recurrence_type": "weekly",
        "start_date": "2026-04-06",
        "occurrence_count": 3,
        "interval": 1,
        "start_time": "18:00:00",
        "end_time": "19:30:00",
        "timezone": "Europe/Berlin",
        "by_weekday": ["monday"],
        "title": "Matrix Event",
        "location_mode": "online",
        "online_url": "https://example.org/live",
        "status": "draft",
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_reader_cannot_create_series(api_client, dcs_campaign, event_type):
    _authenticate(api_client, "reader-create-series", "READER")
    response = api_client.post(
        "/api/v1/event-series/", _series_payload(dcs_campaign, event_type), format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_writer_can_create_series(api_client, dcs_campaign, event_type):
    _authenticate(api_client, "writer-create-series", "WRITER")
    response = api_client.post(
        "/api/v1/event-series/", _series_payload(dcs_campaign, event_type), format="json",
    )
    assert response.status_code == 201


@pytest.mark.django_db
def test_reader_cannot_update_series(api_client, dcs_campaign, event_type):
    creator = _authenticate(api_client, "writer-seed-series", "WRITER")
    create_response = api_client.post(
        "/api/v1/event-series/", _series_payload(dcs_campaign, event_type), format="json",
    )
    assert create_response.status_code == 201
    series_id = create_response.data["id"]

    _authenticate(api_client, "reader-update-series", "READER")
    response = api_client.patch(
        f"/api/v1/event-series/{series_id}/", {"name": "Hacked"}, format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_cross_org_write_denied(api_client, gq_campaign, event_type):
    """A DCS WRITER cannot create a series attached to a GQ campaign."""
    _authenticate(api_client, "dcs-writer-crossorg-series", "WRITER", org_slug="dcs")
    response = api_client.post(
        "/api/v1/event-series/", _series_payload(gq_campaign, event_type), format="json",
    )
    assert response.status_code in (400, 403)
