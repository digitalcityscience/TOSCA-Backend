import os

from django.db import transaction

from ...engine_factory import EngineClientFactory
from ...models import Layer, Store, Workspace


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
            return {
                'success': True,
                'idempotent': True,
                'already_exists': True,
                'message': 'Store already exists',
                'resource': existing,
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

        sync_result = cls._sync_after_create(target_workspace, user=user, trigger_sync=trigger_sync)
        create_result['sync_result'] = sync_result
        return create_result

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
        details = ", ".join(f"{label}={value}" for label, value in counts.items() if value)
        return f"Cannot delete store '{store.name}': dependent records exist ({details})."

    @staticmethod
    def _sync_skipped_result() -> dict:
        return {'success': None, 'skipped': True, 'reason': 'Sync not requested'}

    @classmethod
    def _sync_after_create(cls, workspace: Workspace, *, user, trigger_sync: bool) -> dict:
        if not trigger_sync:
            return cls._sync_skipped_result()

        engine = workspace.geodata_engine if workspace else None
        if not engine:
            return {'success': None, 'skipped': True, 'reason': 'Workspace has no engine'}

        try:
            service = EngineClientFactory.create_sync_service(engine)
            return service.sync_stores_for_workspace(workspace, created_by=user)
        except Exception as exc:
            return {'success': False, 'skipped': False, 'error': str(exc)}
