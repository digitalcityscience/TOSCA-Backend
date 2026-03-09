"""
URL configuration for tosca_api project.
"""
from django.contrib import admin
from django.urls import include, path

from tosca_api.apps.authentication.views import KeycloakLogoutView
from tosca_api.views import base

urlpatterns = [
    path('admin/logout/', KeycloakLogoutView.as_view(), name='admin_logout'),
    path('', base, name='base'),
    path('accounts/', include('tosca_api.apps.authentication.urls')),  # Include allauth URLs for authentication
    path('api/geoengine/', include('tosca_api.apps.geodata_engine.api.urls'), name='geodata_engine_api'),
    path("console/", include("tosca_api.apps.geo_console.urls"), name="geo_console"),
    # Backward-compatible alias (can be removed after clients migrate).
    path('admin/', admin.site.urls),
]
