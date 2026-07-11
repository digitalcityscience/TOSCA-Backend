from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from tosca_api.apps.geodata_providers.api.serializers import LayerSerializer
from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Workspace
from tosca_api.apps.geodata_providers.services.commands.layer_service import LayerService, LayerUpdateService


class LayerServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='layer-service-user', password='testpass123')
        self.engine = GeodataEngine.objects.create(
            name='Layer Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='demo_ws',
            description='workspace',
            created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            name='demo_store',
            description='store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            created_by=self.user,
        )

    @patch('tosca_api.apps.geodata_providers.services.commands.layer_service.get_table_bbox')
    @patch('tosca_api.apps.geodata_providers.services.commands.layer_service.EngineClientFactory.create_client')
    def test_publish_postgis_persists_layer_after_remote_verify(self, mock_create_client, mock_get_table_bbox):
        client = mock_create_client.return_value
        client.get_layer_info.return_value = None
        client.publish_featuretype.return_value = {'success': True}
        client.verify_featuretype.return_value = True
        mock_get_table_bbox.return_value = {'minx': 1, 'miny': 2, 'maxx': 3, 'maxy': 4}

        result = LayerService.publish_postgis(
            workspace=self.workspace,
            store=self.store,
            table_name='roads',
            layer_name='roads_public',
            title='Roads',
            description='Road network',
            geometry_column='geom',
            geometry_type='LineString',
            srid=4326,
            user=self.user,
        )

        self.assertTrue(result['success'])
        self.assertTrue(result['created'])
        self.assertEqual(result['bbox']['minx'], 1)
        layer = Layer.objects.get(workspace=self.workspace, name='roads_public')
        self.assertEqual(layer.table_name, 'roads')
        self.assertEqual(layer.publishing_state, 'PUBLISHED')

    @patch('tosca_api.apps.geodata_providers.services.commands.layer_service.EngineClientFactory.create_client')
    def test_update_published_metadata_updates_remote_then_local(self, mock_create_client):
        layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='roads_public',
            title='Old Title',
            description='old',
            table_name='roads',
            geometry_column='geom',
            geometry_type='LineString',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        client = mock_create_client.return_value
        client.update_featuretype.return_value = {'success': True}
        client.verify_featuretype_metadata.return_value = {'verified': True, 'mismatches': {}, 'actual': {}}

        result = LayerService.update_published_metadata(
            layer=layer,
            title='New Title',
            description='new',
        )

        self.assertTrue(result['success'])
        layer.refresh_from_db()
        self.assertEqual(layer.title, 'New Title')
        self.assertEqual(layer.description, 'new')

    @patch('tosca_api.apps.geodata_providers.services.commands.layer_service.EngineClientFactory.create_client')
    def test_delete_layer_safe_deletes_local_record_after_remote_unpublish(self, mock_create_client):
        layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='roads_public',
            title='Roads',
            description='roads',
            table_name='roads',
            geometry_column='geom',
            geometry_type='LineString',
            srid=4326,
            publishing_state='PUBLISHED',
            created_by=self.user,
        )
        client = mock_create_client.return_value
        client.delete_layer.return_value = {'success': True, 'message': 'deleted'}
        client.verify_featuretype.return_value = False

        result = LayerService.delete_layer_safe(layer)

        self.assertTrue(result['success'])
        self.assertFalse(Layer.objects.filter(pk=layer.pk).exists())


class LayerUpdateServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='layer-update-user', password='testpass123')
        self.engine = GeodataEngine.objects.create(
            name='Layer Update Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
            public_url='http://example.com/geoserver',
            admin_username='admin',
            admin_password='secret',
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='demo_ws',
            description='workspace',
            created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            name='demo_store',
            description='store',
            store_type='postgis',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
            created_by=self.user,
        )
        self.layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='roads',
            title='Old Title',
            description='old',
            table_name='roads',
            geometry_column='geom',
            geometry_type='LineString',
            srid=4326,
            publishing_state='DRAFT',
            created_by=self.user,
        )

    def test_apply_rejects_empty_field_set(self):
        result = LayerUpdateService.apply(
            layer=self.layer,
            fields={'name': 'should_be_ignored'},
            serializer_class=LayerSerializer,
        )

        self.assertFalse(result['success'])
        self.assertEqual(result['status_code'], 400)
        self.assertIn('detail', result['body'])

    def test_apply_updates_draft_layer_via_serializer(self):
        result = LayerUpdateService.apply(
            layer=self.layer,
            fields={'title': 'New Title', 'srid': 3857, 'name': 'ignored'},
            serializer_class=LayerSerializer,
        )

        self.assertTrue(result['success'])
        self.layer.refresh_from_db()
        self.assertEqual(self.layer.title, 'New Title')
        self.assertEqual(self.layer.srid, 3857)
        self.assertEqual(self.layer.name, 'roads')

    @patch('tosca_api.apps.geodata_providers.services.commands.layer_service.EngineClientFactory.create_client')
    def test_apply_syncs_published_layer_metadata_then_updates_django_only_fields(self, mock_create_client):
        self.layer.publishing_state = 'PUBLISHED'
        self.layer.save(update_fields=['publishing_state'])
        client = mock_create_client.return_value
        client.update_featuretype.return_value = {'success': True}
        client.verify_featuretype_metadata.return_value = {'verified': True, 'mismatches': {}, 'actual': {}}

        result = LayerUpdateService.apply(
            layer=self.layer,
            fields={'title': 'Published Title', 'srid': 3857},
            serializer_class=LayerSerializer,
        )

        self.assertTrue(result['success'])
        self.layer.refresh_from_db()
        self.assertEqual(self.layer.title, 'Published Title')
        self.assertEqual(self.layer.srid, 3857)
        client.update_featuretype.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.services.commands.layer_service.EngineClientFactory.create_client')
    def test_apply_returns_error_when_published_metadata_sync_fails(self, mock_create_client):
        self.layer.publishing_state = 'PUBLISHED'
        self.layer.save(update_fields=['publishing_state'])
        client = mock_create_client.return_value
        client.update_featuretype.side_effect = RuntimeError('geoserver unreachable')

        result = LayerUpdateService.apply(
            layer=self.layer,
            fields={'title': 'Published Title'},
            serializer_class=LayerSerializer,
        )

        self.assertFalse(result['success'])
        self.assertEqual(result['status_code'], 400)
        self.assertIn('error', result['body'])
        self.layer.refresh_from_db()
        self.assertEqual(self.layer.title, 'Old Title')

