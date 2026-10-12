"""Runs started in TOSCA: submission, runner dispatch, callbacks and import.

A started run has one ``SimulationJob``. Its life::

    pending ──dispatch──▶ dispatched ──callback "running"──▶ running
                              │                                  │
                              └──────callback "succeeded"────────┴──▶ uploaded ──import──▶ succeeded
    any non-terminal state ──callback "failed" / timeout / import error──▶ failed

``run_simulation_worker`` calls ``claim_next_job()`` and ``process_job()`` in a
loop, plus ``fail_stale_jobs()``. The functions hold all the logic, so a later
Celery + Redis worker only replaces that loop. The run mirrors the job in
``execution_state`` (queued → running → succeeded / failed).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import socket
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import Case, Q, When
from django.urls import reverse
from django.utils import timezone
from jsonschema import Draft202012Validator

from tosca_api.apps.core.models import MediaAsset

from . import storage
from .importer import PackageImportError, import_package
from .models import SimulationJob, SimulationModel, SimulationRun
from .packages import PackageValidationError

logger = logging.getLogger(__name__)

UPLOAD_ALIAS = MediaAsset.StorageAlias.DEFAULT
PACKAGE_CONTENT_TYPE = "application/zip"
RETRY_DELAYS = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10))
BUSY_RUNNER_DELAY = timedelta(seconds=30)

State = SimulationJob.State
ExecutionState = SimulationRun.ExecutionState


class RunSubmissionError(Exception):
    """The run cannot be started (no runner, invalid parameters)."""

    def __init__(self, message: str, *, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or [message]


class RunnerUnavailable(RunSubmissionError):
    """The model has no active runner, so runs cannot be started."""


class QuotaExceeded(RunSubmissionError):
    """Too many runs in progress or started today; try again after ``retry_after`` seconds."""

    def __init__(self, message: str, *, retry_after: int):
        super().__init__(message)
        self.retry_after = max(1, retry_after)


@dataclass(frozen=True)
class StartResult:
    run: SimulationRun
    #: An identical readable run was returned instead of starting a new one.
    reused: bool


def _worker_settings() -> dict:
    return settings.GEOSIMULATIONS_WORKER


# -- submission -----------------------------------------------------------------


def parameter_errors(model: SimulationModel, parameters: dict) -> list[str]:
    """Problems with ``parameters`` against the model's JSON Schema (none without a schema)."""
    if not isinstance(parameters, dict):
        return ["Parameters must be a JSON object."]
    if not model.parameter_schema:
        return []
    validator = Draft202012Validator(model.parameter_schema)
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or 'parameters'}: {error.message}"
        for error in sorted(validator.iter_errors(parameters), key=lambda error: list(error.absolute_path))
    ]


def resolve_parameters(model: SimulationModel, parameters: dict) -> dict:
    """The model's defaults overridden by ``parameters``, validated; raises ``RunSubmissionError``.

    Runs store resolved parameters, so a request that spells out a default and
    one that leaves it out get the same ``params_hash``.
    """
    if not isinstance(parameters, dict):
        raise RunSubmissionError("Parameters must be a JSON object.")
    resolved = {**(model.default_parameters or {}), **parameters}
    errors = parameter_errors(model, resolved)
    if errors:
        raise RunSubmissionError("Invalid parameters: " + "; ".join(errors), errors=errors)
    return resolved


def _require_runner(model: SimulationModel):
    runner = model.runner
    if runner is None or not runner.is_active:
        raise RunnerUnavailable(f"Simulation model {model.key!r} has no active runner.")
    return runner


def submit_run(
    model: SimulationModel,
    parameters: dict,
    *,
    user=None,
    organization=None,
    title: str | None = None,
    seed: int | None = None,
    visibility: str = SimulationRun.Visibility.ORGANIZATION,
) -> SimulationRun:
    """Create a queued run and its pending job; the worker sends it to the model's runner."""
    runner = _require_runner(model)
    resolved = resolve_parameters(model, parameters)
    started = timezone.now()
    with transaction.atomic():
        run = SimulationRun.objects.create(
            model=model,
            key=f"run-{started:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}",
            title=title or f"{model.title} — {timezone.localtime(started):%Y-%m-%d %H:%M}",
            model_version=model.runner_version,
            parameters=resolved,
            seed=seed,
            visibility=visibility,
            execution_state=ExecutionState.QUEUED,
            runner=runner,
            created_by=user,
            submitted_org=organization,
        )
        SimulationJob.objects.create(run=run, runner=runner)
    return run


