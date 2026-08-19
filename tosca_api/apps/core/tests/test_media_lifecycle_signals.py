"""Tests for the Campaign/GeoStory `post_save` -> media lifecycle sync signals
(epic-11 PR3).

``MediaLifecycleService`` is mocked -- these tests verify *when* the signal
fires (only on status/visibility change, never on unrelated edits, never on
create), not what the service does to storage objects (see
test_media_lifecycle.py for that).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import Event
from tosca_api.apps.geostories.models import GeoStory
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.django_db

_SERVICE_TARGET = "tosca_api.apps.core.media_lifecycle_signals.MediaLifecycleService"


@pytest.fixture
def org(db):
    return Organization.objects.create(slug="acme", name="Acme")


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="signal-owner")


def test_campaign_create_does_not_trigger_sync(org, user):
    with patch(_SERVICE_TARGET) as mock_service_cls:
        Campaign.objects.create(title="New Campaign", created_by=user, organization=org)

    mock_service_cls.return_value.sync_campaign_assets.assert_not_called()


def test_campaign_unrelated_field_update_does_not_trigger_sync(org, user):
    campaign = Campaign.objects.create(title="Camp", created_by=user, organization=org)

    with patch(_SERVICE_TARGET) as mock_service_cls:
        campaign.title = "Renamed"
        campaign.save()

    mock_service_cls.return_value.sync_campaign_assets.assert_not_called()


def test_campaign_status_change_triggers_sync(org, user):
    campaign = Campaign.objects.create(title="Camp", created_by=user, organization=org)

    with patch(_SERVICE_TARGET) as mock_service_cls:
        campaign.status = Campaign.Status.ARCHIVED
        campaign.save()

    mock_service_cls.return_value.sync_campaign_assets.assert_called_once_with(campaign)


def test_campaign_visibility_change_triggers_sync(org, user):
    campaign = Campaign.objects.create(title="Camp", created_by=user, organization=org)

    with patch(_SERVICE_TARGET) as mock_service_cls:
        campaign.visibility = Campaign.Visibility.PUBLIC
        campaign.save()

    mock_service_cls.return_value.sync_campaign_assets.assert_called_once_with(campaign)


def test_geostory_create_does_not_trigger_sync(org, user):
    campaign = Campaign.objects.create(title="Camp", created_by=user, organization=org)

    with patch(_SERVICE_TARGET) as mock_service_cls:
        GeoStory.objects.create(title="Story", campaign=campaign, author=user)

    mock_service_cls.return_value.sync_story_assets.assert_not_called()


def test_geostory_unrelated_field_update_does_not_trigger_sync(org, user):
    campaign = Campaign.objects.create(title="Camp", created_by=user, organization=org)
    story = GeoStory.objects.create(title="Story", campaign=campaign, author=user)

    with patch(_SERVICE_TARGET) as mock_service_cls:
        story.title = "Renamed Story"
        story.save()

    mock_service_cls.return_value.sync_story_assets.assert_not_called()


def test_geostory_status_change_triggers_sync(org, user):
    campaign = Campaign.objects.create(title="Camp", created_by=user, organization=org)
    story = GeoStory.objects.create(title="Story", campaign=campaign, author=user)

    with patch(_SERVICE_TARGET) as mock_service_cls:
        story.status = GeoStory.Status.ARCHIVED
        story.save()

    mock_service_cls.return_value.sync_story_assets.assert_called_once_with(story)


def _make_event(campaign, user, **overrides):
    from datetime import timedelta

    from django.contrib.gis.geos import Point
    from django.utils import timezone

    defaults = dict(
        campaign=campaign,
        title="Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )
    defaults.update(overrides)
    return Event.objects.create(**defaults)


def test_event_create_does_not_trigger_sync(org, user):
    campaign = Campaign.objects.create(title="Camp", created_by=user, organization=org)

    with patch(_SERVICE_TARGET) as mock_service_cls:
        _make_event(campaign, user)

    mock_service_cls.return_value.sync_event_assets.assert_not_called()


def test_event_unrelated_field_update_does_not_trigger_sync(org, user):
    campaign = Campaign.objects.create(title="Camp", created_by=user, organization=org)
    event = _make_event(campaign, user)

    with patch(_SERVICE_TARGET) as mock_service_cls:
        event.title = "Renamed Event"
        event.save()

    mock_service_cls.return_value.sync_event_assets.assert_not_called()


def test_event_status_change_triggers_sync(org, user):
    campaign = Campaign.objects.create(title="Camp", created_by=user, organization=org)
    event = _make_event(campaign, user, status=Event.Status.DRAFT)

    with patch(_SERVICE_TARGET) as mock_service_cls:
        event.status = Event.Status.PUBLISHED
        event.save()

    mock_service_cls.return_value.sync_event_assets.assert_called_once_with(event)
