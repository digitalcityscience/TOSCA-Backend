"""Style pull-sync (GeoServer -> Django), global and per-workspace scope."""
import logging
from typing import Dict, List

from django.contrib.auth.models import User

from ..exceptions import GeoServerConnectionError
from ..models import Style, Workspace
from .base import BaseSyncer

logger = logging.getLogger(__name__)


class StyleSyncer(BaseSyncer):
    """Owns all style sync logic."""

    def sync_all_styles(self, created_by: User) -> Dict:
        """Sync global and workspace-scoped GeoServer styles to Django."""
        if not self.engine.is_active:
            return self._inactive_section_result()

        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}

        global_results = self.sync_styles_for_scope(None, created_by)
        results['synced'] += global_results['synced']
        results['created'] += global_results['created']
        results['deleted'] += global_results['deleted']
        results['errors'].extend(global_results['errors'])

        for workspace in Workspace.objects.filter(geodata_engine=self.engine):
            workspace_results = self.sync_styles_for_scope(workspace, created_by)
            results['synced'] += workspace_results['synced']
            results['created'] += workspace_results['created']
            results['deleted'] += workspace_results['deleted']
            results['errors'].extend(workspace_results['errors'])

        return results

    def sync_styles_for_scope(self, workspace: Workspace | None, created_by: User) -> Dict:
        """Sync styles for a global provider scope or a single workspace scope."""
        if not self.engine.is_active:
            return self._inactive_section_result()

        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}

        try:
            workspace_name = workspace.name if workspace else None
            geoserver_styles = self._fetch_remote(workspace_name)
            geoserver_style_names = {style_data['name'] for style_data in geoserver_styles}
            django_styles = Style.objects.filter(geodata_engine=self.engine, workspace=workspace)
            django_style_names = {style.name for style in django_styles}

            upsert_results = self._upsert_styles(workspace, geoserver_styles, created_by)
            results['synced'] += upsert_results['synced']
            results['created'] += upsert_results['created']
            results['errors'].extend(upsert_results['errors'])

            styles_to_delete = django_style_names - geoserver_style_names
            delete_results = self._delete_stale_styles(django_styles, styles_to_delete)
            results['deleted'] += delete_results['deleted']
            results['errors'].extend(delete_results['errors'])

            return results
        except GeoServerConnectionError as e:
            self._mark_queryset_sync_failed(
                Style.objects.filter(geodata_engine=self.engine, workspace=workspace),
                str(e),
            )
            raise
        # Genuinely-unexpected fallback below the known GeoServerConnectionError
        # case above — kept broad so a bug here still reports a sync error
        # instead of crashing the admin action that called this.
        except Exception as e:
            scope = workspace.name if workspace else 'global'
            error = f"Failed to sync styles for {scope}: {e}"
            results['errors'].append(error)
            self._mark_queryset_sync_failed(
                Style.objects.filter(geodata_engine=self.engine, workspace=workspace),
                error,
            )
            return results

    def _fetch_remote(self, workspace_name: str | None) -> List[Dict]:
        """
        Get style catalog records from GeoServer.
        Raises GeoServerConnectionError if GeoServer is unreachable.
        """
        return self.client.get_styles(workspace_name)

    def _upsert_styles(self, workspace: Workspace | None, geoserver_styles: List[Dict], created_by: User) -> Dict:
        """CREATE/UPDATE: GeoServer styles that need to be in Django."""
        results = {'synced': 0, 'created': 0, 'errors': []}
        workspace_name = workspace.name if workspace else None

        for style_data in geoserver_styles:
            try:
                style_name = style_data['name']
                style_content = self.client.get_style_content(
                    name=style_name,
                    workspace=workspace_name,
                ) or {}
                file_content = style_content.get('content', '')
                style_format = style_content.get('format', 'sld')
                file_name = style_content.get('file_name', '')
                validation_state = 'UNKNOWN'
                validation_errors = []
                if file_content:
                    from ..services.commands.style_validation_service import StyleValidationService
                    validation = StyleValidationService.validate(
                        content=file_content,
                        style_format=style_format,
                    )
                    validation_state = 'VALID' if validation.get('valid') else 'INVALID'
                    validation_errors = validation.get('errors', [])
                style, created = Style.objects.update_or_create(
                    geodata_engine=self.engine,
                    workspace=workspace,
                    name=style_name,
                    defaults={
                        'title': style_name,
                        'description': f'Synced from GeoServer: {style_name}',
                        'format': style_format,
                        'file_name': file_name,
                        'file_content': file_content,
                        'validation_state': validation_state,
                        'validation_errors': validation_errors,
                        'remote_state': 'SYNCED',
                        'remote_error': '',
                        **self._sync_success_defaults(remote_identifier=style_name),
                        'created_by': created_by,
                    },
                )
                if created:
                    results['created'] += 1
                    logger.info("✅ Created style: %s", style.qualified_name)
                else:
                    results['synced'] += 1
                    logger.info("✅ Synced style: %s", style.qualified_name)
            # Per-item isolation: one bad remote style must not abort
            # the whole sync loop — record it and keep going.
            except Exception as e:
                error_msg = f"Failed to sync style {style_data.get('name')}: {e}"
                results['errors'].append(error_msg)
                logger.error(error_msg)
                style = Style.objects.filter(
                    geodata_engine=self.engine,
                    workspace=workspace,
                    name=style_data.get('name'),
                ).first()
                self._mark_sync_failed(style, error_msg)
        return results

    def _delete_stale_styles(self, django_styles, styles_to_delete: set) -> Dict:
        """DELETE: Django styles that don't exist in GeoServer."""
        results = {'deleted': 0, 'errors': []}
        for style_name in styles_to_delete:
            try:
                style = django_styles.get(name=style_name)
                style.delete()
                results['deleted'] += 1
                logger.info("🗑️ Deleted style: %s", style_name)
            # Per-item isolation: same as above, for the delete pass.
            except Exception as e:
                error_msg = f"Failed to delete style {style_name}: {e}"
                results['errors'].append(error_msg)
                logger.error(error_msg)
        return results
