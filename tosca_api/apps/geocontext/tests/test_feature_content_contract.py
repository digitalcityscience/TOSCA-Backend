from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import Event, EventSeries, EventType
from tosca_api.apps.feedback.models import GeoFeedback
from tosca_api.apps.geostories.models import GeoStory

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="content-owner")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Owned content", created_by=user)


@pytest.fixture
def document():
    return {
        "blocks": [
            {"type": "header", "data": {"text": "Introduction", "level": 2}},
            {"type": "paragraph", "data": {"text": "Main content"}},
        ]
    }


def test_feature_models_own_canonical_content(user, campaign, document):
    story = GeoStory.objects.create(title="Story", campaign=campaign, author=user, content=document)
    feedback = GeoFeedback.objects.create(
        title="Feedback", campaign=campaign, created_by=user, content=document
    )

    assert story.content == document
    assert feedback.content == document
    assert not hasattr(story, "context")
    assert not hasattr(feedback, "context")


def test_event_content_inherits_and_can_be_explicitly_suppressed(user, campaign, document):
    event_type = EventType.objects.create(code="owned", label="Owned")
    now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
    series = EventSeries.objects.create(
        campaign=campaign,
        event_type=event_type,
        created_by=user,
        name="Series",
        default_content=document,
        series_mode=EventSeries.SeriesMode.MANUAL_BATCH,
        start_date=now.date(),
        start_time=now.time().replace(tzinfo=None),
        end_time=(now + timedelta(hours=1)).time().replace(tzinfo=None),
        timezone="Europe/Berlin",
    )
    event = Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        organizer=user,
        series=series,
        occurrence_index=1,
        title="Occurrence",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.test",
    )

    assert event.effective_content == document
    assert event.content_source == "series"

    event.content_override = {"blocks": []}
    event.save()
    assert event.effective_content == {"blocks": []}
    assert event.content_source == "event"


def test_detail_apis_expose_content_directly(user, campaign, document):
    story = GeoStory.objects.create(
        title="Story",
        campaign=campaign,
        author=user,
        status=GeoStory.Status.PUBLISHED,
        content=document,
    )
    feedback = GeoFeedback.objects.create(
        title="Feedback",
        campaign=campaign,
        created_by=user,
        status=GeoFeedback.Status.PUBLISHED,
        content=document,
    )
    event = Event.objects.create(
        campaign=campaign,
        organizer=user,
        title="Event",
        start_datetime=timezone.now(),
        end_datetime=timezone.now() + timedelta(hours=1),
        location_mode=Event.LocationMode.ONLINE,
        online_url="https://example.test",
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
        content_override=document,
    )
    client = APIClient()
    client.force_authenticate(user=user)

    story_response = client.get(f"/api/v1/stories/{story.id}/")
    event_response = client.get(f"/api/v1/events/{event.id}/")
    feedback_response = client.get(f"/api/v1/feedback/{feedback.id}/")

    assert story_response.data["content"] == document
    assert "context" not in story_response.data
    assert event_response.data["content"] == document
    assert event_response.data["content_source"] == "event"
    assert "context" not in event_response.data
    assert feedback_response.data["content"] == document
    assert "context" not in feedback_response.data
