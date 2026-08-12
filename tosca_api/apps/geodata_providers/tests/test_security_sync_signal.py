"""Tests for the Workspace `post_save` -> ACL sync signal (epic-11 ticket 08).

`GeoServerSecuritySyncService.sync` is mocked -- these tests only verify
*when* the signal fires, not what the service does with a real client (see
test_security_sync_service.py for that).
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from tosca_api.apps.geodata_providers.models import GeodataEngine, Workspace
from tosca_api.apps.organizations.models import Organization

_SYNC_TARGET = 'tosca_api.apps.geodata_providers.signals.GeoServerSecuritySyncService'


class WorkspaceAclSignalTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='acl-signal-user', password='testpass123')
        self.org = Organization.objects.get_or_create(slug='dcs', defaults={'name': 'DCS'})[0]
        self.other_org = Organization.objects.get_or_create(slug='gq', defaults={'name': 'GQ'})[0]
        self.engine = GeodataEngine.objects.create(
            name='ACL Signal Engine',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )

    def test_create_triggers_sync(self):
        with patch(_SYNC_TARGET) as mock_service_cls:
            Workspace.objects.create(
                geodata_engine=self.engine,
                organization=self.org,
                name='hamburg',
                created_by=self.user,
            )

        mock_service_cls.return_value.sync.assert_called_once()

    def test_unrelated_field_update_does_not_trigger_sync(self):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            organization=self.org,
            name='hamburg',
            created_by=self.user,
        )

        with patch(_SYNC_TARGET) as mock_service_cls:
            workspace.description = 'now with a description'
            workspace.save()

        mock_service_cls.assert_not_called()

    def test_visibility_change_triggers_sync(self):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            organization=self.org,
            name='hamburg',
            created_by=self.user,
        )

        with patch(_SYNC_TARGET) as mock_service_cls:
            workspace.visibility = Workspace.Visibility.PUBLIC
            workspace.save()

        mock_service_cls.return_value.sync.assert_called_once()

    def test_organization_change_triggers_sync(self):
        workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            organization=self.org,
            name='hamburg',
            created_by=self.user,
        )

        with patch(_SYNC_TARGET) as mock_service_cls:
            workspace.organization = self.other_org
            workspace.save()

        mock_service_cls.return_value.sync.assert_called_once()
