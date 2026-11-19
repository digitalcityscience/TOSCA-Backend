"""0009 keeps what anonymous readers saw: nothing hidden before becomes public."""

import importlib

import pytest
from django.apps import apps

from tosca_api.apps.geosimulations.models import SimulationRun, Status

from .helpers import org, simulation_model, simulation_run

pytestmark = pytest.mark.django_db

migration = importlib.import_module(
    "tosca_api.apps.geosimulations.migrations.0009_publishing_visibility_and_run_access"
)


def test_published_runs_of_unpublished_models_stay_organization_only():
    organization = org("acme")
    published = simulation_model(organization, key="published", status=Status.PUBLISHED)
    draft = simulation_model(organization, key="draft")
    shown = simulation_run(published, key="shown", status=Status.PUBLISHED)
    hidden = simulation_run(draft, key="hidden")
    # Set the pre-0009 state directly (save() validates against the new rules).
    SimulationRun.objects.filter(pk__in=[shown.pk, hidden.pk]).update(
        status=Status.PUBLISHED, visibility=SimulationRun.Visibility.ORGANIZATION
    )

    migration.keep_published_items_public(apps, None)

    shown.refresh_from_db()
    hidden.refresh_from_db()
    assert shown.visibility == SimulationRun.Visibility.PUBLIC
    assert hidden.visibility == SimulationRun.Visibility.ORGANIZATION
