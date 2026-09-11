from __future__ import annotations

import io
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management import call_command
from django.test import override_settings
from PIL import Image

from tosca_api.apps.core.management.commands.reconcile_editorjs_media import (
    EDITORJS_ROOT,
    _iter_storage_keys,
)
from tosca_api.apps.core.models import MediaAsset
from tosca_api.settings.base import build_storage_config


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (240, 180), color=(20, 40, 60)).save(buffer, format="PNG")
    return buffer.getvalue()


def _storage_settings(tmp_path):
    return {
        alias: {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {
                "location": str(tmp_path / alias),
                "base_url": f"/{alias}/",
            },
        }
        for alias in ("default", "media_public", "media_archive")
    }


@pytest.mark.django_db
def test_reconcile_is_dry_run_by_default_and_apply_creates_metadata(tmp_path):
    path = "geocontext/editorjs/context-id/orphan.png"
    with override_settings(STORAGES=_storage_settings(tmp_path)):
        storages["media_public"].save(path, ContentFile(_png_bytes()))

        dry_run_output = StringIO()
        call_command("reconcile_editorjs_media", stdout=dry_run_output)
        assert "would_create=1" in dry_run_output.getvalue()
        assert not MediaAsset.objects.filter(storage_path=path).exists()

        apply_output = StringIO()
        call_command("reconcile_editorjs_media", "--apply", stdout=apply_output)

    asset = MediaAsset.objects.get(storage_path=path)
    assert asset.storage_alias == MediaAsset.StorageAlias.PUBLIC
    assert asset.mime == "image/png"
    assert (asset.width, asset.height) == (240, 180)
    assert asset.size == len(_png_bytes())
    assert "created=1" in apply_output.getvalue()


@pytest.mark.django_db
def test_reconcile_repairs_an_unambiguous_storage_alias(tmp_path):
    path = "geocontext/editorjs/context-id/moved.png"
    MediaAsset.objects.create(
        storage_path=path,
        original_name="moved.png",
        mime="image/png",
        width=240,
        height=180,
        size=len(_png_bytes()),
        storage_alias=MediaAsset.StorageAlias.DEFAULT,
    )

    with override_settings(STORAGES=_storage_settings(tmp_path)):
        storages["media_public"].save(path, ContentFile(_png_bytes()))
        call_command("reconcile_editorjs_media", "--apply")

    asset = MediaAsset.objects.get(storage_path=path)
    assert asset.storage_alias == MediaAsset.StorageAlias.PUBLIC


def _fake_bucket(keys):
    def filter(Prefix):  # noqa: A002 - matches boto3's ObjectsCollection.filter kwarg
        return [SimpleNamespace(key=key) for key in keys if key.startswith(Prefix)]

    return SimpleNamespace(objects=SimpleNamespace(filter=filter))


def test_iter_storage_keys_strips_s3_storage_location_prefix():
    """The private alias's S3 ``location`` (MEDIA_PRIVATE_PREFIX, e.g.
    "media/originals" in production) is invisible to Storage.save/listdir
    callers. The fast flat-listing path for S3Storage must replicate that:
    search under the real, location-prefixed key, but yield keys relative to
    ``directory`` so they match what MediaAsset.storage_path and the
    FileSystemStorage fallback both use -- otherwise the command silently
    finds nothing on any alias with a non-empty location.
    """
    s3_storages = build_storage_config(
        "s3",
        bucket_name="test-bucket",
        public_bucket_name="test-bucket-public",
        endpoint_url="https://garage.example.org",
        region_name="garage",
        access_key="test-access-key",
        secret_key="test-secret-key",
        location="media/originals",
    )
    with override_settings(STORAGES=s3_storages):
        storage = storages[MediaAsset.StorageAlias.DEFAULT]
        storage._bucket = _fake_bucket(
            [
                "media/originals/geocontext/editorjs/ctx/one.png",
                "media/originals/geocontext/editorjs/ctx/two.png",
                "media/originals/geocontext/editorjs/ctx/",
                "media/originals/other/ignored.png",
            ]
        )

        keys = sorted(_iter_storage_keys(storage, EDITORJS_ROOT))

    assert keys == [
        "geocontext/editorjs/ctx/one.png",
        "geocontext/editorjs/ctx/two.png",
    ]
