from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.forms import get_taxonomy_dimensions_for_source
from tosca_api.apps.events.models import (
    Event,
    EventTerm,
    EventType,
    TaxonomyDimension,
    TaxonomyTerm,
)
from tosca_api.apps.events.services import resolve_taxonomy_assignments

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


def _org_token(*roles, org="dcs"):
    """Keycloak-shaped token for org-scoped writes (epic-11 PR1 SS3.3)."""
    return {"realm_access": {"roles": list(roles)}, "default_organization": org}


@pytest.fixture
def user():
    return User.objects.create_user(username="phase7", password="pw")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Phase 7", created_by=user)


@pytest.fixture
def general_event_type():
    return EventType.objects.create(
        code="phase7-general",
        label="General",
        profile_mode=EventType.ProfileMode.CORE,
    )


@pytest.fixture
def ph_event_type():
    return EventType.objects.create(
        code="phase7-ph",
        label="Public Health",
        profile_mode=EventType.ProfileMode.EXTENSION,
        profile_key="public_health",
    )


@pytest.fixture
def unscoped_dimension():
    dim = TaxonomyDimension.objects.create(
        code="topic-unscoped",
        label="Topic",
        selection_mode=TaxonomyDimension.SelectionMode.MULTIPLE,
    )
    TaxonomyTerm.objects.create(dimension=dim, code="climate", label="Climate")
    return dim


@pytest.fixture
def ph_scoped_dimension():
    dim = TaxonomyDimension.objects.create(
        code="format-ph",
        label="Format (PH)",
        selection_mode=TaxonomyDimension.SelectionMode.SINGLE,
        profile_key="public_health",
    )
    TaxonomyTerm.objects.create(dimension=dim, code="kurs", label="Kurs")
    return dim


def _make_event(user, campaign, event_type, **overrides):
    defaults = {
        "campaign": campaign,
        "event_type": event_type,
        "title": "Phase 7 event",
        "summary": "Phase 7 summary",
        "start_datetime": timezone.now() + timedelta(days=1),
        "end_datetime": timezone.now() + timedelta(days=1, hours=1),
        "location": Point(10.0, 53.5, srid=4326),
        "organizer": user,
        "status": Event.Status.PUBLISHED,
        "visibility": Event.Visibility.PUBLIC,
        "provider_phone": "+49 89 12345",
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


@pytest.mark.django_db
def test_event_term_rejects_ph_dimension_on_general_event(
    user, campaign, general_event_type, ph_scoped_dimension
):
    event = _make_event(user, campaign, general_event_type)
    term = ph_scoped_dimension.terms.first()
    candidate = EventTerm(event=event, term=term)
    with pytest.raises(ValidationError) as exc:
        candidate.clean()
    assert "term" in exc.value.message_dict


@pytest.mark.django_db
def test_event_term_accepts_ph_dimension_on_ph_event(
    user, campaign, ph_event_type, ph_scoped_dimension
):
    event = _make_event(user, campaign, ph_event_type)
    term = ph_scoped_dimension.terms.first()
    EventTerm.objects.create(event=event, term=term)
    assert EventTerm.objects.filter(event=event, term=term).exists()


@pytest.mark.django_db
def test_event_term_accepts_unscoped_dimension_on_general_event(
    user, campaign, general_event_type, unscoped_dimension
):
    event = _make_event(user, campaign, general_event_type)
    term = unscoped_dimension.terms.first()
    EventTerm.objects.create(event=event, term=term)
    assert EventTerm.objects.filter(event=event, term=term).exists()


@pytest.mark.django_db
def test_resolve_taxonomy_assignments_rejects_mismatched_profile(
    ph_scoped_dimension,
):
    term = ph_scoped_dimension.terms.first()
    with pytest.raises(ValidationError) as exc:
        resolve_taxonomy_assignments(
            [{"dimension_id": ph_scoped_dimension.id, "term_ids": [term.id]}],
            event_profile_key="",
        )
    assert "taxonomy_assignments" in exc.value.message_dict


@pytest.mark.django_db
def test_resolve_taxonomy_assignments_accepts_matched_profile(
    ph_scoped_dimension,
):
    term = ph_scoped_dimension.terms.first()
    resolved = resolve_taxonomy_assignments(
        [{"dimension_id": ph_scoped_dimension.id, "term_ids": [term.id]}],
        event_profile_key="public_health",
    )
    assert [t.id for t in resolved] == [term.id]


@pytest.mark.django_db
def test_event_detail_exposes_profile_key_on_taxonomy_assignment(
    api_client, user, campaign, ph_event_type, ph_scoped_dimension
):
    event = _make_event(user, campaign, ph_event_type)
    term = ph_scoped_dimension.terms.first()
    EventTerm.objects.create(event=event, term=term)

    response = api_client.get(f"/api/v1/events/{event.id}/")
    assert response.status_code == 200
    assignments = response.data["taxonomy_assignments"]
    assert assignments[0]["profile_key"] == "public_health"


@pytest.mark.django_db
def test_event_api_rejects_ph_dimension_on_general_event_via_write(
    api_client, user, campaign, general_event_type, ph_scoped_dimension
):
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    term = ph_scoped_dimension.terms.first()
    payload = {
        "title": "Mismatch",
        "summary": "Mismatch",
        "campaign": str(campaign.id),
        "event_type": str(general_event_type.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=1)).isoformat(),
        "location_mode": "physical",
        "location": {"type": "Point", "coordinates": [10.0, 53.5]},
        "status": "published",
        "provider_phone": "+49 89 12345",
        "taxonomy_assignments": [
            {"dimension_id": str(ph_scoped_dimension.id), "term_ids": [str(term.id)]}
        ],
    }
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 400
    assert "taxonomy_assignments" in response.data


