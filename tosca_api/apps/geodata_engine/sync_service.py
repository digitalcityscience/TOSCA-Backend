"""
GeoServer Synchronization Service
Ensures Django DB stays in sync with GeoServer (Single Source of Truth = GeoServer)
"""
import logging
from typing import Dict, List
from django.contrib.auth.models import User
from .models import GeodataEngine, Workspace, Store, Layer
from .geoserver.client import GeoServerClient

logger = logging.getLogger(__name__)


class GeoServerSyncService:
    """
    Service to sync Django DB with GeoServer state
    GeoServer is Single Source of Truth
    """
    
    def __init__(self, geodata_engine: GeodataEngine):
        self.engine = geodata_engine
        self.client = GeoServerClient(
            url=self.engine.geoserver_url,
            username=self.engine.admin_username,
            password=self.engine.decrypted_admin_password
        )
    
    def sync_all_resources(self, created_by: User) -> Dict:
        """
        Full sync: Pull all resources from GeoServer and update Django DB
        """
        logger.info(f"Starting full sync for engine: {self.engine.name}")
        
        results = {
            'workspaces': {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []},
            'stores': {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []},
            'layers': {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []},
            'success': True
        }
        
        try:
            # 1️⃣ Sync Workspaces
            workspace_results = self.sync_workspaces(created_by)
            results['workspaces'] = workspace_results
            
            # 2️⃣ Sync Stores (for each workspace)  
            store_results = self.sync_all_stores(created_by)
            results['stores'] = store_results
            
            # 3️⃣ Sync Layers (for each store)
            layer_results = self.sync_all_layers(created_by)
            results['layers'] = layer_results
            
            logger.info(f"Sync completed: {results}")
            return results
            
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            results['success'] = False
            results['error'] = str(e)
            return results
    
    def sync_workspaces(self, created_by: User) -> Dict:
        """Sync workspaces from GeoServer to Django - includes DELETE operations"""
        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}
        
        try:
            # 1️⃣ Get workspaces from GeoServer (Single Source of Truth)
            geoserver_workspaces = self._get_geoserver_workspaces()
            logger.info(f"GeoServer has {len(geoserver_workspaces)} workspaces: {geoserver_workspaces}")
            
            # 2️⃣ Get current Django workspaces for this engine
            django_workspaces = Workspace.objects.filter(geodata_engine=self.engine)
            django_workspace_names = set(ws.name for ws in django_workspaces)
            logger.info(f"Django has {len(django_workspace_names)} workspaces: {django_workspace_names}")
            
            # 3️⃣ CREATE/UPDATE: GeoServer workspaces that need to be in Django
            for ws_name in geoserver_workspaces:
                try:
                    workspace, created = Workspace.objects.get_or_create(
                        geodata_engine=self.engine,
                        name=ws_name,
                        defaults={
                            'description': f'Synced from GeoServer: {ws_name}',
                            'created_by': created_by
                        }
                    )
                    
                    if created:
                        results['created'] += 1
                        logger.info(f"✅ Created workspace: {ws_name}")
                    else:
                        results['synced'] += 1
                        logger.info(f"✅ Synced workspace: {ws_name}")
                        
                except Exception as e:
                    error_msg = f"Failed to sync workspace {ws_name}: {e}"
                    results['errors'].append(error_msg)
                    logger.error(error_msg)
            
            # 4️⃣ DELETE: Django workspaces that don't exist in GeoServer
            geoserver_workspace_names = set(geoserver_workspaces)
            workspaces_to_delete = django_workspace_names - geoserver_workspace_names
            
            if workspaces_to_delete:
                logger.info(f"🗑️ Deleting {len(workspaces_to_delete)} workspaces not in GeoServer: {workspaces_to_delete}")
                
                for ws_name in workspaces_to_delete:
                    try:
                        workspace = django_workspaces.get(name=ws_name)
                        workspace.delete()
                        results['deleted'] += 1
                        logger.info(f"🗑️ Deleted workspace: {ws_name}")
                    except Exception as e:
                        error_msg = f"Failed to delete workspace {ws_name}: {e}"
                        results['errors'].append(error_msg)
                        logger.error(error_msg)
            
            return results
            
        except Exception as e:
            results['errors'].append(f"Failed to sync workspaces: {e}")
            return results
    
    def sync_all_stores(self, created_by: User) -> Dict:
        """Sync all stores from GeoServer"""
        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}
        
        # Get all Django workspaces for this engine
        workspaces = Workspace.objects.filter(geodata_engine=self.engine)
        
        for workspace in workspaces:
            store_results = self.sync_stores_for_workspace(workspace, created_by)
            results['synced'] += store_results['synced']
            results['created'] += store_results['created']
            results['deleted'] += store_results['deleted']
            results['errors'].extend(store_results['errors'])
        
        return results
    
    def sync_stores_for_workspace(self, workspace: Workspace, created_by: User) -> Dict:
        """Sync stores for a specific workspace - includes DELETE operations"""
        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}
        
        try:
            # 1️⃣ Get datastores from GeoServer (Single Source of Truth)
            geoserver_stores = self._get_geoserver_stores(workspace.name)
            geoserver_store_names = set(store_data['name'] for store_data in geoserver_stores)
            logger.info(f"GeoServer workspace '{workspace.name}' has {len(geoserver_store_names)} stores: {geoserver_store_names}")
            
            # 2️⃣ Get current Django stores for this workspace
            django_stores = Store.objects.filter(workspace=workspace)
            django_store_names = set(store.name for store in django_stores)
            logger.info(f"Django workspace '{workspace.name}' has {len(django_store_names)} stores: {django_store_names}")
            
            # 3️⃣ CREATE/UPDATE: GeoServer stores that need to be in Django
            for store_data in geoserver_stores:
                try:
                    store_name = store_data['name']
                    
                    store, created = Store.objects.update_or_create(
                        workspace=workspace,
                        name=store_name,
                        defaults={
                            'geodata_engine': self.engine,
                            'description': f'Synced from GeoServer: {store_name}',
                            'host': store_data.get('host', 'localhost'),
                            'port': store_data.get('port', 5432),
                            'database': store_data.get('database', 'gis'),
                            'username': store_data.get('username', 'gis'),
                            'password': store_data.get('password', ''),
                            'schema': store_data.get('schema', 'public'),
                            'created_by': created_by
                        }
                    )
                    
                    if created:
                        results['created'] += 1
                        logger.info(f"✅ Created store: {workspace.name}/{store_name}")
                    else:
                        results['synced'] += 1
                        logger.info(f"✅ Synced store: {workspace.name}/{store_name}")
                        
                except Exception as e:
                    error_msg = f"Failed to sync store {store_data.get('name')}: {e}"
                    results['errors'].append(error_msg)
                    logger.error(error_msg)
            
            # 4️⃣ DELETE: Django stores that don't exist in GeoServer
            stores_to_delete = django_store_names - geoserver_store_names
            
            if stores_to_delete:
                logger.info(f"🗑️ Deleting {len(stores_to_delete)} stores not in GeoServer: {stores_to_delete}")
                
                for store_name in stores_to_delete:
                    try:
                        store = django_stores.get(name=store_name)
                        store.delete()
                        results['deleted'] += 1
                        logger.info(f"🗑️ Deleted store: {workspace.name}/{store_name}")
                    except Exception as e:
                        error_msg = f"Failed to delete store {store_name}: {e}"
                        results['errors'].append(error_msg)
                        logger.error(error_msg)
            
            return results
            
        except Exception as e:
            results['errors'].append(f"Failed to get stores for workspace {workspace.name}: {e}")
            return results
    
    def sync_all_layers(self, created_by: User) -> Dict:
        """Sync all layers from GeoServer"""
        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}
        
        # Get all Django workspaces for this engine
        workspaces = Workspace.objects.filter(geodata_engine=self.engine)
        
        for workspace in workspaces:
            layer_results = self.sync_layers_for_workspace(workspace, created_by)
            results['synced'] += layer_results['synced']
            results['created'] += layer_results['created']
            results['deleted'] += layer_results['deleted']
            results['errors'].extend(layer_results['errors'])
        
        return results
    
    def sync_layers_for_workspace(self, workspace: Workspace, created_by: User) -> Dict:
        """Sync layers for a specific workspace - includes DELETE operations"""
        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}
        
        try:
            # 1️⃣ Get layers from GeoServer (Single Source of Truth)
            geoserver_layers = self._get_geoserver_layers(workspace.name)
            geoserver_layer_names = set(layer_data['name'] for layer_data in geoserver_layers)
            logger.info(f"GeoServer workspace '{workspace.name}' has {len(geoserver_layer_names)} layers: {geoserver_layer_names}")
            
            # 2️⃣ Get current Django layers for this workspace
            django_layers = Layer.objects.filter(workspace=workspace)
            django_layer_names = set(layer.name for layer in django_layers)
            logger.info(f"Django workspace '{workspace.name}' has {len(django_layer_names)} layers: {django_layer_names}")
            
            # 3️⃣ CREATE/UPDATE: GeoServer layers that need to be in Django
            for layer_data in geoserver_layers:
                try:
                    layer_name = layer_data['name']
                    
                    # Find associated store
                    store_name = layer_data.get('store_name', '')
                    try:
                        store = Store.objects.get(workspace=workspace, name=store_name)
                    except Store.DoesNotExist:
                        # Create placeholder store if not exists
                        store = Store.objects.create(
                            geodata_engine=self.engine,
                            workspace=workspace,
                            name=store_name,
                            description=f'Auto-created for layer {layer_name}',
                            host='localhost',
                            port=5432,
                            database='gis',
                            username='gis',
                            password='',
                            schema='public',
                            created_by=created_by
                        )
                    
                    layer, created = Layer.objects.update_or_create(
                        workspace=workspace,
                        store=store,
                        name=layer_name,
                        defaults={
                            'title': layer_data.get('title', layer_name),
                            'description': f'Synced from GeoServer: {layer_name}',
                            'table_name': layer_data.get('table_name', layer_name),
                            'geometry_column': layer_data.get('geometry_column', 'geom'),
                            'geometry_type': layer_data.get('geometry_type', 'Point'),
                            'srid': layer_data.get('srid', 4326),
                            'publishing_state': 'published',
                            'created_by': created_by
                        }
                    )
                    
                    if created:
                        results['created'] += 1
                        logger.info(f"✅ Created layer: {workspace.name}/{layer_name}")
                    else:
                        results['synced'] += 1
                        logger.info(f"✅ Synced layer: {workspace.name}/{layer_name}")
                        
                except Exception as e:
                    error_msg = f"Failed to sync layer {layer_data.get('name')}: {e}"
                    results['errors'].append(error_msg)
                    logger.error(error_msg)
            
            # 4️⃣ DELETE: Django layers that don't exist in GeoServer
            layers_to_delete = django_layer_names - geoserver_layer_names
            
            if layers_to_delete:
                logger.info(f"🗑️ Deleting {len(layers_to_delete)} layers not in GeoServer: {layers_to_delete}")
                
                for layer_name in layers_to_delete:
                    try:
                        layer = django_layers.get(name=layer_name)
                        layer.delete()
                        results['deleted'] += 1
                        logger.info(f"🗑️ Deleted layer: {workspace.name}/{layer_name}")
                    except Exception as e:
                        error_msg = f"Failed to delete layer {layer_name}: {e}"
                        results['errors'].append(error_msg)
                        logger.error(error_msg)
            
            return results
            
        except Exception as e:
            results['errors'].append(f"Failed to get layers for workspace {workspace.name}: {e}")
            return results
    
    def _get_geoserver_workspaces(self) -> List[str]:
        """Get workspace names from GeoServer"""
        try:
            return self.client.get_workspaces()
        except Exception as e:
            logger.error(f"Failed to get workspaces from GeoServer: {e}")
            return []
    
    def _get_geoserver_stores(self, workspace: str) -> List[Dict]:
        """Get datastore info from GeoServer"""
        try:
            return self.client.get_datastores(workspace)
        except Exception as e:
            logger.error(f"Failed to get stores from workspace {workspace}: {e}")
            return []
    
    def _get_geoserver_layers(self, workspace: str) -> List[Dict]:
        """Get layer info from GeoServer"""
        try:
            return self.client.get_layers(workspace)
        except Exception as e:
            logger.error(f"Failed to get layers from workspace {workspace}: {e}")
            return []