"""Runners that speak OGC API – Processes (``ogc_runner.py`` and its use in ``jobs.py``).

All HTTP is faked (``FakeHttp``); packages land in the configured test storage.
"""

from __future__ import annotations

import io
import json
from datetime import timedelta
from unittest.mock import patch

import pytest
import requests
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.test import override_settings
from django.utils import timezone

from tosca_api.apps.geosimulations import jobs, ogc_runner
from tosca_api.apps.geosimulations.models import SimulationJob, SimulationRun, SimulationRunner

from .helpers import org, simulation_model

pytestmark = pytest.mark.django_db

State = SimulationJob.State
Execution = SimulationRun.ExecutionState
BASE = "http://ogc-runner:5000/api"
EXECUTE = f"{BASE}/processes/hamburg-bike-abm/execution"
STATUS = f"{BASE}/jobs/j-1"
RESULTS = f"{STATUS}/results"
SCHEMA = {
    "type": "object",
    "properties": {"agents": {"type": "integer", "minimum": 1}},
    "required": ["agents"],
    "additionalProperties": False,
}
PACKAGE = b"PK\x03\x04 not really a zip, but bytes to store"
UPLOAD_URL = "http://storage.test/package.zip?X-Amz-Signature=secret"


def reply(status=200, body=None, headers=None):
    """A factory for fresh ``requests.Response`` objects (bodies are streams)."""

    def make():
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers or {})
        raw = body if isinstance(body, bytes) else json.dumps(body if body is not None else {}).encode()
        response.raw = io.BytesIO(raw)
        return response

    return make


class FakeHttp:
    """Stands in for ``requests.request``; answers by (method, url) and records each call."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[dict] = []

    def __call__(self, method, url, json=None, headers=None, timeout=None, allow_redirects=True, stream=False):
        assert allow_redirects is False and timeout
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers or {}})
        answer = self.routes.get((method, url))
        if answer is None:
            raise AssertionError(f"unexpected {method} {url}")
        if isinstance(answer, Exception):
            raise answer
        return answer()

    def call(self, url) -> dict:
        return next(call for call in self.calls if call["url"] == url)


def _http(fake: FakeHttp):
    return patch("tosca_api.apps.geosimulations.ogc_runner.requests.request", fake)


@pytest.fixture
def dcs():
    return org("dcs")


@pytest.fixture
def runner(dcs):
    runner = SimulationRunner(
        organization=dcs,
        name="OGC runner",
        base_url=BASE,
        protocol=SimulationRunner.Protocol.OGC_API_PROCESSES,
        result_hosts=["results.example.org"],
    )
    runner.set_token("runner-secret")
    runner.save()
    return runner


@pytest.fixture
def push_model(dcs, runner):
    return simulation_model(
        dcs,
        runner=runner,
        parameter_schema=SCHEMA,
        transfer_mode="push",
        accepts_seed=True,
        process_inputs={"agents": 1, "tosca_seed": 1, "tosca_run": 1, "tosca_upload": 1},
    )


@pytest.fixture
def pull_model(dcs, runner):
    return simulation_model(
        dcs,
        runner=runner,
        parameter_schema=SCHEMA,
        transfer_mode="pull",
        accepts_seed=False,
        package_output="package",
        package_transmission="reference",
        process_inputs={"agents": 1},
    )


@pytest.fixture
def stored():
    """Removes what a test put at a job's incoming path."""
    names = []
    yield names
    for name in names:
        if storages["default"].exists(name):
            storages["default"].delete(name)


def _at_runner(model, state=State.DISPATCHED) -> SimulationJob:
    run = jobs.submit_run(model, {"agents": 1})
    now = timezone.now()
    SimulationJob.objects.filter(pk=run.job.pk).update(
        state=state,
        status_url=STATUS,
        runner_job_id="j-1",
        upload_path=jobs.upload_path(run.job),
        dispatched_at=now - timedelta(seconds=30),
        available_at=now - timedelta(seconds=1),
    )
    return SimulationJob.objects.get(pk=run.job.pk)


def _poll(job: SimulationJob, fake: FakeHttp) -> SimulationJob:
    with _http(fake):
        claimed = jobs.claim_next_job("test-worker")
        assert claimed is not None and claimed.pk == job.pk
        jobs.process_job(claimed)
    return SimulationJob.objects.select_related("run").get(pk=job.pk)


def _dispatch(job: SimulationJob, fake: FakeHttp) -> SimulationJob:
    with _http(fake), patch("tosca_api.apps.geosimulations.jobs.storage.presigned_upload_url", return_value=UPLOAD_URL):
        claimed = jobs.claim_next_job("test-worker")
        assert claimed.pk == job.pk
        jobs.process_job(claimed)
    return SimulationJob.objects.select_related("run").get(pk=job.pk)


