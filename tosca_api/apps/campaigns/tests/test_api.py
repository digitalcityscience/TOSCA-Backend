import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user():
    return User.objects.create_user(username="apiuser", password="password")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Existing Campaign", created_by=user)


@pytest.mark.django_db
def test_campaign_list_unauthenticated(api_client):
    """Test that unauthenticated users cannot list campaigns."""
    response = api_client.get("/api/v1/campaigns/")
    assert response.status_code == 403  # or 401 depending on DRF setting


@pytest.mark.django_db
def test_campaign_list_authenticated(api_client, user, campaign):
    """Test that authenticated users can list campaigns."""
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/campaigns/")
    assert response.status_code == 200
    assert len(response.data["results"]) >= 1
    assert response.data["results"][0]["title"] == "Existing Campaign"


@pytest.mark.django_db
def test_campaign_create_authenticated(api_client, user):
    """Test that authenticated users can create campaigns."""
    api_client.force_authenticate(user=user)
    data = {
        "title": "New API Campaign",
        "summary": "Created via API",
        "status": "draft",
        "visibility": "private",
    }
    response = api_client.post("/api/v1/campaigns/", data)
    assert response.status_code == 201
    assert response.data["title"] == "New API Campaign"
    assert response.data["created_by"] == user.id


@pytest.mark.django_db
def test_campaign_retrieve(api_client, user, campaign):
    """Test retrieving a specific campaign."""
    api_client.force_authenticate(user=user)
    response = api_client.get(f"/api/v1/campaigns/{campaign.id}/")
    assert response.status_code == 200
    assert response.data["id"] == str(campaign.id)
