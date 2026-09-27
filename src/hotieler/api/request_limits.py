"""ASGI request-size guard for the public HTTP boundary."""

from __future__ import annotations

from typing import Final

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BODY_BYTES: Final = 1024 * 1024


def _payload_too_large_response(max_bytes: int) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "error": {
                "code": "PAYLOAD_TOO_LARGE",
                "message": "The request body is too large.",
                "details": {"max_bytes": max_bytes},
            }
        },
    )


class RequestBodyLimitMiddleware:
    """Reject request bodies larger than the fixed application limit.

    ``Content-Length`` enables an early rejection, while counting received ASGI
    chunks also protects requests that omit the header or use chunked transfer.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                declared_bytes = int(content_length, 10)
            except ValueError:
                declared_bytes = 0
            if declared_bytes > self._max_bytes:
                await _payload_too_large_response(self._max_bytes)(scope, receive, send)
                return

        # Pre-read and bound the request before entering FastAPI. Raising from
        # ``receive`` would be translated by FastAPI's body parser into a generic
        # 400, so the size decision must happen before route parsing.
        buffered_messages: list[Message] = []
        received_bytes = 0
        while True:
            message = await receive()
            buffered_messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            received_bytes += len(message.get("body", b""))
            if received_bytes > self._max_bytes:
                await _payload_too_large_response(self._max_bytes)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        next_message = 0

        async def replay_receive() -> Message:
            nonlocal next_message
            if next_message < len(buffered_messages):
                message = buffered_messages[next_message]
                next_message += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self._app(scope, replay_receive, send)