# ---------------------------------------------------------------------------
# Process description → model
# ---------------------------------------------------------------------------

DESCRIPTION = {
    "id": "hamburg-bike-abm",
    "version": "1.4.0",
    "jobControlOptions": ["async-execute"],
    "outputTransmission": ["value", "reference"],
    "inputs": {
        "agents": {"title": "Cyclists", "schema": {"type": "integer", "minimum": 1, "default": 100}},
        "area": {"minOccurs": 0, "schema": {"type": "object"}},
        "stops": {"minOccurs": 1, "maxOccurs": 3, "schema": {"type": "string"}},
        "tosca_seed": {"minOccurs": 0, "schema": {"type": "integer"}},
    },
    "outputs": {
        "log": {"schema": {"type": "string"}},
        "package": {"schema": {"type": "string", "contentMediaType": "application/vnd.tosca.simulation-package+zip"}},
    },
}


def test_a_process_description_becomes_the_models_schema_and_flags():
    fields = ogc_runner.model_fields(DESCRIPTION)

    schema = fields["parameter_schema"]
    assert schema["required"] == ["agents", "stops"]
    assert schema["properties"]["agents"] == {"type": "integer", "minimum": 1, "default": 100, "title": "Cyclists"}
    assert schema["properties"]["area"] == {"type": "object"}
    assert schema["properties"]["stops"] == {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3}
    assert "tosca_seed" not in schema["properties"] and schema["additionalProperties"] is False
    assert fields["default_parameters"] == {"agents": 100}
    assert fields["runner_version"] == "1.4.0"
    assert (fields["transfer_mode"], fields["accepts_seed"]) == ("pull", True)
    assert (fields["package_output"], fields["package_transmission"]) == ("package", "reference")
    assert fields["process_inputs"] == {"agents": 1, "area": 1, "stops": 3, "tosca_seed": 1}


def test_a_process_with_tosca_upload_is_push_and_value_is_the_default_transmission():
    description = {
        **DESCRIPTION,
        "outputTransmission": ["value"],
        "inputs": {"tosca_upload": {"schema": {"type": "object"}}},
        "outputs": {},
    }

    fields = ogc_runner.model_fields(description)

    assert (fields["transfer_mode"], fields["accepts_seed"]) == ("push", False)
    assert fields["package_transmission"] == "value"
    assert fields["parameter_schema"]["properties"] == {}


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"jobControlOptions": ["sync-execute"]}, "asynchronous"),
        ({"inputs": {"tosca_secret": {"schema": {}}}}, "reserved"),
        ({"inputs": {"agents": {"maxOccurs": 0}}}, "maxOccurs"),
        ({"outputs": {"a": {"schema": {}}, "b": {"schema": {}}}}, "package output"),
        ({"outputs": {"echo": {"schema": {"contentMediaType": "application/json"}}}}, "package output"),
    ],
)
def test_unusable_processes_are_refused(change, error):
    with pytest.raises(ogc_runner.RunnerError, match=error):
        ogc_runner.model_fields({**DESCRIPTION, **change})


def test_sync_reads_each_models_process(dcs, runner):
    model = simulation_model(dcs, runner=runner)
    simulation_model(dcs, key="not-offered", runner=runner)
    fake = FakeHttp(
        {
            ("GET", f"{BASE}/processes/hamburg-bike-abm"): reply(body=DESCRIPTION),
            ("GET", f"{BASE}/processes/not-offered"): reply(404),
        }
    )

    with _http(fake):
        synced = jobs.sync_runner_models(runner)

    model.refresh_from_db()
    assert synced == [model]
    assert model.runner_version == "1.4.0" and model.transfer_mode == "pull"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer runner-secret"


# ---------------------------------------------------------------------------
# Execute request
# ---------------------------------------------------------------------------


def test_inputs_are_encoded_for_the_execute_request(dcs, runner):
    model = simulation_model(
        dcs,
        runner=runner,
        transfer_mode="push",
        process_inputs={"agents": 1, "area": 1, "weights": 1, "stops": "unbounded", "tosca_seed": 1, "tosca_run": 1,
                        "tosca_upload": 1},
    )
    parameters = {"agents": 5, "area": {"type": "Point"}, "weights": [1, 2], "stops": ["a", {"id": "b"}]}
    run = jobs.submit_run(model, parameters, seed=7)
    upload = {"url": UPLOAD_URL, "method": "PUT", "headers": {"Content-Type": "application/zip"}, "expires_at": "x"}

    body = ogc_runner.execute_request(run.job, upload=upload)

    inputs = body["inputs"]
    assert inputs["agents"] == 5
    assert inputs["area"] == {"value": {"type": "Point"}}
    assert inputs["weights"] == {"value": [1, 2]}
    assert inputs["stops"] == ["a", {"value": {"id": "b"}}]
    assert inputs["tosca_seed"] == 7
    assert inputs["tosca_run"] == {"value": {"key": run.key, "title": run.title, "job_id": str(run.job.pk)}}
    assert inputs["tosca_upload"] == {"value": upload} and "href" not in inputs["tosca_upload"]
    assert body["response"] == "document" and "outputs" not in body


