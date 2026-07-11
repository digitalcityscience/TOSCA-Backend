"""
GeoServer Synchronization Service
Ensures Django DB stays in sync with GeoServer (Single Source of Truth = GeoServer)

This is a thin coordinator: all per-resource sync logic lives in the
sync/ package (WorkspaceSyncer, StoreSyncer, StyleSyncer, LayerSyncer).
GeoServerSyncService wires them up and exposes the same public method
surface that existed before the split, unchanged.
"""
import logging
from typing import Dict

from django.contrib.auth.models import User

from .geoserver.client import GeoServerClient
from .models import GeodataEngine, Layer, Workspace
from .sync import LayerSyncer, StoreSyncer, StyleSyncer, WorkspaceSyncer

logger = logging.getLogger(__name__)


class GeoServerSyncService:
    """
    Service to sync Django DB with GeoServer state.
    GeoServer is Single Source of Truth.
    """

    def __init__(self, geodata_engine: GeodataEngine):
        self.engine = geodata_engine
        self.client = GeoServerClient(
            url=self.engine.geoserver_url,
            username=self.engine.admin_username,
            password=self.engine.decrypted_admin_password
        )
        self._workspace_syncer = WorkspaceSyncer(self.engine, self.client)
        self._store_syncer = StoreSyncer(self.engine, self.client)
        self._style_syncer = StyleSyncer(self.engine, self.client)
        self._layer_syncer = LayerSyncer(self.engine, self.client)

    def sync_all_resources(self, created_by: User) -> Dict:
        """
        Full sync: Pull all resources from GeoServer and update Django DB.
        Always runs an integrity cleanup first to strip legacy 'workspace:name'
        prefixes from Layer records created before the client.py patch.
        """
        if not self.engine.is_active:
            logger.info("Skipping sync for inactive engine: %s", self.engine.name)
            return self._inactive_sync_result()

        logger.info(f"Starting full sync for engine: {self.engine.name}")

        self._cleanup_corrupt_layer_names()

        results = {
            'workspaces': {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []},
            'stores': {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []},
            'styles': {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []},
            'layers': {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []},
            'success': True
        }

        try:
            # 1️⃣ Sync Workspaces
            results['workspaces'] = self.sync_workspaces(created_by)

            # 2️⃣ Sync Stores (for each workspace)
            results['stores'] = self.sync_all_stores(created_by)

            # 3️⃣ Sync Styles before layer settings so default/additional style
            # assignments can resolve against the provider catalog.
            results['styles'] = self.sync_all_styles(created_by)

            # 4️⃣ Sync Layers (for each store)
            results['layers'] = self.sync_all_layers(created_by)

            logger.info(f"Sync completed: {results}")
            return results

        except Exception as e:
            logger.error(f"Sync failed: {e}")
            results['success'] = False
            results['error'] = str(e)
            return results

    def _cleanup_corrupt_layer_names(self) -> None:
        """Strip ':' prefix from any corrupted Layer names (legacy bug, see
        client.py history) before running the real sync.
        """
        corrupt_layers = Layer.objects.filter(
            workspace__geodata_engine=self.engine, name__contains=':'
        )
        if not corrupt_layers.exists():
            return

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

    def _inactive_sync_result(self) -> Dict:
        return {
            'workspaces': self._inactive_section_result(),
            'stores': self._inactive_section_result(),
            'styles': self._inactive_section_result(),
            'layers': self._inactive_section_result(),
            'success': True,
            'skipped': True,
            'reason': f"Engine '{self.engine.name}' is inactive.",
        }

    def _inactive_section_result(self) -> Dict:
        return {
            'synced': 0,
            'created': 0,
            'deleted': 0,
            'errors': [],
            'success': True,
            'skipped': True,
            'reason': f"Engine '{self.engine.name}' is inactive.",
        }

    # ------------------------------------------------------------------
    # Delegating methods — same public surface as before the split.
    # Each resource's actual sync logic now lives in sync/<resource>_syncer.py.
    # ------------------------------------------------------------------

    def sync_workspaces(self, created_by: User) -> Dict:
        return self._workspace_syncer.sync_workspaces(created_by)

    def sync_all_stores(self, created_by: User) -> Dict:
        return self._store_syncer.sync_all_stores(created_by)

    def sync_stores_for_workspace(self, workspace: Workspace, created_by: User) -> Dict:
        return self._store_syncer.sync_stores_for_workspace(workspace, created_by)

    def sync_all_styles(self, created_by: User) -> Dict:
        return self._style_syncer.sync_all_styles(created_by)

    def sync_styles_for_scope(self, workspace: Workspace | None, created_by: User) -> Dict:
        return self._style_syncer.sync_styles_for_scope(workspace, created_by)

    def sync_all_layers(self, created_by: User) -> Dict:
        return self._layer_syncer.sync_all_layers(created_by)

    def sync_layers_for_workspace(self, workspace: Workspace, created_by: User) -> Dict:
        return self._layer_syncer.sync_layers_for_workspace(workspace, created_by)

    def sync_workspace_resources(self, workspace: Workspace, created_by: User) -> Dict:
        """
        Sync stores, styles, and layers for a single workspace (in that
        order, matching sync_all_resources' resource ordering).

        This is "sync a workspace" as a single call — previously the admin
        action, the admin sync view, and the post-save sync hook each
        repeated this same three-call sequence independently. Callers that
        need individual error/count breakdowns can read result['stores'],
        result['styles'], result['layers'].
        """
        return {
            'stores': self.sync_stores_for_workspace(workspace, created_by),
            'styles': self.sync_styles_for_scope(workspace, created_by),
            'layers': self.sync_layers_for_workspace(workspace, created_by),
        }

    def push_workspace(self, workspace: Workspace) -> Dict:
        return self._workspace_syncer.push_workspace(workspace)

    def push_all_workspaces(self, created_by: User) -> Dict:
        return self._workspace_syncer.push_all_workspaces(created_by)

    def delete_workspace_safe(self, workspace: Workspace) -> Dict:
        return self._workspace_syncer.delete_workspace_safe(workspace)
