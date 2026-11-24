"""The run-start policy applied directly, without a view."""

from __future__ import annotations

from dataclasses import replace

import pytest
from django.test import override_settings

from tosca_api.apps.geosimulations import jobs, policy
from tosca_api.apps.geosimulations.models import SimulationModel, SimulationRun, SimulationRunner, Status

from .helpers import org, simulation_model
from .test_run_api import DEFAULTS, QUOTAS, SCHEMA

pytestmark = pytest.mark.django_db

Visibility = SimulationRun.Visibility


@pytest.fixture(autouse=True)
def settings_on():
    with override_settings(GEOSIMULATIONS_QUOTAS=QUOTAS, GEOSIMULATIONS_RUNS_ENABLED=True):
        yield


@pytest.fixture
def model():
    dcs = org("dcs")
    runner = SimulationRunner(organization=dcs, name="Bike runner", base_url="http://bike-runner:8080")
    runner.set_token("runner-secret")
    runner.save()
    return simulation_model(
        dcs,
        status=Status.PUBLISHED,
        runner=runner,
        runner_version="0.2.0",
        parameter_schema=SCHEMA,
        default_parameters=DEFAULTS,
    )


def _user(django_user_model, name="planner"):
    return django_user_model.objects.create_user(username=name)


def _caller(user=None, *, org_slug=None, org_level=None, exempt=False, client_ident="203.0.113.7"):
    return policy.Caller(
        user=user, org_slug=org_slug, org_level=org_level, exempt=exempt, client_ident=client_ident
    )


def _start(caller, model, *, visibility=Visibility.PRIVATE, **parameters):
    return policy.start_run(
        caller, model, parameters=parameters, seed=None, title="", visibility=visibility
    )


def test_allowed_caller_starts_a_run(model, django_user_model):
    user = _user(django_user_model)

    result = _start(_caller(user), model, agents=50)

    assert isinstance(result, jobs.StartResult) and not result.reused
    assert result.run.created_by == user and result.run.visibility == Visibility.PRIVATE


def test_anonymous_caller_gets_a_link_run_keyed_by_client(model):
    result = _start(_caller(), model, agents=12)

    assert result.run.created_by is None and result.run.visibility == Visibility.LINK
    assert result.run.submitter_key == jobs.submitter_key_for("203.0.113.7")


def test_run_access_denied(model, django_user_model):
    SimulationModel.objects.filter(pk=model.pk).update(run_access=SimulationModel.RunAccess.ORGANIZATION)
    model.refresh_from_db()
    outsider = _caller(_user(django_user_model), org_slug="other", org_level="WRITER")

    assert not policy.may_start_runs(outsider, model)
    with pytest.raises(policy.RunAccessDenied):
        _start(outsider, model)
    member = replace(outsider, org_slug="dcs")
    assert policy.may_start_runs(member, model)
    assert _start(member, model).run.pk


def test_runs_disabled(model, django_user_model):
    with override_settings(GEOSIMULATIONS_RUNS_ENABLED=False):
        assert not policy.runs_enabled()
        with pytest.raises(policy.RunsDisabled):
            _start(_caller(_user(django_user_model)), model)


def test_organization_visibility_needs_membership(model, django_user_model):
    user = _user(django_user_model)

    with pytest.raises(policy.VisibilityNotAllowed):
        _start(_caller(user, org_slug="other", org_level="READER"), model, visibility=Visibility.ORGANIZATION)
    member = _caller(user, org_slug="dcs", org_level="READER")
    assert _start(member, model, visibility=Visibility.ORGANIZATION).run.visibility == Visibility.ORGANIZATION
    exempt = _caller(user, exempt=True)
    assert _start(exempt, model, visibility=Visibility.ORGANIZATION, agents=7).run.pk


def test_forced_non_exempt_caller_is_subject_to_quotas(model, django_user_model):
    exempt = _caller(_user(django_user_model), exempt=True)
    for agents in (1, 2, 3):
        _start(exempt, model, agents=agents)

    with pytest.raises(jobs.QuotaExceeded):
        _start(replace(exempt, exempt=False), model, agents=4)
    assert _start(exempt, model, agents=4).run.pk


def test_readable_runs_by_id_adds_link_runs(model, django_user_model):
    link = _start(_caller(), model, agents=12).run
    stranger = _caller(_user(django_user_model))

    assert not policy.readable_runs(stranger).filter(pk=link.pk).exists()
    assert policy.readable_runs(stranger, by_id=True).filter(pk=link.pk).exists()
