"""The MCP endpoint and the map bridge, end to end over HTTP and WebSocket."""

import threading
from contextlib import contextmanager
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tosca_api.apps.mcp_server import bridge
from tosca_api.apps.mcp_server.server import BRIDGE_PATH, build_app
from tosca_api.apps.mcp_server.tools.map import MAP_COMMANDS

ORIGIN = "https://tosca.example"
RITA = {
    "sub": "sub-rita", "preferred_username": "rita", "azp": "tosca-mcp", "exp": 4102444800,
    "scope": "openid", "realm_access": {"roles": ["ROLE_DCS_READER"]}, "default_organization": "dcs",
}
OMAR = {**RITA, "sub": "sub-omar", "preferred_username": "omar",
        "realm_access": {"roles": ["ROLE_GQ2_READER"]}, "default_organization": "gq2"}
NO_ROLE = {**RITA, "sub": "sub-none", "realm_access": {"roles": []}}
TOKENS = {"rita-token": RITA, "omar-token": OMAR, "no-role-token": NO_ROLE}


@override_settings(
    MCP_PUBLIC_URL="https://tosca.example/mcp",
    MCP_BRIDGE_ALLOWED_ORIGINS=[ORIGIN],
    KEYCLOAK_ISSUER="https://auth.example/realms/tosca",
)
class McpServerTestCase(SimpleTestCase):
    def setUp(self):
        # Fresh pairing state per test (the registry is a process-wide object).
        self.registry = bridge.BridgeRegistry()
        for target in ("bridge.registry", "bridge_ws.registry", "tools.map.registry"):
            patcher = patch(f"tosca_api.apps.mcp_server.{target}", self.registry)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("tosca_api.apps.mcp_server.principal.verify_token_claims", side_effect=TOKENS.get)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._request_id = 0
        self._tabs = []

    @contextmanager
    def app(self):
        with TestClient(build_app(), base_url="https://tosca.example") as client:
            try:
                yield client
            finally:
                # Open sockets keep the app from shutting down.
                for tab in self._tabs:
                    try:
                        tab.__exit__(None, None, None)
                    except Exception:
                        pass
                self._tabs.clear()

    def rpc(self, client, method, params=None, token="rita-token"):
        self._request_id += 1
        headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
        if params is not None:
            body["params"] = params
        return client.post("/mcp", json=body, headers=headers)

    def call(self, client, name, arguments=None, token="rita-token"):
        response = self.rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, token=token)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["result"]

    def open_tab(self, client, token="rita-token", **hello):
        websocket = client.websocket_connect(BRIDGE_PATH, headers={"origin": ORIGIN})
        websocket.__enter__()
        self._tabs.append(websocket)
        websocket.send_json({"type": "tosca:hello", "token": token, **hello})
        return websocket


