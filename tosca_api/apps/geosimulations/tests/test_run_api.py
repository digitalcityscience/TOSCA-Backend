"""Runs users start through the API (Task 12.12): submission, reuse, quotas,
visibility, runner schema sync and retention."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
import requests
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.geosimulations import jobs, retention
from tosca_api.apps.geosimulations.models import (
    SimulationJob,
    SimulationModel,
    SimulationRun,
    SimulationRunner,
    Status,
)

from .helpers import org, simulation_model, simulation_run

pytestmark = pytest.mark.django_db

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "agents": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 100},
        "turn_aware_signals": {"type": "boolean", "default": False},
    },
}
DEFAULTS = {"agents": 100, "turn_aware_signals": False}
QUOTAS = {"user_active": 2, "user_daily": 3, "organization_active": 5, "organization_daily": 4}
Visibility = SimulationRun.Visibility
Execution = SimulationRun.ExecutionState


@pytest.fixture(autouse=True)
def quotas():
    with override_settings(GEOSIMULATIONS_QUOTAS=QUOTAS):
        yield


@pytest.fixture
def dcs():
    return org("dcs")


@pytest.fixture
def runner(dcs):
    runner = SimulationRunner(organization=dcs, name="Bike runner", base_url="http://bike-runner:8080")
    runner.set_token("runner-secret")
    runner.save()
    return runner


@pytest.fixture
def model(dcs, runner):
    return simulation_model(
        dcs,
        status=Status.PUBLISHED,
        runner=runner,
        runner_version="0.2.0",
        parameter_schema=SCHEMA,
        default_parameters=DEFAULTS,
    )


def _client(django_user_model, username, *, org_slug=None, roles=()):
    user = django_user_model.objects.create_user(username=username)
    client = APIClient()
    client.force_authenticate(user=user, token={"realm_access": {"roles": list(roles)}, "default_organization": org_slug})
    return client, user


def _start(client, model, **body):
    return client.post(f"/api/v1/geosimulations/{model.pk}/runs/", body, format="json")


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def test_signed_in_users_start_private_runs(model, django_user_model):
    client, user = _client(django_user_model, "planner")

    response = _start(client, model, parameters={"agents": 50}, seed=7, title="My run")

    assert response.status_code == 202
    assert response.data["reused"] is False
    assert response.data["visibility"] == "private"
    assert response.data["execution_state"] == "queued"
    run = SimulationRun.objects.get(pk=response.data["id"])
    assert run.parameters == {"agents": 50, "turn_aware_signals": False}  # defaults filled in
    assert (run.created_by, run.seed, run.title, run.model_version) == (user, 7, "My run", "0.2.0")
    assert run.job.state == SimulationJob.State.PENDING


def test_anonymous_callers_cannot_start_runs(model):
    assert _start(APIClient(), model, parameters={}).status_code in (401, 403)


def test_invalid_parameters_and_models_without_runner(model, dcs, django_user_model):
    client, _user = _client(django_user_model, "planner")

    invalid = _start(client, model, parameters={"agents": 9000, "colour": "red"})
    assert invalid.status_code == 400
    assert any("9000 is greater than the maximum" in error for error in invalid.data["parameters"])

    no_runner = simulation_model(dcs, key="static-only", status=Status.PUBLISHED)
    response = _start(client, no_runner, parameters={})
    assert response.status_code == 409 and "no active runner" in response.data["detail"]
    assert not SimulationRun.objects.exists()


def test_identical_requests_reuse_the_run(model, django_user_model):
    client, _user = _client(django_user_model, "planner")
    first = _start(client, model, parameters={"agents": 50})

    # Same parameters spelled out with the default: same run, still in progress.
    again = _start(client, model, parameters={"agents": 50, "turn_aware_signals": False})
    other_seed = _start(client, model, parameters={"agents": 50}, seed=3)

    assert again.status_code == 200 and again.data["reused"] is True
    assert again.data["id"] == first.data["id"]
    assert other_seed.status_code == 202 and other_seed.data["id"] != first.data["id"]


def test_reuse_prefers_finished_runs_and_skips_failed_ones(model, django_user_model):
    client, _user = _client(django_user_model, "planner")
    failed = SimulationRun.objects.get(pk=_start(client, model, parameters={"agents": 5}).data["id"])
    jobs._fail(failed.job, "boom")

    fresh = _start(client, model, parameters={"agents": 5})

    assert fresh.status_code == 202 and fresh.data["id"] != str(failed.pk)


def test_other_users_private_runs_are_not_reused(model, django_user_model):
    alice, _ = _client(django_user_model, "alice")
    bob, _ = _client(django_user_model, "bob")
    first = _start(alice, model, parameters={"agents": 20})
    SimulationRun.objects.filter(pk=first.data["id"]).update(execution_state=Execution.SUCCEEDED)

    second = _start(bob, model, parameters={"agents": 20})

    assert second.status_code == 202 and second.data["id"] != first.data["id"]


def test_published_runs_are_reused_by_anyone(model, django_user_model):
    alice, _ = _client(django_user_model, "alice")
    bob, _ = _client(django_user_model, "bob")
    first = _start(alice, model, parameters={"agents": 20})
    SimulationRun.objects.filter(pk=first.data["id"]).update(
        execution_state=Execution.SUCCEEDED, status=Status.PUBLISHED
    )

    second = _start(bob, model, parameters={"agents": 20})

    assert second.status_code == 200 and second.data["id"] == first.data["id"]


def test_only_model_organization_members_can_share_with_it(model, django_user_model):
    outsider, _ = _client(django_user_model, "outsider", org_slug="qg2", roles=["ROLE_QG2_READER"])
    member, _ = _client(django_user_model, "member", org_slug="dcs", roles=["ROLE_DCS_READER"])

    assert _start(outsider, model, parameters={}, visibility="organization").status_code == 400
    shared = _start(member, model, parameters={}, visibility="organization")
    assert shared.status_code == 202 and shared.data["visibility"] == "organization"


# ---------------------------------------------------------------------------
# Quotas
# ---------------------------------------------------------------------------


def test_user_quotas_limit_active_and_daily_runs(model, django_user_model):
    client, _user = _client(django_user_model, "planner")
    for agents in (1, 2):
        assert _start(client, model, parameters={"agents": agents}).status_code == 202

    busy = _start(client, model, parameters={"agents": 3})
    assert busy.status_code == 429 and "2 runs in progress" in busy.data["detail"]
    assert busy["Retry-After"] == "60"
    # An identical request is still answered with the existing run.
    assert _start(client, model, parameters={"agents": 1}).status_code == 200

    SimulationRun.objects.update(execution_state=Execution.SUCCEEDED)
    assert _start(client, model, parameters={"agents": 3}).status_code == 202
    SimulationRun.objects.update(execution_state=Execution.SUCCEEDED)
    daily = _start(client, model, parameters={"agents": 4})
    assert daily.status_code == 429 and "daily limit" in daily.data["detail"]
    assert 0 < int(daily["Retry-After"]) <= 24 * 3600


def test_organization_quotas_count_every_member(model, django_user_model):
    with override_settings(GEOSIMULATIONS_QUOTAS={**QUOTAS, "organization_active": 2}):
        first, _ = _client(django_user_model, "one", org_slug="dcs", roles=["ROLE_DCS_READER"])
        second, _ = _client(django_user_model, "two", org_slug="dcs", roles=["ROLE_DCS_READER"])
        _start(first, model, parameters={"agents": 1})
        _start(first, model, parameters={"agents": 2})

        response = _start(second, model, parameters={"agents": 3})

    assert response.status_code == 429 and response.data["detail"].startswith("Your organization")


def test_platform_admins_are_not_limited(model, django_user_model):
    admin, _ = _client(django_user_model, "root", roles=["DJANGO_SUPERADMIN"])
    statuses = [_start(admin, model, parameters={"agents": agents}).status_code for agents in range(1, 5)]
    assert statuses == [202, 202, 202, 202]


# ---------------------------------------------------------------------------
# Reading started runs
# ---------------------------------------------------------------------------


def test_private_runs_are_readable_only_by_their_creator(model, django_user_model):
    alice, _ = _client(django_user_model, "alice", org_slug="dcs", roles=["ROLE_DCS_READER"])
    colleague, _ = _client(django_user_model, "colleague", org_slug="dcs", roles=["ROLE_DCS_READER"])
    run_id = _start(alice, model, parameters={"agents": 5}).data["id"]
    SimulationRun.objects.filter(pk=run_id).update(execution_error="Runner exploded")

    own = alice.get(f"/api/v1/geosimulations/runs/{run_id}/")
    mine = alice.get("/api/v1/geosimulations/runs/", {"mine": "true"})
    model_detail = alice.get(f"/api/v1/geosimulations/{model.pk}/")

    assert own.status_code == 200 and own.data["is_owner"] is True
    assert own.data["execution"]["error"] == "Runner exploded"
    assert [item["id"] for item in mine.data["results"]] == [run_id]
    assert model_detail.data["runnable"] is True
    assert run_id in [item["id"] for item in model_detail.data["runs"]]
    assert colleague.get(f"/api/v1/geosimulations/runs/{run_id}/").status_code == 404
    assert APIClient().get(f"/api/v1/geosimulations/runs/{run_id}/").status_code == 404
    assert colleague.get("/api/v1/geosimulations/runs/", {"mine": "true"}).data["results"] == []


def test_imported_runs_keep_organization_visibility(model, django_user_model):
    imported = simulation_run(model, key="imported")
    member, _ = _client(django_user_model, "member", org_slug="dcs", roles=["ROLE_DCS_READER"])

    detail = member.get(f"/api/v1/geosimulations/runs/{imported.pk}/")

    assert imported.visibility == Visibility.ORGANIZATION
    assert detail.status_code == 200 and detail.data["execution"] is None and detail.data["is_owner"] is False


# ---------------------------------------------------------------------------
# Runner schema sync
# ---------------------------------------------------------------------------


def _models_response(payload):
    response = requests.Response()
    response.status_code = 200
    response._content = __import__("json").dumps(payload).encode()
    return response


def test_sync_copies_schema_defaults_and_version(dcs, runner, capsys):
    model = simulation_model(dcs, runner=runner)
    described = [{"key": model.key, "version": "0.3.0", "parameter_schema": SCHEMA, "default_parameters": DEFAULTS}]

    with patch("tosca_api.apps.geosimulations.jobs.requests.get", return_value=_models_response(described)) as get:
        call_command("sync_simulation_runners")

    model.refresh_from_db()
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer runner-secret"
    assert (model.parameter_schema, model.default_parameters, model.runner_version) == (SCHEMA, DEFAULTS, "0.3.0")
    assert "hamburg-bike-abm 0.3.0" in capsys.readouterr().out


def test_sync_rejects_an_invalid_schema(dcs, runner):
    model = simulation_model(dcs, runner=runner)
    described = [{"key": model.key, "version": "1", "parameter_schema": {"type": "nonsense"}}]

    with patch("tosca_api.apps.geosimulations.jobs.requests.get", return_value=_models_response(described)):
        with pytest.raises(Exception, match="nonsense"):
            jobs.sync_runner_models(runner)
    model.refresh_from_db()
    assert model.parameter_schema is None


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def _age(run, days):
    SimulationRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=days))


def test_purge_deletes_expired_private_and_failed_runs(model, django_user_model, django_capture_on_commit_callbacks):
    user = django_user_model.objects.create_user(username="planner")
    old_private = jobs.submit_run(model, {"agents": 1}, user=user, visibility=Visibility.PRIVATE)
    recent_private = jobs.submit_run(model, {"agents": 2}, user=user, visibility=Visibility.PRIVATE)
    old_shared = jobs.submit_run(model, {"agents": 3}, user=user, visibility=Visibility.ORGANIZATION)
    old_failed = jobs.submit_run(model, {"agents": 4}, user=user, visibility=Visibility.ORGANIZATION)
    jobs._fail(old_failed.job, "boom")
    published = jobs.submit_run(model, {"agents": 5}, user=user, visibility=Visibility.PRIVATE)
    SimulationRun.objects.filter(pk=published.pk).update(status=Status.PUBLISHED)
    curated = simulation_run(model, key="imported")
    for run in (old_private, old_shared, old_failed, published, curated):
        _age(run, 40)

    dry = retention.purge(dry_run=True)
    with django_capture_on_commit_callbacks(execute=True):
        result = retention.purge()

    assert sorted(dry.deleted_runs) == sorted(result.deleted_runs) == sorted([old_private.key, old_failed.key])
    remaining = set(SimulationRun.objects.values_list("key", flat=True))
    assert remaining == {recent_private.key, old_shared.key, published.key, curated.key}


def test_purge_keeps_runs_used_elsewhere(model, django_user_model):
    from tosca_api.apps.geosimulations.models import SimulationComparison

    user = django_user_model.objects.create_user(username="planner")
    private = jobs.submit_run(model, {"agents": 1}, user=user, visibility=Visibility.PRIVATE)
    other = simulation_run(model, key="imported")
    SimulationComparison.objects.create(
        model=model, key="cmp", title="Cmp", baseline_run=other, candidate_run=private
    )
    _age(private, 40)

    result = retention.purge()

    assert result.kept_runs == [private.key]
    assert SimulationRun.objects.filter(pk=private.pk).exists()


def test_purge_command_reports(capsys, model):
    call_command("purge_simulation_runs", "--dry-run")
    assert "Would delete 0 run(s)" in capsys.readouterr().out


def test_models_without_runner_report_not_runnable(dcs):
    model = simulation_model(dcs, status=Status.PUBLISHED)
    assert APIClient().get(f"/api/v1/geosimulations/{model.pk}/").data["runnable"] is False
    assert SimulationModel.objects.get(pk=model.pk).runner_version == ""
