"""Regression tests for the GeodataEngine admin view-only path (2026-08-19).

An org staff user (e.g. DCS ADMIN) holds `view_geodataengine` via
`OrgRolePermissionBackend` (geodataengine is role-controlled, see
`settings.TOSCA_PERMISSION_MODELS["geodata_providers"]`), but
`GeodataEngineAdmin.has_change_permission` is hard-coded superuser-only
(engine mutation can affect every org that shares the engine, not just the
caller's). Django's own `ModelAdmin.get_form()` correctly renders that
combination as a read-only detail page by excluding every field from the
`ModelForm` -- but `GeodataEngineForm.__init__` unconditionally assumed
`self.fields['engine_type']` existed, so the read-only page 500'd with
`KeyError: 'engine_type'` instead of rendering.

These tests exercise the fix over real HTTP (`django.test.Client`), not just
the form/permission methods in isolation, so they catch template-level
regressions (e.g. the custom change_form.html rendering mutation buttons for
a view-only user) as well as the KeyError itself.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from tosca_api.apps.geodata_providers.models import GeodataEngine
from tosca_api.apps.organizations.models import (
    Organization,
    OrganizationAppEntitlement,
    UserAuthorizationSnapshot,
)

_CHANGELIST = reverse("admin:geodata_providers_geodataengine_changelist")


def _org_staff_user(username, org_slug, level, entitled_apps=("geodata_providers",)):
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


def _change_url(engine):
    return reverse("admin:geodata_providers_geodataengine_change", args=[engine.pk])


class GeodataEngineAdminViewOnlyTests(TestCase):
    def setUp(self):
        creator = User.objects.create_user(username="engine-owner-vo")
        self.engine = GeodataEngine.objects.create(
            name="view-only-engine",
            engine_type="geoserver",
            base_url="http://example.com/geoserver",
            public_url="http://example.com/geoserver",
            admin_username="admin",
            admin_password="secret",
            created_by=creator,
        )

    # 1. DCS org admin -> allowed engine changelist 200
    def test_org_admin_sees_changelist(self):
        user = _org_staff_user("dcs-admin-changelist", "dcs", "ADMIN")
        client = Client()
        client.force_login(user)

        response = client.get(_CHANGELIST)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.engine.name)

    # 2. DCS org admin -> allowed engine detail 200, read-only, save/delete action hidden
    def test_org_admin_sees_readonly_detail_without_mutation_actions(self):
        user = _org_staff_user("dcs-admin-detail", "dcs", "ADMIN")
        client = Client()
        client.force_login(user)

        response = client.get(_change_url(self.engine))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.engine.name)
        self.assertNotContains(response, 'name="_save"')
        self.assertNotContains(response, 'name="_continue"')
        self.assertNotContains(response, "Delete")
        self.assertNotContains(response, "Test Connection")
        self.assertNotContains(response, "Sync Now")
        self.assertNotContains(response, "Deactivate Provider")
        self.assertNotContains(response, "Force Delete Provider Tree")

    # 3. DCS org admin -> POST can't mutate
    def test_org_admin_cannot_post_change(self):
        user = _org_staff_user("dcs-admin-post", "dcs", "ADMIN")
        client = Client()
        client.force_login(user)

        response = client.post(
            _change_url(self.engine),
            data={"name": "hijacked-name", "engine_type": "geoserver", "_save": "Save"},
        )

        self.assertEqual(response.status_code, 403)
        self.engine.refresh_from_db()
        self.assertEqual(self.engine.name, "view-only-engine")

    # 4. Restricted engine (allow-listed to another org) is invisible to a
    # non-entitled org / not openable.
    def test_restricted_engine_not_visible_to_other_org(self):
        dcs_org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
        self.engine.organizations.add(dcs_org)

        user = _org_staff_user("hpa-admin-restricted", "hpa", "ADMIN")
        client = Client()
        client.force_login(user)

        list_response = client.get(_CHANGELIST)
        self.assertNotContains(list_response, self.engine.name)

        # get_queryset() filters the row out before get_object() ever sees
        # it, so Django's admin treats it as "doesn't exist" -- a redirect
        # back to the changelist with a "was deleted" message, same as a
        # stale/bookmarked URL, not a 404 (and never the object's data).
        detail_response = client.get(_change_url(self.engine))
        self.assertEqual(detail_response.status_code, 302)
        self.assertNotContains(detail_response, self.engine.name, status_code=302)

    # 5. Superuser -> detail editable and POST succeeds
    @patch("tosca_api.apps.geodata_providers.admin.EngineClientFactory.create_client")
    @patch("tosca_api.apps.geodata_providers.admin.GeodataEngineService.update_engine")
    def test_superuser_can_edit_and_save(self, mock_update_engine, mock_create_client):
        mock_create_client.return_value.validate_connection.return_value = None
        mock_update_engine.return_value = (self.engine, {})

        superuser = User.objects.create_superuser(
            username="root-engine", email="root@example.com", password="pw"
        )
        client = Client()
        client.force_login(superuser)

        get_response = client.get(_change_url(self.engine))
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, 'name="_save"')

        post_response = client.post(
            _change_url(self.engine),
            data={
                "name": "view-only-engine",
                "description": "updated by superuser",
                "engine_type": "geoserver",
                "base_url": self.engine.base_url,
                "public_url": self.engine.public_url,
                "admin_username": self.engine.admin_username,
                "admin_password": "",
                "api_key": "",
                "is_active": "on",
                "_save": "Save",
            },
        )

        self.assertEqual(post_response.status_code, 302)
        mock_update_engine.assert_called_once()

    # 6. View-only page never 500s with KeyError('engine_type'), regardless
    # of role level.
    def test_view_only_never_raises_engine_type_keyerror(self):
        for level in ("READER", "WRITER", "ADMIN"):
            with self.subTest(level=level):
                user = _org_staff_user(f"dcs-{level.lower()}-safe", "dcs", level)
                client = Client()
                client.force_login(user)

                response = client.get(_change_url(self.engine))

                self.assertNotEqual(response.status_code, 500)
