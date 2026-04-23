from django.db.models import QuerySet

from ...models import Layer


class LayerQueryService:
    """Read-only query helpers for layer-facing catalog data."""

    @classmethod
    def get_layer_detail(cls, *, layer_id) -> dict:
        layer = cls._base_queryset().get(id=layer_id)
        return cls._serialize_layer_detail(layer)

    @classmethod
    def list_workspace_layers(cls, *, workspace_id) -> list[dict]:
        layers = cls._base_queryset().filter(workspace_id=workspace_id)
        return [cls._serialize_layer_detail(layer) for layer in layers]

    @classmethod
    def _base_queryset(cls) -> QuerySet[Layer]:
        return (
            Layer.objects.select_related(
                "workspace",
                "workspace__geodata_engine",
                "store",
            )
            .order_by("name")
        )

    @classmethod
    def _serialize_layer_detail(cls, layer: Layer) -> dict:
        return {
            "id": str(layer.id),
            "name": layer.name,
            "title": layer.title,
            "description": layer.description,
            "table_name": layer.table_name,
            "geometry_column": layer.geometry_column,
            "geometry_type": layer.geometry_type,
            "srid": layer.srid,
            "publishing_state": layer.publishing_state,
            "is_public": layer.is_public,
            "published_url": layer.published_url,
            "provider": cls._serialize_provider(layer),
            "workspace": cls._serialize_workspace(layer),
            "store": cls._serialize_store(layer),
        }

    @classmethod
    def _serialize_provider(cls, layer: Layer) -> dict:
        provider = layer.workspace.geodata_engine
        return {
            "id": str(provider.id),
            "name": provider.name,
            "engine_type": provider.engine_type,
        }

    @classmethod
    def _serialize_workspace(cls, layer: Layer) -> dict:
        workspace = layer.workspace
        return {
            "id": str(workspace.id),
            "name": workspace.name,
            "description": workspace.description,
        }

    @classmethod
    def _serialize_store(cls, layer: Layer) -> dict:
        store = layer.store
        return {
            "id": str(store.id),
            "name": store.name,
            "description": store.description,
            "store_type": store.store_type,
        }
