"""Map tools: drive the TOSCA map that is open in the user's browser.

Two kinds of tools:

* ``connect_map`` / ``disconnect_map`` (written here): pairing with the user's
  tab. They control the bridge and are not map commands.
* one tool per map command in ``map_commands.json`` (``command_tools``). The
  tool name, description, input schema and wait time all come from that file,
  which is exported from the frontend's command list. Nothing about a single
  command is written here, except a few server-side rules (``SERVER_RULES``).

A command tool checks the caller, checks the arguments against the command's
JSON Schema, applies its server rule, then sends the command over the bridge
(``bridge.py``) and returns what the browser answered. The browser runs it with
the user's own session, through the same store functions as the user
interface, so a command can never do more than the user could do by hand.

The command tools are built as SDK ``Tool`` objects with an explicit input
schema and passed to ``MCPServer(tools=...)``: mcp 2.x has no decorator that
takes a JSON Schema instead of a Python signature.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated, Any

from jsonschema import Draft202012Validator, ValidationError
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase, FuncMetadata
from mcp.types import ToolAnnotations
from pydantic import ConfigDict, Field

from ..bridge import BridgeError, format_code, registry
from ..map_commands import MapCommand, MapCommandsError
from ..principal import McpPrincipal, current_principal

MAX_GEOJSON_BYTES = 1_000_000
MAX_GEOJSON_FEATURES = 5_000
MAX_REPORTED_ERRORS = 5

# Annotations follow the command's `effect`. No map command stores data or
# reaches outside the user's own tab, hence never destructive and never
# open-world. (Write tools that store data are in publishing.py / geostories.py.)
ANNOTATIONS: dict[str, ToolAnnotations] = {
    "read": ToolAnnotations(read_only_hint=True, open_world_hint=False),
    "view": ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
    "add": ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False),
}
CHANGES_VIEW = ANNOTATIONS["view"]

# Commands the hand-written tools need from the file (connect_map, add_scene_from_map,
# show_fetched_features).
REQUIRED_COMMANDS = ("getMapState", "addGeoJsonLayer")


# -- server-side rules ---------------------------------------------------------


def check_geojson(arguments: dict[str, Any]) -> None:
    """addGeoJsonLayer: the data passes through the model and the bridge, so keep it small."""
    geojson = arguments["geojson"]
    features = geojson.get("features")
    if geojson.get("type") != "FeatureCollection" or not isinstance(features, list) or not features:
        raise ToolError("`geojson` must be a FeatureCollection with at least one feature.")
    if len(features) > MAX_GEOJSON_FEATURES:
        raise ToolError(f"Too many features ({len(features)}); the limit is {MAX_GEOJSON_FEATURES}.")
    if len(json.dumps(geojson, separators=(",", ":")).encode()) > MAX_GEOJSON_BYTES:
        raise ToolError("The GeoJSON is larger than 1 MB.")


# command name -> check run after the schema check, before the browser is asked
SERVER_RULES: dict[str, Callable[[dict[str, Any]], None]] = {
    "addGeoJsonLayer": check_geojson,
}


# -- arguments -----------------------------------------------------------------


class _CommandArguments(ArgModelBase):
    """Hands the arguments to the tool unchanged; the tool checks them with jsonschema."""

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    def model_dump_one_level(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


def _allows_string(schema: dict[str, Any]) -> bool:
    kind = schema.get("type")
    return kind is None or kind == "string" or (isinstance(kind, list) and "string" in kind)


def pre_parse(params: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Undo JSON-in-a-string for properties that are not text.

    Some clients send objects, lists, numbers or booleans as JSON text
    ('{"type": ...}', "0.5", "true"). The SDK's own tools accept that; so do these.
    """
    properties = params.get("properties", {})
    parsed = dict(arguments)
    for key, value in arguments.items():
        schema = properties.get(key)
        if not isinstance(value, str) or not isinstance(schema, dict) or _allows_string(schema):
            continue
        try:
            candidate = json.loads(value)
        except (ValueError, RecursionError):
            continue
        if not isinstance(candidate, str):
            parsed[key] = candidate
    return parsed


def _explain(error: ValidationError) -> str:
    """One schema error, without the rejected value (it may be large, and it is the caller's data)."""
    where = ".".join(str(part) for part in error.absolute_path) or "arguments"
    rule, limit = error.validator, error.validator_value
    if rule in ("required", "additionalProperties"):
        # These messages name properties only.
        return f"{where}: {error.message}"
    if rule == "type":
        return f"{where}: must be of type {limit if isinstance(limit, str) else ' or '.join(limit)}"
    if rule == "enum":
        return f"{where}: must be one of {', '.join(map(str, limit))}"
    if rule in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        return f"{where}: {rule} is {limit}"
    return f"{where}: does not match the '{rule}' rule"


def check_arguments(validator: Draft202012Validator, arguments: dict[str, Any]) -> None:
    errors = sorted(validator.iter_errors(arguments), key=lambda error: list(error.absolute_path))
    if errors:
        shown = "; ".join(_explain(error) for error in errors[:MAX_REPORTED_ERRORS])
        more = f" (and {len(errors) - MAX_REPORTED_ERRORS} more)" if len(errors) > MAX_REPORTED_ERRORS else ""
        raise ToolError(f"Invalid arguments: {shown}{more}.")


# -- sending -------------------------------------------------------------------


async def send_map_command(principal: McpPrincipal, command: MapCommand, params: dict[str, Any]) -> Any:
    try:
        session = registry.active_session(principal)
        return await registry.command(session, command.name, params, command.timeout_seconds)
    except BridgeError as error:
        raise ToolError(str(error)) from error


def _command_tool(command: MapCommand) -> Tool:
    validator = command.validator
    rule = SERVER_RULES.get(command.name)

    async def run(**arguments: Any) -> dict[str, Any]:
        principal = current_principal()
        arguments = pre_parse(command.params, arguments)
        check_arguments(validator, arguments)
        if rule is not None:
            rule(arguments)
        value = await send_map_command(principal, command, arguments)
        # Tool results are objects; the commands answer with objects already.
        return value if isinstance(value, dict) else {"result": value}

    return Tool(
        fn=run,
        name=command.tool_name,
        title=command.title,
        description=command.description,
        parameters=command.params,
        fn_metadata=FuncMetadata(arg_model=_CommandArguments, output_model=dict[str, Any]),
        is_async=True,
        annotations=ANNOTATIONS[command.effect],
    )


def command_tools(commands: dict[str, MapCommand]) -> list[Tool]:
    """One tool per command of the file. Fails when the file and this module disagree."""
    stale = [name for name in (*SERVER_RULES, *REQUIRED_COMMANDS) if name not in commands]
    if stale:
        raise MapCommandsError(
            f"map_commands.json lacks {', '.join(stale)}, which tools/map.py relies on. "
            "Export the file again, or update SERVER_RULES / REQUIRED_COMMANDS."
        )
    return [_command_tool(command) for command in commands.values()]


# -- pairing -------------------------------------------------------------------


def register(server: MCPServer, commands: dict[str, MapCommand]) -> None:
    get_map_state = commands["getMapState"]

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
        state = await send_map_command(principal, get_map_state, {})
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
