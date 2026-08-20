"""
Move existing filesystem media into the configured Django storage backends.

Epic 11 flips ``DJANGO_STORAGE_BACKEND`` from ``filesystem`` to ``s3``, but the
storage change only affects *new* uploads. Files already written to the local
``MEDIA_ROOT`` must be copied into the destination buckets first, otherwise
previously published images 404 after the flip.

This module holds the migration logic as a backend-agnostic, dependency-injected
service so it can be exercised without the management-command layer. The command
in ``management/commands/migrate_media_to_storage.py`` is a thin CLI wrapper.

Design notes
------------

- **Routing.** EditorJS inline uploads live under ``geocontext/editorjs/`` and
  belong in the public (unsigned) bucket; everything else defaults to the
  private ``default`` bucket. The prefix map is the single source of truth,
  shared by migration and verification so both agree on where an object lives.
- **Idempotent.** An object already present at the destination with a matching
  byte size is skipped, so a re-run resumes a partially completed migration.
- **Partial-failure tolerant.** A failure on one object is recorded and the run
  continues; nothing aborts the whole batch.
- **Verifiable.** Every upload's destination size is checked against the source.
- **Reversible (with a caveat).** ``rollback`` deletes objects this run
  *created*; it deliberately does not touch objects it *overwrote*, since the
  prior content cannot be restored.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator

from django.core.files import File

# Source-path prefixes whose objects belong in the public (unsigned) bucket.
DEFAULT_PUBLIC_PREFIXES: tuple[str, ...] = ("geocontext/editorjs/",)

# Planned/observed actions.
ACTION_MIGRATED = "migrated"
ACTION_OVERWRITTEN = "overwritten"
ACTION_WOULD_MIGRATE = "would-migrate"
ACTION_SKIPPED = "skipped-exists"
ACTION_MISSING = "missing"
ACTION_WOULD_DELETE = "would-delete"
ACTION_DELETED = "deleted"
ACTION_FAILED = "failed"

# Per-object outcome.
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_MISMATCH = "mismatch"

_REPORT_FIELDS = ("path", "alias", "source_size", "action", "status", "detail")


@dataclass
class MigrationEntry:
    """One object's outcome, both a planned step and a report row."""

    path: str
    alias: str
    source_size: int
    action: str
    status: str
    detail: str = ""


# A resolver from a storage-alias name to a Django Storage instance. Injected so
# tests can supply temp-dir backends and production can pass ``storages``.
StorageResolver = Callable[[str], "object"]