def test_undeclared_tosca_inputs_are_not_sent(pull_model):
    run = jobs.submit_run(pull_model, {"agents": 2})

    body = ogc_runner.execute_request(run.job, upload={"url": UPLOAD_URL})

    assert body["inputs"] == {"agents": 2}
    assert body["outputs"] == {"package": {"transmissionMode": "reference"}} and body["response"] == "document"


def test_a_by_value_package_is_requested_raw(pull_model):
    pull_model.package_transmission = "value"
    pull_model.save()
    run = jobs.submit_run(pull_model, {"agents": 2})

    body = ogc_runner.execute_request(run.job)

    assert body["outputs"] == {"package": {"transmissionMode": "value"}} and body["response"] == "raw"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_dispatch_starts_an_asynchronous_job(push_model):
    run = jobs.submit_run(push_model, {"agents": 3}, seed=4)
    fake = FakeHttp({("POST", EXECUTE): reply(201, {"jobID": "j-1", "status": "accepted"}, {"Location": "jobs/j-1"})})

    job = _dispatch(run.job, fake)

    call = fake.calls[0]
    assert call["headers"]["Prefer"] == "respond-async"
    assert call["headers"]["Authorization"] == "Bearer runner-secret"
    assert call["json"]["inputs"]["tosca_upload"]["value"]["url"] == UPLOAD_URL
    assert (job.state, job.status_url, job.runner_job_id, job.attempts) == (State.DISPATCHED, STATUS, "j-1", 1)
    assert job.callback_token_hash == "" and job.available_at > timezone.now()
    assert (job.locked_by, job.locked_at) == ("", None)


def test_an_absolute_location_on_the_runner_is_accepted(pull_model):
    run = jobs.submit_run(pull_model, {"agents": 3})
    fake = FakeHttp({("POST", EXECUTE): reply(201, b"", {"Location": "/api/jobs/j-9"})})

    job = _dispatch(run.job, fake)

    assert (job.state, job.status_url, job.runner_job_id) == (State.DISPATCHED, f"{BASE}/jobs/j-9", "j-9")


def test_a_synchronous_answer_fails_without_a_retry(pull_model):
    run = jobs.submit_run(pull_model, {"agents": 3})
    fake = FakeHttp({("POST", EXECUTE): reply(200, {"package": "..."})})

    job = _dispatch(run.job, fake)

    assert (job.state, job.attempts, len(fake.calls)) == (State.FAILED, 1, 1)
    assert "synchronously" in job.run.execution_error


def test_a_location_off_the_runners_origin_fails(pull_model):
    run = jobs.submit_run(pull_model, {"agents": 3})
    fake = FakeHttp({("POST", EXECUTE): reply(201, {}, {"Location": "http://elsewhere.example/jobs/1"})})

    job = _dispatch(run.job, fake)

    assert job.state == State.FAILED and "own address" in job.error
    assert "elsewhere" not in job.error


def test_a_refused_execution_is_retried(pull_model):
    run = jobs.submit_run(pull_model, {"agents": 3})
    fake = FakeHttp({("POST", EXECUTE): reply(503)})

    job = _dispatch(run.job, fake)

    assert (job.state, job.attempts) == (State.PENDING, 1)
    assert "HTTP 503" in job.error and job.available_at > timezone.now()


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "state", "execution"),
    [
        ("accepted", State.DISPATCHED, Execution.QUEUED),
        ("running", State.RUNNING, Execution.RUNNING),
        ("failed", State.FAILED, Execution.FAILED),
        ("dismissed", State.FAILED, Execution.FAILED),
        ("exploded", State.DISPATCHED, Execution.QUEUED),
    ],
)
def test_runner_statuses_move_the_job(pull_model, status, state, execution):
    job = _at_runner(pull_model)
    fake = FakeHttp({("GET", STATUS): reply(body={"jobID": "j-1", "status": status, "message": "Routing"})})

    job = _poll(job, fake)

    assert (job.state, job.run.execution_state) == (state, execution)
    assert (job.locked_by, job.locked_at) == ("", None)
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer runner-secret"
    if status == "failed":
        assert job.run.execution_error == "Routing"
    if status == "dismissed":
        assert job.run.execution_error == "The job was dismissed at the runner."
    if state != State.FAILED:
        assert job.message == "Routing" and job.available_at > timezone.now()


