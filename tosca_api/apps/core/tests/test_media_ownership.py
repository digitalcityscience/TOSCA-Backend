"""Tests for MediaAsset ownership backfill (epic-11 PR1 §4/§6.1).

Covers ``core.media_ownership``: hero-image matching, EditorJS-content
matching, unmatched fallback, and the apply step's bulk write.
"""

from __future__ import annotations

import io
import importlib
import inspect
import re

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.core.media_ownership import (
    apply_backfill,
    plan_backfill,
    summarize,
)
from tosca_api.apps.core.models import MediaAsset
from tosca_api.apps.events.models import Event, EventSeries
from tosca_api.apps.geostories.models import GeoStory
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


def _image_bytes(*, width: int = 10, height: int = 10, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(10, 20, 30)).save(buf, format=fmt)
    return buf.getvalue()


def _write_storage_image(path: str) -> None:
    """Write a real decodable image to default_storage at ``path``.

    Feature.save() -> validate_and_normalize() -> _normalize_image()
    requires image blocks to reference a file that actually exists and
    decodes (see core.editorjs._read_storage_image_metadata), so plain
    fixture URLs aren't enough here.
    """
    default_storage.save(path, ContentFile(_image_bytes()))


def _make_asset(path: str, **overrides) -> MediaAsset:
    defaults = dict(
        storage_path=path,
        original_name=path.rsplit("/", 1)[-1],
        mime="image/png",
        width=10,
        height=10,
        size=100,
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


def test_plan_backfill_matches_hero_image(campaign, django_user_model):
    author = django_user_model.objects.create_user(username="author")
    story = GeoStory(
        title="Story",
        campaign=campaign,
        author=author,
        hero_image_alt="alt text",
    )
    story.hero_image.name = "geostories/x/hero/img.png"
    story.save()

    asset = _make_asset("geostories/x/hero/img.png")

    entries = plan_backfill(MediaAsset.objects.filter(id=asset.id))

    assert len(entries) == 1
    entry = entries[0]
    assert entry.matched_via == "hero_image"
    assert entry.campaign_id == str(campaign.id)
    assert entry.organization_id == str(campaign.organization_id)


def test_plan_backfill_matches_editorjs_content_via_geostory(campaign, django_user_model, tmp_path):
    from django.test import override_settings

    author = django_user_model.objects.create_user(username="author2")
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        _write_storage_image("geocontext/editorjs/z/pic.png")
        content = {
            "blocks": [
                {
                    "type": "image",
                    "data": {
                        "file": {"url": "https://cdn.test/media/geocontext/editorjs/z/pic.png"},
                        "caption": "",
                        "alt": "a pic",
                    },
                }
            ]
        }
        GeoStory.objects.create(title="Story2", campaign=campaign, author=author, content=content)
    asset = _make_asset("geocontext/editorjs/z/pic.png")

    entries = plan_backfill(MediaAsset.objects.filter(id=asset.id))

    assert len(entries) == 1
    assert entries[0].matched_via == "editorjs_content"
    assert entries[0].campaign_id == str(campaign.id)


def test_plan_backfill_matches_editorjs_content_via_event(campaign, django_user_model, tmp_path):
    from django.utils import timezone
    from datetime import timedelta
    from django.test import override_settings

    author = django_user_model.objects.create_user(username="author3")
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        _write_storage_image("geocontext/editorjs/e/evt.png")
        content = {
            "blocks": [
                {
                    "type": "image",
                    "data": {
                        "file": {"url": "/media/geocontext/editorjs/e/evt.png"},
                        "caption": "",
                        "alt": "event pic",
                    },
                }
            ]
        }
        now = timezone.now()
        Event.objects.create(
            campaign=campaign,
            title="Evt",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            organizer=author,
            content_override=content,
            location_mode="online",
            online_url="https://example.test",
        )
    asset = _make_asset("geocontext/editorjs/e/evt.png")

    entries = plan_backfill(MediaAsset.objects.filter(id=asset.id))

    assert entries[0].matched_via == "editorjs_content"
    assert entries[0].campaign_id == str(campaign.id)


def test_plan_backfill_matches_editorjs_content_via_event_series_default_content(
    campaign, django_user_model, tmp_path
):
    from django.test import override_settings
    from django.utils import timezone
    from datetime import timedelta

    author = django_user_model.objects.create_user(username="author4")
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        _write_storage_image("geocontext/editorjs/s/series.png")
        content = {
            "blocks": [
                {
                    "type": "image",
                    "data": {
                        "file": {"url": "/media/geocontext/editorjs/s/series.png"},
                        "caption": "",
                        "alt": "series pic",
                    },
                }
            ]
        }
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        EventSeries.objects.create(
            campaign=campaign,
            default_content=content,
            name="Series",
            created_by=author,
            series_mode=EventSeries.SeriesMode.MANUAL_BATCH,
            start_date=now.date(),
            start_time=now.time(),
            end_time=(now + timedelta(hours=1)).time(),
            timezone="Europe/Berlin",
        )
    asset = _make_asset("geocontext/editorjs/s/series.png")

    entries = plan_backfill(MediaAsset.objects.filter(id=asset.id))

    assert entries[0].matched_via == "editorjs_content"
    assert entries[0].campaign_id == str(campaign.id)


def test_plan_backfill_leaves_unmatched_assets_unresolved():
    asset = _make_asset("orphan/unlinked.png")

    entries = plan_backfill(MediaAsset.objects.filter(id=asset.id))

    assert len(entries) == 1
    assert entries[0].matched_via == "unmatched"
    assert entries[0].campaign_id is None
    assert entries[0].organization_id is None


def test_apply_backfill_writes_only_resolved_entries(campaign, django_user_model):
    author = django_user_model.objects.create_user(username="author5")
    story = GeoStory(
        title="Story3",
        campaign=campaign,
        author=author,
        hero_image_alt="alt",
    )
    story.hero_image.name = "geostories/y/hero/img2.png"
    story.save()

    matched = _make_asset("geostories/y/hero/img2.png")
    unmatched = _make_asset("orphan/other.png")

    entries = plan_backfill(MediaAsset.objects.filter(id__in=[matched.id, unmatched.id]))
    updated = apply_backfill(entries)

    assert updated == 1
    matched.refresh_from_db()
    unmatched.refresh_from_db()
    assert matched.campaign_id == campaign.id
    assert matched.owner_org_id == campaign.organization_id
    assert unmatched.campaign_id is None
    assert unmatched.owner_org_id is None


def test_migration_backfill_uses_historical_app_registry():
    """The old migration must resolve every model it needs from the historical
    ``apps`` registry it's given, not from today's live model imports.

    This can no longer be exercised by actually running the migration: the
    columns it reads (``GeoStory.context_id``, ``Event.context_id``,
    ``EventSeries.default_context_id``) and the ``GeoContext`` model itself
    are dropped by later migrations in this same series, so the test
    database -- fully migrated for every other test -- no longer has them.
    Executing the function for real would require a separate historical-state
    database frozen at migration 0003, which this suite has no tooling for.
    Instead, statically verify the function still requests every model it
    historically depended on via ``apps.get_model(...)`` (so a database at
    this migration's state can still be upgraded) and never imports today's
    ``core.media_ownership`` helpers (which query columns this migration
    predates).
    """
    migration = importlib.import_module(
        "tosca_api.apps.core.migrations.0003_backfill_media_asset_ownership"
    )
    source = inspect.getsource(migration.backfill_media_asset_ownership)

    requested_models = set(re.findall(r'apps\.get_model\("(\w+)", "(\w+)"\)', source))
    assert requested_models == {
        ("campaigns", "Campaign"),
        ("core", "MediaAsset"),
        ("events", "Event"),
        ("events", "EventSeries"),
        ("geocontext", "GeoContext"),
        ("geostories", "GeoStory"),
    }
    assert "core.media_ownership" not in source


def test_plan_backfill_only_considers_assets_missing_campaign_by_default(
    campaign, django_user_model
):
    author = django_user_model.objects.create_user(username="author6")
    other_campaign = Campaign.objects.create(
        title="Other", created_by=author, organization=campaign.organization
    )
    already_linked = _make_asset("already/linked.png", campaign=other_campaign)

    entries = plan_backfill()

    ids = {e.asset_id for e in entries}
    assert str(already_linked.id) not in ids


def test_summarize_counts_by_match_type():
    _make_asset("orphan/a.png")
    _make_asset("orphan/b.png")

    entries = plan_backfill()

    counts = summarize(entries)
    assert counts.get("unmatched", 0) >= 2
