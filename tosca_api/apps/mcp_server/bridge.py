"""Registry of browser map sessions and the commands sent to them.

A signed-in TOSCA tab opens a WebSocket (``bridge_ws.py``) and gets a short
pairing code. The user tells the code to the assistant; ``connect_map`` claims
it. From then on the map tools of that Keycloak user go to that tab.

Rules:

* a code works once, for a few minutes, and only for the user who opened the
  tab (same Keycloak ``sub`` on both sides);
* one paired tab per user: pairing another tab replaces the first;
* every command carries a ``requestId``; the answer with the same id resolves
  it. A command is sent once and never repeated by the server;
* when the tab disconnects, waiting commands fail at once. The tab may come
  back within a short time (``MCP_BRIDGE_RESUME_SECONDS``) and keep its pairing.

State is kept in this process only. The MCP endpoint and the WebSocket must
therefore run in the same single process (one uvicorn worker). When Redis is
available, this module is the one place to change.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from .principal import McpPrincipal

logger = logging.getLogger(__name__)

# No 0/O, 1/I/L: the code is read aloud or typed from a screen.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
MAX_SESSIONS_PER_USER = 5
MAX_FAILED_CLAIMS = 5
FAILED_CLAIM_WINDOW_SECONDS = 300
# A tab whose access token expired longer ago than this gets no commands.
TOKEN_EXPIRY_GRACE_SECONDS = 60

Send = Callable[[dict[str, Any]], Awaitable[None]]


class BridgeError(Exception):
    """A map command could not be delivered or the pairing failed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class BridgeSession:
    id: str
    secret: str
    principal: McpPrincipal
    send: Send | None
    token_expires_at: float
    code: str | None = None
    code_expires_at: float = 0.0
    paired: bool = False
    disconnected_at: float | None = None
    pending: dict[str, asyncio.Future] = field(default_factory=dict)

    @property
    def connected(self) -> bool:
        return self.send is not None


def normalize_code(value: str) -> str:
    return "".join(char for char in str(value).upper() if char.isalnum())


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


