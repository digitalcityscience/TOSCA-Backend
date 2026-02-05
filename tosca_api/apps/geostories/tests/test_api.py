import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.featurelinks.models import FeatureLink
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.geostories.models import GeoStory, GeoStoryLayer
from tosca_api.apps.layerrefs.models import LayerRef

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user():
    return User.objects.create_user(username="testuser", password="password")


@pytest.fixture
def staff_user():
    return User.objects.create_user(username="staffuser", password="password", is_staff=True)


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Test Campaign", created_by=user)


@pytest.fixture
def geocontext(user):
    return GeoContext.objects.create(
        content="This is the story content.",
        content_type=GeoContext.ContentType.SIMPLE,
        created_by=user,
    )


@pytest.fixture
def layer_ref():
    return LayerRef.objects.create(layer_name="workspace:test_layer")


@pytest.fixture
def geostory(user, campaign, geocontext):
    """Create a published story with context."""
    return GeoStory.objects.create(
        title="Existing Story",
        summary="Story summary",
        status=GeoStory.Status.PUBLISHED,
        campaign=campaign,
        author=user,
        context=geocontext,
    )


@pytest.fixture
def draft_story(user, campaign):
    """Create a draft story."""
    return GeoStory.objects.create(
        title="Draft Story",
        status=GeoStory.Status.DRAFT,
        campaign=campaign,
        author=user,
    )


# =============================================================================
# Authentication Tests
# =============================================================================


@pytest.mark.django_db
def test_geostory_list_unauthenticated(api_client):
    """Test that unauthenticated users cannot list geostories."""
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 403


# =============================================================================
# List View Tests (Task 1.6)
# =============================================================================


@pytest.mark.django_db
def test_geostory_list_published_only(api_client, user, geostory, draft_story):
    """Test that non-staff users only see published stories."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200
    
    results = response.data["results"]
    titles = [r["title"] for r in results]
    
    # Published story should be visible
    assert "Existing Story" in titles
    # Draft story should NOT be visible to non-staff
    assert "Draft Story" not in titles


@pytest.mark.django_db
def test_geostory_list_staff_sees_all(api_client, staff_user, geostory, draft_story):
    """Test that staff users can see all stories including drafts."""
    api_client.force_authenticate(user=staff_user)
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200
    
    results = response.data["results"]
    titles = [r["title"] for r in results]
    
    # Staff should see both
    assert "Existing Story" in titles
    assert "Draft Story" in titles


@pytest.mark.django_db
def test_geostory_list_payload_fields(api_client, user, geostory):
    """Test that list response has slim payload (required fields only)."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200
    
    story_data = response.data["results"][0]
    
    # Required fields in list
    assert "id" in story_data
    assert "title" in story_data
    assert "summary" in story_data
    assert "campaign" in story_data
    assert "created_at" in story_data
    
    # These should NOT be in list (detail only)
    assert "context" not in story_data
    assert "layers" not in story_data
    assert "feature_links" not in story_data


@pytest.mark.django_db
def test_geostory_filter_by_campaign(api_client, user, campaign):
    """Test filtering geostories by campaign_id."""
    # Create published stories in the campaign
    GeoStory.objects.create(
        title="Story 1", campaign=campaign, author=user, status=GeoStory.Status.PUBLISHED
    )
    GeoStory.objects.create(
        title="Story 2", campaign=campaign, author=user, status=GeoStory.Status.PUBLISHED
    )

    # Create another campaign with a story
    other_campaign = Campaign.objects.create(title="Other Campaign", created_by=user)
    GeoStory.objects.create(
        title="Other Story", campaign=other_campaign, author=user, status=GeoStory.Status.PUBLISHED
    )

    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/stories/?campaign_id={campaign.id}")
    assert response.status_code == 200
    assert len(response.data["results"]) == 2


# =============================================================================
# Detail View Tests (Task 1.6)
# =============================================================================


@pytest.mark.django_db
def test_geostory_detail_has_nested_context(api_client, user, geostory):
    """Test that detail view returns nested context object (not just UUID)."""
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200
    
    context = response.data["context"]
    assert context is not None
    assert "content" in context
    assert context["content"] == "This is the story content."
    assert "content_type" in context
    assert context["content_type"] == "simple"


@pytest.mark.django_db
def test_geostory_detail_has_layers(api_client, user, geostory, layer_ref):
    """Test that detail view returns layers with display_order."""
    # Add layer to story
    GeoStoryLayer.objects.create(geostory=geostory, layer=layer_ref, display_order=1)
    
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200
    
    layers = response.data["layers"]
    assert len(layers) == 1
    assert layers[0]["layer_name"] == "workspace:test_layer"
    assert layers[0]["display_order"] == 1


@pytest.mark.django_db
def test_geostory_detail_has_feature_links(api_client, user, geostory, campaign):
    """Test that detail view returns outgoing feature links."""
    # Create another story to link to
    target_story = GeoStory.objects.create(
        title="Target Story",
        campaign=campaign,
        author=user,
        status=GeoStory.Status.PUBLISHED,
    )
    
    # Create a feature link
    FeatureLink.objects.create(
        campaign=campaign,
        source_object=geostory,
        target_object=target_story,
        link_type=FeatureLink.LinkType.READ_MORE,
        created_by=user,
    )
    
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200
    
    links = response.data["feature_links"]
    assert len(links) == 1
    assert links[0]["target_object_id"] == str(target_story.id)
    assert links[0]["link_type"] == "read_more"
    assert links[0]["target_type"] == "geostory"


@pytest.mark.django_db
def test_geostory_detail_full_payload(api_client, user, geostory):
    """Test that detail response has all required fields."""
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200
    
    data = response.data
    
    # All required fields
    assert "id" in data
    assert "title" in data
    assert "summary" in data
    assert "status" in data
    assert "campaign" in data
    assert "context" in data
    assert "layers" in data
    assert "feature_links" in data
    assert "created_at" in data
    assert "updated_at" in data


# =============================================================================
# Create/Update/Delete Tests (existing functionality)
# =============================================================================


@pytest.mark.django_db
def test_geostory_create(api_client, user, campaign):
    """Test creating a new geostory."""
    api_client.force_authenticate(user=user)
    data = {
        "title": "New Story",
        "summary": "A test story",
        "status": "draft",
        "campaign": str(campaign.id),
    }
    response = api_client.post("/api/v1/stories/", data)
    assert response.status_code == 201
    assert response.data["title"] == "New Story"
    assert response.data["author"] == user.id


@pytest.mark.django_db
def test_geostory_create_requires_title(api_client, user, campaign):
    """Test that title is required."""
    api_client.force_authenticate(user=user)
    data = {
        "campaign": str(campaign.id),
    }
    response = api_client.post("/api/v1/stories/", data)
    assert response.status_code == 400
    assert "title" in response.data


@pytest.mark.django_db
def test_geostory_update(api_client, user, geostory):
    """Test updating a geostory."""
    api_client.force_authenticate(user=user)
    response = api_client.patch(
        f"/api/v1/stories/{geostory.id}/",
        {"title": "Updated Title"},
    )
    assert response.status_code == 200
    assert response.data["title"] == "Updated Title"


@pytest.mark.django_db
def test_geostory_delete(api_client, user, geostory):
    """Test deleting a geostory."""
    api_client.force_authenticate(user=user)
    response = api_client.delete(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 204
    assert not GeoStory.objects.filter(id=geostory.id).exists()
