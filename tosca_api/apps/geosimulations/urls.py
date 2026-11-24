from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import ogc_api
from .views import (
    SimulationComparisonViewSet,
    SimulationJobCallbackView,
    SimulationModelViewSet,
    SimulationRunViewSet,
)

router = DefaultRouter()
# Runs and comparisons first: "geosimulations/runs/" must not be read as a model id.
router.register(r"geosimulations/runs", SimulationRunViewSet, basename="simulationrun")
router.register(r"geosimulations/comparisons", SimulationComparisonViewSet, basename="simulationcomparison")
router.register(r"geosimulations", SimulationModelViewSet, basename="simulationmodel")

urlpatterns = [
    path("ogcapi/", ogc_api.LandingView.as_view(), name="ogcapi-landing"),
    path("ogcapi/conformance", ogc_api.ConformanceView.as_view(), name="ogcapi-conformance"),
    path("ogcapi/processes", ogc_api.ProcessListView.as_view(), name="ogcapi-processes"),
    path("ogcapi/processes/<str:process_id>", ogc_api.ProcessView.as_view(), name="ogcapi-process"),
    path(
        "ogcapi/processes/<str:process_id>/execution",
        ogc_api.ExecutionView.as_view(),
        name="ogcapi-execution",
    ),
    path("ogcapi/jobs", ogc_api.JobListView.as_view(), name="ogcapi-jobs"),
    path("ogcapi/jobs/<str:job_id>", ogc_api.JobView.as_view(), name="ogcapi-job"),
    path("ogcapi/jobs/<str:job_id>/results", ogc_api.JobResultsView.as_view(), name="ogcapi-job-results"),
    path(
        "geosimulations/jobs/<uuid:job_id>/callback/",
        SimulationJobCallbackView.as_view(),
        name="simulationjob-callback",
    ),
    path("", include(router.urls)),
]
