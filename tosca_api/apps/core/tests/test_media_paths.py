"""Tests for the canonical Garage storage path scheme (epic-11 PR2 §4).

Covers ``core.media_paths``: path construction and entity resolution
(hero image, EditorJS content via story/event, campaign-only fallback,
unassigned/orphan assets).
"""

from __future__ import annotations

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import override_settings

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.core.media_paths import (
    KIND_EVENT,
    KIND_MISC,
    KIND_STORY,
    ResolvedEntity,
    canonical_storage_path,
    filename_from_legacy_path,
    resolve_entity,
)
from tosca_api.apps.core.models import MediaAsset
from tosca_api.apps.events.models import Event
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.geostories.models import GeoStory
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


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


def test_filename_from_legacy_path_extracts_trailing_segment():
    assert filename_from_legacy_path("geostories/x/hero/img.png") == "img.png"
    assert filename_from_legacy_path("flat.png") == "flat.png"


def test_canonical_storage_path_for_story():
    resolved = ResolvedEntity("acme", "camp-1", KIND_STORY, "story-1")
    assert (
        canonical_storage_path(resolved, "img.png")
        == "orgs/acme/campaigns/camp-1/stories/story-1/img.png"
    )


def test_canonical_storage_path_for_event():
    resolved = ResolvedEntity("acme", "camp-1", KIND_EVENT, "event-1")
    assert (
        canonical_storage_path(resolved, "img.png")
        == "orgs/acme/campaigns/camp-1/events/event-1/img.png"
    )


def test_canonical_storage_path_for_misc_has_no_entity_segment():
    resolved = ResolvedEntity("acme", "camp-1", KIND_MISC, None)
    assert (
        canonical_storage_path(resolved, "img.png") == "orgs/acme/campaigns/camp-1/misc/img.png"
    )


def test_resolve_entity_returns_none_for_unassigned_asset():
    asset = _make_asset("orphan/unlinked.png")

    assert resolve_entity(asset) is None


def test_resolve_entity_matches_hero_image(campaign, django_user_model):
    author = django_user_model.objects.create_user(username="author")
    story = GeoStory(title="Story", campaign=campaign, author=author, hero_image_alt="alt")
    story.hero_image.name = "geostories/x/hero/img.png"
    story.save()
    asset = _make_asset("geostories/x/hero/img.png", campaign=campaign, owner_org=campaign.organization)

    resolved = resolve_entity(asset)

    assert resolved is not None
    assert resolved.kind == KIND_STORY
    assert resolved.entity_id == str(story.id)
    assert resolved.campaign_id == str(campaign.id)
    assert resolved.org_slug == campaign.organization.slug


def test_resolve_entity_matches_editorjs_content_via_geostory(campaign, django_user_model, tmp_path):
    author = django_user_model.objects.create_user(username="author2")
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        default_storage.save("geocontext/editorjs/z/pic.png", ContentFile(b"x"))
        context = GeoContext.objects.create(
            content={
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
            },
            created_by=author,
        )
        story = GeoStory.objects.create(
            title="Story2", campaign=campaign, author=author, context=context
        )
        asset = _make_asset(
            "geocontext/editorjs/z/pic.png", campaign=campaign, owner_org=campaign.organization
        )

        resolved = resolve_entity(asset)

    assert resolved is not None
    assert resolved.kind == KIND_STORY
    assert resolved.entity_id == str(story.id)


def test_resolve_entity_matches_editorjs_content_via_event(campaign, django_user_model, tmp_path):
    from django.utils import timezone
    from datetime import timedelta

    author = django_user_model.objects.create_user(username="author3")
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        default_storage.save("geocontext/editorjs/e/evt.png", ContentFile(b"x"))
        context = GeoContext.objects.create(
            content={
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
            },
            created_by=author,
        )
        now = timezone.now()
        event = Event.objects.create(
            campaign=campaign,
            title="Evt",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            organizer=author,
            context=context,
            location_mode="online",
            online_url="https://example.test",
        )
        asset = _make_asset(
            "geocontext/editorjs/e/evt.png", campaign=campaign, owner_org=campaign.organization
        )

        resolved = resolve_entity(asset)

    assert resolved is not None
    assert resolved.kind == KIND_EVENT
    assert resolved.entity_id == str(event.id)


def test_resolve_entity_falls_back_to_misc_when_campaign_known_but_no_entity(
    campaign, django_user_model
):
    # Campaign linked directly (as PR1 backfill / normal upload flow would
    # set it) but no GeoStory/Event/GeoContext ties this asset to a
    # specific entity.
    asset = _make_asset(
        "some/unrelated/path.png", campaign=campaign, owner_org=campaign.organization
    )

    resolved = resolve_entity(asset)

    assert resolved is not None
    assert resolved.kind == KIND_MISC
    assert resolved.entity_id is None
    assert resolved.campaign_id == str(campaign.id)
