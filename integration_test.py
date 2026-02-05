#!/usr/bin/env python
"""
Simple GeoServer integration test script 
Run directly without Django test framework
"""

import os
import sys
import requests
import random
import string
import json

# Add project to path
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tosca_api.settings.development')

import django
django.setup()

def generate_random_name(prefix="test"):
    """Generate random name for testing"""
    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{prefix}_{random_suffix}"

def test_geoserver_connection():
    """Test basic GeoServer connectivity"""
    print("🔍 Testing GeoServer connection...")
    
    geoserver_host = os.getenv('GEOSERVER_HOST', 'geoserver')
    geoserver_port = os.getenv('GEOSERVER_PORT', '8080')
    admin_user = os.getenv('GEOSERVER_ADMIN_USER', 'admin')
    admin_pass = os.getenv('GEOSERVER_ADMIN_PASSWORD', 'admin')
    
    rest_url = f"http://{geoserver_host}:{geoserver_port}/geoserver/rest"
    auth = (admin_user, admin_pass)
    
    try:
        response = requests.get(f"{rest_url}/workspaces", auth=auth, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ GeoServer connection successful!")
            print(f"   URL: {rest_url}")
            print(f"   Response: {response.status_code}")
            
            # Count workspaces
            workspace_count = 0
            if 'workspaces' in data and data['workspaces']:
                if isinstance(data['workspaces'], dict) and 'workspace' in data['workspaces']:
                    workspaces = data['workspaces']['workspace']
                    if isinstance(workspaces, list):
                        workspace_count = len(workspaces)
                    elif isinstance(workspaces, dict):
                        workspace_count = 1
            
            print(f"   Existing workspaces: {workspace_count}")
            return True, rest_url, auth
            
        else:
            print(f"❌ GeoServer connection failed!")
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False, None, None
            
    except requests.exceptions.RequestException as e:
        print(f"❌ GeoServer connection error: {e}")
        return False, None, None

def test_workspace_crud(rest_url, auth):
    """Test workspace create, read, delete operations"""
    print("\n🏗️  Testing workspace CRUD operations...")
    
    workspace_name = generate_random_name("testws")
    print(f"   Using workspace name: {workspace_name}")
    
    try:
        # 1. Create workspace
        print("   📝 Creating workspace...")
        create_data = {
            "workspace": {
                "name": workspace_name,
                "description": f"Integration test workspace - {workspace_name}"
            }
        }
        
        create_response = requests.post(
            f"{rest_url}/workspaces",
            json=create_data,
            auth=auth,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        
        if create_response.status_code in [201, 409]:
            print(f"   ✅ Workspace created (status: {create_response.status_code})")
        else:
            print(f"   ❌ Failed to create workspace: {create_response.status_code}")
            print(f"      Response: {create_response.text}")
            return False
        
        # 2. Verify workspace exists
        print("   🔍 Checking if workspace exists...")
        list_response = requests.get(f"{rest_url}/workspaces", auth=auth, timeout=10)
        
        if list_response.status_code == 200:
            workspaces_data = list_response.json()
            workspace_names = []
            
            if 'workspaces' in workspaces_data and workspaces_data['workspaces']:
                if isinstance(workspaces_data['workspaces'], dict) and 'workspace' in workspaces_data['workspaces']:
                    workspaces = workspaces_data['workspaces']['workspace']
                    if isinstance(workspaces, list):
                        workspace_names = [ws['name'] for ws in workspaces]
                    elif isinstance(workspaces, dict):
                        workspace_names = [workspaces['name']]
            
            if workspace_name in workspace_names:
                print(f"   ✅ Workspace found in list!")
            else:
                print(f"   ❌ Workspace NOT found in list!")
                print(f"      Available: {workspace_names[:5]}...")  # Show first 5
                return False
        else:
            print(f"   ❌ Failed to list workspaces: {list_response.status_code}")
            return False
        
        # 3. Get specific workspace
        print("   📖 Getting workspace details...")
        get_response = requests.get(f"{rest_url}/workspaces/{workspace_name}", auth=auth, timeout=10)
        
        if get_response.status_code == 200:
            workspace_data = get_response.json()
            if workspace_data['workspace']['name'] == workspace_name:
                print(f"   ✅ Workspace details retrieved successfully!")
            else:
                print(f"   ❌ Workspace name mismatch!")
                return False
        else:
            print(f"   ❌ Failed to get workspace: {get_response.status_code}")
            return False
        
        # 4. Delete workspace
        print("   🗑️  Deleting workspace...")
        delete_response = requests.delete(
            f"{rest_url}/workspaces/{workspace_name}?recurse=true",
            auth=auth,
            timeout=10
        )
        
        if delete_response.status_code == 200:
            print(f"   ✅ Workspace deleted successfully!")
        else:
            print(f"   ❌ Failed to delete workspace: {delete_response.status_code}")
            return False
        
        # 5. Verify deletion
        print("   ✓ Verifying deletion...")
        verify_response = requests.get(f"{rest_url}/workspaces/{workspace_name}", auth=auth, timeout=10)
        
        if verify_response.status_code == 404:
            print(f"   ✅ Workspace successfully deleted!")
            return True
        else:
            print(f"   ❌ Workspace still exists after deletion!")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"   ❌ Request error: {e}")
        return False

def test_django_api_endpoints():
    """Test Django API endpoints"""
    print("\n🌐 Testing Django API endpoints...")
    
    try:
        from rest_framework.test import APIClient
        from django.contrib.auth.models import User
        from tosca_api.apps.geodata_engine.models import GeodataEngine
        from django.test import override_settings
        
        # Override ALLOWED_HOSTS for test client
        with override_settings(ALLOWED_HOSTS=['*']):
            # Create test client and user
            client = APIClient()
# Override ALLOWED_HOSTS for test client
        with override_settings(ALLOWED_HOSTS=['*']):
            # Create test client and user
            client = APIClient()
            user, created = User.objects.get_or_create(
            username='integration_test_user',
            defaults={
                'email': 'inttest@example.com',
                'is_active': True
            }
        )
        if created:
            user.set_password('testpass123')
            user.save()
        
        client.force_authenticate(user=user)
        
        # Test engines list endpoint
        print("   📋 Testing engines list API...")
        response = client.get('/api/geodata/engines/')
        
        if response.status_code == 200:
            data = response.json()
            engine_count = data.get('count', 0)
            print(f"   ✅ Engines list API works! Found {engine_count} engines")
            
            if engine_count > 0:
                # Test first engine detail
                first_engine_id = data['results'][0]['id']
                print(f"   📖 Testing engine detail API for ID: {first_engine_id}")
                
                detail_response = client.get(f'/api/geodata/engines/{first_engine_id}/')
                if detail_response.status_code == 200:
                    print(f"   ✅ Engine detail API works!")
                    return True
                else:
                    print(f"   ❌ Engine detail API failed: {detail_response.status_code}")
                    return False
            else:
                print("   ℹ️  No engines found, creating test engine...")
                
                # Create a test engine
                engine = GeodataEngine.objects.create(
                    name='Integration Test Engine',
                    description='Created by integration test',
                    base_url=f"http://{os.getenv('GEOSERVER_HOST')}:{os.getenv('GEOSERVER_PORT')}/geoserver",
                    admin_username=os.getenv('GEOSERVER_ADMIN_USER'),
                    admin_password=os.getenv('GEOSERVER_ADMIN_PASSWORD'),
                    is_active=True,
                    created_by=user
                )
                
                print(f"   ✅ Created test engine: {engine.name}")
                return True
                
        else:
            print(f"   ❌ Engines list API failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"   ❌ Django API test error: {e}")
        return False

def main():
    """Run all integration tests"""
    print("🚀 Starting GeoServer Integration Tests")
    print("=" * 50)
    
    # Test 1: GeoServer Connection
    success, rest_url, auth = test_geoserver_connection()
    if not success:
        print("\n❌ GeoServer connection failed. Stopping tests.")
        return False
    
    # Test 2: Workspace CRUD
    success = test_workspace_crud(rest_url, auth)
    if not success:
        print("\n❌ Workspace CRUD tests failed.")
        return False
    
    # Test 3: Django API
    success = test_django_api_endpoints()
    if not success:
        print("\n❌ Django API tests failed.")
        return False
    
    print("\n🎉 All integration tests passed!")
    print("=" * 50)
    return True

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)