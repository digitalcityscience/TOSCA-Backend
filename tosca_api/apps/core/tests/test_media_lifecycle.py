"""Tests for the Campaign/GeoStory archive & restore lifecycle (epic-11 PR3).

Covers ``core.media_lifecycle``: desired-alias resolution (campaign archived,
story archived, visibility-driven private/public), copy-then-verify-then-delete
safety for regular and hero media, and the campaign/story sweep entry points.
"""

from __future__ import annotations

import io

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from PIL import Image

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.core.media_lifecycle import (
    ACTION_FAILED,
    ACTION_MOVED,
    ACTION_NO_CHANGE,
    MediaLifecycleService,
    desired_alias_for_asset,
)
from tosca_api.apps.core.models import MediaAsset
from tosca_api.apps.geostories.models import GeoStory
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _make_asset(path: str, **overrides) -> MediaAsset:
    defaults = dict(
        storage_path=path,
        original_name=path.rsplit("/", 1)[-1],
        mime="image/png",
        width=10,
        height=10,
        size=5,
    )
    defaults.update(overrides)
    return MediaAsset.objects.create(**defaults)


@pytest.fixture
def org(db):
    return Organization.objects.create(slug="acme", name="Acme")


@pytest.fixture
def campaign(org, django_user_model):
    user = django_user_model.objects.create_user(username="owner")
    return Campaign.objects.create(title="Camp", created_by=user, organization=org)


def _service(tmp_path):
    """A MediaLifecycleService backed by one temp-dir FileSystemStorage per alias."""
    backends = {}

    def storage_for_alias(alias):
        if alias not in backends:
            root = tmp_path / alias
            root.mkdir(parents=True, exist_ok=True)
            backends[alias] = FileSystemStorage(location=str(root))
        return backends[alias]

    return MediaLifecycleService(storage_for_alias=storage_for_alias), backends, storage_for_alias


# ---------------------------------------------------------------------------
# desired_alias_for_asset
# ---------------------------------------------------------------------------


def test_desired_alias_is_none_for_orphan_asset():
    asset = _make_asset("orphan/unlinked.png")
    assert desired_alias_for_asset(asset) is None


def test_desired_alias_is_archive_when_campaign_archived(campaign):
    campaign.status = Campaign.Status.ARCHIVED
    campaign.save()
    asset = _make_asset(
        "misc/path.png", campaign=campaign, owner_org=campaign.organization
    )

    assert desired_alias_for_asset(asset) == MediaAsset.StorageAlias.ARCHIVE


def test_desired_alias_is_public_when_campaign_active_and_public(campaign):
    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    asset = _make_asset(
        "misc/path.png", campaign=campaign, owner_org=campaign.organization
    )

    assert desired_alias_for_asset(asset) == MediaAsset.StorageAlias.PUBLIC


def test_desired_alias_is_default_when_campaign_active_and_private(campaign):
    asset = _make_asset(
        "misc/path.png", campaign=campaign, owner_org=campaign.organization
    )

    assert desired_alias_for_asset(asset) == MediaAsset.StorageAlias.DEFAULT


def test_desired_alias_is_archive_when_owning_story_archived_but_campaign_active(
    campaign, django_user_model
):
    author = django_user_model.objects.create_user(username="author")
    story = GeoStory.objects.create(
        title="Story",
        campaign=campaign,
        author=author,
        status=GeoStory.Status.ARCHIVED,
    )
    # This asset resolves to the story via EditorJS content.
    from tosca_api.apps.geocontext.models import GeoContext

    from django.core.files.storage import default_storage

    default_storage.save("geocontext/editorjs/z/pic.png", ContentFile(_png_bytes()))
    context = GeoContext.objects.create(
        content={
            "blocks": [
                {
                    "type": "image",
                    "data": {
                        "file": {"url": "/media/geocontext/editorjs/z/pic.png"},
                        "alt": "a pic",
                    },
                }
            ]
        },
        created_by=author,
    )
    story.context = context
    story.save()
    asset = _make_asset(
        "geocontext/editorjs/z/pic.png", campaign=campaign, owner_org=campaign.organization
    )

    assert desired_alias_for_asset(asset) == MediaAsset.StorageAlias.ARCHIVE


