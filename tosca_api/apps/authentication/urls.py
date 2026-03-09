from django.urls import path, include
from tosca_api.apps.authentication.views import (
    AutoSignupView,
    KeycloakLogoutView,
    KeycloakRedirectView,
    test_token_auth,
    welcome_view,
)

urlpatterns = [
    path('', welcome_view, name='home'),
    path('welcome/', welcome_view, name='welcome'),  # Welcome page for normal users
    path('login/', KeycloakRedirectView.as_view(), name='account_login'),  # Show custom login page
    path('signup/', KeycloakRedirectView.as_view(), name='account_signup'),  # Block local signup
    path('logout/', KeycloakLogoutView.as_view(), name='account_logout'),  # Custom logout
    # Override allauth's socialaccount signup with our auto-signup
    path('3rdparty/signup/', AutoSignupView.as_view(), name='socialaccount_signup'),
    path('', include('allauth.urls')),  # Keycloak OIDC (callback + login)
    path('api/v1/auth/test-token/', test_token_auth),
]
