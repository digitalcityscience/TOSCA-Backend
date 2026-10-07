"""Load services' source index from the command line (e.g. a weekly cron job).

The admin does the same with "Load catalog"; see :mod:`...harvest`.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.external_catalog import harvest, remote
from tosca_api.apps.external_catalog.models import ExternalService


class Command(BaseCommand):
    help = (
        "Read every collection / SensorThings layer of the active services into the source "
        "index used by the category picker. Same as the admin's 'Load catalog'."
    )

    def add_arguments(self, parser):
        parser.add_argument("--service", action="append", default=[], help="Service slug (repeatable).")

    def handle(self, *args, **options):
        services = ExternalService.objects.filter(is_active=True)
        if options["service"]:
            services = services.filter(slug__in=options["service"])
            missing = set(options["service"]) - set(services.values_list("slug", flat=True))
            if missing:
                raise CommandError(f"No active service with slug: {', '.join(sorted(missing))}")

        failed = False
        for service in services:
            if not harvest.claim(service):
                self.stderr.write(f"{service.slug}: a load is already running.")
                failed = True
                continue
            try:
                rows = harvest.run(service)
            except remote.RemoteServiceError as exc:
                failed = True
                self.stderr.write(f"{service.slug}: {exc}")
                continue
            self.stdout.write(f"{service.slug}: {len(rows)} sources indexed.")
            if service.catalog_load_note:
                self.stderr.write(f"{service.slug}: {service.catalog_load_note}")
        if failed:
            raise CommandError("Some services could not be loaded.")
