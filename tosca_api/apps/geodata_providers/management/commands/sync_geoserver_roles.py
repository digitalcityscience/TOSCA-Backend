"""
Reconcile GeoServer role services from Django's catalog (Epic 11 Phase 2, §4).

Mirrors the catalog's **reader/writer** roles into each active ``GeodataEngine``'s
active role service: active roles are created, deactivated ones are deleted.
Idempotent; only roles that need it are touched. A push failure is reported but
never aborts the run (roles are a manageability nicety; ACL enforcement already
works by string match). Shares :func:`role_sync.reconcile_all_engines` with the
"Sync with Keycloak" admin button (which additionally runs the Keycloak pull).

    python manage.py sync_geoserver_roles [--dry-run] [--engine NAME]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from tosca_api.apps.geodata_providers.models import GeodataEngine
from tosca_api.apps.geodata_providers.role_sync import reconcile_all_engines


class Command(BaseCommand):
    help = "Mirror the catalog's reader/writer roles into each active GeoServer role service."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing to GeoServer.",
        )
        parser.add_argument(
            "--engine",
            type=str,
            help="Reconcile a single engine by name (default: all active engines).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if options["engine"]:
            engines = GeodataEngine.objects.filter(
                name=options["engine"], is_active=True
            )
            if not engines.exists():
                self.stdout.write(
                    self.style.ERROR(
                        f'❌ Engine "{options["engine"]}" not found or inactive'
                    )
                )
                return
        else:
            engines = GeodataEngine.objects.filter(is_active=True)

        if not engines.exists():
            self.stdout.write(self.style.WARNING("⚠️ No active GeoServer engines found"))
            return

        self.stdout.write(
            f"🔄 Reconciling reader/writer roles across {engines.count()} engine(s)..."
        )

        for engine, summary, error in reconcile_all_engines(dry_run=dry_run, engines=engines):
            self.stdout.write(f"\n🏭 Engine: {engine.name}")
            if error:
                self.stdout.write(self.style.ERROR(f"  ❌ Failed: {error}"))
                continue

            prefix = "[DRY RUN] would " if dry_run else ""
            self.stdout.write(
                self.style.SUCCESS(
                    f"  ✅ {prefix}create {len(summary['created'])}, "
                    f"delete {len(summary['deleted'])} "
                    f"({len(summary['existed'])} already present, "
                    f"{len(summary['absent'])} already absent)"
                )
            )
            if summary["created"]:
                self.stdout.write("     + " + ", ".join(summary["created"]))
            if summary["deleted"]:
                self.stdout.write("     - " + ", ".join(summary["deleted"]))
            for name, err in summary["failed"]:
                self.stdout.write(self.style.WARNING(f"  ⚠️ {name}: {err}"))
