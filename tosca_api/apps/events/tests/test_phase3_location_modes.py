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


@pytest.fixture
def user():
    return User.objects.create_user(username="phase3", password="pw")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Phase 3", created_by=user)


def _make_event(user, campaign, **overrides) -> Event:
    defaults = {
        "campaign": campaign,
        "title": "Mode event",
        "summary": "Mode event summary",
        "start_datetime": timezone.now() + timedelta(days=1),
        "end_datetime": timezone.now() + timedelta(days=1, hours=1),
        "organizer": user,
        "status": Event.Status.PUBLISHED,
        "visibility": Event.Visibility.PUBLIC,
        "provider_phone": "+49 89 12345",
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


@pytest.mark.django_db
def test_by_arrangement_event_accepted_without_geometry(user, campaign):
    event = _make_event(
        user,
        campaign,
        title="By Arrangement",
        location_mode=Event.LocationMode.BY_ARRANGEMENT,
        access_notes="Call to schedule.",
    )
    assert event.location is None
    assert event.location_mode == Event.LocationMode.BY_ARRANGEMENT


@pytest.mark.django_db
def test_home_visit_event_accepted_without_geometry(user, campaign):
    event = _make_event(
        user,
        campaign,
        title="Home Visit",
        location_mode=Event.LocationMode.HOME_VISIT,
        access_notes="We come to you within 5 km.",
    )
    assert event.location is None
    assert event.location_mode == Event.LocationMode.HOME_VISIT


@pytest.mark.django_db
def test_by_arrangement_event_rejects_geometry(user, campaign):
    candidate = Event(
        campaign=campaign,
        title="By Arrangement with geom",
        summary="x",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        organizer=user,
        location_mode=Event.LocationMode.BY_ARRANGEMENT,
        location=Point(10.0, 53.5, srid=4326),
    )
    with pytest.raises(ValidationError) as exc:
        candidate.clean()
    assert "location" in exc.value.message_dict


@pytest.mark.django_db
def test_home_visit_event_rejects_geometry(user, campaign):
    candidate = Event(
        campaign=campaign,
        title="Home Visit with geom",
        summary="x",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        organizer=user,
        location_mode=Event.LocationMode.HOME_VISIT,
        location=Point(10.0, 53.5, srid=4326),
    )
    with pytest.raises(ValidationError) as exc:
        candidate.clean()
    assert "location" in exc.value.message_dict


@pytest.mark.django_db
def test_by_arrangement_and_home_visit_appear_in_online_bucket(
    api_client, user, campaign
):
    _make_event(
        user,
        campaign,
        title="Physical",
        location=Point(10.0, 53.5, srid=4326),
        location_mode=Event.LocationMode.PHYSICAL,
    )
    _make_event(
        user,
        campaign,
        title="Online Stream",
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.org/live",
    )
    _make_event(
        user,
        campaign,
        title="By Arrangement",
        location_mode=Event.LocationMode.BY_ARRANGEMENT,
    )
    _make_event(
        user,
        campaign,
        title="Home Visit",
        location_mode=Event.LocationMode.HOME_VISIT,
    )

    response = api_client.get("/api/v1/events/map/")
    assert response.status_code == 200
    spatial_titles = [
        feature["properties"]["title"]
        for feature in response.data["spatial_events"]["features"]
    ]
    online_titles = [item["title"] for item in response.data["online_events"]]
    assert spatial_titles == ["Physical"]
    assert set(online_titles) == {"Online Stream", "By Arrangement", "Home Visit"}


@pytest.mark.django_db
def test_bbox_filter_preserves_by_arrangement_and_home_visit(
    api_client, user, campaign
):
    _make_event(
        user,
        campaign,
        title="Inside Physical",
        location=Point(10.0, 53.5, srid=4326),
        location_mode=Event.LocationMode.PHYSICAL,
    )
    _make_event(
        user,
        campaign,
        title="By Arrangement",
        location_mode=Event.LocationMode.BY_ARRANGEMENT,
    )
    _make_event(
        user,
        campaign,
        title="Home Visit",
        location_mode=Event.LocationMode.HOME_VISIT,
    )

    response = api_client.get("/api/v1/events/map/?bbox=9.5,53.0,10.5,54.0")
    assert response.status_code == 200
    spatial_titles = [
        feature["properties"]["title"]
        for feature in response.data["spatial_events"]["features"]
    ]
    online_titles = [item["title"] for item in response.data["online_events"]]
    assert spatial_titles == ["Inside Physical"]
    assert set(online_titles) == {"By Arrangement", "Home Visit"}


@pytest.mark.django_db
def test_list_endpoint_includes_new_modes(api_client, user, campaign):
    _make_event(
        user,
        campaign,
        title="By Arrangement",
        location_mode=Event.LocationMode.BY_ARRANGEMENT,
    )
    _make_event(
        user,
        campaign,
        title="Home Visit",
        location_mode=Event.LocationMode.HOME_VISIT,
    )
    response = api_client.get("/api/v1/events/")
    assert response.status_code == 200
    titles = {item["title"] for item in response.data["results"]}
    assert {"By Arrangement", "Home Visit"} <= titles


@pytest.mark.django_db
def test_within_polygon_preserves_by_arrangement_and_home_visit(
    api_client, user, campaign
):
    _make_event(
        user,
        campaign,
        title="Inside Physical",
        location=Point(10.0, 53.5, srid=4326),
        location_mode=Event.LocationMode.PHYSICAL,
    )
    _make_event(
        user,
        campaign,
        title="By Arrangement",
        location_mode=Event.LocationMode.BY_ARRANGEMENT,
    )
    payload = {
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [9.0, 53.0],
                    [11.0, 53.0],
                    [11.0, 54.0],
                    [9.0, 54.0],
                    [9.0, 53.0],
                ]
            ],
        }
    }
    response = api_client.post("/api/v1/events/within/", payload, format="json")
    assert response.status_code == 200
    titles = {f["properties"]["title"] for f in response.data["features"]}
    assert {"Inside Physical", "By Arrangement"} <= titles