def test_a_status_without_fields_does_not_crash(pull_model):
    job = _at_runner(pull_model, state=State.RUNNING)

    job = _poll(job, FakeHttp({("GET", STATUS): reply(body=["not", "an", "object"])}))

    assert job.state == State.RUNNING and job.locked_at is None


def test_an_unknown_job_fails_after_three_misses(pull_model):
    job = _at_runner(pull_model)
    fake = FakeHttp({("GET", STATUS): reply(404)})

    for missed in (1, 2):
        job = _poll(job, fake)
        assert (job.state, job.missed_polls) == (State.DISPATCHED, missed)
        SimulationJob.objects.filter(pk=job.pk).update(available_at=timezone.now())

    job = _poll(job, fake)
    assert job.state == State.FAILED and job.run.execution_error == "The runner no longer knows the job."


def test_a_found_job_resets_the_misses(pull_model):
    job = _at_runner(pull_model)
    SimulationJob.objects.filter(pk=job.pk).update(missed_polls=2)

    job = _poll(job, FakeHttp({("GET", STATUS): reply(body={"status": "running"})}))

    assert (job.state, job.missed_polls) == (State.RUNNING, 0)


@pytest.mark.parametrize("answer", [requests.ConnectionError("down"), reply(502)])
def test_network_errors_only_delay_the_next_poll(pull_model, answer):
    job = _at_runner(pull_model)

    job = _poll(job, FakeHttp({("GET", STATUS): answer}))

    assert (job.state, job.missed_polls, job.error) == (State.DISPATCHED, 0, "")
    assert job.available_at > timezone.now() and job.locked_at is None


def test_polls_back_off_from_5_to_60_seconds(pull_model):
    job = _at_runner(pull_model)
    now = timezone.now()

    def delay(age_seconds):
        job.dispatched_at = timezone.now() - timedelta(seconds=age_seconds)
        return (jobs._next_poll(job) - now).total_seconds()

    assert 5 <= delay(0) < 6
    assert 20 <= delay(100) < 21
    assert 60 <= delay(3600) < 61


def test_only_due_unlocked_jobs_of_ogc_runners_are_polled(dcs, pull_model):
    job = _at_runner(pull_model)
    v1_runner = SimulationRunner.objects.create(organization=dcs, name="v1", base_url="http://bike-runner:8080")
    v1_job = _at_runner(simulation_model(dcs, key="v1-model", runner=v1_runner))
    SimulationJob.objects.filter(pk=job.pk).update(locked_by="other", locked_at=timezone.now())

    assert jobs.claim_next_job("w") is None

    SimulationJob.objects.filter(pk=job.pk).update(locked_by="", locked_at=None)
    assert jobs.claim_next_job("w").pk == job.pk
    assert SimulationJob.objects.get(pk=v1_job.pk).locked_by == ""


def test_one_bad_poll_does_not_stop_the_worker(pull_model):
    job = _at_runner(pull_model)

    with patch.object(ogc_runner, "job_status", side_effect=KeyError("surprise")):
        claimed = jobs.claim_next_job("w")
        jobs.process_job(claimed)

    job.refresh_from_db()
    assert job.state == State.DISPATCHED and job.locked_at is None and job.available_at > timezone.now()


def test_stale_ogc_jobs_still_time_out(pull_model):
    job = _at_runner(pull_model)
    SimulationJob.objects.filter(pk=job.pk).update(dispatched_at=timezone.now() - timedelta(hours=7))

    assert jobs.fail_stale_jobs() == 1


# ---------------------------------------------------------------------------
# Package transfer
# ---------------------------------------------------------------------------


def test_push_success_with_the_package_is_uploaded(push_model, stored):
    job = _at_runner(push_model, state=State.RUNNING)
    stored.append(storages["default"].save(job.upload_path, ContentFile(PACKAGE)))

    job = _poll(job, FakeHttp({("GET", STATUS): reply(body={"status": "successful"})}))

    assert job.state == State.UPLOADED and job.available_at <= timezone.now()
    assert job.run.execution_state == Execution.RUNNING


def test_push_success_without_a_package_fails(push_model):
    job = _at_runner(push_model, state=State.RUNNING)

    job = _poll(job, FakeHttp({("GET", STATUS): reply(body={"status": "successful"})}))

    assert job.state == State.FAILED
    assert job.run.execution_error == "The runner reported success but uploaded no package."


def _stored_package(job, stored) -> bytes:
    stored.append(job.upload_path)
    with storages["default"].open(job.upload_path, "rb") as handle:
        return handle.read()


