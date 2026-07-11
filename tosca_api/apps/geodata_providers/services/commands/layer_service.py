import logging

from django.db import transaction
from django.utils import timezone

from ...engine_factory import EngineClientFactory
from ...exceptions import GeodataEngineError
from ...models import Layer, Store, Workspace
from ...postgis_inspector import PostGISInspectorError, get_table_bbox

logger = logging.getLogger(__name__)


class LayerService:
    @classmethod
    def publish_postgis(
        cls,
        *,
        workspace: Workspace,
        store: Store,
        table_name: str,
        layer_name: str,
        title: str,
        description: str,
        geometry_column: str,
        geometry_type: str,
        srid: int,
        user,
    ) -> dict:
        if store.workspace_id != workspace.id:
            return {
                'success': False,
                'error': 'Store does not belong to the selected workspace.',
                'message': 'Store/workspace mismatch.',
            }

        engine = workspace.geodata_engine
        if not engine:
            return {
                'success': False,
                'error': 'Workspace has no associated GeoServer engine.',
                'message': 'Workspace has no engine.',
            }

        client = EngineClientFactory.create_client(engine)
        already_in_geoserver = client.get_layer_info(
            workspace=workspace.name,
            layer_name=layer_name,
        )
        if already_in_geoserver:
            return {
                'success': False,
                'error': (
                    f"Layer '{layer_name}' already exists in workspace '{workspace.name}'. "
                    'Choose a different layer name.'
                ),
                'message': 'Layer already exists in engine.',
                'error_code': 'LAYER_ALREADY_EXISTS',
                'already_exists': True,
                'verified': True,
            }

        publish_result = client.publish_featuretype(
            store_name=store.name,
            workspace=workspace.name,
            pg_table=table_name,
            srid=srid,
            geometry_type=geometry_type,
            layer_name=layer_name,
            title=title or layer_name,
        )
        verified = client.verify_featuretype(
            workspace=workspace.name,
            store_name=store.name,
            featuretype_name=layer_name,
        )
        if not verified:
            return {
                'success': False,
                'verified': False,
                'error': (
                    'Layer publish_featurestore reported success but featuretype '
                    'could not be verified in GeoServer.'
                ),
                'message': 'Remote publish verification failed.',
                'publish_result': publish_result,
            }

        bbox = cls._get_bbox(
            store=store,
            table_name=table_name,
            geometry_column=geometry_column,
        )

        with transaction.atomic():
            layer, created = Layer.objects.get_or_create(
                workspace=workspace,
                name=layer_name,
                defaults={
                    'store': store,
                    'title': title,
                    'description': description,
                    'table_name': table_name,
                    'geometry_column': geometry_column,
                    'geometry_type': geometry_type,
                    'srid': srid,
                    'is_public': True,
                    'publishing_state': 'PUBLISHED',
                    'sync_state': 'SYNCED',
                    'last_sync_at': timezone.now(),
                    'last_sync_error': '',
                    'remote_identifier': f"{workspace.name}:{layer_name}",
                    'published_url': '',
                    'published_at': timezone.now(),
                    'created_by': user,
                },
            )
            if not created:
                Layer.objects.filter(pk=layer.pk).update(
                    store=store,
                    title=title,
                    description=description,
                    table_name=table_name,
                    geometry_column=geometry_column,
                    geometry_type=geometry_type,
                    srid=srid,
                    is_public=True,
                    publishing_state='PUBLISHED',
                    sync_state='SYNCED',
                    last_sync_at=timezone.now(),
                    last_sync_error='',
                    remote_identifier=f"{workspace.name}:{layer_name}",
                    published_url='',
                    published_at=timezone.now(),
                    publishing_error='',
                )
                layer.refresh_from_db()

        return {
            'success': True,
            'verified': True,
            'created': created,
            'message': f"Layer '{layer_name}' published in workspace '{workspace.name}'.",
            'bbox': bbox,
            'resource': layer,
            'publish_result': publish_result,
        }

    @classmethod
    def publish_existing_layer(cls, layer: Layer) -> dict:
        if layer.publishing_state == 'PUBLISHED':
            return {
                'success': True,
                'idempotent': True,
                'message': 'Layer already published',
                'resource': layer,
            }

        engine = layer.workspace.geodata_engine if layer.workspace else None
        if not engine:
            return {
                'success': False,
                'error': f"Layer '{layer.name}' has no engine.",
                'message': 'Layer has no engine.',
            }

        client = EngineClientFactory.create_client(engine)
        already = client.verify_featuretype(
            workspace=layer.workspace.name,
            store_name=layer.store.name,
            featuretype_name=layer.name,
        )
        if already:
            Layer.objects.filter(pk=layer.pk).update(
                publishing_state='PUBLISHED',
                sync_state='SYNCED',
                last_sync_at=timezone.now(),
                last_sync_error='',
                remote_identifier=f"{layer.workspace.name}:{layer.name}",
                publishing_error='',
                published_at=timezone.now(),
            )
            layer.refresh_from_db()
            return {
                'success': True,
                'already_exists': True,
                'verified': True,
                'message': f"Layer '{layer.name}' already exists in GeoServer.",
                'resource': layer,
            }

        publish_result = client.publish_featuretype(
            store_name=layer.store.name,
            workspace=layer.workspace.name,
            pg_table=layer.table_name,
            srid=layer.srid,
            geometry_type=layer.geometry_type,
            layer_name=layer.name,
            title=layer.title or layer.name,
        )
        verified = client.verify_featuretype(
            workspace=layer.workspace.name,
            store_name=layer.store.name,
            featuretype_name=layer.name,
        )
        if not verified:
            error = 'Publish reported success but verification failed.'
            Layer.objects.filter(pk=layer.pk).update(
                publishing_state='FAILED',
                publishing_error=error,
                sync_state='FAILED',
                last_sync_at=timezone.now(),
                last_sync_error=error,
            )
            layer.refresh_from_db()
            return {
                'success': False,
                'verified': False,
                'error': f"Layer '{layer.name}': publish reported success but verification failed.",
                'message': 'Remote publish verification failed.',
                'publish_result': publish_result,
                'resource': layer,
            }

        Layer.objects.filter(pk=layer.pk).update(
            publishing_state='PUBLISHED',
            sync_state='SYNCED',
            last_sync_at=timezone.now(),
            last_sync_error='',
            remote_identifier=f"{layer.workspace.name}:{layer.name}",
            published_at=timezone.now(),
            publishing_error='',
            published_url='',
        )
        layer.refresh_from_db()
        return {
            'success': True,
            'verified': True,
            'message': f"Layer '{layer.name}' published successfully.",
            'publish_result': publish_result,
            'resource': layer,
        }

    @classmethod
    def update_published_metadata(cls, *, layer: Layer, title: str, description: str) -> dict:
        engine = layer.workspace.geodata_engine if layer.workspace else None
        if not engine:
            raise GeodataEngineError(f"Layer '{layer.name}' has no engine.")

        client = EngineClientFactory.create_client(engine)
        remote_result = client.update_featuretype(
            workspace=layer.workspace.name,
            store_name=layer.store.name,
            featuretype_name=layer.name,
            title=title or layer.name,
            abstract=description or None,
        )
        verification = client.verify_featuretype_metadata(
            workspace=layer.workspace.name,
            store_name=layer.store.name,
            featuretype_name=layer.name,
            expected_title=title or layer.name,
            expected_abstract=description or '',
        )
        if not verification.get('verified'):
            error = f"GeoServer metadata verify failed: {verification.get('mismatches', {})}"
            Layer.objects.filter(pk=layer.pk).update(
                sync_state='FAILED',
                last_sync_at=timezone.now(),
                last_sync_error=error,
            )
            raise GeodataEngineError(
                error
            )

        Layer.objects.filter(pk=layer.pk).update(
            title=title,
            description=description,
            sync_state='SYNCED',
            last_sync_at=timezone.now(),
            last_sync_error='',
            remote_identifier=f"{layer.workspace.name}:{layer.name}",
            publishing_error='',
        )
        layer.refresh_from_db()
        return {
            'success': True,
            'verified': True,
            'message': f"Layer '{layer.name}' metadata updated successfully.",
            'verification': verification,
            'remote_result': remote_result,
            'resource': layer,
        }

    @classmethod
    def unpublish_layer(cls, layer: Layer) -> dict:
        if layer.publishing_state in {'DRAFT', 'UNPUBLISHED'}:
            return {
                'success': True,
                'idempotent': True,
                'message': 'Layer already unpublished',
                'resource': layer,
            }

        engine = layer.workspace.geodata_engine if layer.workspace else None
        if not engine:
            return {
                'success': False,
                'error': f"Layer '{layer.name}' has no engine.",
                'message': 'Layer has no engine.',
                'resource': layer,
            }

        client = EngineClientFactory.create_client(engine)
        remote_result = client.delete_layer(
            workspace=layer.workspace.name,
            layer_name=layer.name,
        )
        if not remote_result.get('success'):
            error = remote_result.get('error', remote_result.get('message', 'Engine unpublish failed'))
            Layer.objects.filter(pk=layer.pk).update(
                sync_state='FAILED',
                last_sync_at=timezone.now(),
                last_sync_error=error,
            )
            return {
                'success': False,
                'error': error,
                'message': remote_result.get('message', 'Engine unpublish failed'),
                'resource': layer,
            }

        verified = True
        if not remote_result.get('already_deleted'):
            verified = not client.verify_featuretype(
                workspace=layer.workspace.name,
                store_name=layer.store.name,
                featuretype_name=layer.name,
            )

        if not verified:
            error = f"Layer '{layer.name}' still exists in GeoServer after delete call."
            Layer.objects.filter(pk=layer.pk).update(
                sync_state='FAILED',
                last_sync_at=timezone.now(),
                last_sync_error=error,
            )
            return {
                'success': False,
                'verified': False,
                'error': error,
                'message': 'Remote unpublish verification failed.',
                'resource': layer,
            }

        Layer.objects.filter(pk=layer.pk).update(
            publishing_state='UNPUBLISHED',
            sync_state='LOCAL_ONLY',
            last_sync_at=timezone.now(),
            last_sync_error='',
            publishing_error='',
            published_url='',
            published_at=None,
        )
        layer.refresh_from_db()
        return {
            'success': True,
            'verified': True,
            'already_deleted': remote_result.get('already_deleted', False),
            'idempotent': remote_result.get('already_deleted', False),
            'message': f"Layer '{layer.name}' unpublished successfully.",
            'remote_result': remote_result,
            'resource': layer,
        }

    @classmethod
    def delete_layer_safe(cls, layer: Layer) -> dict:
        unpublish_result = None
        if layer.publishing_state == 'PUBLISHED':
            unpublish_result = cls.unpublish_layer(layer)
            if not unpublish_result.get('success'):
                return unpublish_result

        name = layer.name
        layer.delete()
        return {
            'success': True,
            'message': f"Layer '{name}' deleted successfully.",
            'resource_name': name,
            'already_deleted': bool(unpublish_result and unpublish_result.get('already_deleted')),
        }

    @staticmethod
    def _get_bbox(*, store: Store, table_name: str, geometry_column: str):
        if store.store_type != 'postgis' or not store.host:
            return None

        try:
            return get_table_bbox(
                host=store.host,
                port=store.port or 5432,
                database=store.database,
                username=store.username,
                password=store.decrypted_password,
                schema=store.schema or 'public',
                table=table_name,
                geometry_column=geometry_column,
            )
        except (PostGISInspectorError, Exception):
            return None


