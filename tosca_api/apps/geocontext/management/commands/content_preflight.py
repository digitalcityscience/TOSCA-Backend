"""Read-only validation scan for feature-owned Editor.js content."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.core.editorjs import validate_and_normalize
from tosca_api.apps.core.legacy_html import LegacyHtmlMediaError, convert_legacy_html


class Command(BaseCommand):
    help = "Read-only preflight for feature-owned Editor.js content."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--legacy-input-json",
            dest="legacy_input_json",
            default=None,
            help="Optional JSON object mapping {id: legacy_html} for a dry run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        checked, invalid = self._scan_existing_rows()
        legacy_failures = self._scan_legacy_input(options.get("legacy_input_json"))

        self.stdout.write(
            f"Scanned {checked} feature content document(s); "
            f"{len(invalid)} fail canonical validation."
        )
        for entry in invalid:
            self.stdout.write(f"  INVALID {entry['id']}: {entry['error']}")

        if legacy_failures is not None:
            media = [entry for entry in legacy_failures if entry["kind"] == "media"]
            other = [entry for entry in legacy_failures if entry["kind"] != "media"]
            self.stdout.write("")
            self.stdout.write(
                f"Legacy backfill dry-run: {len(media)} media-blocked, "
                f"{len(other)} other failure(s)."
            )
            for entry in legacy_failures:
                self.stdout.write(f"  {entry['kind'].upper()} {entry['id']}: {entry['error']}")

        if invalid or legacy_failures:
            self.stdout.write("")
            self.stdout.write("Preflight complete with failures. No rows were modified.")
        else:
            self.stdout.write("Preflight complete. No failures detected.")

    def _scan_existing_rows(self) -> tuple[int, list[dict]]:
        from tosca_api.apps.events.models import Event, EventSeries
        from tosca_api.apps.feedback.models import GeoFeedback
        from tosca_api.apps.geostories.models import GeoStory

        sources = (
            ("geostories.GeoStory", GeoStory.objects.only("id", "content"), "content"),
            ("feedback.GeoFeedback", GeoFeedback.objects.only("id", "content"), "content"),
            (
                "events.Event",
                Event.objects.exclude(content_override=None).only("id", "content_override"),
                "content_override",
            ),
            (
                "events.EventSeries",
                EventSeries.objects.only("id", "default_content"),
                "default_content",
            ),
        )

        checked = 0
        failures: list[dict] = []
        for label, queryset, field_name in sources:
            for row in queryset.order_by("id").iterator():
                checked += 1
                row_id = f"{label}:{row.id}"
                try:
                    normalized = validate_and_normalize(getattr(row, field_name))
                    if validate_and_normalize(normalized) != normalized:
                        failures.append({"id": row_id, "error": "normalization is not idempotent"})
                except ValidationError as exc:
                    failures.append({"id": row_id, "error": "; ".join(exc.messages)})
        failures.sort(key=lambda entry: entry["id"])
        return checked, failures

    def _scan_legacy_input(self, path: str | None) -> list[dict] | None:
        if not path:
            return None
        file_path = Path(path)
        if not file_path.is_file():
            raise CommandError(f"Legacy input file not found: {path}")
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Legacy input file is not valid JSON: {exc.msg}") from exc
        if not isinstance(raw, dict):
            raise CommandError("Legacy input JSON must be an object mapping {id: html}.")

        failures: list[dict] = []
        for row_id in sorted(raw):
            html = raw[row_id]
            if not isinstance(html, str):
                failures.append({"id": row_id, "kind": "shape", "error": "value is not a string"})
                continue
            try:
                validate_and_normalize(convert_legacy_html(html))
            except LegacyHtmlMediaError as exc:
                failures.append({"id": row_id, "kind": "media", "error": str(exc)})
            except ValidationError as exc:
                failures.append({"id": row_id, "kind": "invalid", "error": "; ".join(exc.messages)})
        failures.sort(key=lambda entry: entry["id"])
        return failures
