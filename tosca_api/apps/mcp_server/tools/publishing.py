"""Write tools for layers, styles and layer groups.

Thin wrappers over the services the Django admin and the REST API use
(``LayerService``, ``LayerUpdateService``, ``StyleService``,
``LayerGroupService``). Every write goes through ``authorization.authorize``;
the role rules there are still OPEN (see that module).

Targets are looked up among the caller's own organization's records (all of
them for platform admins). A record of another organization is "not found",
as in the catalog.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from django.db import transaction
from django.db.models import Q, QuerySet
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from tosca_api.apps.catalog_api.services.v1.visibility_service import workspace_visible_to
from tosca_api.apps.geodata_providers.api.serializers import LayerSerializer
from tosca_api.apps.geodata_providers.engine_factory import EngineClientFactory
from tosca_api.apps.geodata_providers.models import (
    Layer,
    LayerGroup,
    LayerStyleAssignment,
    Store,
    Style,
    Workspace,
)
from tosca_api.apps.geodata_providers.postgis_inspector import PostGISInspectorError, get_geometry_tables
from tosca_api.apps.geodata_providers.services.commands.layer_group_service import LayerGroupService
from tosca_api.apps.geodata_providers.services.commands.layer_service import LayerService, LayerUpdateService
from tosca_api.apps.geodata_providers.services.commands.style_service import StyleService

from ..authorization import WriteAction, authorize, django_user
from ..principal import McpPrincipal, current_principal
from .catalog import MAX_PAGE_SIZE, READ_ONLY, _shorten, database_tool

logger = logging.getLogger(__name__)

MAX_STYLE_CHARACTERS = 200_000

WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
REMOVES = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)

ProviderId = Annotated[str, Field(description="Provider id (from list_workspaces or list_editable_layers).")]
WorkspaceName = Annotated[str, Field(description="Workspace name.")]
LayerName = Annotated[str, Field(description="Layer name (not the title).")]


# -- lookups -------------------------------------------------------------------


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise ToolError(f"`{what}` must be an id as list_workspaces returns it.") from exc


def _own(queryset: QuerySet, principal: McpPrincipal, org_path: str) -> QuerySet:
    """Records of the caller's organization; everything for platform admins."""
    if principal.exempt:
        return queryset
    if not principal.org_slug:
        return queryset.none()
    return queryset.filter(**{f"{org_path}__slug": principal.org_slug})


def editable_layers(principal: McpPrincipal) -> QuerySet[Layer]:
    layers = Layer.objects.select_related("workspace__geodata_engine", "workspace__organization", "store")
    return _own(layers, principal, "workspace__organization")


def find_workspace(principal: McpPrincipal, provider_id: str, workspace: str) -> Workspace:
    found = _own(
        Workspace.objects.select_related("geodata_engine", "organization"), principal, "organization"
    ).filter(geodata_engine_id=_uuid(provider_id, "provider_id"), name=workspace).first()
    if found is None:
        raise ToolError(f"Workspace '{workspace}' was not found among your organization's workspaces.")
    return found


def find_layer(principal: McpPrincipal, provider_id: str, workspace: str, name: str) -> Layer:
    layer = editable_layers(principal).filter(
        workspace__geodata_engine_id=_uuid(provider_id, "provider_id"), workspace__name=workspace, name=name
    ).first()
    if layer is None:
        raise ToolError(f"Layer '{workspace}:{name}' was not found among your organization's layers.")
    return layer


def layer_result(layer: Layer) -> dict[str, Any]:
    result: dict[str, Any] = {
        "provider_id": str(layer.workspace.geodata_engine_id),
        "workspace": layer.workspace.name,
        "name": layer.name,
        "title": layer.title or layer.name,
        "summary": _shorten(layer.description),
        "state": layer.publishing_state,
        "public": bool(layer.is_public),
        "store": layer.store.name,
        "table": layer.table_name,
    }
    if layer.publishing_state == "PUBLISHED":
        result["show_on_map"] = {"tool": "add_catalog_layer", "arguments": {
            "providerId": result["provider_id"], "workspace": layer.workspace.name, "layerName": layer.name,
        }}
    return result


def _service_failure(result: dict[str, Any], fallback: str) -> ToolError:
    return ToolError(str(result.get("error") or result.get("message") or fallback))


