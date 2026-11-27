"""The map commands the server may send to the browser, read from ``map_commands.json``.

The list is not written here. It is exported from the frontend's single command
list (TOSCA-2 ``src/mcp/commandCatalog.ts``) with ``npm run mcp:export`` and
committed next to this module. Each entry becomes one MCP tool
(``tools/map.py``), and the same entries are the bridge's allow-list: a name
that is not in the file is never sent.

The file is checked when it is loaded. A missing, malformed or out-of-date file
stops the server from starting instead of offering broken tools.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

DEFAULT_PATH = Path(__file__).with_name("map_commands.json")
EXPORT_FORMAT = 1
EFFECTS = ("read", "view", "add")
MAX_TIMEOUT_SECONDS = 300
FIELDS = {"name", "title", "description", "params", "effect", "timeoutSeconds"}
NAME_PATTERN = re.compile(r"^[a-z][A-Za-z]+$")

Effect = Literal["read", "view", "add"]


class MapCommandsError(Exception):
    """``map_commands.json`` is missing or does not hold a valid command list."""


@dataclass(frozen=True)
class MapCommand:
    name: str
    title: str
    description: str
    params: dict[str, Any]
    effect: Effect
    timeout_seconds: float

    @property
    def tool_name(self) -> str:
        return tool_name(self.name)

    @property
    def validator(self) -> Draft202012Validator:
        return Draft202012Validator(self.params)


def tool_name(command_name: str) -> str:
    """snake_case of a command name, as the frontend's ``toolName`` makes it (setView -> set_view)."""
    return re.sub(r"[A-Z]", lambda match: f"_{match.group().lower()}", command_name)


def _entry(raw: Any, index: int) -> MapCommand:
    where = f"command #{index}"
    if not isinstance(raw, dict):
        raise MapCommandsError(f"{where} is not an object.")
    where = f"command #{index} ({raw.get('name', '?')!s})"
    if set(raw) != FIELDS:
        raise MapCommandsError(
            f"{where} has fields {sorted(raw)}, expected {sorted(FIELDS)}. Export the file again."
        )
    name, title, description = raw["name"], raw["title"], raw["description"]
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise MapCommandsError(f"{where}: name must be camelCase letters.")
    for field, value in (("title", title), ("description", description)):
        if not isinstance(value, str) or not value.strip():
            raise MapCommandsError(f"{where}: {field} must be non-empty text.")
    if raw["effect"] not in EFFECTS:
        raise MapCommandsError(f"{where}: effect must be one of {', '.join(EFFECTS)}.")
    timeout = raw["timeoutSeconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, int | float) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise MapCommandsError(f"{where}: timeoutSeconds must be a number above 0 and at most {MAX_TIMEOUT_SECONDS}.")
    params = raw["params"]
    if not isinstance(params, dict) or params.get("type") != "object":
        raise MapCommandsError(f"{where}: params must be a JSON Schema of type object.")
    try:
        Draft202012Validator.check_schema(params)
    except SchemaError as error:
        raise MapCommandsError(f"{where}: params is not a valid JSON Schema: {error.message}") from error
    declared = set(params.get("properties", {}))
    missing = [item for item in params.get("required", []) if item not in declared]
    if missing:
        raise MapCommandsError(f"{where}: params requires undeclared properties {missing}.")
    return MapCommand(
        name=name,
        title=title,
        description=description,
        params=params,
        effect=raw["effect"],
        timeout_seconds=float(timeout),
    )


def load_map_commands(path: Path | None = None) -> dict[str, MapCommand]:
    """Read and check the command file (default: ``DEFAULT_PATH``). Keys are command names, in file order."""
    path = DEFAULT_PATH if path is None else path
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise MapCommandsError(
            f"{path} is missing. Export it from the frontend: npm run mcp:export -- <path to this file>."
        ) from error
    except (OSError, ValueError) as error:
        raise MapCommandsError(f"{path} could not be read as JSON: {error}") from error
    if not isinstance(document, dict) or document.get("format") != EXPORT_FORMAT:
        found = document.get("format") if isinstance(document, dict) else None
        raise MapCommandsError(
            f"{path} has export format {found!r}; this server reads format {EXPORT_FORMAT}. "
            "Export the file again with the matching frontend, or update this server."
        )
    raw_commands = document.get("commands")
    if not isinstance(raw_commands, list) or not raw_commands:
        raise MapCommandsError(f"{path} lists no commands.")
    commands: dict[str, MapCommand] = {}
    for index, raw in enumerate(raw_commands):
        command = _entry(raw, index)
        if command.name in commands:
            raise MapCommandsError(f"{path}: command {command.name} is listed twice.")
        commands[command.name] = command
    return commands