def test_pull_by_reference_downloads_from_a_result_host_without_the_token(pull_model, stored):
    job = _at_runner(pull_model, state=State.RUNNING)
    href = "https://results.example.org/j-1/package.zip?X-Amz-Signature=abc"
    fake = FakeHttp(
        {
            ("GET", STATUS): reply(body={"status": "successful"}),
            ("GET", RESULTS): reply(body={"package": {"href": href, "type": "application/zip"}}),
            ("GET", href): reply(body=PACKAGE),
        }
    )

    job = _poll(job, fake)

    assert job.state == State.UPLOADED
    assert _stored_package(job, stored) == PACKAGE
    assert fake.call(RESULTS)["headers"]["Authorization"] == "Bearer runner-secret"
    assert "Authorization" not in fake.call(href)["headers"]


def test_pull_by_value_streams_the_results(pull_model, stored):
    pull_model.package_transmission = "value"
    pull_model.save()
    job = _at_runner(pull_model, state=State.RUNNING)
    fake = FakeHttp(
        {
            ("GET", STATUS): reply(body={"status": "successful"}),
            ("GET", RESULTS): reply(body=PACKAGE, headers={"Content-Type": "application/zip"}),
        }
    )

    job = _poll(job, fake)

    assert job.state == State.UPLOADED and _stored_package(job, stored) == PACKAGE


def test_an_expired_link_is_asked_for_again(pull_model, stored):
    job = _at_runner(pull_model, state=State.RUNNING)
    old, new = "https://results.example.org/old.zip", "https://results.example.org/new.zip"
    documents = iter([{"package": {"href": old}}, {"package": {"href": new}}])
    fake = FakeHttp(
        {
            ("GET", STATUS): reply(body={"status": "successful"}),
            ("GET", old): reply(403),
            ("GET", new): reply(body=PACKAGE),
        }
    )
    fake.routes[("GET", RESULTS)] = lambda: reply(body=next(documents))()

    job = _poll(job, fake)

    assert job.state == State.UPLOADED and _stored_package(job, stored) == PACKAGE


def _pull_fails(job, routes) -> SimulationJob:
    fake = FakeHttp({("GET", STATUS): reply(body={"status": "successful"}), **routes})
    job = _poll(job, fake)
    assert job.state == State.FAILED
    assert not storages["default"].exists(jobs.upload_path(job))
    return job


def test_a_link_to_a_foreign_host_is_refused(pull_model):
    job = _at_runner(pull_model, state=State.RUNNING)

    job = _pull_fails(job, {("GET", RESULTS): reply(body={"package": {"href": "http://169.254.169.254/latest"}})})

    assert "not allowed" in job.run.execution_error and "169.254" not in job.run.execution_error


def test_a_redirect_to_a_foreign_host_is_refused(pull_model):
    job = _at_runner(pull_model, state=State.RUNNING)
    href = "https://results.example.org/package.zip"
    fake_routes = {
        ("GET", RESULTS): reply(body={"package": {"href": href}}),
        ("GET", href): reply(302, b"", {"Location": "http://internal.example/secret"}),
    }

    job = _pull_fails(job, fake_routes)

    assert "not allowed" in job.run.execution_error


def test_a_redirect_from_the_runner_to_a_result_host_drops_the_token(pull_model, stored):
    job = _at_runner(pull_model, state=State.RUNNING)
    href = f"{BASE}/jobs/j-1/package"
    target = "https://results.example.org/signed.zip"
    fake = FakeHttp(
        {
            ("GET", STATUS): reply(body={"status": "successful"}),
            ("GET", RESULTS): reply(body={"package": {"href": href}}),
            ("GET", href): reply(307, b"", {"Location": target}),
            ("GET", target): reply(body=PACKAGE),
        }
    )

    job = _poll(job, fake)

    assert job.state == State.UPLOADED and _stored_package(job, stored) == PACKAGE
    assert fake.call(href)["headers"]["Authorization"] == "Bearer runner-secret"
    assert "Authorization" not in fake.call(target)["headers"]


def test_the_runners_port_is_part_of_its_origin(pull_model):
    job = _at_runner(pull_model, state=State.RUNNING)

    _pull_fails(job, {("GET", RESULTS): reply(body={"package": {"href": "http://ogc-runner:6000/package.zip"}})})


@override_settings(GEOSIMULATIONS_PACKAGE_LIMITS={"max_package_bytes": 10})
def test_an_oversized_package_is_refused(pull_model):
    job = _at_runner(pull_model, state=State.RUNNING)
    href = "https://results.example.org/big.zip"

    job = _pull_fails(
        job, {("GET", RESULTS): reply(body={"package": {"href": href}}), ("GET", href): reply(body=PACKAGE)}
    )

    assert "larger than 10 bytes" in job.run.execution_error