# -- layers --------------------------------------------------------------------


def list_editable_layers_for(
    principal: McpPrincipal, *, query: str = "", provider_id: str = "", workspace: str = "",
    state: str = "", limit: int = 25, offset: int = 0,
) -> dict[str, Any]:
    layers = editable_layers(principal).order_by("workspace__name", "name")
    if provider_id:
        layers = layers.filter(workspace__geodata_engine_id=_uuid(provider_id, "provider_id"))
    if workspace:
        layers = layers.filter(workspace__name=workspace)
    if state:
        layers = layers.filter(publishing_state=state)
    for word in query.split():
        layers = layers.filter(Q(name__icontains=word) | Q(title__icontains=word) | Q(description__icontains=word))
    total = layers.count()
    return {
        "total": total,
        "next_offset": offset + limit if offset + limit < total else None,
        "layers": [layer_result(layer) for layer in layers[offset:offset + limit]],
    }


def list_publishable_tables_for(principal: McpPrincipal, *, provider_id: str, workspace: str) -> dict[str, Any]:
    target = find_workspace(principal, provider_id, workspace)
    published = {
        (store_id, table)
        for store_id, table in Layer.objects.filter(workspace=target).values_list("store_id", "table_name")
    }
    stores = []
    for store in Store.objects.filter(workspace=target, store_type=Store.StoreType.POSTGIS).order_by("name"):
        entry: dict[str, Any] = {"store": store.name}
        try:
            tables = get_geometry_tables(
                host=store.host, port=store.port or 5432, database=store.database, username=store.username,
                password=store.decrypted_password, schema=store.schema or "public",
            )
        except PostGISInspectorError:
            # The message names the database host; the assistant gets a plain one.
            logger.warning("MCP: could not list tables of store %s", store.pk, exc_info=True)
            entry["error"] = "The tables of this store could not be read."
            stores.append(entry)
            continue
        entry["tables"] = [
            {
                "table": table["table_name"],
                "geometry_type": table["geometry_type"],
                "srid": table["srid"],
                "has_layer": (store.id, table["table_name"]) in published,
            }
            for table in tables
        ]
        stores.append(entry)
    return {"provider_id": str(target.geodata_engine_id), "workspace": target.name, "stores": stores}


def _sync_workspace(workspace: Workspace, user) -> list[str]:
    """Pull the workspace's styles and layers from GeoServer after a publish, as the admin's publish page does."""
    try:
        service = EngineClientFactory.create_sync_service(workspace.geodata_engine)
        service.sync_styles_for_scope(workspace, created_by=user)
        result = service.sync_layers_for_workspace(workspace, created_by=user)
    except Exception:
        logger.warning("MCP: workspace sync after publish failed", exc_info=True)
        return ["The layer was published, but reading its styles back from GeoServer failed."]
    return [str(error) for error in (result.get("errors") or [])][:3]


def publish_layer_for(
    principal: McpPrincipal, *, provider_id: str, workspace: str, name: str, from_layer: str = "",
    store: str = "", table: str = "", title: str = "", description: str = "",
) -> dict[str, Any]:
    target = find_workspace(principal, provider_id, workspace)
    authorize(principal, WriteAction.PUBLISH_LAYER, workspace=target)
    user = django_user(principal)
    existing = editable_layers(principal).filter(workspace=target, name=name).first()

    if existing is not None and not (from_layer or table):
        result = LayerService.publish_existing_layer(existing)
        if not result.get("success"):
            raise _service_failure(result, "Publishing failed.")
        layer = Layer.objects.get(pk=existing.pk)
        return {"published": True, "already_published": bool(result.get("idempotent")), "layer": layer_result(layer)}

    if from_layer:
        source = editable_layers(principal).filter(workspace=target, name=from_layer).first()
        if source is None:
            raise ToolError(f"Layer '{workspace}:{from_layer}' was not found among your organization's layers.")
        source_store, source_table = source.store, source.table_name
        geometry = {"geometry_column": source.geometry_column, "geometry_type": source.geometry_type, "srid": source.srid}
        title = title or source.title
        description = description or source.description
    elif table:
        source_store = Store.objects.filter(workspace=target, name=store).first() if store else None
        if source_store is None:
            raise ToolError("Give `store` with `table` (list_publishable_tables names both).")
        try:
            tables = get_geometry_tables(
                host=source_store.host, port=source_store.port or 5432, database=source_store.database,
                username=source_store.username, password=source_store.decrypted_password,
                schema=source_store.schema or "public",
            )
        except PostGISInspectorError as exc:
            raise ToolError("The tables of this store could not be read.") from exc
        match = next((item for item in tables if item["table_name"] == table), None)
        if match is None:
            raise ToolError(f"Store '{store}' has no table '{table}' with a geometry column.")
        source_table = table
        geometry = {"geometry_column": match["geometry_column"], "geometry_type": match["geometry_type"], "srid": match["srid"]}
    else:
        raise ToolError(
            f"There is no layer '{workspace}:{name}' to publish. To publish under a new name, give `from_layer` "
            "(copy an existing layer's table) or `store` and `table` (list_publishable_tables)."
        )

    if existing is not None:
        raise ToolError(f"A layer named '{name}' already exists in '{workspace}'. Choose another name.")
    result = LayerService.publish_postgis(
        workspace=target, store=source_store, table_name=source_table, layer_name=name,
        title=title or name, description=description, user=user, **geometry,
    )
    if not result.get("success"):
        raise _service_failure(result, "Publishing failed.")
    warnings = _sync_workspace(target, user)
    layer = Layer.objects.select_related("workspace", "store").get(pk=result["resource"].pk)
    return {"published": True, "created": bool(result.get("created")), "layer": layer_result(layer), "warnings": warnings}


