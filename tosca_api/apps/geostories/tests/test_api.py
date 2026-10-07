import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.featurelinks.models import FeatureLink
from tosca_api.apps.geostories.models import GeoStory, GeoStoryScene, GeoStorySceneLayer
from tosca_api.apps.geostories.tests.scene_helpers import make_raster_layer, make_vector_layer

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


def _org_token(*roles, org="dcs"):
    """Keycloak-shaped token for org-scoped writes (epic-11 PR1 SS3.3)."""
    return {"realm_access": {"roles": list(roles)}, "default_organization": org}


def _authenticate_org_writer(api_client, user, *roles, org="dcs"):
    """Authenticate ``user`` for both gate C (``request.auth`` token, read by
    ``CampaignScopedPermission``) and gate A (``user._auth_claims``, read by
    ``has_perm()`` -> ``OrgRolePermissionBackend`` via
    ``DjangoModelPermissionsOrAnonReadOnly``, security tickets ticket 09).

    ``APIClient.force_authenticate`` bypasses ``KeycloakTokenAuthentication``
    entirely, so it never attaches ``_auth_claims`` itself -- it must be set
    on the same ``user`` object passed in here, since DRF's
    ``force_authenticate`` uses that exact instance as ``request.user``.
    """
    level = roles[0].rsplit("_", 1)[-1] if roles else None
    if level:
        user._auth_claims = AuthClaims(org_roles={org: level}, default_org=org, authoritative=True)
    api_client.force_authenticate(user=user, token=_org_token(*roles, org=org))


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
def story_content():
    return {
        "blocks": [
            {"type": "paragraph", "data": {"text": "This is the story content."}},
        ]
    }


