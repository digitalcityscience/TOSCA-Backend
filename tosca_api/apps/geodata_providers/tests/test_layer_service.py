from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Workspace
from tosca_api.apps.geodata_providers.services.commands.layer_service import LayerService


class LayerServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='layer-service-user', password='testpass123')
        self.engine = GeodataEngine.objects.create(
            name='Layer Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
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


class LayerApiServiceIntegrationTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='layer-api-user',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(self.user)
        self.engine = GeodataEngine.objects.create(
            name='Layer API Engine',
            description='test',
            engine_type='geoserver',
            base_url='http://example.com/geoserver',
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

    @patch('tosca_api.apps.geodata_providers.api.views.LayerService.update_published_metadata')
    def test_partial_update_uses_layer_service_for_published_metadata(self, mock_update_metadata):
        mock_update_metadata.return_value = {'success': True}

        response = self.client.patch(
            f'/api/geoengine/layers/{self.layer.pk}/',
            {'title': 'Updated Title'},
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        mock_update_metadata.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.api.views.LayerService.delete_layer_safe')
    def test_destroy_uses_layer_service(self, mock_delete_layer):
        mock_delete_layer.return_value = {'success': True, 'message': 'deleted'}

        response = self.client.delete(f'/api/geoengine/layers/{self.layer.pk}/')

        self.assertEqual(response.status_code, 200)
        mock_delete_layer.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.api.views.LayerService.unpublish_layer')
    def test_unpublish_uses_layer_service(self, mock_unpublish_layer):
        mock_unpublish_layer.return_value = {'success': True, 'message': 'unpublished'}

        response = self.client.post(f'/api/geoengine/layers/{self.layer.pk}/unpublish/')

        self.assertEqual(response.status_code, 200)
        mock_unpublish_layer.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.api.views.LayerService.publish_postgis')
    def test_publish_postgis_endpoint_uses_service(self, mock_publish_postgis):
        mock_publish_postgis.return_value = {
            'success': True,
            'created': True,
            'message': "Layer 'buildings' published in workspace 'demo_ws'.",
            'bbox': None,
            'resource': self.layer,
        }

        response = self.client.post(
            '/api/geoengine/layers/publish_postgis/',
            {
                'store_id': str(self.store.pk),
                'workspace_id': str(self.workspace.pk),
                'table_name': 'buildings',
                'layer_name': 'buildings',
                'geometry_column': 'geom',
                'geometry_type': 'Polygon',
                'srid': 4326,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        mock_publish_postgis.assert_called_once()
