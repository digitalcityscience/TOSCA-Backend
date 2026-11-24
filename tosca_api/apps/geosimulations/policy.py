"""Who may start simulation runs, and how (shared by every entry point).

The REST views and any other facade call ``start_run`` / ``start_comparison``
here, never ``jobs`` directly, so the rules are applied once and identically.
Failures are typed exceptions; mapping them to responses is the caller's job.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q
from rest_framework.throttling import BaseThrottle

from tosca_api.apps.authentication.role_sync import org_role_level
from tosca_api.apps.organizations.models import Organization
from tosca_api.apps.organizations.permissions import get_request_org_context

from . import jobs
from .models import SimulationModel, SimulationRun


class RunsDisabled(Exception):
    """Starting runs is switched off (``GEOSIMULATIONS_RUNS_ENABLED``)."""


class RunAccessDenied(Exception):
    """The model's ``run_access`` does not allow this caller."""


class VisibilityNotAllowed(Exception):
    """The caller may not share runs with the model's organization."""


@dataclass(frozen=True)
class Caller:
    """Who is asking: ``user`` is ``None`` when anonymous."""

    user: object | None
    org_slug: str | None
    org_level: str | None
    exempt: bool
    client_ident: str = ""

    @classmethod
    def from_request(cls, request) -> Caller:
        ident = BaseThrottle().get_ident(request)
        user = request.user
        if not (user and user.is_authenticated):
            return cls(user=None, org_slug=None, org_level=None, exempt=False, client_ident=ident)
        roles, org_slug, exempt = get_request_org_context(request)
        level = org_role_level(roles, org_slug) if org_slug else None
        return cls(user=user, org_slug=org_slug, org_level=level, exempt=exempt, client_ident=ident)

    @property
    def anonymous(self) -> bool:
        return self.user is None

    @property
    def reader_scope(self) -> dict:
        """Keyword arguments for the ``readable_by`` managers."""
        return {"org_slug": self.org_slug, "exempt": self.exempt, "user": self.user}


def runs_enabled() -> bool:
    return settings.GEOSIMULATIONS_RUNS_ENABLED


def may_start_runs(caller: Caller, model: SimulationModel) -> bool:
    """The model's ``run_access`` applied to the caller."""
    return model.can_start_runs(
        user=caller.user, org_level=caller.org_level, org_slug=caller.org_slug, exempt=caller.exempt
    )


def readable_runs(caller: Caller, *, by_id: bool = False):
    """Runs the caller may read; ``by_id`` adds anonymous ``link`` runs, which anyone with the id may open."""
    queryset = SimulationRun.objects.readable_by(**caller.reader_scope)
    return link_readable(queryset) if by_id else queryset


def link_readable(queryset):
    """``queryset`` plus anonymous ``link`` runs."""
    return SimulationRun.objects.filter(
        Q(pk__in=queryset.values("pk")) | Q(visibility=SimulationRun.Visibility.LINK)
    )


def _authorize(caller: Caller, model: SimulationModel, visibility: str) -> dict:
    """Check the start rules; the caller details ``jobs`` needs."""
    if not runs_enabled():
        raise RunsDisabled
    if not may_start_runs(caller, model):
        raise RunAccessDenied
    if (
        visibility == SimulationRun.Visibility.ORGANIZATION
        and not caller.exempt
        and caller.org_slug != model.organization.slug
    ):
        raise VisibilityNotAllowed
    return {
        "user": caller.user,
        "organization": (
            Organization.objects.filter(slug=caller.org_slug).first() if caller.org_slug else None
        ),
        "readable_runs": readable_runs(caller, by_id=True),
        "exempt": caller.exempt,
        "submitter_key": jobs.submitter_key_for(caller.client_ident) if caller.anonymous else "",
    }


def start_run(
    caller: Caller, model: SimulationModel, *, parameters: dict, seed, title, visibility: str
) -> jobs.StartResult:
    """Start a run for the caller, or return an identical readable one.

    Raises ``RunsDisabled``, ``RunAccessDenied``, ``VisibilityNotAllowed`` and
    what ``jobs.start_run`` raises (``RunSubmissionError``, ``QuotaExceeded``, ``RunnerUnavailable``).
    """
    context = _authorize(caller, model, visibility)
    return jobs.start_run(
        model, parameters, seed=seed, title=title or None, visibility=visibility, **context
    )


def start_comparison(
    caller: Caller,
    model: SimulationModel,
    *,
    baseline: dict,
    candidate: dict,
    seed,
    title,
    visibility: str,
    readable_comparisons,
) -> jobs.ComparisonStart:
    """Start two runs and a comparison of them; the same rules and errors as ``start_run``."""
    context = _authorize(caller, model, visibility)
    return jobs.start_comparison(
        model,
        baseline,
        candidate,
        readable_comparisons=readable_comparisons,
        seed=seed,
        title=title or None,
        visibility=visibility,
        **context,
    )
