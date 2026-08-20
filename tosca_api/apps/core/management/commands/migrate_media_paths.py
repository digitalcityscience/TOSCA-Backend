"""
Migrate ``MediaAsset`` storage objects onto the canonical Garage path scheme.

Backfill-all strategy per epic-11 PR2 (§4): every resolvable asset is moved,
not just new uploads. Defaults to ``--dry-run``; batched/resumable via
``--batch-size`` and ``--start-after`` so a large table is never processed in
one transaction. Safe to run against an empty ``MediaAsset`` table.

Usage
-----

Dry-run over the whole table (default; writes nothing)::

    python manage.py migrate_media_paths

Apply, 500 rows at a time, writing a JSON report::

    python manage.py migrate_media_paths --apply --batch-size 500 --report path-migration.json

Resume a batch run after an interruption, starting after a known asset id::

    python manage.py migrate_media_paths --apply --start-after <last-processed-uuid>
"""

from __future__ import annotations

from typing import Any

from django.core.files.storage import storages
from django.core.management.base import BaseCommand

from tosca_api.apps.core.media_migration import DEFAULT_PUBLIC_PREFIXES
from tosca_api.apps.core.media_path_migration import (
    MediaPathMigrator,
    report_to_json,
    summarize,
)
from tosca_api.apps.core.models import MediaAsset


def _alias_for_asset(asset: MediaAsset) -> str:
    """Mirror media_migration.MediaMigrator.alias_for's routing decision."""
    for prefix in DEFAULT_PUBLIC_PREFIXES:
        if asset.storage_path.startswith(prefix):
            return "media_public"
    return "default"


class Command(BaseCommand):
    help = (
        "Move existing MediaAsset objects onto the canonical "
        "orgs/<org>/campaigns/<id>/{stories,events,misc}/... path scheme. "
        "Backfill-all: every asset with a resolvable campaign is migrated. "
        "Defaults to --dry-run; batched and resumable via --start-after."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually copy objects and rewrite storage_path (default: dry-run).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Rows processed per batch/transaction slice (default 500).",
        )
        parser.add_argument(
            "--start-after",
            default=None,
            metavar="ASSET_ID",
            help="Resume a prior run, skipping ids <= this UUID (ordered by id).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after this many rows total (for staged rollouts).",
        )
        parser.add_argument(
            "--report",
            default=None,
            help="Write the full JSON outcome report to this path.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        apply = options["apply"]
        batch_size = options["batch_size"]
        limit = options["limit"]

        migrator = MediaPathMigrator(
            storage_for_alias=lambda alias: storages[alias],
            alias_for_asset=_alias_for_asset,
        )

        queryset = MediaAsset.objects.filter(campaign__isnull=False).select_related(
            "campaign", "campaign__organization"
        ).order_by("id")
        if options["start_after"]:
            queryset = queryset.filter(id__gt=options["start_after"])

        all_entries = []
        processed = 0
        last_id = None
        while True:
            remaining = None if limit is None else max(limit - processed, 0)
            if remaining == 0:
                break
            take = batch_size if remaining is None else min(batch_size, remaining)
            batch = list(queryset[:take] if last_id is None else queryset.filter(id__gt=last_id)[:take])
            if not batch:
                break

            entries = migrator.apply(batch) if apply else migrator.plan(batch)
            all_entries.extend(entries)
            processed += len(batch)
            last_id = batch[-1].id

            failures = [e for e in entries if e.status != "ok"]
            self.stdout.write(
                f"batch of {len(batch)} (total {processed}): "
                f"{summarize(entries)}"
                + (f" -- {len(failures)} failure(s)" if failures else "")
            )
            if len(batch) < take:
                break  # last page

        if options["report"]:
            with open(options["report"], "w", encoding="utf-8") as fh:
                fh.write(report_to_json(all_entries))
            self.stdout.write(f"Wrote report to {options['report']}")

        verb = "would move" if not apply else "moved"
        counts = summarize(all_entries)
        self.stdout.write(f"{verb} {processed} asset(s) total: {counts or '{}'}")
        failures = [e for e in all_entries if e.status != "ok"]
        if failures:
            self.stdout.write(f"Completed with {len(failures)} problem(s).")
            for entry in failures[:50]:
                self.stdout.write(f"  FAILED {entry.old_path}: {entry.detail}")
