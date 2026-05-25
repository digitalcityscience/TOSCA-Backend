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
    return User.objects.create_user(username="vis-organizer", password="pw")


@pytest.fixture
def regular_user():
    return User.objects.create_user(username="vis-regular", password="pw")


@pytest.fixture
def staff_user():
    return User.objects.create_user(
        username="vis-staff", password="pw", is_staff=True
    )


@pytest.fixture
def campaign(organizer):
    return Campaign.objects.create(title="Visibility Campaign", created_by=organizer)


@pytest.fixture
def published_public_event(organizer, campaign):
    return Event.objects.create(
        campaign=campaign,
        title="Published Public",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=organizer,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
    )


@pytest.fixture
def draft_event(organizer, campaign):
    return Event.objects.create(
        campaign=campaign,
        title="Draft Event",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=organizer,
        status=Event.Status.DRAFT,
        visibility=Event.Visibility.PUBLIC,
    )


@pytest.fixture
def private_event(organizer, campaign):
    return Event.objects.create(
        campaign=campaign,
        title="Private Event",
        start_datetime=timezone.now() + timedelta(days=3),
        end_datetime=timezone.now() + timedelta(days=3, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=organizer,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PRIVATE,
    )


@pytest.fixture
def cancelled_event(organizer, campaign):
    return Event.objects.create(
        campaign=campaign,
        title="Cancelled Event",
        start_datetime=timezone.now() + timedelta(days=4),
        end_datetime=timezone.now() + timedelta(days=4, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=organizer,
        status=Event.Status.CANCELLED,
        visibility=Event.Visibility.PUBLIC,
    )


@pytest.mark.django_db
def test_anon_list_returns_only_published_and_public(
    api_client, published_public_event, draft_event, private_event, cancelled_event
):
    response = api_client.get("/api/v1/events/")
    assert response.status_code == 200
    titles = [item["title"] for item in response.data["results"]]
    assert titles == ["Published Public"]


@pytest.mark.django_db
def test_anon_list_ignores_status_query_override(
    api_client, draft_event, published_public_event
):
    response = api_client.get("/api/v1/events/?status=draft")
    assert response.status_code == 200
    titles = [item["title"] for item in response.data["results"]]
    assert titles == ["Published Public"]


@pytest.mark.django_db
def test_anon_list_ignores_visibility_query_override(
    api_client, private_event, published_public_event
):
    response = api_client.get("/api/v1/events/?visibility=private")
    assert response.status_code == 200
    titles = [item["title"] for item in response.data["results"]]
    assert titles == ["Published Public"]


@pytest.mark.django_db
def test_anon_map_returns_only_published_and_public(
    api_client, published_public_event, draft_event, private_event
):
    response = api_client.get("/api/v1/events/map/")
    assert response.status_code == 200
    titles = [f["properties"]["title"] for f in response.data["spatial_events"]["features"]]
    assert titles == ["Published Public"]


@pytest.mark.django_db
def test_anon_within_returns_only_published_and_public(
    api_client, published_public_event, draft_event, private_event
):
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
    titles = [f["properties"]["title"] for f in response.data["features"]]
    assert titles == ["Published Public"]


@pytest.mark.django_db
def test_anon_retrieve_draft_returns_404(api_client, draft_event):
    response = api_client.get(f"/api/v1/events/{draft_event.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_anon_retrieve_private_returns_404(api_client, private_event):
    response = api_client.get(f"/api/v1/events/{private_event.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_anon_retrieve_published_public_returns_200(
    api_client, published_public_event
):
    response = api_client.get(f"/api/v1/events/{published_public_event.id}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_regular_user_cannot_retrieve_private_event(
    api_client, regular_user, private_event
):
    api_client.force_authenticate(user=regular_user)
    response = api_client.get(f"/api/v1/events/{private_event.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_regular_user_cannot_retrieve_draft_event(
    api_client, regular_user, draft_event
):
    api_client.force_authenticate(user=regular_user)
    response = api_client.get(f"/api/v1/events/{draft_event.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_staff_user_can_retrieve_draft_event(api_client, staff_user, draft_event):
    api_client.force_authenticate(user=staff_user)
    response = api_client.get(f"/api/v1/events/{draft_event.id}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_staff_user_can_retrieve_private_event(
    api_client, staff_user, private_event
):
    api_client.force_authenticate(user=staff_user)
    response = api_client.get(f"/api/v1/events/{private_event.id}/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_staff_user_list_includes_draft_and_private(
    api_client, staff_user, published_public_event, draft_event, private_event
):
    api_client.force_authenticate(user=staff_user)
    response = api_client.get("/api/v1/events/?status=draft&visibility=public")
    assert response.status_code == 200
    titles = {item["title"] for item in response.data["results"]}
    assert titles == {"Draft Event"}


@pytest.mark.django_db
def test_anon_post_returns_403(api_client, campaign):
    response = api_client.post(
        "/api/v1/events/",
        {
            "title": "x",
            "campaign": str(campaign.id),
            "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
            "end_datetime": (timezone.now() + timedelta(days=1, hours=1)).isoformat(),
        },
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_anon_patch_returns_403(api_client, published_public_event):
    response = api_client.patch(
        f"/api/v1/events/{published_public_event.id}/",
        {"title": "new"},
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_anon_delete_returns_403(api_client, published_public_event):
    response = api_client.delete(f"/api/v1/events/{published_public_event.id}/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_anon_cannot_retrieve_series_with_no_visible_occurrences(
    api_client, organizer, campaign, draft_event
):
    event_type = EventType.objects.create(code="vis-series", label="Vis Series")
    draft_event.event_type = event_type
    draft_event.save()
    series = EventSeries.objects.create(
        campaign=campaign,
        event_type=event_type,
        created_by=organizer,
        name="All-draft series",
        series_mode=EventSeries.SeriesMode.MANUAL_BATCH,
        start_date=timezone.now().date(),
        start_time=timezone.now().time().replace(microsecond=0),
        end_time=(timezone.now() + timedelta(hours=1))
        .time()
        .replace(microsecond=0),
        timezone="Europe/Berlin",
    )
    draft_event.series = series
    draft_event.occurrence_index = 1
    draft_event.save()

    response = api_client.get(f"/api/v1/event-series/{series.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_anon_can_retrieve_series_with_at_least_one_visible_occurrence(
    api_client, organizer, campaign, published_public_event
):
    event_type = EventType.objects.create(code="vis-series-2", label="Vis Series 2")
    published_public_event.event_type = event_type
    published_public_event.save()
    series = EventSeries.objects.create(
        campaign=campaign,
        event_type=event_type,
        created_by=organizer,
        name="Visible series",
        series_mode=EventSeries.SeriesMode.MANUAL_BATCH,
        start_date=timezone.now().date(),
        start_time=timezone.now().time().replace(microsecond=0),
        end_time=(timezone.now() + timedelta(hours=1))
        .time()
        .replace(microsecond=0),
        timezone="Europe/Berlin",
    )
    published_public_event.series = series
    published_public_event.occurrence_index = 1
    published_public_event.save()

    response = api_client.get(f"/api/v1/event-series/{series.id}/")
    assert response.status_code == 200
