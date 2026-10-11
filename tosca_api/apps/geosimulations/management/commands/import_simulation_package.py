"""Import a TOSCA simulation package (directory or .zip): a new run, or a comparison of two runs."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.geosimulations.importer import PackageImportError, import_package
from tosca_api.apps.geosimulations.packages import PackageValidationError
from tosca_api.apps.organizations.models import Organization


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("package", help="Package directory or .zip file")
        parser.add_argument("--organization", required=True, help="Owning organization slug")
        parser.add_argument("--user", help="Username recorded as the importer")
        parser.add_argument(
            "--publish", action="store_true", help="Publish the run (and its model if needed) or the comparison"
        )
        parser.add_argument(
            "--replace", action="store_true", help="Replace an existing run or comparison with the same key"
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="Leave an existing run or comparison with the same key untouched (for seeding)",
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

        try:
            result = import_package(
                options["package"],
                organization=organization,
                user=user,
                publish=options["publish"],
                replace=options["replace"],
                skip_existing=options["skip_existing"],
            )
        except PackageValidationError as exc:
            raise CommandError(str(exc)) from exc
        except PackageImportError as exc:
            raise CommandError(f"Import failed: {exc}") from exc

        item = result.item
        if result.skipped:
            self.stdout.write(f"Skipped: {item._meta.verbose_name} {item.key!r} already exists.")
            return
        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(f"Warning: {warning}"))
        model_note = " (new model)" if result.model_created else ""
        self.stdout.write(f"Model:   {item.model.key}{model_note}")
        if result.comparison is not None:
            comparison = result.comparison
            self.stdout.write(f"Compare: {comparison.key} [{comparison.status}] {comparison.pk}")
            self.stdout.write(f"         {comparison.baseline_run.key} → {comparison.candidate_run.key}")
        else:
            self.stdout.write(f"Run:     {item.key} [{item.status}] {item.pk}")
        for output in item.outputs.select_related("asset"):
            size = f" · {output.asset.size / 1e6:.2f} MB" if output.asset_id else ""
            self.stdout.write(f"Output:  {output.role:<20} {output.kind:<13}{size}")
        self.stdout.write(self.style.SUCCESS("Imported."))
