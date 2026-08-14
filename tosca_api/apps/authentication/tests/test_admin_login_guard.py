"""Tests for the SSO-only admin login guard and branded error pages.

Security intent: /admin/ must be reachable only through Keycloak. Django's
built-in /admin/login/ accepts local passwords (a parallel path that bypasses
SSO), so we shadow it with admin_login_redirect. See tosca_api/urls.py and
tosca_api.apps.authentication.views.admin_login_redirect.
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import resolve

from tosca_api.apps.authentication.views import (
    KEYCLOAK_LOGIN_URL,
    admin_login_redirect,
)

User = get_user_model()


class AdminLoginGuardRouteTests(TestCase):
    def test_admin_login_resolves_to_guard_not_django_admin(self):
        """Our redirect must win over admin.site's built-in login route."""
        match = resolve("/admin/login/")
        self.assertIs(match.func, admin_login_redirect)

    def test_anonymous_is_sent_to_keycloak(self):
        resp = Client().get("/admin/login/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], KEYCLOAK_LOGIN_URL)

    def test_authenticated_non_staff_is_sent_to_welcome_not_login_form(self):
        user = User.objects.create_user(username="plain-user")
        client = Client()
        client.force_login(user)

        resp = client.get("/admin/login/")

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/welcome/")

    def test_non_staff_hitting_admin_never_reaches_a_usable_login_form(self):
        """End-to-end: /admin/ for a non-staff user lands on /welcome/."""
        user = User.objects.create_user(username="curious-user")
        client = Client()
        client.force_login(user)

        resp = client.get("/admin/", follow=True)

        # /admin/ -> /admin/login/?next=/admin/ -> /welcome/
        self.assertIn(("/welcome/", 302), resp.redirect_chain)

    def test_staff_user_still_routed_through_keycloak(self):
        staff = User.objects.create_user(username="staff-user", is_staff=True)
        client = Client()
        client.force_login(staff)

        resp = client.get("/admin/login/")

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], KEYCLOAK_LOGIN_URL)


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
class ErrorPageTests(TestCase):
    def test_404_uses_branded_template(self):
        resp = Client().get("/this-path-does-not-exist-xyz/")
        self.assertEqual(resp.status_code, 404)
        self.assertTemplateUsed(resp, "404.html")
        self.assertContains(resp, "TOSCA API", status_code=404)
