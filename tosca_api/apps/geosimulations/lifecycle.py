"""Keep a run's files in the bucket that matches its status.

Published runs live in ``media_public``, archived runs in ``media_archive``
(when configured), everything else in the private ``default`` alias, the same
buckets ``core.media_lifecycle`` uses for other media. None of these buckets is
anonymously readable; the split keeps lifecycle tooling (backups, archive
retention, a future CDN bucket) consistent across TOSCA.

Moves are copy → verify size → update the row → delete the source, so an
interrupted move leaves every file readable at its recorded alias.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings

from tosca_api.apps.core.models import MediaAsset

from . import storage
from .models import SimulationAsset, SimulationRun, Status

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MoveResult:
    asset_id: str
    old_alias: str
    new_alias: str
    moved: bool
    error: str = ""


def desired_alias(run: SimulationRun) -> str:
    if run.status == Status.PUBLISHED:
        return MediaAsset.StorageAlias.PUBLIC
    if run.status == Status.ARCHIVED and MediaAsset.StorageAlias.ARCHIVE in settings.STORAGES:
        return MediaAsset.StorageAlias.ARCHIVE
    return MediaAsset.StorageAlias.DEFAULT


def move_asset(asset: SimulationAsset, target_alias: str) -> MoveResult:
    old_alias = asset.storage_alias
    if old_alias == target_alias:
        return MoveResult(str(asset.pk), old_alias, target_alias, moved=False)
    try:
        if not storage.same_place(old_alias, target_alias):
            copied_size = storage.copy_between(old_alias, target_alias, asset.storage_path)
            if copied_size != asset.size:
                return MoveResult(
                    str(asset.pk), old_alias, target_alias, moved=False,
                    error=f"copy has {copied_size} bytes, expected {asset.size}",
                )
        SimulationAsset.objects.filter(pk=asset.pk).update(storage_alias=target_alias)
        if not storage.same_place(old_alias, target_alias):
            storage.delete(old_alias, asset.storage_path)
    except Exception as exc:  # keep moving the run's other files; report this one
        logger.exception("Moving simulation asset %s to %s failed", asset.pk, target_alias)
        return MoveResult(str(asset.pk), old_alias, target_alias, moved=False, error=repr(exc))
    return MoveResult(str(asset.pk), old_alias, target_alias, moved=True)


def sync_run_storage(run: SimulationRun) -> list[MoveResult]:
    target = desired_alias(run)
    results = [move_asset(asset, target) for asset in run.assets.all()]
    failures = [result for result in results if result.error]
    if failures:
        logger.error(
            "Run %s storage sync to %s: %d of %d files failed", run.pk, target, len(failures), len(results)
        )
    return results
