"""
GeoContext preflight scan.

Reports which existing GeoContext rows (by UUID) cannot be round-tripped
through the canonical Editor.js validator, and optionally dry-runs a
legacy HTML backfill against an input file so operators can see which
legacy rows would abort before any data migration is attempted.

The command is strictly read-only: it never writes to the database and
never mutates its input file. Output is deterministic (IDs are sorted)
so repeated runs on unchanged data produce byte-equal output, making it
safe to diff across environments.

Usage
-----

Scan existing canonical rows::

    python manage.py geocontext_preflight

Dry-run a legacy HTML backfill from a JSON map of ``{id: html}``::

    python manage.py geocontext_preflight --legacy-input-json legacy.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.core.editorjs import validate_and_normalize
from tosca_api.apps.core.legacy_html import (
    LegacyHtmlMediaError,
    convert_legacy_html,
)
from tosca_api.apps.geocontext.models import GeoContext


class Command(BaseCommand):
    help = (
        "Read-only preflight that reports GeoContext rows whose content "
        "cannot be normalized to the canonical Editor.js contract, and "
        "optionally dry-runs a legacy HTML backfill from an input file."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--legacy-input-json",
            dest="legacy_input_json",
            default=None,
            help=(
                "Optional path to a JSON file mapping {id: legacy_html}. "
                "Each entry is dry-run through the legacy HTML converter "
                "and any row that fails is reported by id. Read-only."
            ),
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def handle(self, *args: Any, **options: Any) -> None:
        invalid_existing = self._scan_existing_rows()
        legacy_failures = self._scan_legacy_input(options.get("legacy_input_json"))

        total_checked = GeoContext.objects.count()
        self.stdout.write(
            f"Scanned {total_checked} existing GeoContext row(s); "
            f"{len(invalid_existing)} fail canonical validation."
        )
        for entry in invalid_existing:
            self.stdout.write(f"  INVALID {entry['id']}: {entry['error']}")

        if legacy_failures is not None:
            media_ids = [e for e in legacy_failures if e["kind"] == "media"]
            other_ids = [e for e in legacy_failures if e["kind"] != "media"]
            self.stdout.write("")
            self.stdout.write(
                f"Legacy backfill dry-run: {len(media_ids)} media-blocked, "
                f"{len(other_ids)} other failure(s)."
            )
            for entry in legacy_failures:
                self.stdout.write(
                    f"  {entry['kind'].upper()} {entry['id']}: {entry['error']}"
                )

        if invalid_existing or (legacy_failures and len(legacy_failures) > 0):
            self.stdout.write("")
            self.stdout.write("Preflight complete with failures. No rows were modified.")
        else:
            self.stdout.write("Preflight complete. No failures detected.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _scan_existing_rows(self) -> list[dict]:
        failures: list[dict] = []
        # Iterate in a stable order so output is deterministic.
        queryset = GeoContext.objects.all().only("id", "content").order_by("id")
        for row in queryset.iterator():
            try:
                normalized = validate_and_normalize(row.content)
            except ValidationError as exc:
                failures.append({"id": str(row.id), "error": "; ".join(exc.messages)})
                continue
            # Stability check: re-normalizing the result must be a no-op.
            try:
                second = validate_and_normalize(normalized)
            except ValidationError as exc:  # pragma: no cover — defensive
                failures.append({"id": str(row.id), "error": "; ".join(exc.messages)})
                continue
            if second != normalized:  # pragma: no cover — defensive
                failures.append(
                    {"id": str(row.id), "error": "normalization is not idempotent"}
                )
        failures.sort(key=lambda e: e["id"])
        return failures

    def _scan_legacy_input(self, path: str | None) -> list[dict] | None:
        if not path:
            return None
        file_path = Path(path)
        if not file_path.is_file():
            raise CommandError(f"Legacy input file not found: {path}")
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Legacy input file is not valid JSON: {exc.msg}")
        if not isinstance(raw, dict):
            raise CommandError(
                "Legacy input JSON must be an object mapping {id: html}."
            )

        failures: list[dict] = []
        for row_id in sorted(raw):
            html = raw[row_id]
            if not isinstance(html, str):
                failures.append(
                    {"id": row_id, "kind": "shape", "error": "value is not a string"}
                )
                continue
            try:
                converted = convert_legacy_html(html)
                validate_and_normalize(converted)
            except LegacyHtmlMediaError as exc:
                failures.append(
                    {"id": row_id, "kind": "media", "error": str(exc)}
                )
            except ValidationError as exc:
                failures.append(
                    {"id": row_id, "kind": "invalid", "error": "; ".join(exc.messages)}
                )
            except Exception as exc:  # pragma: no cover — defensive catch-all
                failures.append(
                    {"id": row_id, "kind": "error", "error": repr(exc)}
                )
        failures.sort(key=lambda e: e["id"])
        return failures
