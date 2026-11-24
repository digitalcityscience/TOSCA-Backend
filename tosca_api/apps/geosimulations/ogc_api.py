"""OGC API – Processes (Part 1: Core 1.0) facade for external clients.

A thin layer over ``policy`` and the run models: the same rules as the REST
API, with three differences. Only Keycloak Bearer tokens authenticate and the
token must carry ``GEOSIMULATIONS_OGC_API_ROLE``; callers are never
platform-exempt, so quotas and visibility always apply; results are expiring
links only. Everything answers 404 while ``GEOSIMULATIONS_OGC_API_ENABLED`` is off.

Process id = ``SimulationModel.key`` (unique, URL-safe and stable across
environments; the REST API uses the UUID). Job id = ``SimulationRun.id``.

Result style: ``GET jobs/{id}/results`` answers ``raw`` (204 + ``Link`` headers, the
spec default) unless ``?response=document``. The style asked for in the execute
request is not stored per job; it only shapes the results link of the execute
response.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import replace
from http import HTTPStatus
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ParseError, PermissionDenied, Throttled
from rest_framework.parsers import JSONParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from tosca_api.apps.authentication.backends import KeycloakTokenAuthentication
from tosca_api.apps.organizations.permissions import get_request_org_context

from . import ogc_mapping as mapping
from . import policy
from .delivery import file_url
from .jobs import QuotaExceeded, RunnerUnavailable, RunSubmissionError
from .models import SimulationModel, SimulationOutput, SimulationRun, Status

TAG = "OGC API - Processes"
DEFAULT_LIMIT, MAX_LIMIT = 10, 100


class OgcError(APIException):
    """An error the facade raises on purpose; ``detail`` is written for clients."""

    def __init__(self, status_code: int, title: str, detail: str | None = None, *, type: str | None = None,
                 headers: dict | None = None, **extra):
        super().__init__(detail or title)
        self.status_code = status_code
        self.title, self.problem_type, self.headers, self.extra = title, type, headers or {}, extra


def ogc_exception_handler(exc, context):
    """Every error as the OGC exception JSON; unexpected exceptions stay with Django's 500 (no text echoed)."""
    if isinstance(exc, Http404):
        exc = NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = PermissionDenied()
    if not isinstance(exc, APIException):
        return None
    headers = {}
    if isinstance(exc, OgcError):
        title, detail, problem_type, extra = exc.title, str(exc.detail), exc.problem_type, exc.extra
        headers.update(exc.headers)
    else:
        title, detail, problem_type, extra = HTTPStatus(exc.status_code).phrase, str(exc.detail), None, {}
        if isinstance(exc, ParseError):
            detail = "The request body is not valid JSON."
        if isinstance(exc, Throttled) and exc.wait is not None:
            headers["Retry-After"] = str(math.ceil(exc.wait))
        if auth_header := getattr(exc, "auth_header", None):
            headers["WWW-Authenticate"] = auth_header
    body = mapping.exception_body(exc.status_code, title=str(title), detail=detail, type=problem_type, **extra)
    return Response(body, status=exc.status_code, headers=headers)


class HasOgcRole(BasePermission):
    """The token carries ``GEOSIMULATIONS_OGC_API_ROLE`` (a platform-exempt role does not replace it)."""

    message = "This API requires the simulation API role."

    def has_permission(self, request, view) -> bool:
        roles, _org_slug, _exempt = get_request_org_context(request)
        return settings.GEOSIMULATIONS_OGC_API_ROLE in roles


class OgcExecuteThrottle(UserRateThrottle):
    scope = "ogc_execute"


class OgcStatusThrottle(UserRateThrottle):
    scope = "ogc_status"


