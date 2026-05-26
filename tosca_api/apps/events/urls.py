from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    EventSeriesViewSet,
    EventTaxonomyRegistryView,
    EventTypeRegistryView,
    EventViewSet,
)

router = DefaultRouter()
router.register(r"events", EventViewSet, basename="event")
router.register(r"event-series", EventSeriesViewSet, basename="event-series")

urlpatterns = [
    path("event-taxonomy/", EventTaxonomyRegistryView.as_view(), name="event-taxonomy"),
    path("event-types/", EventTypeRegistryView.as_view(), name="event-types"),
    path("", include(router.urls)),
]
