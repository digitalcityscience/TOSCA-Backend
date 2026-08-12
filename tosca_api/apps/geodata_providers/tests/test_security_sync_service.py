"""Tests for GeoServerSecuritySyncService (epic-11 ticket 08, canonical §5c).

GeoServerClient is mocked -- no real GeoServer. Covers PRIVATE/PUBLIC rule
computation and the PUBLIC<->PRIVATE transition.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from tosca_api.apps.geodata_providers.models import GeodataEngine, Workspace
from tosca_api.apps.geodata_providers.results import OperationResult
from tosca_api.apps.geodata_providers.security_sync import GeoServerSecuritySyncService
from tosca_api.apps.organizations.models import Organization


class GeoServerSecuritySyncServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='acl-sync-user', password='testpass123')
        self.org = Organization.objects.get_or_create(slug='dcs', defaults={'name': 'DCS'})[0]
        self.engine = GeodataEngine.objects.create(
            name='ACL Sync Engine',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            organization=self.org,
            name='hamburg',
            created_by=self.user,
        )

    def _mock_client(self):
        client = MagicMock()
        client.set_layer_rule.return_value = OperationResult(success=True, message='ok')
        return client

    def test_private_workspace_pushes_org_reader_and_writer_roles(self):
        self.workspace.visibility = Workspace.Visibility.PRIVATE
        client = self._mock_client()

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            success = GeoServerSecuritySyncService(self.workspace).sync()

        self.assertTrue(success)
        client.set_layer_rule.assert_any_call('hamburg.*.r', 'ROLE_DCS_READER')
        client.set_layer_rule.assert_any_call('hamburg.*.w', 'ROLE_DCS_WRITER')
        self.assertEqual(client.set_layer_rule.call_count, 2)

    def test_public_workspace_opens_read_to_anonymous_but_not_write(self):
        self.workspace.visibility = Workspace.Visibility.PUBLIC
        client = self._mock_client()

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            success = GeoServerSecuritySyncService(self.workspace).sync()

        self.assertTrue(success)
        client.set_layer_rule.assert_any_call('hamburg.*.r', '*')
        client.set_layer_rule.assert_any_call('hamburg.*.w', 'ROLE_DCS_WRITER')

    def test_public_to_private_transition_reverts_read_rule_to_org_role(self):
        client = self._mock_client()

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            self.workspace.visibility = Workspace.Visibility.PUBLIC
            GeoServerSecuritySyncService(self.workspace).sync()
            client.set_layer_rule.assert_any_call('hamburg.*.r', '*')

            self.workspace.visibility = Workspace.Visibility.PRIVATE
            GeoServerSecuritySyncService(self.workspace).sync()

        # same key, PUT-via-set_layer_rule now carries the org reader role instead of "*"
        last_read_rule_call = [
            call for call in client.set_layer_rule.call_args_list if call.args[0] == 'hamburg.*.r'
        ][-1]
        self.assertEqual(last_read_rule_call.args[1], 'ROLE_DCS_READER')

    def test_sync_is_idempotent_across_repeated_calls(self):
        client = self._mock_client()

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            first = GeoServerSecuritySyncService(self.workspace).sync()
            second = GeoServerSecuritySyncService(self.workspace).sync()

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(client.set_layer_rule.call_count, 4)

    def test_rule_failure_is_logged_and_returns_false_without_raising(self):
        client = self._mock_client()
        client.set_layer_rule.return_value = OperationResult(
            success=False, error='HTTP 500', message='boom'
        )

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            success = GeoServerSecuritySyncService(self.workspace).sync()

        self.assertFalse(success)

    def test_no_engine_is_a_noop(self):
        self.workspace.geodata_engine = None

        success = GeoServerSecuritySyncService(self.workspace).sync()

        self.assertFalse(success)

    def test_inactive_engine_is_skipped(self):
        self.engine.is_active = False
        self.engine.save()
        client = self._mock_client()

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            success = GeoServerSecuritySyncService(self.workspace).sync()

        self.assertFalse(success)
        client.set_layer_rule.assert_not_called()
