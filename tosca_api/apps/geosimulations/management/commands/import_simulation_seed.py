"""Import portable simulation seeds without configuring a simulation runner."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.organizations.models import Organization

from ...packages import PackageValidationError
from ...seed_bundles import SeedBundleError, import_seed


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("archives", nargs="+", help="One or more simulation seed .zip files")
        parser.add_argument("--organization", required=True, help="Owning organization slug")
        parser.add_argument("--user", help="Username recorded as the importer")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Replace runs with matching model/run keys; protected story uses still refuse replacement",
        )

    def handle(self, *args, **options):
        organization = Organization.objects.filter(slug=options["organization"]).first()
        if organization is None:
            raise CommandError(f"Unknown organization {options['organization']!r}.")
        user = None
        if options["user"]:
            user = get_user_model().objects.filter(username=options["user"]).first()
            if user is None:
                raise CommandError(f"Unknown user {options['user']!r}.")

        for archive in options["archives"]:
            try:
                result = import_seed(
                    archive,
                    organization=organization,
                    user=user,
                    replace=options["replace"],
                )
            except (SeedBundleError, PackageValidationError) as exc:
                raise CommandError(f"Import failed for {archive}: {exc}") from exc
            self.stdout.write(f"Model:    {result.model.key} [published, no runner]")
            for run in result.imported:
                self.stdout.write(f"Imported: {run.key} [published]")
            for run in result.skipped:
                self.stdout.write(f"Skipped:  {run.key} (already published)")
            for item in result.omitted:
                self.stdout.write(
                    self.style.WARNING(
                        f"Not transferred: {item.get('key', '?')} ({item.get('reason', 'unknown reason')})"
                    )
                )
            self.stdout.write(self.style.SUCCESS(f"Imported {archive}."))