@pytest.fixture
def geostory(user, campaign, story_content):
    """Create a published story with feature-owned content."""
    return GeoStory.objects.create(
        title="Existing Story",
        summary="Story summary",
        status=GeoStory.Status.PUBLISHED,
        campaign=campaign,
        author=user,
        content=story_content,
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
def test_geostory_list_published_and_own_org_draft(api_client, user, geostory, draft_story):
    """Non-staff org members see published stories plus their own org's drafts.

    ``draft_story`` belongs to the caller's own org (security tickets S1 /
    ticket 02 target rule: own-org unpublished content is visible; only
    *cross-org* unpublished content is excluded -- see
    ``geostories/tests/test_org_isolation.py`` for the cross-org case).
    """
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200

    results = response.data["results"]
    titles = [r["title"] for r in results]

    assert "Existing Story" in titles
    assert "Draft Story" in titles


@pytest.mark.django_db
def test_geostory_list_staff_sees_all(api_client, staff_user, geostory, draft_story):
    """Test that staff users can see all stories including drafts."""
    _authenticate_org_writer(api_client, staff_user, "ROLE_DCS_WRITER")
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
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200

    story_data = response.data["results"][0]

    # Required fields in list
    assert "id" in story_data
    assert "title" in story_data
    assert "summary" in story_data
    assert "about_author" in story_data
    assert "hero_image_url" in story_data
    assert "hero_image_alt" in story_data
    assert "campaign" in story_data
    assert "created_at" in story_data

    # These should NOT be in list (detail only)
    assert "content" not in story_data
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

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.get(f"/api/v1/stories/?campaign_id={campaign.id}")
    assert response.status_code == 200
    assert len(response.data["results"]) == 2


# =============================================================================
# Detail View Tests (Task 1.6)
# =============================================================================


@pytest.mark.django_db
def test_geostory_detail_has_owned_content(api_client, user, geostory):
    """Test that detail returns the story content directly."""
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200

    assert isinstance(response.data["content"], dict)
    assert response.data["content"] == {
        "blocks": [
            {"type": "paragraph", "data": {"text": "This is the story content."}},
        ]
    }
    assert "context" not in response.data


def _scene_with_layers(story, *layers, **scene_attrs):
    scene = GeoStoryScene.objects.create(geostory=story, title="Scene", **scene_attrs)
    for layer in layers:
        GeoStorySceneLayer.objects.create(scene=scene, layer=layer)
    return scene


@pytest.mark.django_db
def test_geostory_detail_has_scenes(api_client, user, geostory):
    layer = make_vector_layer("workspace:scene_parks", user=user)
    scene = GeoStoryScene.objects.create(
        geostory=geostory,
        title="Harbour",
        caption="Look east",
        center_lng=9.99,
        center_lat=53.55,
        zoom=13.5,
        bearing=-20,
        pitch=45,
        bounds=[9.9, 53.5, 10.1, 53.6],
        transition=GeoStoryScene.Transition.EASE,
        duration_ms=800,
    )
    scene_layer = GeoStorySceneLayer.objects.create(
        scene=scene,
        layer=layer,
        opacity=0.5,
        feature_mode=GeoStorySceneLayer.FeatureMode.HIGHLIGHT,
        feature_id_attribute="name",
        feature_ids=["Stadtpark"],
    )

    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200

    [scene_payload] = response.data["scenes"]
    assert scene_payload["id"] == str(scene.id)
    assert scene_payload["order"] == 0
    assert scene_payload["title"] == "Harbour"
    assert scene_payload["caption"] == "Look east"
    assert scene_payload["camera"] == {
        "center": [9.99, 53.55],
        "zoom": 13.5,
        "bearing": -20,
        "pitch": 45,
        "bounds": [9.9, 53.5, 10.1, 53.6],
    }
    assert scene_payload["transition"] == {"type": "ease", "duration_ms": 800}

    [layer_payload] = scene_payload["layers"]
    assert layer_payload["id"] == str(scene_layer.id)
    assert layer_payload["layer"]["name"] == "scene_parks"
    assert layer_payload["layer"]["workspace"]["name"] == "workspace"
    assert layer_payload["layer"]["publishing_state"] == "PUBLISHED"
    assert layer_payload["style_assignment"]["id"] == str(scene_layer.style_assignment_id)
    assert layer_payload["style_assignment"]["format"] == "mbstyle"
    assert layer_payload["render_layer_ids"] == ["parks-fill", "parks-line"]
    assert layer_payload["opacity"] == 0.5
    assert layer_payload["features"] == {
        "mode": "highlight",
        "attribute": "name",
        "ids": ["Stadtpark"],
    }


@pytest.mark.django_db
def test_geostory_detail_scene_without_camera_fits_layers(api_client, user, geostory):
    _scene_with_layers(geostory, make_vector_layer("workspace:fit", user=user))

    response = api_client.get(f"/api/v1/stories/{geostory.id}/")

    camera = response.data["scenes"][0]["camera"]
    assert camera["center"] is None
    assert camera["zoom"] is None
    assert camera["bounds"] is None


@pytest.mark.django_db
def test_geostory_detail_deprecated_layers_are_distinct_across_scenes(
    api_client, user, geostory
):
    shared = make_vector_layer("workspace:shared", user=user)
    ortho = make_raster_layer("workspace:ortho", user=user)
    _scene_with_layers(geostory, shared)
    _scene_with_layers(geostory, ortho, shared)

    response = api_client.get(f"/api/v1/stories/{geostory.id}/")

    layers = response.data["layers"]
    assert [item["layer"]["name"] for item in layers] == ["shared", "ortho"]
    assert [item["display_order"] for item in layers] == [0, 1]
    assert layers[1]["style_assignment"]["format"] == "sld"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    [{"is_public": False}, {"publishing_state": "DRAFT"}],
)
def test_geostory_detail_skips_layers_no_longer_public(api_client, user, geostory, change):
    from tosca_api.apps.geodata_providers.models import Layer

    visible = make_vector_layer("workspace:still_public", user=user)
    hidden = make_vector_layer("workspace:went_private", user=user)
    _scene_with_layers(geostory, visible, hidden)
    Layer.objects.filter(pk=hidden.pk).update(**change)

    response = api_client.get(f"/api/v1/stories/{geostory.id}/")

    scene_layers = response.data["scenes"][0]["layers"]
    assert [item["layer"]["name"] for item in scene_layers] == ["still_public"]
    assert [item["layer"]["name"] for item in response.data["layers"]] == ["still_public"]