class McpEndpointTests(McpServerTestCase):
    def test_missing_token_gets_401_with_the_resource_metadata_pointer(self):
        with self.app() as client:
            response = self.rpc(client, "tools/list", token=None)
        self.assertEqual(response.status_code, 401)
        self.assertIn(
            'resource_metadata="https://tosca.example/.well-known/oauth-protected-resource/mcp"',
            response.headers["www-authenticate"],
        )

    def test_refused_token_gets_401(self):
        with self.app() as client:
            self.assertEqual(self.rpc(client, "tools/list", token="unknown").status_code, 401)

    def test_protected_resource_metadata_names_keycloak(self):
        with self.app() as client:
            response = client.get("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resource"], "https://tosca.example/mcp")
        self.assertEqual(response.json()["authorization_servers"], ["https://auth.example/realms/tosca"])

    def test_initialize_answers_with_plain_json(self):
        with self.app() as client:
            response = self.rpc(client, "initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.assertEqual(response.json()["result"]["serverInfo"]["name"], "tosca")
        self.assertNotIn("mcp-session-id", response.headers)

    def test_every_tool_is_typed_described_and_annotated(self):
        with self.app() as client:
            tools = self.rpc(client, "tools/list").json()["result"]["tools"]
        self.assertGreaterEqual(len(tools), 15)
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertGreater(len(tool["description"]), 30)
                self.assertEqual(tool["inputSchema"]["type"], "object")
                self.assertIn("readOnlyHint", tool["annotations"])
                for name, schema in tool["inputSchema"].get("properties", {}).items():
                    self.assertTrue("type" in schema or "anyOf" in schema or "enum" in schema, name)

    def test_tool_runs_as_the_token_user(self):
        with self.app() as client:
            result = self.call(client, "whoami")
        self.assertFalse(result["isError"])
        self.assertEqual(
            result["structuredContent"],
            {"username": "rita", "organization": "dcs", "role": "READER", "platform_admin": False},
        )

    def test_user_without_a_role_is_refused_by_every_tool(self):
        with self.app() as client:
            for name in ("whoami", "get_map_state"):
                result = self.call(client, name, token="no-role-token")
                self.assertTrue(result["isError"])
                self.assertIn("no role", result["content"][0]["text"])

    def test_invalid_arguments_are_refused_before_the_map_is_asked(self):
        with self.app() as client:
            result = self.call(client, "set_layer_opacity", {"layer_id": "x", "opacity": 7})
        self.assertTrue(result["isError"])

    @override_settings(MCP_ALLOWED_HOSTS=["tosca.example"], MCP_ALLOWED_ORIGINS=["https://claude.ai"])
    def test_host_and_origin_are_checked_when_configured(self):
        with self.app() as client:
            self.assertEqual(self.rpc(client, "tools/list").status_code, 200)
            headers = {"Authorization": "Bearer rita-token", "MCP-Protocol-Version": "2025-06-18"}
            body = {"jsonrpc": "2.0", "id": 9, "method": "tools/list"}
            evil_origin = client.post("/mcp", json=body, headers={**headers, "Origin": "https://evil.example"})
            self.assertEqual(evil_origin.status_code, 403)
            good_origin = client.post("/mcp", json=body, headers={**headers, "Origin": "https://claude.ai"})
            self.assertEqual(good_origin.status_code, 200)
            evil_host = client.post("/mcp", json=body, headers={**headers, "Host": "evil.example"})
            self.assertEqual(evil_host.status_code, 421)


class MapBridgeTests(McpServerTestCase):
    def test_map_tools_need_a_connected_map(self):
        with self.app() as client:
            result = self.call(client, "get_map_state")
        self.assertTrue(result["isError"])
        self.assertIn("connect_map", result["content"][0]["text"])

    def test_bridge_refuses_foreign_origins_and_bad_tokens(self):
        with self.app() as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect(BRIDGE_PATH, headers={"origin": "https://evil.example"}):
                    pass
            for token, code in (("unknown", "unauthorized"), ("no-role-token", "forbidden")):
                tab = self.open_tab(client, token=token)
                self.assertEqual(tab.receive_json()["code"], code)

    def _connect(self, client, tab, token="rita-token"):
        """Run connect_map in a thread and answer its getMapState from the tab."""
        ready = tab.receive_json()
        self.assertEqual(ready["type"], "tosca:ready")
        self.assertFalse(ready["paired"])
        outcome = {}
        thread = threading.Thread(
            target=lambda: outcome.update(result=self.call(client, "connect_map", {"code": ready["code"]}, token))
        )
        thread.start()
        self.assertEqual(tab.receive_json(), {"type": "tosca:paired", "client": "tosca-mcp"})
        command = tab.receive_json()
        self.assertEqual((command["type"], command["method"]), ("tosca:command", "getMapState"))
        tab.send_json({"type": "tosca:result", "requestId": command["requestId"], "ok": True,
                       "value": {"view": {"zoom": 11}, "layers": []}})
        thread.join(timeout=10)
        return ready, outcome["result"]

    def test_pairing_then_a_command_round_trip(self):
        with self.app() as client:
            tab = self.open_tab(client)
            _ready, connected = self._connect(client, tab)
            self.assertFalse(connected["isError"])
            self.assertEqual(connected["structuredContent"],
                             {"connected": True, "map": {"view": {"zoom": 11}, "layers": []}})

            outcome = {}
            thread = threading.Thread(target=lambda: outcome.update(result=self.call(
                client, "set_layer_opacity", {"layer_id": "bicycle", "opacity": 0.5})))
            thread.start()
            command = tab.receive_json()
            self.assertEqual(command["method"], "setLayerOpacity")
            self.assertEqual(command["params"], {"layerId": "bicycle", "opacity": 0.5})
            self.assertEqual(command["origin"], "mcp")
            tab.send_json({"type": "tosca:result", "requestId": command["requestId"], "ok": True,
                           "value": {"layerId": "bicycle", "opacity": 0.5}})
            thread.join(timeout=10)
            self.assertEqual(outcome["result"]["structuredContent"], {"layerId": "bicycle", "opacity": 0.5})

    def test_error_from_the_map_is_the_tool_result(self):
        with self.app() as client:
            tab = self.open_tab(client)
            self._connect(client, tab)
            outcome = {}
            thread = threading.Thread(target=lambda: outcome.update(result=self.call(
                client, "remove_layer", {"layer_id": "nope"})))
            thread.start()
            command = tab.receive_json()
            tab.send_json({"type": "tosca:result", "requestId": command["requestId"], "ok": False,
                           "error": "Layer not found: nope"})
            thread.join(timeout=10)
            self.assertTrue(outcome["result"]["isError"])
            self.assertIn("Layer not found: nope", outcome["result"]["content"][0]["text"])

    def test_code_of_another_user_does_not_pair(self):
        with self.app() as client:
            tab = self.open_tab(client, token="rita-token")
            ready = tab.receive_json()
            with self.assertLogs("tosca_api.apps.mcp_server.bridge", level="WARNING"):
                result = self.call(client, "connect_map", {"code": ready["code"]}, token="omar-token")
            self.assertTrue(result["isError"])
            self.assertIn("not valid", result["content"][0]["text"])
            # Omar still has no map; Rita's tab got nothing.
            self.assertTrue(self.call(client, "get_map_state", token="omar-token")["isError"])

    def test_commands_stop_when_the_tab_closes_and_resume_keeps_the_pairing(self):
        with self.app() as client:
            tab = self.open_tab(client)
            ready, _ = self._connect(client, tab)
            tab.close()
            offline = self.call(client, "get_map_state")
            self.assertTrue(offline["isError"])
            self.assertIn("offline", offline["content"][0]["text"])

            again = self.open_tab(client, resume={"sessionId": ready["sessionId"], "secret": ready["secret"]})
            resumed = again.receive_json()
            self.assertEqual((resumed["type"], resumed["paired"], resumed["sessionId"]),
                             ("tosca:ready", True, ready["sessionId"]))
            self.assertNotIn("code", resumed)

    def test_browser_can_end_the_pairing(self):
        with self.app() as client:
            tab = self.open_tab(client)
            self._connect(client, tab)
            tab.send_json({"type": "tosca:unpair"})
            unpaired = tab.receive_json()
            self.assertEqual((unpaired["type"], unpaired["reason"]), ("tosca:unpaired", "browser"))
            self.assertIn("code", unpaired)
            self.assertTrue(self.call(client, "get_map_state")["isError"])

    def test_assistant_can_end_the_pairing(self):
        with self.app() as client:
            tab = self.open_tab(client)
            self._connect(client, tab)
            self.assertEqual(self.call(client, "disconnect_map")["structuredContent"], {"connected": False})
            self.assertEqual(tab.receive_json()["reason"], "assistant")

    def test_token_refresh_for_another_user_closes_the_tab(self):
        with self.app() as client:
            tab = self.open_tab(client)
            self._connect(client, tab)
            tab.send_json({"type": "tosca:auth", "token": "omar-token"})
            self.assertEqual(tab.receive_json()["code"], "unauthorized")
            self.assertTrue(self.call(client, "get_map_state")["isError"])

    def test_every_map_tool_sends_a_listed_command(self):
        """The tools cannot send anything outside MAP_COMMANDS."""
        calls = {
            "get_map_state": {},
            "set_map_view": {"longitude": 9.93, "latitude": 53.55, "zoom": 13},
            "fit_map_bounds": {"west": 9.8, "south": 53.5, "east": 10.0, "north": 53.6},
            "zoom_to_layer": {"layer_id": "a"},
            "add_catalog_layer": {"provider_id": "p", "workspace": "w", "name": "n"},
            "add_geojson_layer": {"title": "t", "geojson": {"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [9.9, 53.5]}}]}},
            "remove_layer": {"layer_id": "a"},
            "set_layer_visibility": {"layer_id": "a", "visible": False},
            "set_layer_opacity": {"layer_id": "a", "opacity": 0.2},
            "get_layer_attributes": {"layer_id": "a"},
            "add_attribute_filter": {"layer_id": "a", "attribute": "year", "operator": ">=", "value": 1960},
            "remove_attribute_filter": {"layer_id": "a", "attribute": "year", "operator": ">="},
        }
        sent = set()
        with self.app() as client:
            tab = self.open_tab(client)
            self._connect(client, tab)
            for name, arguments in calls.items():
                thread = threading.Thread(target=self.call, args=(client, name, arguments))
                thread.start()
                command = tab.receive_json()
                sent.add(command["method"])
                tab.send_json({"type": "tosca:result", "requestId": command["requestId"], "ok": True, "value": {}})
                thread.join(timeout=10)
        self.assertEqual(sent, set(MAP_COMMANDS))
