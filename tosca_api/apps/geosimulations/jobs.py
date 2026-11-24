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

Jobs of ``ogc-api-processes`` runners take the same states, but nothing calls
back: the worker claims dispatched / running jobs again when ``available_at``
passed and polls the runner (``poll_job``, HTTP in ``ogc_runner.py``).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
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

from . import ogc_runner, storage
from .importer import PackageImportError, import_package
from .models import SimulationComparison, SimulationJob, SimulationModel, SimulationRun, SimulationRunner
from .packages import PackageValidationError

logger = logging.getLogger(__name__)

UPLOAD_ALIAS = MediaAsset.StorageAlias.DEFAULT
PACKAGE_CONTENT_TYPE = "application/zip"
RETRY_DELAYS = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10))
BUSY_RUNNER_DELAY = timedelta(seconds=30)
WAITING_POLL = timedelta(seconds=10)
POLL_MIN_SECONDS, POLL_MAX_SECONDS = 5, 60
MAX_MISSED_POLLS = 3

State = SimulationJob.State
ExecutionState = SimulationRun.ExecutionState


class RunSubmissionError(Exception):
    """The run cannot be started (no runner, invalid parameters)."""

    def __init__(self, message: str, *, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or [message]


class RunnerUnavailable(RunSubmissionError):
    """The model has no active runner, so runs cannot be started."""


class ComparisonsUnsupported(RunnerUnavailable):
    """The model's runner cannot compute comparisons (OGC API – Processes runners)."""


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


def _require_runner(model: SimulationModel, seed: int | None = None):
    runner = model.runner
    if runner is None or not runner.is_active:
        raise RunnerUnavailable(f"Simulation model {model.key!r} has no active runner.")
    if seed is not None and runner.is_ogc and not model.accepts_seed:
        raise RunSubmissionError(f"Simulation model {model.key!r} does not accept a seed.")
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
    submitter_key: str = "",
) -> SimulationRun:
    """Create a queued run and its pending job; the worker sends it to the model's runner."""
    runner = _require_runner(model, seed)
    resolved = resolve_parameters(model, parameters)
    started = timezone.now()
    with transaction.atomic():
        run = SimulationRun.objects.create(
            model=model,
            key=f"run-{started:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}",
            # UTC, labelled: the server cannot know the visitor's timezone.
            title=title or f"{model.title} — {started:%Y-%m-%d %H:%M} UTC",
            model_version=model.runner_version,
            parameters=resolved,
            seed=seed,
            visibility=visibility,
            execution_state=ExecutionState.QUEUED,
            runner=runner,
            created_by=user,
            submitted_org=organization,
            submitter_key=submitter_key,
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


def submitter_key_for(client_ident: str) -> str:
    """Salted hash of an anonymous client's address; the address itself is never stored."""
    return hashlib.sha256(f"{settings.SECRET_KEY}:geosim-submitter:{client_ident}".encode()).hexdigest()


def check_quotas(user, organization, submitter_key: str = "", *, needed: int = 1) -> None:
    """Raise ``QuotaExceeded`` when the caller (user, organization, or anonymous address) started too many runs.

    Anonymous runs are limited per client address and, to protect the runner,
    all together. ``needed`` is how many new runs the request starts (a pair for
    a comparison), so a pair is never half-created.
    """
    quotas = settings.GEOSIMULATIONS_QUOTAS
    now = timezone.now()
    since = now - timedelta(hours=24)
    started = SimulationRun.objects.filter(job__isnull=False)
    if user is None:
        anonymous = started.filter(created_by__isnull=True)
        scopes = [
            ("Runs from your connection", "anonymous", anonymous.filter(submitter_key=submitter_key)),
            ("Anonymous visitors", "all_anonymous", anonymous),
        ]
    else:
        scopes = [("You", "user", started.filter(created_by=user))]
        if organization is not None:
            scopes.append(("Your organization", "organization", started.filter(submitted_org=organization)))
    for who, scope, runs in scopes:
        active_limit = quotas[f"{scope}_active"]
        if runs.filter(execution_state__in=SimulationRun.ACTIVE_STATES).count() + needed > active_limit:
            raise QuotaExceeded(
                f"{who}: {active_limit} run(s) already in progress; wait until one finishes.",
                retry_after=60,
            )
        daily_limit = quotas[f"{scope}_daily"]
        today = runs.filter(created_at__gte=since)
        if today.count() + needed > daily_limit:
            oldest = today.order_by("created_at").values_list("created_at", flat=True).first()
            raise QuotaExceeded(
                f"{who}: {daily_limit} runs started in the last 24 hours, the daily limit.",
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
    submitter_key: str = "",
) -> StartResult:
    """Start a run for a user (``None`` for anonymous), or return an identical readable run.

    ``params_hash`` reuse comes first; quotas apply only to new runs and not to
    platform-exempt users. Anonymous runs always get ``link`` visibility.
    """
    if user is None:
        visibility = SimulationRun.Visibility.LINK
    _require_runner(model, seed)
    resolved = resolve_parameters(model, parameters)
    if readable_runs is not None:
        existing = find_reusable_run(readable_runs, model, resolved, seed)
        if existing is not None:
            return StartResult(existing, reused=True)
    if not exempt:
        check_quotas(user, organization, submitter_key)
    run = submit_run(
        model,
        resolved,
        user=user,
        organization=organization,
        title=title,
        seed=seed,
        visibility=visibility,
        submitter_key=submitter_key if user is None else "",
    )
    return StartResult(run, reused=False)


@dataclass(frozen=True)
class ComparisonStart:
    comparison: SimulationComparison
    #: An identical readable comparison was returned instead of starting a new one.
    reused: bool


def start_comparison(
    model: SimulationModel,
    baseline_parameters: dict,
    candidate_parameters: dict,
    *,
    user,
    organization=None,
    readable_runs=None,
    readable_comparisons=None,
    seed: int | None = None,
    title: str | None = None,
    visibility: str = SimulationRun.Visibility.PRIVATE,
    exempt: bool = False,
    submitter_key: str = "",
) -> ComparisonStart:
    """Start two runs with the same seed and a comparison of them (or reuse what exists).

    Each run is reused when an identical readable one exists; when both are,
    an existing readable comparison of the pair is returned. The comparison's
    job waits until both runs succeeded, then the runner computes it.
    """
    runner = _require_runner(model, seed)
    if runner.is_ogc:
        raise ComparisonsUnsupported(f"Simulation model {model.key!r} does not support comparisons.")
    if user is None:
        visibility = SimulationRun.Visibility.LINK
    sides = {
        "baseline": resolve_parameters(model, baseline_parameters),
        "candidate": resolve_parameters(model, candidate_parameters),
    }
    if sides["baseline"] == sides["candidate"]:
        raise RunSubmissionError("The baseline and the candidate have the same parameters.")
    existing = {
        side: find_reusable_run(readable_runs, model, parameters, seed) if readable_runs is not None else None
        for side, parameters in sides.items()
    }
    if readable_comparisons is not None and all(existing.values()):
        found = (
            readable_comparisons.filter(
                baseline_run=existing["baseline"], candidate_run=existing["candidate"]
            )
            .exclude(execution_state=ExecutionState.FAILED)
            .order_by("-created_at")
            .first()
        )
        if found is not None:
            return ComparisonStart(found, reused=True)
    needed = sum(run is None for run in existing.values())
    if needed and not exempt:
        check_quotas(user, organization, submitter_key, needed=needed)
    started = timezone.now()
    label = title or f"{model.title} — comparison {started:%Y-%m-%d %H:%M} UTC"
    with transaction.atomic():
        runs = {
            side: existing[side]
            or submit_run(
                model,
                sides[side],
                user=user,
                organization=organization,
                title=f"{label} · {side}",
                seed=seed,
                visibility=visibility,
                submitter_key=submitter_key if user is None else "",
            )
            for side in ("baseline", "candidate")
        }
        comparison = SimulationComparison.objects.create(
            model=model,
            key=f"cmp-{started:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}",
            title=label,
            baseline_run=runs["baseline"],
            candidate_run=runs["candidate"],
            visibility=visibility,
            execution_state=ExecutionState.QUEUED,
            created_by=user,
            submitted_org=organization,
            submitter_key=submitter_key if user is None else "",
        )
        SimulationJob.objects.create(comparison=comparison, runner=runner, state=State.WAITING)
    return ComparisonStart(comparison, reused=False)


def sync_runner_models(runner) -> list[SimulationModel]:
    """Copy parameter schema, defaults and version from ``{base_url}/models`` to the runner's models.

    OGC runners: from ``/processes/{model key}`` instead (``ogc_runner.model_fields``).
    """
    if runner.is_ogc:
        return _sync_ogc_models(runner)
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


def _sync_ogc_models(runner) -> list[SimulationModel]:
    # Every process is checked before any model changes.
    found = []
    for model in SimulationModel.objects.filter(runner=runner):
        description = ogc_runner.describe_process(runner, model.key)
        if description is None:
            continue
        try:
            found.append((model, ogc_runner.model_fields(description)))
        except ogc_runner.RunnerError as exc:
            raise ogc_runner.RunnerError(f"Process {model.key!r}: {exc}") from None
    for model, fields in found:
        for name, value in fields.items():
            setattr(model, name, value)
        model.save()
    return [model for model, _ in found]


# -- worker queue ---------------------------------------------------------------


def claim_next_job(worker_id: str) -> SimulationJob | None:
    """Lock the next job the worker should act on, or ``None``.

    Pending jobs are dispatched, uploaded jobs imported, waiting comparison jobs
    checked, jobs at an OGC runner polled. A job another worker holds is
    skipped until its lock is older than ``lock_timeout_seconds``; then that
    worker is taken to be dead and the job (an import too) is claimed again.
    """
    now = timezone.now()
    stale_lock = now - timedelta(seconds=_worker_settings()["lock_timeout_seconds"])
    unlocked = Q(locked_at__isnull=True) | Q(locked_at__lt=stale_lock)
    # A subquery, not a join: a join would lock the runner row as well.
    ogc_runners = SimulationRunner.objects.filter(protocol=SimulationRunner.Protocol.OGC_API_PROCESSES)
    with transaction.atomic():
        job = (
            SimulationJob.objects.select_for_update(skip_locked=True)
            .filter(
                (Q(state__in=[State.WAITING, State.PENDING, State.UPLOADED], available_at__lte=now) & unlocked)
                | Q(state=State.IMPORTING, locked_at__lt=stale_lock)
                | (
                    Q(
                        state__in=SimulationJob.AT_RUNNER_STATES,
                        runner_id__in=ogc_runners.values("pk"),
                        available_at__lte=now,
                    )
                    & unlocked
                )
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
    """Act on a claimed job; never raises for dispatch, polling or import problems."""
    if job.state in (State.WAITING, State.PENDING):
        try:
            if job.state == State.WAITING:
                check_waiting(job)
            else:
                dispatch(job)
        except Exception as exc:  # one bad job must not stop the worker or a drain
            logger.exception("Dispatching simulation job %s failed unexpectedly", job.pk)
            _count_failed_dispatch(job, exc)
    elif job.state == State.IMPORTING:
        import_uploaded_package(job)
    elif job.state in SimulationJob.AT_RUNNER_STATES and job.runner.is_ogc:
        poll_job(job)


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


def _upload_target(upload_url: str, runner) -> dict:
    expires_at = timezone.now() + timedelta(minutes=runner.timeout_minutes)
    return {
        "url": upload_url,
        "method": "PUT",
        "headers": {"Content-Type": PACKAGE_CONTENT_TYPE},
        "expires_at": expires_at.isoformat(),
    }


def job_payload(job: SimulationJob, *, callback_token: str, upload_url: str) -> dict:
    """What a runner receives (``POST {base_url}/jobs``).

    Run jobs carry the run; comparison jobs (``"kind": "comparison"``) carry both
    runs, which the runner simulated before (or simulates again from the same
    parameters and seed).
    """
    owner, runner = job.owner, job.runner
    if job.comparison_id:
        subject = {
            "kind": "comparison",
            "comparison": {"key": owner.key, "title": owner.title},
            **{
                side: {"run_key": run.key, "parameters": run.parameters, "seed": run.seed}
                for side, run in (("baseline", owner.baseline_run), ("candidate", owner.candidate_run))
            },
        }
    else:
        subject = {
            "kind": "run",
            "run": {"key": owner.key, "title": owner.title},
            "parameters": owner.parameters,
            "seed": owner.seed,
        }
    return {
        "job_id": str(job.pk),
        "model": {"key": owner.model.key},
        **subject,
        "upload": _upload_target(upload_url, runner),
        "callback": {"url": callback_url(job), "token": callback_token},
    }


def dispatch(job: SimulationJob) -> None:
    """Send a pending job to its runner; retry later on failure, fail after the last attempt."""
    # The runner row stays locked from the count to the saved state change, so
    # workers dispatching to one runner take turns and cannot exceed
    # ``max_concurrent_jobs``. The lock is held across the POST (at most
    # ``dispatch_timeout_seconds``); NO KEY UPDATE still lets new jobs reference
    # the runner meanwhile.
    with transaction.atomic():
        runner = SimulationRunner.objects.select_for_update(no_key=True).get(pk=job.runner_id)
        job.runner = runner
        busy = SimulationJob.objects.filter(runner=runner, state__in=SimulationJob.AT_RUNNER_STATES).count()
        if busy >= runner.max_concurrent_jobs:
            _release(job, available_at=timezone.now() + BUSY_RUNNER_DELAY)
            return
        if not runner.is_active:
            _fail(job, f"Runner {runner.name!r} is not active.")
            return
        if runner.is_ogc:
            _dispatch_ogc(job, runner)
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


def _dispatch_ogc(job: SimulationJob, runner) -> None:
    """Execute the run's process at an OGC runner; the answer must be ``201`` with a job location."""
    if job.comparison_id:
        _fail(job, "OGC API – Processes runners do not compute comparisons.")
        return
    model = job.run.model
    try:
        upload = None
        if model.transfer_mode == "push":
            upload_url = storage.presigned_upload_url(
                UPLOAD_ALIAS,
                upload_path(job),
                content_type=PACKAGE_CONTENT_TYPE,
                expires_in=runner.timeout_minutes * 60,
            )
            upload = _upload_target(upload_url, runner)
        status_url, runner_job_id = ogc_runner.execute(job, ogc_runner.execute_request(job, upload=upload))
    except ogc_runner.RunnerError as exc:
        # The process may have run: sending it again could run it twice.
        job.attempts += 1
        _fail(job, f"Dispatch to {runner.name!r} failed: {exc}")
        return
    except (ogc_runner.TemporaryRunnerError, ImproperlyConfigured, storage.UploadUrlUnavailable) as exc:
        _retry_dispatch(job, exc)
        return
    now = timezone.now()
    job.state = State.DISPATCHED
    job.attempts += 1
    job.runner_job_id = runner_job_id
    job.status_url = status_url
    job.missed_polls = 0
    job.callback_token_hash = ""
    job.upload_path = upload_path(job)
    job.dispatched_at = now
    job.available_at = now + timedelta(seconds=POLL_MIN_SECONDS)
    job.error = ""
    job.locked_by, job.locked_at = "", None
    job.save()


def _retry_dispatch(job: SimulationJob, exc: Exception) -> None:
    job.attempts += 1
    message = f"Dispatch to {job.runner.name!r} failed: {exc}"
    logger.warning("Simulation job %s: %s", job.pk, _without_signatures(message))
    if job.attempts >= _worker_settings()["max_dispatch_attempts"]:
        _fail(job, message)
        return
    delay = RETRY_DELAYS[min(job.attempts - 1, len(RETRY_DELAYS) - 1)]
    job.error = public_error(message)
    _release(job, available_at=timezone.now() + delay)


def _count_failed_dispatch(job: SimulationJob, exc: Exception) -> None:
    """After an unexpected error (its transaction rolled back): one failed attempt, lock cleared.

    Goes through ``_retry_dispatch``, so the job fails for good after
    ``max_dispatch_attempts``. Only acts while the job is still waiting for
    dispatch and locked by this worker.
    """
    try:
        with transaction.atomic():
            current = (
                SimulationJob.objects.select_for_update(of=("self",))
                .select_related("run", "comparison", "runner")
                .filter(pk=job.pk, state__in=[State.WAITING, State.PENDING], locked_by=job.locked_by)
                .first()
            )
            if current is None:
                return
            _retry_dispatch(current, RuntimeError(f"Unexpected error ({exc.__class__.__name__})."))
    except Exception:
        logger.exception("Could not record the failed dispatch of simulation job %s", job.pk)


def _release(job: SimulationJob, *, available_at) -> None:
    job.available_at = available_at
    job.locked_by, job.locked_at = "", None
    job.save()


_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s'\"<>]+", re.IGNORECASE)
_REQUEST_DETAIL = re.compile(r"\b(host|url)(=|: )('[^']*'|\S+)", re.IGNORECASE)
_QUERY = re.compile(r"\?[^\s'\"<>]*=[^\s'\"<>]*")


def public_error(text: str) -> str:
    """``text`` without links, query strings or request hosts, as owners and link viewers see it.

    Runner messages and HTTP errors can carry signed download links and
    internal host names; the full text goes to the server log only.
    """
    text = _URL.sub("[link removed]", text)
    text = _REQUEST_DETAIL.sub(lambda match: f"{match[1]}{match[2]}[removed]", text)
    return _QUERY.sub("", text)


def _without_signatures(text: str) -> str:
    """For logs: links keep host and path, but no query string (signatures, tokens)."""
    return _QUERY.sub("?[query removed]", text)


def _fail(job: SimulationJob, error: str) -> None:
    shown = public_error(error)
    if shown != error:
        logger.warning("Simulation job %s failed: %s", job.pk, _without_signatures(error))
    error = shown
    now = timezone.now()
    job.state = State.FAILED
    job.error = error
    job.finished_at = now
    job.locked_by, job.locked_at = "", None
    job.save()
    owner = job.owner
    owner.execution_state = ExecutionState.FAILED
    owner.execution_error = error
    owner.save()


def check_waiting(job: SimulationJob) -> None:
    """A comparison job waits for both runs: dispatch when they succeeded, fail when one failed."""
    comparison = job.comparison
    runs = {"baseline": comparison.baseline_run, "candidate": comparison.candidate_run}
    for side, run in runs.items():
        run.refresh_from_db(fields=["execution_state", "execution_error"])
        if run.execution_state == ExecutionState.FAILED:
            _fail(job, f"The {side} run failed: {run.execution_error or 'no details'}")
            return
    complete = {ExecutionState.SUCCEEDED, ExecutionState.IMPORTED}
    if all(run.execution_state in complete for run in runs.values()):
        job.state = State.PENDING
        job.available_at = timezone.now()
        job.save()
        dispatch(job)
        return
    _release(job, available_at=timezone.now() + WAITING_POLL)


# -- polling (OGC runners) ----------------------------------------------------------


def _next_poll(job: SimulationJob):
    """Back-off: a fifth of the job's age at the runner, between 5 and 60 seconds."""
    now = timezone.now()
    age = (now - (job.dispatched_at or now)).total_seconds()
    return now + timedelta(seconds=min(POLL_MAX_SECONDS, max(POLL_MIN_SECONDS, age / 5)))


def poll_job(job: SimulationJob) -> None:
    """Ask an OGC runner for the job's status and move the job on; never raises."""
    try:
        _poll(job)
    except Exception:  # one bad job must not stop the worker
        logger.exception("Polling simulation job %s failed", job.pk)
        _finish_poll(job)


def _poll(job: SimulationJob) -> None:
    try:
        info = ogc_runner.job_status(job)
    except ogc_runner.JobUnknown:
        job.missed_polls += 1
        if job.missed_polls >= MAX_MISSED_POLLS:
            _finish_poll(job, fail="The runner no longer knows the job.")
        else:
            _finish_poll(job)
        return
    except (ogc_runner.TemporaryRunnerError, ogc_runner.RunnerError) as exc:
        logger.info("Simulation job %s: status poll failed, asking again later: %s", job.pk, exc)
        _finish_poll(job)
        return
    job.missed_polls = 0
    status = info.get("status")
    raw_message = info.get("message")
    message = public_error(raw_message)[:500] if isinstance(raw_message, str) else None
    if status == "accepted":
        _finish_poll(job, state=State.DISPATCHED, message=message)
    elif status == "running":
        _finish_poll(job, state=State.RUNNING, message=message)
    elif status == "successful":
        _collect_package(job, message)
    elif status == "failed":
        reported = raw_message.strip()[:5000] if isinstance(raw_message, str) else ""
        _finish_poll(job, fail=reported or "The runner reported a failure.", message=message)
    elif status == "dismissed":
        _finish_poll(job, fail="The job was dismissed at the runner.", message=message)
    else:
        logger.warning("Simulation job %s: ignoring unknown runner status %r", job.pk, str(status)[:50])
        _finish_poll(job, message=message)


def _collect_package(job: SimulationJob, message: str | None) -> None:
    """A successful job's package into TOSCA storage: push checks it arrived, pull downloads it."""
    if job.run.model.transfer_mode == "push":
        if storages[UPLOAD_ALIAS].exists(job.upload_path):
            _finish_poll(job, state=State.UPLOADED, message=message)
        else:
            _finish_poll(job, fail="The runner reported success but uploaded no package.", message=message)
        return
    name = upload_path(job)
    try:
        with tempfile.TemporaryDirectory(prefix="geosim-pull-") as staging:
            package = Path(staging) / "package.zip"
            ogc_runner.fetch_package(job, package)
            if storages[UPLOAD_ALIAS].exists(name):
                storage.delete(UPLOAD_ALIAS, name)
            name = storage.upload_file(UPLOAD_ALIAS, name, package, content_type=PACKAGE_CONTENT_TYPE)
    except ogc_runner.TemporaryRunnerError as exc:
        logger.info("Simulation job %s: package download failed, trying again later: %s", job.pk, exc)
        _finish_poll(job, message=message)
        return
    except ogc_runner.RunnerError as exc:
        _finish_poll(job, fail=f"Could not download the package: {exc}", message=message)
        return
    if not _finish_poll(job, state=State.UPLOADED, message=message, uploaded=name):
        _discard_package(job, name)


def _discard_package(job: SimulationJob, name: str) -> None:
    """Remove a package stored for a job that moved on meanwhile; best effort.

    Kept when the job is waiting for import of that very object (another
    worker that reclaimed the job stored it there too).
    """
    try:
        now = SimulationJob.objects.filter(pk=job.pk).values_list("state", "upload_path").first()
        if now is not None and now[0] in (State.UPLOADED, State.IMPORTING) and now[1] == name:
            return
        storage.delete(UPLOAD_ALIAS, name)
    except Exception:
        logger.exception("Could not remove the package of simulation job %s, which moved on meanwhile", job.pk)


def _finish_poll(
    job: SimulationJob, *, state=None, fail: str = "", message: str | None = None, uploaded=None
) -> bool:
    """Apply a poll's outcome unless the job moved on meanwhile (timeout, another worker); clear the lock.

    Returns whether the outcome was applied.
    """
    with transaction.atomic():
        current = (
            SimulationJob.objects.select_for_update(of=("self",))
            .select_related("run", "comparison")
            .filter(pk=job.pk, state__in=SimulationJob.AT_RUNNER_STATES, locked_by=job.locked_by)
            .first()
        )
        if current is None:
            return False
        current.missed_polls = job.missed_polls
        if message is not None:
            current.message = message
        if fail:
            _fail(current, fail)
            return True
        now = timezone.now()
        owner = current.owner
        if state == State.RUNNING and current.state != State.RUNNING:
            current.started_at = current.started_at or now
            owner.execution_state = ExecutionState.RUNNING
            owner.save()
        if state == State.UPLOADED:
            current.upload_path = uploaded or current.upload_path
            current.available_at = now
            owner.execution_state = ExecutionState.RUNNING
            owner.save()
        else:
            current.available_at = _next_poll(current)
        if state is not None:
            current.state = state
        current.locked_by, current.locked_at = "", None
        current.save()
    return True


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
        job = (
            # Lock the job row only: run and comparison are outer joins now.
            SimulationJob.objects.select_for_update(of=("self",))
            .select_related("run", "comparison", "runner")
            .filter(pk=job_id)
            .first()
        )
        # OGC runners never call this endpoint; their jobs move by polling only.
        if job is None or job.runner.is_ogc:
            raise CallbackRejected
        check_callback_token(job, token)
        now = timezone.now()
        job.last_callback_at = now
        job.message = str(report.get("message") or "")[:500]
        if job.state not in SimulationJob.AT_RUNNER_STATES:
            # Late or repeated reports do not move a job that already went on.
            job.save(update_fields=["last_callback_at", "message", "updated_at"])
            return job
        owner = job.owner
        if state == "running":
            job.state = State.RUNNING
            job.started_at = job.started_at or now
            owner.execution_state = ExecutionState.RUNNING
            owner.save()
        elif state == "succeeded":
            if not storages[UPLOAD_ALIAS].exists(job.upload_path):
                job.save()
                _fail(job, "The runner reported success but uploaded no package.")
                return job
            job.state = State.UPLOADED
            job.available_at = now
            owner.execution_state = ExecutionState.RUNNING
            owner.save()
        else:
            job.save()
            _fail(job, str(report.get("error") or "The runner reported a failure.")[:5000])
            return job
        job.save()
    return job


# -- import -----------------------------------------------------------------------


def import_uploaded_package(job: SimulationJob) -> None:
    """Import the runner's package into the job's run or comparison, then remove the upload."""
    owner = job.owner
    target = {"into_comparison": owner} if job.comparison_id else {"into_run": owner}
    try:
        with tempfile.TemporaryDirectory(prefix="geosim-job-") as staging:
            package = Path(staging) / "package.zip"
            storage.download_to(UPLOAD_ALIAS, job.upload_path, package)
            result = import_package(
                package,
                organization=owner.model.organization,
                user=owner.created_by,
                **target,
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
    """Start a failed job again (a run is simulated again; a comparison waits for its runs again)."""
    if job.state != State.FAILED:
        raise RunSubmissionError("Only failed jobs can be retried.")
    owner = job.owner
    if owner.outputs.exists():
        raise RunSubmissionError("It already has outputs; start a new one instead.")
    job.state = State.WAITING if job.comparison_id else State.PENDING
    job.attempts = 0
    job.error = ""
    job.message = ""
    job.callback_token_hash = ""
    job.status_url, job.missed_polls = "", 0
    job.available_at = timezone.now()
    job.dispatched_at = job.started_at = job.finished_at = None
    job.save()
    owner.execution_state = ExecutionState.QUEUED
    owner.execution_error = ""
    owner.save()
