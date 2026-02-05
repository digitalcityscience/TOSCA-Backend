from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import CalendarEvent

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user():
    return User.objects.create_user(username="eventapiuser", password="password")


@pytest.fixture
def staff_user():
    return User.objects.create_user(
        username="staffuser", password="password", is_staff=True
    )


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="API Test Campaign", created_by=user)


@pytest.fixture
def future_event(user, campaign):
    """Create a future published event with location."""
    return CalendarEvent.objects.create(
        campaign=campaign,
        title="Future Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=2),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=CalendarEvent.Status.PUBLISHED,
    )


@pytest.fixture
def past_event(user, campaign):
    """Create a past published event."""
    return CalendarEvent.objects.create(
        campaign=campaign,
        title="Past Event",
        start_datetime=timezone.now() - timedelta(days=2),
        end_datetime=timezone.now() - timedelta(days=2, hours=-2),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=CalendarEvent.Status.PUBLISHED,
    )


@pytest.fixture
def event_without_location(user, campaign):
    """Create an event without location."""
    return CalendarEvent.objects.create(
        campaign=campaign,
        title="No Location Event",
        start_datetime=timezone.now() + timedelta(days=3),
        end_datetime=timezone.now() + timedelta(days=3, hours=1),
        location=None,
        organizer=user,
        status=CalendarEvent.Status.PUBLISHED,
    )


@pytest.fixture
def draft_event(user, campaign):
    """Create a draft event."""
    return CalendarEvent.objects.create(
        campaign=campaign,
        title="Draft Event",
        start_datetime=timezone.now() + timedelta(days=5),
        end_datetime=timezone.now() + timedelta(days=5, hours=1),
        organizer=user,
        status=CalendarEvent.Status.DRAFT,
    )


# =============================================================================
# Authentication Tests
# =============================================================================


@pytest.mark.django_db
def test_events_list_unauthenticated(api_client):
    """Test that unauthenticated users cannot list events."""
    response = api_client.get("/api/v1/events/")
    assert response.status_code == 403


# =============================================================================
# Calendar View Tests (List)
# =============================================================================


@pytest.mark.django_db
def test_events_list_returns_future_only_by_default(
    api_client, user, future_event, past_event
):
    """Test that list returns only future events by default."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/")
    assert response.status_code == 200

    titles = [e["title"] for e in response.data["results"]]
    assert "Future Event" in titles
    assert "Past Event" not in titles


@pytest.mark.django_db
def test_events_list_include_past(api_client, user, future_event, past_event):
    """Test that include_past=true returns past events."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/?include_past=true")
    assert response.status_code == 200

    titles = [e["title"] for e in response.data["results"]]
    assert "Future Event" in titles
    assert "Past Event" in titles


@pytest.mark.django_db
def test_events_list_includes_events_without_location(
    api_client, user, future_event, event_without_location
):
    """Test that calendar view includes events without location."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/")
    assert response.status_code == 200

    titles = [e["title"] for e in response.data["results"]]
    assert "Future Event" in titles
    assert "No Location Event" in titles


@pytest.mark.django_db
def test_events_list_filters_by_published_status(
    api_client, user, future_event, draft_event
):
    """Test that list returns only published events by default."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/")
    assert response.status_code == 200

    titles = [e["title"] for e in response.data["results"]]
    assert "Future Event" in titles
    assert "Draft Event" not in titles


@pytest.mark.django_db
def test_events_list_filter_by_campaign(api_client, user, campaign, future_event):
    """Test filtering by campaign_id."""
    # Create another campaign with event
    other_campaign = Campaign.objects.create(title="Other Campaign", created_by=user)
    CalendarEvent.objects.create(
        campaign=other_campaign,
        title="Other Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        organizer=user,
        status=CalendarEvent.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/?campaign_id={campaign.id}")
    assert response.status_code == 200

    titles = [e["title"] for e in response.data["results"]]
    assert "Future Event" in titles
    assert "Other Event" not in titles


# =============================================================================
# Map View Tests (BBox)
# =============================================================================


@pytest.mark.django_db
def test_events_bbox_returns_geojson(api_client, user, future_event):
    """Test that bbox filter returns GeoJSON format."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/?bbox=9.0,53.0,11.0,54.0")
    assert response.status_code == 200

    # GeoJSON FeatureCollection structure
    assert response.data["type"] == "FeatureCollection"
    assert "features" in response.data
    assert len(response.data["features"]) >= 1

    feature = response.data["features"][0]
    assert feature["type"] == "Feature"
    assert "geometry" in feature
    assert "properties" in feature
    assert feature["properties"]["title"] == "Future Event"


@pytest.mark.django_db
def test_events_bbox_excludes_events_without_location(
    api_client, user, future_event, event_without_location
):
    """Test that bbox filter excludes events without location."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/?bbox=9.0,53.0,11.0,54.0")
    assert response.status_code == 200

    titles = [f["properties"]["title"] for f in response.data["features"]]
    assert "Future Event" in titles
    assert "No Location Event" not in titles


