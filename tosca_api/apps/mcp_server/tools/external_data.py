"""Fetch features from a registered OGC API Features source on the server.

The assistant may read external data without putting it on the map first, and
large GeoJSON never has to pass through the conversation:

1. ``fetch_ogc_features`` reads a collection of a registered source
   (``catalog_sources/``, protocol ``ogc-api-features``) through the guarded
   client of ``external_catalog.remote`` (registered host only, public
   addresses, bounded time and size). The server keeps the result for an hour,
   for the calling user only, and answers with a reference and a summary.
2. ``get_fetched_features`` pages through the kept features.
3. ``show_fetched_features`` puts them on the user's map (``addGeoJsonLayer``
   over the bridge), without the model sending the GeoJSON.

Only registered sources can be read: there is no tool that takes a URL.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable
from typing import Annotated, Any
from urllib.parse import urlsplit

from django.core.cache import cache
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from tosca_api.apps.external_catalog import remote

from .. import sources
from ..map_commands import MapCommand
from ..principal import McpPrincipal, current_principal
from .catalog import READ_ONLY
from .map import send_map_command

KEEP_SECONDS = 60 * 60
PAGE_SIZE = 500
DEFAULT_LIMIT = 1000
MAX_LIMIT = 10_000
MAX_KEPT_BYTES = 20_000_000
MAX_MAP_BYTES = 5_000_000
MAX_EXAMPLES = 3
PREVIEW_FEATURES = 5
MAX_FEATURE_PAGE = 500
MAX_PAGE_BYTES = 1_000_000

FETCHES = ToolAnnotations(read_only_hint=True, open_world_hint=True)
CHANGES_MAP = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


def _key(principal: McpPrincipal, ref: str) -> str:
    return f"mcp_fetched:{principal.sub}:{ref}"


def keep(principal: McpPrincipal, data: dict[str, Any]) -> str:
    ref = f"fetch-{secrets.token_urlsafe(9)}"
    cache.set(_key(principal, ref), data, KEEP_SECONDS)
    return ref


def kept(principal: McpPrincipal, ref: str) -> dict[str, Any]:
    data = cache.get(_key(principal, ref)) if isinstance(ref, str) and ref.startswith("fetch-") else None
    if data is None:
        raise ToolError("Unknown or expired reference. Call fetch_ogc_features again.")
    return data


# -- summary -------------------------------------------------------------------


def _positions(coordinates: Any) -> Iterable[tuple[float, float]]:
    if isinstance(coordinates, list) and len(coordinates) >= 2 and all(isinstance(v, int | float) for v in coordinates[:2]):
        yield float(coordinates[0]), float(coordinates[1])
    elif isinstance(coordinates, list):
        for item in coordinates:
            yield from _positions(item)


def _geometry_positions(geometry: Any) -> Iterable[tuple[float, float]]:
    if not isinstance(geometry, dict):
        return
    if geometry.get("type") == "GeometryCollection":
        for part in geometry.get("geometries") or []:
            yield from _geometry_positions(part)
    else:
        yield from _positions(geometry.get("coordinates"))


def bbox_of(features: list[dict[str, Any]]) -> list[float] | None:
    west = south = float("inf")
    east = north = float("-inf")
    for feature in features:
        for x, y in _geometry_positions(feature.get("geometry")):
            west, south, east, north = min(west, x), min(south, y), max(east, x), max(north, y)
    return None if west == float("inf") else [west, south, east, north]


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "text"
    return "object" if isinstance(value, dict | list) else "null"


def attribute_summary(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for feature in features:
        for name, value in (feature.get("properties") or {}).items():
            entry = summary.setdefault(name, {"name": name, "types": set(), "examples": []})
            entry["types"].add(_type_name(value))
            if value is not None and not isinstance(value, dict | list) and value not in entry["examples"] \
                    and len(entry["examples"]) < MAX_EXAMPLES:
                entry["examples"].append(value if not isinstance(value, str) else value[:80])
    return [
        {"name": entry["name"], "type": "/".join(sorted(entry["types"] - {"null"}) or ["null"]), "examples": entry["examples"]}
        for entry in summary.values()
    ]


def summary_of(ref: str, data: dict[str, Any]) -> dict[str, Any]:
    features = data["features"]
    return {
        "ref": ref,
        "source": data["source"],
        "dataset": data["dataset"],
        "collection": data["collection"],
        "returned": len(features),
        "number_matched": data.get("number_matched"),
        "complete": data["complete"],
        "bbox": bbox_of(features),
        "geometry_types": sorted({str((f.get("geometry") or {}).get("type")) for f in features}),
        "attributes": attribute_summary(features),
        "preview": [feature.get("properties") or {} for feature in features[:PREVIEW_FEATURES]],
        "kept_for_minutes": KEEP_SECONDS // 60,
    }


# -- fetching ------------------------------------------------------------------


def _next_link(page: dict[str, Any]) -> str | None:
    for link in page.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "next" and isinstance(link.get("href"), str):
            return link["href"]
    return None


def fetch_collection(
    source: sources.CatalogSource, *, dataset_id: str, collection_id: str, limit: int,
    bbox: list[float] | None, properties: dict[str, str] | None,
) -> dict[str, Any]:
    if source.protocol != sources.OGC_API_FEATURES:
        raise ToolError(f"Source '{source.id}' is not an OGC API Features source.")
    service = sources._service(source)
    datasets = {item["id"]: item for item in sources._read(source, remote.ogc_datasets, service)}
    if dataset_id not in datasets:
        raise ToolError(f"{source.title}: no dataset '{dataset_id}'. Use an item_id from search_sources.")
    collections = {item["id"]: item for item in sources._read(source, remote.ogc_collections, service, dataset_id)}
    if collection_id not in collections:
        raise ToolError(f"Dataset '{dataset_id}' has no collection '{collection_id}' (describe_source_item lists them).")
    if not collections[collection_id]["geojson"]:
        raise ToolError(f"Collection '{collection_id}' does not offer GeoJSON.")

    params: dict[str, Any] | None = {"f": "json", "limit": min(PAGE_SIZE, limit)}
    if bbox is not None:
        params["bbox"] = ",".join(str(value) for value in bbox)
    for name, value in (properties or {}).items():
        if name in {"f", "limit", "bbox", "offset"}:
            raise ToolError(f"'{name}' cannot be used as a property filter.")
        params[name] = value
    url: str | None = f"{datasets[dataset_id]['landing_page_url']}/collections/{collection_id}/items"
    features: list[dict[str, Any]] = []
    number_matched = None
    size = 0
    while url and len(features) < limit:
        page = sources._read(source, remote.fetch_json, service, url, params)
        if not isinstance(page, dict) or not isinstance(page.get("features"), list):
            raise ToolError(f"{source.title}: the collection did not answer with GeoJSON features.")
        number_matched = page.get("numberMatched", number_matched)
        size += len(json.dumps(page["features"], separators=(",", ":")))
        if size > MAX_KEPT_BYTES:
            raise ToolError("The result is larger than 20 MB. Narrow it with `bbox` or `properties`, or lower `limit`.")
        features.extend(feature for feature in page["features"] if isinstance(feature, dict))
        url = _next_link(page)
        # The next link carries its own query; it must stay on the source's host (remote checks it too).
        if url and urlsplit(url).hostname != urlsplit(source.url).hostname:
            url = None
        params = None
    return {
        "source": source.id,
        "dataset": dataset_id,
        "collection": collection_id,
        "title": collections[collection_id]["title"],
        "features": features[:limit],
        "number_matched": number_matched,
        "complete": url is None and len(features) <= limit,
    }


def fetch_ogc_features_for(
    principal: McpPrincipal, *, source_id: str, dataset_id: str, collection_id: str, limit: int = DEFAULT_LIMIT,
    bbox: list[float] | None = None, properties: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        source = sources.get_source(source_id)
        data = fetch_collection(
            source, dataset_id=dataset_id, collection_id=collection_id, limit=limit, bbox=bbox, properties=properties
        )
    except sources.SourceError as exc:
        raise ToolError(str(exc)) from exc
    ref = keep(principal, data)
    return summary_of(ref, data)


def get_fetched_features_for(
    principal: McpPrincipal, *, ref: str, offset: int = 0, limit: int = 50, with_geometry: bool = False,
    where: dict[str, str] | None = None,
) -> dict[str, Any]:
    features = kept(principal, ref)["features"]
    if where:
        features = [
            feature for feature in features
            if all(str((feature.get("properties") or {}).get(name)) == str(value) for name, value in where.items())
        ]
    page = [
        {"properties": feature.get("properties") or {}, **({"geometry": feature.get("geometry")} if with_geometry else {})}
        for feature in features[offset:offset + limit]
    ]
    if len(json.dumps(page, separators=(",", ":"))) > MAX_PAGE_BYTES:
        raise ToolError("This page is larger than 1 MB. Ask for fewer features" + (" or leave out the geometries." if with_geometry else "."))
    return {
        "ref": ref,
        "total": len(features),
        "offset": offset,
        "next_offset": offset + limit if offset + limit < len(features) else None,
        "features": page,
    }


def map_geojson(data: dict[str, Any]) -> dict[str, Any]:
    collection = {"type": "FeatureCollection", "features": [f for f in data["features"] if f.get("geometry")]}
    if not collection["features"]:
        raise ToolError("The fetched features have no geometries to show.")
    if len(json.dumps(collection, separators=(",", ":"))) > MAX_MAP_BYTES:
        raise ToolError("These features are larger than 5 MB; fetch fewer (bbox, properties, limit) to show them.")
    return collection


def register(server: MCPServer, map_commands: dict[str, MapCommand]) -> None:
    add_geojson = map_commands["addGeoJsonLayer"]

    @server.tool(
        title="Fetch features from an external source",
        description=(
            "Read features of an OGC API collection of a registered source on the server (source_id, dataset_id "
            "and collection_id as search_sources / describe_source_item give them), optionally inside `bbox` and "
            "with attribute equality filters. Answers with a `ref` and a summary (count, extent, attributes with "
            "examples, a few features), not the data: page with get_fetched_features, show it on the map with "
            "show_fetched_features. Results are kept for an hour."
        ),
        annotations=FETCHES,
    )
    def fetch_ogc_features(
        source_id: Annotated[str, Field(description="Source id from list_sources (an OGC API Features source).")],
        dataset_id: Annotated[str, Field(description="Dataset id (item_id from search_sources).")],
        collection_id: Annotated[str, Field(description="Collection id from describe_source_item.")],
        limit: Annotated[int, Field(ge=1, le=MAX_LIMIT, description="Most features to read.")] = DEFAULT_LIMIT,
        bbox: Annotated[list[float] | None, Field(min_length=4, max_length=4, description="[west, south, east, north] in WGS84.")] = None,
        properties: Annotated[dict[str, str] | None, Field(description="Attribute filters, e.g. {\"bezirk_name\": \"Altona\"}.")] = None,
    ) -> dict[str, Any]:
        return fetch_ogc_features_for(
            current_principal(), source_id=source_id, dataset_id=dataset_id, collection_id=collection_id,
            limit=limit, bbox=bbox, properties=properties,
        )

    @server.tool(
        title="Read fetched features",
        description="Page through features kept by fetch_ogc_features; `where` keeps those whose attributes equal the given values.",
        annotations=READ_ONLY,
    )
    def get_fetched_features(
        ref: Annotated[str, Field(description="`ref` from fetch_ogc_features.")],
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=MAX_FEATURE_PAGE)] = 50,
        with_geometry: bool = False,
        where: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return get_fetched_features_for(
            current_principal(), ref=ref, offset=offset, limit=limit, with_geometry=with_geometry, where=where
        )

    @server.tool(
        title="Show fetched features on the map",
        description=(
            "Put features kept by fetch_ogc_features on the user's map as a GeoJSON layer (needs connect_map). "
            "A polygon layer can then be the area of apply_geometry_filter."
        ),
        annotations=CHANGES_MAP,
    )
    async def show_fetched_features(
        ref: Annotated[str, Field(description="`ref` from fetch_ogc_features.")],
        title: Annotated[str, Field(min_length=1, max_length=80, description="Layer title on the map.")],
    ) -> dict[str, Any]:
        principal = current_principal()
        geojson = map_geojson(kept(principal, ref))
        value = await send_map_command(principal, add_geojson, {"title": title, "geojson": geojson})
        return value if isinstance(value, dict) else {"result": value}
