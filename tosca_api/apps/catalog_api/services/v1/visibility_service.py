import uuid
from dataclasses import dataclass

from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet

from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    LayerGroup,
    LayerGroupMember,
    Workspace,
)


@dataclass(frozen=True)
class CatalogViewer:
    """Who is reading the catalog.

    * anonymous / no organization: public layers and groups in public
      workspaces only;
    * organization member: that, plus every published layer and group of
      their organization's workspaces (private ones included) -- the same
      rule GeoServer's workspace ACL applies (``security_sync``);
    * platform exempt (DJANGO_SUPERADMIN): everything published.
    """

    org_slug: str | None = None
    exempt: bool = False

    @classmethod
    def from_request(cls, request) -> "CatalogViewer":
        from tosca_api.apps.organizations.permissions import get_request_org_context

        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return ANONYMOUS
        _roles, org_slug, exempt = get_request_org_context(request)
        return cls(org_slug=org_slug or None, exempt=bool(exempt))

    @property
    def is_anonymous_view(self) -> bool:
        """Sees exactly what an anonymous visitor sees."""
        return not self.exempt and not self.org_slug


ANONYMOUS = CatalogViewer()


def visible_to(viewer: CatalogViewer, prefix: str = "") -> Q | None:
    """Filter for layers/groups (``prefix`` reaches one, e.g. ``"layer__"``)
    the viewer may see. ``None`` means no restriction (exempt)."""
    if viewer.exempt:
        return None
    public = Q(**{f"{prefix}is_public": True, f"{prefix}workspace__visibility": "public"})
    if viewer.org_slug:
        return public | Q(**{f"{prefix}workspace__organization__slug": viewer.org_slug})
    return public


def workspace_visible_to(viewer: CatalogViewer, prefix: str = "workspace__") -> Q | None:
    """Filter for things scoped to an optional workspace (styles): global ones,
    public workspaces, and the viewer's own organization's workspaces."""
    if viewer.exempt:
        return None
    visible = Q(**{f"{prefix[:-2]}__isnull": True}) | Q(**{f"{prefix}visibility": "public"})
    if viewer.org_slug:
        visible |= Q(**{f"{prefix}organization__slug": viewer.org_slug})
    return visible


def _restrict(queryset, condition: Q | None):
    return queryset if condition is None else queryset.filter(condition)


