"""WebSocket endpoint the TOSCA frontend connects to (the map bridge).

Messages are JSON objects with a ``type``:

browser -> server
    ``tosca:hello``     first message: ``{token, resume?: {sessionId, secret}}``
    ``tosca:auth``      a refreshed access token: ``{token}``
    ``tosca:new-code``  ask for a fresh pairing code
    ``tosca:unpair``    end the assistant connection, keep the socket
    ``tosca:result``    answer to a command: ``{requestId, ok, value | error}``

server -> browser
    ``tosca:ready``     ``{sessionId, secret, paired, code?, codeExpiresIn?}``
    ``tosca:code``      ``{code, codeExpiresIn}``
    ``tosca:paired``    an assistant claimed the code: ``{client}``
    ``tosca:unpaired``  the pairing ended: ``{reason, code?, codeExpiresIn?}``
    ``tosca:command``   ``{requestId, method, params, origin}``
    ``tosca:error``     ``{code, message}``, followed by a close

Browsers cannot set an Authorization header on a WebSocket, so the access
token travels in the first message. No cookies are used.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from django.conf import settings
from starlette.websockets import WebSocket, WebSocketDisconnect

from .bridge import BridgeError, BridgeSession, format_code, registry
from .principal import McpPrincipal, principal_from_claims, verify_token_claims_async

logger = logging.getLogger(__name__)

HELLO_TIMEOUT_SECONDS = 10
# Close codes in the application range (4000-4999).
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_PROTOCOL = 4400


def origin_allowed(origin: str | None) -> bool:
    allowed = settings.MCP_BRIDGE_ALLOWED_ORIGINS
    if not allowed:
        # Nothing configured: local development only.
        return settings.DEBUG
    return origin is not None and origin.rstrip("/") in {item.rstrip("/") for item in allowed}


async def _authenticate(token: Any) -> tuple[McpPrincipal, float] | None:
    if not isinstance(token, str) or not token:
        return None
    claims = await verify_token_claims_async(token)
    if claims is None:
        return None
    return principal_from_claims(claims), float(claims["exp"])


def _code_fields(session: BridgeSession) -> dict[str, Any]:
    if session.code is None:
        return {}
    return {"code": format_code(session.code), "codeExpiresIn": registry.code_seconds_left(session)}


async def _fail(websocket: WebSocket, close_code: int, code: str, message: str) -> None:
    try:
        await websocket.send_json({"type": "tosca:error", "code": code, "message": message})
        await websocket.close(code=close_code)
    except (RuntimeError, WebSocketDisconnect):
        pass


async def bridge_endpoint(websocket: WebSocket) -> None:
    if not origin_allowed(websocket.headers.get("origin")):
        logger.warning("Map bridge refused: origin not allowed", extra={"origin": websocket.headers.get("origin")})
        await websocket.close(code=CLOSE_FORBIDDEN)
        return
    await websocket.accept()

    try:
        hello = await asyncio.wait_for(websocket.receive_json(), HELLO_TIMEOUT_SECONDS)
    except (TimeoutError, WebSocketDisconnect, ValueError):
        await _fail(websocket, CLOSE_PROTOCOL, "protocol", "Expected tosca:hello.")
        return
    if not isinstance(hello, dict) or hello.get("type") != "tosca:hello":
        await _fail(websocket, CLOSE_PROTOCOL, "protocol", "Expected tosca:hello.")
        return

    identity = await _authenticate(hello.get("token"))
    if identity is None:
        await _fail(websocket, CLOSE_UNAUTHORIZED, "unauthorized", "Sign in to TOSCA first.")
        return
    principal, token_expires_at = identity
    if not principal.may_use_mcp:
        await _fail(
            websocket, CLOSE_FORBIDDEN, "forbidden",
            f"Your account needs at least the {settings.MCP_MIN_ROLE_LEVEL} role in your organization.",
        )
        return

    send_lock = asyncio.Lock()

    async def send(message: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_json(message)

    session: BridgeSession | None = None
    resume = hello.get("resume")
    if isinstance(resume, dict):
        session = registry.resume(
            resume.get("sessionId", ""), resume.get("secret", ""), principal, send, token_expires_at
        )
    if session is None:
        try:
            session = registry.open(principal, send, token_expires_at)
        except BridgeError as error:
            await _fail(websocket, CLOSE_FORBIDDEN, error.code, str(error))
            return

    await send({
        "type": "tosca:ready",
        "sessionId": session.id,
        "secret": session.secret,
        "paired": session.paired,
        **_code_fields(session),
    })

    closed_by_client = False
    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind == "tosca:result":
                registry.resolve(
                    session,
                    str(message.get("requestId", "")),
                    message.get("ok") is True,
                    message.get("value"),
                    message.get("error"),
                )
            elif kind == "tosca:auth":
                refreshed = await _authenticate(message.get("token"))
                if refreshed is None or not registry.refresh_token(session, *refreshed):
                    # A different or invalid user: this tab is no longer the one that paired.
                    closed_by_client = True
                    await _fail(websocket, CLOSE_UNAUTHORIZED, "unauthorized", "The sign-in changed.")
                    return
            elif kind == "tosca:new-code":
                if not session.paired:
                    registry.new_code(session)
                    await send({"type": "tosca:code", **_code_fields(session)})
            elif kind == "tosca:unpair":
                if session.paired:
                    registry.unpair(session.principal)
                await send({"type": "tosca:unpaired", "reason": "browser", **_code_fields(session)})
            elif kind == "tosca:bye":
                closed_by_client = True
                return
    except WebSocketDisconnect:
        pass
    except ValueError:
        # Not JSON.
        await _fail(websocket, CLOSE_PROTOCOL, "protocol", "Messages must be JSON objects.")
    finally:
        if closed_by_client:
            registry.close(session)
        else:
            registry.detach(session)