def unpublish_layer_for(principal: McpPrincipal, *, provider_id: str, workspace: str, name: str) -> dict[str, Any]:
    layer = find_layer(principal, provider_id, workspace, name)
    authorize(principal, WriteAction.UNPUBLISH_LAYER, workspace=layer.workspace)
    result = LayerService.unpublish_layer(layer)
    if not result.get("success"):
        raise _service_failure(result, "Unpublishing failed.")
    return {"unpublished": True, "layer": layer_result(Layer.objects.get(pk=layer.pk))}


def update_layer_for(
    principal: McpPrincipal, *, provider_id: str, workspace: str, name: str,
    title: str | None = None, description: str | None = None,
) -> dict[str, Any]:
    layer = find_layer(principal, provider_id, workspace, name)
    authorize(principal, WriteAction.UPDATE_LAYER, workspace=layer.workspace)
    fields = {key: value for key, value in (("title", title), ("description", description)) if value is not None}
    if not fields:
        raise ToolError("Give `title`, `description` or both.")
    result = LayerUpdateService.apply(layer=layer, fields=fields, serializer_class=LayerSerializer)
    if not result.get("success"):
        body = result.get("body") or {}
        raise ToolError(str(body.get("error") or body.get("detail") or "The layer could not be updated."))
    return {"updated": True, "layer": layer_result(Layer.objects.get(pk=layer.pk))}


# -- styles --------------------------------------------------------------------


def _visible_styles(principal: McpPrincipal) -> QuerySet[Style]:
    """Styles the caller may read: global ones, public workspaces', and their own organization's."""
    styles = Style.objects.select_related("geodata_engine", "workspace__organization")
    visible = workspace_visible_to(principal.viewer)
    return styles if visible is None else styles.filter(visible)


def _find_style(queryset: QuerySet[Style], provider_id: str, style: str) -> Style:
    found = queryset.filter(geodata_engine_id=_uuid(provider_id, "provider_id"), name=style).order_by(
        "workspace__name"
    )
    count = found.count()
    if count == 0:
        raise ToolError(f"Style '{style}' was not found.")
    if count > 1 and ":" not in style:
        names = ", ".join(item.qualified_name for item in found)
        raise ToolError(f"Several styles are named '{style}': {names}. Give the workspace with workspace:name.")
    return found.first()


def _split_style(style: str) -> tuple[str | None, str]:
    workspace, _, name = style.rpartition(":")
    return (workspace or None), name


def find_visible_style(principal: McpPrincipal, provider_id: str, style: str) -> Style:
    workspace, name = _split_style(style)
    styles = _visible_styles(principal)
    if workspace:
        styles = styles.filter(workspace__name=workspace)
    return _find_style(styles, provider_id, name)


