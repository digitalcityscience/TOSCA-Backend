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
