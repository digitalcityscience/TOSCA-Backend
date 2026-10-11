"""Public read API for GeoSimulations (Task 12.5).

Reads are open. What a caller sees follows the 12.1 visibility rules:
anonymous readers get published models and runs (a run also needs a published
model, a comparison its model and both runs published); members additionally
see their own organization's drafts and archives;
platform-exempt callers see everything. Anything else is a 404, never a 403.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from tosca_api.apps.organizations.permissions import get_request_org_context

from .jobs import CallbackRejected, handle_callback
from .models import SimulationComparison, SimulationModel, SimulationOutput, SimulationRun
from .serializers import (
    SimulationComparisonDetailSerializer,
    SimulationComparisonSummarySerializer,
    SimulationModelDetailSerializer,
    SimulationModelListSerializer,
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


def _reader_scope(request) -> dict:
    user = request.user
    if not (user and user.is_authenticated):
        return {"org_slug": None, "exempt": False}
    _roles, org_slug, exempt = get_request_org_context(request)
    return {"org_slug": org_slug, "exempt": exempt}


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


def _readable_comparisons(scope: dict):
    """Comparisons the caller may read whose two runs the caller may read too."""
    readable_runs = SimulationRun.objects.readable_by(**scope)
    return SimulationComparison.objects.readable_by(**scope).filter(
        baseline_run__in=readable_runs, candidate_run__in=readable_runs
    )


@extend_schema_view(
    list=extend_schema(
        summary="List simulation runs the caller may read",
        tags=["GeoSimulations"],
        parameters=[OpenApiParameter("model", str, description="Only runs of this model id")],
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
        queryset = SimulationRun.objects.readable_by(**_reader_scope(self.request)).select_related(
            "model"
        )
        model_id = self.request.query_params.get("model")
        if model_id:
            queryset = queryset.filter(model_id=model_id)
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                Prefetch("outputs", queryset=SimulationOutput.objects.select_related("asset"))
            )
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SimulationRunDetailSerializer
        return SimulationRunSummarySerializer

    def get_serializer_context(self):
        # One timestamp per response, so every URL reports the same expiry.
        return {**super().get_serializer_context(), "now": timezone.now()}


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
        queryset = _readable_comparisons(_reader_scope(self.request)).select_related(
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
        return {**super().get_serializer_context(), "now": timezone.now()}


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