def describe_style_for(principal: McpPrincipal, *, provider_id: str, style: str) -> dict[str, Any]:
    found = find_visible_style(principal, provider_id, style)
    content = found.file_content or ""
    return {
        "provider_id": str(found.geodata_engine_id),
        "name": found.name,
        "qualified_name": found.qualified_name,
        "title": found.title or found.name,
        "description": found.description,
        "format": found.format,
        "scope": "global" if found.is_global else "workspace",
        "workspace": found.workspace.name if found.workspace else None,
        "valid": found.validation_state == Style.ValidationState.VALID,
        "remote_state": found.remote_state,
        "used_by_layers": list(
            found.layer_assignments.filter(is_active=True)
            .values_list("layer__workspace__name", "layer__name", "role")[:50]
        ),
        "content": content[:MAX_STYLE_CHARACTERS],
        "content_truncated": len(content) > MAX_STYLE_CHARACTERS,
    }


def update_style_for(
    principal: McpPrincipal, *, provider_id: str, style: str, content: str | None = None,
    title: str | None = None, description: str | None = None,
) -> dict[str, Any]:
    found = find_visible_style(principal, provider_id, style)
    if found.workspace is None and not principal.exempt:
        raise ToolError(f"'{found.name}' is a global style shared by all organizations; only platform admins change it.")
    if found.workspace is not None:
        # A public workspace of another organization is readable, not writable.
        own = find_workspace(principal, str(found.geodata_engine_id), found.workspace.name)
        authorize(principal, WriteAction.UPDATE_STYLE, workspace=own)
    else:
        authorize(principal, WriteAction.UPDATE_STYLE)
    if content is None and title is None and description is None:
        raise ToolError("Give `content`, `title` or `description`.")
    if content is not None and len(content) > MAX_STYLE_CHARACTERS:
        raise ToolError(f"The style is longer than {MAX_STYLE_CHARACTERS} characters.")
    result = StyleService.update_content(found, content=content, title=title, description=description)
    if not result.get("saved"):
        raise ToolError("The style was not saved: " + " | ".join(map(str, result.get("errors") or ["invalid"])))
    answer = {"saved": True, "uploaded": bool(result.get("success")), "style": found.qualified_name}
    if not result.get("success"):
        answer["upload_error"] = result.get("error")
    return answer


def set_layer_default_style_for(
    principal: McpPrincipal, *, provider_id: str, workspace: str, name: str, style: str,
) -> dict[str, Any]:
    layer = find_layer(principal, provider_id, workspace, name)
    authorize(principal, WriteAction.SET_LAYER_DEFAULT_STYLE, workspace=layer.workspace)
    target = find_visible_style(principal, provider_id, style)
    if target.validation_state != Style.ValidationState.VALID:
        raise ToolError(f"Style '{target.qualified_name}' is not valid and cannot be used.")
    if target.workspace_id not in (None, layer.workspace_id):
        raise ToolError(f"Style '{target.qualified_name}' belongs to another workspace.")
    user = django_user(principal)
    with transaction.atomic():
        assignments = LayerStyleAssignment.objects.select_for_update().filter(layer=layer)
        current = assignments.filter(role=LayerStyleAssignment.Role.DEFAULT, is_active=True).first()
        if current is not None and current.style_id == target.id:
            return {"changed": False, "layer": layer_result(layer), "default_style": target.qualified_name}
        if current is not None:
            # Release the default first: one active default per layer is a database constraint.
            demoted = assignments.filter(style=current.style, role=LayerStyleAssignment.Role.ALTERNATE).first()
            if demoted is None:
                current.role = LayerStyleAssignment.Role.ALTERNATE
                current.save()
            else:
                current.delete()
                demoted.is_active = True
                demoted.save()
        chosen = assignments.filter(style=target, role=LayerStyleAssignment.Role.ALTERNATE).first()
        if chosen is not None:
            chosen.role = LayerStyleAssignment.Role.DEFAULT
            chosen.is_active = True
            chosen.save()
        else:
            LayerStyleAssignment.objects.update_or_create(
                layer=layer, style=target, role=LayerStyleAssignment.Role.DEFAULT,
                defaults={"is_active": True, "created_by": user},
            )
    return {"changed": True, "layer": layer_result(layer), "default_style": target.qualified_name}


# -- layer groups --------------------------------------------------------------


