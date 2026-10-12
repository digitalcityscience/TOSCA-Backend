"""Delete expired runs that users started, and leftover uploads (see geosimulations.retention)."""

from django.core.management.base import BaseCommand

from tosca_api.apps.geosimulations.retention import purge


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List what would be deleted")

    def handle(self, *args, **options):
        result = purge(dry_run=options["dry_run"])
        verb = "Would delete" if options["dry_run"] else "Deleted"
        self.stdout.write(f"{verb} {len(result.deleted_runs)} run(s): {', '.join(result.deleted_runs) or '—'}")
        if result.kept_runs:
            self.stdout.write(f"Kept {len(result.kept_runs)} run(s) used by stories or comparisons: "
                              f"{', '.join(result.kept_runs)}")
        if not options["dry_run"]:
            self.stdout.write(f"Removed {result.removed_uploads} leftover upload(s).")
