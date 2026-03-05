"""
URL configuration for tosca_api project.
"""
from django.contrib import admin
from django.urls import include, path

from tosca_api.apps.authentication.views import KeycloakLogoutView

urlpatterns = [
    path('admin/logout/', KeycloakLogoutView.as_view(), name='admin_logout'),
    path('', include('tosca_api.apps.authentication.urls')),
    # path('console/', include('tosca_api.apps.console.urls')),
    path('api/geoengine/', include('tosca_api.apps.geodata_engine.api.urls')),
    # Backward-compatible alias (can be removed after clients migrate).
    path('admin/', admin.site.urls),
]
