from django.urls import include, path
from rest_framework.routers import DefaultRouter

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
    path(
        "geosimulations/jobs/<uuid:job_id>/callback/",
        SimulationJobCallbackView.as_view(),
        name="simulationjob-callback",
    ),
    path("", include(router.urls)),
]
