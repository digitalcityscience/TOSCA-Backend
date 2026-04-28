import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient

from formbuilder.models import CustomForm
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.feedback.models import GeoFeedback

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_user():
    return User.objects.create_superuser(username="admin", password="password")


@pytest.fixture
def regular_user():
    return User.objects.create_user(username="user", password="password")


@pytest.fixture
def admin_client(admin_user):
    client = APIClient()
    client.force_authenticate(user=admin_user)
    return client


@pytest.fixture
def user_client(regular_user):
    client = APIClient()
    client.force_authenticate(user=regular_user)
    return client


@pytest.fixture
def campaign(admin_user):
    return Campaign.objects.create(title="Test Campaign", created_by=admin_user)


@pytest.fixture
def custom_form():
    return CustomForm.objects.create(
        name="Test Form",
        slug="test-form",
        status=CustomForm.FormStatus.PUBLISHED,
    )


@pytest.fixture
def feedback(admin_user, campaign, custom_form):
    """Fully enabled public/published feedback."""
    return GeoFeedback.objects.create(
        campaign=campaign,
        title="Full Feedback",
        created_by=admin_user,
        custom_form=custom_form,
        rating_enabled=True,
        form_enabled=True,
        allow_drawings=True,
        status=GeoFeedback.Status.PUBLISHED,
        visibility=GeoFeedback.Visibility.PUBLIC,
    )


@pytest.fixture
def feedback_no_drawings(admin_user, campaign):
    """Published but no drawings allowed."""
    return GeoFeedback.objects.create(
        campaign=campaign,
        title="No Drawings Feedback",
        created_by=admin_user,
        rating_enabled=True,
        form_enabled=False,
        allow_drawings=False,
        status=GeoFeedback.Status.PUBLISHED,
        visibility=GeoFeedback.Visibility.PUBLIC,
    )

@pytest.fixture
def feedback_draft_private(admin_user, campaign):
    """Draft and private feedback."""
    return GeoFeedback.objects.create(
        campaign=campaign,
        title="Draft Feedback",
        created_by=admin_user,
        rating_enabled=True,
        form_enabled=False,
        status=GeoFeedback.Status.DRAFT,
        visibility=GeoFeedback.Visibility.PRIVATE,
    )


@pytest.mark.django_db
class TestGeoFeedbackAPI:
    """Test GeoFeedback list and retrieval."""

    def test_list_feedbacks_public(self, api_client, feedback, feedback_draft_private):
        """Anonymous users should only see published/public feedbacks."""
        url = "/api/v1/feedback/"
        resp = api_client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        results = resp.data["results"]
        # Standard cursor pagination returns 'results' list
        assert len(results) == 1
        assert results[0]["id"] == str(feedback.id)

    def test_list_feedbacks_admin(self, admin_client, feedback, feedback_draft_private):
        """Admins should see all feedbacks."""
        url = "/api/v1/feedback/"
        resp = admin_client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data["results"]) == 2

    def test_retrieve_feedback_includes_slug(self, api_client, feedback):
        """Wait, detail read should return custom_form_slug."""
        url = f"/api/v1/feedback/{feedback.id}/"
        resp = api_client.get(url)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["custom_form_slug"] == "test-form"
        assert resp.data["rating_enabled"] is True

    def test_anonymous_create_fails(self, api_client, campaign):
        """Anonymous user cannot create feedback."""
        url = "/api/v1/feedback/"
        data = {
            "title": "New Feedback",
            "campaign": str(campaign.id),
            "rating_enabled": True,
        }
        resp = api_client.post(url, data)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_create_feedback(self, admin_client, campaign):
        """Admin user can create feedback."""
        url = "/api/v1/feedback/"
        data = {
            "title": "New Feedback",
            "campaign": str(campaign.id),
            "rating_enabled": True,
            "form_enabled": False,
        }
        resp = admin_client.post(url, data)
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["title"] == "New Feedback"


