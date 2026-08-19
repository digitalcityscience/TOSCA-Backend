"""Tests for the ``backfill_media_aliases`` management command (ticket 15).

Recomputes the desired alias for every campaign's media (S2 truth table, via
``MediaLifecycleService.sync_campaign_assets``) and relocates anything that
is currently mis-aliased -- e.g. a private/draft story's image left sitting
in ``media_public`` from before ticket 13 closed the upload-time gap.

Under the local ``filesystem`` storage backend every alias mirrors the same
directory on disk (``build_storage_config``'s documented dev convenience),
so a real move would collapse source and destination onto one path. These
tests instead point ``settings.STORAGES`` at three distinct temp
directories, mirroring the isolation ``test_media_lifecycle.py``'s
``_service`` fixture already uses for the same reason.
"""

from __future__ import annotations

import json

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management import call_command

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.core.models import MediaAsset
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolated_storage_aliases(settings, tmp_path):
    """Give ``default``/``media_public``/``media_archive`` separate directories."""
    aliases = ("default", "media_public", "media_archive")
    storages_config = dict(settings.STORAGES)
    for alias in aliases:
        root = tmp_path / alias
        root.mkdir(parents=True, exist_ok=True)
        storages_config[alias] = {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(root)},
        }
    settings.STORAGES = storages_config


@pytest.fixture
def org(db):
    return Organization.objects.create(slug="backfill-org", name="Backfill Org")


@pytest.fixture
def campaign(org, django_user_model):
    user = django_user_model.objects.create_user(username="backfill-owner")
    return Campaign.objects.create(title="Backfill Camp", created_by=user, organization=org)


def _wrongly_public_asset(campaign, path: str = "misc/legacy.png") -> MediaAsset:
    """A private/draft-campaign asset that legacy upload code put in ``media_public``."""
    storages["media_public"].save(path, ContentFile(b"legacy-bytes"))
    return MediaAsset.objects.create(
        storage_path=path,
        original_name=path.rsplit("/", 1)[-1],
        mime="image/png",
        width=10,
        height=10,
        size=12,
        campaign=campaign,
        owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.PUBLIC,
    )


def test_dry_run_reports_would_move_without_touching_storage(campaign):
    asset = _wrongly_public_asset(campaign)

    call_command("backfill_media_aliases")

    asset.refresh_from_db()
    assert asset.storage_alias == MediaAsset.StorageAlias.PUBLIC
    assert storages["media_public"].exists("misc/legacy.png")
    assert not storages["default"].exists("misc/legacy.png")


def test_apply_relocates_mis_aliased_asset(campaign):
    asset = _wrongly_public_asset(campaign)

    call_command("backfill_media_aliases", "--apply")

    asset.refresh_from_db()
    assert asset.storage_alias == MediaAsset.StorageAlias.DEFAULT
    assert storages["default"].exists("misc/legacy.png")
    assert not storages["media_public"].exists("misc/legacy.png")


def test_apply_is_idempotent_on_rerun(campaign):
    _wrongly_public_asset(campaign)

    call_command("backfill_media_aliases", "--apply")
    call_command("backfill_media_aliases", "--apply")  # must be a no-op, not an error

    asset = MediaAsset.objects.get(storage_path="misc/legacy.png")
    assert asset.storage_alias == MediaAsset.StorageAlias.DEFAULT
    assert storages["default"].exists("misc/legacy.png")


def test_apply_writes_json_report(campaign, tmp_path):
    _wrongly_public_asset(campaign)
    report_path = tmp_path / "report.json"

    call_command("backfill_media_aliases", "--apply", "--report", str(report_path))

    entries = json.loads(report_path.read_text(encoding="utf-8"))
    assert any(e["action"] == "moved" for e in entries)


def test_correctly_aliased_asset_is_left_untouched(campaign):
    path = "misc/already-fine.png"
    storages["default"].save(path, ContentFile(b"fine-bytes"))
    asset = MediaAsset.objects.create(
        storage_path=path,
        original_name="already-fine.png",
        mime="image/png",
        width=10,
        height=10,
        size=11,
        campaign=campaign,
        owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )

    call_command("backfill_media_aliases", "--apply")

    asset.refresh_from_db()
    assert asset.storage_alias == MediaAsset.StorageAlias.DEFAULT
    assert storages["default"].exists(path)


def test_no_object_deleted_without_verified_copy_on_source_missing(campaign):
    """A source object missing at the old alias is reported failed, not silently dropped."""
    asset = MediaAsset.objects.create(
        storage_path="misc/gone.png",
        original_name="gone.png",
        mime="image/png",
        width=10,
        height=10,
        size=5,
        campaign=campaign,
        owner_org=campaign.organization,
        storage_alias=MediaAsset.StorageAlias.PUBLIC,
    )

    call_command("backfill_media_aliases", "--apply")

    asset.refresh_from_db()
    assert asset.storage_alias == MediaAsset.StorageAlias.PUBLIC


def test_dry_run_is_default_apply_requires_explicit_flag(campaign):
    _wrongly_public_asset(campaign)

    call_command("backfill_media_aliases")  # no --apply

    asset = MediaAsset.objects.get(storage_path="misc/legacy.png")
    assert asset.storage_alias == MediaAsset.StorageAlias.PUBLIC
