from django.db.models import Count, Prefetch, Q, QuerySet

from ...models import Layer, Store, Workspace


class WorkspaceQueryService:
    """Read-only query helpers for workspace-facing catalog data."""

    VISIBLE_LAYER_FILTER = Q(is_public=True, publishing_state="PUBLISHED")

    @classmethod
    def get_workspace_detail(cls, *, provider_id, workspace_id, include_inactive: bool = False) -> dict:
        workspace = cls._base_queryset(include_inactive=include_inactive).get(
            geodata_engine_id=provider_id,
            id=workspace_id,
        )
        return cls._serialize_workspace_detail(workspace)

    @classmethod
    def list_provider_workspaces(cls, *, provider_id, include_inactive: bool = False) -> list[dict]:
        workspaces = cls._base_queryset(include_inactive=include_inactive).filter(geodata_engine_id=provider_id)
        return [cls._serialize_workspace_summary(workspace) for workspace in workspaces]

    @classmethod
    def _base_queryset(cls, *, include_inactive: bool = False) -> QuerySet[Workspace]:
        store_queryset = Store.objects.order_by("name")
        layer_queryset = cls._visible_layer_queryset()
        queryset = (
            Workspace.objects.select_related("geodata_engine")
            .annotate(
                store_count=Count(
                    "stores",
                    filter=Q(stores__layers__is_public=True, stores__layers__publishing_state="PUBLISHED"),
                    distinct=True,
                ),
                layer_count=Count("layers", filter=Q(layers__is_public=True, layers__publishing_state="PUBLISHED"), distinct=True),
            )
            .prefetch_related(
                Prefetch("stores", queryset=store_queryset),
                Prefetch("layers", queryset=layer_queryset),
            )
            .order_by("name")
        )
        if not include_inactive:
            queryset = queryset.filter(geodata_engine__is_active=True)
        return queryset

    @classmethod
    def _serialize_workspace_detail(cls, workspace: Workspace) -> dict:
        visible_layers = list(workspace.layers.all())
        visible_store_ids = {layer.store_id for layer in visible_layers}
        return {
            "id": str(workspace.id),
            "name": workspace.name,
            "description": workspace.description,
            "provider": cls._serialize_provider(workspace),
            "stores": [
                cls._serialize_store(store)
                for store in workspace.stores.all()
                if store.id in visible_store_ids
            ],
            "layers": [cls._serialize_layer(layer) for layer in visible_layers],
        }

    @classmethod
    def _serialize_workspace_summary(cls, workspace: Workspace) -> dict:
        return {
            "id": str(workspace.id),
            "name": workspace.name,
            "description": workspace.description,
            "provider": cls._serialize_provider(workspace),
            "store_count": workspace.store_count,
            "layer_count": workspace.layer_count,
        }

    @classmethod
    def _serialize_provider(cls, workspace: Workspace) -> dict:
        provider = workspace.geodata_engine
        return {
            "id": str(provider.id),
            "name": provider.name,
            "engine_type": provider.engine_type,
        }

    @classmethod
    def _serialize_store(cls, store: Store) -> dict:
        return {
            "id": str(store.id),
            "name": store.name,
            "description": store.description,
            "store_type": store.store_type,
        }

    @classmethod
    def _serialize_layer(cls, layer: Layer) -> dict:
        return {
            "id": str(layer.id),
            "name": layer.name,
            "title": layer.title,
            "description": layer.description,
            "table_name": layer.table_name,
            "publishing_state": layer.publishing_state,
            "is_public": layer.is_public,
            "store_id": str(layer.store_id),
        }

    @classmethod
    def _visible_layer_queryset(cls) -> QuerySet[Layer]:
        return Layer.objects.filter(cls.VISIBLE_LAYER_FILTER).select_related("store").order_by("name")
