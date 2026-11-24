"""Celery tasks (optional): ``geosim.drain`` does what one worker iteration does."""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("celery")

from tosca_api.apps.geosimulations import jobs, tasks  # noqa: E402
from tosca_api.apps.geosimulations.models import SimulationRunner  # noqa: E402

from .helpers import org, simulation_model  # noqa: E402

pytestmark = pytest.mark.django_db


def test_drain_processes_every_due_job():
    dcs = org("dcs")
    runner = SimulationRunner(organization=dcs, name="Runner", base_url="http://runner:8080/")
    runner.set_token("secret")
    runner.save()
    model = simulation_model(dcs, runner=runner, parameter_schema={"type": "object"})
    first = jobs.submit_run(model, {}, seed=1).job
    second = jobs.submit_run(model, {}, seed=2).job
    processed = []

    with (
        patch.object(jobs, "process_job", lambda job: processed.append(job.pk)),
        patch.object(jobs, "fail_stale_jobs", return_value=0) as stale,
    ):
        count = tasks.drain.apply().get()

    assert count == 2 and sorted(processed) == sorted([first.pk, second.pk])
    stale.assert_called_once_with()
    # Nothing is left due: a second drain claims nothing.
    with patch.object(jobs, "process_job") as process:
        assert tasks.drain.apply().get() == 0
    process.assert_not_called()


def _two_jobs():
    dcs = org("dcs")
    runner = SimulationRunner(organization=dcs, name="Runner", base_url="http://runner:8080/")
    runner.set_token("secret")
    runner.save()
    model = simulation_model(dcs, runner=runner, parameter_schema={"type": "object"})
    return jobs.submit_run(model, {}, seed=1).job, jobs.submit_run(model, {}, seed=2).job


def test_drain_continues_after_a_job_raises():
    _two_jobs()
    calls = []

    def boom(job):
        calls.append(job.pk)
        raise RuntimeError("boom")

    with patch.object(jobs, "process_job", boom), patch.object(jobs, "fail_stale_jobs", return_value=0):
        count = tasks.drain.apply().get()

    assert count == 2 and len(calls) == 2


def test_drain_stops_claiming_jobs_after_its_time_budget():
    _two_jobs()
    processed = []
    clock = iter([0, 0, tasks.DRAIN_BUDGET_SECONDS + 1, tasks.DRAIN_BUDGET_SECONDS + 1])

    with (
        patch.object(tasks.time, "monotonic", lambda: next(clock)),
        patch.object(jobs, "process_job", lambda job: processed.append(job.pk)),
        patch.object(jobs, "fail_stale_jobs", return_value=0),
    ):
        count = tasks.drain.apply().get()

    assert count == 1 and len(processed) == 1


def test_purge_runs_the_retention_policy():
    with patch.object(tasks.retention, "purge") as purge:
        tasks.purge.apply().get()
    purge.assert_called_once_with()
