"""Origin guard for the localhost API.

Browsers send an ``Origin`` header on cross-site requests and WebSocket
handshakes. Any page on the internet could otherwise drive JARVIS through
``127.0.0.1``. State-changing requests and the event stream are therefore only
accepted from allowed origins (the Electron ``app://`` origin and the dev
server). Requests without an ``Origin`` header come from local non-browser
clients and are allowed.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class OriginGuard:
    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self._app = app
        self._allowed = set(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            origin = _header(scope, b"origin")
            guarded = scope["type"] == "websocket" or scope.get("method") not in _SAFE_METHODS
            if guarded and origin is not None and origin not in self._allowed:
                await self._reject(scope, receive, send)
                return
        await self._app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 4403})
            return
        body = json.dumps({"detail": "Origin not allowed"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return str(value.decode("latin-1"))
    return None
