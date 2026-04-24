from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import GeoStoryViewSet

router = DefaultRouter()
router.register(r"stories", GeoStoryViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
