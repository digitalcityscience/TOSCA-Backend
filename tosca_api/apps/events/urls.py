from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import EventSeriesViewSet, EventViewSet

router = DefaultRouter()
router.register(r"events", EventViewSet, basename="event")
router.register(r"event-series", EventSeriesViewSet, basename="event-series")

urlpatterns = [
    path("", include(router.urls)),
]
