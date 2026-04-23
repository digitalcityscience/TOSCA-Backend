from django.db.models import QuerySet

from tosca_api.apps.geodata_providers.models import Layer, Workspace


class CatalogVisibilityService:
    """Read helpers for the public catalog visibility surface."""

    @classmethod
    def list_visible_workspaces(cls):
        return list(cls._visible_workspace_queryset())

    @classmethod
    def get_visible_workspace(cls, *, workspace_name: str):
        return cls._visible_workspace_queryset().get(name=workspace_name)

    @classmethod
    def list_visible_layers(cls, *, workspace_name: str | None = None):
        queryset = cls._visible_layer_queryset()
        if workspace_name:
            queryset = queryset.filter(workspace__name=workspace_name)
        return list(queryset)

    @classmethod
    def get_visible_layer(cls, *, workspace_name: str, layer_name: str):
        return cls._visible_layer_queryset().get(
            workspace__name=workspace_name,
            name=layer_name,
        )

    @classmethod
    def _visible_workspace_queryset(cls) -> QuerySet[Workspace]:
        return (
            Workspace.objects.filter(
                geodata_engine__is_active=True,
                layers__is_public=True,
                layers__publishing_state="PUBLISHED",
            )
            .distinct()
            .order_by("name")
        )

    @classmethod
    def _visible_layer_queryset(cls) -> QuerySet[Layer]:
        return (
            Layer.objects.select_related("workspace", "workspace__geodata_engine")
            .filter(
                workspace__geodata_engine__is_active=True,
                is_public=True,
                publishing_state="PUBLISHED",
            )
            .order_by("workspace__name", "name")
        )
