import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.featurelinks.models import FeatureLink
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.geostories.models import GeoStory, GeoStoryLayer
from tosca_api.apps.geodata_providers.test_helpers import make_layer

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


def _org_token(*roles, org="dcs"):
    """Keycloak-shaped token for org-scoped writes (epic-11 PR1 SS3.3)."""
    return {"realm_access": {"roles": list(roles)}, "default_organization": org}


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
        content={
            "blocks": [
                {"type": "paragraph", "data": {"text": "This is the story content."}},
            ]
        },
        created_by=user,
    )


@pytest.fixture
def layer_ref(user):
    return make_layer("workspace:test_layer", user=user)


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
    """Anonymous users can list published geostories."""
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_published_queryset_matches_inline_visibility_rule(geostory, draft_story):
    """GeoStory.objects.published() (issue 23) must return exactly the same
    rows the view's inline status filter did before it was extracted into a
    named queryset method.
    """
    assert list(GeoStory.objects.published()) == [geostory]


# =============================================================================
# List View Tests (Task 1.6)
# =============================================================================


@pytest.mark.django_db
def test_geostory_list_published_only(api_client, user, geostory, draft_story):
    """Test that non-staff users only see published stories."""
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
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
    api_client.force_authenticate(user=staff_user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200
    
    results = response.data["results"]
    titles = [r["title"] for r in results]
    
    # Staff should see both
    assert "Existing Story" in titles
    assert "Draft Story" in titles


@pytest.mark.django_db
def test_geostory_list_unauthenticated_published_only(api_client, geostory, draft_story):
    """Anonymous users only see published stories."""
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200

    titles = [r["title"] for r in response.data["results"]]
    assert "Existing Story" in titles
    assert "Draft Story" not in titles


@pytest.mark.django_db
def test_geostory_detail_unauthenticated_can_read_published(api_client, geostory):
    """Anonymous users can retrieve a published story."""
    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200
    assert response.data["id"] == str(geostory.id)


@pytest.mark.django_db
def test_geostory_detail_unauthenticated_cannot_read_draft(api_client, draft_story):
    """Anonymous users cannot retrieve unpublished stories."""
    response = api_client.get(f"/api/v1/stories/{draft_story.id}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_geostory_create_unauthenticated_forbidden(api_client, campaign):
    """Anonymous users cannot create stories."""
    response = api_client.post(
        "/api/v1/stories/",
        {
            "title": "Anonymous Draft",
            "campaign": str(campaign.id),
        },
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_geostory_list_payload_fields(api_client, user, geostory):
    """Test that list response has slim payload (required fields only)."""
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200
    
    story_data = response.data["results"][0]
    
    # Required fields in list
    assert "id" in story_data
    assert "title" in story_data
    assert "summary" in story_data
    assert "hero_image_url" in story_data
    assert "hero_image_alt" in story_data
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

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.get(f"/api/v1/stories/?campaign_id={campaign.id}")
    assert response.status_code == 200
    assert len(response.data["results"]) == 2


# =============================================================================
# Detail View Tests (Task 1.6)
# =============================================================================


@pytest.mark.django_db
def test_geostory_detail_has_nested_context(api_client, user, geostory):
    """Test that detail view returns nested context object (not just UUID)."""
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200
    
    context = response.data["context"]
    assert context is not None
    assert "content" in context
    assert isinstance(context["content"], dict)
    assert context["content"] == {
        "blocks": [
            {"type": "paragraph", "data": {"text": "This is the story content."}},
        ]
    }
    assert "content_type" not in context


@pytest.mark.django_db
def test_geostory_detail_has_layers(api_client, user, geostory, layer_ref):
    """Test that detail view returns layers with display_order."""
    # Add layer to story
    GeoStoryLayer.objects.create(geostory=geostory, layer=layer_ref, display_order=1)
    
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200
    
    layers = response.data["layers"]
    assert len(layers) == 1
    layer_payload = layers[0]["layer"]
    assert layer_payload["name"] == "test_layer"
    assert layer_payload["workspace"]["name"] == "workspace"
    assert layer_payload["geometry_type"] == "Point"
    assert layer_payload["srid"] == 4326
    assert layer_payload["is_public"] is True
    assert layer_payload["publishing_state"] == "PUBLISHED"
    assert layers[0]["display_order"] == 1


@pytest.mark.django_db
def test_geostory_detail_layers_no_n_plus_one(api_client, user, geostory):
    """Detail endpoint query count must not scale with linked layer count."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from tosca_api.apps.geodata_providers.test_helpers import make_layer

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    url = f"/api/v1/stories/{geostory.id}/"

    # Warm caches (auth, content types) so they don't pollute the count.
    api_client.get(url)

    for i in range(2):
        layer = make_layer(f"workspace:n1_a_{i}", user=user)
        GeoStoryLayer.objects.create(
            geostory=geostory, layer=layer, display_order=i
        )

    with CaptureQueriesContext(connection) as ctx_2:
        response = api_client.get(url)
    assert response.status_code == 200
    assert len(response.data["layers"]) == 2

    for i in range(2, 8):
        layer = make_layer(f"workspace:n1_a_{i}", user=user)
        GeoStoryLayer.objects.create(
            geostory=geostory, layer=layer, display_order=i
        )

    with CaptureQueriesContext(connection) as ctx_8:
        response = api_client.get(url)
    assert response.status_code == 200
    assert len(response.data["layers"]) == 8

    # Query count must be the same regardless of how many layers are linked.
    assert len(ctx_8) == len(ctx_2)


@pytest.mark.django_db
def test_geostory_create_with_layer_uuids(api_client, user, campaign):
    """POST with layers=[uuid1, uuid2] must persist GeoStoryLayer rows."""
    from tosca_api.apps.geodata_providers.test_helpers import make_layer

    layer1 = make_layer("workspace:write_a", user=user)
    layer2 = make_layer("workspace:write_b", user=user)

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    payload = {
        "title": "Story With Layers",
        "campaign": str(campaign.id),
        "layers": [str(layer1.id), str(layer2.id)],
    }
    response = api_client.post("/api/v1/stories/", payload, format="json")
    assert response.status_code == 201

    story = GeoStory.objects.get(id=response.data["id"])
    rows = list(GeoStoryLayer.objects.filter(geostory=story).order_by("display_order"))
    assert [r.layer_id for r in rows] == [layer1.id, layer2.id]
    assert [r.display_order for r in rows] == [0, 1]


@pytest.mark.django_db
def test_geostory_create_rejects_unknown_layer_uuid(api_client, user, campaign):
    import uuid as uuid_module

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    payload = {
        "title": "Story",
        "campaign": str(campaign.id),
        "layers": [str(uuid_module.uuid4())],
    }
    response = api_client.post("/api/v1/stories/", payload, format="json")
    assert response.status_code == 400
    assert "layers" in response.data


@pytest.mark.django_db
def test_geostory_create_rejects_non_public_layer(api_client, user, campaign):
    from tosca_api.apps.geodata_providers.test_helpers import make_layer

    private = make_layer("workspace:write_private", user=user, is_public=False)

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    payload = {
        "title": "Story",
        "campaign": str(campaign.id),
        "layers": [str(private.id)],
    }
    response = api_client.post("/api/v1/stories/", payload, format="json")
    assert response.status_code == 400
    assert "layers" in response.data


@pytest.mark.django_db
def test_geostory_update_replaces_layers(api_client, user, geostory):
    from tosca_api.apps.geodata_providers.test_helpers import make_layer

    initial = make_layer("workspace:upd_initial", user=user)
    GeoStoryLayer.objects.create(geostory=geostory, layer=initial, display_order=0)

    replacement = make_layer("workspace:upd_replacement", user=user)
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.patch(
        f"/api/v1/stories/{geostory.id}/",
        {"layers": [str(replacement.id)]},
        format="json",
    )
    assert response.status_code == 200

    rows = list(GeoStoryLayer.objects.filter(geostory=geostory))
    assert len(rows) == 1
    assert rows[0].layer_id == replacement.id


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
    
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
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
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
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
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
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
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    data = {
        "campaign": str(campaign.id),
    }
    response = api_client.post("/api/v1/stories/", data)
    assert response.status_code == 400
    assert "title" in response.data


@pytest.mark.django_db
def test_geostory_update(api_client, user, geostory):
    """Test updating a geostory."""
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.patch(
        f"/api/v1/stories/{geostory.id}/",
        {"title": "Updated Title"},
    )
    assert response.status_code == 200
    assert response.data["title"] == "Updated Title"


@pytest.mark.django_db
def test_geostory_write_serializer_surfaces_hero_alt_error(api_client, user, geostory):
    """Model clean() errors should be exposed as field-keyed API errors."""
    geostory.hero_image = "geostories/existing/hero/example.jpg"
    geostory.hero_image_alt = "Existing alt"
    geostory.save()

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.patch(
        f"/api/v1/stories/{geostory.id}/",
        {"hero_image_alt": ""},
        format="json",
    )

    assert response.status_code == 400
    assert "hero_image_alt" in response.data


@pytest.mark.django_db
def test_geostory_list_includes_hero_fields(api_client, user, geostory):
    """List payload exposes hero_image_url + hero_image_alt."""
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200

    story_data = next(r for r in response.data["results"] if r["id"] == str(geostory.id))
    assert "hero_image_url" in story_data
    assert "hero_image_alt" in story_data
    assert story_data["hero_image_url"] is None  # No hero set on the fixture


@pytest.mark.django_db
def test_geostory_detail_includes_hero_fields_no_image(api_client, user, geostory):
    """Detail payload exposes hero fields even when no image is set."""
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200
    assert response.data["hero_image_url"] is None
    assert response.data["hero_image_alt"] == ""


@pytest.mark.django_db
def test_geostory_create_with_hero_image_multipart(api_client, user, campaign):
    """Multipart POST persists the hero image and returns absolute URL on detail."""
    import io
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1200, 800), color=(10, 20, 30)).save(buf, format="JPEG")
    upload = SimpleUploadedFile("hero.jpg", buf.getvalue(), content_type="image/jpeg")

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.post(
        "/api/v1/stories/",
        {
            "title": "Hero Story",
            "campaign": str(campaign.id),
            "hero_image": upload,
            "hero_image_alt": "A descriptive alt",
        },
        format="multipart",
    )
    assert response.status_code == 201, response.data

    story = GeoStory.objects.get(id=response.data["id"])
    assert bool(story.hero_image) is True
    assert story.hero_image_alt == "A descriptive alt"

    detail = api_client.get(f"/api/v1/stories/{story.id}/")
    assert detail.status_code == 200
    assert detail.data["hero_image_url"].startswith("http")
    assert detail.data["hero_image_url"].endswith(story.hero_image.url)
    assert detail.data["hero_image_alt"] == "A descriptive alt"


@pytest.mark.django_db
def test_geostory_create_rejects_undersized_hero(api_client, user, campaign):
    """Hero policy rejects below the 800x450 minimum."""
    import io
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 300)).save(buf, format="PNG")
    upload = SimpleUploadedFile("small.png", buf.getvalue(), content_type="image/png")

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.post(
        "/api/v1/stories/",
        {
            "title": "Bad Hero",
            "campaign": str(campaign.id),
            "hero_image": upload,
            "hero_image_alt": "alt",
        },
        format="multipart",
    )
    assert response.status_code == 400
    assert "hero_image" in response.data


@pytest.mark.django_db
def test_geostory_create_rejects_disallowed_mime(api_client, user, campaign):
    """A GIF body with a forged content-type is rejected by header inspection."""
    import io
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1200, 800)).save(buf, format="GIF")
    upload = SimpleUploadedFile(
        "fake.png", buf.getvalue(), content_type="image/png"
    )

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.post(
        "/api/v1/stories/",
        {
            "title": "Bad Mime",
            "campaign": str(campaign.id),
            "hero_image": upload,
            "hero_image_alt": "alt",
        },
        format="multipart",
    )
    assert response.status_code == 400
    assert "hero_image" in response.data


@pytest.mark.django_db
def test_geostory_create_with_hero_requires_alt(api_client, user, campaign):
    """Uploading a valid hero image without alt returns a field-keyed 400."""
    import io
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1200, 800)).save(buf, format="JPEG")
    upload = SimpleUploadedFile("hero.jpg", buf.getvalue(), content_type="image/jpeg")

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    response = api_client.post(
        "/api/v1/stories/",
        {
            "title": "Missing Alt",
            "campaign": str(campaign.id),
            "hero_image": upload,
        },
        format="multipart",
    )
    assert response.status_code == 400
    assert "hero_image_alt" in response.data


@pytest.mark.django_db
def test_geostory_patch_replaces_hero_image(api_client, user, campaign):
    """PATCH with a fresh upload swaps the stored file and updates alt."""
    import io
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))

    buf = io.BytesIO()
    Image.new("RGB", (1200, 800), color=(1, 2, 3)).save(buf, format="JPEG")
    initial = SimpleUploadedFile("a.jpg", buf.getvalue(), content_type="image/jpeg")
    create_response = api_client.post(
        "/api/v1/stories/",
        {
            "title": "Replaceable",
            "campaign": str(campaign.id),
            "hero_image": initial,
            "hero_image_alt": "first",
        },
        format="multipart",
    )
    assert create_response.status_code == 201
    story_id = create_response.data["id"]
    original_path = GeoStory.objects.get(id=story_id).hero_image.name

    buf2 = io.BytesIO()
    Image.new("RGB", (1600, 900), color=(9, 8, 7)).save(buf2, format="PNG")
    replacement = SimpleUploadedFile(
        "b.png", buf2.getvalue(), content_type="image/png"
    )
    patch_response = api_client.patch(
        f"/api/v1/stories/{story_id}/",
        {"hero_image": replacement, "hero_image_alt": "second"},
        format="multipart",
    )
    assert patch_response.status_code == 200, patch_response.data

    refreshed = GeoStory.objects.get(id=story_id)
    assert refreshed.hero_image.name != original_path
    assert refreshed.hero_image_alt == "second"


@pytest.mark.django_db
def test_geostory_patch_clears_hero_image(api_client, user, campaign):
    """Setting hero_image to null lifts the alt requirement."""
    import io
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_WRITER"))
    buf = io.BytesIO()
    Image.new("RGB", (1200, 800)).save(buf, format="JPEG")
    upload = SimpleUploadedFile("hero.jpg", buf.getvalue(), content_type="image/jpeg")
    create_response = api_client.post(
        "/api/v1/stories/",
        {
            "title": "Clearable",
            "campaign": str(campaign.id),
            "hero_image": upload,
            "hero_image_alt": "alt",
        },
        format="multipart",
    )
    assert create_response.status_code == 201
    story_id = create_response.data["id"]

    response = api_client.patch(
        f"/api/v1/stories/{story_id}/",
        {"hero_image": "", "hero_image_alt": ""},
        format="multipart",
    )
    assert response.status_code == 200, response.data

    refreshed = GeoStory.objects.get(id=story_id)
    assert not refreshed.hero_image
    assert refreshed.hero_image_alt == ""


@pytest.mark.django_db
def test_geostory_admin_thumbnail_handles_missing_image(geostory):
    """Admin thumbnail/preview render harmlessly when no image is set."""
    from django.contrib.admin.sites import AdminSite

    from tosca_api.apps.geostories.admin import GeoStoryAdmin

    admin_instance = GeoStoryAdmin(GeoStory, AdminSite())
    assert admin_instance.hero_image_thumbnail(geostory) == "—"
    assert "No image uploaded." in admin_instance.hero_image_preview(geostory)


@pytest.mark.django_db
def test_geostory_delete(api_client, user, geostory):
    """Test deleting a geostory."""
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_ADMIN"))
    response = api_client.delete(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 204
    assert not GeoStory.objects.filter(id=geostory.id).exists()