@pytest.mark.django_db
def test_geostory_detail_scenes_no_n_plus_one(api_client, user, geostory):
    """Detail query count must not scale with scene or scene layer count."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    url = f"/api/v1/stories/{geostory.id}/"

    # Warm caches (auth, content types) so they don't pollute the count.
    api_client.get(url)

    _scene_with_layers(
        geostory, *(make_vector_layer(f"workspace:n1_a_{i}", user=user) for i in range(2))
    )

    with CaptureQueriesContext(connection) as ctx_small:
        response = api_client.get(url)
    assert response.status_code == 200
    assert len(response.data["layers"]) == 2

    for scene_index in range(2):
        _scene_with_layers(
            geostory,
            *(
                make_vector_layer(f"workspace:n1_{scene_index}_{i}", user=user)
                for i in range(3)
            ),
        )

    with CaptureQueriesContext(connection) as ctx_large:
        response = api_client.get(url)
    assert response.status_code == 200
    assert len(response.data["scenes"]) == 3
    assert len(response.data["layers"]) == 8

    assert len(ctx_large) == len(ctx_small)


@pytest.mark.django_db
def test_geostory_write_does_not_accept_layers(api_client, user, campaign):
    """Scenes are authored in the admin; the API ignores legacy ``layers``."""
    layer = make_vector_layer("workspace:write_ignored", user=user)

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.post(
        "/api/v1/stories/",
        {"title": "Story", "campaign": str(campaign.id), "layers": [str(layer.id)]},
        format="json",
    )

    assert response.status_code == 201
    assert "layers" not in response.data
    assert not GeoStoryScene.objects.filter(geostory_id=response.data["id"]).exists()


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

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
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
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.get(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 200

    data = response.data

    # All required fields
    assert "id" in data
    assert "title" in data
    assert "summary" in data
    assert "about_author" in data
    assert "status" in data
    assert "campaign" in data
    assert "content" in data
    assert "scenes" in data
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
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    data = {
        "title": "New Story",
        "summary": "A test story",
        "about_author": "A short biography of the story author.",
        "status": "draft",
        "campaign": str(campaign.id),
    }
    response = api_client.post("/api/v1/stories/", data)
    assert response.status_code == 201
    assert response.data["title"] == "New Story"
    assert response.data["author"] == user.id
    assert response.data["about_author"] == "A short biography of the story author."


@pytest.mark.django_db
def test_geostory_create_requires_title(api_client, user, campaign):
    """Test that title is required."""
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    data = {
        "campaign": str(campaign.id),
    }
    response = api_client.post("/api/v1/stories/", data)
    assert response.status_code == 400
    assert "title" in response.data


@pytest.mark.django_db
def test_geostory_update(api_client, user, geostory):
    """Test updating a geostory."""
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.patch(
        f"/api/v1/stories/{geostory.id}/",
        {"title": "Updated Title", "about_author": "Updated author biography."},
    )
    assert response.status_code == 200
    assert response.data["title"] == "Updated Title"
    assert response.data["about_author"] == "Updated author biography."
    geostory.refresh_from_db()
    assert geostory.about_author == "Updated author biography."


@pytest.mark.django_db
def test_geostory_write_serializer_surfaces_hero_alt_error(api_client, user, geostory):
    """Model clean() errors should be exposed as field-keyed API errors."""
    geostory.hero_image = "geostories/existing/hero/example.jpg"
    geostory.hero_image_alt = "Existing alt"
    geostory.save()

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
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
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200

    story_data = next(r for r in response.data["results"] if r["id"] == str(geostory.id))
    assert "hero_image_url" in story_data
    assert "hero_image_alt" in story_data
    assert story_data["hero_image_url"] is None  # No hero set on the fixture


@pytest.mark.django_db
def test_geostory_detail_includes_hero_fields_no_image(api_client, user, geostory):
    """Detail payload exposes hero fields even when no image is set."""
    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
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

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
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

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
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
    upload = SimpleUploadedFile("fake.png", buf.getvalue(), content_type="image/png")

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
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

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
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

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")

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
    replacement = SimpleUploadedFile("b.png", buf2.getvalue(), content_type="image/png")
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

    _authenticate_org_writer(api_client, user, "ROLE_DCS_WRITER")
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
    _authenticate_org_writer(api_client, user, "ROLE_DCS_ADMIN")
    response = api_client.delete(f"/api/v1/stories/{geostory.id}/")
    assert response.status_code == 204
    assert not GeoStory.objects.filter(id=geostory.id).exists()
