"""End-to-end GeoServer ACL integration test (epic-11 ticket 10, canonical §5c).

Automates the manual §5c verification against a real, running GeoServer:
Workspace.save() -> GeoServerSecuritySyncService -> GeoServer Data Security
ACL, read back over the real REST API. Runs under `make django-test-integration`
(`-m integration`) -- see `test_integration.py` for the same env-var pattern.
"""
import os
import random
import string

import pytest
from django.contrib.auth.models import User
from django.test import TestCase

from tosca_api.apps.geodata_providers.models import GeodataEngine, Workspace
from tosca_api.apps.geodata_providers.security_sync import (
    GeoServerACLSyncError,
    GeoServerSecuritySyncService,
)
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.integration


def _random_suffix() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


class GeoServerAclIntegrationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username=f"acl-integ-{_random_suffix()}", password="testpass123"
        )
        self.org = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})[0]
        self.engine = GeodataEngine.objects.create(
            name=f"ACL Integration Engine {_random_suffix()}",
            engine_type="geoserver",
            base_url=f"http://{os.getenv('GEOSERVER_HOST')}:{os.getenv('GEOSERVER_PORT')}/geoserver",
            public_url=os.getenv(
                "GEOSERVER_PUBLIC_URL",
                f"http://localhost:{os.getenv('GEOSERVER_PORT')}/geoserver",
            ),
            admin_username=os.getenv("GEOSERVER_ADMIN_USER"),
            admin_password=os.getenv("GEOSERVER_ADMIN_PASSWORD"),
            is_active=True,
            created_by=self.user,
        )
        self.gs_client = self.engine.get_client()
        self.ws_name = f"inttest_acl_{_random_suffix()}"
        self.read_key = f"{self.ws_name}.*.r"
        self.write_key = f"{self.ws_name}.*.w"

    def tearDown(self):
        self.gs_client.delete_layer_rule(self.read_key)
        self.gs_client.delete_layer_rule(self.write_key)

    def _create_workspace(self, visibility):
        return Workspace.objects.create(
            geodata_engine=self.engine,
            organization=self.org,
            name=self.ws_name,
            visibility=visibility,
            created_by=self.user,
        )

    def test_private_workspace_writes_reader_and_writer_rules(self):
        self._create_workspace(Workspace.Visibility.PRIVATE)

        rules = self.gs_client.get_layer_rules()

        self.assertEqual(rules[self.read_key], self.org.reader_role)
        self.assertEqual(rules[self.write_key], self.org.writer_role)

    def test_public_workspace_opens_read_to_anonymous_but_not_write(self):
        self._create_workspace(Workspace.Visibility.PUBLIC)

        rules = self.gs_client.get_layer_rules()

        self.assertEqual(rules[self.read_key], "*")
        self.assertEqual(rules[self.write_key], self.org.writer_role)

    def test_private_to_public_transition_updates_read_rule_in_place(self):
        workspace = self._create_workspace(Workspace.Visibility.PRIVATE)
        self.assertEqual(self.gs_client.get_layer_rules()[self.read_key], self.org.reader_role)

        workspace.visibility = Workspace.Visibility.PUBLIC
        workspace.save()

        rules = self.gs_client.get_layer_rules()
        self.assertEqual(rules[self.read_key], "*")
        self.assertEqual(rules[self.write_key], self.org.writer_role)

    def test_repeated_sync_is_idempotent(self):
        workspace = self._create_workspace(Workspace.Visibility.PRIVATE)

        # The post_save signal only fires on create or an organization/visibility
        # change, so a bare re-save wouldn't re-push -- call the service directly
        # to exercise the real POST-then-PUT idempotency against GeoServer.
        GeoServerSecuritySyncService(workspace).sync()
        GeoServerSecuritySyncService(workspace).sync()

        rules = self.gs_client.get_layer_rules()
        self.assertEqual(rules[self.read_key], self.org.reader_role)
        self.assertEqual(rules[self.write_key], self.org.writer_role)

    def test_break_glass_global_admin_rule_is_untouched(self):
        before = self.gs_client.get_layer_rules().get("*.*.w")

        self._create_workspace(Workspace.Visibility.PRIVATE)

        after = self.gs_client.get_layer_rules().get("*.*.w")
        self.assertEqual(before, after)

    def test_push_failure_raises_and_workspace_is_not_persisted(self):
        self.engine.admin_password = "definitely-wrong-password"
        self.engine.save()

        with self.assertRaises(GeoServerACLSyncError):
            self._create_workspace(Workspace.Visibility.PRIVATE)

        self.assertFalse(Workspace.objects.filter(name=self.ws_name).exists())
