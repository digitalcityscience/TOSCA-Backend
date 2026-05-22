from django.db.models import QuerySet

from tosca_api.apps.geodata_providers.models import Layer, Workspace


class CatalogVisibilityService:
    """Read helpers for the public catalog visibility surface."""

    @classmethod
    def list_visible_workspaces(cls):
        return cls._dedupe_workspaces(cls._visible_workspace_queryset())

    @classmethod
    def get_visible_workspace(cls, *, workspace_name: str):
        workspace = cls._visible_workspace_queryset().filter(name=workspace_name).first()
        if workspace is None:
            raise Workspace.DoesNotExist(f"Workspace '{workspace_name}' not found.")
        return workspace

    @classmethod
    def list_visible_layers(cls, *, workspace_name: str | None = None):
        queryset = cls._visible_layer_queryset()
        if workspace_name:
            workspace = cls.get_visible_workspace(workspace_name=workspace_name)
            queryset = queryset.filter(workspace=workspace)
        return cls._dedupe_layers(queryset)

    @classmethod
    def get_visible_layer(cls, *, workspace_name: str, layer_name: str):
        workspace = cls.get_visible_workspace(workspace_name=workspace_name)
        layer = cls._visible_layer_queryset().filter(workspace=workspace, name=layer_name).first()
        if layer is None:
            raise Layer.DoesNotExist(
                f"Layer '{layer_name}' not found in workspace '{workspace_name}'."
            )
        return layer

    @classmethod
    def _visible_workspace_queryset(cls) -> QuerySet[Workspace]:
        return (
            Workspace.objects.filter(
                geodata_engine__is_active=True,
                layers__is_public=True,
                layers__publishing_state="PUBLISHED",
            )
            .exclude(layers__sync_state__in=["FAILED", "STALE"])
            .select_related("geodata_engine")
            .order_by("name", "-geodata_engine__is_default", "geodata_engine__name", "id")
        )

    @classmethod
    def _visible_layer_queryset(cls) -> QuerySet[Layer]:
        return (
            Layer.objects.select_related("workspace", "workspace__geodata_engine")
            .prefetch_related("style_assignments__style")
            .filter(
                workspace__geodata_engine__is_active=True,
                is_public=True,
                publishing_state="PUBLISHED",
            )
            .exclude(sync_state__in=["FAILED", "STALE"])
            .order_by(
                "workspace__name",
                "name",
                "-workspace__geodata_engine__is_default",
                "workspace__geodata_engine__name",
                "id",
            )
        )

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
