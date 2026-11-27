"""Source tools: search further data catalogs besides this server's own.

The sources are listed in ``catalog_sources/`` (see :mod:`..sources`). They
are public catalogs read over HTTP; nothing here touches TOSCA's database.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import sources
from ..principal import current_principal

# Reads only, but from services outside TOSCA.
READ_REMOTE = ToolAnnotations(read_only_hint=True, open_world_hint=True)
MAX_RESULTS = 50


def list_sources_payload() -> dict[str, Any]:
    return {"sources": [source.summary() for source in sources.load_sources()]}


def search_sources_payload(*, query: str, source: str = "", limit: int = 20) -> dict[str, Any]:
    all_sources = sources.load_sources()
    try:
        selected = [sources.get_source(source, all_sources)] if source else all_sources
    except sources.SourceError as exc:
        raise ToolError(str(exc)) from exc
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for current in selected:
        try:
            hits = sources.search(current, query)
        except sources.SourceError as exc:
            # One unreachable catalog must not hide the others' answers.
            errors.append({"source": current.id, "error": str(exc)})
            continue
        results.append({"source": current.id, "title": current.title, "total": len(hits), "items": hits[:limit]})
    return {"query": query, "results": results, "errors": errors}


def describe_source_item_payload(*, source: str, item_id: str) -> dict[str, Any]:
    try:
        return sources.describe(sources.get_source(source), item_id)
    except sources.SourceError as exc:
        raise ToolError(str(exc)) from exc


def register(server: MCPServer) -> None:
    @server.tool(
        title="List data sources",
        description=(
            "List further data catalogs you can search besides the TOSCA catalog of this server: id, title, "
            "description, covered area (extent.bbox, WGS84 west, south, east, north), protocol and notes. "
            "Read each source's `notes` before searching it. Pick sources whose extent covers the place "
            "the user asks about."
        ),
        annotations=READ_REMOTE,
    )
    def list_sources() -> dict[str, Any]:
        current_principal()
        return list_sources_payload()

    @server.tool(
        title="Search data sources",
        description=(
            "Search the catalogs from list_sources, in their priority order. Use it when search_layers finds "
            "nothing, or when the user asks for data TOSCA itself may not hold. `query` words must all match "
            "the title or description; the sources' own language applies (see notes). Results are grouped by "
            "source; `errors` lists sources that could not be read. Then call describe_source_item."
        ),
        annotations=READ_REMOTE,
    )
    def search_sources(
        query: Annotated[str, Field(description="Words to look for, e.g. 'Gebäude' or 'bezirke'.")],
        source: Annotated[str, Field(description="Limit to one source id from list_sources. Empty searches all.")] = "",
        limit: Annotated[int, Field(ge=1, le=MAX_RESULTS, description="Maximum items per source.")] = 20,
    ) -> dict[str, Any]:
        current_principal()
        return search_sources_payload(query=query, source=source, limit=limit)

    @server.tool(
        title="Describe a source item",
        description=(
            "Details of one item found by search_sources: description, extent and attributes for a TOSCA "
            "layer, the collections of an OGC API dataset with their GeoJSON items URL, or the sensors of a "
            "SensorThings layer. When the result (or one of its collections) has `show_on_map`, call that tool "
            "with those arguments if the user wants it on the map."
        ),
        annotations=READ_REMOTE,
    )
    def describe_source_item(
        source: Annotated[str, Field(description="Source id from search_sources.")],
        item_id: Annotated[str, Field(description="item_id from search_sources.")],
    ) -> dict[str, Any]:
        current_principal()
        return describe_source_item_payload(source=source, item_id=item_id)