def _story_with_context_asset(campaign, django_user_model, *, status, path):
    """A GeoStory whose context embeds an EditorJS-uploaded asset at ``path``."""
    from django.core.files.storage import default_storage

    from tosca_api.apps.geocontext.models import GeoContext

    author = django_user_model.objects.create_user(username=f"author-{path}")
    default_storage.save(path, ContentFile(_png_bytes()))
    context = GeoContext.objects.create(
        content={
            "blocks": [
                {
                    "type": "image",
                    "data": {"file": {"url": f"/media/{path}"}, "alt": "a pic"},
                }
            ]
        },
        created_by=author,
    )
    story = GeoStory.objects.create(
        title="Story", campaign=campaign, author=author, status=status, context=context
    )
    return story


def test_desired_alias_is_default_when_campaign_public_but_story_draft(
    campaign, django_user_model
):
    """S2 fix: public campaign alone is not enough -- the owning story must
    also be published, or the asset stays private (ticket 13 truth table)."""
    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    _story_with_context_asset(
        campaign, django_user_model, status=GeoStory.Status.DRAFT, path="geocontext/editorjs/draft/pic.png"
    )
    asset = _make_asset(
        "geocontext/editorjs/draft/pic.png", campaign=campaign, owner_org=campaign.organization
    )

    assert desired_alias_for_asset(asset) == MediaAsset.StorageAlias.DEFAULT


def test_desired_alias_is_public_when_campaign_public_and_story_published(
    campaign, django_user_model
):
    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    _story_with_context_asset(
        campaign,
        django_user_model,
        status=GeoStory.Status.PUBLISHED,
        path="geocontext/editorjs/pub/pic.png",
    )
    asset = _make_asset(
        "geocontext/editorjs/pub/pic.png", campaign=campaign, owner_org=campaign.organization
    )

    assert desired_alias_for_asset(asset) == MediaAsset.StorageAlias.PUBLIC


def test_desired_alias_is_default_when_campaign_public_but_event_draft(
    campaign, django_user_model
):
    from django.contrib.gis.geos import Point
    from django.utils import timezone
    from datetime import timedelta

    from tosca_api.apps.events.models import Event

    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()

    from django.core.files.storage import default_storage

    from tosca_api.apps.geocontext.models import GeoContext

    author = django_user_model.objects.create_user(username="event-author")
    path = "geocontext/editorjs/evt/pic.png"
    default_storage.save(path, ContentFile(_png_bytes()))
    context = GeoContext.objects.create(
        content={
            "blocks": [
                {"type": "image", "data": {"file": {"url": f"/media/{path}"}, "alt": "a pic"}}
            ]
        },
        created_by=author,
    )
    Event.objects.create(
        campaign=campaign,
        title="Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=author,
        status=Event.Status.DRAFT,
        context=context,
    )
    asset = _make_asset(path, campaign=campaign, owner_org=campaign.organization)

    assert desired_alias_for_asset(asset) == MediaAsset.StorageAlias.DEFAULT


# ---------------------------------------------------------------------------
# move_one
# ---------------------------------------------------------------------------


def test_move_one_copies_bytes_updates_alias_and_deletes_source(campaign, tmp_path):
    service, backends, storage_for_alias = _service(tmp_path)
    default_backend = storage_for_alias("default")
    default_backend.save("misc/path.png", ContentFile(b"hello"))
    asset = _make_asset(
        "misc/path.png",
        campaign=campaign,
        owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )

    entry = service.move_one(asset, MediaAsset.StorageAlias.PUBLIC)

    assert entry.action == ACTION_MOVED
    assert entry.status == "ok"
    public_backend = storage_for_alias("media_public")
    assert public_backend.exists("misc/path.png")
    with public_backend.open("misc/path.png", "rb") as fh:
        assert fh.read() == b"hello"
    assert not default_backend.exists("misc/path.png")

    asset.refresh_from_db()
    assert asset.storage_alias == MediaAsset.StorageAlias.PUBLIC


