"""
URL configuration for tosca_api project.
"""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from tosca_api.apps.authentication.views import KeycloakLogoutView
from tosca_api.views import base

urlpatterns = [
    path('admin/logout/', KeycloakLogoutView.as_view(), name='admin_logout'),  # Override Django admin logout
    # API Documentation
    path('api/v1/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/v1/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/v1/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    # App URLs
    path('', include('tosca_api.apps.authentication.urls')),  # ← Include authentication app URLs
    path('api/v1/', include('tosca_api.apps.campaigns.urls')),
    path('api/v1/', include('tosca_api.apps.geostories.urls')),
    path('api/v1/', include('tosca_api.apps.events.urls')),
    path("api/v1/", include("tosca_api.apps.feedback.urls")),
    path('api/v1/catalog/', include('tosca_api.apps.catalog_api.urls'), name='catalog_api'),
    path('admin/logout/', KeycloakLogoutView.as_view(), name='admin_logout'),
    path('', base, name='base'),
    path('accounts/', include('tosca_api.apps.authentication.urls')),  # Include allauth URLs for authentication
    # Backward-compatible alias (can be removed after clients migrate).
    path('admin/', admin.site.urls),
]