def _find_group(principal: McpPrincipal, provider_id: str, workspace: str, name: str) -> LayerGroup:
    groups = _own(
        LayerGroup.objects.select_related("workspace__organization", "workspace__geodata_engine"),
        principal, "workspace__organization",
    )
    group = groups.filter(
        workspace__geodata_engine_id=_uuid(provider_id, "provider_id"), workspace__name=workspace, name=name
    ).first()
    if group is None:
        raise ToolError(f"Layer group '{workspace}:{name}' was not found among your organization's groups.")
    return group


def set_group_publication_for(
    principal: McpPrincipal, *, provider_id: str, workspace: str, name: str, published: bool,
) -> dict[str, Any]:
    group = _find_group(principal, provider_id, workspace, name)
    action = WriteAction.PUBLISH_LAYER_GROUP if published else WriteAction.UNPUBLISH_LAYER_GROUP
    authorize(principal, action, workspace=group.workspace)
    group.publishing_state = LayerGroup.PublishingState.PUBLISHED if published else LayerGroup.PublishingState.UNPUBLISHED
    group.save(update_fields=["publishing_state", "updated_at"])
    outcome = LayerGroupService.reconcile_publication(group=group)
    group.refresh_from_db()
    answer: dict[str, Any] = {
        "provider_id": provider_id, "workspace": workspace, "name": group.name,
        "state": group.publishing_state, "ok": outcome.ok,
    }
    if not outcome.ok:
        answer["error"] = outcome.error
    elif published:
        answer["show_on_map"] = {"tool": "add_catalog_layer_group", "arguments": {
            "providerId": provider_id, "workspace": workspace, "groupName": group.name,
        }}
    return answer


# -- registration --------------------------------------------------------------


