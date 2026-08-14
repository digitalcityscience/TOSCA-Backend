from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import Event

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


def _org_token(*roles, org="dcs"):
    """Keycloak-shaped token for org-scoped writes (epic-11 PR1 SS3.3)."""
    return {"realm_access": {"roles": list(roles)}, "default_organization": org}


@pytest.fixture
def user():
    return User.objects.create_user(username="phase2", password="pw")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Phase 2", created_by=user)


def _published_payload(campaign):
    return {
        "title": "Phase 2 event",
        "summary": "Phase 2 summary",
        "campaign": str(campaign.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=1)).isoformat(),
        "location_mode": Event.LocationMode.PHYSICAL,
        "location": {"type": "Point", "coordinates": [10.0, 53.5]},
        "status": Event.Status.PUBLISHED,
        "provider_phone": "+49 89 12345",
    }


@pytest.mark.django_db
def test_summary_over_100_chars_rejected_via_api(api_client, user, campaign):
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    payload = _published_payload(campaign)
    payload["summary"] = "x" * 101
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 400
    assert "summary" in response.data


@pytest.mark.django_db
def test_published_event_requires_summary_via_api(api_client, user, campaign):
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    payload = _published_payload(campaign)
    payload["summary"] = ""
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 400
    assert "summary" in response.data


@pytest.mark.django_db
def test_published_event_requires_provider_contact_via_api(api_client, user, campaign):
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    payload = _published_payload(campaign)
    payload["provider_phone"] = ""
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 400
    assert "provider_phone" in response.data


@pytest.mark.django_db
def test_published_event_accepts_email_as_only_contact(api_client, user, campaign):
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    payload = _published_payload(campaign)
    payload["provider_phone"] = ""
    payload["provider_email"] = "team@example.com"
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 201, response.data


@pytest.mark.django_db
def test_draft_event_can_omit_summary_and_contact(api_client, user, campaign):
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    payload = _published_payload(campaign)
    payload["status"] = Event.Status.DRAFT
    payload["summary"] = ""
    payload["provider_phone"] = ""
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 201, response.data


@pytest.mark.django_db
def test_language_enum_rejects_unknown_code(user, campaign):
    event = Event(
        campaign=campaign,
        title="Lang Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        language=["xx"],
    )
    with pytest.raises(ValidationError) as exc:
        event.clean()
    assert "language" in exc.value.message_dict


@pytest.mark.django_db
def test_language_note_requires_other_in_language(user, campaign):
    event = Event(
        campaign=campaign,
        title="Note Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        language=["de"],
        language_note="Bavarian dialect",
    )
    with pytest.raises(ValidationError) as exc:
        event.clean()
    assert "language_note" in exc.value.message_dict


@pytest.mark.django_db
def test_language_note_allowed_when_other_present(user, campaign):
    event = Event.objects.create(
        campaign=campaign,
        title="Note Event OK",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        language=["de", "other"],
        language_note="Bavarian dialect",
    )
    assert event.language_note == "Bavarian dialect"


@pytest.mark.django_db
def test_event_create_round_trips_new_fields(api_client, user, campaign):
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    payload = _published_payload(campaign)
    payload.update(
        {
            "lead_name": "Max Mustermann (Trainer)",
            "external_url": "https://example.com/register",
            "venue_address": "Musterstraße 4, 81671 München",
            "district": "Maxvorstadt",
            "language": ["de", "en"],
            "provider_address": "Carl-Wery-Straße 28, 81739 München",
            "provider_email": "kontakt@example.com",
            "provider_social": "Instagram: @example",
        }
    )
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 201, response.data
    detail = api_client.get(f"/api/v1/events/{response.data['id']}/")
    assert detail.data["lead_name"] == "Max Mustermann (Trainer)"
    assert detail.data["external_url"] == "https://example.com/register"
    assert detail.data["venue_address"] == "Musterstraße 4, 81671 München"
    assert detail.data["district"] == "Maxvorstadt"
    assert detail.data["language"] == ["de", "en"]
    assert detail.data["provider_address"] == "Carl-Wery-Straße 28, 81739 München"
    assert detail.data["provider_email"] == "kontakt@example.com"
    assert detail.data["provider_social"] == "Instagram: @example"
