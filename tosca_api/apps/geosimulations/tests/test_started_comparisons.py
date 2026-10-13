"""Comparisons users start (construction impact): two runs with one seed, then the runner
computes the comparison once both succeeded."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.geosimulations import jobs, retention
from tosca_api.apps.geosimulations.models import (
    SimulationAsset,
    SimulationComparison,
    SimulationJob,
    SimulationRun,
    SimulationRunner,
    Status,
)

from .helpers import org, simulation_model
from .package_helpers import comparison_manifest, write_comparison_package, zip_dir
from .test_jobs import FakeRunner, _callback, needs_s3

pytestmark = pytest.mark.django_db

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "agents": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 100},
        "construction": {"type": "boolean", "default": True},
    },
}
QUOTAS = {
    "anonymous_active": 2,
    "anonymous_daily": 6,
    "all_anonymous_active": 4,
    "all_anonymous_daily": 100,
    "user_active": 2,
    "user_daily": 10,
    "organization_active": 5,
    "organization_daily": 50,
}
State = SimulationJob.State
Execution = SimulationRun.ExecutionState
BODY = {
    "baseline": {"parameters": {"agents": 30, "construction": False}},
    "candidate": {"parameters": {"agents": 30, "construction": True}},
    "seed": 7,
    "title": "With vs without construction",
}


@pytest.fixture(autouse=True)
def settings_for_jobs():
    with override_settings(GEOSIMULATIONS_QUOTAS=QUOTAS, GEOSIMULATIONS_CALLBACK_BASE_URL="http://testserver"):
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
        default_parameters={"agents": 100, "construction": True},
    )


@pytest.fixture
def cleanup(django_capture_on_commit_callbacks):
    yield
    with django_capture_on_commit_callbacks(execute=True):
        SimulationComparison.objects.all().delete()
        SimulationRun.objects.all().delete()
        SimulationAsset.objects.all().delete()


def _start(client, model, body=None):
    return client.post(f"/api/v1/geosimulations/{model.pk}/comparisons/", body or BODY, format="json")


def _comparison(response) -> SimulationComparison:
    return SimulationComparison.objects.select_related("baseline_run", "candidate_run", "job").get(
        pk=response.data["id"]
    )


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------


def test_starting_creates_two_runs_with_one_seed_and_a_waiting_comparison(model):
    response = _start(APIClient(REMOTE_ADDR="203.0.113.7"), model)

    assert response.status_code == 202 and response.data["reused"] is False
    assert response.data["execution_state"] == "queued" and response.data["visibility"] == "link"
    comparison = _comparison(response)
    baseline, candidate = comparison.baseline_run, comparison.candidate_run
    assert (baseline.parameters["construction"], candidate.parameters["construction"]) == (False, True)
    assert baseline.seed == candidate.seed == 7
    assert baseline.title == "With vs without construction · baseline"
    assert {baseline.visibility, candidate.visibility} == {"link"}
    assert comparison.job.state == State.WAITING
    assert (baseline.job.state, candidate.job.state) == (State.PENDING, State.PENDING)


def test_identical_requests_reuse_the_comparison(model):
    first = _start(APIClient(REMOTE_ADDR="203.0.113.7"), model)
    again = _start(APIClient(REMOTE_ADDR="198.51.100.4"), model)

    assert again.status_code == 200 and again.data["reused"] is True
    assert again.data["id"] == first.data["id"]
    assert SimulationRun.objects.count() == 2


def test_one_side_can_reuse_an_existing_run(model):
    single = APIClient(REMOTE_ADDR="203.0.113.7").post(
        f"/api/v1/geosimulations/{model.pk}/runs/",
        {"parameters": {"agents": 30, "construction": False}, "seed": 7},
        format="json",
    )

    response = _start(APIClient(REMOTE_ADDR="198.51.100.4"), model)

    assert response.status_code == 202
    assert str(_comparison(response).baseline_run_id) == single.data["id"]
    assert SimulationRun.objects.count() == 2


def test_identical_sides_and_quotas_are_refused_without_half_created_pairs(model):
    client = APIClient(REMOTE_ADDR="203.0.113.7")
    same = {**BODY, "candidate": BODY["baseline"]}
    assert _start(client, model, same).status_code == 400

    with override_settings(GEOSIMULATIONS_QUOTAS={**QUOTAS, "anonymous_active": 1}):
        refused = _start(client, model)
    assert refused.status_code == 429
    assert not SimulationRun.objects.exists() and not SimulationComparison.objects.exists()


def test_link_comparisons_open_by_id_but_are_not_listed(model):
    response = _start(APIClient(REMOTE_ADDR="203.0.113.7"), model)
    stranger = APIClient()

    detail = stranger.get(f"/api/v1/geosimulations/comparisons/{response.data['id']}/")

    assert detail.status_code == 200 and detail.data["execution_state"] == "queued"
    assert detail.data["execution"] == {"message": "", "error": "", "started_at": None, "finished_at": None}
    assert stranger.get("/api/v1/geosimulations/comparisons/").data["results"] == []
    assert stranger.get(f"/api/v1/geosimulations/{model.pk}/").data["comparisons"] == []


# ---------------------------------------------------------------------------
# Waiting, dispatch, import
# ---------------------------------------------------------------------------


def _finish_runs(comparison) -> None:
    """Both runs done (their own jobs too, so the worker never sends them anywhere)."""
    runs = [comparison.baseline_run_id, comparison.candidate_run_id]
    SimulationRun.objects.filter(pk__in=runs).update(execution_state=Execution.SUCCEEDED)
    SimulationJob.objects.filter(run_id__in=runs).update(state=State.SUCCEEDED)


def _claim_comparison_job(comparison, fake=None):
    with patch("tosca_api.apps.geosimulations.jobs.requests.post", fake or FakeRunner()):
        job = SimulationJob.objects.get(comparison=comparison)
        SimulationJob.objects.filter(pk=job.pk).update(available_at=timezone.now())
        jobs.process_job(SimulationJob.objects.get(pk=job.pk))
    job.refresh_from_db()
    return job


def test_the_comparison_waits_for_its_runs(model):
    comparison = _comparison(_start(APIClient(), model))

    job = _claim_comparison_job(comparison)

    assert job.state == State.WAITING and job.available_at > timezone.now()


def test_a_failed_run_fails_the_comparison(model):
    comparison = _comparison(_start(APIClient(), model))
    jobs._fail(comparison.candidate_run.job, "No route to Hbf")

    job = _claim_comparison_job(comparison)

    comparison.refresh_from_db()
    assert job.state == State.FAILED
    assert comparison.execution_state == Execution.FAILED
    assert comparison.execution_error == "The candidate run failed: No route to Hbf"


@needs_s3
def test_finished_runs_send_the_comparison_to_the_runner(model):
    comparison = _comparison(_start(APIClient(), model))
    _finish_runs(comparison)
    fake = FakeRunner()

    job = _claim_comparison_job(comparison, fake)

    payload = fake.payload
    assert job.state == State.DISPATCHED
    assert payload["kind"] == "comparison"
    assert payload["comparison"] == {"key": comparison.key, "title": comparison.title}
    assert payload["baseline"] == {
        "run_key": comparison.baseline_run.key,
        "parameters": {"agents": 30, "construction": False},
        "seed": 7,
    }
    assert payload["candidate"]["parameters"]["construction"] is True
    assert "run" not in payload and "parameters" not in payload


@needs_s3
def test_full_comparison_from_runs_to_imported_outputs(model, tmp_path, cleanup):
    import requests

    comparison = _comparison(_start(APIClient(), model))
    _finish_runs(comparison)
    fake = FakeRunner()
    job = _claim_comparison_job(comparison, fake)
    token = fake.payload["callback"]["token"]

    assert _callback(job.pk, token, {"state": "running", "message": "Comparing runs"}).data["state"] == "running"
    manifest = comparison_manifest(comparison.baseline_run.key, comparison.candidate_run.key)
    manifest["comparison"]["key"] = comparison.key
    write_comparison_package(tmp_path / "package", manifest)
    archive = zip_dir(tmp_path / "package", tmp_path / "package.zip")
    upload = fake.payload["upload"]
    requests.put(upload["url"], data=archive.read_bytes(), headers=upload["headers"], timeout=30).raise_for_status()
    assert _callback(job.pk, token, {"state": "succeeded"}).data["state"] == "uploaded"

    call_command("run_simulation_worker", "--once")

    comparison.refresh_from_db()
    assert comparison.execution_state == Execution.SUCCEEDED
    assert sorted(comparison.outputs.values_list("role", flat=True)) == ["flow_change", "summary"]
    detail = APIClient().get(f"/api/v1/geosimulations/comparisons/{comparison.pk}/")
    assert detail.data["execution_state"] == "succeeded" and len(detail.data["outputs"]) == 2


@needs_s3
def test_a_package_for_another_comparison_is_refused(model, tmp_path, cleanup):
    import requests

    comparison = _comparison(_start(APIClient(), model))
    _finish_runs(comparison)
    fake = FakeRunner()
    job = _claim_comparison_job(comparison, fake)
    write_comparison_package(tmp_path / "package", comparison_manifest("someone", "else"))
    archive = zip_dir(tmp_path / "package", tmp_path / "package.zip")
    upload = fake.payload["upload"]
    requests.put(upload["url"], data=archive.read_bytes(), headers=upload["headers"], timeout=30).raise_for_status()
    _callback(job.pk, fake.payload["callback"]["token"], {"state": "succeeded"})

    call_command("run_simulation_worker", "--once")

    comparison.refresh_from_db()
    assert comparison.execution_state == Execution.FAILED
    assert "expected" in comparison.execution_error


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def test_purge_removes_expired_comparisons_before_their_runs(model, django_capture_on_commit_callbacks):
    comparison = _comparison(_start(APIClient(), model))
    old = timezone.now() - timedelta(days=40)
    SimulationComparison.objects.filter(pk=comparison.pk).update(created_at=old)
    SimulationRun.objects.update(created_at=old)

    with django_capture_on_commit_callbacks(execute=True):
        result = retention.purge()

    assert result.deleted_comparisons == [comparison.key]
    assert len(result.deleted_runs) == 2 and result.kept_runs == []
    assert not SimulationRun.objects.exists()
