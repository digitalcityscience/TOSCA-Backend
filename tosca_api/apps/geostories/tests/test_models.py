"""
Tests for GeoStory models.
"""

import pytest
from django.contrib.auth import get_user_model

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.geostories.models import GeoStory, GeoStoryLayer
from tosca_api.apps.layerrefs.models import LayerRef

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(username="testuser", password="password")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Test Campaign", created_by=user)


@pytest.mark.django_db
def test_geostory_creation(user, campaign):
    """Test standard GeoStory creation."""
    story = GeoStory.objects.create(
        title="My Story",
        summary="A nice summary",
        campaign=campaign,
        author=user,
    )
    assert story.id is not None
    assert story.status == GeoStory.Status.DRAFT
    assert story.title == "My Story"


@pytest.mark.django_db
def test_geostory_sanitization(user, campaign):
    """Test standard GeoStory sanitization."""
    story = GeoStory.objects.create(
        title="<h1>My Story</h1>",
        summary="<script>alert(1)</script>Summary",
        campaign=campaign,
        author=user,
    )
    assert story.title == "My Story"  # Stripped
    assert story.summary == "Summary"  # Stripped


@pytest.mark.django_db
def test_geostory_context_linking(user, campaign):
    """Test linking a GeoContext."""
    context = GeoContext.objects.create(
        content="<p>Rich</p>",
        content_type="rich",
        created_by=user,
    )
    story = GeoStory.objects.create(
        title="Context Story",
        campaign=campaign,
        author=user,
        context=context,
    )
    assert story.context == context
    assert context.geostory == story  # Reverse relation


@pytest.mark.django_db
def test_geostory_layers(user, campaign):
    """Test linking layers with order."""
    story = GeoStory.objects.create(
        title="Layer Story",
        campaign=campaign,
        author=user,
    )
    layer1 = LayerRef.objects.create(layer_name="workspace:roads")
    layer2 = LayerRef.objects.create(layer_name="workspace:buildings")

    # Add via through model
    GeoStoryLayer.objects.create(geostory=story, layer=layer1, display_order=2)
    GeoStoryLayer.objects.create(geostory=story, layer=layer2, display_order=1)

    assert story.layers.count() == 2
    
    # Check ordering
    refs = story.layers.all().order_by("geostorylayer__display_order")
    assert refs[0] == layer2  # order 1
    assert refs[1] == layer1  # order 2


@pytest.mark.django_db
def test_geostory_layer_auto_increment(user, campaign):
    """Test that layer display_order auto-increments."""
    story = GeoStory.objects.create(
        title="Ordered Story",
        campaign=campaign,
        author=user,
    )
    layer1 = LayerRef.objects.create(layer_name="workspace:layer1")
    layer2 = LayerRef.objects.create(layer_name="workspace:layer2")
    layer3 = LayerRef.objects.create(layer_name="workspace:layer3")

    # Creation without specifying display_order (defaults to 0)
    gsl1 = GeoStoryLayer.objects.create(geostory=story, layer=layer1)
    gsl2 = GeoStoryLayer.objects.create(geostory=story, layer=layer2)
    gsl3 = GeoStoryLayer.objects.create(geostory=story, layer=layer3)

    # First one might stay 0 or become 1 depending on logic (max is None -> 0? No, 0+1=1?)
    # Logic was: if max_order is not None: display_order = max_order + 1.
    # Initially max_order is None (empty). So defaults to 0. Correct.
    # Second one: max order is 0. So becomes 1.
    # Third one: max order is 1. So becomes 2.
    
    assert gsl1.display_order == 0
    assert gsl2.display_order == 1
    assert gsl3.display_order == 2
