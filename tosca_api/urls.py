"""
URL configuration for tosca_api project.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from tosca_api.apps.authentication.views import (
    KeycloakLogoutView,
    admin_login_redirect,
)
from tosca_api.views import base, healthz, readyz

urlpatterns = [
    path('admin/logout/', KeycloakLogoutView.as_view(), name='admin_logout'),  # Override Django admin logout
    # Shadow Django's local admin login form so /admin/ is reachable only via
    # Keycloak SSO (closes the parallel username/password path). MUST stay
    # before admin.site.urls so it wins over the built-in admin:login route.
    path('admin/login/', admin_login_redirect, name='admin_login_sso'),
    # Health/readiness (see tosca_api/views.py — healthz is liveness-only,
    # readyz checks DB connectivity).
    path('healthz', healthz, name='healthz'),
    path('readyz', readyz, name='readyz'),
    # API Documentation
    path('api/v1/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/v1/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/v1/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    # App URLs
    path('api/v1/', include('tosca_api.apps.campaigns.urls')),
    path('api/v1/', include('tosca_api.apps.geostories.urls')),
    path('api/v1/', include('tosca_api.apps.events.urls')),
    path("api/v1/", include("tosca_api.apps.feedback.urls")),
    path("api/v1/", include("tosca_api.apps.core.urls")),
    path("api/v1/", include("tosca_api.apps.geocontext.urls")),
    path('api/v1/catalog/', include('tosca_api.apps.catalog_api.urls'), name='catalog_api'),
    # Authentication is mounted at both root and accounts/ deliberately:
    # settings reference paths under both prefixes (LOGIN_REDIRECT_URL=
    # /welcome/ at root, LOGIN_URL=/accounts/login/ and LOGOUT_REDIRECT_URL=
    # /accounts/logout/ under accounts/). See test_url_routing.py, which
    # resolves every settings-referenced auth path against this urlconf.
    # NOTE: because this root mount is registered before the path('', base)
    # pattern below, its own '' sub-route (welcome_view, name='home') always
    # wins for GET / — the base view is unreachable. Pre-existing behavior,
    # not introduced by this cleanup; left as-is pending a product decision
    # on what should render at '/'.
    path('', include('tosca_api.apps.authentication.urls')),
    path('', base, name='base'),
    path('accounts/', include('tosca_api.apps.authentication.urls')),
    path('admin/', admin.site.urls),
]

if settings.DEBUG:
    # Serve uploaded media via the dev server. In production, MEDIA_URL is
    # served by the reverse proxy / object store, not Django.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