def test_move_one_is_no_change_when_already_in_target_alias(campaign, tmp_path):
    service, backends, storage_for_alias = _service(tmp_path)
    asset = _make_asset(
        "misc/path.png",
        campaign=campaign,
        owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.PUBLIC,
    )

    entry = service.move_one(asset, MediaAsset.StorageAlias.PUBLIC)

    assert entry.action == ACTION_NO_CHANGE


def test_move_one_moves_hero_image_matched_asset(campaign, django_user_model, tmp_path):
    author = django_user_model.objects.create_user(username="author4")
    story = GeoStory(
        title="Story", campaign=campaign, author=author, hero_image_alt="alt"
    )
    story.hero_image.name = "geostories/x/hero/img.png"
    story.save()
    service, backends, storage_for_alias = _service(tmp_path)
    storage_for_alias("default").save(
        "geostories/x/hero/img.png", ContentFile(b"hero-bytes")
    )
    asset = _make_asset(
        "geostories/x/hero/img.png",
        campaign=campaign,
        owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )

    entry = service.move_one(asset, MediaAsset.StorageAlias.ARCHIVE)

    assert entry.action == ACTION_MOVED
    assert storage_for_alias("media_archive").exists("geostories/x/hero/img.png")
    assert not storage_for_alias("default").exists("geostories/x/hero/img.png")
    asset.refresh_from_db()
    story.refresh_from_db()
    assert asset.storage_alias == MediaAsset.StorageAlias.ARCHIVE
    assert story.hero_image_storage_alias == MediaAsset.StorageAlias.ARCHIVE


def test_sync_story_assets_moves_hero_without_media_asset(campaign, django_user_model, tmp_path):
    author = django_user_model.objects.create_user(username="hero-only-author")
    story = GeoStory(
        title="Hero-only story", campaign=campaign, author=author, hero_image_alt="alt"
    )
    story.hero_image.name = "geostories/hero-only/hero/image.png"
    story.save()

    service, backends, storage_for_alias = _service(tmp_path)
    storage_for_alias("default").save(
        "geostories/hero-only/hero/image.png", ContentFile(b"hero-only")
    )
    story.status = GeoStory.Status.ARCHIVED
    story.save()

    entries = service.sync_story_assets(story)

    assert entries[0].action == ACTION_MOVED
    assert storage_for_alias("media_archive").exists("geostories/hero-only/hero/image.png")
    story.refresh_from_db()
    assert story.hero_image_storage_alias == MediaAsset.StorageAlias.ARCHIVE


def test_move_one_fails_when_source_object_missing(campaign, tmp_path):
    service, backends, storage_for_alias = _service(tmp_path)
    asset = _make_asset(
        "misc/missing.png",
        campaign=campaign,
        owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )

    entry = service.move_one(asset, MediaAsset.StorageAlias.PUBLIC)

    assert entry.status == "failed"
    assert entry.action == ACTION_FAILED
    assert "missing" in entry.detail


def test_move_one_resumes_when_destination_already_copied(campaign, tmp_path):
    service, backends, storage_for_alias = _service(tmp_path)
    default_backend = storage_for_alias("default")
    default_backend.save("misc/path.png", ContentFile(b"hello"))
    public_backend = storage_for_alias("media_public")
    public_backend.save("misc/path.png", ContentFile(b"hello"))  # simulate prior partial run
    asset = _make_asset(
        "misc/path.png",
        campaign=campaign,
        owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )

    entry = service.move_one(asset, MediaAsset.StorageAlias.PUBLIC)

    assert entry.action == ACTION_MOVED
    assert entry.status == "ok"
    asset.refresh_from_db()
    assert asset.storage_alias == MediaAsset.StorageAlias.PUBLIC
    assert not default_backend.exists("misc/path.png")


# ---------------------------------------------------------------------------
# sync_campaign_assets / sync_story_assets
# ---------------------------------------------------------------------------


