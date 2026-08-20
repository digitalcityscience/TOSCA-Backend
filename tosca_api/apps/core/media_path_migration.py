"""
Backfill-all migration of ``MediaAsset`` storage objects onto the canonical
Garage path scheme (epic-11 PR2, §4 of
``docs/development/epic-11-campaign-ownership-visibility-garage-lifecycle-14082026.md``).

Design constraints from the ticket, and how this module satisfies them:

- **Backfill-all, not new-uploads-only.** Mixed legacy/new paths would
  complicate the archive lifecycle (PR3), so every asset with a resolvable
  canonical path (see ``media_paths.resolve_entity``) is migrated, not just
  future uploads.
- **``--dry-run`` default.** The management command wrapper defaults to
  dry-run; this module's ``plan()``/``apply()`` split makes that the natural
  shape (``plan`` never touches storage or the DB).
- **Batch/resumable.** ``apply()`` processes a bounded queryset slice per
  call; the command chunks ``plan()`` calls over the full table so a large
  asset table is never held in one Python list or one DB transaction.
- **Unique-constraint safe.** ``MediaAsset.storage_path`` is unique. Each
  asset's DB row is updated in its own transaction immediately after its
  object copy is verified, so a concurrent write racing on the *old* path is
  irrelevant (we've already moved past it) and a collision on the *new* path
  surfaces as an ``IntegrityError`` that is caught and recorded rather than
  aborting the batch.
- **Copy-then-verify-then-delete.** The source object is never deleted until
  the destination copy's size has been confirmed to match, so an
  interrupted run leaves the asset reachable at its *old* path (broken links
  are the failure mode being avoided, not the migration finishing 100%
  atomically -- copy+delete across two S3 objects cannot be atomic).
- **Safe on empty or populated environments.** ``plan()`` over an empty
  ``MediaAsset`` table yields nothing; there is no assumption that any rows
  exist.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Iterable

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction

from tosca_api.apps.core.media_paths import (
    canonical_storage_path,
    filename_from_legacy_path,
    resolve_entity,
)

# Planned/observed actions.
ACTION_WOULD_MOVE = "would-move"
ACTION_MOVED = "moved"
ACTION_ALREADY_CANONICAL = "already-canonical"
ACTION_UNRESOLVED = "unresolved"  # no campaign -> no canonical path possible
ACTION_FAILED = "failed"

STATUS_OK = "ok"
STATUS_FAILED = "failed"

_REPORT_FIELDS = ("asset_id", "old_path", "new_path", "alias", "action", "status", "detail")


@dataclass
class PathMigrationEntry:
    asset_id: str
    old_path: str
    new_path: str
    alias: str
    action: str
    status: str
    detail: str = ""


# Resolves a storage alias name ("default" | "media_public") to a Django
# Storage instance. The alias an asset lives under does not change --
# canonicalization only rewrites the *key* within the same bucket/alias, the
# private/public split is an orthogonal axis owned by the upload flow.
StorageResolver = Callable[[str], "object"]

# Resolves a MediaAsset's current storage alias (which bucket it lives in
# today). Injected because "how do I know which alias an existing row is in"
# depends on the same prefix-routing logic as media_migration.MediaMigrator,
# which the caller already has configured.
AliasResolver = Callable[["object"], str]


class MediaPathMigrator:
    def __init__(
        self,
        *,
        storage_for_alias: StorageResolver,
        alias_for_asset: AliasResolver,
    ) -> None:
        self._storage_for_alias = storage_for_alias
        self._alias_for_asset = alias_for_asset

    # ------------------------------------------------------------------
    # Planning (read-only)
    # ------------------------------------------------------------------

    def plan_one(self, asset) -> PathMigrationEntry:
        alias = self._alias_for_asset(asset)
        old_path = asset.storage_path
        resolved = resolve_entity(asset)
        if resolved is None:
            return PathMigrationEntry(
                str(asset.id), old_path, old_path, alias, ACTION_UNRESOLVED, STATUS_OK,
                "asset has no campaign -- no canonical path to move to",
            )
        new_path = canonical_storage_path(resolved, filename_from_legacy_path(old_path))
        if new_path == old_path:
            return PathMigrationEntry(
                str(asset.id), old_path, new_path, alias, ACTION_ALREADY_CANONICAL, STATUS_OK,
            )
        return PathMigrationEntry(
            str(asset.id), old_path, new_path, alias, ACTION_WOULD_MOVE, STATUS_OK,
        )

    def plan(self, assets: Iterable) -> list[PathMigrationEntry]:
        return [self.plan_one(asset) for asset in assets]

    # ------------------------------------------------------------------
    # Apply (copy -> verify -> update DB -> delete old object)
    # ------------------------------------------------------------------

    def apply_one(self, asset) -> PathMigrationEntry:
        planned = self.plan_one(asset)
        if planned.action not in (ACTION_WOULD_MOVE,):
            return planned

        alias = planned.alias
        old_path, new_path = planned.old_path, planned.new_path
        try:
            storage = self._storage_for_alias(alias)

            if not storage.exists(old_path):
                return PathMigrationEntry(
                    planned.asset_id, old_path, new_path, alias, ACTION_FAILED, STATUS_FAILED,
                    "source object missing at old_path",
                )
            source_size = storage.size(old_path)

            if storage.exists(new_path):
                dest_size = storage.size(new_path)
                if dest_size != source_size:
                    return PathMigrationEntry(
                        planned.asset_id, old_path, new_path, alias, ACTION_FAILED, STATUS_FAILED,
                        f"destination already exists with mismatched size "
                        f"({dest_size} != {source_size})",
                    )
                # Destination already has the right bytes (e.g. a prior run
                # copied but was interrupted before delete/DB update) --
                # treat as already copied and proceed to DB update + delete.
            else:
                with storage.open(old_path, "rb") as handle:
                    data = handle.read()
                storage.save(new_path, ContentFile(data, name=new_path))
                dest_size = storage.size(new_path)
                if dest_size != source_size:
                    return PathMigrationEntry(
                        planned.asset_id, old_path, new_path, alias, ACTION_FAILED, STATUS_FAILED,
                        f"post-copy size {dest_size} != source {source_size}",
                    )

            try:
                with transaction.atomic():
                    asset.storage_path = new_path
                    asset.save(update_fields=["storage_path"])
            except IntegrityError as exc:
                return PathMigrationEntry(
                    planned.asset_id, old_path, new_path, alias, ACTION_FAILED, STATUS_FAILED,
                    f"storage_path collision writing DB row: {exc!r}",
                )

            storage.delete(old_path)
            return PathMigrationEntry(
                planned.asset_id, old_path, new_path, alias, ACTION_MOVED, STATUS_OK,
            )
        except Exception as exc:  # partial-failure tolerance: record and continue
            return PathMigrationEntry(
                planned.asset_id, old_path, new_path, alias, ACTION_FAILED, STATUS_FAILED, repr(exc)
            )

    def apply(self, assets: Iterable) -> list[PathMigrationEntry]:
        return [self.apply_one(asset) for asset in assets]


# ----------------------------------------------------------------------
# Report serialization (matches media_migration's shape for tooling reuse)
# ----------------------------------------------------------------------


def summarize(entries: list[PathMigrationEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.action] = counts.get(entry.action, 0) + 1
    return counts


def report_to_json(entries: list[PathMigrationEntry]) -> str:
    import json

    return json.dumps([asdict(entry) for entry in entries], indent=2, sort_keys=True)
