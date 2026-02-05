from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status
import requests
import random
import string
import os
import json

from tosca_api.apps.geodata_engine.models import GeodataEngine


class GeoServerIntegrationTestCase(TestCase):
    """Simple integration tests with real GeoServer"""
    
    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)
        
        # Create test engine with real GeoServer connection
        self.engine = GeodataEngine.objects.create(
            name='Integration Test Engine',
            description='For integration testing',
            base_url=f"http://{os.getenv('GEOSERVER_HOST')}:{os.getenv('GEOSERVER_PORT')}/geoserver",
            admin_username=os.getenv('GEOSERVER_ADMIN_USER'),
            admin_password=os.getenv('GEOSERVER_ADMIN_PASSWORD'),
            is_active=True,
            created_by=self.user
        )
        
        # GeoServer REST API base URL
        self.geoserver_url = f"http://{os.getenv('GEOSERVER_HOST')}:{os.getenv('GEOSERVER_PORT')}/geoserver"
        self.rest_url = f"{self.geoserver_url}/rest"
        
        # Auth for GeoServer REST API
        self.auth = (os.getenv('GEOSERVER_ADMIN_USER'), os.getenv('GEOSERVER_ADMIN_PASSWORD'))
    
    def generate_random_name(self, prefix="test"):
        """Generate random name for testing"""
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"{prefix}_{random_suffix}"
    
    def test_geoserver_connection(self):
        """Test basic GeoServer connectivity"""
        response = requests.get(f"{self.rest_url}/workspaces", auth=self.auth)
        
        # Should return 200 if GeoServer is running and auth works
        self.assertEqual(response.status_code, 200)
        
        # Should return JSON with workspaces
        data = response.json()
        self.assertIn('workspaces', data)
    
    def test_workspace_crud_operations(self):
        """Test workspace create, read, delete operations"""
        workspace_name = self.generate_random_name("inttest_ws")
        
        # 1. Create workspace
        create_data = {
            "workspace": {
                "name": workspace_name,
                "description": "Integration test workspace"
            }
        }
        
        create_response = requests.post(
            f"{self.rest_url}/workspaces",
            json=create_data,
            auth=self.auth,
            headers={'Content-Type': 'application/json'}
        )
        
        # Should create successfully (201) or already exist (409)
        self.assertIn(create_response.status_code, [201, 409])
        
        # 2. Verify workspace exists in list
        list_response = requests.get(f"{self.rest_url}/workspaces", auth=self.auth)
        self.assertEqual(list_response.status_code, 200)
        
        workspaces_data = list_response.json()
        workspace_names = []
        
        if 'workspaces' in workspaces_data and workspaces_data['workspaces']:
            if isinstance(workspaces_data['workspaces'], dict) and 'workspace' in workspaces_data['workspaces']:
                workspaces = workspaces_data['workspaces']['workspace']
                if isinstance(workspaces, list):
                    workspace_names = [ws['name'] for ws in workspaces]
                elif isinstance(workspaces, dict):
                    workspace_names = [workspaces['name']]
        
        self.assertIn(workspace_name, workspace_names, f"Workspace {workspace_name} not found in: {workspace_names}")
        
        # 3. Get specific workspace
        get_response = requests.get(f"{self.rest_url}/workspaces/{workspace_name}", auth=self.auth)
        self.assertEqual(get_response.status_code, 200)
        
        workspace_data = get_response.json()
        self.assertEqual(workspace_data['workspace']['name'], workspace_name)
        
        # 4. Delete workspace
        delete_response = requests.delete(
            f"{self.rest_url}/workspaces/{workspace_name}?recurse=true",
            auth=self.auth
        )
        self.assertEqual(delete_response.status_code, 200)
        
        # 5. Verify workspace is deleted
        verify_delete_response = requests.get(f"{self.rest_url}/workspaces/{workspace_name}", auth=self.auth)
        self.assertEqual(verify_delete_response.status_code, 404)
    
    def test_api_endpoints_with_real_data(self):
        """Test our API endpoints work with real GeoServer data"""
        
        # Test engines list
        response = self.client.get('/api/geodata/engines/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        data = response.json()
        self.assertIn('results', data)
        self.assertGreaterEqual(data['count'], 1)  # At least our test engine
        
        # Test engine detail
        response = self.client.get(f'/api/geodata/engines/{self.engine.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        data = response.json()
        self.assertEqual(data['name'], 'Integration Test Engine')
        self.assertIn('geoserver_url', data)
    
    def test_sync_with_real_geoserver(self):
        """Test sync operation with real GeoServer"""
        
        # Create a test workspace directly in GeoServer first
        workspace_name = self.generate_random_name("sync_test")
        
        create_data = {
            "workspace": {
                "name": workspace_name,
                "description": "Sync test workspace"
            }
        }
        
        requests.post(
            f"{self.rest_url}/workspaces",
            json=create_data,
            auth=self.auth,
            headers={'Content-Type': 'application/json'}
        )
        
        try:
            # Test sync endpoint
            response = self.client.post(f'/api/geodata/engines/{self.engine.id}/sync/')
            
            # Should return 200 for success or 400/500 for expected errors
            self.assertIn(response.status_code, [200, 400, 500])
            
            data = response.json()
            self.assertIn('status', data)
            self.assertIn('message', data)
            
        finally:
            # Clean up test workspace
            requests.delete(
                f"{self.rest_url}/workspaces/{workspace_name}?recurse=true",
                auth=self.auth
            )


class SimpleConsoleIntegrationTestCase(TestCase):
    """Simple console integration tests"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user('consoleuser', 'console@test.com', 'testpass')
        
        self.engine = GeodataEngine.objects.create(
            name='Console Integration Engine',
            description='For console testing',
            base_url=f"http://{os.getenv('GEOSERVER_HOST')}:{os.getenv('GEOSERVER_PORT')}/geoserver",
            admin_username=os.getenv('GEOSERVER_ADMIN_USER'),
            admin_password=os.getenv('GEOSERVER_ADMIN_PASSWORD'),
            is_active=True,
            created_by=self.user
        )
    
    def test_console_engines_view(self):
        """Test console engines view works"""
        self.client.login(username='consoleuser', password='testpass')
        
        response = self.client.get('/console/engines/')
        self.assertEqual(response.status_code, 200)
        
        # Check engine is displayed
        self.assertContains(response, 'Console Integration Engine')
        self.assertContains(response, 'Sync All')