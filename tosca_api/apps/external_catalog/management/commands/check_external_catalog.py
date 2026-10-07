"""Check category items against their services (e.g. a nightly cron job).

The admin runs the same check with the "Check availability" actions.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.external_catalog import health
from tosca_api.apps.external_catalog.models import Category, ExternalService


class Command(BaseCommand):
    help = (
        "Check that every category item's source still exists at its service and store its "
        "feature/datastream count. Same as the admin's 'Check availability' actions."
    )

    def add_arguments(self, parser):
        parser.add_argument("--service", action="append", default=[], help="Service slug (repeatable).")
        parser.add_argument("--category", action="append", default=[], help="Category slug (repeatable).")

    def handle(self, *args, **options):
        services = categories = None
        if options["service"]:
            services = list(ExternalService.objects.filter(slug__in=options["service"]))
            _require_all(options["service"], [service.slug for service in services], "service")
        if options["category"]:
            categories = list(Category.objects.filter(slug__in=options["category"]))
            _require_all(options["category"], [category.slug for category in categories], "category")

        summary: dict[str, int] = {}
        for service in ExternalService.objects.filter(items__isnull=False).distinct().order_by("slug"):
            if services is not None and service not in services:
                continue
            items = health.items_for(services=[service], categories=categories)
            if not items.exists():
                continue
            result = health.check_items(items)
            self.stdout.write(f"{service.slug}: " + ", ".join(f"{count} {state}" for state, count in sorted(result.items())))
            for state, count in result.items():
                summary[state] = summary.get(state, 0) + count
        if not summary:
            self.stdout.write("No items to check.")


def _require_all(requested, found, kind):
    missing = set(requested) - set(found)
    if missing:
        raise CommandError(f"No {kind} with slug: {', '.join(sorted(missing))}")