def find_reusable_run(readable_runs, model: SimulationModel, parameters: dict, seed: int | None):
    """A readable run of ``model`` with the same resolved parameters, seed and version.

    Finished runs come first, then runs still in progress; failed runs never.
    """
    params_hash = SimulationRun.compute_params_hash(model.key, model.runner_version, parameters)
    complete = [ExecutionState.SUCCEEDED, ExecutionState.IMPORTED]
    return (
        readable_runs.filter(
            model=model,
            params_hash=params_hash,
            seed=seed,
            execution_state__in=[*complete, *SimulationRun.ACTIVE_STATES],
        )
        .annotate(_complete=Case(When(execution_state__in=complete, then=0), default=1))
        .order_by("_complete", "-created_at")
        .first()
    )


def check_quotas(user, organization) -> None:
    """Raise ``QuotaExceeded`` when the user or their organization started too many runs."""
    quotas = settings.GEOSIMULATIONS_QUOTAS
    now = timezone.now()
    since = now - timedelta(hours=24)
    scopes = [("You", "user", SimulationRun.objects.filter(created_by=user, job__isnull=False))]
    if organization is not None:
        scopes.append(
            ("Your organization", "organization", SimulationRun.objects.filter(submitted_org=organization))
        )
    for who, scope, runs in scopes:
        active_limit = quotas[f"{scope}_active"]
        if runs.filter(execution_state__in=SimulationRun.ACTIVE_STATES).count() >= active_limit:
            raise QuotaExceeded(
                f"{who} already have {active_limit} runs in progress; wait until one finishes.",
                retry_after=60,
            )
        daily_limit = quotas[f"{scope}_daily"]
        today = runs.filter(created_at__gte=since)
        if today.count() >= daily_limit:
            oldest = today.order_by("created_at").values_list("created_at", flat=True).first()
            raise QuotaExceeded(
                f"{who} started {daily_limit} runs in the last 24 hours, the daily limit.",
                retry_after=int((oldest + timedelta(hours=24) - now).total_seconds()),
            )


def start_run(
    model: SimulationModel,
    parameters: dict,
    *,
    user,
    organization=None,
    readable_runs=None,
    seed: int | None = None,
    title: str | None = None,
    visibility: str = SimulationRun.Visibility.PRIVATE,
    exempt: bool = False,
) -> StartResult:
    """Start a run for a user, or return an identical run they can read (``params_hash`` reuse).

    Quotas apply only to new runs and not to platform-exempt users.
    """
    _require_runner(model)
    resolved = resolve_parameters(model, parameters)
    if readable_runs is not None:
        existing = find_reusable_run(readable_runs, model, resolved, seed)
        if existing is not None:
            return StartResult(existing, reused=True)
    if not exempt:
        check_quotas(user, organization)
    run = submit_run(
        model,
        resolved,
        user=user,
        organization=organization,
        title=title,
        seed=seed,
        visibility=visibility,
    )
    return StartResult(run, reused=False)


def sync_runner_models(runner) -> list[SimulationModel]:
    """Copy parameter schema, defaults and version from ``{base_url}/models`` to the runner's models."""
    response = requests.get(
        f"{runner.base_url}/models",
        headers={"Authorization": f"Bearer {runner.get_token()}"},
        timeout=_worker_settings()["dispatch_timeout_seconds"],
    )
    response.raise_for_status()
    described = {item["key"]: item for item in response.json() if isinstance(item, dict) and "key" in item}
    updated = []
    for model in SimulationModel.objects.filter(runner=runner, key__in=described):
        item = described[model.key]
        schema = item.get("parameter_schema")
        if schema is not None:
            Draft202012Validator.check_schema(schema)
        model.parameter_schema = schema
        model.default_parameters = item.get("default_parameters") or {}
        model.runner_version = str(item.get("version") or "")[:50]
        model.save()
        updated.append(model)
    return updated


# -- worker queue ---------------------------------------------------------------


def claim_next_job(worker_id: str) -> SimulationJob | None:
    """Lock the next job the worker should act on, or ``None``.

    Pending jobs are dispatched, uploaded jobs imported. An import whose worker
    died (lock older than ``lock_timeout_seconds``) is claimed again.
    """
    now = timezone.now()
    stale_lock = now - timedelta(seconds=_worker_settings()["lock_timeout_seconds"])
    with transaction.atomic():
        job = (
            SimulationJob.objects.select_for_update(skip_locked=True)
            .filter(
                Q(state__in=[State.PENDING, State.UPLOADED], available_at__lte=now)
                | Q(state=State.IMPORTING, locked_at__lt=stale_lock)
            )
            .order_by("available_at", "created_at")
            .first()
        )
        if job is None:
            return None
        job.locked_by, job.locked_at = worker_id, now
        if job.state in (State.UPLOADED, State.IMPORTING):
            job.state = State.IMPORTING
        job.save(update_fields=["locked_by", "locked_at", "state", "updated_at"])
    return job