def test_a_broken_download_is_tried_again_later(pull_model):
    job = _at_runner(pull_model, state=State.RUNNING)
    href = "https://results.example.org/package.zip"
    fake = FakeHttp(
        {
            ("GET", STATUS): reply(body={"status": "successful"}),
            ("GET", RESULTS): reply(body={"package": {"href": href}}),
            ("GET", href): requests.ConnectionError("reset"),
        }
    )

    job = _poll(job, fake)

    assert job.state == State.RUNNING and job.available_at > timezone.now()


# ---------------------------------------------------------------------------
# Rejections and error texts
# ---------------------------------------------------------------------------


def test_the_tosca_callback_rejects_jobs_of_ogc_runners(pull_model):
    job = _at_runner(pull_model)
    SimulationJob.objects.filter(pk=job.pk).update(callback_token_hash=jobs._hash_token("known"))

    with pytest.raises(jobs.CallbackRejected):
        jobs.handle_callback(job.pk, "known", {"state": "failed", "error": "forged"})

    job.refresh_from_db()
    assert job.state == State.DISPATCHED


def test_a_seed_needs_a_model_that_accepts_one(pull_model):
    with pytest.raises(jobs.RunSubmissionError, match="does not accept a seed"):
        jobs.start_run(pull_model, {"agents": 1}, user=None, seed=3)
    with pytest.raises(jobs.RunSubmissionError, match="does not accept a seed"):
        jobs.submit_run(pull_model, {"agents": 1}, seed=3)

    assert jobs.start_run(pull_model, {"agents": 1}, user=None).run.seed is None
    pull_model.accepts_seed = True
    pull_model.save()
    assert jobs.submit_run(pull_model, {"agents": 1}, seed=3).seed == 3


def test_comparisons_are_refused_for_ogc_models(pull_model):
    with pytest.raises(jobs.ComparisonsUnsupported) as raised:
        jobs.start_comparison(pull_model, {"agents": 1}, {"agents": 2}, user=None)

    assert isinstance(raised.value, jobs.RunnerUnavailable)


def test_links_and_hosts_are_stripped_from_shown_errors(pull_model):
    job = _at_runner(pull_model)
    message = (
        "Upload to https://s3.internal/bucket/p.zip?X-Amz-Signature=abc failed; "
        "HTTPConnectionPool(host='bike-runner', port=8080): Max retries exceeded with url: /jobs?token=t"
    )

    job = _poll(job, FakeHttp({("GET", STATUS): reply(body={"status": "failed", "message": message})}))

    shown = job.run.execution_error
    for secret in ("https://", "s3.internal", "Signature", "bike-runner", "token=t"):
        assert secret not in shown and secret not in job.error and secret not in job.message
    assert shown.startswith("Upload to [link removed] failed")


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


