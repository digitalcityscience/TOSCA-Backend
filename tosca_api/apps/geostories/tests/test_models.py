"""
Tests for GeoStory models.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.geostories.models import (
    GeoStory,
    GeoStoryLayer,
    geostory_hero_image_upload_to,
)
from tosca_api.apps.geodata_providers.test_helpers import make_layer

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(username="testuser", password="password")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Test Campaign", created_by=user)


@pytest.mark.django_db
def test_hero_image_alias_is_default_when_campaign_public_but_story_draft(user, campaign):
    """S2 fix (security tickets ticket 13): public campaign alone is not
    enough -- a draft story's hero image must stay private."""
    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    story = GeoStory.objects.create(
        title="Draft", campaign=campaign, author=user, status=GeoStory.Status.DRAFT
    )
    assert story.desired_hero_image_storage_alias() == GeoStory.StorageAlias.DEFAULT


@pytest.mark.django_db
def test_hero_image_alias_is_public_when_campaign_public_and_story_published(user, campaign):
    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    story = GeoStory.objects.create(
        title="Published", campaign=campaign, author=user, status=GeoStory.Status.PUBLISHED
    )
    assert story.desired_hero_image_storage_alias() == GeoStory.StorageAlias.PUBLIC


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
def test_geostory_saves_without_hero_image(user, campaign):
    """Hero image fields are optional at the database/model contract level."""
    story = GeoStory.objects.create(
        title="No Hero Story",
        campaign=campaign,
        author=user,
    )

    story.full_clean()
    assert not story.hero_image
    assert story.hero_image_alt == ""


@pytest.mark.django_db
def test_geostory_hero_image_requires_alt_text(user, campaign):
    """Model validation returns a field-keyed error when image alt is missing."""
    story = GeoStory(
        title="Hero Story",
        campaign=campaign,
        author=user,
        hero_image="geostories/test/hero/example.jpg",
    )

    with pytest.raises(ValidationError) as exc:
        story.full_clean()

    assert "hero_image_alt" in exc.value.message_dict


@pytest.mark.django_db
def test_geostory_hero_image_upload_path_is_story_scoped(user, campaign):
    """Hero image paths use the GeoStory UUID, not mutable story text."""
    story = GeoStory(title="Hero Story", campaign=campaign, author=user)

    path = geostory_hero_image_upload_to(story, "My Cover.JPG")

    assert path.startswith(f"geostories/{story.pk}/hero/")
    assert path.endswith(".jpg")
    assert "My Cover" not in path


@pytest.mark.django_db
@pytest.mark.parametrize(
    "filename",
    [
        "x.jpg/../evil",
        "../../evil.jpg",
        "a/../../b.jpg",
        "x.jpg\\..\\evil",
        "....//....//etc/passwd.jpg",
        "..",
        "../",
    ],
)
def test_geostory_hero_image_upload_path_rejects_traversal_filenames(user, campaign, filename):
    """A crafted filename must never let the stored path escape the
    per-story ``hero/`` scope.

    The function only ever keeps the substring after the *last* ``.`` in the
    filename (as a lowercased "extension") and prefixes a fresh UUID -- so
    even when that substring carries a stray ``/`` or ``\\`` from a crafted
    filename, it can never carry a ``..`` (a ``..`` needs two adjacent dots,
    and splitting on the last dot always consumes the second one). This
    regression test pins that existing, already-safe behavior; it adds no
    new sanitization.
    """
    story = GeoStory(title="Hero Story", campaign=campaign, author=user)

    path = geostory_hero_image_upload_to(story, filename)

    assert path.startswith(f"geostories/{story.pk}/hero/")
    assert ".." not in path.split("/")


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
def test_geostory_hero_image_alt_sanitization(user, campaign):
    """Hero image alt text follows the same plain-text sanitization policy."""
    story = GeoStory.objects.create(
        title="Hero Story",
        campaign=campaign,
        author=user,
        hero_image_alt="<strong>Descriptive alt</strong>",
    )

    assert story.hero_image_alt == "Descriptive alt"


@pytest.mark.django_db
def test_geostory_context_linking(user, campaign):
    """Test linking a GeoContext."""
    context = GeoContext.objects.create(
        content={"blocks": [{"type": "paragraph", "data": {"text": "Rich"}}]},
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
    layer1 = make_layer("workspace:roads", user=user)
    layer2 = make_layer("workspace:buildings", user=user)

    # Add via through model
    GeoStoryLayer.objects.create(geostory=story, layer=layer1, display_order=2)
    GeoStoryLayer.objects.create(geostory=story, layer=layer2, display_order=1)

    assert story.layers.count() == 2
    
    # Check ordering
    refs = story.layers.all().order_by("geostory_uses__display_order")
    assert refs[0] == layer2  # order 1
    assert refs[1] == layer1  # order 2


@pytest.mark.django_db
def test_geostory_layer_has_updated_at_that_changes_on_save(user, campaign):
    """Regression test: through-tables were missing
    updated_at (created_at-only via manual field, not TimeStampedModel).
    """
    story = GeoStory.objects.create(title="Through Story", campaign=campaign, author=user)
    layer = make_layer("workspace:through_layer", user=user)
    ref = GeoStoryLayer.objects.create(geostory=story, layer=layer)

    original_updated_at = ref.updated_at
    assert original_updated_at is not None

    ref.display_order = 5
    ref.save()
    ref.refresh_from_db()

    assert ref.updated_at > original_updated_at


@pytest.mark.django_db
def test_geostory_layer_auto_increment(user, campaign):
    """Test that layer display_order auto-increments."""
    story = GeoStory.objects.create(
        title="Ordered Story",
        campaign=campaign,
        author=user,
    )
    layer1 = make_layer("workspace:layer1", user=user)
    layer2 = make_layer("workspace:layer2", user=user)
    layer3 = make_layer("workspace:layer3", user=user)

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


@pytest.mark.django_db
def test_geostory_layer_rejects_non_public_layer(user, campaign):
    """Through-model clean() must reject is_public=False layers."""
    from django.core.exceptions import ValidationError

    story = GeoStory.objects.create(title="S", campaign=campaign, author=user)
    layer = make_layer("workspace:private_layer", user=user, is_public=False)

    with pytest.raises(ValidationError):
        GeoStoryLayer.objects.create(geostory=story, layer=layer)


@pytest.mark.django_db
def test_geostory_layer_rejects_unpublished_layer(user, campaign):
    """Through-model clean() must reject non-PUBLISHED layers."""
    from django.core.exceptions import ValidationError

    story = GeoStory.objects.create(title="S", campaign=campaign, author=user)
    layer = make_layer(
        "workspace:draft_layer", user=user, publishing_state="DRAFT"
    )

    with pytest.raises(ValidationError):
        GeoStoryLayer.objects.create(geostory=story, layer=layer)


@pytest.mark.django_db
def test_geostory_layer_accepts_public_published(user, campaign):
    """Through-model clean() accepts public + published layers."""
    story = GeoStory.objects.create(title="S", campaign=campaign, author=user)
    layer = make_layer("workspace:ok_layer", user=user)
    gsl = GeoStoryLayer.objects.create(geostory=story, layer=layer)
    assert gsl.id is not None
