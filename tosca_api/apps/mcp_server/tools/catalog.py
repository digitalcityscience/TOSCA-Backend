"""Catalog tools: what data TOSCA offers to the signed-in user.

Thin wrappers over ``CatalogVisibilityService``, the service behind
``/api/v1/catalog/``. A caller sees exactly what the catalog API shows them:
public layers, plus the published layers of their own organization.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from django.db import close_old_connections
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from tosca_api.apps.catalog_api.services.v1.visibility_service import CatalogVisibilityService
from tosca_api.apps.geodata_providers.services.queries import ProviderQueryService

from ..principal import McpPrincipal, current_principal

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
MAX_PAGE_SIZE = 100
SUMMARY_LENGTH = 300

_F = TypeVar("_F", bound=Callable[..., Any])


def database_tool(function: _F) -> _F:
    """Synchronous tools run in worker threads: give each call a usable
    database connection and hand it back afterwards, as Django does per request."""

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        close_old_connections()
        try:
            return function(*args, **kwargs)
        finally:
            close_old_connections()

    return wrapper  # type: ignore[return-value]


def _shorten(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= SUMMARY_LENGTH else f"{text[:SUMMARY_LENGTH].rstrip()}…"


def _kind(layer) -> str:
    return "raster" if layer.store.store_type == "geotiff" else "vector"


def _layer_summary(layer) -> dict[str, Any]:
    return {
        "provider_id": str(layer.workspace.geodata_engine_id),
        "workspace": layer.workspace.name,
        "name": layer.name,
        "title": layer.title or layer.name,
        "summary": _shorten(layer.description),
        "kind": _kind(layer),
        "geometry_type": layer.geometry_type,
    }


def _visible_layers(principal: McpPrincipal, provider_id: str | None, workspace: str | None) -> list:
    provider_ids = [provider_id] if provider_id else [item["id"] for item in ProviderQueryService.list_providers()]
    layers: list = []
    for current in provider_ids:
        try:
            layers.extend(
                CatalogVisibilityService.list_visible_layers(
                    provider_id=current, workspace_name=workspace or None, viewer=principal.viewer
                )
            )
        except Exception:
            # Unknown provider or a workspace this caller may not see: same
            # answer as the catalog API (not found), here an empty page.
            continue
    return layers


def list_workspaces_for(principal: McpPrincipal) -> dict[str, Any]:
    workspaces = []
    for provider in ProviderQueryService.list_providers():
        for workspace in CatalogVisibilityService.list_visible_workspaces(
            provider_id=provider["id"], viewer=principal.viewer
        ):
            workspaces.append({
                "provider_id": provider["id"],
                "provider": provider["name"],
                "name": workspace.name,
                "summary": _shorten(workspace.description),
                "visibility": workspace.visibility,
            })
    return {"workspaces": workspaces}


def search_layers_for(
    principal: McpPrincipal,
    *,
    query: str = "",
    workspace: str = "",
    provider_id: str = "",
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    layers = _visible_layers(principal, provider_id or None, workspace or None)
    words = query.lower().split()
    if words:
        layers = [
            layer for layer in layers
            if all(
                word in f"{layer.name} {layer.title} {layer.description} {layer.workspace.name}".lower()
                for word in words
            )
        ]
    page = layers[offset:offset + limit]
    return {
        "total": len(layers),
        "next_offset": offset + limit if offset + limit < len(layers) else None,
        "layers": [_layer_summary(layer) for layer in page],
    }


def describe_layer_for(principal: McpPrincipal, *, provider_id: str, workspace: str, name: str) -> dict[str, Any]:
    try:
        layer = CatalogVisibilityService.get_visible_layer(
            workspace_name=workspace, layer_name=name, provider_id=provider_id, viewer=principal.viewer
        )
    except Exception as exc:
        # Same answer for "does not exist" and "not yours", like the API's 404.
        raise ToolError(f"Layer '{workspace}:{name}' was not found in the catalog.") from exc
    styles = [
        {
            "name": assignment.style.name,
            "title": assignment.style.title or assignment.style.name,
            "default": assignment.role == "default",
        }
        for assignment in layer.style_assignments.all()
        if assignment.is_active
    ]
    return {
        **_layer_summary(layer),
        "description": " ".join((layer.description or "").split()),
        "srid": layer.srid,
        "extent_wgs84": layer.bounds,
        "attributes": layer.attributes or [],
        "styles": styles,
        "public": bool(layer.is_public and layer.workspace.visibility == "public"),
    }


def register(server: MCPServer) -> None:
    @server.tool(
        title="Who am I",
        description=(
            "Show which TOSCA account, organization and role this connection uses. "
            "Use it when the user asks why some data is missing or not allowed."
        ),
        annotations=READ_ONLY,
    )
    def whoami() -> dict[str, Any]:
        principal = current_principal()
        return {
            "username": principal.username,
            "organization": principal.org_slug,
            "role": principal.level,
            "platform_admin": principal.exempt,
        }

    @server.tool(
        title="List workspaces",
        description=(
            "List the data workspaces (thematic folders of layers) the user may see in the TOSCA catalog. "
            "Each has a provider_id and a name; pass both to search_layers to list its layers."
        ),
        annotations=READ_ONLY,
    )
    @database_tool
    def list_workspaces() -> dict[str, Any]:
        return list_workspaces_for(current_principal())

    @server.tool(
        title="Search catalog layers",
        description=(
            "Find map layers in the TOSCA catalog that the user may see. `query` matches words in the "
            "layer name, title or description (all words must match; leave empty to list everything). "
            "Results are paged: when `next_offset` is not null, call again with it as `offset`. "
            "Use describe_layer for attributes and extent, add_catalog_layer to show a layer on the map."
        ),
        annotations=READ_ONLY,
    )
    @database_tool
    def search_layers(
        query: Annotated[str, Field(description="Words to look for, e.g. 'bicycle parking'. Empty lists all.")] = "",
        workspace: Annotated[str, Field(description="Limit to one workspace name (from list_workspaces).")] = "",
        provider_id: Annotated[str, Field(description="Limit to one provider id.")] = "",
        limit: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE, description="Page size.")] = 25,
        offset: Annotated[int, Field(ge=0, description="Number of results to skip.")] = 0,
    ) -> dict[str, Any]:
        return search_layers_for(
            current_principal(), query=query, workspace=workspace, provider_id=provider_id,
            limit=limit, offset=offset,
        )

    @server.tool(
        title="Describe a catalog layer",
        description=(
            "Details of one catalog layer: description, geometry type, extent (WGS84 west, south, east, north), "
            "attribute names and types, and its styles. Take provider_id, workspace and name from search_layers."
        ),
        annotations=READ_ONLY,
    )
    @database_tool
    def describe_layer(
        provider_id: Annotated[str, Field(description="Provider id from search_layers.")],
        workspace: Annotated[str, Field(description="Workspace name from search_layers.")],
        name: Annotated[str, Field(description="Layer name from search_layers (not the title).")],
    ) -> dict[str, Any]:
        return describe_layer_for(current_principal(), provider_id=provider_id, workspace=workspace, name=name)
