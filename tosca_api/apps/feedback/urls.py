from rest_framework.routers import DefaultRouter

from .views import GeoFeedbackViewSet

router = DefaultRouter()
router.register(r"feedback", GeoFeedbackViewSet, basename="feedback")

urlpatterns = router.urls
