"""
Tests for CampaignAdmin's delete-confirmation warning.

Campaign deletion CASCADEs to Event/EventSeries/GeoStory/GeoFeedback with
no soft-delete (see the deliberate decision documented on Campaign's
docstring). These tests cover the warning banner added on top of Django's
default cascade-delete confirmation page.
"""
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import Event
from tosca_api.apps.feedback.models import GeoFeedback
from tosca_api.apps.geostories.models import GeoStory

User = get_user_model()


@pytest.fixture
def admin_client(db):
    client = Client()
    user = User.objects.create_user(
        username="campaign-admin",
        password="testpass123",
        is_staff=True,
        is_superuser=True,
    )
    client.force_login(user)
    return client, user


@pytest.mark.django_db
def test_delete_confirmation_shows_no_warning_for_empty_campaign(admin_client):
    client, user = admin_client
    campaign = Campaign.objects.create(title="Empty Campaign", created_by=user)

    response = client.get(
        reverse("admin:campaigns_campaign_delete", args=[campaign.pk])
    )

    assert response.status_code == 200
    messages = [str(m) for m in response.context["messages"]]
    assert not any("Confirming will permanently delete" in m for m in messages)


@pytest.mark.django_db
def test_delete_confirmation_warns_with_dependent_counts(admin_client):
    client, user = admin_client
    campaign = Campaign.objects.create(title="Busy Campaign", created_by=user)
    now = timezone.now()
    Event.objects.create(
        campaign=campaign,
        title="Workshop",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )
    GeoStory.objects.create(title="Story", campaign=campaign, author=user)
    GeoFeedback.objects.create(
        campaign=campaign,
        title="Feedback",
        rating_enabled=True,
        created_by=user,
    )

    response = client.get(
        reverse("admin:campaigns_campaign_delete", args=[campaign.pk])
    )

    assert response.status_code == 200
    messages = [str(m) for m in response.context["messages"]]
    warning = next(
        (m for m in messages if "Confirming will permanently delete" in m), None
    )
    assert warning is not None
    assert "1 events" in warning
    assert "1 geostories" in warning
    assert "1 feedbacks" in warning
    assert "0 event series" in warning


@pytest.mark.django_db
def test_confirmed_delete_cascades_dependents(admin_client):
    client, user = admin_client
    campaign = Campaign.objects.create(title="To Delete", created_by=user)
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="Workshop",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )
    story = GeoStory.objects.create(title="Story", campaign=campaign, author=user)

    response = client.post(
        reverse("admin:campaigns_campaign_delete", args=[campaign.pk]),
        {"post": "yes"},
    )

    assert response.status_code == 302
    assert not Campaign.objects.filter(pk=campaign.pk).exists()
    assert not Event.objects.filter(pk=event.pk).exists()
    assert not GeoStory.objects.filter(pk=story.pk).exists()
