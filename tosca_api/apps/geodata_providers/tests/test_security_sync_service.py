"""Tests for GeoServerSecuritySyncService (epic-11 ticket 08/09, canonical §5c).

GeoServerClient is mocked -- no real GeoServer. Covers PRIVATE/PUBLIC rule
computation, the PUBLIC<->PRIVATE transition, and the ticket 09 hard-fail
contract: any push failure raises `GeoServerACLSyncError` instead of being
logged and swallowed.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from tosca_api.apps.geodata_providers.models import GeodataEngine, Workspace
from tosca_api.apps.geodata_providers.results import OperationResult
from tosca_api.apps.geodata_providers.security_sync import (
    GeoServerACLSyncError,
    GeoServerSecuritySyncService,
)
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
        # conftest.py's default GeodataEngine.get_client patch makes this creation's
        # ACL push succeed; individual tests below override it per-call.
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

    def test_private_workspace_pushes_reader_writer_and_admin_rules(self):
        self.workspace.visibility = Workspace.Visibility.PRIVATE
        client = self._mock_client()

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            GeoServerSecuritySyncService(self.workspace).sync()

        # read = writer + reader (writer also reads); write & workspace-admin = writer.
        client.set_layer_rule.assert_any_call('hamburg.*.r', 'ROLE_DCS_WRITER,ROLE_DCS_READER')
        client.set_layer_rule.assert_any_call('hamburg.*.w', 'ROLE_DCS_WRITER')
        client.set_layer_rule.assert_any_call('hamburg.*.a', 'ROLE_DCS_WRITER')
        self.assertEqual(client.set_layer_rule.call_count, 3)

    def test_rules_are_pushed_read_then_write_then_admin(self):
        client = self._mock_client()
        calls_in_order = []
        client.set_layer_rule.side_effect = lambda key, roles: (
            calls_in_order.append(key) or OperationResult(success=True, message='ok')
        )

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            GeoServerSecuritySyncService(self.workspace).sync()

        self.assertEqual(calls_in_order, ['hamburg.*.r', 'hamburg.*.w', 'hamburg.*.a'])

    def test_public_workspace_opens_read_to_anonymous_but_not_write_or_admin(self):
        self.workspace.visibility = Workspace.Visibility.PUBLIC
        client = self._mock_client()

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            GeoServerSecuritySyncService(self.workspace).sync()

        client.set_layer_rule.assert_any_call('hamburg.*.r', '*')
        client.set_layer_rule.assert_any_call('hamburg.*.w', 'ROLE_DCS_WRITER')
        client.set_layer_rule.assert_any_call('hamburg.*.a', 'ROLE_DCS_WRITER')

    def test_public_to_private_transition_reverts_read_rule_to_org_roles(self):
        client = self._mock_client()

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            self.workspace.visibility = Workspace.Visibility.PUBLIC
            GeoServerSecuritySyncService(self.workspace).sync()
            client.set_layer_rule.assert_any_call('hamburg.*.r', '*')

            self.workspace.visibility = Workspace.Visibility.PRIVATE
            GeoServerSecuritySyncService(self.workspace).sync()

        # same key, PUT-via-set_layer_rule now carries writer+reader instead of "*"
        last_read_rule_call = [
            call for call in client.set_layer_rule.call_args_list if call.args[0] == 'hamburg.*.r'
        ][-1]
        self.assertEqual(last_read_rule_call.args[1], 'ROLE_DCS_WRITER,ROLE_DCS_READER')

    def test_no_engine_is_a_silent_noop(self):
        self.workspace.geodata_engine = None

        GeoServerSecuritySyncService(self.workspace).sync()  # must not raise

    def test_inactive_engine_is_a_silent_noop_without_calling_client(self):
        self.engine.is_active = False
        self.engine.save()
        client = self._mock_client()

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            GeoServerSecuritySyncService(self.workspace).sync()  # must not raise

        client.set_layer_rule.assert_not_called()

    def test_workspace_create_rolls_back_when_acl_push_fails(self):
        client = self._mock_client()
        client.set_layer_rule.return_value = OperationResult(
            success=False, error='HTTP 500', message='boom'
        )

        with patch.object(GeodataEngine, 'get_client', return_value=client):
            with self.assertRaises(GeoServerACLSyncError):
                Workspace.objects.create(
                    geodata_engine=self.engine,
                    organization=self.org,
                    name='bremen',
                    created_by=self.user,
                )

        self.assertFalse(Workspace.objects.filter(name='bremen').exists())
