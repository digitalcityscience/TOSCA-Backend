"""Regression tests for admin sidebar/app_index visibility of geodata_providers.

Reproduces the exact reported bug: an org-role-only staff user (WRITER/ADMIN
via OrgRolePermissionBackend, no real Permission rows) could act on Workspace
directly (has_perm-gated views worked) but the app_index page at
/admin/geodata_providers/ 404'd, because OrgRolePermissionBackend didn't
implement has_module_perms() -- Django fell through to ModelBackend, which
only sees real Permission rows and always denied. See
tosca_api/apps/organizations/auth_backend.py::OrgRolePermissionBackend.has_module_perms.
"""

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from tosca_api.apps.organizations.models import (
    Organization,
    OrganizationAppEntitlement,
    UserAuthorizationSnapshot,
)


def _role_staff_user(username, org_slug, level, entitled_apps=("geodata_providers",)):
    organization, _ = Organization.objects.get_or_create(
        slug=org_slug, defaults={"name": org_slug.upper()}
    )
    for app_label in entitled_apps:
        OrganizationAppEntitlement.objects.get_or_create(organization=organization, app_label=app_label)

    user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={org_slug: level}, default_org=org_slug, synced_at=timezone.now()
    )
    return user


class GeodataProvidersAppIndexVisibilityTestCase(TestCase):
    def test_writer_sees_app_index_not_404(self):
        user = _role_staff_user("corner-writer", "gq2", "WRITER")
        client = Client()
        client.force_login(user)

        response = client.get(reverse("admin:app_list", kwargs={"app_label": "geodata_providers"}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Workspace")

    def test_admin_sees_app_index_not_404(self):
        user = _role_staff_user("corner-admin", "gq2", "ADMIN")
        client = Client()
        client.force_login(user)

        response = client.get(reverse("admin:app_list", kwargs={"app_label": "geodata_providers"}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Workspace")

    def test_staff_without_entitlement_still_gets_404(self):
        """Baseline: the fix must not widen access beyond entitlement --
        a role in an org that isn't entitled to geodata_providers should
        still see the standard Django 404, not the app index."""
        user = _role_staff_user(
            "corner-unentitled", "gq6", "ADMIN", entitled_apps=("campaigns",)
        )
        client = Client()
        client.force_login(user)

        response = client.get(reverse("admin:app_list", kwargs={"app_label": "geodata_providers"}))

        self.assertEqual(response.status_code, 404)

    def test_writer_can_add_workspace_from_admin(self):
        """Matches the reported 'Save and add another' repro: a WRITER/ADMIN
        role user can reach the Workspace add form through the admin."""
        user = _role_staff_user("corner-writer-add", "gq2", "WRITER")
        client = Client()
        client.force_login(user)

        response = client.get(reverse("admin:geodata_providers_workspace_add"))

        self.assertEqual(response.status_code, 200)
