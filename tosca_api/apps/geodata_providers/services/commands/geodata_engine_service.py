from typing import Any

from django.db import transaction
from django.db.models import Q

from ...engine_factory import EngineClientFactory
from ...exceptions import GeodataEngineError
from ...models import GeodataEngine, Layer, Store, Style, Workspace


class GeodataEngineService:
    CONNECTION_FIELDS = {
        'engine_type',
        'base_url',
        'admin_username',
        'admin_password',
        'api_key',
    }
    ENGINE_FIELDS = {
        'name',
        'description',
        'engine_type',
        'base_url',
        'admin_username',
        'admin_password',
        'api_key',
        'is_active',
        'is_default',
    }

    @classmethod
    def create_engine(cls, *, user, trigger_sync=True, **data) -> tuple[GeodataEngine, dict]:
        payload = cls._filter_engine_fields(data)
        payload['created_by'] = user

        cls.validate_engine_connection(config=payload)

        with transaction.atomic():
            engine = GeodataEngine.objects.create(**payload)

        sync_result = cls._sync_after_save(engine, user=user, trigger_sync=trigger_sync)
        return engine, sync_result

    @classmethod
    def update_engine(cls, engine: GeodataEngine, *, user, trigger_sync=True, **changes) -> tuple[GeodataEngine, dict]:
        payload = cls._filter_engine_fields(changes)
        connection_changed = any(field in payload for field in cls.CONNECTION_FIELDS)

        if connection_changed:
            validation_payload = cls._engine_payload(engine)
            validation_payload.update(payload)
            cls.validate_engine_connection(config=validation_payload)

        with transaction.atomic():
            for field, value in payload.items():
                setattr(engine, field, value)
            if payload:
                engine.save(update_fields=list(payload.keys()) + ['updated_at'])

        sync_result = cls._sync_after_save(engine, user=user, trigger_sync=trigger_sync)
        return engine, sync_result

    @classmethod
    def delete_engine_safe(cls, engine: GeodataEngine) -> dict:
        dependency_counts = cls._dependency_counts(engine)
        if any(dependency_counts.values()):
            return {
                'success': False,
                'blocked': True,
                'message': cls._dependency_message(engine, dependency_counts),
                'dependency_counts': dependency_counts,
            }

        engine.delete()
        return {'success': True, 'blocked': False, 'message': f"Engine '{engine.name}' deleted successfully."}

    @classmethod
    def deactivate_engine(cls, engine: GeodataEngine) -> dict:
        if not engine.is_active:
            return {
                'success': True,
                'already_in_state': True,
                'message': f"Engine '{engine.name}' is already inactive.",
                'resource': engine,
            }

        engine.is_active = False
        engine.save(update_fields=['is_active', 'updated_at'])
        return {
            'success': True,
            'already_in_state': False,
            'message': f"Engine '{engine.name}' deactivated.",
            'resource': engine,
        }

    @classmethod
    def reactivate_engine(cls, engine: GeodataEngine) -> dict:
        if engine.is_active:
            return {
                'success': True,
                'already_in_state': True,
                'message': f"Engine '{engine.name}' is already active.",
                'resource': engine,
            }

        engine.is_active = True
        engine.save(update_fields=['is_active', 'updated_at'])
        return {
            'success': True,
            'already_in_state': False,
            'message': f"Engine '{engine.name}' reactivated.",
            'resource': engine,
        }

    @classmethod
    def delete_engine_cascade(cls, engine: GeodataEngine, *, delete_remote: bool) -> dict:
        summary = cls.get_dependency_counts(engine)
        if delete_remote:
            remote_result = cls._delete_engine_resources_remote(engine=engine, summary=summary)
            if not remote_result.get('success'):
                return remote_result

        summary['layers_deleted'] = summary['layers']
        summary['stores_deleted'] = summary['stores']
        summary['workspaces_deleted'] = summary['workspaces']
        summary['styles_deleted'] = summary['styles']

        engine_name = engine.name
        engine.delete()
        return {
            'success': True,
            'blocked': False,
            'message': cls._delete_message(engine_name=engine_name, delete_remote=delete_remote),
            'summary': summary,
            'delete_remote': delete_remote,
        }

    @classmethod
    def sync_engine(cls, engine: GeodataEngine, *, user) -> dict:
        if not engine.is_active:
            return cls._inactive_sync_skipped_result(engine)

        service = EngineClientFactory.create_sync_service(engine)
        result = service.sync_all_resources(created_by=user)
        result['db_workspace_count'] = Workspace.objects.filter(geodata_engine=engine).count()
        result['db_store_count'] = Store.objects.filter(workspace__geodata_engine=engine).count()
        result['db_style_count'] = Style.objects.filter(geodata_engine=engine).count()
        result['db_layer_count'] = Layer.objects.filter(workspace__geodata_engine=engine).count()
        return result

    @classmethod
    def validate_engine_connection(cls, *, engine: GeodataEngine | None = None, config: dict[str, Any] | None = None) -> dict:
        target_engine = engine or cls._build_engine(config or {})
        client = EngineClientFactory.create_client(target_engine)
        result = client.validate_connection()
        if result.get('success') is False:
            raise GeodataEngineError(result.get('error') or result.get('message') or 'Connection validation failed')
        return {
            'success': True,
            'message': result.get('message', 'Connection validated'),
            'version': result.get('version'),
            'raw': result,
        }

    @classmethod
    def _build_engine(cls, config: dict[str, Any]) -> GeodataEngine:
        payload = cls._filter_engine_fields(config)
        payload.setdefault('name', config.get('name') or 'validation-engine')
        payload.setdefault('description', config.get('description') or '')
        payload.setdefault('engine_type', config.get('engine_type') or 'geoserver')
        payload.setdefault('base_url', config.get('base_url') or '')
        payload.setdefault('admin_username', config.get('admin_username') or '')
        payload.setdefault('admin_password', config.get('admin_password') or '')
        payload.setdefault('api_key', config.get('api_key') or '')
        payload.setdefault('is_active', config.get('is_active', True))
        payload.setdefault('is_default', config.get('is_default', False))
        return GeodataEngine(**payload)

    @classmethod
    def _engine_payload(cls, engine: GeodataEngine) -> dict[str, Any]:
        return {
            'name': engine.name,
            'description': engine.description,
            'engine_type': engine.engine_type,
            'base_url': engine.base_url,
            'admin_username': engine.admin_username,
            'admin_password': engine.admin_password,
            'api_key': engine.api_key,
            'is_active': engine.is_active,
            'is_default': engine.is_default,
        }

    @classmethod
    def _filter_engine_fields(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {field: value for field, value in data.items() if field in cls.ENGINE_FIELDS}

    @classmethod
    def _dependency_counts(cls, engine: GeodataEngine) -> dict[str, int]:
        return {
            'workspaces': engine.workspaces.count(),
            'stores': Store.objects.filter(
                Q(workspace__geodata_engine=engine) | Q(geodata_engine=engine)
            ).distinct().count(),
            'layers': Layer.objects.filter(workspace__geodata_engine=engine).count(),
            'styles': Style.objects.filter(geodata_engine=engine).count(),
        }

    @classmethod
    def _dependency_message(cls, engine: GeodataEngine, counts: dict[str, int]) -> str:
        details = ", ".join(f"{label}={value}" for label, value in counts.items() if value)
        return f"Cannot delete engine '{engine.name}': dependent records exist ({details})."

    @classmethod
    def get_dependency_counts(cls, engine: GeodataEngine) -> dict[str, int]:
        counts = cls._dependency_counts(engine)
        return {
            **counts,
            'workspaces_deleted': 0,
            'stores_deleted': 0,
            'layers_deleted': 0,
            'styles_deleted': 0,
            'workspaces_already_deleted': 0,
            'stores_already_deleted': 0,
            'layers_already_deleted': 0,
            'styles_already_deleted': 0,
        }

    @staticmethod
    def _cascade_failure(*, engine: GeodataEngine, resource_label: str, result: dict, summary: dict) -> dict:
        return {
            'success': False,
            'blocked': result.get('blocked', False),
            'message': (
                f"Engine '{engine.name}' cascade delete aborted while deleting {resource_label}: "
                f"{result.get('message', result.get('error', 'unknown error'))}"
            ),
            'error': result.get('error', result.get('message')),
            'summary': summary,
            'step_result': result,
        }

    @classmethod
    def _delete_engine_resources_remote(cls, *, engine: GeodataEngine, summary: dict) -> dict:
        client = EngineClientFactory.create_client(engine)

        styles = list(
            Style.objects.filter(geodata_engine=engine)
            .select_related('workspace')
            .order_by('workspace__name', 'name')
        )
        for style in styles:
            result = client.delete_style(
                name=style.name,
                workspace=style.workspace.name if style.workspace else None,
                ignore_missing=True,
            )
            if not result.get('success'):
                return cls._cascade_failure(
                    engine=engine,
                    resource_label=f"style '{style.name}'",
                    result=result,
                    summary=summary,
                )
            if result.get('already_deleted'):
                summary['styles_already_deleted'] += 1

        workspaces = list(
            Workspace.objects.filter(geodata_engine=engine)
            .order_by('name')
        )
        for workspace in workspaces:
            result = client.delete_workspace(workspace.name)
            if not result.get('success'):
                return cls._cascade_failure(
                    engine=engine,
                    resource_label=f"workspace '{workspace.name}'",
                    result=result,
                    summary=summary,
                )
            if result.get('already_deleted'):
                summary['workspaces_already_deleted'] += 1

        return {'success': True, 'summary': summary}

    @staticmethod
    def _delete_message(*, engine_name: str, delete_remote: bool) -> str:
        if delete_remote:
            return (
                f"Engine '{engine_name}' and its dependent resources were deleted from "
                "the provider and Django."
            )
        return (
            f"Engine '{engine_name}' and its dependent resources were deleted from Django only. "
            "Remote provider resources were left untouched."
        )

    @staticmethod
    def _sync_skipped_result() -> dict:
        return {'success': None, 'skipped': True, 'reason': 'Sync not requested'}

    @staticmethod
    def _inactive_sync_skipped_result(engine: GeodataEngine) -> dict:
        return {
            'success': True,
            'skipped': True,
            'reason': f"Engine '{engine.name}' is inactive.",
            'db_workspace_count': Workspace.objects.filter(geodata_engine=engine).count(),
            'db_store_count': Store.objects.filter(workspace__geodata_engine=engine).count(),
            'db_style_count': Style.objects.filter(geodata_engine=engine).count(),
            'db_layer_count': Layer.objects.filter(workspace__geodata_engine=engine).count(),
        }

    @classmethod
    def _sync_after_save(cls, engine: GeodataEngine, *, user, trigger_sync: bool) -> dict:
        if not trigger_sync:
            return cls._sync_skipped_result()

        try:
            return cls.sync_engine(engine, user=user)
        except Exception as exc:
            return {'success': False, 'skipped': False, 'error': str(exc)}