def process_job(job: SimulationJob) -> None:
    if job.state == State.PENDING:
        dispatch(job)
    elif job.state == State.IMPORTING:
        import_uploaded_package(job)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{secrets.token_hex(3)}"


# -- dispatch ---------------------------------------------------------------------


def callback_url(job: SimulationJob) -> str:
    base = settings.GEOSIMULATIONS_CALLBACK_BASE_URL
    if not base:
        raise ImproperlyConfigured("Set GEOSIMULATIONS_CALLBACK_BASE_URL so runners can report back.")
    return base + reverse("simulationjob-callback", args=[job.pk])


def upload_path(job: SimulationJob) -> str:
    return f"geosimulations/_incoming/{job.pk}/package.zip"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def job_payload(job: SimulationJob, *, callback_token: str, upload_url: str) -> dict:
    """What a runner receives (``POST {base_url}/jobs``)."""
    run, runner = job.run, job.runner
    expires_at = timezone.now() + timedelta(minutes=runner.timeout_minutes)
    return {
        "job_id": str(job.pk),
        "model": {"key": run.model.key},
        "run": {"key": run.key, "title": run.title},
        "parameters": run.parameters,
        "seed": run.seed,
        "upload": {
            "url": upload_url,
            "method": "PUT",
            "headers": {"Content-Type": PACKAGE_CONTENT_TYPE},
            "expires_at": expires_at.isoformat(),
        },
        "callback": {"url": callback_url(job), "token": callback_token},
    }


def dispatch(job: SimulationJob) -> None:
    """Send a pending job to its runner; retry later on failure, fail after the last attempt."""
    runner = job.runner
    busy = SimulationJob.objects.filter(runner=runner, state__in=SimulationJob.AT_RUNNER_STATES).count()
    if busy >= runner.max_concurrent_jobs:
        _release(job, available_at=timezone.now() + BUSY_RUNNER_DELAY)
        return
    if not runner.is_active:
        _fail(job, f"Runner {runner.name!r} is not active.")
        return
    token = secrets.token_urlsafe(32)
    try:
        upload_url = storage.presigned_upload_url(
            UPLOAD_ALIAS,
            upload_path(job),
            content_type=PACKAGE_CONTENT_TYPE,
            expires_in=runner.timeout_minutes * 60,
        )
        payload = job_payload(job, callback_token=token, upload_url=upload_url)
        response = requests.post(
            f"{runner.base_url}/jobs",
            json=payload,
            headers={"Authorization": f"Bearer {runner.get_token()}"},
            timeout=_worker_settings()["dispatch_timeout_seconds"],
        )
        response.raise_for_status()
        runner_job_id = str((response.json() or {}).get("job_id", ""))[:255]
    except (requests.RequestException, ValueError, ImproperlyConfigured, storage.UploadUrlUnavailable) as exc:
        _retry_dispatch(job, exc)
        return
    now = timezone.now()
    job.state = State.DISPATCHED
    job.attempts += 1
    job.runner_job_id = runner_job_id
    job.callback_token_hash = _hash_token(token)
    job.upload_path = upload_path(job)
    job.dispatched_at = now
    job.error = ""
    job.locked_by, job.locked_at = "", None
    job.save()


def _retry_dispatch(job: SimulationJob, exc: Exception) -> None:
    job.attempts += 1
    message = f"Dispatch to {job.runner.name!r} failed: {exc}"
    logger.warning("Simulation job %s: %s", job.pk, message)
    if job.attempts >= _worker_settings()["max_dispatch_attempts"]:
        _fail(job, message)
        return
    delay = RETRY_DELAYS[min(job.attempts - 1, len(RETRY_DELAYS) - 1)]
    job.error = message
    _release(job, available_at=timezone.now() + delay)


def _release(job: SimulationJob, *, available_at) -> None:
    job.available_at = available_at
    job.locked_by, job.locked_at = "", None
    job.save()


def _fail(job: SimulationJob, error: str) -> None:
    now = timezone.now()
    job.state = State.FAILED
    job.error = error
    job.finished_at = now
    job.locked_by, job.locked_at = "", None
    job.save()
    run = job.run
    run.execution_state = ExecutionState.FAILED
    run.execution_error = error
    run.save()


# -- callbacks --------------------------------------------------------------------


class CallbackRejected(Exception):
    """Unknown job or wrong token; answered like an unknown job."""


