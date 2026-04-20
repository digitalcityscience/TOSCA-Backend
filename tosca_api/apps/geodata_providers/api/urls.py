from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import GeodataEngineViewSet, LayerViewSet, StoreViewSet, WorkspaceViewSet

router = DefaultRouter()
router.register('engines', GeodataEngineViewSet, basename='geoengine-engines')
router.register('workspaces', WorkspaceViewSet, basename='geoengine-workspaces')
router.register('stores', StoreViewSet, basename='geoengine-stores')
router.register('layers', LayerViewSet, basename='geoengine-layers')

urlpatterns = [
    path('', include(router.urls)),
]
