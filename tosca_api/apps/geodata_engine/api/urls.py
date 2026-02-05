from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    GeodataEngineViewSet, WorkspaceViewSet, 
    StoreViewSet, LayerViewSet
)

router = DefaultRouter()
router.register('engines', GeodataEngineViewSet, basename='engines')
router.register('workspaces', WorkspaceViewSet, basename='workspaces')
router.register('stores', StoreViewSet, basename='stores')
router.register('layers', LayerViewSet, basename='layers')

urlpatterns = [
    path('', include(router.urls)),
]