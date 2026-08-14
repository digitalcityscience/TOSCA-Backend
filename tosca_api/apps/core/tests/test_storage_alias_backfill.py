"""Tests for the storage_alias backfill data migration (epic-11 PR3).

Exercises ``backfill_storage_alias`` directly (not via a full migration
replay -- matches how the codebase already tests other RunPython data
migrations' logic through the wrapped function/module they call).
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as django_apps

from tosca_api.apps.core.models import MediaAsset

pytestmark = pytest.mark.django_db

_migration_module = importlib.import_module(
    "tosca_api.apps.core.migrations.0005_backfill_media_asset_storage_alias"
)


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


def test_backfill_routes_editorjs_prefixed_assets_to_public_alias():
    editorjs_asset = _make_asset("geocontext/editorjs/x/pic.png")
    other_asset = _make_asset("misc/other.png")
    assert editorjs_asset.storage_alias == MediaAsset.StorageAlias.DEFAULT  # model default

    _migration_module.backfill_storage_alias(apps=django_apps, schema_editor=None)

    editorjs_asset.refresh_from_db()
    other_asset.refresh_from_db()
    assert editorjs_asset.storage_alias == MediaAsset.StorageAlias.PUBLIC
    assert other_asset.storage_alias == MediaAsset.StorageAlias.DEFAULT
