from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import Event, EventSeries, EventType

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def organizer():
    return User.objects.create_user(username="phase5", password="pw")


@pytest.fixture
def campaign(organizer):
    return Campaign.objects.create(title="Phase 5", created_by=organizer)


@pytest.fixture
def event_type():
    return EventType.objects.create(code="phase5-core", label="Phase 5 Core")


@pytest.fixture
def series(organizer, campaign, event_type):
    return EventSeries.objects.create(
        campaign=campaign,
        event_type=event_type,
        created_by=organizer,
        name="Phase 5 Series",
        series_mode=EventSeries.SeriesMode.MANUAL_BATCH,
        start_date=timezone.now().date(),
        start_time=timezone.now().replace(hour=10, minute=0, microsecond=0).time(),
        end_time=timezone.now().replace(hour=11, minute=0, microsecond=0).time(),
        timezone="Europe/Berlin",
    )


def _make_occurrence(organizer, campaign, event_type, series, index: int, **overrides):
    base = timezone.now() + timedelta(days=index)
    defaults = {
        "campaign": campaign,
        "event_type": event_type,
        "title": f"Occurrence {index}",
        "summary": f"Occurrence {index} summary",
        "start_datetime": base,
        "end_datetime": base + timedelta(hours=1),
        "location": Point(10.0, 53.5, srid=4326),
        "organizer": organizer,
        "status": Event.Status.PUBLISHED,
        "visibility": Event.Visibility.PUBLIC,
        "provider_phone": "+49 89 12345",
        "series": series,
        "occurrence_index": index,
        "original_start_datetime": base,
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


def _make_standalone(organizer, campaign, **overrides):
    defaults = {
        "campaign": campaign,
        "title": "Standalone",
        "summary": "Standalone summary",
        "start_datetime": timezone.now() + timedelta(days=1),
        "end_datetime": timezone.now() + timedelta(days=1, hours=1),
        "location": Point(10.0, 53.5, srid=4326),
        "organizer": organizer,
        "status": Event.Status.PUBLISHED,
        "visibility": Event.Visibility.PUBLIC,
        "provider_phone": "+49 89 12345",
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


@pytest.mark.django_db
def test_detail_series_block_is_null_for_standalone_event(api_client, organizer, campaign):
    event = _make_standalone(organizer, campaign)
    response = api_client.get(f"/api/v1/events/{event.id}/")
    assert response.status_code == 200
    assert response.data["series"] is None


@pytest.mark.django_db
def test_detail_series_first_occurrence_has_no_previous(
    api_client, organizer, campaign, event_type, series
):
    first = _make_occurrence(organizer, campaign, event_type, series, 1)
    _make_occurrence(organizer, campaign, event_type, series, 2)

    response = api_client.get(f"/api/v1/events/{first.id}/")
    assert response.status_code == 200
    series_block = response.data["series"]
    assert series_block["occurrence_index"] == 1
    assert series_block["total_occurrences"] == 2
    assert series_block["previous_occurrence"] is None
    assert series_block["next_occurrence"]["id"]


@pytest.mark.django_db
def test_detail_series_last_occurrence_has_no_next(
    api_client, organizer, campaign, event_type, series
):
    _make_occurrence(organizer, campaign, event_type, series, 1)
    last = _make_occurrence(organizer, campaign, event_type, series, 2)

    response = api_client.get(f"/api/v1/events/{last.id}/")
    assert response.status_code == 200
    series_block = response.data["series"]
    assert series_block["occurrence_index"] == 2
    assert series_block["previous_occurrence"]["id"]
    assert series_block["next_occurrence"] is None


@pytest.mark.django_db
def test_detail_series_middle_occurrence_has_prev_and_next(
    api_client, organizer, campaign, event_type, series
):
    first = _make_occurrence(organizer, campaign, event_type, series, 1)
    middle = _make_occurrence(organizer, campaign, event_type, series, 2)
    last = _make_occurrence(organizer, campaign, event_type, series, 3)

    response = api_client.get(f"/api/v1/events/{middle.id}/")
    assert response.status_code == 200
    series_block = response.data["series"]
    assert series_block["total_occurrences"] == 3
    assert series_block["previous_occurrence"]["id"] == str(first.id)
    assert series_block["next_occurrence"]["id"] == str(last.id)


@pytest.mark.django_db
def test_detail_series_exception_still_receives_prev_next(
    api_client, organizer, campaign, event_type, series
):
    _make_occurrence(organizer, campaign, event_type, series, 1)
    exception = _make_occurrence(
        organizer,
        campaign,
        event_type,
        series,
        2,
        is_exception=True,
    )
    _make_occurrence(organizer, campaign, event_type, series, 3)

    response = api_client.get(f"/api/v1/events/{exception.id}/")
    assert response.status_code == 200
    series_block = response.data["series"]
    assert series_block["is_exception"] is True
    assert series_block["previous_occurrence"] is not None
    assert series_block["next_occurrence"] is not None


@pytest.mark.django_db
def test_list_includes_lightweight_series_fields(
    api_client, organizer, campaign, event_type, series
):
    _make_occurrence(organizer, campaign, event_type, series, 1)
    _make_occurrence(organizer, campaign, event_type, series, 2)
    _make_standalone(organizer, campaign)

    response = api_client.get(f"/api/v1/events/?campaign_id={campaign.id}")
    assert response.status_code == 200
    by_title = {item["title"]: item for item in response.data["results"]}

    occurrence_1 = by_title["Occurrence 1"]
    assert occurrence_1["series_id"] == str(series.id)
    assert occurrence_1["series_name"] == "Phase 5 Series"
    assert occurrence_1["occurrence_index"] == 1
    assert occurrence_1["total_occurrences"] == 2
    assert occurrence_1["is_exception"] is False

    standalone = by_title["Standalone"]
    assert standalone["series_id"] is None
    assert standalone["series_name"] == ""
    assert standalone["occurrence_index"] is None
    assert standalone["total_occurrences"] is None


@pytest.mark.django_db
def test_map_endpoint_includes_lightweight_series_fields(
    api_client, organizer, campaign, event_type, series
):
    _make_occurrence(organizer, campaign, event_type, series, 1)
    _make_occurrence(organizer, campaign, event_type, series, 2)

    response = api_client.get(f"/api/v1/events/map/?campaign_id={campaign.id}")
    assert response.status_code == 200
    features = response.data["spatial_events"]["features"]
    assert len(features) == 2
    sample = features[0]["properties"]
    assert sample["series_id"] == str(series.id)
    assert sample["series_name"] == "Phase 5 Series"
    assert sample["total_occurrences"] == 2
    assert sample["occurrence_index"] in {1, 2}
    assert sample["is_exception"] is False


@pytest.mark.django_db
def test_detail_series_preserves_original_start_datetime(
    api_client, organizer, campaign, event_type, series
):
    _make_occurrence(organizer, campaign, event_type, series, 1)
    original = timezone.now() + timedelta(days=2)
    moved = original + timedelta(hours=2)
    exception = _make_occurrence(
        organizer,
        campaign,
        event_type,
        series,
        2,
        start_datetime=moved,
        end_datetime=moved + timedelta(hours=1),
        is_exception=True,
        original_start_datetime=original,
    )

    response = api_client.get(f"/api/v1/events/{exception.id}/")
    series_block = response.data["series"]
    assert series_block["is_exception"] is True
    assert series_block["original_start_datetime"] is not None
