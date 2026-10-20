"""Export a simulation model and its completed runs for another TOSCA installation."""

from django.core.management.base import BaseCommand, CommandError

from ...models import SimulationModel
from ...seed_bundles import SeedBundleError, export_seed


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("model", help="Simulation model key")
        parser.add_argument("destination", help="Destination .zip file or directory")

    def handle(self, *args, **options):
        model = SimulationModel.objects.filter(key=options["model"]).first()
        if model is None:
            raise CommandError(f"Unknown simulation model {options['model']!r}.")
        try:
            destination, omitted = export_seed(model, options["destination"])
        except SeedBundleError as exc:
            raise CommandError(str(exc)) from exc
        for item in omitted:
            self.stdout.write(
                self.style.WARNING(f"Omitted {item['key']}: {item['reason']}.")
            )
        exported_count = model.runs.count() - len(omitted)
        self.stdout.write(f"Model: {model.key}")
        self.stdout.write(f"Runs:  {exported_count} exported, {len(omitted)} omitted")
        self.stdout.write(self.style.SUCCESS(f"Created {destination}"))
