"""
Backfill existing media onto the correct storage bucket (S2 truth table).

Ticket 13 fixed new-upload correctness (uploads default private) and ticket
14 wired the on-transition lifecycle sync, but neither touches objects that
were already mis-aliased before those tickets landed -- e.g. a private/draft
story's image that was uploaded straight into ``media_public`` under the old
behavior. This command closes that historical exposure by recomputing each
campaign's desired alias set (via ``media_lifecycle.MediaLifecycleService``,
the same copy -> verify -> re-point -> delete machinery tickets 13/14 use)
and relocating anything that doesn't match.

Migration strategy: **backfill-all**, not new-uploads-only -- every campaign
is re-evaluated, matching the strategy already used by ``migrate_media_paths``
for the canonical-path migration. New uploads are already correct as of
ticket 13/14; this command only has work to do on pre-existing objects, and
is idempotent (re-running once every asset is correctly aliased is a no-op).

Defaults to ``--dry-run``; batched and resumable via ``--start-after``, one
``Campaign`` (plus its GeoStories/media) per unit of work, since
``MediaLifecycleService.sync_campaign_assets`` already operates at that
granularity and covers both plain ``MediaAsset`` rows and hero images that
have no ``MediaAsset`` row of their own.

Usage
-----

Dry-run over every campaign (default; writes nothing)::

    python manage.py backfill_media_aliases

Apply, 200 campaigns at a time, writing a JSON report::

    python manage.py backfill_media_aliases --apply --batch-size 200 --report alias-backfill.json

Resume a batch run after an interruption, starting after a known campaign id::

    python manage.py backfill_media_aliases --apply --start-after <last-processed-uuid>
"""

from __future__ import annotations

from typing import Any

from django.core.files.storage import storages
from django.core.management.base import BaseCommand

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.core.media_lifecycle import (
    MediaLifecycleService,
    report_to_json,
    summarize,
)


class Command(BaseCommand):
    help = (
        "Recompute each campaign's desired media storage alias (S2 truth table) "
        "and relocate mis-aliased objects. Backfill-all: every campaign is "
        "re-evaluated. Defaults to --dry-run; batched and resumable via --start-after."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually copy objects and re-point storage_alias (default: dry-run).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=200,
            help="Campaigns processed per batch (default 200).",
        )
        parser.add_argument(
            "--start-after",
            default=None,
            metavar="CAMPAIGN_ID",
            help="Resume a prior run, skipping ids <= this UUID (ordered by id).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Stop after this many campaigns total (for staged rollouts).",
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

        service = MediaLifecycleService(storage_for_alias=lambda alias: storages[alias])

        queryset = Campaign.objects.select_related("organization").order_by("id")
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

            entries = []
            for campaign in batch:
                entries.extend(service.sync_campaign_assets(campaign, dry_run=not apply))
            all_entries.extend(entries)
            processed += len(batch)
            last_id = batch[-1].id

            failures = [e for e in entries if e.status != "ok"]
            self.stdout.write(
                f"batch of {len(batch)} campaign(s) (total {processed}): "
                f"{summarize(entries)}"
                + (f" -- {len(failures)} failure(s)" if failures else "")
            )
            if len(batch) < take:
                break  # last page

        if options["report"]:
            with open(options["report"], "w", encoding="utf-8") as fh:
                fh.write(report_to_json(all_entries))
            self.stdout.write(f"Wrote report to {options['report']}")

        verb = "would relocate" if not apply else "relocated"
        counts = summarize(all_entries)
        self.stdout.write(
            f"{verb} media for {processed} campaign(s) total ({len(all_entries)} asset move(s) evaluated): "
            f"{counts or '{}'}"
        )
        failures = [e for e in all_entries if e.status != "ok"]
        if failures:
            self.stdout.write(f"Completed with {len(failures)} problem(s).")
            for entry in failures[:50]:
                self.stdout.write(f"  FAILED {entry.asset_id}: {entry.detail}")