@pytest.mark.django_db
def test_event_api_accepts_ph_dimension_on_ph_event_via_write(
    api_client, user, campaign, ph_event_type, ph_scoped_dimension
):
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    term = ph_scoped_dimension.terms.first()
    payload = {
        "title": "Match",
        "summary": "Match",
        "campaign": str(campaign.id),
        "event_type": str(ph_event_type.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=1)).isoformat(),
        "location_mode": "physical",
        "location": {"type": "Point", "coordinates": [10.0, 53.5]},
        "status": "published",
        "provider_phone": "+49 89 12345",
        "taxonomy_assignments": [
            {"dimension_id": str(ph_scoped_dimension.id), "term_ids": [str(term.id)]}
        ],
    }
    response = api_client.post("/api/v1/events/", payload, format="json")
    assert response.status_code == 201, response.data


@pytest.mark.django_db
def test_admin_dimension_picker_hides_ph_dimensions_for_general_event(
    user, campaign, general_event_type, unscoped_dimension, ph_scoped_dimension
):
    event = _make_event(user, campaign, general_event_type)
    codes = {dim.code for dim in get_taxonomy_dimensions_for_source(event)}
    assert "topic-unscoped" in codes
    assert "format-ph" not in codes


@pytest.mark.django_db
def test_admin_dimension_picker_shows_ph_dimensions_for_ph_event(
    user, campaign, ph_event_type, unscoped_dimension, ph_scoped_dimension
):
    event = _make_event(user, campaign, ph_event_type)
    codes = {dim.code for dim in get_taxonomy_dimensions_for_source(event)}
    assert {"topic-unscoped", "format-ph"} <= codes


@pytest.mark.django_db
def test_admin_dimension_picker_keeps_existing_assignment_after_event_type_change(
    user, campaign, ph_event_type, general_event_type, ph_scoped_dimension
):
    event = _make_event(user, campaign, ph_event_type)
    term = ph_scoped_dimension.terms.first()
    EventTerm.objects.create(event=event, term=term)

    # Simulate the admin viewing the event after the event_type was switched away
    # from public_health. The PH dimension stays visible so the admin can clear it.
    event.event_type = general_event_type
    codes = {dim.code for dim in get_taxonomy_dimensions_for_source(event)}
    assert "format-ph" in codes