class CatalogVisibilityService:
    """Read helpers for the catalog visibility surface (see ``CatalogViewer``)."""

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
    def list_visible_workspaces(cls, *, provider_id, viewer: CatalogViewer = ANONYMOUS):
        queryset = cls._visible_workspace_queryset(provider_id=provider_id, viewer=viewer)
        return list(queryset)

    @classmethod
    def get_visible_workspace(cls, *, workspace_name: str, provider_id, viewer: CatalogViewer = ANONYMOUS):
        workspace = (
            cls._visible_workspace_queryset(provider_id=provider_id, viewer=viewer)
            .filter(name=workspace_name)
            .first()
        )
        if workspace is None:
            raise Workspace.DoesNotExist(f"Workspace '{workspace_name}' not found.")
        return workspace

    @classmethod
    def list_visible_layers(cls, *, provider_id, workspace_name: str | None = None, viewer: CatalogViewer = ANONYMOUS):
        queryset = cls._visible_layer_queryset(provider_id=provider_id, viewer=viewer)
        if workspace_name:
            workspace = cls.get_visible_workspace(
                workspace_name=workspace_name,
                provider_id=provider_id,
                viewer=viewer,
            )
            queryset = queryset.filter(workspace=workspace)
        return list(queryset)

    @classmethod
    def get_visible_layer(cls, *, workspace_name: str, layer_name: str, provider_id, viewer: CatalogViewer = ANONYMOUS):
        workspace = cls.get_visible_workspace(
            workspace_name=workspace_name,
            provider_id=provider_id,
            viewer=viewer,
        )
        layer = (
            cls._visible_layer_queryset(provider_id=provider_id, viewer=viewer)
            .filter(workspace=workspace, name=layer_name)
            .first()
        )
        if layer is None:
            raise Layer.DoesNotExist(
                f"Layer '{layer_name}' not found in workspace '{workspace_name}'."
        )
        return layer

    @classmethod
    def list_visible_groups(cls, *, provider_id, workspace_name: str | None = None, viewer: CatalogViewer = ANONYMOUS):
        queryset = cls._visible_group_queryset(provider_id=provider_id, viewer=viewer)
        if workspace_name:
            workspace = cls.get_visible_workspace(
                workspace_name=workspace_name,
                provider_id=provider_id,
                viewer=viewer,
            )
            queryset = queryset.filter(workspace=workspace)
        return list(queryset)

    @classmethod
    def get_visible_group(cls, *, workspace_name: str, group_name: str, provider_id, viewer: CatalogViewer = ANONYMOUS):
        workspace = cls.get_visible_workspace(
            workspace_name=workspace_name,
            provider_id=provider_id,
            viewer=viewer,
        )
        group = (
            cls._visible_group_queryset(provider_id=provider_id, viewer=viewer)
            .filter(workspace=workspace, name=group_name)
            .first()
        )
        if group is None:
            raise LayerGroup.DoesNotExist(
                f"Layer group '{group_name}' not found in workspace '{workspace_name}'."
            )
        return group

    @classmethod
    def _visible_workspace_queryset(cls, *, provider_id, viewer: CatalogViewer = ANONYMOUS) -> QuerySet[Workspace]:
        provider = cls.get_visible_provider(provider_id=provider_id)
        visible_layer_exists = (
            _restrict(Layer.objects.filter(
                workspace_id=OuterRef("pk"),
                publishing_state="PUBLISHED",
            ), visible_to(viewer))
            .exclude(sync_state__in=cls.BLOCKED_SYNC_STATES)
            .exclude(store__sync_state__in=cls.BLOCKED_SYNC_STATES)
        )
        visible_group_exists = (
            _restrict(LayerGroup.objects.filter(
                workspace_id=OuterRef("pk"),
                publishing_state="PUBLISHED",
            ), visible_to(viewer))
            .exclude(sync_state__in=cls.BLOCKED_SYNC_STATES)
            .annotate(member_count=Count("members", distinct=True))
            .filter(member_count__gte=2)
            .annotate(
                has_invalid_member=Exists(cls._invalid_group_member_queryset(viewer))
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
    def _visible_layer_queryset(cls, *, provider_id, viewer: CatalogViewer = ANONYMOUS) -> QuerySet[Layer]:
        provider = cls.get_visible_provider(provider_id=provider_id)
        queryset = (
            _restrict(Layer.objects.select_related("workspace", "workspace__geodata_engine")
            .prefetch_related("style_assignments__style")
            .filter(
                workspace__geodata_engine__is_active=True,
                publishing_state="PUBLISHED",
            ), visible_to(viewer))
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
    def _visible_group_queryset(cls, *, provider_id, viewer: CatalogViewer = ANONYMOUS) -> QuerySet[LayerGroup]:
        provider = cls.get_visible_provider(provider_id=provider_id)
        invalid_member = cls._invalid_group_member_queryset(viewer)
        return _restrict(
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
                publishing_state="PUBLISHED",
            ),
            visible_to(viewer),
        ).exclude(sync_state__in=cls.BLOCKED_SYNC_STATES).exclude(
            workspace__sync_state__in=cls.BLOCKED_SYNC_STATES
        ).annotate(member_count=Count("members", distinct=True)).filter(
            member_count__gte=2
        ).annotate(has_invalid_member=Exists(invalid_member)).filter(
            has_invalid_member=False
        ).order_by("workspace__name", "name", "id")

    @classmethod
    def _invalid_group_member_queryset(cls, viewer: CatalogViewer = ANONYMOUS) -> QuerySet[LayerGroupMember]:
        """Members that make a group unusable for ``viewer``: a member layer
        the viewer may not see, or a broken/unpublished/unstyled member."""
        member_visible = visible_to(viewer, "layer__")
        hidden_member = ~member_visible if member_visible is not None else Q(pk__in=[])
        return LayerGroupMember.objects.filter(group_id=OuterRef("pk")).filter(
            hidden_member
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