class LayerUpdateService:
    """
    Applies a PATCH/PUT to a Layer for LayerViewSet.update.

    Allowed editable fields:
        title, description  — synced to GeoServer (if PUBLISHED) then Django
        srid                — Django-only

    All other fields (name, table_name, workspace, store, geometry_*) are
    silently ignored — they cannot be changed via this endpoint.
    """

    ALLOWED_FIELDS = {'title', 'description', 'srid'}

    @classmethod
    def apply(cls, *, layer: Layer, fields: dict, serializer_class) -> dict:
        incoming = {k: v for k, v in fields.items() if k in cls.ALLOWED_FIELDS}

        if not incoming:
            return {
                'success': False,
                'status_code': 400,
                'body': {'detail': 'No editable fields provided. Allowed: title, description, srid.'},
            }

        if layer.publishing_state == 'PUBLISHED' and {'title', 'description'} & set(incoming):
            try:
                LayerService.update_published_metadata(
                    layer=layer,
                    title=incoming.get('title', layer.title),
                    description=incoming.get('description', layer.description),
                )
            except Exception as exc:
                logger.error(
                    'LayerUpdateService.apply: featuretype update failed for %s/%s: %s',
                    layer.workspace.name, layer.name, exc,
                )
                return {
                    'success': False,
                    'status_code': 400,
                    'body': {'success': False, 'error': f'GeoServer featuretype update failed: {exc}'},
                }
            incoming = {key: value for key, value in incoming.items() if key not in {'title', 'description'}}
            layer.refresh_from_db()

        if incoming:
            serializer = serializer_class(layer, data=incoming, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()

        return {'success': True, 'layer': layer}
