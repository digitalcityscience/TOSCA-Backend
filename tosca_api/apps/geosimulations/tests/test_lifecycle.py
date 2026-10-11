"""File delivery and lifecycle (Task 12.6).

Runs against the configured test storage (Garage test buckets under S3).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.files.storage import storages
from django.core.management import call_command

from tosca_api.apps.geosimulations import lifecycle
from tosca_api.apps.geosimulations.importer import import_package
from tosca_api.apps.geosimulations.management.commands.configure_storage_cors import (
    RULE_ID,
    merged_rules,
)
from tosca_api.apps.geosimulations.models import SimulationRun, Status

from .helpers import org
from .package_helpers import write_real_package

pytestmark = pytest.mark.django_db

S3 = hasattr(storages["default"], "bucket_name")
needs_s3 = pytest.mark.skipif(not S3, reason="needs S3 storage")


@pytest.fixture
def run(tmp_path, django_capture_on_commit_callbacks):
    write_real_package(tmp_path / "package")
    run = import_package(tmp_path / "package", organization=org("dcs")).run
    yield run
    with django_capture_on_commit_callbacks(execute=True):
        for existing in SimulationRun.objects.all():
            existing.delete()


def _head(asset) -> dict:
    storage = storages[asset.storage_alias]
    return storage.connection.meta.client.head_object(
        Bucket=storage.bucket_name, Key=storage._normalize_name(asset.storage_path)
    )


def _set_status(run, status, capture):
    run.status = status
    with capture(execute=True):
        run.save()
    return {asset.storage_alias for asset in run.assets.all()}


def test_desired_alias_follows_the_run_status(run):
    assert lifecycle.desired_alias(run) == "default"
    run.status = Status.PUBLISHED
    assert lifecycle.desired_alias(run) == "media_public"
    run.status = Status.ARCHIVED
    assert lifecycle.desired_alias(run) == "media_archive"


@needs_s3
def test_uploads_carry_type_encoding_and_cache_headers(run):
    assets = {asset.original_name: asset for asset in run.assets.all()}

    tiles = _head(assets["bike_flow.pmtiles"])
    replay = _head(assets["trips.json.gz"])

    assert tiles["ContentType"] == "application/vnd.pmtiles"
    assert tiles["CacheControl"] == "private, max-age=3600"
    assert replay["ContentType"] == "application/json"
    assert replay["ContentEncoding"] == "gzip"


@needs_s3
def test_publishing_moves_files_and_keeps_their_headers(run, django_capture_on_commit_callbacks):
    paths = list(run.assets.values_list("storage_path", flat=True))

    assert _set_status(run, Status.PUBLISHED, django_capture_on_commit_callbacks) == {"media_public"}
    for path in paths:
        assert storages["media_public"].exists(path)
        assert not storages["default"].exists(path)
    tiles = run.assets.get(original_name="bike_flow.pmtiles")
    assert _head(tiles)["CacheControl"] == "private, max-age=3600"

    assert _set_status(run, Status.ARCHIVED, django_capture_on_commit_callbacks) == {"media_archive"}
    assert _set_status(run, Status.DRAFT, django_capture_on_commit_callbacks) == {"default"}
    assert all(storages["default"].exists(path) for path in paths)


def test_unrelated_edits_do_not_move_files(run, django_capture_on_commit_callbacks):
    run.title = "Renamed"
    with patch.object(lifecycle, "sync_run_storage") as sync:
        with django_capture_on_commit_callbacks(execute=True):
            run.save()

    assert not sync.called


def test_rolled_back_status_changes_do_not_move_files(run, django_capture_on_commit_callbacks):
    run.status = Status.PUBLISHED
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        run.save()

    assert len(callbacks) == 1  # the move waits for a commit that never happens here
    assert {asset.storage_alias for asset in run.assets.all()} == {"default"}


def test_a_failed_copy_keeps_the_file_where_it_was(run):
    asset = run.assets.first()

    with patch.object(lifecycle.storage, "copy_between", side_effect=OSError("bucket down")):
        result = lifecycle.move_asset(asset, "media_public")

    asset.refresh_from_db()
    assert (result.moved, asset.storage_alias) == (False, "default")
    assert "bucket down" in result.error
    assert storages["default"].exists(asset.storage_path)


def test_a_short_copy_is_not_trusted(run):
    asset = run.assets.first()

    with patch.object(lifecycle.storage, "copy_between", return_value=asset.size - 1):
        result = lifecycle.move_asset(asset, "media_public")

    asset.refresh_from_db()
    assert asset.storage_alias == "default"
    assert "expected" in result.error


def test_filesystem_aliases_sharing_one_media_root_are_not_copied(run, tmp_path, settings):
    asset = run.assets.first()
    filesystem = {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": str(tmp_path)}}
    settings.STORAGES = {**settings.STORAGES, "default": filesystem, "media_public": filesystem}

    with patch.object(lifecycle.storage, "copy_between") as copy, patch.object(lifecycle.storage, "delete") as delete:
        result = lifecycle.move_asset(asset, "media_public")

    assert result.moved and not copy.called and not delete.called


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def test_cors_rules_are_one_per_origin_and_merged_by_id_prefix():
    other = {"ID": "someone-else", "AllowedOrigins": ["https://other.test"], "AllowedMethods": ["PUT"]}
    stale = [
        {"ID": RULE_ID, "AllowedOrigins": ["https://old.test"], "AllowedMethods": ["GET"]},
        {"ID": f"{RULE_ID}-7", "AllowedOrigins": ["https://older.test"], "AllowedMethods": ["GET"]},
    ]

    rules = merged_rules([other, *stale], ["http://localhost:5173", "http://127.0.0.1:5173"])

    assert rules[0] == other
    assert [(rule["ID"], rule["AllowedOrigins"]) for rule in rules[1:]] == [
        (f"{RULE_ID}-1", ["http://localhost:5173"]),
        (f"{RULE_ID}-2", ["http://127.0.0.1:5173"]),
    ]
    assert rules[1]["AllowedHeaders"] == ["*"]
    assert "Content-Range" in rules[1]["ExposeHeaders"]


@needs_s3
def test_configure_storage_cors_applies_the_rule_to_every_bucket(capsys):
    call_command(
        "configure_storage_cors", "--origin", "http://localhost:5173", "--origin", "http://127.0.0.1:5173"
    )
    try:
        for alias in ("default", "media_public"):
            storage = storages[alias]
            rules = storage.connection.meta.client.get_bucket_cors(Bucket=storage.bucket_name)["CORSRules"]
            assert sorted(rule["AllowedOrigins"][0] for rule in rules) == [
                "http://127.0.0.1:5173",
                "http://localhost:5173",
            ]
        assert "Storage CORS configured." in capsys.readouterr().out
    finally:
        for alias in ("default", "media_public", "media_archive"):
            storage = storages[alias]
            storage.connection.meta.client.delete_bucket_cors(Bucket=storage.bucket_name)
