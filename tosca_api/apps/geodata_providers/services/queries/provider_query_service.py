from django.db.models import Count, QuerySet

from ...models import GeodataEngine


class ProviderQueryService:
    """Read-only query helpers for provider-facing catalog data."""

    @classmethod
    def list_providers(cls, *, include_inactive: bool = False) -> list[dict]:
        queryset = cls._base_queryset()
        if not include_inactive:
            queryset = queryset.filter(is_active=True)
        return [cls._serialize_provider(provider) for provider in queryset]

    @classmethod
    def _base_queryset(cls) -> QuerySet[GeodataEngine]:
        return GeodataEngine.objects.annotate(
            workspace_count=Count("workspaces", distinct=True),
            layer_count=Count("workspaces__layers", distinct=True),
        )

    @classmethod
    def _serialize_provider(cls, provider: GeodataEngine) -> dict:
        return {
            "id": str(provider.id),
            "name": provider.name,
            "base_url": provider.base_url,
            "engine_type": provider.engine_type,
            "description": provider.description,
            "is_active": provider.is_active,
            "is_default": provider.is_default,
            "workspace_count": provider.workspace_count,
            "layer_count": provider.layer_count,
        }
