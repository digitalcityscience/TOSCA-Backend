"""Celery tasks of GeoSimulations (``TOSCA_TASK_BACKEND="celery"``).

Same work as one iteration of ``run_simulation_worker``. The queue lives in
Postgres; a task only means "look at the queue now" and takes no job data.
"""

import logging
import time

from celery import shared_task

from tosca_api.apps.geosimulations import jobs, retention

logger = logging.getLogger(__name__)

#: ``drain`` stops claiming new jobs after this long; the next beat tick continues
#: (keeps the task well inside the Celery task time limit).
DRAIN_BUDGET_SECONDS = 60


@shared_task(name="geosim.drain")
def drain() -> int:
    started = time.monotonic()
    jobs.fail_stale_jobs()
    worker_id = jobs.default_worker_id()
    processed = 0
    while time.monotonic() - started < DRAIN_BUDGET_SECONDS and (job := jobs.claim_next_job(worker_id)) is not None:
        try:
            jobs.process_job(job)
        except Exception:
            logger.exception("Simulation job %s failed unexpectedly.", job.pk)
        processed += 1
    return processed


@shared_task(name="geosim.purge")
def purge() -> None:
    retention.purge()
