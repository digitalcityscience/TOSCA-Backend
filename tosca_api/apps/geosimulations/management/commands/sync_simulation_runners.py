"""Copy parameter schemas, defaults and versions from simulation runners (GET /models)."""

import requests
from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.geosimulations.jobs import sync_runner_models
from tosca_api.apps.geosimulations.models import SimulationRunner


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("--runner", help="Runner name (default: every active runner)")

    def handle(self, *args, **options):
        runners = SimulationRunner.objects.filter(is_active=True)
        if options["runner"]:
            runners = runners.filter(name=options["runner"])
            if not runners.exists():
                raise CommandError(f"No active runner named {options['runner']!r}.")
        for runner in runners:
            try:
                models = sync_runner_models(runner)
            except (requests.RequestException, ValueError) as exc:
                self.stderr.write(f"{runner.name}: {exc}")
                continue
            names = ", ".join(f"{model.key} {model.runner_version}" for model in models) or "no models"
            self.stdout.write(f"{runner.name}: {names}")
