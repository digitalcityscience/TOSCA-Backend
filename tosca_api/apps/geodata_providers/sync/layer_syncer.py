"""Layer pull-sync (GeoServer -> Django), per workspace and engine-wide.

Also owns layer<->style assignment resolution, since that's only ever
invoked as part of syncing a layer's remote default/additional styles.
"""
import logging
from typing import Dict, List

from django.contrib.auth.models import User

from ..exceptions import GeoServerConnectionError
from ..models import Layer, LayerStyleAssignment, Store, Style, Workspace
from .base import BaseSyncer

logger = logging.getLogger(__name__)


class LayerSyncer(BaseSyncer):
    """Owns all layer sync logic, including style assignment resolution."""

    GEOMETRY_TYPES = set(Layer.GeometryType.values)

    def sync_all_layers(self, created_by: User) -> Dict:
        """Sync all layers from GeoServer"""
        if not self.engine.is_active:
            return self._inactive_section_result()

        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}

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
        if not self.engine.is_active:
            return self._inactive_section_result()

        results = {'synced': 0, 'created': 0, 'deleted': 0, 'errors': []}

        try:
            geoserver_layers = self._fetch_remote(workspace.name)
            geoserver_layer_names = set(layer_data['name'] for layer_data in geoserver_layers)
            logger.info(f"GeoServer workspace '{workspace.name}' has {len(geoserver_layer_names)} layers: {geoserver_layer_names}")

            django_layers = Layer.objects.filter(workspace=workspace)
            django_layer_names = set(layer.name for layer in django_layers)
            logger.info(f"Django workspace '{workspace.name}' has {len(django_layer_names)} layers: {django_layer_names}")

            upsert_results = self._upsert_layers(workspace, geoserver_layers, created_by)
            results['synced'] += upsert_results['synced']
            results['created'] += upsert_results['created']
            results['errors'].extend(upsert_results['errors'])

            layers_to_delete = django_layer_names - geoserver_layer_names
            delete_results = self._delete_stale_layers(workspace, django_layers, layers_to_delete)
            results['deleted'] += delete_results['deleted']
            results['errors'].extend(delete_results['errors'])

            return results

        except GeoServerConnectionError as e:
            self._mark_queryset_sync_failed(
                Layer.objects.filter(workspace=workspace),
                str(e),
            )
            raise
        # Genuinely-unexpected fallback below the known GeoServerConnectionError
        # case above — kept broad so a bug here still reports a sync error
        # instead of crashing the admin action that called this.
        except Exception as e:
            error = f"Failed to get layers for workspace {workspace.name}: {e}"
            results['errors'].append(error)
            self._mark_queryset_sync_failed(
                Layer.objects.filter(workspace=workspace),
                error,
            )
            return results

    def _fetch_remote(self, workspace_name: str) -> List[Dict]:
        """
        Get layer info from GeoServer.
        Raises GeoServerConnectionError if GeoServer is unreachable.
        """
        return self.client.get_layers(workspace_name)

    def _upsert_layers(self, workspace: Workspace, geoserver_layers: List[Dict], created_by: User) -> Dict:
        """CREATE/UPDATE: GeoServer layers that need to be in Django."""
        results = {'synced': 0, 'created': 0, 'errors': []}

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

                spatial_metadata = self._resolve_spatial_metadata(
                    workspace=workspace,
                    store=store,
                    layer_data=layer_data,
                )

                layer, created = Layer.objects.update_or_create(
                    workspace=workspace,
                    name=layer_name,
                    defaults={
                        'store': store,
                        'title': layer_data.get('title', layer_name),
                        'description': f'Synced from GeoServer: {layer_name}',
                        'table_name': layer_data.get('table_name', layer_name),
                        **spatial_metadata,
                        'is_public': layer_data.get('advertised', True),  # read from GeoServer
                        'queryable': layer_data.get('queryable', True),
                        'opaque': layer_data.get('opaque', False),
                        'publishing_state': 'PUBLISHED',
                        'created_by': created_by,
                        **self._sync_success_defaults(
                            remote_identifier=f"{workspace.name}:{layer_name}",
                            remote_hash=layer_data.get('remote_hash', ''),
                        ),
                    }
                )
                self._sync_layer_style_assignments(
                    layer=layer,
                    default_style_name=layer_data.get('default_style_name', ''),
                    additional_style_names=layer_data.get('additional_style_names', []),
                    created_by=created_by,
                )

                if created:
                    results['created'] += 1
                    logger.info(f"✅ Created layer: {workspace.name}/{layer_name}")
                else:
                    results['synced'] += 1
                    logger.info(f"✅ Synced layer: {workspace.name}/{layer_name}")

            # Per-item isolation: one bad remote layer must not abort
            # the whole sync loop — record it and keep going.
            except Exception as e:
                error_msg = f"Failed to sync layer {layer_data.get('name')}: {e}"
                results['errors'].append(error_msg)
                logger.error(error_msg)
                layer = Layer.objects.filter(
                    workspace=workspace,
                    name=layer_data.get('name'),
                ).first()
                self._mark_sync_failed(layer, error_msg)
        return results

    def _resolve_spatial_metadata(
        self,
        *,
        workspace: Workspace,
        store: Store,
        layer_data: Dict,
    ) -> dict:
        """Resolve trustworthy geometry metadata before persisting a layer.

        Coverage layers intentionally use the existing raster placeholders.
        Vector layers must have a supported geometry type. Older client
        summaries omit it, so fetch the authoritative feature-type detail
        instead of silently storing every unknown geometry as ``Point``.
        """
        if store.store_type == Store.StoreType.GEOTIFF:
            return {
                'geometry_column': layer_data.get('geometry_column') or 'rast',
                'geometry_type': layer_data.get('geometry_type') or Layer.GeometryType.POINT,
                'srid': self._normalize_srid(layer_data.get('srid')),
            }

        geometry_type = self._normalize_geometry_type(layer_data.get('geometry_type'))
        detail = {}
        if geometry_type is None:
            detail = self.client.get_featuretype_detail(
                workspace.name,
                store.name,
                layer_data['name'],
            )
            geometry_type = self._normalize_geometry_type(detail.get('geometry_type'))

        if geometry_type is None:
            raise ValueError(
                f"GeoServer did not report a supported geometry type for "
                f"'{workspace.name}:{layer_data['name']}'."
            )

        return {
            'geometry_column': (
                layer_data.get('geometry_column')
                or detail.get('geometry_column')
                or 'geom'
            ),
            'geometry_type': geometry_type,
            'srid': self._normalize_srid(layer_data.get('srid') or detail.get('srid')),
        }

    @classmethod
    def _normalize_geometry_type(cls, value) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        geometry_type = value.strip().rsplit('.', 1)[-1]
        return geometry_type if geometry_type in cls.GEOMETRY_TYPES else None

    @staticmethod
    def _normalize_srid(value) -> int:
        if isinstance(value, str) and ':' in value:
            value = value.rsplit(':', 1)[-1]
        try:
            return int(value)
        except (TypeError, ValueError):
            return 4326

    def _delete_stale_layers(self, workspace: Workspace, django_layers, layers_to_delete: set) -> Dict:
        """DELETE: Django layers that don't exist in GeoServer."""
        results = {'deleted': 0, 'errors': []}
        if not layers_to_delete:
            return results

        logger.info(f"🗑️ Deleting {len(layers_to_delete)} layers not in GeoServer: {layers_to_delete}")
        for layer_name in layers_to_delete:
            try:
                layer = django_layers.get(name=layer_name)
                layer.delete()
                results['deleted'] += 1
                logger.info(f"🗑️ Deleted layer: {workspace.name}/{layer_name}")
            # Per-item isolation: same as above, for the delete pass.
            except Exception as e:
                error_msg = f"Failed to delete layer {layer_name}: {e}"
                results['errors'].append(error_msg)
                logger.error(error_msg)
        return results

    # ------------------------------------------------------------------
    # Layer <-> style assignment resolution
    # ------------------------------------------------------------------

    def _sync_layer_style_assignments(
        self,
        *,
        layer: Layer,
        default_style_name: str,
        additional_style_names: List[str],
        created_by: User,
    ) -> None:
        active_assignment_ids = []

        if default_style_name:
            assignment = self._upsert_layer_style_assignment(
                layer=layer,
                style_name=default_style_name,
                role='default',
                created_by=created_by,
            )
            if assignment:
                active_assignment_ids.append(assignment.id)

        for style_name in additional_style_names or []:
            assignment = self._upsert_layer_style_assignment(
                layer=layer,
                style_name=style_name,
                role='alternate',
                created_by=created_by,
            )
            if assignment:
                active_assignment_ids.append(assignment.id)

        LayerStyleAssignment.objects.filter(layer=layer).exclude(
            id__in=active_assignment_ids,
        ).update(is_active=False)

    def _upsert_layer_style_assignment(
        self,
        *,
        layer: Layer,
        style_name: str,
        role: str,
        created_by: User,
    ) -> LayerStyleAssignment | None:
        style = self._resolve_style_for_layer(layer=layer, style_name=style_name, created_by=created_by)
        if not style:
            return None
        if role == 'default':
            LayerStyleAssignment.objects.filter(
                layer=layer,
                role='default',
                is_active=True,
            ).exclude(style=style).update(is_active=False)
        assignment, _created = LayerStyleAssignment.objects.update_or_create(
            layer=layer,
            style=style,
            role=role,
            defaults={
                'is_active': True,
                'created_by': created_by,
            },
        )
        return assignment

    def _resolve_style_for_layer(
        self,
        *,
        layer: Layer,
        style_name: str,
        created_by: User,
    ) -> Style | None:
        if not style_name:
            return None
        style = (
            Style.objects.filter(
                geodata_engine=self.engine,
                workspace=layer.workspace,
                name=style_name,
            ).first()
            or Style.objects.filter(
                geodata_engine=self.engine,
                workspace__isnull=True,
                name=style_name,
            ).first()
            or Style.objects.filter(
                geodata_engine=self.engine,
                name=style_name,
            ).first()
        )
        if style:
            return style
        return Style.objects.create(
            geodata_engine=self.engine,
            workspace=None,
            name=style_name,
            title=style_name,
            description=f'Referenced by GeoServer layer: {layer.qualified_name if hasattr(layer, "qualified_name") else layer}',
            format='sld',
            file_name='',
            file_content='',
            validation_state='UNKNOWN',
            validation_errors=[],
            remote_state='SYNCED',
            **self._sync_success_defaults(remote_identifier=style_name),
            created_by=created_by,
        )