class OgcApiView(APIView):
    authentication_classes = [KeycloakTokenAuthentication]
    permission_classes = [IsAuthenticated, HasOgcRole]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]
    throttle_classes = [OgcStatusThrottle]

    def initial(self, request, *args, **kwargs):
        if not settings.GEOSIMULATIONS_OGC_API_ENABLED:
            raise Http404
        super().initial(request, *args, **kwargs)

    def get_exception_handler(self):
        return ogc_exception_handler

    def url(self, name: str, **kwargs) -> str:
        return self.request.build_absolute_uri(reverse(name, kwargs=kwargs))

    @property
    def caller(self) -> policy.Caller:
        """The caller with the platform exemption removed: quotas and visibility always apply."""
        return replace(policy.Caller.from_request(self.request), exempt=False)

    def page_links(self, offset: int, limit: int, *, has_next: bool) -> list[dict]:
        """``self`` and, when more follow, ``next`` for the current query string."""
        params = self.request.query_params.copy()
        links = [mapping.link(self.request.build_absolute_uri(), "self")]
        if has_next:
            params["limit"], params["offset"] = limit, offset + limit
            href = self.request.build_absolute_uri(self.request.path) + "?" + urlencode(params)
            links.append(mapping.link(href, "next"))
        return links

    def paging(self) -> tuple[int, int]:
        try:
            limit = int(self.request.query_params.get("limit", DEFAULT_LIMIT))
            offset = int(self.request.query_params.get("offset", 0))
        except ValueError:
            limit = offset = -1
        if not (1 <= limit <= MAX_LIMIT and offset >= 0):
            raise OgcError(400, "Invalid paging parameters", f"'limit' must be 1 to {MAX_LIMIT}, 'offset' 0 or more.")
        return limit, offset

    # Processes

    def runnable_models(self):
        """Published models the caller sees and may run, by key (none while runs are off)."""
        if not policy.runs_enabled():
            return []
        caller = self.caller
        models = (
            SimulationModel.objects.readable_by(**caller.reader_scope)
            .filter(status=Status.PUBLISHED, runner__is_active=True)
            .select_related("organization", "runner")
            .order_by("key")
        )
        return [model for model in models if policy.may_start_runs(caller, model)]

    def is_runnable(self, model: SimulationModel) -> bool:
        runner = model.runner
        return (
            policy.runs_enabled()
            and runner is not None
            and runner.is_active
            and policy.may_start_runs(self.caller, model)
        )

    def visible_model(self, process_id: str) -> SimulationModel:
        model = (
            SimulationModel.objects.readable_by(**self.caller.reader_scope)
            .filter(key=process_id, status=Status.PUBLISHED)
            .select_related("organization", "runner")
            .first()
        )
        if model is None:
            raise no_such_process()
        return model

    # Jobs

    def readable_run(self, job_id: str) -> SimulationRun:
        run = (
            policy.readable_runs(self.caller, by_id=True)  # a reused anonymous link run is readable by id
            .filter(pk=job_id)
            .select_related("model", "job")
            .first()
            if _is_uuid(job_id)
            else None
        )
        if run is None:
            raise no_such_job(job_id)
        return run

    def job_status(self, run: SimulationRun) -> dict:
        return mapping.status_info(
            run,
            job_href=self.url("ogcapi-job", job_id=run.pk),
            results_href=self.url("ogcapi-job-results", job_id=run.pk),
        )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def no_such_process() -> OgcError:
    return OgcError(404, "No such process", "The process does not exist.", type=mapping.NO_SUCH_PROCESS)


def no_such_job(job_id: str) -> OgcError:
    return OgcError(404, "No such job", "The job does not exist.", type=mapping.NO_SUCH_JOB)


JSON_OBJECT = OpenApiTypes.OBJECT


