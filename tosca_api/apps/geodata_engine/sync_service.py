"""
GeoServer Synchronization Service
Ensures Django DB stays in sync with GeoServer (Single Source of Truth = GeoServer)
"""
import logging
from typing import Dict, List
from django.contrib.auth.models import User
from .models import GeodataEngine, Workspace, Store, Layer
from .geoserver.client import GeoServerClient
from .exceptions import GeoServerConnectionError

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
        Full sync: Pull all resources from GeoServer and update Django DB.
        Always runs an integrity cleanup first to strip legacy 'workspace:name'
        prefixes from Layer records created before the client.py patch.
        """
        logger.info(f"Starting full sync for engine: {self.engine.name}")

        # ── Integrity cleanup: strip ':' prefix from any corrupted Layer names ──
        corrupt_layers = Layer.objects.filter(
            workspace__geodata_engine=self.engine, name__contains=':'
        )
        if corrupt_layers.exists():
            logger.warning(
                f"Integrity check: {corrupt_layers.count()} Layer record(s) with ':' prefix "
                f"found for engine '{self.engine.name}' — cleaning up before sync."
            )
            for layer in corrupt_layers:
                clean_name = layer.name.split(':', 1)[1]
                if Layer.objects.filter(workspace=layer.workspace, name=clean_name).exists():
                    logger.warning(
                        f"Duplicate '{clean_name}' already exists in workspace "
                        f"'{layer.workspace.name}' — deleting corrupt record '{layer.name}'."
                    )
                    layer.delete()
                else:
                    old_name = layer.name
                    layer.name = clean_name
                    layer.save(update_fields=['name'])
                    logger.info(f"Renamed Layer '{old_name}' → '{clean_name}'")
        # ─────────────────────────────────────────────────────────────────────────

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

        except GeoServerConnectionError:
            raise
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
                    store_type = store_data.get('store_type', 'postgis')

                    # For postgis stores, skip if GeoServer didn't return
                    # the essential connection details (host/database/username).
                    # This prevents ValidationError from the Store model.
                    if store_type == 'postgis' and not all([
                        store_data.get('host'),
                        store_data.get('database'),
                        store_data.get('username'),
                    ]):
                        logger.warning(
                            f"Skipping store '{store_name}' in workspace '{workspace.name}' "
                            f"— incomplete connection details returned from GeoServer "
                            f"(host={store_data.get('host')!r}, "
                            f"database={store_data.get('database')!r}, "
                            f"username={store_data.get('username')!r})."
                        )
                        continue

                    store, created = Store.objects.update_or_create(
                        workspace=workspace,
                        name=store_name,
                        defaults={
                            'geodata_engine': self.engine,
                            'store_type': store_type,
                            'description': f'Synced from GeoServer: {store_name}',
                            'host': store_data.get('host', ''),
                            'port': store_data.get('port', 5432),
                            'database': store_data.get('database', ''),
                            'username': store_data.get('username', ''),
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

        except GeoServerConnectionError:
            raise
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

                    # Resolve the store this layer belongs to.
                    # get_layers() now returns store_name for every layer.
                    store_name = layer_data.get('store_name', '')
                    store = None
                    if store_name:
                        try:
                            store = Store.objects.get(workspace=workspace, name=store_name)
                        except Store.DoesNotExist:
                            logger.warning(
                                f"Layer '{layer_name}': store '{store_name}' not in Django yet. "
                                f"Skipping — run sync again after stores are populated."
                            )
                            continue

                    if store is None:
                        logger.warning(
                            f"Layer '{layer_name}' in workspace '{workspace.name}' has no "
                            f"store_name — cannot associate with a Store, skipping."
                        )
                        continue
                    
                    layer, created = Layer.objects.update_or_create(
                        workspace=workspace,
                        name=layer_name,
                        defaults={
                            'store': store,
                            'title': layer_data.get('title', layer_name),
                            'description': f'Synced from GeoServer: {layer_name}',
                            'table_name': layer_data.get('table_name', layer_name),
                            'geometry_column': layer_data.get('geometry_column', 'geom'),
                            'geometry_type': layer_data.get('geometry_type', 'Point'),
                            'srid': layer_data.get('srid', 4326),
                            'publishing_state': 'PUBLISHED',
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

        except GeoServerConnectionError:
            raise
        except Exception as e:
            results['errors'].append(f"Failed to get layers for workspace {workspace.name}: {e}")
            return results

    def _get_geoserver_workspaces(self) -> List[str]:
        """
        Get workspace names from GeoServer.
        Raises GeoServerConnectionError if GeoServer is unreachable — callers must NOT swallow this.
        """
        return self.client.get_workspaces()

    def _get_geoserver_stores(self, workspace: str) -> List[Dict]:
        """
        Get datastore info from GeoServer.
        Raises GeoServerConnectionError if GeoServer is unreachable.
        """
        return self.client.get_datastores(workspace)

    def _get_geoserver_layers(self, workspace: str) -> List[Dict]:
        """
        Get layer info from GeoServer.
        Raises GeoServerConnectionError if GeoServer is unreachable.
        """
        return self.client.get_layers(workspace)

    # ------------------------------------------------------------------
    # Push sync — Django intent → GeoServer
    # ------------------------------------------------------------------

    def push_workspace(self, workspace: Workspace) -> Dict:
        """
        Push a single Django workspace to GeoServer.
        Pattern: check exists → create if missing → verify → return result.
        Does NOT modify Django state — GeoServer is the destination here.
        """
        result = {'success': False, 'workspace': workspace.name}

        try:
            # 1. Check if already exists in GeoServer
            existing = self.client.get_workspaces()
            if workspace.name in existing:
                logger.info(f"Push workspace '{workspace.name}': already exists in GeoServer.")
                result.update({'success': True, 'action': 'already_exists'})
                return result

            # 2. Create in GeoServer
            create_result = self.client.create_workspace(workspace.name)
            if not create_result.get('success', False):
                error = create_result.get('error', create_result.get('message', 'Unknown error'))
                logger.error(f"Push workspace '{workspace.name}' create failed: {error}")
                result['error'] = error
                return result

            # 3. Verify: confirm workspace is now present
            workspaces_after = self.client.get_workspaces()
            if workspace.name not in workspaces_after:
                error = f"Workspace '{workspace.name}' not found in GeoServer after create — possible partial failure."
                logger.error(error)
                result['error'] = error
                return result

            logger.info(f"Push workspace '{workspace.name}': created and verified in GeoServer.")
            result.update({'success': True, 'action': 'created'})
            return result

        except Exception as e:
            logger.error(f"Push workspace '{workspace.name}' unexpected error: {e}")
            result['error'] = str(e)
            return result

    def push_all_workspaces(self, created_by: User) -> Dict:
        """
        Push all Django workspaces for this engine to GeoServer.
        Returns aggregate results.
        """
        results = {'pushed': 0, 'already_exists': 0, 'errors': [], 'success': True}
        workspaces = Workspace.objects.filter(geodata_engine=self.engine)

        for workspace in workspaces:
            r = self.push_workspace(workspace)
            if r.get('success'):
                if r.get('action') == 'already_exists':
                    results['already_exists'] += 1
                else:
                    results['pushed'] += 1
            else:
                results['errors'].append({'workspace': workspace.name, 'error': r.get('error', '')})

        if results['errors']:
            results['success'] = False
        return results

    def delete_workspace_safe(self, workspace: Workspace) -> Dict:
        """
        Safe delete: engine FIRST → verify deletion → THEN delete Django object.
        Never deletes Django if engine operation fails.

        Returns:
            {'success': True, 'deleted': 'both'}   — removed from engine + Django
            {'success': True, 'deleted': 'django_only'}  — no engine attached, only Django
            {'success': False, 'error': '...'}     — engine delete failed, Django untouched
        """
        result = {'success': False, 'workspace': workspace.name}

        if not workspace.geodata_engine:
            # No engine attached — safe to remove only from Django
            workspace.delete()
            logger.info(f"delete_workspace_safe '{workspace.name}': no engine, deleted from Django only.")
            result.update({'success': True, 'deleted': 'django_only'})
            return result

        # 1. Delete from engine FIRST
        delete_result = self.client.delete_workspace(workspace.name)
        if not delete_result.get('success', False):
            error = delete_result.get('error', delete_result.get('message', 'Engine delete failed'))
            logger.error(f"delete_workspace_safe '{workspace.name}': engine delete failed — {error}")
            result['error'] = error
            result['detail'] = 'Django object NOT deleted — engine deletion must succeed first.'
            return result

        # 2. Verify: workspace is actually gone from engine
        try:
            workspaces_after = self.client.get_workspaces()
            if workspace.name in workspaces_after:
                error = f"Workspace '{workspace.name}' still present in GeoServer after delete."
                logger.error(f"delete_workspace_safe verification failed: {error}")
                result['error'] = error
                result['detail'] = 'Django object NOT deleted — engine state unconfirmed.'
                return result
        except Exception as verify_exc:
            # Cannot reach engine to verify — but delete already returned success.
            # Proceed with Django deletion rather than leaving an orphan record.
            logger.warning(
                "delete_workspace_safe '%s': verify step failed (%s) — "
                "proceeding with Django deletion since engine delete returned success.",
                workspace.name, verify_exc,
            )

        # 3. Engine deletion confirmed (or unverifiable after success) — safe to delete Django object
        workspace.delete()
        logger.info(f"delete_workspace_safe '{workspace.name}': deleted from engine and Django.")
        result.update({'success': True, 'deleted': 'both'})
        return result

