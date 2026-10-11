from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import SimulationComparisonViewSet, SimulationModelViewSet, SimulationRunViewSet

router = DefaultRouter()
# Runs and comparisons first: "geosimulations/runs/" must not be read as a model id.
router.register(r"geosimulations/runs", SimulationRunViewSet, basename="simulationrun")
router.register(r"geosimulations/comparisons", SimulationComparisonViewSet, basename="simulationcomparison")
router.register(r"geosimulations", SimulationModelViewSet, basename="simulationmodel")

urlpatterns = [
    path("", include(router.urls)),
]
