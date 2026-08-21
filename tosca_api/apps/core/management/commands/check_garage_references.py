"""Warning-only Garage reference check (P0 snapshot/restore ticket 05).

Invoked as part of `make restore`'s post-restore verify, after
geoengine_smoke_test (see scripts/snapshot.sh). Never fails the command: a
missing reference is reported, never blocking -- full Garage backup/
versioning is explicitly out of P0 scope
(docs/development/p0-snapshot-restore-spec.md §6.2).
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from tosca_api.apps.core.garage_reference_check import run_reference_check


class Command(BaseCommand):
    help = "Warning-only check: do DB media references still resolve in Garage?"

    def handle(self, *args: Any, **options: Any) -> None:
        result = run_reference_check()
        self.stdout.write(
            f"{result.checked} referans kontrol edildi, {result.missing_count} eksik"
        )
        if result.missing:
            self.stdout.write(self.style.WARNING("Missing:"))
            for key in result.missing:
                self.stdout.write(f"  {key}")
