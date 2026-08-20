"""Tests for the canonical-path backfill-all migration (epic-11 PR2 §4).

Covers ``core.media_path_migration.MediaPathMigrator``: dry-run planning,
copy-then-verify-then-delete apply, already-canonical no-ops, unresolved
(no-campaign) skip, and destination-exists resume behavior. Also covers the
``migrate_media_paths`` management command's dry-run default and batching.
"""

from __future__ import annotations

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management import call_command

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.core.media_path_migration import (
    ACTION_ALREADY_CANONICAL,
    ACTION_MOVED,
    ACTION_UNRESOLVED,
    ACTION_WOULD_MOVE,
    MediaPathMigrator,
)
from tosca_api.apps.core.models import MediaAsset
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


def _migrator(tmp_path):
    root = tmp_path / "default"
    root.mkdir()
    backend = FileSystemStorage(location=str(root))
    migrator = MediaPathMigrator(
        storage_for_alias=lambda alias: backend,
        alias_for_asset=lambda asset: "default",
    )
    return migrator, backend


def test_plan_one_reports_would_move_for_resolvable_misc_asset(campaign, tmp_path):
    migrator, backend = _migrator(tmp_path)
    asset = _make_asset("legacy/path.png", campaign=campaign, owner_org=campaign.organization)

    entry = migrator.plan_one(asset)

    assert entry.action == ACTION_WOULD_MOVE
    assert entry.new_path == f"orgs/acme/campaigns/{campaign.id}/misc/path.png"
    assert entry.old_path == "legacy/path.png"


def test_plan_one_reports_unresolved_for_orphan_asset(tmp_path):
    migrator, backend = _migrator(tmp_path)
    asset = _make_asset("orphan/unlinked.png")

    entry = migrator.plan_one(asset)

    assert entry.action == ACTION_UNRESOLVED
    assert entry.new_path == entry.old_path


def test_plan_one_reports_already_canonical_when_path_matches(campaign, tmp_path):
    migrator, backend = _migrator(tmp_path)
    canonical = f"orgs/acme/campaigns/{campaign.id}/misc/path.png"
    asset = _make_asset(canonical, campaign=campaign, owner_org=campaign.organization)

    entry = migrator.plan_one(asset)

    assert entry.action == ACTION_ALREADY_CANONICAL


def test_apply_one_copies_bytes_updates_db_and_deletes_source(campaign, tmp_path):
    migrator, backend = _migrator(tmp_path)
    backend.save("legacy/path.png", ContentFile(b"hello"))
    asset = _make_asset(
        "legacy/path.png", campaign=campaign, owner_org=campaign.organization, size=5
    )

    entry = migrator.apply_one(asset)

    assert entry.action == ACTION_MOVED
    assert entry.status == "ok"
    new_path = f"orgs/acme/campaigns/{campaign.id}/misc/path.png"
    assert backend.exists(new_path)
    with backend.open(new_path, "rb") as fh:
        assert fh.read() == b"hello"
    assert not backend.exists("legacy/path.png")

    asset.refresh_from_db()
    assert asset.storage_path == new_path


def test_apply_one_leaves_orphan_asset_untouched(tmp_path):
    migrator, backend = _migrator(tmp_path)
    backend.save("orphan/unlinked.png", ContentFile(b"hello"))
    asset = _make_asset("orphan/unlinked.png")

    entry = migrator.apply_one(asset)

    assert entry.action == ACTION_UNRESOLVED
    assert backend.exists("orphan/unlinked.png")
    asset.refresh_from_db()
    assert asset.storage_path == "orphan/unlinked.png"


def test_apply_one_fails_when_source_object_missing(campaign, tmp_path):
    migrator, backend = _migrator(tmp_path)
    # DB row exists but the backing object was never written -- simulates
    # drift between MediaAsset and storage.
    asset = _make_asset(
        "legacy/missing.png", campaign=campaign, owner_org=campaign.organization
    )

    entry = migrator.apply_one(asset)

    assert entry.status == "failed"
    assert "missing" in entry.detail
    asset.refresh_from_db()
    assert asset.storage_path == "legacy/missing.png"


def test_apply_one_is_idempotent_when_destination_already_copied(campaign, tmp_path):
    """A prior interrupted run copied the object but never updated the DB row
    (e.g. crashed between save() and asset.save()). Re-running must not
    error or double-copy -- it proceeds straight to the DB update + source
    delete."""
    migrator, backend = _migrator(tmp_path)
    backend.save("legacy/path.png", ContentFile(b"hello"))
    new_path = None
    asset = _make_asset(
        "legacy/path.png", campaign=campaign, owner_org=campaign.organization, size=5
    )
    new_path = f"orgs/acme/campaigns/{campaign.id}/misc/path.png"
    backend.save(new_path, ContentFile(b"hello"))  # simulate the prior partial copy

    entry = migrator.apply_one(asset)

    assert entry.action == ACTION_MOVED
    assert entry.status == "ok"
    asset.refresh_from_db()
    assert asset.storage_path == new_path
    assert not backend.exists("legacy/path.png")


def test_apply_one_fails_on_size_mismatch_with_existing_destination(campaign, tmp_path):
    migrator, backend = _migrator(tmp_path)
    backend.save("legacy/path.png", ContentFile(b"hello"))
    asset = _make_asset(
        "legacy/path.png", campaign=campaign, owner_org=campaign.organization, size=5
    )
    new_path = f"orgs/acme/campaigns/{campaign.id}/misc/path.png"
    backend.save(new_path, ContentFile(b"different-length-content"))

    entry = migrator.apply_one(asset)

    assert entry.status == "failed"
    assert "mismatched size" in entry.detail
    # Source untouched -- never delete on an unverified destination.
    assert backend.exists("legacy/path.png")
    asset.refresh_from_db()
    assert asset.storage_path == "legacy/path.png"


def test_command_defaults_to_dry_run_and_writes_nothing(campaign, capsys):
    default_storage.save("legacy/cmd.png", ContentFile(b"hi"))
    asset = _make_asset(
        "legacy/cmd.png", campaign=campaign, owner_org=campaign.organization, size=2
    )

    call_command("migrate_media_paths")

    asset.refresh_from_db()
    assert asset.storage_path == "legacy/cmd.png"
    assert default_storage.exists("legacy/cmd.png")
    new_path = f"orgs/acme/campaigns/{campaign.id}/misc/cmd.png"
    assert not default_storage.exists(new_path)


def test_command_apply_moves_asset_and_batches(campaign, django_user_model):
    default_storage.save("legacy/one.png", ContentFile(b"one"))
    default_storage.save("legacy/two.png", ContentFile(b"two"))
    a1 = _make_asset(
        "legacy/one.png", campaign=campaign, owner_org=campaign.organization, size=3
    )
    a2 = _make_asset(
        "legacy/two.png", campaign=campaign, owner_org=campaign.organization, size=3
    )

    call_command("migrate_media_paths", "--apply", "--batch-size", "1")

    a1.refresh_from_db()
    a2.refresh_from_db()
    assert a1.storage_path == f"orgs/acme/campaigns/{campaign.id}/misc/one.png"
    assert a2.storage_path == f"orgs/acme/campaigns/{campaign.id}/misc/two.png"
    assert default_storage.exists(a1.storage_path)
    assert default_storage.exists(a2.storage_path)
    assert not default_storage.exists("legacy/one.png")
    assert not default_storage.exists("legacy/two.png")


def test_command_is_safe_on_empty_table():
    # No MediaAsset rows at all -- must not raise.
    call_command("migrate_media_paths", "--apply")
