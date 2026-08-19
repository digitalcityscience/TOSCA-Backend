import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tosca_api.apps.authentication.role_sync import AuthClaims
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
    # conftest.py's default-org save wrapper attaches this to the seeded
    # 'dcs' org, matching the reader/writer tokens below.
    return Campaign.objects.create(title="Existing Campaign", created_by=user)


def _token(*roles):
    return {
        "realm_access": {"roles": list(roles)},
        "default_organization": "dcs",
    }


def _authenticate(api_client, user, *roles, org_slug="dcs"):
    """Authenticate ``user`` for both gate C (``request.auth`` token, read by
    ``OrgScopedPermission``/``org_scoped_queryset``) and gate A
    (``user._auth_claims``, read by ``has_perm()`` -> ``OrgRolePermissionBackend``
    via ``ViewGatedModelPermissions``/``DjangoModelPermissions``).

    ``APIClient.force_authenticate`` bypasses ``KeycloakTokenAuthentication``
    entirely, so it never attaches ``_auth_claims`` itself (security tickets
    ticket 08) -- it must be set on the same ``user`` object passed in here,
    since DRF's ``force_authenticate`` uses that exact instance as
    ``request.user``.
    """
    level = roles[0].rsplit("_", 1)[-1] if roles else None
    if level:
        user._auth_claims = AuthClaims(
            org_roles={org_slug: level}, default_org=org_slug, authoritative=True
        )
    api_client.force_authenticate(user=user, token=_token(*roles))


@pytest.mark.django_db
def test_campaign_list_unauthenticated(api_client):
    """Test that unauthenticated users cannot list campaigns."""
    response = api_client.get("/api/v1/campaigns/")
    assert response.status_code == 403  # or 401 depending on DRF setting


@pytest.mark.django_db
def test_campaign_list_authenticated(api_client, user, campaign):
    """Test that authenticated org-reader users can list campaigns."""
    _authenticate(api_client, user, "ROLE_DCS_READER")
    response = api_client.get("/api/v1/campaigns/")
    assert response.status_code == 200
    assert len(response.data["results"]) >= 1
    assert response.data["results"][0]["title"] == "Existing Campaign"


@pytest.mark.django_db
def test_campaign_create_authenticated(api_client, user):
    """Test that authenticated org-writer users can create campaigns."""
    _authenticate(api_client, user, "ROLE_DCS_WRITER")
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
    """Test retrieving a specific campaign as an org-reader."""
    _authenticate(api_client, user, "ROLE_DCS_READER")
    response = api_client.get(f"/api/v1/campaigns/{campaign.id}/")
    assert response.status_code == 200
    assert response.data["id"] == str(campaign.id)
