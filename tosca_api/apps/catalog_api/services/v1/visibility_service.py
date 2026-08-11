import uuid

from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet

from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    LayerGroup,
    LayerGroupMember,
    Workspace,
)


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
    def list_visible_workspaces(cls, *, provider_id):
        queryset = cls._visible_workspace_queryset(provider_id=provider_id)
        return list(queryset)

    @classmethod
    def get_visible_workspace(cls, *, workspace_name: str, provider_id):
        workspace = (
            cls._visible_workspace_queryset(provider_id=provider_id)
            .filter(name=workspace_name)
            .first()
        )
        if workspace is None:
            raise Workspace.DoesNotExist(f"Workspace '{workspace_name}' not found.")
        return workspace

    @classmethod
    def list_visible_layers(cls, *, provider_id, workspace_name: str | None = None):
        queryset = cls._visible_layer_queryset(provider_id=provider_id)
        if workspace_name:
            workspace = cls.get_visible_workspace(
                workspace_name=workspace_name,
                provider_id=provider_id,
            )
            queryset = queryset.filter(workspace=workspace)
        return list(queryset)

    @classmethod
    def get_visible_layer(cls, *, workspace_name: str, layer_name: str, provider_id):
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
    def list_visible_groups(cls, *, provider_id, workspace_name: str | None = None):
        queryset = cls._visible_group_queryset(provider_id=provider_id)
        if workspace_name:
            workspace = cls.get_visible_workspace(
                workspace_name=workspace_name,
                provider_id=provider_id,
            )
            queryset = queryset.filter(workspace=workspace)
        return list(queryset)

    @classmethod
    def get_visible_group(cls, *, workspace_name: str, group_name: str, provider_id):
        workspace = cls.get_visible_workspace(
            workspace_name=workspace_name,
            provider_id=provider_id,
        )
        group = (
            cls._visible_group_queryset(provider_id=provider_id)
            .filter(workspace=workspace, name=group_name)
            .first()
        )
        if group is None:
            raise LayerGroup.DoesNotExist(
                f"Layer group '{group_name}' not found in workspace '{workspace_name}'."
            )
        return group

    @classmethod
    def _visible_workspace_queryset(cls, *, provider_id) -> QuerySet[Workspace]:
        provider = cls.get_visible_provider(provider_id=provider_id)
        visible_layer_exists = (
            Layer.objects.filter(
                workspace_id=OuterRef("pk"),
                is_public=True,
                publishing_state="PUBLISHED",
            )
            .exclude(sync_state__in=cls.BLOCKED_SYNC_STATES)
            .exclude(store__sync_state__in=cls.BLOCKED_SYNC_STATES)
        )
        visible_group_exists = (
            LayerGroup.objects.filter(
                workspace_id=OuterRef("pk"),
                is_public=True,
                publishing_state="PUBLISHED",
            )
            .exclude(sync_state__in=cls.BLOCKED_SYNC_STATES)
            .annotate(member_count=Count("members", distinct=True))
            .filter(member_count__gte=2)
            .annotate(
                has_invalid_member=Exists(cls._invalid_group_member_queryset())
            )
            .filter(has_invalid_member=False)
        )
        queryset = (
            Workspace.objects.filter(
                geodata_engine__is_active=True,
            )
            .exclude(sync_state__in=cls.BLOCKED_SYNC_STATES)
            .filter(Exists(visible_layer_exists) | Exists(visible_group_exists))
            .select_related("geodata_engine")
            .order_by("name", "-geodata_engine__is_default", "geodata_engine__name", "id")
        )
        return queryset.filter(geodata_engine=provider)

    @classmethod
    def _visible_layer_queryset(cls, *, provider_id) -> QuerySet[Layer]:
        provider = cls.get_visible_provider(provider_id=provider_id)
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
        return queryset.filter(workspace__geodata_engine=provider)

    @classmethod
    def _visible_group_queryset(cls, *, provider_id) -> QuerySet[LayerGroup]:
        provider = cls.get_visible_provider(provider_id=provider_id)
        invalid_member = cls._invalid_group_member_queryset()
        return (
            LayerGroup.objects.select_related(
                "workspace__geodata_engine",
            )
            .prefetch_related(
                "members__layer__store",
                "members__style_assignment__style__sprite_asset",
            )
            .filter(
                workspace__geodata_engine=provider,
                workspace__geodata_engine__is_active=True,
                is_public=True,
                publishing_state="PUBLISHED",
            )
            .exclude(sync_state__in=cls.BLOCKED_SYNC_STATES)
            .exclude(workspace__sync_state__in=cls.BLOCKED_SYNC_STATES)
            .annotate(member_count=Count("members", distinct=True))
            .filter(member_count__gte=2)
            .annotate(has_invalid_member=Exists(invalid_member))
            .filter(has_invalid_member=False)
            .order_by("workspace__name", "name", "id")
        )

    @classmethod
    def _invalid_group_member_queryset(cls) -> QuerySet[LayerGroupMember]:
        return LayerGroupMember.objects.filter(group_id=OuterRef("pk")).filter(
            Q(layer__is_public=False)
            | ~Q(layer__publishing_state="PUBLISHED")
            | Q(layer__sync_state__in=cls.BLOCKED_SYNC_STATES)
            | Q(layer__store__sync_state__in=cls.BLOCKED_SYNC_STATES)
            | Q(style_assignment__isnull=True)
            | Q(style_assignment__is_active=False)
            | ~Q(style_assignment__layer_id=F("layer_id"))
            | ~Q(style_assignment__style__validation_state="VALID")
            | (
                Q(layer__store__store_type="geotiff")
                & ~Q(style_assignment__style__format="sld")
            )
            | (
                ~Q(layer__store__store_type="geotiff")
                & (
                    ~Q(style_assignment__style__format="mbstyle")
                    | (
                        Q(render_layer_ids=[])
                        & Q(style_assignment__style_layer_ids=[])
                    )
                )
            )
        )
