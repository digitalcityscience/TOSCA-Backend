import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geostories.models import GeoStory

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user():
    return User.objects.create_user(username="testuser", password="password")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Test Campaign", created_by=user)


@pytest.fixture
def geostory(user, campaign):
    return GeoStory.objects.create(
        title="Existing Story",
        campaign=campaign,
        author=user,
    )


@pytest.mark.django_db
def test_geostory_list_unauthenticated(api_client):
    """Test that unauthenticated users cannot list geostories."""
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_geostory_list_authenticated(api_client, user, geostory):
    """Test that authenticated users can list geostories."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200
    assert len(response.data["results"]) >= 1


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
def test_geostory_filter_by_campaign(api_client, user, campaign):
    """Test filtering geostories by campaign_id."""
    # Create stories in the campaign
    GeoStory.objects.create(title="Story 1", campaign=campaign, author=user)
    GeoStory.objects.create(title="Story 2", campaign=campaign, author=user)
    
    # Create another campaign with a story
    other_campaign = Campaign.objects.create(title="Other Campaign", created_by=user)
    GeoStory.objects.create(title="Other Story", campaign=other_campaign, author=user)
    
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/stories/?campaign_id={campaign.id}")
    assert response.status_code == 200
    assert len(response.data["results"]) == 2


@pytest.mark.django_db
def test_geostory_retrieve(api_client, user, geostory):
    """Test retrieving a specific geostory."""
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200
    assert response.data["id"] == str(geostory.id)
    assert response.data["title"] == "Existing Story"


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
