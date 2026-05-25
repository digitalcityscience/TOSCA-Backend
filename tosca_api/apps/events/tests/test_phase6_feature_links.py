from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import Event
from tosca_api.apps.featurelinks.models import FeatureLink
from tosca_api.apps.feedback.models import GeoFeedback
from tosca_api.apps.geostories.models import GeoStory

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user():
    return User.objects.create_user(username="phase6", password="pw")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Phase 6", created_by=user)


@pytest.fixture
def source_event(user, campaign):
    return Event.objects.create(
        campaign=campaign,
        title="Source Event",
        summary="Source",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
        provider_phone="+49 89 12345",
    )


@pytest.mark.django_db
def test_event_detail_returns_empty_feature_links_when_none(api_client, source_event):
    response = api_client.get(f"/api/v1/events/{source_event.id}/")
    assert response.status_code == 200
    assert response.data["feature_links"] == []


@pytest.mark.django_db
def test_event_detail_returns_outgoing_feature_links_to_mixed_targets(
    api_client, user, campaign, source_event
):
    story = GeoStory.objects.create(
        title="Linked Story",
        campaign=campaign,
        author=user,
        status=GeoStory.Status.PUBLISHED,
    )
    sibling_event = Event.objects.create(
        campaign=campaign,
        title="Linked Event",
        summary="Sibling",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
        provider_phone="+49 89 12345",
    )
    feedback = GeoFeedback.objects.create(
        campaign=campaign,
        title="Linked feedback",
        description="Linked feedback",
        created_by=user,
    )

    event_ct = ContentType.objects.get_for_model(Event)
    story_ct = ContentType.objects.get_for_model(GeoStory)
    feedback_ct = ContentType.objects.get_for_model(GeoFeedback)

    FeatureLink.objects.create(
        campaign=campaign,
        source_content_type=event_ct,
        source_object_id=source_event.id,
        target_content_type=story_ct,
        target_object_id=story.id,
        link_type=FeatureLink.LinkType.READ_MORE,
        created_by=user,
    )
    FeatureLink.objects.create(
        campaign=campaign,
        source_content_type=event_ct,
        source_object_id=source_event.id,
        target_content_type=event_ct,
        target_object_id=sibling_event.id,
        link_type=FeatureLink.LinkType.DIRECT,
        created_by=user,
    )
    FeatureLink.objects.create(
        campaign=campaign,
        source_content_type=event_ct,
        source_object_id=source_event.id,
        target_content_type=feedback_ct,
        target_object_id=feedback.id,
        link_type=FeatureLink.LinkType.ACTION,
        created_by=user,
    )

    response = api_client.get(f"/api/v1/events/{source_event.id}/")
    assert response.status_code == 200
    links = response.data["feature_links"]
    assert len(links) == 3

    by_target_type = {link["target_type"]: link for link in links}
    assert set(by_target_type) == {"geostory", "event", "geofeedback"}

    assert by_target_type["geostory"]["target_object_id"] == str(story.id)
    assert by_target_type["geostory"]["link_type"] == "read_more"

    assert by_target_type["event"]["target_object_id"] == str(sibling_event.id)
    assert by_target_type["event"]["link_type"] == "direct"

    assert by_target_type["geofeedback"]["target_object_id"] == str(feedback.id)
    assert by_target_type["geofeedback"]["link_type"] == "action"


@pytest.mark.django_db
def test_event_detail_only_returns_outgoing_links_not_incoming(
    api_client, user, campaign, source_event
):
    """FeatureLinks where the event is the *target* must not surface in feature_links."""
    other_event = Event.objects.create(
        campaign=campaign,
        title="Other Event",
        summary="Other",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        visibility=Event.Visibility.PUBLIC,
        provider_phone="+49 89 12345",
    )
    event_ct = ContentType.objects.get_for_model(Event)
    FeatureLink.objects.create(
        campaign=campaign,
        source_content_type=event_ct,
        source_object_id=other_event.id,
        target_content_type=event_ct,
        target_object_id=source_event.id,
        link_type=FeatureLink.LinkType.DIRECT,
        created_by=user,
    )

    response = api_client.get(f"/api/v1/events/{source_event.id}/")
    assert response.status_code == 200
    assert response.data["feature_links"] == []
