import uuid

from django.db.models import Exists, OuterRef, QuerySet

from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Workspace


class CatalogVisibilityService:
    """Read helpers for the public catalog visibility surface."""

    BLOCKED_SYNC_STATES = ["FAILED", "STALE"]

    @classmethod
    def get_visible_provider(cls, *, provider_id):
        try:
            normalized_provider_id = uuid.UUID(str(provider_id))
        except (TypeError, ValueError) as exc:
            raise GeodataEngine.DoesNotExist("Provider not found.") from exc

        provider = GeodataEngine.objects.filter(
            id=normalized_provider_id,
            is_active=True,
        ).first()
        if provider is None:
            raise GeodataEngine.DoesNotExist("Provider not found.")
        return provider

    @classmethod
    def list_visible_workspaces(cls, *, provider_id=None):
        queryset = cls._visible_workspace_queryset(provider_id=provider_id)
        if provider_id is not None:
            return list(queryset)
        return cls._dedupe_workspaces(queryset)

    @classmethod
    def get_visible_workspace(cls, *, workspace_name: str, provider_id=None):
        workspace = (
            cls._visible_workspace_queryset(provider_id=provider_id)
            .filter(name=workspace_name)
            .first()
        )
        if workspace is None:
            raise Workspace.DoesNotExist(f"Workspace '{workspace_name}' not found.")
        return workspace

    @classmethod
    def list_visible_layers(cls, *, workspace_name: str | None = None, provider_id=None):
        queryset = cls._visible_layer_queryset(provider_id=provider_id)
        if workspace_name:
            workspace = cls.get_visible_workspace(
                workspace_name=workspace_name,
                provider_id=provider_id,
            )
            queryset = queryset.filter(workspace=workspace)
        if provider_id is not None:
            return list(queryset)
        return cls._dedupe_layers(queryset)

    @classmethod
    def get_visible_layer(cls, *, workspace_name: str, layer_name: str, provider_id=None):
        workspace = cls.get_visible_workspace(
            workspace_name=workspace_name,
            provider_id=provider_id,
        )
        layer = (
            cls._visible_layer_queryset(provider_id=provider_id)
            .filter(workspace=workspace, name=layer_name)
            .first()
        )
        if layer is None:
            raise Layer.DoesNotExist(
                f"Layer '{layer_name}' not found in workspace '{workspace_name}'."
            )
        return layer

    @classmethod
    def _visible_workspace_queryset(cls, *, provider_id=None) -> QuerySet[Workspace]:
        visible_layer_exists = (
            Layer.objects.filter(
                workspace_id=OuterRef("pk"),
                is_public=True,
                publishing_state="PUBLISHED",
            )
            .exclude(sync_state__in=cls.BLOCKED_SYNC_STATES)
            .exclude(store__sync_state__in=cls.BLOCKED_SYNC_STATES)
        )
        queryset = (
            Workspace.objects.filter(
                geodata_engine__is_active=True,
            )
            .exclude(sync_state__in=cls.BLOCKED_SYNC_STATES)
            .filter(Exists(visible_layer_exists))
            .select_related("geodata_engine")
            .order_by("name", "-geodata_engine__is_default", "geodata_engine__name", "id")
        )
        if provider_id is not None:
            provider = cls.get_visible_provider(provider_id=provider_id)
            queryset = queryset.filter(geodata_engine=provider)
        return queryset

    @classmethod
    def _visible_layer_queryset(cls, *, provider_id=None) -> QuerySet[Layer]:
        queryset = (
            Layer.objects.select_related("workspace", "workspace__geodata_engine")
            .prefetch_related("style_assignments__style")
            .filter(
                workspace__geodata_engine__is_active=True,
                is_public=True,
                publishing_state="PUBLISHED",
            )
            .exclude(sync_state__in=cls.BLOCKED_SYNC_STATES)
            .exclude(store__sync_state__in=cls.BLOCKED_SYNC_STATES)
            .exclude(workspace__sync_state__in=cls.BLOCKED_SYNC_STATES)
            .order_by(
                "workspace__name",
                "name",
                "-workspace__geodata_engine__is_default",
                "workspace__geodata_engine__name",
                "id",
            )
        )
        if provider_id is not None:
            provider = cls.get_visible_provider(provider_id=provider_id)
            queryset = queryset.filter(workspace__geodata_engine=provider)
        return queryset

    @staticmethod
    def _dedupe_workspaces(queryset: QuerySet[Workspace]) -> list[Workspace]:
        workspaces_by_name: dict[str, Workspace] = {}
        for workspace in queryset:
            workspaces_by_name.setdefault(workspace.name, workspace)
        return list(workspaces_by_name.values())

    @staticmethod
    def _dedupe_layers(queryset: QuerySet[Layer]) -> list[Layer]:
        layers_by_key: dict[tuple[str, str], Layer] = {}
        for layer in queryset:
            key = (layer.workspace.name, layer.name)
            layers_by_key.setdefault(key, layer)
        return list(layers_by_key.values())
