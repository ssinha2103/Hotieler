from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from starlette.types import Message, Scope

from hotieler.api.request_limits import RequestBodyLimitMiddleware


def test_streamed_body_without_content_length_is_limited_by_received_bytes() -> None:
    configured_limit = 1
    received_messages: list[Message] = [
        {"type": "http.request", "body": b"x", "more_body": True},
        {
            "type": "http.request",
            "body": b"x",
            "more_body": False,
        },
    ]
    sent_messages: list[Message] = []
    downstream_reached_end = False

    async def downstream(
        _scope: Scope,
        receive: Callable[[], Awaitable[Message]],
        _send: Callable[[Message], Awaitable[None]],
    ) -> None:
        nonlocal downstream_reached_end
        while (await receive()).get("more_body", False):
            pass
        downstream_reached_end = True

    async def exercise() -> None:
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/owners",
            "raw_path": b"/api/v1/owners",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "root_path": "",
            "state": {},
        }

        async def receive() -> Message:
            return received_messages.pop(0)

        async def send(message: Message) -> None:
            sent_messages.append(message)

        middleware = RequestBodyLimitMiddleware(downstream, max_bytes=configured_limit)
        await middleware(scope, receive, send)

    asyncio.run(exercise())

    assert downstream_reached_end is False
    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 413
    body = json.loads(sent_messages[1]["body"])
    assert body == {
        "error": {
            "code": "PAYLOAD_TOO_LARGE",
            "message": "The request body is too large.",
            "details": {"max_bytes": configured_limit},
        }
    }
