"""map_commands.json: the committed file is well-formed, and a broken one stops the server."""

import asyncio
import copy
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase
from mcp.server.mcpserver.exceptions import ToolError

from tosca_api.apps.mcp_server import map_commands
from tosca_api.apps.mcp_server.map_commands import EFFECTS, MapCommandsError, load_map_commands, tool_name
from tosca_api.apps.mcp_server.server import build_server
from tosca_api.apps.mcp_server.tools import map as map_tools

GOOD_ENTRY = {
    "name": "setLayerOpacity",
    "title": "Set layer opacity",
    "description": "Set the opacity of a layer on the map, from 0 to 1.",
    "params": {
        "type": "object",
        "properties": {"layerId": {"type": "string", "minLength": 1}, "opacity": {"type": "number", "maximum": 1}},
        "required": ["layerId", "opacity"],
        "additionalProperties": False,
    },
    "effect": "view",
    "timeoutSeconds": 10,
}


class CommittedFileTests(SimpleTestCase):
    def test_every_entry_is_well_formed(self):
        commands = load_map_commands()
        self.assertGreaterEqual(len(commands), 12)
        tool_names = [command.tool_name for command in commands.values()]
        self.assertEqual(len(set(tool_names)), len(tool_names))
        for command in commands.values():
            with self.subTest(command=command.name):
                self.assertRegex(command.tool_name, r"^[a-z][a-z_]+$")
                self.assertIn(command.effect, EFFECTS)
                self.assertGreater(command.timeout_seconds, 0)
                self.assertGreater(len(command.description), 30)
                self.assertEqual(command.params.get("additionalProperties"), False)

    def test_descriptions_name_tools_not_commands(self):
        """The assistant sees snake_case tool names; a camelCase command name in a text would confuse it."""
        commands = load_map_commands()
        for command in commands.values():
            text = json.dumps([command.description, command.params])
            for other in commands:
                self.assertIsNone(re.search(rf"\b{other}\b", text), f"{command.name} mentions {other}")

    def test_the_rules_written_here_match_the_file(self):
        commands = load_map_commands()
        for name in (*map_tools.SERVER_RULES, *map_tools.REQUIRED_COMMANDS):
            self.assertIn(name, commands)

    def test_tool_name_is_snake_case(self):
        self.assertEqual(tool_name("setLayerOpacity"), "set_layer_opacity")
        self.assertEqual(tool_name("addGeoJsonLayer"), "add_geo_json_layer")


class BrokenFileTests(SimpleTestCase):
    def load(self, document):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "map_commands.json"
            path.write_text(document if isinstance(document, str) else json.dumps(document))
            return load_map_commands(path)

    def assert_refused(self, document, message):
        with self.assertRaisesRegex(MapCommandsError, message):
            self.load(document)

    def entry(self, **changes):
        entry = copy.deepcopy(GOOD_ENTRY)
        entry.update(changes)
        return {"format": 1, "commands": [entry]}

    def test_a_good_file_loads(self):
        commands = self.load(self.entry())
        self.assertEqual(commands["setLayerOpacity"].timeout_seconds, 10.0)

    def test_missing_file_says_how_to_make_it(self):
        with self.assertRaisesRegex(MapCommandsError, "npm run mcp:export"):
            load_map_commands(Path("/nonexistent/map_commands.json"))

    def test_server_does_not_start_without_the_file(self):
        with patch.object(map_commands, "DEFAULT_PATH", Path("/nonexistent/map_commands.json")):
            with self.assertRaises(MapCommandsError):
                build_server()

    def test_refuses_broken_files(self):
        unknown_type = {"type": "object", "properties": {"a": {"type": "nope"}}}
        cases = {
            "not json": ("{", "could not be read"),
            "other format": ({"format": 2, "commands": [GOOD_ENTRY]}, "format 2"),
            "no commands": ({"format": 1, "commands": []}, "no commands"),
            "extra field": (self.entry(status="available"), "fields"),
            "missing field": ({"format": 1, "commands": [{k: v for k, v in GOOD_ENTRY.items() if k != "title"}]},
                              "fields"),
            "bad name": (self.entry(name="set_layer_opacity"), "camelCase"),
            "bad effect": (self.entry(effect="write"), "effect"),
            "zero timeout": (self.entry(timeoutSeconds=0), "timeoutSeconds"),
            "bool timeout": (self.entry(timeoutSeconds=True), "timeoutSeconds"),
            "not an object schema": (self.entry(params={"type": "string"}), "type object"),
            "invalid schema": (self.entry(params=unknown_type), "not a valid JSON Schema"),
            "undeclared required": (self.entry(params={"type": "object", "required": ["x"]}), "undeclared"),
            "duplicate": ({"format": 1, "commands": [GOOD_ENTRY, GOOD_ENTRY]}, "twice"),
        }
        for case, (document, message) in cases.items():
            with self.subTest(case=case):
                self.assert_refused(document, message)

    def test_file_without_a_command_the_server_relies_on_is_stale(self):
        commands = self.load(self.entry())
        with self.assertRaisesRegex(MapCommandsError, "addGeoJsonLayer"):
            map_tools.command_tools(commands)


