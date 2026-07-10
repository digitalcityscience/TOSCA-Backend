"""Store pull-sync (GeoServer -> Django), per workspace and engine-wide."""
import logging
from typing import Dict, List

from django.contrib.auth.models import User

from ..exceptions import GeoServerConnectionError
from ..models import Store, Workspace
from .base import BaseSyncer

logger = logging.getLogger(__name__)


class StoreSyncer(BaseSyncer):
    """Owns all store sync logic."""

    def sync_all_stores(self, created_by: User) -> Dict:
        """Sync all stores from GeoServer"""
        if not self.engine.is_active:
            return self._inactive_section_result()

        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}

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
        if not self.engine.is_active:
            return self._inactive_section_result()

        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}

        try:
            geoserver_stores = self._fetch_remote(workspace.name)
            geoserver_store_names = set(store_data['name'] for store_data in geoserver_stores)
            logger.info(f"GeoServer workspace '{workspace.name}' has {len(geoserver_store_names)} stores: {geoserver_store_names}")

            django_stores = Store.objects.filter(workspace=workspace)
            django_store_names = set(store.name for store in django_stores)
            logger.info(f"Django workspace '{workspace.name}' has {len(django_store_names)} stores: {django_store_names}")

            upsert_results = self._upsert_stores(workspace, geoserver_stores, created_by)
            results['synced'] += upsert_results['synced']
            results['created'] += upsert_results['created']
            results['errors'].extend(upsert_results['errors'])

            stores_to_delete = django_store_names - geoserver_store_names
            delete_results = self._delete_stale_stores(workspace, django_stores, stores_to_delete)
            results['deleted'] += delete_results['deleted']
            results['errors'].extend(delete_results['errors'])

            return results

        except GeoServerConnectionError as e:
            self._mark_queryset_sync_failed(
                Store.objects.filter(workspace=workspace),
                str(e),
            )
            raise
        except Exception as e:
            error = f"Failed to get stores for workspace {workspace.name}: {e}"
            results['errors'].append(error)
            self._mark_queryset_sync_failed(
                Store.objects.filter(workspace=workspace),
                error,
            )
            return results

    def _fetch_remote(self, workspace_name: str) -> List[Dict]:
        """
        Get datastore info from GeoServer.
        Raises GeoServerConnectionError if GeoServer is unreachable.
        """
        return self.client.get_datastores(workspace_name)

    def _upsert_stores(self, workspace: Workspace, geoserver_stores: List[Dict], created_by: User) -> Dict:
        """CREATE/UPDATE: GeoServer stores that need to be in Django."""
        results = {'synced': 0, 'created': 0, 'errors': []}
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

                # NOTE: 'password' is intentionally excluded from defaults.
                # GeoServer REST API never exposes credentials — store_data
                # will always return '' for password.  Including it in defaults
                # would wipe any password the user entered via Store Detail on
                # every sync cycle (F-009 / task 4.4.6).
                # Password is managed exclusively via PATCH /api/v1/providers/provider/stores/{id}/.
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
                        'schema': store_data.get('schema', 'public'),
                        'file_path': store_data.get('file_path', ''),
                        'created_by': created_by,
                        **self._sync_success_defaults(
                            remote_identifier=f"{workspace.name}:{store_name}",
                            remote_hash=store_data.get('remote_hash', ''),
                        ),
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
                store = Store.objects.filter(
                    workspace=workspace,
                    name=store_data.get('name'),
                ).first()
                self._mark_sync_failed(store, error_msg)
        return results

    def _delete_stale_stores(self, workspace: Workspace, django_stores, stores_to_delete: set) -> Dict:
        """DELETE: Django stores that don't exist in GeoServer."""
        results = {'deleted': 0, 'errors': []}
        if not stores_to_delete:
            return results

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