def test_sync_campaign_assets_moves_every_owned_asset_to_archive(campaign, tmp_path):
    service, backends, storage_for_alias = _service(tmp_path)
    default_backend = storage_for_alias("default")
    default_backend.save("misc/a.png", ContentFile(b"a"))
    default_backend.save("misc/b.png", ContentFile(b"b"))
    a1 = _make_asset(
        "misc/a.png", campaign=campaign, owner_org=campaign.organization, size=1,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )
    a2 = _make_asset(
        "misc/b.png", campaign=campaign, owner_org=campaign.organization, size=1,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )
    campaign.status = Campaign.Status.ARCHIVED
    campaign.save()

    entries = service.sync_campaign_assets(campaign)

    assert all(e.action == ACTION_MOVED for e in entries)
    a1.refresh_from_db()
    a2.refresh_from_db()
    assert a1.storage_alias == MediaAsset.StorageAlias.ARCHIVE
    assert a2.storage_alias == MediaAsset.StorageAlias.ARCHIVE
    archive_backend = storage_for_alias("media_archive")
    assert archive_backend.exists("misc/a.png")
    assert archive_backend.exists("misc/b.png")


def test_sync_campaign_assets_skips_orphan_assets(campaign, tmp_path):
    service, backends, storage_for_alias = _service(tmp_path)
    # No campaign FK -- orphan asset, not owned by this campaign, must not
    # appear in the sweep at all (campaign.media_assets excludes it).
    _make_asset("orphan/x.png")

    entries = service.sync_campaign_assets(campaign)

    assert entries == []


def test_sync_story_assets_moves_only_that_storys_assets(campaign, django_user_model, tmp_path):
    author = django_user_model.objects.create_user(username="author5")
    from tosca_api.apps.geocontext.models import GeoContext
    from django.core.files.storage import default_storage

    default_storage.save("geocontext/editorjs/z/pic.png", ContentFile(_png_bytes()))
    context = GeoContext.objects.create(
        content={
            "blocks": [
                {
                    "type": "image",
                    "data": {
                        "file": {"url": "/media/geocontext/editorjs/z/pic.png"},
                        "alt": "a pic",
                    },
                }
            ]
        },
        created_by=author,
    )
    story = GeoStory.objects.create(title="Story", campaign=campaign, author=author, context=context)

    service, backends, storage_for_alias = _service(tmp_path)
    default_backend = storage_for_alias("default")
    default_backend.save("geocontext/editorjs/z/pic.png", ContentFile(b"x"))
    default_backend.save("misc/other.png", ContentFile(b"y"))
    story_asset = _make_asset(
        "geocontext/editorjs/z/pic.png",
        campaign=campaign,
        owner_org=campaign.organization,
        size=1,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )
    other_asset = _make_asset(
        "misc/other.png",
        campaign=campaign,
        owner_org=campaign.organization,
        size=1,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )

    # Bypass the post_save signal here: it would run the *real* lifecycle
    # service against the real storages and leave nothing for the isolated
    # tmp_path-backed service under test to do. Signal wiring itself is
    # covered by test_media_lifecycle_signals.py.
    GeoStory.objects.filter(pk=story.pk).update(status=GeoStory.Status.ARCHIVED)
    story.refresh_from_db()

    entries = service.sync_story_assets(story)

    assert len(entries) == 1
    assert entries[0].action == ACTION_MOVED
    story_asset.refresh_from_db()
    other_asset.refresh_from_db()
    assert story_asset.storage_alias == MediaAsset.StorageAlias.ARCHIVE
    # The campaign-only asset (not scoped to this story) is untouched.
    assert other_asset.storage_alias == MediaAsset.StorageAlias.DEFAULT


# ---------------------------------------------------------------------------
# sync_event_assets (ticket 14)
# ---------------------------------------------------------------------------


def _make_event(campaign, author, **overrides):
    from datetime import timedelta

    from django.contrib.gis.geos import Point
    from django.utils import timezone

    from tosca_api.apps.events.models import Event

    defaults = dict(
        campaign=campaign,
        title="Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=author,
    )
    defaults.update(overrides)
    return Event.objects.create(**defaults)


def _event_with_context_asset(campaign, django_user_model, *, status, path):
    """An Event whose context embeds an EditorJS-uploaded asset at ``path``."""
    from django.core.files.storage import default_storage

    from tosca_api.apps.geocontext.models import GeoContext

    author = django_user_model.objects.create_user(username=f"event-author-{path}")
    default_storage.save(path, ContentFile(_png_bytes()))
    context = GeoContext.objects.create(
        content={
            "blocks": [
                {"type": "image", "data": {"file": {"url": f"/media/{path}"}, "alt": "a pic"}}
            ]
        },
        created_by=author,
    )
    return _make_event(campaign, author, status=status, context=context)


