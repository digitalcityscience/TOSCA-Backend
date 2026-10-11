"""Runs started in TOSCA (Task 12.11): submission, dispatch, callbacks, import, worker.

The runner's HTTP endpoint is mocked; uploads go to the configured test storage
through real presigned URLs, and callbacks go through the real view.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
import requests
from django.core.files.storage import storages
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from tosca_api.apps.geosimulations import jobs
from tosca_api.apps.geosimulations.models import (
    SimulationAsset,
    SimulationJob,
    SimulationRun,
    SimulationRunner,
)

from .helpers import org, simulation_model
from .package_helpers import manifest_template, write_real_package, zip_dir

pytestmark = pytest.mark.django_db

S3 = hasattr(storages["default"], "bucket_name")
needs_s3 = pytest.mark.skipif(not S3, reason="runner uploads need S3 storage")
CALLBACK_BASE = "http://testserver"
SCHEMA = {
    "type": "object",
    "properties": {"agents": {"type": "integer", "minimum": 1, "maximum": 5000}},
    "required": ["agents"],
    "additionalProperties": False,
}
State = SimulationJob.State
Execution = SimulationRun.ExecutionState


@pytest.fixture(autouse=True)
def callback_base():
    with override_settings(GEOSIMULATIONS_CALLBACK_BASE_URL=CALLBACK_BASE):
        yield


@pytest.fixture
def dcs():
    return org("dcs")


@pytest.fixture
def runner(dcs):
    runner = SimulationRunner(organization=dcs, name="Bike runner", base_url="http://bike-runner:8080/")
    runner.set_token("runner-secret")
    runner.save()
    return runner


@pytest.fixture
def model(dcs, runner):
    return simulation_model(dcs, runner=runner, parameter_schema=SCHEMA)


@pytest.fixture
def cleanup(django_capture_on_commit_callbacks):
    yield
    with django_capture_on_commit_callbacks(execute=True):
        for run in SimulationRun.objects.all():
            run.delete()
        SimulationAsset.objects.all().delete()


class FakeRunner:
    """Stands in for ``requests.post`` to the runner; remembers the jobs it got."""

    def __init__(self, *, fail: Exception | None = None):
        self.fail = fail
        self.calls: list[dict] = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self.fail is not None:
            raise self.fail
        response = requests.Response()
        response.status_code = 202
        response._content = b'{"job_id": "runner-7"}'
        return response

    @property
    def payload(self) -> dict:
        return self.calls[-1]["json"]


def _dispatch(job, fake: FakeRunner) -> SimulationJob:
    with patch("tosca_api.apps.geosimulations.jobs.requests.post", fake):
        claimed = jobs.claim_next_job("test-worker")
        assert claimed is not None and claimed.pk == job.pk
        jobs.process_job(claimed)
    job.refresh_from_db()
    return job


def _callback(job_id, token, report):
    return APIClient().post(
        f"/api/v1/geosimulations/jobs/{job_id}/callback/",
        report,
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )


def _upload_package(payload: dict, tmp_path) -> None:
    """What the runner does: write a package for the run and PUT it to the presigned URL."""
    manifest = manifest_template()
    manifest["run"]["key"] = payload["run"]["key"]
    write_real_package(tmp_path / "package", manifest)
    archive = zip_dir(tmp_path / "package", tmp_path / "package.zip")
    upload = payload["upload"]
    response = requests.put(upload["url"], data=archive.read_bytes(), headers=upload["headers"], timeout=30)
    response.raise_for_status()


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def test_submit_creates_a_queued_run_and_a_pending_job(model, django_user_model):
    user = django_user_model.objects.create_user(username="planner")

    run = jobs.submit_run(model, {"agents": 50}, user=user, seed=7)

    assert run.execution_state == Execution.QUEUED
    assert run.key.startswith("run-") and run.model_version == ""
    assert (run.runner, run.created_by, run.seed) == (model.runner, user, 7)
    assert run.job.state == State.PENDING
    assert run.title.startswith("Hamburg bike simulation — ")


def test_submit_validates_parameters_and_needs_an_active_runner(model, runner):
    with pytest.raises(jobs.RunSubmissionError, match="agents: 9000 is greater than the maximum of 5000"):
        jobs.submit_run(model, {"agents": 9000})
    with pytest.raises(jobs.RunSubmissionError, match="'agents' is a required property"):
        jobs.submit_run(model, {})

    runner.is_active = False
    runner.save()
    with pytest.raises(jobs.RunSubmissionError, match="no active runner"):
        jobs.submit_run(model, {"agents": 5})
    assert not SimulationRun.objects.exists()


def test_runner_tokens_are_stored_encrypted(runner):
    runner.refresh_from_db()

    assert "runner-secret" not in runner.token_encrypted
    assert runner.get_token() == "runner-secret"
    assert runner.base_url == "http://bike-runner:8080"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@needs_s3
def test_dispatch_sends_the_job_with_upload_and_callback(model):
    run = jobs.submit_run(model, {"agents": 50}, seed=7)
    fake = FakeRunner()

    job = _dispatch(run.job, fake)

    call = fake.calls[0]
    assert call["url"] == "http://bike-runner:8080/jobs"
    assert call["headers"]["Authorization"] == "Bearer runner-secret"
    payload = fake.payload
    assert payload["job_id"] == str(job.pk)
    assert payload["model"] == {"key": "hamburg-bike-abm"}
    assert payload["run"]["key"] == run.key
    assert (payload["parameters"], payload["seed"]) == ({"agents": 50}, 7)
    assert payload["upload"]["method"] == "PUT" and "X-Amz-Signature" in payload["upload"]["url"]
    assert payload["callback"]["url"] == f"{CALLBACK_BASE}/api/v1/geosimulations/jobs/{job.pk}/callback/"
    assert (job.state, job.runner_job_id, job.attempts) == (State.DISPATCHED, "runner-7", 1)
    assert job.callback_token_hash and payload["callback"]["token"] not in job.callback_token_hash
    assert job.upload_path == f"geosimulations/_incoming/{job.pk}/package.zip"


@needs_s3
def test_a_busy_runner_keeps_the_job_waiting(model):
    first = jobs.submit_run(model, {"agents": 1})
    second = jobs.submit_run(model, {"agents": 2})
    fake = FakeRunner()
    _dispatch(first.job, fake)

    with patch("tosca_api.apps.geosimulations.jobs.requests.post", fake):
        claimed = jobs.claim_next_job("test-worker")
        jobs.process_job(claimed)
    second.job.refresh_from_db()

    assert len(fake.calls) == 1
    assert second.job.state == State.PENDING
    assert second.job.available_at > timezone.now()
    assert jobs.claim_next_job("test-worker") is None


@needs_s3
def test_failed_dispatches_are_retried_then_fail_the_run(model):
    run = jobs.submit_run(model, {"agents": 1})
    fake = FakeRunner(fail=requests.ConnectionError("runner down"))

    job = _dispatch(run.job, fake)
    assert (job.state, job.attempts) == (State.PENDING, 1)
    assert "runner down" in job.error and job.available_at > timezone.now()

    for _ in range(2):
        SimulationJob.objects.filter(pk=job.pk).update(available_at=timezone.now())
        job = _dispatch(job, fake)

    run.refresh_from_db()
    assert (job.state, job.attempts) == (State.FAILED, 3)
    assert run.execution_state == Execution.FAILED and "runner down" in run.execution_error


# ---------------------------------------------------------------------------
# Callbacks and import
# ---------------------------------------------------------------------------


@needs_s3
def test_full_job_from_dispatch_to_imported_run(model, tmp_path, cleanup, capsys):
    run = jobs.submit_run(model, {"agents": 1})
    fake = FakeRunner()
    job = _dispatch(run.job, fake)
    token = fake.payload["callback"]["token"]

    running = _callback(job.pk, token, {"state": "running", "message": "Routing 1 agent"})
    assert running.status_code == 202 and running.data["state"] == "running"
    run.refresh_from_db()
    assert run.execution_state == Execution.RUNNING

    _upload_package(fake.payload, tmp_path)
    done = _callback(job.pk, token, {"state": "succeeded"})
    assert done.status_code == 202 and done.data["state"] == "uploaded"

    call_command("run_simulation_worker", "--once", "--worker-id", "test-worker")

    job.refresh_from_db()
    run.refresh_from_db()
    assert job.state == State.SUCCEEDED and job.finished_at is not None
    assert run.execution_state == Execution.SUCCEEDED
    assert run.model_version == "0.2.0" and run.time_start is not None
    assert run.parameters == {"agents": 1}  # what the user asked for, not the manifest's
    assert sorted(run.outputs.values_list("role", flat=True)) == ["bike_flow", "kpis", "preview", "replay"]
    assert run.source_package is not None
    assert not storages["default"].exists(job.upload_path)
    assert "→ succeeded" in capsys.readouterr().out

    late = _callback(job.pk, token, {"state": "failed", "error": "late"})
    job.refresh_from_db()
    assert late.status_code == 202 and job.state == State.SUCCEEDED


@needs_s3
def test_callbacks_need_the_jobs_token(model):
    run = jobs.submit_run(model, {"agents": 1})
    job = _dispatch(run.job, FakeRunner())

    assert _callback(job.pk, "wrong", {"state": "running"}).status_code == 404
    assert _callback("01a12bf1-eeb5-706a-b2f6-fe3b8b238d26", "wrong", {"state": "running"}).status_code == 404
    job.refresh_from_db()
    assert job.state == State.DISPATCHED


@needs_s3
def test_bad_reports_and_missing_packages(model):
    run = jobs.submit_run(model, {"agents": 1})
    fake = FakeRunner()
    job = _dispatch(run.job, fake)
    token = fake.payload["callback"]["token"]

    assert _callback(job.pk, token, {"state": "exploded"}).status_code == 400
    response = _callback(job.pk, token, {"state": "succeeded"})

    run.refresh_from_db()
    assert response.data["state"] == "failed"
    assert run.execution_error == "The runner reported success but uploaded no package."


@needs_s3
def test_a_runner_failure_fails_the_run(model):
    run = jobs.submit_run(model, {"agents": 1})
    fake = FakeRunner()
    job = _dispatch(run.job, fake)

    _callback(job.pk, fake.payload["callback"]["token"], {"state": "failed", "error": "No route to Hbf"})

    run.refresh_from_db()
    assert run.execution_state == Execution.FAILED and run.execution_error == "No route to Hbf"


@needs_s3
def test_a_package_for_another_run_is_refused(model, tmp_path, cleanup):
    run = jobs.submit_run(model, {"agents": 1})
    fake = FakeRunner()
    job = _dispatch(run.job, fake)
    payload = {**fake.payload, "run": {"key": "someone-else"}}
    _upload_package(payload, tmp_path)
    _callback(job.pk, fake.payload["callback"]["token"], {"state": "succeeded"})

    call_command("run_simulation_worker", "--once")

    job.refresh_from_db()
    run.refresh_from_db()
    assert job.state == State.FAILED
    assert "expected" in run.execution_error and run.key in run.execution_error
    assert not run.outputs.exists()


# ---------------------------------------------------------------------------
# Timeouts, stale locks, retries
# ---------------------------------------------------------------------------


@needs_s3
def test_jobs_past_the_runner_timeout_fail(model):
    run = jobs.submit_run(model, {"agents": 1})
    job = _dispatch(run.job, FakeRunner())
    SimulationJob.objects.filter(pk=job.pk).update(dispatched_at=timezone.now() - timedelta(hours=7))

    assert jobs.fail_stale_jobs() == 1

    run.refresh_from_db()
    assert run.execution_state == Execution.FAILED and "360 minutes" in run.execution_error


def test_an_abandoned_import_is_claimed_again(model):
    run = jobs.submit_run(model, {"agents": 1})
    SimulationJob.objects.filter(pk=run.job.pk).update(
        state=State.IMPORTING, locked_by="dead-worker", locked_at=timezone.now() - timedelta(hours=1)
    )

    claimed = jobs.claim_next_job("new-worker")

    assert claimed.pk == run.job.pk and claimed.locked_by == "new-worker"
    assert claimed.state == State.IMPORTING


def test_failed_jobs_can_be_retried(model):
    run = jobs.submit_run(model, {"agents": 1})
    jobs._fail(run.job, "boom")

    jobs.retry_job(run.job)

    run.refresh_from_db()
    assert (run.job.state, run.job.attempts, run.execution_state) == (State.PENDING, 0, Execution.QUEUED)
    with pytest.raises(jobs.RunSubmissionError, match="Only failed jobs"):
        jobs.retry_job(run.job)


def test_start_simulation_run_command(model, tmp_path, capsys):
    params = tmp_path / "params.json"
    params.write_text('{"agents": 3}', encoding="utf-8")

    call_command("start_simulation_run", "hamburg-bike-abm", "--params", str(params))

    run = SimulationRun.objects.get()
    assert run.parameters == {"agents": 3}
    assert f"Queued run {run.key}" in capsys.readouterr().out
