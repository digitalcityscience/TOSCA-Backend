"""
Copy existing filesystem media into the configured Django storage backends.

Run this **before** flipping ``DJANGO_STORAGE_BACKEND`` to ``s3`` in production
(or right after, while the old files still exist on disk). The storage change
only affects new uploads; without this migration, already-published images 404
after the flip.

The logic lives in :mod:`tosca_api.apps.core.media_migration`; this command is a
thin CLI wrapper.

Usage
-----

Dry-run against the default ``MEDIA_ROOT`` (writes nothing)::

    python manage.py migrate_media_to_storage --dry-run

Migrate and write a JSON report (safe to re-run; already-present objects are
skipped)::

    python manage.py migrate_media_to_storage --report media-migration.json

Verify every tracked ``MediaAsset`` resolves at its destination::

    python manage.py migrate_media_to_storage --verify --dry-run

Undo a migration using its report (deletes objects this tool created)::

    python manage.py migrate_media_to_storage --rollback media-migration.json
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.core.media_migration import (
    MediaMigrator,
    load_report,
    report_to_csv,
    report_to_json,
    summarize,
)


class Command(BaseCommand):
    help = (
        "Copy existing filesystem media into the configured storage backends "
        "so published images survive a DJANGO_STORAGE_BACKEND flip. Idempotent, "
        "supports --dry-run, --verify, and --rollback."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--source",
            default=None,
            help="Filesystem root to migrate from (default: settings.MEDIA_ROOT).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would happen without writing to any backend.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace destination objects whose size differs from the source.",
        )
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Also report any tracked MediaAsset missing at its destination.",
        )
        parser.add_argument(
            "--rollback",
            default=None,
            metavar="REPORT",
            help="Delete objects created by a prior run, using its JSON report.",
        )
        parser.add_argument(
            "--report",
            default=None,
            help="Write the outcome report to this path (.csv or .json).",
        )
        parser.add_argument(
            "--format",
            choices=["json", "csv"],
            default="json",
            help="Report format when --report has no recognised extension.",
        )

    # ------------------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:
        source_root = options["source"] or getattr(settings, "MEDIA_ROOT", None)
        if not source_root:
            raise CommandError("No source root: pass --source or configure MEDIA_ROOT.")

        migrator = MediaMigrator(
            source_root=source_root,
            storage_for_alias=lambda alias: storages[alias],
        )

        if options["rollback"]:
            entries = self._rollback(migrator, options["rollback"], dry_run=options["dry_run"])
            verb = "would delete" if options["dry_run"] else "deleted"
        else:
            entries = migrator.migrate(
                dry_run=options["dry_run"], overwrite=options["overwrite"]
            )
            if options["verify"]:
                entries += migrator.verify_tracked_assets()
            verb = "planned" if options["dry_run"] else "processed"

        self._emit_report(entries, options.get("report"), options["format"])

        counts = summarize(entries)
        self.stdout.write(f"{verb} {len(entries)} object(s): {counts or '{}'}")
        failures = [e for e in entries if e.status != "ok"]
        for entry in failures:
            self.stdout.write(
                f"  {entry.status.upper()} {entry.path} [{entry.alias}]: {entry.detail}"
            )
        if failures:
            self.stdout.write(f"Completed with {len(failures)} problem(s).")

    # ------------------------------------------------------------------

    def _rollback(self, migrator: MediaMigrator, report_path: str, *, dry_run: bool) -> list:
        path = Path(report_path)
        if not path.is_file():
            raise CommandError(f"Rollback report not found: {report_path}")
        try:
            prior = load_report(path.read_text(encoding="utf-8"))
        except (ValueError, KeyError) as exc:
            raise CommandError(f"Rollback report is not a valid migration report: {exc}")
        return migrator.rollback(prior, dry_run=dry_run)

    def _emit_report(self, entries: list, report_path: str | None, fmt: str) -> None:
        if not report_path:
            return
        path = Path(report_path)
        suffix = path.suffix.lower()
        use_csv = suffix == ".csv" or (suffix != ".json" and fmt == "csv")
        payload = report_to_csv(entries) if use_csv else report_to_json(entries)
        path.write_text(payload, encoding="utf-8")
        self.stdout.write(f"Wrote report to {path}")
