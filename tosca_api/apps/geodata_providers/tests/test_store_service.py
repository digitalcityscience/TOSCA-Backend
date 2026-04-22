from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Workspace
from tosca_api.apps.geodata_providers.services.commands.store_service import StoreService


class StoreServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='store-service-user', password='testpass123')
        self.engine = GeodataEngine.objects.create(
            name='Store Engine',
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
            geodata_engine=self.engine,
            name='source_store',
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

    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_create_postgis_store_persists_after_remote_create(self, mock_create_client):
        client = mock_create_client.return_value
        client.create_postgis_store.return_value = {'success': True, 'verified': True, 'message': 'created'}

        result = StoreService.create_postgis_store(
            workspace=self.workspace,
            name='new_store',
            user=self.user,
            store_type='postgis',
            description='desc',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
        )

        self.assertTrue(result['success'])
        self.assertTrue(result['created'])
        self.assertTrue(Store.objects.filter(workspace=self.workspace, name='new_store').exists())
        client.create_postgis_store.assert_called_once_with(
            name='new_store',
            workspace='demo_ws',
            host='db',
            port=5432,
            database='gis',
            username='postgres',
            password='secret',
            schema='public',
        )

    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_sync_service')
    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_clone_store_returns_sync_result(self, mock_create_client, mock_create_sync_service):
        mock_create_client.return_value.create_postgis_store.return_value = {
            'success': True,
            'verified': True,
            'message': 'created',
        }
        mock_create_sync_service.return_value.sync_stores_for_workspace.return_value = {
            'success': True,
            'created': 1,
            'errors': [],
        }

        result = StoreService.clone_store(
            source_store=self.store,
            target_workspace=self.workspace,
            name='cloned_store',
            user=self.user,
            description='clone',
            password='secret',
        )

        self.assertTrue(result['success'])
        self.assertEqual(result['sync_result']['created'], 1)
        self.assertTrue(Store.objects.filter(workspace=self.workspace, name='cloned_store').exists())

    def test_delete_store_safe_blocks_when_layers_exist(self):
        Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='dependent_layer',
            title='Dependent Layer',
            description='desc',
            table_name='dependent_layer',
            geometry_column='geom',
            geometry_type='Point',
            srid=4326,
            created_by=self.user,
        )

        result = StoreService.delete_store_safe(self.store)

        self.assertFalse(result['success'])
        self.assertTrue(result['blocked'])
        self.assertIn('layers=1', result['message'])
        self.assertTrue(Store.objects.filter(pk=self.store.pk).exists())

    @patch('tosca_api.apps.geodata_providers.services.commands.store_service.EngineClientFactory.create_client')
    def test_delete_store_safe_deletes_after_remote_delete(self, mock_create_client):
        mock_create_client.return_value.delete_store.return_value = {
            'success': True,
            'verified': True,
            'already_deleted': False,
            'message': 'deleted',
        }

        result = StoreService.delete_store_safe(self.store)

        self.assertTrue(result['success'])
        self.assertFalse(Store.objects.filter(pk=self.store.pk).exists())


class StoreApiServiceIntegrationTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='store-api-user',
            password='testpass123',
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(self.user)
        self.engine = GeodataEngine.objects.create(
            name='Store API Engine',
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
            geodata_engine=self.engine,
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

    @patch('tosca_api.apps.geodata_providers.api.views.StoreService.create_postgis_store')
    def test_create_endpoint_uses_store_service(self, mock_create_store):
        mocked_store = Store(
            workspace=self.workspace,
            geodata_engine=self.engine,
            name='posted_store',
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
        mock_create_store.return_value = {
            'success': True,
            'created': True,
            'message': 'created',
            'resource': mocked_store,
        }

        response = self.client.post(
            '/api/geoengine/stores/',
            {
                'workspace': str(self.workspace.pk),
                'name': 'posted_store',
                'store_type': 'postgis',
                'host': 'db',
                'port': 5432,
                'database': 'gis',
                'username': 'postgres',
                'password': 'secret',
                'schema': 'public',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201)
        mock_create_store.assert_called_once()

    @patch('tosca_api.apps.geodata_providers.api.views.StoreService.delete_store_safe')
    def test_destroy_endpoint_uses_store_service(self, mock_delete_store):
        mock_delete_store.return_value = {'success': True, 'message': 'deleted'}

        response = self.client.delete(f'/api/geoengine/stores/{self.store.pk}/')

        self.assertEqual(response.status_code, 200)
        mock_delete_store.assert_called_once_with(self.store)
