"""
Regression tests for issue 20: tosca_api/urls.py had duplicate imports, a
duplicate admin/logout/ route registration, and mounts
tosca_api.apps.authentication.urls at both '' and 'accounts/' — which turned
out to be load-bearing (LOGIN_REDIRECT_URL uses the root prefix,
LOGIN_URL/LOGOUT_REDIRECT_URL use the accounts/ prefix), so it's kept as a
documented, tested alias rather than removed.
"""
from django.conf import settings
from django.test import SimpleTestCase
from django.urls import Resolver404, resolve, reverse

from tosca_api.apps.authentication.views import KeycloakLogoutView
from tosca_api.urls import urlpatterns


class AdminLogoutRouteTests(SimpleTestCase):
    def test_admin_logout_registered_exactly_once(self):
        matches = [
            pattern for pattern in urlpatterns
            if getattr(pattern, "pattern", None) is not None
            and str(pattern.pattern) == "admin/logout/"
        ]
        self.assertEqual(len(matches), 1)

    def test_admin_logout_resolves_to_keycloak_logout_view(self):
        match = resolve("/admin/logout/")
        self.assertIs(match.func.view_class, KeycloakLogoutView)


class SpectacularRoutesTests(SimpleTestCase):
    def test_schema_docs_redoc_resolve_to_expected_paths(self):
        self.assertEqual(reverse("schema"), "/api/v1/schema/")
        self.assertEqual(reverse("swagger-ui"), "/api/v1/docs/")
        self.assertEqual(reverse("redoc"), "/api/v1/redoc/")


class AuthUrlAliasTests(SimpleTestCase):
    """The root/accounts/ duplication is intentional: settings reference
    paths under both prefixes, so both must resolve.
    """

    def test_settings_referenced_auth_paths_all_resolve(self):
        referenced_paths = {
            settings.LOGIN_URL,
            settings.LOGIN_REDIRECT_URL,
            settings.LOGOUT_REDIRECT_URL,
        }
        for path in referenced_paths:
            with self.subTest(path=path):
                try:
                    resolve(path)
                except Resolver404:
                    self.fail(f"{path!r} is referenced by settings but does not resolve to any view.")

    def test_authentication_urls_resolve_under_both_prefixes(self):
        for path in ("/welcome/", "/accounts/welcome/", "/login/", "/accounts/login/", "/logout/", "/accounts/logout/"):
            with self.subTest(path=path):
                try:
                    resolve(path)
                except Resolver404:
                    self.fail(f"{path!r} should resolve under the root/accounts/ authentication alias.")