def test_sync_event_assets_moves_only_that_events_assets(campaign, django_user_model, tmp_path):
    from tosca_api.apps.events.models import Event

    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    path = "geocontext/editorjs/evt-scope/pic.png"
    event = _event_with_context_asset(
        campaign, django_user_model, status=Event.Status.DRAFT, path=path
    )

    service, backends, storage_for_alias = _service(tmp_path)
    default_backend = storage_for_alias("default")
    default_backend.save(path, ContentFile(b"x"))
    default_backend.save("misc/other.png", ContentFile(b"y"))
    event_asset = _make_asset(
        path, campaign=campaign, owner_org=campaign.organization, size=1,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )
    other_asset = _make_asset(
        "misc/other.png", campaign=campaign, owner_org=campaign.organization, size=1,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )

    # Bypass the post_save signal here: it would run the *real* lifecycle
    # service against the real storages and leave nothing for the isolated
    # tmp_path-backed service under test to do. Signal wiring itself is
    # covered by test_media_lifecycle_signals.py.
    Event.objects.filter(pk=event.pk).update(status=Event.Status.PUBLISHED)
    event.refresh_from_db()

    entries = service.sync_event_assets(event)

    assert len(entries) == 1
    assert entries[0].action == ACTION_MOVED
    event_asset.refresh_from_db()
    other_asset.refresh_from_db()
    assert event_asset.storage_alias == MediaAsset.StorageAlias.PUBLIC
    # The campaign-only asset (not scoped to this event) is untouched.
    assert other_asset.storage_alias == MediaAsset.StorageAlias.DEFAULT


# ---------------------------------------------------------------------------
# Promotion / demotion (ticket 14: draft<->published under a public campaign
# must flip the storage alias private<->public via the sync entry points)
# ---------------------------------------------------------------------------


def test_sync_story_assets_promotes_hero_image_on_publish_under_public_campaign(
    campaign, django_user_model, tmp_path
):
    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    author = django_user_model.objects.create_user(username="hero-promo-author")
    story = GeoStory(
        title="Story", campaign=campaign, author=author, hero_image_alt="alt",
        status=GeoStory.Status.DRAFT,
    )
    story.hero_image.name = "geostories/promo/hero/image.png"
    story.save()
    assert story.hero_image_storage_alias == MediaAsset.StorageAlias.DEFAULT

    service, backends, storage_for_alias = _service(tmp_path)
    storage_for_alias("default").save(
        "geostories/promo/hero/image.png", ContentFile(b"hero-promo")
    )

    GeoStory.objects.filter(pk=story.pk).update(status=GeoStory.Status.PUBLISHED)
    story.refresh_from_db()

    entries = service.sync_story_assets(story)

    assert entries[0].action == ACTION_MOVED
    assert storage_for_alias("media_public").exists("geostories/promo/hero/image.png")
    assert not storage_for_alias("default").exists("geostories/promo/hero/image.png")
    story.refresh_from_db()
    assert story.hero_image_storage_alias == MediaAsset.StorageAlias.PUBLIC


def test_sync_story_assets_demotes_hero_image_on_unpublish_under_public_campaign(
    campaign, django_user_model, tmp_path
):
    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    author = django_user_model.objects.create_user(username="hero-demote-author")
    story = GeoStory(
        title="Story", campaign=campaign, author=author, hero_image_alt="alt",
        status=GeoStory.Status.PUBLISHED,
    )
    story.hero_image.name = "geostories/demote/hero/image.png"
    story.save()
    assert story.hero_image_storage_alias == MediaAsset.StorageAlias.PUBLIC

    service, backends, storage_for_alias = _service(tmp_path)
    storage_for_alias("media_public").save(
        "geostories/demote/hero/image.png", ContentFile(b"hero-demote")
    )

    GeoStory.objects.filter(pk=story.pk).update(status=GeoStory.Status.DRAFT)
    story.refresh_from_db()

    entries = service.sync_story_assets(story)

    assert entries[0].action == ACTION_MOVED
    assert storage_for_alias("default").exists("geostories/demote/hero/image.png")
    assert not storage_for_alias("media_public").exists("geostories/demote/hero/image.png")
    story.refresh_from_db()
    assert story.hero_image_storage_alias == MediaAsset.StorageAlias.DEFAULT