def check_callback_token(job: SimulationJob, token: str) -> None:
    if not job.callback_token_hash or not hmac.compare_digest(job.callback_token_hash, _hash_token(token or "")):
        raise CallbackRejected


def handle_callback(job_id, token: str, report: dict) -> SimulationJob:
    """Apply a runner's report: ``{"state": "running" | "succeeded" | "failed", "message"?, "error"?}``."""
    state = report.get("state")
    if state not in ("running", "succeeded", "failed"):
        raise ValidationError({"state": "Expected running, succeeded or failed."})
    with transaction.atomic():
        job = SimulationJob.objects.select_for_update().select_related("run").filter(pk=job_id).first()
        if job is None:
            raise CallbackRejected
        check_callback_token(job, token)
        now = timezone.now()
        job.last_callback_at = now
        job.message = str(report.get("message") or "")[:500]
        if job.state not in SimulationJob.AT_RUNNER_STATES:
            # Late or repeated reports do not move a job that already went on.
            job.save(update_fields=["last_callback_at", "message", "updated_at"])
            return job
        run = job.run
        if state == "running":
            job.state = State.RUNNING
            job.started_at = job.started_at or now
            run.execution_state = ExecutionState.RUNNING
            run.save()
        elif state == "succeeded":
            if not storages[UPLOAD_ALIAS].exists(job.upload_path):
                job.save()
                _fail(job, "The runner reported success but uploaded no package.")
                return job
            job.state = State.UPLOADED
            job.available_at = now
            run.execution_state = ExecutionState.RUNNING
            run.save()
        else:
            job.save()
            _fail(job, str(report.get("error") or "The runner reported a failure.")[:5000])
            return job
        job.save()
    return job


# -- import -----------------------------------------------------------------------


def import_uploaded_package(job: SimulationJob) -> None:
    """Import the runner's package into the job's run, then remove the upload."""
    run = job.run
    try:
        with tempfile.TemporaryDirectory(prefix="geosim-job-") as staging:
            package = Path(staging) / "package.zip"
            storage.download_to(UPLOAD_ALIAS, job.upload_path, package)
            result = import_package(
                package,
                organization=run.model.organization,
                user=run.created_by,
                into_run=run,
            )
    except PackageValidationError as exc:
        _fail(job, "The runner's package is invalid:\n" + "\n".join(f"- {issue}" for issue in exc.issues))
        return
    except PackageImportError as exc:
        _fail(job, f"Import failed: {exc}")
        return
    except Exception as exc:  # keep the worker alive; the job records what went wrong
        logger.exception("Importing simulation job %s failed", job.pk)
        _fail(job, f"Import failed unexpectedly: {exc!r}")
        return
    job.state = State.SUCCEEDED
    job.finished_at = timezone.now()
    job.message = "; ".join(result.warnings)[:500]
    job.locked_by, job.locked_at = "", None
    job.save()
    try:
        storage.delete(UPLOAD_ALIAS, job.upload_path)
    except Exception:
        logger.exception("Could not remove the uploaded package of job %s", job.pk)


# -- timeouts ---------------------------------------------------------------------


def fail_stale_jobs() -> int:
    """Fail jobs a runner holds longer than its ``timeout_minutes``; returns how many."""
    now = timezone.now()
    failed = 0
    stale = SimulationJob.objects.filter(state__in=SimulationJob.AT_RUNNER_STATES).select_related("runner")
    for job in stale:
        if job.dispatched_at and job.dispatched_at + timedelta(minutes=job.runner.timeout_minutes) < now:
            with transaction.atomic():
                locked = SimulationJob.objects.select_for_update().filter(
                    pk=job.pk, state__in=SimulationJob.AT_RUNNER_STATES
                ).first()
                if locked is None:
                    continue
                _fail(locked, f"The runner did not finish within {job.runner.timeout_minutes} minutes.")
                failed += 1
    return failed


def retry_job(job: SimulationJob) -> None:
    """Start a failed job again from dispatch (the run is simulated again)."""
    if job.state != State.FAILED:
        raise RunSubmissionError("Only failed jobs can be retried.")
    run = job.run
    if run.outputs.exists():
        raise RunSubmissionError("The run already has outputs; start a new run instead.")
    job.state = State.PENDING
    job.attempts = 0
    job.error = ""
    job.message = ""
    job.callback_token_hash = ""
    job.available_at = timezone.now()
    job.dispatched_at = job.started_at = job.finished_at = None
    job.save()
    run.execution_state = ExecutionState.QUEUED
    run.execution_error = ""
    run.save()
