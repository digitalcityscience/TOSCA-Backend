# NOT mounted in tosca_api/urls.py -- deliberately quarantined (security
# tickets 2026-08-19 ticket 08). GeodataEngineViewSet's default CRUD actions
# are plain IsAuthenticated with no capability/org gate (only its custom
# sync/sync_all/validate/push actions are IsAdminUser) and would let any
# authenticated user write/delete GeodataEngine rows -- which hold admin
# GeoServer credentials -- if this router were wired into the root URLconf.
# Workspace/Store/Layer management stays admin-only until that gap is closed.
# Do not mount this without hardening GeodataEngineViewSet's permissions first.
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
