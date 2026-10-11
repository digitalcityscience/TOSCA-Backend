"""Start a simulation run on the model's runner (until the run API exists, Task 12.12)."""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.geosimulations.jobs import RunSubmissionError, submit_run
from tosca_api.apps.geosimulations.models import SimulationModel


class Command(BaseCommand):
    help = __doc__

    def add_arguments(self, parser):
        parser.add_argument("model", help="Simulation model key")
        parser.add_argument("--params", type=Path, help="JSON file with the run parameters (default: {})")
        parser.add_argument("--seed", type=int)
        parser.add_argument("--title")
        parser.add_argument("--user", help="Username recorded as the run's creator")

    def handle(self, *args, **options):
        model = SimulationModel.objects.filter(key=options["model"]).select_related("runner").first()
        if model is None:
            raise CommandError(f"Unknown simulation model {options['model']!r}.")
        parameters = {}
        if options["params"]:
            try:
                parameters = json.loads(options["params"].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CommandError(f"Cannot read {options['params']}: {exc}") from exc
        user = None
        if options["user"]:
            user = get_user_model().objects.filter(username=options["user"]).first()
            if user is None:
                raise CommandError(f"Unknown user {options['user']!r}.")
        try:
            run = submit_run(model, parameters, user=user, title=options["title"], seed=options["seed"])
        except RunSubmissionError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Queued run {run.key} ({run.pk}) on {model.runner.name}; job {run.job.pk}.")
