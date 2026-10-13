"""Retention for runs users start (``purge_simulation_runs``, also hourly in the worker).

Curated runs and comparisons (imported, or published by staff) are never purged. Started runs
that stayed private (or anonymous ``link`` runs) and unpublished, and started runs that failed, are deleted
with their files after ``GEOSIMULATIONS_RETENTION`` days; a package a failed job
left in the upload area is removed after ``upload_days``. Runs that a GeoStory
scene or a comparison uses are kept (they are ``PROTECT``ed) and reported.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.utils import timezone

from . import storage
from .models import SimulationComparison, SimulationJob, SimulationRun, Status

logger = logging.getLogger(__name__)

UPLOAD_ALIAS = "default"


@dataclass
class PurgeResult:
    deleted_comparisons: list[str] = field(default_factory=list)
    deleted_runs: list[str] = field(default_factory=list)
    kept_runs: list[str] = field(default_factory=list)
    removed_uploads: int = 0


def _expired(queryset, now):
    retention = settings.GEOSIMULATIONS_RETENTION
    private_cutoff = now - timedelta(days=retention["private_runs_days"])
    failed_cutoff = now - timedelta(days=retention["failed_runs_days"])
    return (
        queryset.filter(job__isnull=False)
        .exclude(status=Status.PUBLISHED)
        .filter(
            Q(
                visibility__in=[SimulationRun.Visibility.PRIVATE, SimulationRun.Visibility.LINK],
                created_at__lt=private_cutoff,
            )
            | Q(execution_state=SimulationRun.ExecutionState.FAILED, created_at__lt=failed_cutoff)
        )
        .select_related("job")
    )


def expired_runs(now=None):
    """Started, unpublished runs past their retention period."""
    return _expired(SimulationRun.objects.all(), now or timezone.now())


def expired_comparisons(now=None):
    """Comparisons users started, unpublished, past their retention period."""
    return _expired(SimulationComparison.objects.all(), now or timezone.now())


def _remove_upload(job: SimulationJob) -> bool:
    if not job.upload_path:
        return False
    try:
        if storages[UPLOAD_ALIAS].exists(job.upload_path):
            storage.delete(UPLOAD_ALIAS, job.upload_path)
            return True
    except Exception:
        logger.exception("Could not remove the upload of simulation job %s", job.pk)
    return False


def purge(*, now=None, dry_run: bool = False) -> PurgeResult:
    now = now or timezone.now()
    result = PurgeResult()
    # Comparisons first: they protect their runs.
    for comparison in expired_comparisons(now):
        if dry_run:
            result.deleted_comparisons.append(comparison.key)
            continue
        upload = comparison.job.upload_path
        try:
            with transaction.atomic():
                comparison.delete()
        except ProtectedError:
            continue
        if upload:
            result.removed_uploads += _remove_upload(SimulationJob(upload_path=upload))
        result.deleted_comparisons.append(comparison.key)
    for run in expired_runs(now):
        if dry_run:
            result.deleted_runs.append(run.key)
            continue
        try:
            with transaction.atomic():
                upload = run.job.upload_path
                run.delete()  # files are deleted after commit (signals.delete_stored_object)
        except ProtectedError:
            result.kept_runs.append(run.key)
            continue
        if upload:
            result.removed_uploads += _remove_upload(SimulationJob(upload_path=upload))
        result.deleted_runs.append(run.key)

    upload_cutoff = now - timedelta(days=settings.GEOSIMULATIONS_RETENTION["upload_days"])
    leftovers = SimulationJob.objects.filter(
        state=SimulationJob.State.FAILED, finished_at__lt=upload_cutoff
    ).exclude(upload_path="")
    for job in leftovers:
        if dry_run:
            continue
        result.removed_uploads += _remove_upload(job)
        SimulationJob.objects.filter(pk=job.pk).update(upload_path="")
    return result