@pytest.mark.django_db
class TestFeedbackSubmissionAPI:
    """Test POST /api/v1/feedback/{id}/submit/ action."""

    def test_submit_feedback_with_rating_and_form(self, api_client, feedback):
        """Anonymous submit with both rating and form matches criteria."""
        url = f"/api/v1/feedback/{feedback.id}/submit/"
        resp = api_client.post(
            url,
            {
                "rating": 5,
                "form_data": {"test_field": "test_answer"}
            },
            format="json"
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["rating"] == 5
        assert resp.data["form_data"]["test_field"] == "test_answer"
        assert resp.data["submitted_by"] is None

    def test_submit_fails_when_missing_required_rating(self, api_client, feedback):
        """Should fail if rating_enabled=True but rating is missing."""
        url = f"/api/v1/feedback/{feedback.id}/submit/"
        resp = api_client.post(
            url,
            {"form_data": {"comment": "Hi"}},
            format="json"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "rating" in resp.data

    def test_submit_fails_when_missing_required_form(self, api_client, feedback):
        """Should fail if form_enabled=True but form_data is missing."""
        url = f"/api/v1/feedback/{feedback.id}/submit/"
        resp = api_client.post(
            url,
            {"rating": 3},
            format="json"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "form_data" in resp.data

    def test_submit_authenticated_user(self, user_client, regular_user, feedback):
        """Authenticated user's ID should be saved as submitted_by."""
        url = f"/api/v1/feedback/{feedback.id}/submit/"
        resp = user_client.post(
            url,
            {"rating": 4, "form_data": {"q": "a"}},
            format="json"
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["submitted_by"] == regular_user.id

    def test_submit_with_geometry(self, api_client, feedback):
        """Should parse GeoJSON if allow_drawings=True."""
        url = f"/api/v1/feedback/{feedback.id}/submit/"
        resp = api_client.post(
            url,
            {
                "rating": 5,
                "form_data": {"any": "val"},
                "geometry": {
                    "type": "Point",
                    "coordinates": [10.0, 53.5]
                }
            },
            format="json"
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert "geometry" in resp.data

    def test_submit_geometry_rejected_if_disabled(self, api_client, feedback_no_drawings):
        """If allow_drawings is False, geometry should be rejected."""
        url = f"/api/v1/feedback/{feedback_no_drawings.id}/submit/"
        resp = api_client.post(
            url,
            {
                "rating": 4,
                "geometry": {
                    "type": "Point",
                    "coordinates": [10.0, 53.5]
                }
            },
            format="json"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "geometry" in resp.data


@pytest.mark.django_db
class TestFeedbackDetailLayers:
    """GeoFeedback detail must return canonical Layer metadata."""

    def test_detail_returns_layer_summary(self, api_client, feedback, admin_user):
        from tosca_api.apps.feedback.models import FeedbackLayer
        from tosca_api.apps.geodata_providers.test_helpers import make_layer

        layer = make_layer("workspace:fb_detail_layer", user=admin_user)
        FeedbackLayer.objects.create(
            feedback=feedback, layer=layer, display_order=1
        )

        resp = api_client.get(f"/api/v1/feedback/{feedback.id}/")
        assert resp.status_code == 200

        layers = resp.data["layers"]
        assert len(layers) == 1
        layer_payload = layers[0]["layer"]
        assert layer_payload["name"] == "fb_detail_layer"
        assert layer_payload["workspace"]["name"] == "workspace"
        assert layer_payload["geometry_type"] == "Point"
        assert layer_payload["srid"] == 4326
        assert layer_payload["is_public"] is True
        assert layer_payload["publishing_state"] == "PUBLISHED"
        assert layers[0]["display_order"] == 1


@pytest.mark.django_db
class TestFeedbackLayerWrites:
    """Nested layer writes via UUID list."""

    def test_create_with_layer_uuids(self, admin_client, admin_user, campaign):
        from tosca_api.apps.feedback.models import FeedbackLayer, GeoFeedback
        from tosca_api.apps.geodata_providers.test_helpers import make_layer

        l1 = make_layer("workspace:fb_w_1", user=admin_user)
        l2 = make_layer("workspace:fb_w_2", user=admin_user)

        payload = {
            "title": "FB w/ layers",
            "campaign": str(campaign.id),
            "rating_enabled": True,
            "form_enabled": False,
            "layers": [str(l1.id), str(l2.id)],
        }
        resp = admin_client.post("/api/v1/feedback/", payload, format="json")
        assert resp.status_code == 201, resp.data

        fb = GeoFeedback.objects.get(id=resp.data["id"])
        rows = list(
            FeedbackLayer.objects.filter(feedback=fb).order_by("display_order")
        )
        assert [r.layer_id for r in rows] == [l1.id, l2.id]
        assert [r.display_order for r in rows] == [0, 1]

    def test_create_rejects_non_public_layer(self, admin_client, admin_user, campaign):
        from tosca_api.apps.geodata_providers.test_helpers import make_layer

        private = make_layer(
            "workspace:fb_w_private", user=admin_user, is_public=False
        )
        payload = {
            "title": "FB",
            "campaign": str(campaign.id),
            "rating_enabled": True,
            "form_enabled": False,
            "layers": [str(private.id)],
        }
        resp = admin_client.post("/api/v1/feedback/", payload, format="json")
        assert resp.status_code == 400
        assert "layers" in resp.data