class LandingView(OgcApiView):
    @extend_schema(tags=[TAG], summary="OGC API landing page", responses=JSON_OBJECT)
    def get(self, request):
        return Response(
            {
                "title": "TOSCA simulations (OGC API - Processes)",
                "description": "Run TOSCA simulation models through OGC API - Processes 1.0.",
                "links": [
                    mapping.link(self.url("ogcapi-landing"), "self", title="This document"),
                    mapping.link(
                        request.build_absolute_uri(reverse("schema")) + "?format=json",
                        "service-desc",
                        type=mapping.OPENAPI_TYPE,
                        title="API definition (all TOSCA endpoints; this API is tagged '" + TAG + "')",
                    ),
                    mapping.link(self.url("ogcapi-conformance"), mapping.REL + "conformance", title="Conformance"),
                    mapping.link(self.url("ogcapi-processes"), mapping.REL + "processes", title="Processes"),
                    mapping.link(self.url("ogcapi-jobs"), mapping.REL + "job-list", title="Jobs"),
                ],
            }
        )


class ConformanceView(OgcApiView):
    @extend_schema(tags=[TAG], summary="Conformance classes", responses=JSON_OBJECT)
    def get(self, request):
        return Response({"conformsTo": mapping.CONFORMANCE_CLASSES})


class ProcessListView(OgcApiView):
    @extend_schema(
        tags=[TAG],
        summary="Simulation models the caller may run",
        parameters=[OpenApiParameter("limit", int), OpenApiParameter("offset", int)],
        responses=JSON_OBJECT,
    )
    def get(self, request):
        limit, offset = self.paging()
        models = self.runnable_models()
        page = models[offset : offset + limit]
        return Response(
            {
                "processes": [
                    mapping.process_summary(model, self_href=self.url("ogcapi-process", process_id=model.key))
                    for model in page
                ],
                "links": self.page_links(offset, limit, has_next=len(models) > offset + limit),
            }
        )


class ProcessView(OgcApiView):
    @extend_schema(tags=[TAG], summary="Process description", responses=JSON_OBJECT)
    def get(self, request, process_id):
        model = self.visible_model(process_id)
        if not self.is_runnable(model):
            raise no_such_process()
        return Response(
            mapping.process_description(
                model,
                self_href=self.url("ogcapi-process", process_id=model.key),
                execute_href=self.url("ogcapi-execution", process_id=model.key),
                output_specs=self.output_specs(model),
            )
        )

    def output_specs(self, model: SimulationModel) -> list[dict]:
        """Output roles the runs the caller may read delivered as files."""
        rows = (
            SimulationOutput.objects.filter(
                run__in=policy.readable_runs(self.caller).filter(model=model), asset__isnull=False
            )
            .order_by("role", "display_order")
            .values("role", "title", "asset__mime")
        )
        specs: dict[str, dict] = {}
        for row in rows:
            specs.setdefault(row["role"], {"role": row["role"], "title": row["title"], "mime": row["asset__mime"]})
        return list(specs.values())


class ExecutionView(OgcApiView):
    throttle_classes = [OgcExecuteThrottle]

    @extend_schema(
        tags=[TAG],
        summary="Execute a process (always asynchronous; an identical readable run is reused)",
        request=JSON_OBJECT,
        responses={201: JSON_OBJECT},
    )
    def post(self, request, process_id):
        caller = self.caller
        model = self.visible_model(process_id)
        try:
            parsed = mapping.parse_execute_body(request.data)
        except mapping.ExecuteBodyError as exc:
            raise OgcError(400, "Invalid execute request", str(exc)) from exc
        try:
            result = policy.start_run(
                caller,
                model,
                parameters=parsed.parameters,
                seed=parsed.seed,
                title=parsed.title,
                visibility=parsed.visibility,
            )
        except policy.RunsDisabled as exc:
            raise OgcError(404, "Not found", "Starting runs is not enabled.") from exc
        except policy.RunAccessDenied as exc:
            raise OgcError(403, "Forbidden", "You are not allowed to run this process.") from exc
        except policy.VisibilityNotAllowed as exc:
            raise OgcError(
                400, "Invalid execute request",
                "Only members of the model's organization can share runs with it.",
            ) from exc
        except QuotaExceeded as exc:
            raise OgcError(
                429, "Too many requests", str(exc), headers={"Retry-After": str(exc.retry_after)}
            ) from exc
        except RunnerUnavailable as exc:
            raise OgcError(409, "Process unavailable", str(exc)) from exc
        except RunSubmissionError as exc:
            raise OgcError(400, "Invalid inputs", "The inputs are not valid for this process.", errors=exc.errors) from exc
        run = result.run
        run.model = model
        info = self.job_status(run)
        results_href = mapping.results_href(self.url("ogcapi-job-results", job_id=run.pk), parsed.response)
        info["links"] = [item for item in info["links"] if item["rel"] != mapping.REL + "results"]
        info["links"].append(mapping.link(results_href, mapping.REL + "results", title="Job results"))
        body = {**info, "reused": result.reused}
        return Response(
            body, status=status.HTTP_201_CREATED, headers={"Location": self.url("ogcapi-job", job_id=run.pk)}
        )