def test_sync_story_assets_promotes_editorjs_asset_on_publish_under_public_campaign(
    campaign, django_user_model, tmp_path
):
    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    path = "geocontext/editorjs/story-promo/pic.png"
    story = _story_with_context_asset(
        campaign, django_user_model, status=GeoStory.Status.DRAFT, path=path
    )

    service, backends, storage_for_alias = _service(tmp_path)
    storage_for_alias("default").save(path, ContentFile(b"x"))
    asset = _make_asset(
        path, campaign=campaign, owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )

    GeoStory.objects.filter(pk=story.pk).update(status=GeoStory.Status.PUBLISHED)
    story.refresh_from_db()

    entries = service.sync_story_assets(story)

    moved = [e for e in entries if e.asset_id == str(asset.id)]
    assert moved and moved[0].action == ACTION_MOVED
    asset.refresh_from_db()
    assert asset.storage_alias == MediaAsset.StorageAlias.PUBLIC


def test_sync_story_assets_demotes_editorjs_asset_on_unpublish_under_public_campaign(
    campaign, django_user_model, tmp_path
):
    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    path = "geocontext/editorjs/story-demote/pic.png"
    story = _story_with_context_asset(
        campaign, django_user_model, status=GeoStory.Status.PUBLISHED, path=path
    )

    service, backends, storage_for_alias = _service(tmp_path)
    storage_for_alias("media_public").save(path, ContentFile(b"x"))
    asset = _make_asset(
        path, campaign=campaign, owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.PUBLIC,
    )

    GeoStory.objects.filter(pk=story.pk).update(status=GeoStory.Status.DRAFT)
    story.refresh_from_db()

    entries = service.sync_story_assets(story)

    moved = [e for e in entries if e.asset_id == str(asset.id)]
    assert moved and moved[0].action == ACTION_MOVED
    asset.refresh_from_db()
    assert asset.storage_alias == MediaAsset.StorageAlias.DEFAULT


def test_sync_event_assets_promotes_editorjs_asset_on_publish_under_public_campaign(
    campaign, django_user_model, tmp_path
):
    from tosca_api.apps.events.models import Event

    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    path = "geocontext/editorjs/event-promo/pic.png"
    event = _event_with_context_asset(
        campaign, django_user_model, status=Event.Status.DRAFT, path=path
    )

    service, backends, storage_for_alias = _service(tmp_path)
    storage_for_alias("default").save(path, ContentFile(b"x"))
    asset = _make_asset(
        path, campaign=campaign, owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )

    Event.objects.filter(pk=event.pk).update(status=Event.Status.PUBLISHED)
    event.refresh_from_db()

    entries = service.sync_event_assets(event)

    assert len(entries) == 1
    assert entries[0].action == ACTION_MOVED
    asset.refresh_from_db()
    assert asset.storage_alias == MediaAsset.StorageAlias.PUBLIC


def test_sync_event_assets_demotes_editorjs_asset_on_unpublish_under_public_campaign(
    campaign, django_user_model, tmp_path
):
    from tosca_api.apps.events.models import Event

    campaign.visibility = Campaign.Visibility.PUBLIC
    campaign.save()
    path = "geocontext/editorjs/event-demote/pic.png"
    event = _event_with_context_asset(
        campaign, django_user_model, status=Event.Status.PUBLISHED, path=path
    )

    service, backends, storage_for_alias = _service(tmp_path)
    storage_for_alias("media_public").save(path, ContentFile(b"x"))
    asset = _make_asset(
        path, campaign=campaign, owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.PUBLIC,
    )

    Event.objects.filter(pk=event.pk).update(status=Event.Status.DRAFT)
    event.refresh_from_db()

    entries = service.sync_event_assets(event)

    assert len(entries) == 1
    assert entries[0].action == ACTION_MOVED
    asset.refresh_from_db()
    assert asset.storage_alias == MediaAsset.StorageAlias.DEFAULT
