"""
Tests for Campaign model.

These tests use pytest-django's database fixtures which automatically
handle transactions and rollback after each test.
"""

import pytest
from django.contrib.auth import get_user_model

from tosca_api.apps.campaigns.models import Campaign

User = get_user_model()


@pytest.fixture
def test_user(db):
    """Create a test user for campaign ownership."""
    return User.objects.create_user(
        username="testuser_campaigns",
        password="testpass123",
    )


@pytest.mark.django_db
def test_campaign_creation(test_user):
    """Test that a campaign can be created with minimal fields."""
    campaign = Campaign.objects.create(
        title="Test Campaign",
        created_by=test_user,
    )
    assert campaign.id is not None
    assert campaign.status == Campaign.Status.DRAFT
    assert campaign.visibility == Campaign.Visibility.PRIVATE
    assert campaign.summary == ""
    assert campaign.created_at is not None
    assert campaign.updated_at is not None


@pytest.mark.django_db
def test_campaign_str(test_user):
    """Test the string representation of a campaign."""
    campaign = Campaign.objects.create(
        title="My Campaign",
        created_by=test_user,
    )
    assert str(campaign) == "My Campaign"


@pytest.mark.django_db
def test_campaign_status_choices(test_user):
    """Test that status choices are applied correctly."""
    campaign = Campaign.objects.create(
        title="Active Campaign",
        status=Campaign.Status.ACTIVE,
        created_by=test_user,
    )
    assert campaign.status == "active"
    
    campaign.status = Campaign.Status.ARCHIVED
    campaign.save()
    campaign.refresh_from_db()
    assert campaign.status == "archived"


@pytest.mark.django_db
def test_campaign_visibility_choices(test_user):
    """Test that visibility choices are applied correctly."""
    campaign = Campaign.objects.create(
        title="Public Campaign",
        visibility=Campaign.Visibility.PUBLIC,
        created_by=test_user,
    )
    assert campaign.visibility == "public"


@pytest.mark.django_db
def test_campaign_sanitization(test_user):
    """Test that campaign fields are sanitized."""
    unsafe_title = "My <b>Campaign</b><script>alert(1)</script>"
    unsafe_summary = "A summary with <script>bad</script> tags."
    
    campaign = Campaign.objects.create(
        title=unsafe_title,
        summary=unsafe_summary,
        created_by=test_user,
    )
    
    # Simple sanitization strips ALL tags
    assert "<script>" not in campaign.title
    assert "<b>" not in campaign.title
    assert campaign.title == "My Campaign"
    
    assert "<script>" not in campaign.summary
    assert campaign.summary == "A summary with  tags."


@pytest.mark.django_db
def test_usage_summary_zero_for_empty_campaign(test_user):
    campaign = Campaign.objects.create(title="Empty Campaign", created_by=test_user)

    assert campaign.usage_summary() == {
        "events": 0,
        "event_series": 0,
        "geostories": 0,
        "feedbacks": 0,
    }


@pytest.mark.django_db
def test_usage_summary_counts_dependents(test_user):
    from datetime import timedelta

    from django.contrib.gis.geos import Point
    from django.utils import timezone

    from tosca_api.apps.events.models import Event
    from tosca_api.apps.feedback.models import GeoFeedback
    from tosca_api.apps.geostories.models import GeoStory

    campaign = Campaign.objects.create(title="Busy Campaign", created_by=test_user)
    now = timezone.now()
    Event.objects.create(
        campaign=campaign,
        title="Workshop",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=test_user,
    )
    GeoStory.objects.create(title="Story", campaign=campaign, author=test_user)
    GeoFeedback.objects.create(
        campaign=campaign,
        title="Feedback",
        rating_enabled=True,
        created_by=test_user,
    )

    usage = campaign.usage_summary()
    assert usage["events"] == 1
    assert usage["geostories"] == 1
    assert usage["feedbacks"] == 1
    assert usage["event_series"] == 0
