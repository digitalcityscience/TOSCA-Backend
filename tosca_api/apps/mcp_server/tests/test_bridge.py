"""Pairing a browser tab with an assistant, and commands between them."""

import asyncio

from django.test import SimpleTestCase

from tosca_api.apps.mcp_server.bridge import (
    MAX_FAILED_CLAIMS,
    MAX_SESSIONS_PER_USER,
    BridgeError,
    BridgeRegistry,
    format_code,
)
from tosca_api.apps.mcp_server.principal import McpPrincipal

RITA = McpPrincipal(sub="sub-rita", username="rita", org_slug="dcs", level="READER", exempt=False)
OMAR = McpPrincipal(sub="sub-omar", username="omar", org_slug="gq2", level="READER", exempt=False)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Tab:
    """A fake browser tab: records what the server sends."""

    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class BridgeRegistryTests(SimpleTestCase):
    def setUp(self):
        self.clock = Clock()
        self.registry = BridgeRegistry(clock=self.clock, wall_clock=self.clock)
        self.tab = Tab()

    def open(self, principal=RITA, tab=None, token_expires_at=None):
        return self.registry.open(
            principal, (tab or self.tab).send, self.clock.now + 300 if token_expires_at is None else token_expires_at
        )

    def assertBridgeError(self, code, callable_, *args):
        with self.assertRaises(BridgeError) as raised:
            callable_(*args)
        self.assertEqual(raised.exception.code, code)

    # -- pairing ----------------------------------------------------------

    def test_code_pairs_the_tab_with_its_own_user(self):
        session = self.open()
        claimed, replaced = self.registry.claim(format_code(session.code).lower(), RITA)
        self.assertIs(claimed, session)
        self.assertIsNone(replaced)
        self.assertTrue(session.paired)
        self.assertIs(self.registry.active_session(RITA), session)

    def test_code_works_once(self):
        session = self.open()
        code = session.code
        self.registry.claim(code, RITA)
        self.assertIsNone(session.code)
        self.assertBridgeError("pairing_invalid", self.registry.claim, code, RITA)

    def test_code_expires(self):
        session = self.open()
        code = session.code
        self.clock.now += 301
        self.assertBridgeError("pairing_invalid", self.registry.claim, code, RITA)
        self.assertFalse(session.paired)

    def test_code_of_another_user_is_refused_like_an_unknown_code(self):
        session = self.open(RITA)
        with self.assertLogs("tosca_api.apps.mcp_server.bridge", level="WARNING"):
            with self.assertRaises(BridgeError) as foreign:
                self.registry.claim(session.code, OMAR)
        with self.assertRaises(BridgeError) as unknown:
            self.registry.claim("ZZZZ-ZZZZ", OMAR)
        self.assertEqual(str(foreign.exception), str(unknown.exception))
        self.assertFalse(session.paired)
        # Rita's code still works for Rita.
        self.registry.claim(session.code, RITA)

    def test_wrong_codes_lock_the_caller_for_a_while(self):
        session = self.open()
        for _ in range(MAX_FAILED_CLAIMS):
            self.assertBridgeError("pairing_invalid", self.registry.claim, "AAAA-AAAA", RITA)
        self.assertBridgeError("pairing_locked", self.registry.claim, session.code, RITA)
        self.clock.now += 200
        self.registry.new_code(session)
        self.clock.now += 101
        self.registry.claim(session.code, RITA)

    def test_pairing_another_tab_replaces_the_first(self):
        first = self.open()
        self.registry.claim(first.code, RITA)
        second = self.open(tab=Tab())
        claimed, replaced = self.registry.claim(second.code, RITA)
        self.assertIs(claimed, second)
        self.assertIs(replaced, first)
        self.assertFalse(first.paired)
        self.assertIsNotNone(first.code)
        self.assertIs(self.registry.active_session(RITA), second)

    def test_users_do_not_share_a_pairing(self):
        session = self.open(RITA)
        self.registry.claim(session.code, RITA)
        self.assertBridgeError("not_connected", self.registry.active_session, OMAR)

    def test_number_of_waiting_tabs_per_user_is_limited(self):
        for _ in range(MAX_SESSIONS_PER_USER):
            self.open(tab=Tab())
        self.assertBridgeError("too_many_sessions", self.open)
        self.open(OMAR)

    def test_unpair_ends_the_pairing_and_gives_the_tab_a_new_code(self):
        session = self.open()
        self.registry.claim(session.code, RITA)
        self.registry.unpair(RITA)
        self.assertFalse(session.paired)
        self.assertIsNotNone(session.code)
        self.assertBridgeError("not_connected", self.registry.active_session, RITA)

    # -- disconnects ------------------------------------------------------

    def test_unpaired_tab_is_forgotten_when_it_disconnects(self):
        session = self.open()
        code = session.code
        self.registry.detach(session)
        self.assertBridgeError("pairing_invalid", self.registry.claim, code, RITA)

    def test_paired_tab_can_resume_within_the_grace_time(self):
        session = self.open()
        self.registry.claim(session.code, RITA)
        self.registry.detach(session)
        self.assertBridgeError("disconnected", self.registry.active_session, RITA)

        self.clock.now += 30
        new_tab = Tab()
        resumed = self.registry.resume(session.id, session.secret, RITA, new_tab.send, self.clock.now + 300)
        self.assertIs(resumed, session)
        self.assertTrue(session.paired)
        self.assertIs(self.registry.active_session(RITA), session)

    def test_resume_needs_the_secret_the_same_user_and_the_grace_time(self):
        session = self.open()
        self.registry.claim(session.code, RITA)
        self.registry.detach(session)
        tab = Tab()
        self.assertIsNone(self.registry.resume(session.id, "wrong", RITA, tab.send, 0))
        self.assertIsNone(self.registry.resume(session.id, session.secret, OMAR, tab.send, 0))
        self.clock.now += 121
        self.assertIsNone(self.registry.resume(session.id, session.secret, RITA, tab.send, 0))
        self.assertBridgeError("not_connected", self.registry.active_session, RITA)

    def test_connected_session_cannot_be_taken_over_by_resume(self):
        session = self.open()
        self.assertIsNone(self.registry.resume(session.id, session.secret, RITA, Tab().send, 0))

    def test_expired_sign_in_stops_commands(self):
        session = self.open(token_expires_at=self.clock.now + 10)
        self.registry.claim(session.code, RITA)
        self.clock.now += 100
        self.assertBridgeError("session_expired", self.registry.active_session, RITA)
        self.registry.refresh_token(session, RITA, self.clock.now + 300)
        self.assertIs(self.registry.active_session(RITA), session)

    def test_token_refresh_for_another_user_is_refused(self):
        session = self.open()
        self.assertFalse(self.registry.refresh_token(session, OMAR, self.clock.now + 300))

    # -- commands ---------------------------------------------------------

    def paired(self):
        session = self.open()
        self.registry.claim(session.code, RITA)
        return session

    def test_command_returns_the_answer_with_the_same_request_id(self):
        session = self.paired()

        async def scenario():
            task = asyncio.create_task(self.registry.command(session, "setLayerOpacity", {"opacity": 0.5}, 5))
            await asyncio.sleep(0)
            command = self.tab.sent[-1]
            self.registry.resolve(session, "another-id", True, "wrong", None)
            self.registry.resolve(session, command["requestId"], True, {"opacity": 0.5}, None)
            return command, await task

        command, value = asyncio.run(scenario())
        self.assertEqual(command["type"], "tosca:command")
        self.assertEqual((command["method"], command["params"], command["origin"]),
                         ("setLayerOpacity", {"opacity": 0.5}, "mcp"))
        self.assertEqual(value, {"opacity": 0.5})
        self.assertEqual(session.pending, {})

    def test_browser_error_reaches_the_caller(self):
        session = self.paired()

        async def scenario():
            task = asyncio.create_task(self.registry.command(session, "removeLayer", {"layerId": "x"}, 5))
            await asyncio.sleep(0)
            self.registry.resolve(session, self.tab.sent[-1]["requestId"], False, None, "Layer not found: x")
            await task

        with self.assertRaises(BridgeError) as raised:
            asyncio.run(scenario())
        self.assertEqual((raised.exception.code, str(raised.exception)), ("command_failed", "Layer not found: x"))

    def test_command_times_out_and_a_late_answer_is_ignored(self):
        session = self.paired()

        async def scenario():
            try:
                await self.registry.command(session, "getMapState", {}, 0.01)
            finally:
                # The answer arrives after the timeout: nothing waits for it any more.
                self.registry.resolve(session, self.tab.sent[-1]["requestId"], True, {}, None)

        with self.assertRaises(BridgeError) as raised:
            asyncio.run(scenario())
        self.assertEqual(raised.exception.code, "timeout")
        self.assertEqual(session.pending, {})

    def test_waiting_commands_fail_when_the_tab_disconnects(self):
        session = self.paired()

        async def scenario():
            task = asyncio.create_task(self.registry.command(session, "getMapState", {}, 5))
            await asyncio.sleep(0)
            self.registry.detach(session)
            await task

        with self.assertRaises(BridgeError) as raised:
            asyncio.run(scenario())
        self.assertEqual(raised.exception.code, "disconnected")

    def test_command_is_sent_once(self):
        session = self.paired()

        async def scenario():
            with self.assertRaises(BridgeError):
                await self.registry.command(session, "addCatalogLayer", {}, 0.01)

        asyncio.run(scenario())
        self.assertEqual(len(self.tab.sent), 1)
