"""Direct ASGI contract tests for middleware branches hidden by TestClient."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import cast

from starlette.types import Message, Receive, Scope, Send

from hotieler.api.observability import RequestLoggingMiddleware
from hotieler.api.request_limits import RequestBodyLimitMiddleware


def _http_scope(*, headers: Sequence[tuple[bytes, bytes]] = ()) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/owners",
        "raw_path": b"/api/v1/owners",
        "query_string": b"",
        "headers": list(headers),
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
    }


def _exercise_request_limit(
    incoming: list[Message],
    *,
    headers: Sequence[tuple[bytes, bytes]] = (),
    downstream_reads: int,
) -> list[Message]:
    replayed: list[Message] = []

    async def downstream(_scope: Scope, receive: Receive, _send: Send) -> None:
        for _ in range(downstream_reads):
            replayed.append(await receive())

    async def exercise() -> None:
        remaining = incoming.copy()

        async def receive() -> Message:
            return remaining.pop(0)

        async def send(_message: Message) -> None:
            raise AssertionError("A permitted request must not be answered by the limiter.")

        middleware = RequestBodyLimitMiddleware(downstream, max_bytes=16)
        await middleware(_http_scope(headers=headers), receive, send)

    asyncio.run(exercise())
    return replayed


def test_request_limit_delegates_non_http_scopes_unchanged() -> None:
    delegated_scope: Scope | None = None

    async def downstream(scope: Scope, _receive: Receive, _send: Send) -> None:
        nonlocal delegated_scope
        delegated_scope = scope

    async def exercise() -> Scope:
        scope = cast(
            Scope,
            {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}},
        )

        async def receive() -> Message:
            raise AssertionError("The middleware must not consume lifespan messages.")

        async def send(_message: Message) -> None:
            return None

        await RequestBodyLimitMiddleware(downstream)(scope, receive, send)
        return scope

    original_scope = asyncio.run(exercise())
    assert delegated_scope is original_scope


def test_request_logging_delegates_non_http_scopes_unchanged() -> None:
    delegated_scope: Scope | None = None

    async def downstream(scope: Scope, _receive: Receive, _send: Send) -> None:
        nonlocal delegated_scope
        delegated_scope = scope

    async def exercise() -> Scope:
        scope = cast(
            Scope,
            {"type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.3"}},
        )

        async def receive() -> Message:
            raise AssertionError("The middleware must not consume websocket messages.")

        async def send(_message: Message) -> None:
            return None

        await RequestLoggingMiddleware(downstream)(scope, receive, send)
        return scope

    original_scope = asyncio.run(exercise())
    assert delegated_scope is original_scope


def test_request_limit_ignores_an_invalid_content_length_and_replays_fallback() -> None:
    replayed = _exercise_request_limit(
        [{"type": "http.request", "body": b"{}", "more_body": False}],
        headers=((b"content-length", b"not-a-number"),),
        downstream_reads=2,
    )

    assert replayed == [
        {"type": "http.request", "body": b"{}", "more_body": False},
        {"type": "http.request", "body": b"", "more_body": False},
    ]


def test_request_limit_replays_disconnect_without_waiting_for_a_body() -> None:
    disconnect = cast(Message, {"type": "http.disconnect"})

    assert _exercise_request_limit([disconnect], downstream_reads=1) == [disconnect]


def test_request_limit_preserves_unexpected_events_before_the_request_body() -> None:
    unexpected = cast(Message, {"type": "hotieler.test_event"})
    body: Message = {"type": "http.request", "body": b"{}", "more_body": False}

    assert _exercise_request_limit([unexpected, body], downstream_reads=2) == [
        unexpected,
        body,
    ]
