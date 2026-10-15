"""Public read API for GeoSimulations (Task 12.5).

Reads are open. What a caller sees follows the 12.1 visibility rules:
anonymous readers get published models and runs (a run also needs a published
model, a comparison its model and both runs published); members additionally
see their own organization's drafts and archives;
platform-exempt callers see everything. Anything else is a 404, never a 403.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import action
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.throttling import BaseThrottle
from rest_framework.response import Response
from rest_framework.views import APIView

from tosca_api.apps.authentication.backends import KeycloakTokenAuthentication
from tosca_api.apps.organizations.models import Organization
from tosca_api.apps.organizations.permissions import get_request_org_context

from .jobs import (
    CallbackRejected,
    QuotaExceeded,
    RunnerUnavailable,
    RunSubmissionError,
    handle_callback,
    start_comparison,
    start_run,
    submitter_key_for,
)
from .models import SimulationComparison, SimulationModel, SimulationOutput, SimulationRun
from .serializers import (
    ComparisonSubmissionSerializer,
    SimulationComparisonDetailSerializer,
    SimulationComparisonSummarySerializer,
    SimulationModelDetailSerializer,
    SimulationModelListSerializer,
    RunSubmissionSerializer,
    SimulationRunDetailSerializer,
    SimulationRunSummarySerializer,
)

UUID_PATTERN = "[0-9a-f-]{36}"


class TitleCursorPagination(CursorPagination):
    page_size = 20
    ordering = ("title", "id")


class NewestCursorPagination(CursorPagination):
    page_size = 20
    ordering = ("-created_at", "id")


class CanStartRuns(BasePermission):
    """Signed-in users, and anonymous visitors while ``GEOSIMULATIONS_ALLOW_ANONYMOUS_RUNS`` is on."""

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated) or settings.GEOSIMULATIONS_ALLOW_ANONYMOUS_RUNS


def _runs_disabled() -> Response | None:
    """``404`` while starting runs is switched off (``GEOSIMULATIONS_RUNS_ENABLED``)."""
    if settings.GEOSIMULATIONS_RUNS_ENABLED:
        return None
    return Response({"detail": "Starting simulation runs is not enabled."}, status=status.HTTP_404_NOT_FOUND)


def _link_readable(queryset):
    """Readable runs plus anonymous ``link`` runs, which anyone with the id may open."""
    return SimulationRun.objects.filter(
        Q(pk__in=queryset.values("pk")) | Q(visibility=SimulationRun.Visibility.LINK)
    )


def _reader_scope(request) -> dict:
    user = request.user
    if not (user and user.is_authenticated):
        return {"org_slug": None, "exempt": False, "user": None}
    _roles, org_slug, exempt = get_request_org_context(request)
    return {"org_slug": org_slug, "exempt": exempt, "user": user}


@extend_schema_view(
    list=extend_schema(summary="List simulation models", tags=["GeoSimulations"]),
    retrieve=extend_schema(
        summary="Simulation model with the runs the caller may read", tags=["GeoSimulations"]
    ),
)
class SimulationModelViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    pagination_class = TitleCursorPagination
    lookup_value_regex = UUID_PATTERN

    def get_queryset(self):
        scope = _reader_scope(self.request)
        readable_runs = SimulationRun.objects.readable_by(**scope)
        return (
            SimulationModel.objects.readable_by(**scope)
            .select_related("organization", "preview_image")
            .annotate(run_count=Count("runs", filter=Q(runs__in=readable_runs), distinct=True))
        )

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SimulationModelDetailSerializer
        return SimulationModelListSerializer

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action == "retrieve" and "pk" in self.kwargs:
            scope = _reader_scope(self.request)
            context["readable_runs"] = (
                SimulationRun.objects.readable_by(**scope)
                .filter(model_id=self.kwargs["pk"])
                .order_by("-created_at")
            )
            context["readable_comparisons"] = (
                _readable_comparisons(scope)
                .filter(model_id=self.kwargs["pk"])
                .select_related("baseline_run", "candidate_run")
                .order_by("-created_at")
            )
        return context

    @extend_schema(
        summary="Start a simulation run with parameters (or get an identical existing run)",
        tags=["GeoSimulations"],
        request=RunSubmissionSerializer,
        responses={200: SimulationRunSummarySerializer, 202: SimulationRunSummarySerializer},
    )
    # Token authentication only: a Django admin session cookie (shared across
    # localhost ports in dev) must not turn the frontend's request into a
    # session-authenticated one that needs a CSRF token.
    @action(
        detail=True,
        methods=["post"],
        url_path="runs",
        permission_classes=[CanStartRuns],
        authentication_classes=[KeycloakTokenAuthentication, TokenAuthentication],
    )
    def start_run(self, request, pk=None):
        """``202`` for a new queued run, ``200`` with ``reused: true`` for an identical readable run.

        ``400`` invalid parameters (``{"parameters": [...]}``), ``409`` the model
        has no runner, ``429`` a quota is reached (``Retry-After`` header).
        Anonymous visitors (while allowed) get ``link`` runs: readable by id, never listed.
        """
        if (disabled := _runs_disabled()) is not None:
            return disabled
        model = self.get_object()
        submission = RunSubmissionSerializer(data=request.data)
        submission.is_valid(raise_exception=True)
        data = submission.validated_data
        context = _submission_context(request, model, data["visibility"])
        if isinstance(context, Response):
            return context
        try:
            result = start_run(
                model,
                data["parameters"],
                seed=data["seed"],
                title=data["title"] or None,
                visibility=data["visibility"],
                **context,
            )
        except RunSubmissionError as exc:
            return _submission_error(exc)
        body = SimulationRunSummarySerializer(result.run, context=self.get_serializer_context()).data
        body["reused"] = result.reused
        return Response(body, status=status.HTTP_200_OK if result.reused else status.HTTP_202_ACCEPTED)

    @extend_schema(
        summary="Start two runs with the same seed and a comparison of them (or get an identical one)",
        tags=["GeoSimulations"],
        request=ComparisonSubmissionSerializer,
        responses={200: SimulationComparisonSummarySerializer, 202: SimulationComparisonSummarySerializer},
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="comparisons",
        permission_classes=[CanStartRuns],
        authentication_classes=[KeycloakTokenAuthentication, TokenAuthentication],
    )
    def start_comparison(self, request, pk=None):
        """Body ``{baseline: {parameters}, candidate: {parameters}, seed, title, visibility}``.

        ``202`` new comparison (its runs start now, the comparison follows),
        ``200`` with ``reused: true`` for an identical readable comparison; errors as for runs.
        """
        if (disabled := _runs_disabled()) is not None:
            return disabled
        model = self.get_object()
        submission = ComparisonSubmissionSerializer(data=request.data)
        submission.is_valid(raise_exception=True)
        data = submission.validated_data
        context = _submission_context(request, model, data["visibility"])
        if isinstance(context, Response):
            return context
        scope = _reader_scope(request)
        try:
            result = start_comparison(
                model,
                data["baseline"],
                data["candidate"],
                readable_comparisons=_readable_comparisons(scope, by_id=True),
                seed=data["seed"],
                title=data["title"] or None,
                visibility=data["visibility"],
                **context,
            )
        except RunSubmissionError as exc:
            return _submission_error(exc)
        body = SimulationComparisonSummarySerializer(result.comparison, context=self.get_serializer_context()).data
        body["reused"] = result.reused
        return Response(body, status=status.HTTP_200_OK if result.reused else status.HTTP_202_ACCEPTED)


def _submission_context(request, model, visibility: str) -> dict | Response:
    """Caller details for ``start_run`` / ``start_comparison``, or a 400 response."""
    scope = _reader_scope(request)
    org_slug = scope["org_slug"]
    if (
        visibility == SimulationRun.Visibility.ORGANIZATION
        and not scope["exempt"]
        and org_slug != model.organization.slug
    ):
        return Response(
            {"visibility": ["Only members of the model's organization can share runs with it."]},
            status=status.HTTP_400_BAD_REQUEST,
        )
    anonymous = scope["user"] is None
    return {
        "user": scope["user"],
        "organization": Organization.objects.filter(slug=org_slug).first() if org_slug else None,
        "readable_runs": _link_readable(SimulationRun.objects.readable_by(**scope)),
        "exempt": scope["exempt"],
        "submitter_key": submitter_key_for(BaseThrottle().get_ident(request)) if anonymous else "",
    }


def _submission_error(exc: RunSubmissionError) -> Response:
    if isinstance(exc, QuotaExceeded):
        return Response(
            {"detail": str(exc)},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(exc.retry_after)},
        )
    if isinstance(exc, RunnerUnavailable):
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    return Response({"parameters": exc.errors}, status=status.HTTP_400_BAD_REQUEST)


def _readable_comparisons(scope: dict, *, by_id: bool = False):
    """Comparisons the caller may read whose two runs the caller may read too.

    ``by_id`` (detail views) adds anonymous ``link`` comparisons and runs.
    """
    readable_runs = SimulationRun.objects.readable_by(**scope)
    comparisons = SimulationComparison.objects.readable_by(**scope)
    if by_id:
        readable_runs = _link_readable(readable_runs)
        comparisons = SimulationComparison.objects.filter(
            Q(pk__in=comparisons.values("pk")) | Q(visibility=SimulationRun.Visibility.LINK)
        )
    return comparisons.filter(baseline_run__in=readable_runs, candidate_run__in=readable_runs)


@extend_schema_view(
    list=extend_schema(
        summary="List simulation runs the caller may read",
        tags=["GeoSimulations"],
        parameters=[
            OpenApiParameter("model", str, description="Only runs of this model id"),
            OpenApiParameter("mine", bool, description="Only runs the caller started"),
        ],
    ),
    retrieve=extend_schema(
        summary="Simulation run with outputs, file URLs, styles and legends",
        tags=["GeoSimulations"],
    ),
)
class SimulationRunViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    pagination_class = NewestCursorPagination
    lookup_value_regex = UUID_PATTERN

    def get_queryset(self):
        readable = SimulationRun.objects.readable_by(**_reader_scope(self.request))
        if self.action == "retrieve":
            # Anonymous runs are opened by id; lists never show them.
            readable = _link_readable(readable)
        queryset = readable.select_related("model")
        model_id = self.request.query_params.get("model")
        if model_id:
            queryset = queryset.filter(model_id=model_id)
        if self.request.query_params.get("mine") in ("1", "true"):
            user = self.request.user
            queryset = queryset.filter(created_by=user) if user.is_authenticated else queryset.none()
        if self.action == "retrieve":
            queryset = queryset.select_related("job").prefetch_related(
                Prefetch("outputs", queryset=SimulationOutput.objects.select_related("asset"))
            )
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SimulationRunDetailSerializer
        return SimulationRunSummarySerializer

    def get_serializer_context(self):
        # One timestamp per response, so every URL reports the same expiry.
        return {
            **super().get_serializer_context(),
            "now": timezone.now(),
            "exempt": _reader_scope(self.request)["exempt"],
        }


@extend_schema_view(
    list=extend_schema(
        summary="List simulation comparisons the caller may read",
        tags=["GeoSimulations"],
        parameters=[
            OpenApiParameter("model", str, description="Only comparisons of this model id"),
            OpenApiParameter("run", str, description="Only comparisons with this run on either side"),
        ],
    ),
    retrieve=extend_schema(
        summary="Simulation comparison with both runs, KPI changes and outputs",
        tags=["GeoSimulations"],
    ),
)
class SimulationComparisonViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    pagination_class = NewestCursorPagination
    lookup_value_regex = UUID_PATTERN

    def get_queryset(self):
        queryset = _readable_comparisons(
            _reader_scope(self.request), by_id=self.action == "retrieve"
        ).select_related(
            "model", "baseline_run", "candidate_run"
        )
        model_id = self.request.query_params.get("model")
        if model_id:
            queryset = queryset.filter(model_id=model_id)
        run_id = self.request.query_params.get("run")
        if run_id:
            queryset = queryset.filter(Q(baseline_run_id=run_id) | Q(candidate_run_id=run_id))
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                Prefetch("outputs", queryset=SimulationOutput.objects.select_related("asset"))
            )
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SimulationComparisonDetailSerializer
        return SimulationComparisonSummarySerializer

    def get_serializer_context(self):
        return {
            **super().get_serializer_context(),
            "now": timezone.now(),
            "exempt": _reader_scope(self.request)["exempt"],
        }


class SimulationJobCallbackView(APIView):
    """Status reports from a simulation runner (Phase 4 runner contract).

    Authenticated by the job's own callback token (``Authorization: Bearer``),
    which TOSCA sent with the job; unknown jobs and wrong tokens both answer 404.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)
    def post(self, request, job_id):
        header = request.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        report = request.data if isinstance(request.data, dict) else {}
        try:
            job = handle_callback(job_id, token, report)
        except CallbackRejected:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        except DjangoValidationError as exc:
            return Response(exc.message_dict, status=status.HTTP_400_BAD_REQUEST)
        return Response({"job_id": str(job.pk), "state": job.state}, status=status.HTTP_202_ACCEPTED)