class MediaMigrator:
    def __init__(
        self,
        *,
        source_root,
        storage_for_alias: StorageResolver,
        public_prefixes: tuple[str, ...] = DEFAULT_PUBLIC_PREFIXES,
        default_alias: str = "default",
        public_alias: str = "media_public",
    ) -> None:
        self.source_root = Path(source_root)
        self._storage_for_alias = storage_for_alias
        self._public_prefixes = tuple(public_prefixes)
        self._default_alias = default_alias
        self._public_alias = public_alias

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def alias_for(self, key: str) -> str:
        """Return the destination storage alias for a storage-relative key."""
        for prefix in self._public_prefixes:
            if key.startswith(prefix):
                return self._public_alias
        return self._default_alias

    def iter_source_objects(self) -> Iterator[tuple[str, int]]:
        """Yield ``(posix_key, size)`` for every file under the source root.

        Sorted so runs and reports are deterministic.
        """
        if not self.source_root.is_dir():
            return
        for path in sorted(self.source_root.rglob("*")):
            if path.is_file():
                key = path.relative_to(self.source_root).as_posix()
                yield key, path.stat().st_size

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def migrate(self, *, dry_run: bool = False, overwrite: bool = False) -> list[MigrationEntry]:
        return [
            self._migrate_one(key, size, dry_run=dry_run, overwrite=overwrite)
            for key, size in self.iter_source_objects()
        ]

    def _migrate_one(
        self, key: str, source_size: int, *, dry_run: bool, overwrite: bool
    ) -> MigrationEntry:
        alias = self.alias_for(key)
        try:
            storage = self._storage_for_alias(alias)
            exists = storage.exists(key)

            if exists and not overwrite:
                dest_size = storage.size(key)
                if dest_size == source_size:
                    return MigrationEntry(
                        key, alias, source_size, ACTION_SKIPPED, STATUS_OK, "already present"
                    )
                return MigrationEntry(
                    key,
                    alias,
                    source_size,
                    ACTION_SKIPPED,
                    STATUS_MISMATCH,
                    f"destination size {dest_size} != source {source_size}; "
                    "rerun with --overwrite to replace",
                )

            action = ACTION_OVERWRITTEN if exists else ACTION_MIGRATED
            if dry_run:
                return MigrationEntry(
                    key,
                    alias,
                    source_size,
                    ACTION_WOULD_MIGRATE,
                    STATUS_OK,
                    "would overwrite" if exists else "",
                )

            if exists:
                # Delete first so the key is reused verbatim rather than the
                # storage backend appending a uniqueness suffix.
                storage.delete(key)
            source_path = self.source_root / key
            with source_path.open("rb") as handle:
                saved_key = storage.save(key, File(handle, name=key))

            dest_size = storage.size(saved_key)
            if dest_size != source_size:
                return MigrationEntry(
                    key,
                    alias,
                    source_size,
                    action,
                    STATUS_MISMATCH,
                    f"post-upload size {dest_size} != source {source_size}",
                )
            detail = "" if saved_key == key else f"stored as {saved_key}"
            return MigrationEntry(key, alias, source_size, action, STATUS_OK, detail)
        except Exception as exc:  # partial-failure tolerance: record and continue
            return MigrationEntry(key, alias, source_size, ACTION_FAILED, STATUS_FAILED, repr(exc))

    # ------------------------------------------------------------------
    # Verification (missing-object detection over tracked assets)
    # ------------------------------------------------------------------

    def verify_tracked_assets(self) -> list[MigrationEntry]:
        """Report every ``MediaAsset`` whose object is absent at its destination."""
        from tosca_api.apps.core.models import MediaAsset

        missing: list[MigrationEntry] = []
        for asset in MediaAsset.objects.all().order_by("storage_path").iterator():
            key = asset.storage_path
            alias = self.alias_for(key)
            try:
                storage = self._storage_for_alias(alias)
                present = storage.exists(key)
            except Exception as exc:  # pragma: no cover - defensive
                missing.append(
                    MigrationEntry(key, alias, asset.size, ACTION_MISSING, STATUS_FAILED, repr(exc))
                )
                continue
            if not present:
                missing.append(
                    MigrationEntry(
                        key,
                        alias,
                        asset.size,
                        ACTION_MISSING,
                        STATUS_FAILED,
                        "tracked asset not found in destination storage",
                    )
                )
        return missing

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback(self, entries: list[MigrationEntry], *, dry_run: bool = False) -> list[MigrationEntry]:
        """Delete objects a prior run *created*.

        Only ``migrated`` (newly created) objects are removed. ``overwritten``
        objects are left in place — deleting them would not restore the content
        that was replaced, so rolling them back is unsafe.
        """
        results: list[MigrationEntry] = []
        for entry in entries:
            if entry.action != ACTION_MIGRATED or entry.status != STATUS_OK:
                continue
            key, alias = entry.path, entry.alias
            try:
                storage = self._storage_for_alias(alias)
                if dry_run:
                    results.append(
                        MigrationEntry(key, alias, entry.source_size, ACTION_WOULD_DELETE, STATUS_OK)
                    )
                    continue
                detail = ""
                if storage.exists(key):
                    storage.delete(key)
                else:
                    detail = "already absent"
                results.append(
                    MigrationEntry(key, alias, entry.source_size, ACTION_DELETED, STATUS_OK, detail)
                )
            except Exception as exc:  # pragma: no cover - defensive
                results.append(
                    MigrationEntry(key, alias, entry.source_size, ACTION_FAILED, STATUS_FAILED, repr(exc))
                )
        return results


# ----------------------------------------------------------------------
# Report serialization
# ----------------------------------------------------------------------


def summarize(entries: list[MigrationEntry]) -> dict[str, int]:
    """Count entries by ``action`` for a compact operator summary."""
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.action] = counts.get(entry.action, 0) + 1
    return counts


def report_to_json(entries: list[MigrationEntry]) -> str:
    return json.dumps([asdict(entry) for entry in entries], indent=2, sort_keys=True)


def report_to_csv(entries: list[MigrationEntry]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(_REPORT_FIELDS))
    writer.writeheader()
    for entry in entries:
        writer.writerow(asdict(entry))
    return buffer.getvalue()


def load_report(json_text: str) -> list[MigrationEntry]:
    """Rebuild entries from a JSON report (used for ``--rollback``)."""
    data = json.loads(json_text)
    return [MigrationEntry(**{field: row[field] for field in _REPORT_FIELDS}) for row in data]