class Clock:
    """Stands in for ``ogc_runner.monotonic``."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    fake = Clock()
    with patch.object(ogc_runner, "monotonic", fake):
        yield fake


class Trickle:
    """A body that sends one byte per read while ``seconds`` pass each time; it never ends."""

    def __init__(self, clock, seconds):
        self.clock, self.seconds, self.reads = clock, seconds, 0

    def read(self, amount=-1):
        self.reads += 1
        self.clock.now += self.seconds
        return b"x"

    def close(self):
        pass


def trickling(body: Trickle, status=200, headers=None):
    def make():
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers or {})
        response.raw = body
        return response

    return make


def test_a_trickling_status_answer_is_cut_off_and_asked_again(pull_model, clock):
    job = _at_runner(pull_model)
    body = Trickle(clock, seconds=1)

    job = _poll(job, FakeHttp({("GET", STATUS): trickling(body)}))

    assert (job.state, job.error, job.locked_at) == (State.DISPATCHED, "", None)
    assert body.reads <= ogc_runner.JSON_DEADLINE_SECONDS + 1
    assert job.available_at > timezone.now()


def test_a_trickling_download_is_cut_off_and_tried_again_later(pull_model, clock):
    job = _at_runner(pull_model, state=State.RUNNING)
    href = "https://results.example.org/package.zip"
    body = Trickle(clock, seconds=60)
    fake = FakeHttp(
        {
            ("GET", STATUS): reply(body={"status": "successful"}),
            ("GET", RESULTS): reply(body={"package": {"href": href}}),
            ("GET", href): trickling(body),
        }
    )

    job = _poll(job, fake)

    assert job.state == State.RUNNING and job.locked_at is None
    assert body.reads <= ogc_runner.DOWNLOAD_DEADLINE_SECONDS / 60 + 1
    assert not storages["default"].exists(jobs.upload_path(job))


@override_settings(GEOSIMULATIONS_WORKER={**jobs.settings.GEOSIMULATIONS_WORKER, "lock_timeout_seconds": 600})
def test_the_download_budget_stays_below_the_lock_timeout():
    assert ogc_runner._download_budget() == 400
    with override_settings(GEOSIMULATIONS_WORKER={**jobs.settings.GEOSIMULATIONS_WORKER, "lock_timeout_seconds": 7200}):
        assert ogc_runner._download_budget() == ogc_runner.DOWNLOAD_DEADLINE_SECONDS


def test_a_trickling_execute_answer_is_cut_off_and_the_location_kept(pull_model, clock):
    run = jobs.submit_run(pull_model, {"agents": 3})
    body = Trickle(clock, seconds=1)
    fake = FakeHttp({("POST", EXECUTE): trickling(body, 201, {"Location": "jobs/j-1?sig=secret"})})

    with patch.object(ogc_runner.logger, "warning") as warning:
        job = _dispatch(run.job, fake)

    assert (job.state, job.runner_job_id, job.attempts) == (State.DISPATCHED, "j-1", 1)
    assert body.reads <= ogc_runner.JSON_DEADLINE_SECONDS + 1
    logged = " ".join(str(arg) for arg in warning.call_args.args)
    assert "could not read the execute answer" in logged and "sig=secret" not in logged


def test_no_request_starts_after_the_budget_ran_out(pull_model, clock):
    def slow_redirect():
        clock.now += ogc_runner.JSON_DEADLINE_SECONDS + 1
        return reply(302, b"", {"Location": "/api/elsewhere"})()

    fake = FakeHttp({("GET", f"{BASE}/processes/hamburg-bike-abm"): slow_redirect})

    with _http(fake), pytest.raises(ogc_runner.TemporaryRunnerError, match="too long"):
        ogc_runner.describe_process(pull_model.runner, "hamburg-bike-abm")
    assert len(fake.calls) == 1


def test_bodies_are_read_as_they_arrive():
    """A real urllib3 response: pieces come back without waiting for a full chunk."""
    import urllib3

    class Socket:
        closed = False

        def __init__(self):
            self.parts = [b'{"a"', b": 1}", b""]

        def read1(self, amount=-1):
            return self.parts.pop(0)

        def read(self, amount=-1):
            raise AssertionError("waited for a full chunk")

        def close(self):
            self.closed = True

    response = requests.Response()
    response.status_code = 200
    response.raw = urllib3.response.HTTPResponse(body=Socket(), preload_content=False)

    assert ogc_runner._read_json(response, ogc_runner.monotonic() + 30) == {"a": 1}


# ---------------------------------------------------------------------------
# Unparsable links, refused executions
# ---------------------------------------------------------------------------


def test_an_unparsable_location_fails_the_job_for_good(pull_model):
    run = jobs.submit_run(pull_model, {"agents": 3})
    fake = FakeHttp({("POST", EXECUTE): reply(201, {}, {"Location": "http://[x"})})

    job = _dispatch(run.job, fake)

    assert (job.state, job.attempts, job.locked_at) == (State.FAILED, 1, None)
    assert "not a valid link" in job.run.execution_error


@pytest.mark.parametrize(
    "routes",
    [
        {("GET", RESULTS): reply(body={"package": {"href": "http://[x/package.zip"}})},
        {("GET", RESULTS): reply(body={"package": {"href": "https://results.example.org:99999/p.zip"}})},
        {
            ("GET", RESULTS): reply(body={"package": {"href": "https://results.example.org/p.zip"}}),
            ("GET", "https://results.example.org/p.zip"): reply(302, b"", {"Location": "http://[x"}),
        },
    ],
)
def test_unparsable_links_in_results_fail_the_job(pull_model, routes):
    job = _at_runner(pull_model, state=State.RUNNING)

    job = _pull_fails(job, routes)

    assert "does not fetch" in job.run.execution_error


def test_an_unexpected_ogc_dispatch_error_counts_as_a_failed_attempt(pull_model):
    run = jobs.submit_run(pull_model, {"agents": 3})

    with patch.object(ogc_runner, "execute_request", side_effect=KeyError("surprise")):
        job = _dispatch(run.job, FakeHttp({}))
        assert (job.state, job.attempts, job.locked_at) == (State.PENDING, 1, None)
        for _ in range(2):
            SimulationJob.objects.filter(pk=job.pk).update(available_at=timezone.now())
            job = _dispatch(job, FakeHttp({}))

    assert (job.state, job.attempts) == (State.FAILED, 3)
    assert job.run.execution_state == Execution.FAILED


@pytest.mark.parametrize("status", [400, 401, 404, 422])
def test_a_refused_execution_fails_without_a_retry(pull_model, status):
    run = jobs.submit_run(pull_model, {"agents": 3})
    fake = FakeHttp({("POST", EXECUTE): reply(status, {"title": "bad input"})})

    job = _dispatch(run.job, fake)

    assert (job.state, job.attempts, len(fake.calls)) == (State.FAILED, 1, 1)
    assert f"HTTP {status}" in job.run.execution_error


@pytest.mark.parametrize("answer", [reply(408), reply(429), reply(500), requests.ReadTimeout("slow")])
def test_temporary_execution_problems_are_retried(pull_model, answer):
    run = jobs.submit_run(pull_model, {"agents": 3})

    job = _dispatch(run.job, FakeHttp({("POST", EXECUTE): answer}))

    assert (job.state, job.attempts) == (State.PENDING, 1) and job.available_at > timezone.now()


# ---------------------------------------------------------------------------
# Result hosts
# ---------------------------------------------------------------------------


@pytest.fixture
def https_runner(dcs):
    return SimulationRunner.objects.create(
        organization=dcs,
        name="Hosted runner",
        base_url="https://ogc.example.org/api",
        protocol=SimulationRunner.Protocol.OGC_API_PROCESSES,
        result_hosts=["results.example.org", "files.example.org:8443"],
    )


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://results.example.org/p.zip", True),
        ("https://results.example.org:443/p.zip", True),
        ("https://results.example.org:8443/p.zip", False),
        ("http://results.example.org/p.zip", False),
        ("https://files.example.org:8443/p.zip", True),
        ("https://files.example.org/p.zip", False),
        ("http://files.example.org:8443/p.zip", False),
        ("https://other.example.org/p.zip", False),
    ],
)
def test_result_hosts_match_scheme_host_and_port(https_runner, url, allowed):
    assert ogc_runner.on_result_host(https_runner, url, https_runner.result_hosts) is allowed


def test_an_http_runner_may_use_http_result_hosts_on_their_default_port(runner):
    assert ogc_runner.on_result_host(runner, "http://results.example.org/p.zip", runner.result_hosts)
    assert not ogc_runner.on_result_host(runner, "http://results.example.org:8080/p.zip", runner.result_hosts)


@pytest.mark.parametrize(
    ("base_url", "hosts", "valid"),
    [
        ("https://ogc.example.org", ["Results.Example.org", "files.example.org:8443"], True),
        ("https://ogc.example.org", ["files.example.org:abc"], False),
        ("https://ogc.example.org", ["files.example.org:0"], False),
        ("https://ogc.example.org", ["files.example.org/path"], False),
        ("https://ogc.example.org", ["[::1]"], False),
        ("https://ogc.example.org", ["10.0.0.1"], False),
        ("https://ogc.example.org", ["127.1"], False),
        ("https://ogc.example.org", ["2130706433"], False),
        ("https://ogc.example.org", ["localhost:9000"], False),
        ("http://ogc-runner:5000", ["10.0.0.1:9000", "localhost", "minio:9000"], True),
    ],
)
def test_result_hosts_are_validated(dcs, base_url, hosts, valid):
    from django.core.exceptions import ValidationError

    runner = SimulationRunner(organization=dcs, name="r", base_url=base_url, result_hosts=hosts)
    if valid:
        runner.full_clean()
        assert runner.result_hosts == [host.lower() for host in hosts]
    else:
        with pytest.raises(ValidationError) as raised:
            runner.full_clean()
        assert "result_hosts" in raised.value.message_dict


# ---------------------------------------------------------------------------
# Raced pulls
# ---------------------------------------------------------------------------


def _fetch_and(change):
    def fetch(job, target):
        target.write_bytes(PACKAGE)
        SimulationJob.objects.filter(pk=job.pk).update(**change(job))

    return patch.object(ogc_runner, "fetch_package", side_effect=fetch)


def test_a_package_pulled_for_a_job_that_moved_on_is_removed(pull_model, stored):
    job = _at_runner(pull_model, state=State.RUNNING)
    stored.append(job.upload_path)

    with _fetch_and(lambda job: {"state": State.FAILED, "locked_by": "", "locked_at": None}):
        job = _poll(job, FakeHttp({("GET", STATUS): reply(body={"status": "successful"})}))

    assert job.state == State.FAILED
    assert not storages["default"].exists(job.upload_path)


def test_a_package_another_worker_handed_over_is_kept(pull_model, stored):
    job = _at_runner(pull_model, state=State.RUNNING)
    stored.append(job.upload_path)

    with _fetch_and(lambda job: {"state": State.UPLOADED, "locked_by": "", "locked_at": None}):
        job = _poll(job, FakeHttp({("GET", STATUS): reply(body={"status": "successful"})}))

    assert job.state == State.UPLOADED
    assert storages["default"].exists(job.upload_path)
