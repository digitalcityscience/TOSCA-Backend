from django.db import transaction
from django.utils import timezone

from ...engine_factory import EngineClientFactory
from ...models import Layer, Store, Workspace


class WorkspaceService:
    RESERVED_NAMES = {'vector'}

    @classmethod
    def create_workspace(
        cls,
        *,
        engine,
        name: str,
        description: str = '',
        user,
    ) -> dict:
        normalized_name = (name or '').strip()
        policy_error = cls.validate_workspace_name(normalized_name)
        if policy_error:
            return {
                'success': False,
                'already_exists': False,
                'already_deleted': False,
                'blocked': True,
                'error': policy_error,
                'message': policy_error,
                'verified': False,
                'resource': None,
            }

        existing = Workspace.objects.filter(geodata_engine=engine, name=normalized_name).first()
        if existing:
            cls._mark_synced(existing, remote_identifier=normalized_name)
            return {
                'success': True,
                'idempotent': True,
                'already_exists': True,
                'already_deleted': False,
                'verified': True,
                'message': 'Workspace already exists',
                'error': None,
                'resource': existing,
            }

        remote_result = {'success': True, 'message': 'Created in DB', 'verified': True}
        if engine:
            client = EngineClientFactory.create_client(engine)
            remote_result = client.create_workspace(normalized_name)
            if not remote_result.get('success', False):
                return {
                    'success': False,
                    'already_exists': False,
                    'already_deleted': False,
                    'blocked': False,
                    'message': remote_result.get('message', 'Workspace create failed.'),
                    'error': remote_result.get('error', remote_result.get('message', 'Workspace create failed.')),
                    'verified': remote_result.get('verified', False),
                    'resource': None,
                    'remote_result': remote_result,
                }

        with transaction.atomic():
            workspace = Workspace.objects.create(
                geodata_engine=engine,
                name=normalized_name,
                description=description,
                sync_state=(
                    'SYNCED' if engine and remote_result.get('verified', True)
                    else 'STALE' if engine
                    else 'LOCAL_ONLY'
                ),
                last_sync_at=timezone.now() if engine else None,
                last_sync_error='',
                remote_identifier=normalized_name if engine else '',
                created_by=user,
            )

        return {
            'success': True,
            'created': True,
            'already_exists': False,
            'already_deleted': False,
            'verified': remote_result.get('verified'),
            'message': f"Workspace '{normalized_name}' created successfully.",
            'error': None,
            'resource': workspace,
            'remote_result': remote_result,
        }

    @classmethod
    def delete_workspace_safe(cls, workspace: Workspace) -> dict:
        policy_error = cls.validate_workspace_delete(workspace)
        if policy_error:
            return {
                'success': False,
                'already_exists': False,
                'already_deleted': False,
                'blocked': True,
                'error': policy_error,
                'message': policy_error,
                'verified': False,
                'resource': workspace,
                'dependency_counts': cls._dependency_counts(workspace),
            }

        engine = workspace.geodata_engine
        remote_result = {'success': True, 'message': 'Deleted from DB only.', 'verified': True}
        if engine:
            client = EngineClientFactory.create_client(engine)
            remote_result = client.delete_workspace(workspace.name)
            if not remote_result.get('success', False):
                cls._mark_failed(
                    workspace,
                    remote_result.get('error', remote_result.get('message', 'Engine failed to delete the workspace.')),
                )
                return {
                    'success': False,
                    'already_exists': False,
                    'already_deleted': False,
                    'blocked': False,
                    'message': remote_result.get('message', 'Engine failed to delete the workspace.'),
                    'error': remote_result.get('error', remote_result.get('message', 'Engine failed to delete the workspace.')),
                    'verified': remote_result.get('verified', False),
                    'resource': workspace,
                    'remote_result': remote_result,
                }

        workspace_name = workspace.name
        already_deleted = remote_result.get('already_deleted', False)
        verified = remote_result.get('verified')
        workspace.delete()
        return {
            'success': True,
            'blocked': False,
            'already_exists': False,
            'already_deleted': already_deleted,
            'verified': verified,
            'message': f"Workspace '{workspace_name}' deleted successfully.",
            'error': None,
            'resource': workspace,
            'remote_result': remote_result,
        }

    @classmethod
    def validate_workspace_name(cls, name: str) -> str | None:
        normalized_name = (name or '').strip()
        if not normalized_name:
            return 'Workspace name is required.'
        if normalized_name.lower() in cls.RESERVED_NAMES:
            return f"Workspace name '{normalized_name}' is reserved."
        return None

    @classmethod
    def validate_workspace_delete(cls, workspace: Workspace) -> str | None:
        if (workspace.name or '').strip().lower() in cls.RESERVED_NAMES:
            return f"Default workspace '{workspace.name}' cannot be deleted."

        counts = cls._dependency_counts(workspace)
        if any(counts.values()):
            return cls._dependency_message(workspace, counts)
        return None

    @classmethod
    def _dependency_counts(cls, workspace: Workspace) -> dict[str, int]:
        return {
            'stores': Store.objects.filter(workspace=workspace).count(),
            'layers': Layer.objects.filter(workspace=workspace).count(),
        }

    @classmethod
    def _dependency_message(cls, workspace: Workspace, counts: dict[str, int]) -> str:
        details = ", ".join(f"{label}={value}" for label, value in counts.items() if value)
        return f"Cannot delete workspace '{workspace.name}': dependent records exist ({details})."

    @staticmethod
    def _mark_synced(workspace: Workspace, *, remote_identifier: str = '') -> None:
        Workspace.objects.filter(pk=workspace.pk).update(
            sync_state='SYNCED',
            last_sync_at=timezone.now(),
            last_sync_error='',
            remote_identifier=remote_identifier,
        )

    @staticmethod
    def _mark_failed(workspace: Workspace, error: str) -> None:
        Workspace.objects.filter(pk=workspace.pk).update(
            sync_state='FAILED',
            last_sync_at=timezone.now(),
            last_sync_error=error,
        )