@pytest.mark.django_db
def test_events_bbox_excludes_events_outside_bbox(api_client, user, campaign):
    """Test that bbox filter excludes events outside the bounding box."""
    # Create event in Hamburg
    CalendarEvent.objects.create(
        campaign=campaign,
        title="Hamburg Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=CalendarEvent.Status.PUBLISHED,
    )

    # Create event in Berlin (outside Hamburg bbox)
    CalendarEvent.objects.create(
        campaign=campaign,
        title="Berlin Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(13.4, 52.5, srid=4326),  # Berlin
        organizer=user,
        status=CalendarEvent.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    # Hamburg area bbox
    response = api_client.get("/api/v1/events/?bbox=9.5,53.0,10.5,54.0")
    assert response.status_code == 200

    titles = [f["properties"]["title"] for f in response.data["features"]]
    assert "Hamburg Event" in titles
    assert "Berlin Event" not in titles


@pytest.mark.django_db
def test_events_bbox_invalid_format(api_client, user):
    """Test that invalid bbox returns 400."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/events/?bbox=invalid")
    assert response.status_code == 400


# =============================================================================
# Map View Tests (Polygon - POST /within/)
# =============================================================================


@pytest.mark.django_db
def test_events_within_polygon(api_client, user, campaign):
    """Test POST /events/within/ with polygon filter."""
    # Create event inside polygon
    CalendarEvent.objects.create(
        campaign=campaign,
        title="Inside Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=CalendarEvent.Status.PUBLISHED,
    )

    # Create event outside polygon
    CalendarEvent.objects.create(
        campaign=campaign,
        title="Outside Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(5.0, 50.0, srid=4326),
        organizer=user,
        status=CalendarEvent.Status.PUBLISHED,
    )

    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/v1/events/within/",
        {
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[9.0, 53.0], [11.0, 53.0], [11.0, 54.0], [9.0, 54.0], [9.0, 53.0]]
                ],
            }
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.data["type"] == "FeatureCollection"

    titles = [f["properties"]["title"] for f in response.data["features"]]
    assert "Inside Event" in titles
    assert "Outside Event" not in titles


@pytest.mark.django_db
def test_events_within_rejects_non_polygon(api_client, user):
    """Test that within/ rejects Point geometry."""
    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/v1/events/within/",
        {
            "geometry": {
                "type": "Point",
                "coordinates": [10.0, 53.5],
            }
        },
        format="json",
    )
    assert response.status_code == 400
    assert "Polygon" in str(response.data)


@pytest.mark.django_db
def test_events_within_invalid_geojson(api_client, user):
    """Test that invalid GeoJSON returns 400."""
    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/v1/events/within/",
        {"geometry": {"type": "invalid", "coordinates": []}},
        format="json",
    )
    assert response.status_code == 400


# =============================================================================
# Detail View Tests
# =============================================================================


@pytest.mark.django_db
def test_events_retrieve_detail(api_client, user, future_event):
    """Test retrieving a single event returns full details."""
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/events/{future_event.id}/")
    assert response.status_code == 200

    assert response.data["id"] == str(future_event.id)
    assert response.data["title"] == "Future Event"
    assert "layers" in response.data
    assert "context" in response.data


# =============================================================================
# Create/Update/Delete Tests
# =============================================================================


@pytest.mark.django_db
def test_events_create(api_client, user, campaign):
    """Test creating a new event."""
    api_client.force_authenticate(user=user)
    data = {
        "title": "New Event",
        "description": "Test description",
        "campaign": str(campaign.id),
        "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
        "end_datetime": (timezone.now() + timedelta(days=1, hours=2)).isoformat(),
        "status": "draft",
    }
    response = api_client.post("/api/v1/events/", data, format="json")
    assert response.status_code == 201
    assert response.data["title"] == "New Event"
    assert response.data["organizer"] == user.id


@pytest.mark.django_db
def test_events_delete(api_client, user, future_event):
    """Test deleting an event."""
    api_client.force_authenticate(user=user)
    response = api_client.delete(f"/api/v1/events/{future_event.id}/")
    assert response.status_code == 204
    assert not CalendarEvent.objects.filter(id=future_event.id).exists()
