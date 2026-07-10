"""Workspace pull-sync (GeoServer -> Django) and push-sync (Django -> GeoServer)."""
import logging
from typing import Dict, List

from django.contrib.auth.models import User

from ..exceptions import GeoServerConnectionError
from ..models import Workspace
from .base import BaseSyncer

logger = logging.getLogger(__name__)


class WorkspaceSyncer(BaseSyncer):
    """Owns all workspace sync logic: pull (sync_workspaces), push
    (push_workspace/push_all_workspaces), and safe delete.
    """

    # ------------------------------------------------------------------
    # Pull sync — GeoServer -> Django
    # ------------------------------------------------------------------

    def sync_workspaces(self, created_by: User) -> Dict:
        """Sync workspaces from GeoServer to Django - includes DELETE operations"""
        if not self.engine.is_active:
            return self._inactive_section_result()

        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}

        try:
            geoserver_workspaces = self._fetch_remote()
            logger.info(f"GeoServer has {len(geoserver_workspaces)} workspaces: {geoserver_workspaces}")

            django_workspaces = Workspace.objects.filter(geodata_engine=self.engine)
            django_workspace_names = set(ws.name for ws in django_workspaces)
            logger.info(f"Django has {len(django_workspace_names)} workspaces: {django_workspace_names}")

            upsert_results = self._upsert_workspaces(geoserver_workspaces, created_by)
            results['synced'] += upsert_results['synced']
            results['created'] += upsert_results['created']
            results['errors'].extend(upsert_results['errors'])

            geoserver_workspace_names = set(geoserver_workspaces)
            workspaces_to_delete = django_workspace_names - geoserver_workspace_names
            delete_results = self._delete_stale_workspaces(django_workspaces, workspaces_to_delete)
            results['deleted'] += delete_results['deleted']
            results['errors'].extend(delete_results['errors'])

            return results

        except GeoServerConnectionError as e:
            self._mark_queryset_sync_failed(
                Workspace.objects.filter(geodata_engine=self.engine),
                str(e),
            )
            raise
        except Exception as e:
            error = f"Failed to sync workspaces: {e}"
            results['errors'].append(error)
            self._mark_queryset_sync_failed(
                Workspace.objects.filter(geodata_engine=self.engine),
                error,
            )
            return results

    def _fetch_remote(self) -> List[str]:
        """
        Get workspace names from GeoServer.
        Raises GeoServerConnectionError if GeoServer is unreachable — callers must NOT swallow this.
        """
        return self.client.get_workspaces()

    def _upsert_workspaces(self, geoserver_workspaces: List[str], created_by: User) -> Dict:
        """CREATE/UPDATE: GeoServer workspaces that need to be in Django."""
        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}
        for ws_name in geoserver_workspaces:
            try:
                workspace, created = Workspace.objects.get_or_create(
                    geodata_engine=self.engine,
                    name=ws_name,
                    defaults={
                        'description': f'Synced from GeoServer: {ws_name}',
                        'created_by': created_by,
                        **self._sync_success_defaults(remote_identifier=ws_name),
                    }
                )
                if not created:
                    Workspace.objects.filter(pk=workspace.pk).update(
                        **self._sync_success_defaults(remote_identifier=ws_name)
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
                workspace = Workspace.objects.filter(
                    geodata_engine=self.engine,
                    name=ws_name,
                ).first()
                self._mark_sync_failed(workspace, error_msg)
        return results

    def _delete_stale_workspaces(self, django_workspaces, workspaces_to_delete: set) -> Dict:
        """DELETE: Django workspaces that don't exist in GeoServer."""
        results = {'deleted': 0, 'errors': []}
        if not workspaces_to_delete:
            return results

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

    # ------------------------------------------------------------------
    # Push sync — Django intent -> GeoServer
    # ------------------------------------------------------------------

    def push_workspace(self, workspace: Workspace) -> Dict:
        """
        Push a single Django workspace to GeoServer.
        Pattern: check exists → create if missing → verify → return result.
        Does NOT modify Django state — GeoServer is the destination here.
        """
        if not self.engine.is_active:
            return {
                'success': True,
                'workspace': workspace.name,
                'skipped': True,
                'reason': f"Engine '{self.engine.name}' is inactive.",
            }

        result = {'success': False, 'workspace': workspace.name}

        try:
            # 1. Check if already exists in GeoServer
            existing = self.client.get_workspaces()
            if workspace.name in existing:
                logger.info(f"Push workspace '{workspace.name}': already exists in GeoServer.")
                self._mark_resource_synced(workspace, remote_identifier=workspace.name)
                result.update({'success': True, 'action': 'already_exists'})
                return result

            # 2. Create in GeoServer
            create_result = self.client.create_workspace(workspace.name)
            if not create_result.get('success', False):
                error = create_result.get('error', create_result.get('message', 'Unknown error'))
                logger.error(f"Push workspace '{workspace.name}' create failed: {error}")
                self._mark_sync_failed(workspace, error)
                result['error'] = error
                return result

            # 3. Verify: confirm workspace is now present
            workspaces_after = self.client.get_workspaces()
            if workspace.name not in workspaces_after:
                error = f"Workspace '{workspace.name}' not found in GeoServer after create — possible partial failure."
                logger.error(error)
                self._mark_sync_failed(workspace, error)
                result['error'] = error
                return result

            logger.info(f"Push workspace '{workspace.name}': created and verified in GeoServer.")
            self._mark_resource_synced(workspace, remote_identifier=workspace.name)
            result.update({'success': True, 'action': 'created'})
            return result

        except Exception as e:
            logger.error(f"Push workspace '{workspace.name}' unexpected error: {e}")
            self._mark_sync_failed(workspace, str(e))
            result['error'] = str(e)
            return result

    def push_all_workspaces(self, created_by: User) -> Dict:
        """
        Push all Django workspaces for this engine to GeoServer.
        Returns aggregate results.
        """
        if not self.engine.is_active:
            return {
                'pushed': 0,
                'already_exists': 0,
                'errors': [],
                'success': True,
                'skipped': True,
                'reason': f"Engine '{self.engine.name}' is inactive.",
            }

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

    # ------------------------------------------------------------------
    # Safe delete — engine first, Django second
    # ------------------------------------------------------------------

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

        was_already_deleted = bool(delete_result.get('already_deleted'))

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
        result.update({
            'success': True,
            'deleted': 'engine_already_absent' if was_already_deleted else 'both',
        })
        return result