class BridgeRegistry:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, wall_clock: Callable[[], float] = time.time):
        self._clock = clock
        self._wall_clock = wall_clock
        self._sessions: dict[str, BridgeSession] = {}
        self._by_code: dict[str, str] = {}
        self._active_by_sub: dict[str, str] = {}
        self._failed_claims: dict[str, deque[float]] = {}

    # -- browser side -----------------------------------------------------

    def open(self, principal: McpPrincipal, send: Send, token_expires_at: float) -> BridgeSession:
        self.sweep()
        own = [session for session in self._sessions.values() if session.principal.sub == principal.sub]
        if len(own) >= MAX_SESSIONS_PER_USER:
            raise BridgeError("too_many_sessions", "Too many open TOSCA tabs are waiting for an assistant.")
        session = BridgeSession(
            id=uuid.uuid4().hex,
            secret=secrets.token_urlsafe(32),
            principal=principal,
            send=send,
            token_expires_at=token_expires_at,
        )
        self._sessions[session.id] = session
        self.new_code(session)
        return session

    def resume(
        self, session_id: str, secret: str, principal: McpPrincipal, send: Send, token_expires_at: float
    ) -> BridgeSession | None:
        """Reattach a tab that lost its connection. None when that is not possible."""
        self.sweep()
        session = self._sessions.get(str(session_id))
        if session is None or session.connected:
            return None
        if not hmac.compare_digest(session.secret, str(secret)) or session.principal.sub != principal.sub:
            return None
        session.send = send
        session.disconnected_at = None
        session.principal = principal
        session.token_expires_at = token_expires_at
        if not session.paired:
            self.new_code(session)
        return session

    def new_code(self, session: BridgeSession) -> str:
        self._drop_code(session)
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        session.code = code
        session.code_expires_at = self._clock() + settings.MCP_PAIRING_CODE_TTL_SECONDS
        self._by_code[code] = session.id
        return code

    def code_seconds_left(self, session: BridgeSession) -> int:
        return max(0, int(session.code_expires_at - self._clock())) if session.code else 0

    def refresh_token(self, session: BridgeSession, principal: McpPrincipal, token_expires_at: float) -> bool:
        if principal.sub != session.principal.sub:
            return False
        session.principal = principal
        session.token_expires_at = token_expires_at
        return True

    def detach(self, session: BridgeSession) -> None:
        """The tab's connection ended. Paired tabs may resume for a while."""
        session.send = None
        session.disconnected_at = self._clock()
        self._fail_pending(session, BridgeError("disconnected", "The TOSCA tab disconnected before it answered."))
        if not session.paired:
            self._remove(session)

    def close(self, session: BridgeSession) -> None:
        """The tab ended the session itself (disconnect button, logout)."""
        self._fail_pending(session, BridgeError("disconnected", "The TOSCA tab ended the assistant connection."))
        self._remove(session)

    def resolve(self, session: BridgeSession, request_id: str, ok: bool, value: Any, error: Any) -> None:
        future = session.pending.get(str(request_id))
        if future is None or future.done():
            # Late or duplicate answer: the command already timed out or was answered.
            return
        if ok:
            future.set_result(value)
        else:
            future.set_exception(BridgeError("command_failed", str(error or "The map refused the command.")))

    # -- MCP side ---------------------------------------------------------

    def claim(self, code: str, principal: McpPrincipal) -> tuple[BridgeSession, BridgeSession | None]:
        """Pair ``principal`` with the tab that shows ``code``.

        Returns the paired session and, when another tab of the user was paired
        before and is still connected, that tab (it got a new code).
        """
        self.sweep()
        now = self._clock()
        failures = self._failed_claims.setdefault(principal.sub, deque())
        while failures and now - failures[0] > FAILED_CLAIM_WINDOW_SECONDS:
            failures.popleft()
        if len(failures) >= MAX_FAILED_CLAIMS:
            raise BridgeError("pairing_locked", "Too many wrong codes. Wait a few minutes, then use a new code.")

        session = self._sessions.get(self._by_code.get(normalize_code(code), ""))
        # One answer for unknown, expired and foreign codes: a caller learns
        # nothing about codes that belong to other users.
        if (
            session is None
            or not session.connected
            or now > session.code_expires_at
            or session.principal.sub != principal.sub
        ):
            failures.append(now)
            if session is not None and session.principal.sub != principal.sub:
                logger.warning(
                    "Pairing code used by another user",
                    extra={"caller_sub": principal.sub, "owner_sub": session.principal.sub, "security": True},
                )
            raise BridgeError(
                "pairing_invalid",
                "This code is not valid. Codes work once, for a few minutes, and only for the account "
                "that is signed in to the TOSCA tab. Ask the user for the code currently shown in TOSCA.",
            )

        self._drop_code(session)
        replaced = None
        previous = self._sessions.get(self._active_by_sub.get(principal.sub, ""))
        if previous is not None and previous is not session:
            previous.paired = False
            self._fail_pending(previous, BridgeError("disconnected", "Another TOSCA tab was connected instead."))
            if previous.connected:
                self.new_code(previous)
                replaced = previous
            else:
                self._remove(previous)
        session.paired = True
        self._active_by_sub[principal.sub] = session.id
        failures.clear()
        return session, replaced

    def active_session(self, principal: McpPrincipal) -> BridgeSession:
        self.sweep()
        session = self._sessions.get(self._active_by_sub.get(principal.sub, ""))
        if session is None or not session.paired:
            raise BridgeError(
                "not_connected",
                "No TOSCA map is connected. Ask the user to open TOSCA, sign in, choose "
                "\"Connect AI assistant\" in the account menu and tell you the code, then call connect_map.",
            )
        if not session.connected:
            raise BridgeError(
                "disconnected",
                "The connected TOSCA tab is offline at the moment (closed, asleep or reconnecting). "
                "Try again shortly, or ask the user to open TOSCA and connect again.",
            )
        if self._wall_clock() > session.token_expires_at + TOKEN_EXPIRY_GRACE_SECONDS:
            raise BridgeError(
                "session_expired",
                "The user's sign-in in the TOSCA tab has expired. Ask them to sign in again and reconnect.",
            )
        return session

    def unpair(self, principal: McpPrincipal) -> BridgeSession | None:
        session = self._sessions.get(self._active_by_sub.pop(principal.sub, ""))
        if session is None:
            return None
        session.paired = False
        self._fail_pending(session, BridgeError("disconnected", "The assistant connection was ended."))
        if session.connected:
            self.new_code(session)
        else:
            self._remove(session)
        return session

    async def command(self, session: BridgeSession, method: str, params: dict[str, Any], timeout: float) -> Any:
        """Send one command to the tab and wait for its answer."""
        send = session.send
        if send is None:
            raise BridgeError("disconnected", "The TOSCA tab is offline.")
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        session.pending[request_id] = future
        try:
            await send({
                "type": "tosca:command",
                "requestId": request_id,
                "method": method,
                "params": params,
                "origin": "mcp",
            })
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            raise BridgeError(
                "timeout",
                f"The TOSCA tab did not answer within {int(timeout)} seconds. The command may still "
                "have been applied; read the map state before repeating it.",
            ) from exc
        finally:
            session.pending.pop(request_id, None)

    # -- housekeeping -----------------------------------------------------

    def sweep(self) -> None:
        now = self._clock()
        for session in list(self._sessions.values()):
            if session.disconnected_at is not None and now - session.disconnected_at > settings.MCP_BRIDGE_RESUME_SECONDS:
                self._remove(session)
            elif session.code is not None and now > session.code_expires_at:
                self._drop_code(session)

    def _drop_code(self, session: BridgeSession) -> None:
        if session.code is not None:
            self._by_code.pop(session.code, None)
            session.code = None

    def _fail_pending(self, session: BridgeSession, error: BridgeError) -> None:
        for future in session.pending.values():
            if not future.done():
                future.set_exception(error)

    def _remove(self, session: BridgeSession) -> None:
        self._drop_code(session)
        self._sessions.pop(session.id, None)
        if self._active_by_sub.get(session.principal.sub) == session.id:
            del self._active_by_sub[session.principal.sub]
        session.paired = False
        session.send = None


registry = BridgeRegistry()