class CommandToolTests(SimpleTestCase):
    def setUp(self):
        self.commands = load_map_commands()
        self.tools = {tool.name: tool for tool in map_tools.command_tools(self.commands)}
        self.registry = MagicMock()
        self.registry.command = AsyncMock(return_value={"ok": True})
        for target, value in (("registry", self.registry), ("current_principal", MagicMock())):
            patcher = patch.object(map_tools, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_tool(self, name, **arguments):
        return asyncio.run(self.tools[name].fn(**arguments))

    def test_wait_time_comes_from_the_file(self):
        for name in ("addCatalogLayer", "getMapState"):
            command = self.commands[name]
            self.registry.command.reset_mock()
            arguments = {"providerId": "p", "workspace": "w", "layerName": "n"} if name == "addCatalogLayer" else {}
            self.run_tool(command.tool_name, **arguments)
            _session, method, params, timeout = self.registry.command.await_args.args
            self.assertEqual((method, params, timeout), (name, arguments, command.timeout_seconds))

    def test_schema_errors_are_refused_and_do_not_echo_the_value(self):
        secret = "x" * 5000
        with self.assertRaises(ToolError) as caught:
            self.run_tool("add_geo_json_layer", title="t", geojson=[secret])
        self.assertIn("geojson: must be of type object", str(caught.exception))
        self.assertNotIn(secret, str(caught.exception))
        self.registry.command.assert_not_awaited()

    def test_geojson_limits(self):
        point = {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [9.9, 53.5]}}
        big = {"type": "Feature", "properties": {"text": "y" * 1_100_000}, "geometry": point["geometry"]}
        for geojson, message in (
            ({"type": "FeatureCollection", "features": []}, "at least one feature"),
            ({"type": "Feature"}, "FeatureCollection"),
            ({"type": "FeatureCollection", "features": [point] * 5001}, "Too many features"),
            ({"type": "FeatureCollection", "features": [big]}, "1 MB"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ToolError, message):
                self.run_tool("add_geo_json_layer", title="t", geojson=geojson)
        self.registry.command.assert_not_awaited()

    def test_json_text_is_parsed_only_where_text_is_not_allowed(self):
        geojson = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [9.9, 53.5]}}]}
        self.run_tool("add_geo_json_layer", title="5", geojson=json.dumps(geojson))
        self.assertEqual(self.registry.command.await_args.args[2], {"title": "5", "geojson": geojson})
        self.run_tool("add_attribute_filter", layerId="a", attribute="x", operator="==", value="5")
        self.assertEqual(self.registry.command.await_args.args[2]["value"], "5")

    def test_a_non_object_answer_is_wrapped(self):
        self.registry.command.return_value = ["a", "b"]
        self.assertEqual(self.run_tool("get_map_state"), {"result": ["a", "b"]})