def register(server: MCPServer) -> None:
    @server.tool(
        title="List my organization's layers",
        description=(
            "Layers of the user's organization in every state (DRAFT, PUBLISHED, UNPUBLISHED, FAILED), for "
            "publish_layer, unpublish_layer, update_layer and set_layer_default_style. search_layers shows only "
            "what the catalog publishes. Paged like search_layers."
        ),
        annotations=READ_ONLY,
    )
    @database_tool
    def list_editable_layers(
        query: Annotated[str, Field(description="Words in name, title or description; empty lists all.")] = "",
        provider_id: Annotated[str, Field(description="Limit to one provider id.")] = "",
        workspace: Annotated[str, Field(description="Limit to one workspace name.")] = "",
        state: Annotated[str, Field(description="DRAFT, PUBLISHED, UNPUBLISHED or FAILED; empty for all.")] = "",
        limit: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)] = 25,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        return list_editable_layers_for(
            current_principal(), query=query, provider_id=provider_id, workspace=workspace, state=state,
            limit=limit, offset=offset,
        )

    @server.tool(
        title="List tables that can be published",
        description=(
            "The database tables with a geometry column in a workspace's PostGIS stores, and whether a layer "
            "already uses each. Pass store and table to publish_layer to publish one as a new layer."
        ),
        annotations=READ_ONLY,
    )
    @database_tool
    def list_publishable_tables(provider_id: ProviderId, workspace: WorkspaceName) -> dict[str, Any]:
        return list_publishable_tables_for(current_principal(), provider_id=provider_id, workspace=workspace)

    @server.tool(
        title="Publish a layer",
        description=(
            "Publish a layer on GeoServer so it appears in the catalog and on maps. Three ways: (1) `name` of "
            "an existing unpublished layer; (2) a new `name` plus `from_layer`: the same data as an existing "
            "layer under a new name; (3) a new `name` plus `store` and `table` from list_publishable_tables. "
            "Ask the user before publishing."
        ),
        annotations=WRITES,
    )
    @database_tool
    def publish_layer(
        provider_id: ProviderId,
        workspace: WorkspaceName,
        name: Annotated[str, Field(description="Layer name to publish (an existing one, or the new name).")],
        from_layer: Annotated[str, Field(description="Existing layer whose table is published under `name`.")] = "",
        store: Annotated[str, Field(description="Store name, with `table`.")] = "",
        table: Annotated[str, Field(description="Table name, with `store`.")] = "",
        title: Annotated[str, Field(description="Title of a new layer.")] = "",
        description: Annotated[str, Field(description="Description of a new layer.")] = "",
    ) -> dict[str, Any]:
        return publish_layer_for(
            current_principal(), provider_id=provider_id, workspace=workspace, name=name, from_layer=from_layer,
            store=store, table=table, title=title, description=description,
        )

    @server.tool(
        title="Unpublish a layer",
        description=(
            "Remove a layer from GeoServer and the catalog. The data and the layer record stay; publish_layer "
            "brings it back. Maps and geostories that show it lose it. Ask the user first."
        ),
        annotations=REMOVES,
    )
    @database_tool
    def unpublish_layer(provider_id: ProviderId, workspace: WorkspaceName, name: LayerName) -> dict[str, Any]:
        return unpublish_layer_for(current_principal(), provider_id=provider_id, workspace=workspace, name=name)

    @server.tool(
        title="Change a layer's title or description",
        description="Change the title and/or description of one of the organization's layers (GeoServer too, if published).",
        annotations=WRITES,
    )
    @database_tool
    def update_layer(
        provider_id: ProviderId,
        workspace: WorkspaceName,
        name: LayerName,
        title: Annotated[str | None, Field(description="New title.")] = None,
        description: Annotated[str | None, Field(description="New description (plain text).")] = None,
    ) -> dict[str, Any]:
        return update_layer_for(
            current_principal(), provider_id=provider_id, workspace=workspace, name=name,
            title=title, description=description,
        )

    @server.tool(
        title="Read a style",
        description=(
            "A map style: format (MBStyle JSON or SLD), the layers using it, and its content. Style names come "
            "from describe_layer; write workspace:name when several workspaces have one of that name."
        ),
        annotations=READ_ONLY,
    )
    @database_tool
    def describe_style(
        provider_id: ProviderId,
        style: Annotated[str, Field(description="Style name, or workspace:name.")],
    ) -> dict[str, Any]:
        return describe_style_for(current_principal(), provider_id=provider_id, style=style)

    @server.tool(
        title="Change a style",
        description=(
            "Replace a style's content (same format as describe_style shows) and/or its title or description. "
            "The content is validated, saved and uploaded to GeoServer; every layer using the style changes. "
            "Read it with describe_style first and ask the user before saving."
        ),
        annotations=WRITES,
    )
    @database_tool
    def update_style(
        provider_id: ProviderId,
        style: Annotated[str, Field(description="Style name, or workspace:name.")],
        content: Annotated[str | None, Field(description="Full new content: MBStyle JSON text or SLD XML.")] = None,
        title: Annotated[str | None, Field(description="New title.")] = None,
        description: Annotated[str | None, Field(description="New description.")] = None,
    ) -> dict[str, Any]:
        return update_style_for(
            current_principal(), provider_id=provider_id, style=style, content=content,
            title=title, description=description,
        )

    @server.tool(
        title="Set a layer's default style",
        description=(
            "Make a style the default style of a layer (the one maps use first). The previous default stays "
            "available as an alternate style."
        ),
        annotations=WRITES,
    )
    @database_tool
    def set_layer_default_style(
        provider_id: ProviderId,
        workspace: WorkspaceName,
        name: LayerName,
        style: Annotated[str, Field(description="Style name, or workspace:name.")],
    ) -> dict[str, Any]:
        return set_layer_default_style_for(
            current_principal(), provider_id=provider_id, workspace=workspace, name=name, style=style
        )

    @server.tool(
        title="Publish a layer group",
        description=(
            "Publish an existing layer group of the organization (its members are checked first). Groups and "
            "their members are put together in the TOSCA admin."
        ),
        annotations=WRITES,
    )
    @database_tool
    def publish_layer_group(
        provider_id: ProviderId,
        workspace: WorkspaceName,
        name: Annotated[str, Field(description="Group name.")],
    ) -> dict[str, Any]:
        return set_group_publication_for(
            current_principal(), provider_id=provider_id, workspace=workspace, name=name, published=True
        )

    @server.tool(
        title="Unpublish a layer group",
        description="Take a layer group out of the catalog. Ask the user first.",
        annotations=REMOVES,
    )
    @database_tool
    def unpublish_layer_group(
        provider_id: ProviderId,
        workspace: WorkspaceName,
        name: Annotated[str, Field(description="Group name.")],
    ) -> dict[str, Any]:
        return set_group_publication_for(
            current_principal(), provider_id=provider_id, workspace=workspace, name=name, published=False
        )
