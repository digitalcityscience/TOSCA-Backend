"""Map tools: drive the TOSCA map that is open in the user's browser.

Each tool sends one named command over the bridge (``bridge.py``) and returns
what the browser answered. The browser runs the command with the user's own
session, through the same store functions as the user interface, so a command
can never do more than the user could do by hand.

``MAP_COMMANDS`` is the complete list of commands the server will send.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from ..bridge import BridgeError, format_code, registry
from ..principal import current_principal

# method -> seconds to wait for the browser's answer
MAP_COMMANDS: dict[str, float] = {
    "getMapState": 10,
    "setView": 10,
    "fitBounds": 10,
    "zoomToLayer": 10,
    "addCatalogLayer": 60,
    "addGeoJsonLayer": 60,
    "removeLayer": 15,
    "setLayerVisibility": 10,
    "setLayerOpacity": 10,
    "getLayerAttributes": 10,
    "addAttributeFilter": 15,
    "removeAttributeFilter": 15,
}

MAX_GEOJSON_BYTES = 1_000_000
MAX_GEOJSON_FEATURES = 5_000

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
# Changes only what is shown in the user's own browser tab; no data is stored.
CHANGES_VIEW = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

LayerId = Annotated[str, Field(description="Id of a layer on the map, from get_map_state (not its title).")]
Operator = Literal["==", "!=", ">", ">=", "<", "<=", "in"]


async def send_map_command(method: str, params: dict[str, Any]) -> Any:
    if method not in MAP_COMMANDS:
        raise ToolError(f"Unknown map command: {method}")
    principal = current_principal()
    try:
        session = registry.active_session(principal)
        return await registry.command(session, method, params, MAP_COMMANDS[method])
    except BridgeError as error:
        raise ToolError(str(error)) from error


def register(server: MCPServer) -> None:
    @server.tool(
        title="Connect to the user's TOSCA map",
        description=(
            "Connect this conversation to the TOSCA map open in the user's browser. The user gets the code in "
            "TOSCA: account menu, \"Connect AI assistant\". Codes work once and expire after a few minutes. "
            "Required once before any other map tool; afterwards the connection stays until the user ends it "
            "or closes the tab. Returns the current map state."
        ),
        annotations=CHANGES_VIEW,
    )
    async def connect_map(
        code: Annotated[str, Field(description="The code shown in TOSCA, e.g. 'K7QM-3XTP'.")],
    ) -> dict[str, Any]:
        principal = current_principal()
        try:
            session, replaced = registry.claim(code, principal)
        except BridgeError as error:
            raise ToolError(str(error)) from error
        if replaced is not None and replaced.send is not None:
            await replaced.send({
                "type": "tosca:unpaired",
                "reason": "replaced",
                "code": format_code(replaced.code) if replaced.code else None,
                "codeExpiresIn": registry.code_seconds_left(replaced),
            })
        if session.send is not None:
            await session.send({"type": "tosca:paired", "client": principal.client_id})
        state = await send_map_command("getMapState", {})
        return {"connected": True, "map": state}

    @server.tool(
        title="Disconnect from the TOSCA map",
        description="End the connection to the user's TOSCA map. The map itself is left as it is.",
        annotations=CHANGES_VIEW,
    )
    async def disconnect_map() -> dict[str, Any]:
        principal = current_principal()
        session = registry.unpair(principal)
        if session is not None and session.send is not None:
            await session.send({
                "type": "tosca:unpaired",
                "reason": "assistant",
                "code": format_code(session.code) if session.code else None,
                "codeExpiresIn": registry.code_seconds_left(session),
            })
        return {"connected": False}

    @server.tool(
        title="Read the map",
        description=(
            "What the user's TOSCA map shows right now: the view (center, zoom, bounds) and every layer on the "
            "map with its id, title, visibility, opacity and applied filters. The user may have changed the "
            "map by hand, so read it again instead of relying on an earlier answer."
        ),
        annotations=READ_ONLY,
    )
    async def get_map_state() -> dict[str, Any]:
        return await send_map_command("getMapState", {})

    @server.tool(
        title="Move the map",
        description="Fly the map to a point (WGS84 longitude/latitude). Zoom 0 is the world, 12 a city, 16 a street.",
        annotations=CHANGES_VIEW,
    )
    async def set_map_view(
        longitude: Annotated[float, Field(ge=-180, le=180)],
        latitude: Annotated[float, Field(ge=-90, le=90)],
        zoom: Annotated[float | None, Field(ge=0, le=22, description="Leave empty to keep the zoom.")] = None,
    ) -> dict[str, Any]:
        return await send_map_command("setView", {"center": [longitude, latitude], "zoom": zoom})

    @server.tool(
        title="Fit the map to an extent",
        description="Show a WGS84 bounding box (west, south, east, north), e.g. a district or a layer's extent.",
        annotations=CHANGES_VIEW,
    )
    async def fit_map_bounds(
        west: Annotated[float, Field(ge=-180, le=180)],
        south: Annotated[float, Field(ge=-90, le=90)],
        east: Annotated[float, Field(ge=-180, le=180)],
        north: Annotated[float, Field(ge=-90, le=90)],
    ) -> dict[str, Any]:
        if west >= east or south >= north:
            raise ToolError("The bounding box is empty: west must be below east and south below north.")
        return await send_map_command("fitBounds", {"bounds": [west, south, east, north]})

    @server.tool(
        title="Zoom to a layer",
        description="Fit the map to the extent of a layer that is on the map.",
        annotations=CHANGES_VIEW,
    )
    async def zoom_to_layer(layer_id: LayerId) -> dict[str, Any]:
        return await send_map_command("zoomToLayer", {"layerId": layer_id})

    @server.tool(
        title="Add a catalog layer to the map",
        description=(
            "Show a TOSCA catalog layer on the user's map, with its catalog style. Take provider_id, workspace "
            "and name from search_layers. Returns the new layer's id on the map."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
        ),
    )
    async def add_catalog_layer(
        provider_id: Annotated[str, Field(description="Provider id from search_layers.")],
        workspace: Annotated[str, Field(description="Workspace name from search_layers.")],
        name: Annotated[str, Field(description="Layer name from search_layers (not the title).")],
    ) -> dict[str, Any]:
        return await send_map_command(
            "addCatalogLayer", {"providerId": provider_id, "workspace": workspace, "layerName": name}
        )

    @server.tool(
        title="Add GeoJSON to the map",
        description=(
            "Show small GeoJSON data (WGS84) as a new layer on the user's map, e.g. a few points or one outline "
            "you computed. The layer exists only in the user's browser tab. All features must have the same "
            f"geometry family (points, lines or polygons). At most {MAX_GEOJSON_FEATURES} features and about "
            "1 MB; for catalog data use add_catalog_layer instead."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
        ),
    )
    async def add_geojson_layer(
        title: Annotated[str, Field(min_length=1, max_length=80, description="Layer title shown in the layer list.")],
        geojson: Annotated[dict[str, Any], Field(description="A GeoJSON FeatureCollection in WGS84.")],
    ) -> dict[str, Any]:
        features = geojson.get("features")
        if geojson.get("type") != "FeatureCollection" or not isinstance(features, list) or not features:
            raise ToolError("`geojson` must be a FeatureCollection with at least one feature.")
        if len(features) > MAX_GEOJSON_FEATURES:
            raise ToolError(f"Too many features ({len(features)}); the limit is {MAX_GEOJSON_FEATURES}.")
        if len(json.dumps(geojson, separators=(",", ":")).encode()) > MAX_GEOJSON_BYTES:
            raise ToolError("The GeoJSON is larger than 1 MB.")
        return await send_map_command("addGeoJsonLayer", {"title": title, "geojson": geojson})

    @server.tool(
        title="Remove a layer from the map",
        description="Remove a layer from the user's map (the catalog data itself is not touched).",
        annotations=CHANGES_VIEW,
    )
    async def remove_layer(layer_id: LayerId) -> dict[str, Any]:
        return await send_map_command("removeLayer", {"layerId": layer_id})

    @server.tool(
        title="Show or hide a layer",
        description="Set whether a layer on the map is visible.",
        annotations=CHANGES_VIEW,
    )
    async def set_layer_visibility(layer_id: LayerId, visible: bool) -> dict[str, Any]:
        return await send_map_command("setLayerVisibility", {"layerId": layer_id, "visible": visible})

    @server.tool(
        title="Set layer opacity",
        description="Set the opacity of a layer on the map, from 0 (transparent) to 1 (opaque).",
        annotations=CHANGES_VIEW,
    )
    async def set_layer_opacity(
        layer_id: LayerId, opacity: Annotated[float, Field(ge=0, le=1)]
    ) -> dict[str, Any]:
        return await send_map_command("setLayerOpacity", {"layerId": layer_id, "opacity": opacity})

    @server.tool(
        title="List a map layer's attributes",
        description=(
            "Attributes of a layer on the map that can be used in filters, with their type "
            "('text' or 'number'). Call it before add_attribute_filter."
        ),
        annotations=READ_ONLY,
    )
    async def get_layer_attributes(layer_id: LayerId) -> dict[str, Any]:
        return await send_map_command("getLayerAttributes", {"layerId": layer_id})

    @server.tool(
        title="Filter a layer by attribute",
        description=(
            "Show only the features of a map layer that match a condition, e.g. attribute 'year' >= 1960. "
            "Number attributes take ==, !=, >, >=, <, <=; text attributes take ==, != and in (text is compared "
            "ignoring case). Several filters on one layer are combined with `relation`. The filter also "
            "appears in TOSCA's filter panel, where the user can remove it."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
        ),
    )
    async def add_attribute_filter(
        layer_id: LayerId,
        attribute: Annotated[str, Field(description="Attribute name from get_layer_attributes.")],
        operator: Operator,
        value: Annotated[str | float, Field(description="Value to compare with.")],
        relation: Annotated[
            Literal["AND", "OR"], Field(description="How this layer's filters are combined.")
        ] = "AND",
    ) -> dict[str, Any]:
        return await send_map_command("addAttributeFilter", {
            "layerId": layer_id,
            "attribute": attribute,
            "operator": operator,
            "value": str(value),
            "relation": relation,
        })

    @server.tool(
        title="Remove an attribute filter",
        description="Remove one filter from a map layer, identified by its attribute and operator (see get_map_state).",
        annotations=CHANGES_VIEW,
    )
    async def remove_attribute_filter(
        layer_id: LayerId,
        attribute: Annotated[str, Field(description="Attribute name of the filter.")],
        operator: Operator,
    ) -> dict[str, Any]:
        return await send_map_command(
            "removeAttributeFilter", {"layerId": layer_id, "attribute": attribute, "operator": operator}
        )
