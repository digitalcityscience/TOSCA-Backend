from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import LayerViewSet

router = DefaultRouter()
router.register("", LayerViewSet, basename="layer")

urlpatterns = [
    path("", include(router.urls)),
]