class JobListView(OgcApiView):
    @extend_schema(
        tags=[TAG],
        summary="Runs the caller started",
        parameters=[
            OpenApiParameter("limit", int),
            OpenApiParameter("offset", int),
            OpenApiParameter("status", str, enum=list(mapping.STATUS_STATES)),
            OpenApiParameter("processID", str),
            OpenApiParameter("type", str),
        ],
        responses=JSON_OBJECT,
    )
    def get(self, request):
        limit, offset = self.paging()
        runs = (
            policy.readable_runs(self.caller)
            .filter(created_by=request.user)
            .select_related("model", "job")
            .order_by("-created_at", "id")
        )
        params = request.query_params
        if (kind := params.get("type")) is not None and kind != "process":
            runs = runs.none()
        if (state := params.get("status")) is not None:
            if state not in mapping.STATUS_STATES:
                raise OgcError(400, "Invalid status filter", f"'status' must be one of: {', '.join(mapping.STATUS_STATES)}.")
            runs = runs.filter(execution_state__in=mapping.STATUS_STATES[state])
        if process_id := params.get("processID"):
            runs = runs.filter(model__key=process_id)
        page = list(runs[offset : offset + limit + 1])
        return Response(
            {
                "jobs": [self.job_status(run) for run in page[:limit]],
                "links": self.page_links(offset, limit, has_next=len(page) > limit),
            }
        )


class JobView(OgcApiView):
    @extend_schema(tags=[TAG], summary="Job status", responses=JSON_OBJECT)
    def get(self, request, job_id):
        return Response(self.job_status(self.readable_run(job_id)))


class JobResultsView(OgcApiView):
    @extend_schema(
        tags=[TAG],
        summary="Job results as expiring links",
        parameters=[OpenApiParameter("response", str, enum=["raw", "document"])],
        responses={200: JSON_OBJECT, 204: None},
    )
    def get(self, request, job_id):
        style = request.query_params.get("response", "raw")
        if style not in ("raw", "document"):
            raise OgcError(400, "Invalid response parameter", "'response' must be 'raw' or 'document'.")
        run = self.readable_run(job_id)
        state = mapping.job_status(run)
        if state == "failed":
            raise OgcError(500, "Job failed", "The simulation run failed; there are no results.")
        if state != "successful":
            raise OgcError(
                404, "Result not ready", "The job has not finished yet.", type=mapping.RESULT_NOT_READY
            )
        files = self.files(run)
        if style == "document":
            return Response(mapping.results_document(files))
        response = HttpResponse(status=status.HTTP_204_NO_CONTENT)
        if files:
            response["Link"] = mapping.results_link_header(files)
        return response

    def files(self, run: SimulationRun) -> list[dict]:
        """Outputs that are files, as expiring links (one timestamp for the whole response)."""
        now = timezone.now()
        files = []
        for output in run.outputs.filter(asset__isnull=False).select_related("asset").order_by("display_order", "role"):
            asset = output.asset
            link = file_url(asset.storage_alias, asset.storage_path, self.request, now=now)
            files.append(
                {"role": output.role, "url": link.url, "mime": asset.mime, "expires_at": link.expires_at}
            )
        return files
