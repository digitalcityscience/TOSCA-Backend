"""
Tests for GeoContext model.
"""

import pytest
from django.contrib.auth import get_user_model

from tosca_api.apps.geocontext.models import GeoContext

User = get_user_model()


@pytest.fixture
def test_user(db):
    """Create a test user for geocontext ownership."""
    return User.objects.create_user(
        username="testuser_geocontext",
        password="testpass123",
    )


@pytest.mark.django_db
def test_geocontext_creation(test_user):
    """Test that a geocontext can be created with minimal fields."""
    ctx = GeoContext.objects.create(
        content="Sample content",
        created_by=test_user,
    )
    assert ctx.id is not None
    assert ctx.content_type == GeoContext.ContentType.SIMPLE
    assert ctx.content == "Sample content"
    assert ctx.created_at is not None


@pytest.mark.django_db
def test_geocontext_str(test_user):
    """Test the string representation of a geocontext."""
    ctx = GeoContext.objects.create(
        content="This is some sample content for testing",
        created_by=test_user,
    )
    assert "This is some sample" in str(ctx)


@pytest.mark.django_db
def test_geocontext_empty_content(test_user):
    """Test geocontext with empty content."""
    ctx = GeoContext.objects.create(
        content="",
        created_by=test_user,
    )
    assert ctx.content == ""
    assert "(empty)" in str(ctx)


@pytest.mark.django_db
def test_geocontext_rich_content_type(test_user):
    """Test geocontext with rich HTML content type."""
    ctx = GeoContext.objects.create(
        content="<h1>Title</h1><p>Body text</p>",
        content_type=GeoContext.ContentType.RICH,
        created_by=test_user,
    )
    assert ctx.content_type == "rich"
