import os

from django.db import transaction
from django.utils import timezone

from ...engine_factory import EngineClientFactory
from ...models import Layer, LayerStyleAssignment, Store, Style, Workspace
from ...postgis_inspector import PostGISInspectorError, test_postgis_connection


class StoreService:
    @classmethod
    def create_postgis_store(
        cls,
        *,
        workspace: Workspace,
        name: str,
        user,
        store_type: str = 'postgis',
        description: str = '',
        host: str = '',
        port: int | None = 5432,
        database: str = '',
        username: str = '',
        password: str = '',
        schema: str = 'public',
        file_path: str = '',
        charset: str = 'UTF-8',
    ) -> dict:
        existing = Store.objects.filter(workspace=workspace, name=name).first()
        if existing:
            if workspace.geodata_engine:
                cls._mark_synced(existing, remote_identifier=f"{workspace.name}:{name}")
            return {
                'success': True,
                'idempotent': True,
                'already_exists': True,
                'message': 'Store already exists',
                'resource': existing,
            }

        validation = cls.test_store_connection(
            store_type=store_type,
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            schema=schema,
        )
        if not validation.get('success'):
            return {
                'success': False,
                'message': validation.get('message', 'Store connection validation failed.'),
                'error': validation.get('error', 'Store connection validation failed.'),
                'validation': validation,
            }

        engine = workspace.geodata_engine
        remote_result = {'success': True, 'message': 'Created in DB', 'verified': True}
        if engine:
            client = EngineClientFactory.create_client(engine)
            remote_result = cls._create_store_in_engine(
                client=client,
                workspace=workspace,
                name=name,
                store_type=store_type,
                host=host,
                port=port,
                database=database,
                username=username,
                password=password,
                schema=schema,
                file_path=file_path,
                charset=charset,
            )
            if not remote_result.get('success', False):
                return {
                    'success': False,
                    'message': remote_result.get('message', 'Store create failed.'),
                    'error': remote_result.get('error', 'Store create failed.'),
                    'verified': remote_result.get('verified', False),
                    'remote_result': remote_result,
                }

        with transaction.atomic():
            store = Store.objects.create(
                workspace=workspace,
                geodata_engine=engine,
                name=name,
                store_type=store_type,
                description=description,
                host=host,
                port=port,
                database=database,
                username=username,
                password=password,
                schema=schema,
                file_path=file_path,
                charset=charset,
                sync_state=(
                    'SYNCED' if engine and remote_result.get('verified', True)
                    else 'STALE' if engine
                    else 'LOCAL_ONLY'
                ),
                last_sync_at=timezone.now() if engine else None,
                last_sync_error='',
                remote_identifier=f"{workspace.name}:{name}" if engine else '',
                created_by=user,
            )

        return {
            'success': True,
            'created': True,
            'verified': remote_result.get('verified'),
            'message': f"Store '{name}' created successfully.",
            'resource': store,
            'remote_result': remote_result,
        }

    @classmethod
    def test_store_connection(
        cls,
        *,
        store_type: str = 'postgis',
        host: str = '',
        port: int | None = 5432,
        database: str = '',
        username: str = '',
        password: str = '',
        schema: str = 'public',
    ) -> dict:
        if store_type != Store.StoreType.POSTGIS:
            return {
                'success': True,
                'skipped': True,
                'message': f"Connection validation skipped for {store_type} stores.",
                'details': {},
            }

        errors = cls._validate_postgis_connection_payload(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
            schema=schema,
        )
        if errors:
            return {
                'success': False,
                'error': 'Missing required PostGIS connection fields.',
                'message': 'Missing required PostGIS connection fields.',
                'details': {'field_errors': errors},
            }

        try:
            normalized_port = int(port or 5432)
        except (TypeError, ValueError):
            return {
                'success': False,
                'error': 'PostGIS port must be a number.',
                'message': 'PostGIS port must be a number.',
                'details': {'field_errors': {'port': 'Enter a valid port number.'}},
            }

        try:
            details = test_postgis_connection(
                host=host,
                port=normalized_port,
                database=database,
                username=username,
                password=password,
                schema=schema or 'public',
            )
        except PostGISInspectorError as exc:
            return {
                'success': False,
                'error': str(exc),
                'message': str(exc),
                'details': {},
            }

        return {
            'success': True,
            'message': 'PostGIS connection validated.',
            'details': details,
        }

    @staticmethod
    def _validate_postgis_connection_payload(
        *,
        host: str,
        port: int | None,
        database: str,
        username: str,
        password: str,
        schema: str,
    ) -> dict[str, str]:
        required = {
            'host': host,
            'port': port,
            'database': database,
            'username': username,
            'password': password,
            'schema': schema,
        }
        return {
            field: 'This field is required for PostGIS connection validation.'
            for field, value in required.items()
            if value in (None, '')
        }

    @classmethod
    def clone_store(
        cls,
        *,
        source_store: Store,
        target_workspace: Workspace,
        name: str,
        user,
        description: str = '',
        host: str = '',
        port: int | None = None,
        database: str = '',
        username: str = '',
        password: str = '',
        schema: str = '',
        trigger_sync: bool = True,
        clone_layers: bool = False,
    ) -> dict:
        create_result = cls.create_postgis_store(
            workspace=target_workspace,
            name=name,
            user=user,
            store_type=source_store.store_type,
            description=description,
            host=host or source_store.host,
            port=port or source_store.port or 5432,
            database=database or source_store.database,
            username=username or source_store.username,
            password=password,
            schema=schema or source_store.schema or 'public',
            file_path=source_store.file_path,
            charset=source_store.charset,
        )
        if not create_result.get('success'):
            return create_result

        cloned_store = create_result['resource']
        layer_clone_result = cls._clone_dependent_layers(
            source_store=source_store,
            target_store=cloned_store,
            user=user,
        ) if clone_layers else {
            'success': None,
            'skipped': True,
            'reason': 'Layer clone not requested',
            'created': 0,
            'synced': 0,
            'style_assignments_created': 0,
            'errors': [],
        }
        sync_result = cls._sync_after_create(
            target_workspace,
            user=user,
            trigger_sync=trigger_sync,
            include_layers=clone_layers,
        )
        create_result['layer_clone_result'] = layer_clone_result
        create_result['sync_result'] = sync_result
        if layer_clone_result.get('errors'):
            create_result['partial_failure'] = True
        return create_result

    @classmethod
    def update_postgis_store_connection(
        cls,
        *,
        store: Store,
        host: str,
        port: int | None,
        database: str,
        username: str,
        password: str,
        schema: str,
        description: str = '',
    ) -> dict:
        if store.store_type != Store.StoreType.POSTGIS:
            return {
                'success': False,
                'message': 'Only PostGIS store connections can be updated with this flow.',
                'error': 'Unsupported store type.',
            }

        normalized = {
            'host': host,
            'port': port or 5432,
            'database': database,
            'username': username,
            'password': password,
            'schema': schema or 'public',
            'description': description,
        }
        current_password = store.decrypted_password if store.password else ''
        if (
            store.host == normalized['host']
            and (store.port or 5432) == normalized['port']
            and store.database == normalized['database']
            and store.username == normalized['username']
            and current_password == normalized['password']
            and (store.schema or 'public') == normalized['schema']
            and store.description == normalized['description']
        ):
            return {
                'success': True,
                'updated': False,
                'idempotent': True,
                'verified': True,
                'message': f"Store '{store.name}' connection already matches requested values.",
                'resource': store,
                'remote_result': {'success': True, 'verified': True, 'idempotent': True},
            }

        validation = cls.test_store_connection(
            store_type=store.store_type,
            host=normalized['host'],
            port=normalized['port'],
            database=normalized['database'],
            username=normalized['username'],
            password=normalized['password'],
            schema=normalized['schema'],
        )
        if not validation.get('success'):
            return {
                'success': False,
                'message': validation.get('message', 'Store connection validation failed.'),
                'error': validation.get('error', 'Store connection validation failed.'),
                'validation': validation,
            }

        workspace = store.workspace
        engine = workspace.geodata_engine if workspace else None
        remote_result = {'success': True, 'message': 'Updated in DB only.', 'verified': True}
        if engine and workspace:
            client = EngineClientFactory.create_client(engine)
            remote_result = client.update_postgis_store(
                name=store.name,
                workspace=workspace.name,
                host=normalized['host'],
                port=normalized['port'],
                database=normalized['database'],
                username=normalized['username'],
                password=normalized['password'],
                schema=normalized['schema'],
            )
            if not remote_result.get('success', False):
                return {
                    'success': False,
                    'message': remote_result.get('message', 'Engine failed to update the store.'),
                    'error': remote_result.get('error', remote_result.get('message', 'Engine failed to update the store.')),
                    'remote_result': remote_result,
                }

        with transaction.atomic():
            store.host = normalized['host']
            store.port = normalized['port']
            store.database = normalized['database']
            store.username = normalized['username']
            store.password = normalized['password']
            store.schema = normalized['schema']
            store.description = normalized['description']
            store.sync_state = (
                'SYNCED' if engine and remote_result.get('verified', True)
                else 'STALE' if engine
                else 'LOCAL_ONLY'
            )
            store.last_sync_at = timezone.now() if engine else None
            store.last_sync_error = ''
            store.remote_identifier = f"{workspace.name}:{store.name}" if engine and workspace else ''
            store.geodata_engine = engine
            store.save()

        layer_sync_result = cls._sync_layers_after_update(workspace, user=store.created_by)
        return {
            'success': True,
            'updated': True,
            'verified': remote_result.get('verified'),
            'message': f"Store '{store.name}' connection updated successfully.",
            'resource': store,
            'remote_result': remote_result,
            'layer_sync_result': layer_sync_result,
        }

    @classmethod
    def delete_store_safe(cls, store: Store) -> dict:
        dependency_counts = cls._dependency_counts(store)
        if any(dependency_counts.values()):
            return {
                'success': False,
                'blocked': True,
                'message': cls._dependency_message(store, dependency_counts),
                'dependency_counts': dependency_counts,
            }

        engine = store.workspace.geodata_engine if store.workspace else None
        workspace_name = store.workspace.name if store.workspace else None
        remote_result = {'success': True, 'message': 'Deleted from DB only.'}
        if engine and workspace_name:
            client = EngineClientFactory.create_client(engine)
            remote_result = client.delete_store(workspace_name, store.name)
            if not remote_result.get('success', False):
                cls._mark_failed(
                    store,
                    remote_result.get('error', remote_result.get('message', 'Engine failed to delete the store.')),
                )
                return {
                    'success': False,
                    'blocked': False,
                    'message': remote_result.get('message', 'Engine failed to delete the store.'),
                    'error': remote_result.get('error', remote_result.get('message', 'Engine failed to delete the store.')),
                    'remote_result': remote_result,
                }

        store_name = store.name
        already_deleted = remote_result.get('already_deleted', False)
        verified = remote_result.get('verified')
        store.delete()
        return {
            'success': True,
            'blocked': False,
            'already_deleted': already_deleted,
            'verified': verified,
            'message': f"Store '{store_name}' deleted successfully.",
            'remote_result': remote_result,
        }

    @classmethod
    def _create_store_in_engine(
        cls,
        *,
        client,
        workspace: Workspace,
        name: str,
        store_type: str,
        host: str,
        port: int | None,
        database: str,
        username: str,
        password: str,
        schema: str,
        file_path: str,
        charset: str,
    ) -> dict:
        if store_type == 'postgis':
            return client.create_postgis_store(
                name=name,
                workspace=workspace.name,
                host=host,
                port=port or 5432,
                database=database,
                username=username,
                password=password,
                schema=schema or 'public',
            )

        if store_type == 'file':
            ext = os.path.splitext(file_path)[1].lower()
            base = {'name': name, 'url': f'file:{file_path}'}
            if ext == '.gpkg':
                return client.create_geopackage_store(workspace=workspace.name, store_data=base)
            if ext == '.geojson':
                return client.create_geojson_store(workspace=workspace.name, store_data=base)
            if ext == '.shp' or os.path.isdir(file_path):
                payload = {**base, 'charset': charset}
                if os.path.isdir(file_path):
                    return client.create_directory_store(workspace=workspace.name, store_data=payload)
                return client.create_shapefile_store(workspace=workspace.name, store_data=payload)
            return {'success': False, 'error': f'Unsupported file type: {ext}', 'message': f'Unsupported file type: {ext}'}

        if store_type == 'geotiff':
            return client.create_geotiff_store(
                workspace=workspace.name,
                store_data={'name': name, 'url': f'file:{file_path}'},
            )

        return {'success': False, 'error': f'Unsupported store type: {store_type}', 'message': f'Unsupported store type: {store_type}'}

    @classmethod
    def _dependency_counts(cls, store: Store) -> dict[str, int]:
        return {
            'layers': Layer.objects.filter(store=store).count(),
        }

    @classmethod
    def _dependency_message(cls, store: Store, counts: dict[str, int]) -> str:
        return (
            f"Cannot delete store '{store.name}': dependent records exist "
            f"({cls._dependency_details(counts)})."
        )

    @staticmethod
    def _dependency_details(counts: dict[str, int]) -> str:
        return ", ".join(f"{label}={value}" for label, value in counts.items() if value)

    @staticmethod
    def _sync_skipped_result() -> dict:
        return {'success': None, 'skipped': True, 'reason': 'Sync not requested'}

    @classmethod
    def _sync_after_create(cls, workspace: Workspace, *, user, trigger_sync: bool, include_layers: bool = False) -> dict:
        if not trigger_sync:
            return cls._sync_skipped_result()

        engine = workspace.geodata_engine if workspace else None
        if not engine:
            return {'success': None, 'skipped': True, 'reason': 'Workspace has no engine'}

        try:
            service = EngineClientFactory.create_sync_service(engine)
            store_result = service.sync_stores_for_workspace(workspace, created_by=user)
            if not include_layers:
                return store_result
            layer_result = service.sync_layers_for_workspace(workspace, created_by=user)
            return {
                **store_result,
                'success': not (store_result.get('errors') or layer_result.get('errors')),
                'stores': store_result,
                'layers': layer_result,
                'errors': store_result.get('errors', []) + layer_result.get('errors', []),
            }
        except Exception as exc:
            return {'success': False, 'skipped': False, 'error': str(exc)}

    @classmethod
    def _sync_layers_after_update(cls, workspace: Workspace | None, *, user) -> dict:
        engine = workspace.geodata_engine if workspace else None
        if not engine:
            return {'success': None, 'skipped': True, 'reason': 'Workspace has no engine'}

        try:
            service = EngineClientFactory.create_sync_service(engine)
            return service.sync_layers_for_workspace(workspace, created_by=user)
        except Exception as exc:
            return {'success': False, 'skipped': False, 'error': str(exc)}

    @classmethod
    def _clone_dependent_layers(
        cls,
        *,
        source_store: Store,
        target_store: Store,
        user,
    ) -> dict:
        source_layers = list(
            Layer.objects.filter(store=source_store)
            .prefetch_related('style_assignments__style')
            .order_by('name')
        )
        result = {
            'success': True,
            'created': 0,
            'synced': 0,
            'style_assignments_created': 0,
            'errors': [],
            'skipped': [],
        }
        if not source_layers:
            return result

        workspace = target_store.workspace
        engine = workspace.geodata_engine if workspace else None
        client = EngineClientFactory.create_client(engine) if engine else None

        for source_layer in source_layers:
            target_layer_name = cls._target_layer_name(source_layer, target_store)
            try:
                remote_publish_result = {'success': None, 'skipped': True}
                if client and source_layer.publishing_state == Layer.PublishingState.PUBLISHED:
                    existing_remote = client.get_layer_info(
                        workspace=workspace.name,
                        layer_name=target_layer_name,
                    )
                    if existing_remote:
                        remote_publish_result = {'success': True, 'already_exists': True}
                    else:
                        remote_publish_result = client.publish_featuretype(
                            store_name=target_store.name,
                            workspace=workspace.name,
                            pg_table=source_layer.table_name,
                            srid=source_layer.srid,
                            geometry_type=source_layer.geometry_type,
                            layer_name=target_layer_name,
                            title=source_layer.title or target_layer_name,
                        )
                        verified = client.verify_featuretype(
                            workspace=workspace.name,
                            store_name=target_store.name,
                            featuretype_name=target_layer_name,
                        )
                        if not verified:
                            raise RuntimeError(
                                f"Layer '{target_layer_name}' publish could not be verified in GeoServer."
                            )

                layer, created = Layer.objects.update_or_create(
                    workspace=workspace,
                    name=target_layer_name,
                    defaults={
                        'store': target_store,
                        'title': source_layer.title,
                        'description': source_layer.description,
                        'table_name': source_layer.table_name,
                        'geometry_column': source_layer.geometry_column,
                        'geometry_type': source_layer.geometry_type,
                        'srid': source_layer.srid,
                        'publishing_state': source_layer.publishing_state,
                        'is_public': source_layer.is_public,
                        'queryable': source_layer.queryable,
                        'opaque': source_layer.opaque,
                        'published_url': '',
                        'publishing_error': '',
                        'published_at': timezone.now() if source_layer.is_published else None,
                        'created_by': user,
                        'sync_state': (
                            'SYNCED'
                            if source_layer.is_published and remote_publish_result.get('success') is not False
                            else 'LOCAL_ONLY'
                        ),
                        'last_sync_at': timezone.now(),
                        'last_sync_error': '',
                        'remote_identifier': (
                            f"{workspace.name}:{target_layer_name}"
                            if source_layer.is_published and engine
                            else ''
                        ),
                    },
                )
                result['created' if created else 'synced'] += 1
                result['style_assignments_created'] += cls._clone_style_assignments(
                    source_layer=source_layer,
                    target_layer=layer,
                    user=user,
                    errors=result['errors'],
                )
            except Exception as exc:
                result['errors'].append(f"Layer '{source_layer.name}' could not be cloned: {exc}")

        result['success'] = not result['errors']
        return result

    @staticmethod
    def _target_layer_name(source_layer: Layer, target_store: Store) -> str:
        workspace = target_store.workspace
        if not Layer.objects.filter(workspace=workspace, name=source_layer.name).exists():
            return source_layer.name

        base = f"{target_store.name}_{source_layer.name}"[:100]
        if not Layer.objects.filter(workspace=workspace, name=base).exists():
            return base

        suffix = 2
        while True:
            suffix_text = f"_{suffix}"
            candidate = f"{base[:100 - len(suffix_text)]}{suffix_text}"
            if not Layer.objects.filter(workspace=workspace, name=candidate).exists():
                return candidate
            suffix += 1

    @classmethod
    def _clone_style_assignments(
        cls,
        *,
        source_layer: Layer,
        target_layer: Layer,
        user,
        errors: list[str],
    ) -> int:
        created_count = 0
        for assignment in source_layer.style_assignments.all():
            if not assignment.is_active:
                continue
            target_style = cls._resolve_target_style(
                assignment.style,
                target_workspace=target_layer.workspace,
            )
            if not target_style:
                errors.append(
                    f"Layer '{source_layer.name}' style '{assignment.style.qualified_name}' "
                    "is not valid for the target workspace."
                )
                continue
            _, created = LayerStyleAssignment.objects.get_or_create(
                layer=target_layer,
                style=target_style,
                role=assignment.role,
                defaults={
                    'is_active': assignment.is_active,
                    'created_by': user,
                },
            )
            if created:
                created_count += 1
        return created_count

    @staticmethod
    def _resolve_target_style(style: Style, *, target_workspace: Workspace) -> Style | None:
        if style.geodata_engine_id != target_workspace.geodata_engine_id:
            return None
        if style.workspace_id in (None, target_workspace.id):
            return style
        return Style.objects.filter(
            geodata_engine=target_workspace.geodata_engine,
            workspace=target_workspace,
            name=style.name,
        ).first()

    @staticmethod
    def _mark_synced(store: Store, *, remote_identifier: str = '') -> None:
        Store.objects.filter(pk=store.pk).update(
            sync_state='SYNCED',
            last_sync_at=timezone.now(),
            last_sync_error='',
            remote_identifier=remote_identifier,
        )

    @staticmethod
    def _mark_failed(store: Store, error: str) -> None:
        Store.objects.filter(pk=store.pk).update(
            sync_state='FAILED',
            last_sync_at=timezone.now(),
            last_sync_error=error,
        )
