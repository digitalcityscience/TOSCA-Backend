from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.conf import settings
from unittest.mock import patch, MagicMock
import json
import os

from tosca_api.apps.geodata_engine.models import GeodataEngine, Workspace, Store, Layer


@patch('tosca_api.apps.geodata_engine.models.Workspace.create_in_geoserver')
@patch('tosca_api.apps.geodata_engine.models.Store.create_in_geoserver')
@patch('tosca_api.apps.geodata_engine.models.Layer.create_in_geoserver')
class GeodataEngineAPITestCase(TestCase):
    """Test API endpoints for geodata engine management"""
    
    def setUp(self, mock_layer_create=None, mock_store_create=None, mock_workspace_create=None):
        """Set up test data"""
        # Mock GeoServer calls during object creation
        if mock_workspace_create:
            mock_workspace_create.return_value = {'success': True, 'created': True}
        if mock_store_create:
            mock_store_create.return_value = {'success': True, 'created': True}
        if mock_layer_create:
            mock_layer_create.return_value = {'success': True, 'created': True}
            
        self.client = APIClient()
        
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Authenticate the client
        self.client.force_authenticate(user=self.user)
        
        # Create test geodata engine  
        self.engine = GeodataEngine.objects.create(
            name='Test Engine',
            description='Test description',
            base_url=f"http://{os.getenv('GEOSERVER_HOST')}:{os.getenv('GEOSERVER_PORT')}/geoserver",
            admin_username=os.getenv('GEOSERVER_ADMIN_USER'),
            admin_password=os.getenv('GEOSERVER_ADMIN_PASSWORD'),
            is_active=True,
            created_by=self.user
        )
        
        # Create test workspace
        self.workspace = Workspace.objects.create(
            geodata_engine=self.engine,
            name='test_workspace',
            description='Test workspace',
            created_by=self.user
        )
        
        # Create test store
        self.store = Store.objects.create(
            workspace=self.workspace,
            name='test_store',
            description='Test store',

            host=os.getenv('PG_HOST'),  # 'db' for container
            port=int(os.getenv('PG_DOCKER_PORT')),  # 5432 internal port
            database=os.getenv('PG_DATABASE'),
            username=os.getenv('PG_SUPERUSER'),
            password=os.getenv('PG_SUPERPASS'),
            schema='public',
            created_by=self.user
        )
        
        # Create test layer
        self.layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name='test_layer',
            title='Test Layer',
            description='Test layer',
            table_name='test_table',
            geometry_type='Point',
            created_by=self.user
        )
    
    def test_engines_list_api(self, mock_layer_create, mock_store_create, mock_workspace_create):
        """Test GET /api/geodata/engines/"""
        url = '/api/geodata/engines/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        # Check paginated response structure
        self.assertIn('results', data)
        self.assertIn('count', data)
        self.assertEqual(data['count'], 1)
        
        # Check engine data
        engine_data = data['results'][0]
        self.assertEqual(engine_data['name'], 'Test Engine')
        self.assertEqual(engine_data['geoserver_url'], f"http://{os.getenv('GEOSERVER_HOST')}:{os.getenv('GEOSERVER_PORT')}/geoserver")
        self.assertTrue(engine_data['is_active'])
    
    def test_engines_detail_api(self, mock_layer_create, mock_store_create, mock_workspace_create):
        """Test GET /api/geodata/engines/{id}/"""
        url = f'/api/geodata/engines/{self.engine.id}/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertEqual(data['name'], 'Test Engine')
        self.assertEqual(data['geoserver_url'], f"http://{os.getenv('GEOSERVER_HOST')}:{os.getenv('GEOSERVER_PORT')}/geoserver")
        self.assertTrue(data['is_active'])
    
    def test_workspaces_list_api(self, mock_layer_create, mock_store_create, mock_workspace_create):
        """Test GET /api/geodata/workspaces/"""
        url = '/api/geodata/workspaces/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertIn('results', data)
        self.assertEqual(data['count'], 1)
        
        workspace_data = data['results'][0]
        self.assertEqual(workspace_data['name'], 'test_workspace')
    
    def test_stores_list_api(self, mock_layer_create, mock_store_create, mock_workspace_create):
        """Test GET /api/geodata/stores/"""
        url = '/api/geodata/stores/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertIn('results', data)
        self.assertEqual(data['count'], 1)
        
        store_data = data['results'][0]
        self.assertEqual(store_data['name'], 'test_store')
        self.assertEqual(store_data['host'], os.getenv('PG_HOST'))
    
    def test_layers_list_api(self, mock_layer_create, mock_store_create, mock_workspace_create):
        """Test GET /api/geodata/layers/"""
        url = '/api/geodata/layers/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertIn('results', data)
        self.assertEqual(data['count'], 1)
        
        layer_data = data['results'][0]
        self.assertEqual(layer_data['name'], 'test_layer')
        self.assertEqual(layer_data['geometry_type'], 'Point')
    
    @patch('tosca_api.apps.geodata_engine.api.views.GeoServerSyncService')
    def test_engine_sync_endpoint_success(self, mock_sync_service, mock_layer_create, mock_store_create, mock_workspace_create):
        """Test POST /api/geodata/engines/{id}/sync/ - successful sync"""
        # Mock successful sync response
        mock_sync_instance = MagicMock()
        mock_sync_instance.sync_all_resources.return_value = {
            'success': True,
            'workspaces_synced': 1,
            'stores_synced': 1,
            'layers_synced': 1
        }
        mock_sync_service.return_value = mock_sync_instance
        
        url = f'/api/geodata/engines/{self.engine.id}/sync/'
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertEqual(data['status'], 'success')
        self.assertIn('message', data)
        self.assertIn('results', data)
        
        # Verify sync service was called
        mock_sync_service.assert_called_once_with(self.engine)
        mock_sync_instance.sync_all_resources.assert_called_once_with(self.user)
    
    @patch('tosca_api.apps.geodata_engine.api.views.GeoServerSyncService')
    def test_engine_sync_endpoint_failure(self, mock_sync_service, mock_layer_create, mock_store_create, mock_workspace_create):
        """Test POST /api/geodata/engines/{id}/sync/ - failed sync"""
        # Mock failed sync response
        mock_sync_instance = MagicMock()
        mock_sync_instance.sync_all_resources.return_value = {
            'success': False,
            'error': 'Connection failed',
            'details': 'Could not connect to GeoServer'
        }
        mock_sync_service.return_value = mock_sync_instance
        
        url = f'/api/geodata/engines/{self.engine.id}/sync/'
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        data = response.json()
        
        self.assertEqual(data['status'], 'error')
        self.assertIn('message', data)
        self.assertIn('results', data)
    
    @patch('tosca_api.apps.geodata_engine.api.views.GeoServerSyncService')
    def test_engine_sync_endpoint_exception(self, mock_sync_service, mock_layer_create, mock_store_create, mock_workspace_create):
        """Test POST /api/geodata/engines/{id}/sync/ - exception handling"""
        # Mock service raising exception
        mock_sync_service.side_effect = Exception('Connection error')
        
        url = f'/api/geodata/engines/{self.engine.id}/sync/'
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        data = response.json()
        
        self.assertEqual(data['status'], 'error')
        self.assertIn('message', data)
    
    @patch('tosca_api.apps.geodata_engine.api.views.GeoServerSyncService')
    def test_engines_sync_all_endpoint(self, mock_sync_service, mock_layer_create, mock_store_create, mock_workspace_create):
        """Test POST /api/geodata/engines/sync_all/"""
        # Create additional test engine
        engine2 = GeodataEngine.objects.create(
            name='Test Engine 2',
            description='Second test engine',
            base_url=f"http://{os.getenv('GEOSERVER_HOST')}:8081/geoserver",
            admin_username=os.getenv('GEOSERVER_ADMIN_USER'),
            admin_password=os.getenv('GEOSERVER_ADMIN_PASSWORD'),
            is_active=True,
            created_by=self.user
        )
        
        # Mock sync service responses
        mock_sync_instance = MagicMock()
        mock_sync_instance.sync_all_resources.return_value = {
            'success': True,
            'workspaces_synced': 1,
            'stores_synced': 1,
            'layers_synced': 1
        }
        mock_sync_service.return_value = mock_sync_instance
        
        url = '/api/geodata/engines/sync_all/'
        response = self.client.post(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertIn('message', data)
        self.assertIn('results', data)
        self.assertEqual(len(data['results']), 2)  # Two engines synced
        
        # Verify sync service was called twice (once for each engine)
        self.assertEqual(mock_sync_service.call_count, 2)
    
    def test_api_pagination(self, mock_layer_create, mock_store_create, mock_workspace_create):
        """Test API pagination with multiple engines"""
        # Get initial count
        initial_count = GeodataEngine.objects.count()
        
        # Create additional engines
        for i in range(5):
            GeodataEngine.objects.create(
                name=f'Engine {i+2}',
                description=f'Test engine {i+2}',
                base_url=f"http://{os.getenv('GEOSERVER_HOST')}:808{i+1}/geoserver",
                admin_username=os.getenv('GEOSERVER_ADMIN_USER'),
                admin_password=os.getenv('GEOSERVER_ADMIN_PASSWORD'),
                is_active=True,
                created_by=self.user
            )
        
        url = '/api/geodata/engines/?page_size=3'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertEqual(len(data['results']), 3)
        self.assertEqual(data['count'], initial_count + 5)  # Total engines
        self.assertIsNotNone(data.get('next'))  # Should have next page
    
    def test_api_filtering(self, mock_layer_create, mock_store_create, mock_workspace_create):
        """Test API filtering by active status"""
        # Count active engines before adding inactive one
        active_engines_before = GeodataEngine.objects.filter(is_active=True).count()
        
        # Create inactive engine
        GeodataEngine.objects.create(
            name='Inactive Engine',
            description='Inactive test engine',
            base_url=f"http://{os.getenv('GEOSERVER_HOST')}:8082/geoserver",
            admin_username=os.getenv('GEOSERVER_ADMIN_USER'),
            admin_password=os.getenv('GEOSERVER_ADMIN_PASSWORD'),
            is_active=False,
            created_by=self.user
        )
        
        # Test active engines only
        url = '/api/geodata/engines/?is_active=true'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        
        self.assertEqual(data['count'], active_engines_before)  # Only active engines
        
        # Test all engines
        url = '/api/geodata/engines/'
        response = self.client.get(url)
        data = response.json()
        
        self.assertEqual(data['count'], active_engines_before + 1)  # Both active and inactive


class ConsoleAPIIntegrationTestCase(TestCase):
    """Test console app integration with API"""
    
    def setUp(self):
        """Set up test data"""
        self.client = Client()  # Django test client
        
        # Create test user for engine creation
        self.user = User.objects.create_user('consoleuser', 'console@test.com', 'testpass')
        
        # Create test geodata engine
        self.engine = GeodataEngine.objects.create(
            name='Console Test Engine',
            description='Test engine for console integration',
            base_url=f"http://{os.getenv('GEOSERVER_HOST')}:{os.getenv('GEOSERVER_PORT')}/geoserver",
            admin_username=os.getenv('GEOSERVER_ADMIN_USER'),
            admin_password=os.getenv('GEOSERVER_ADMIN_PASSWORD'),
            is_active=True,
            created_by=self.user
        )
    
    def test_console_engines_view_uses_api(self):
        """Test that console engines view successfully calls API"""
        # Login the user for console view access
        self.client.login(username='consoleuser', password='testpass')
        
        url = '/console/engines/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        
        # Check that engine data is present in context
        self.assertContains(response, 'Console Test Engine')
        self.assertContains(response, f"http://{os.getenv('GEOSERVER_HOST')}:{os.getenv('GEOSERVER_PORT')}/geoserver")
    
    def test_console_sync_functionality(self):
        """Test console sync button functionality"""
        # Login the user for console view access
        self.client.login(username='consoleuser', password='testpass')
        
        # This would test the sync POST endpoint if implemented
        # For now, just test that the sync view exists
        url = '/console/engines/'
        response = self.client.get(url)
        
        # Check that sync button is present
        self.assertContains(response, 'Sync All')
        self.assertContains(response, 'sync-btn')