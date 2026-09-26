"""Small, dependency-free observability helpers for the HTTP boundary."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Final
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER: Final = "X-Request-ID"
_REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONTEXT_FIELDS: Final = (
    "request_id",
    "http_method",
    "http_route",
    "http_status_code",
    "duration_ms",
    "error_code",
    "owner_id",
    "property_id",
    "room_type_id",
    "booking_id",
    "payment_id",
    "booking_status",
    "payment_status",
    "refund_status",
    "replayed",
    "required_units",
    "available_units",
    "payment_method",
)


class JsonLogFormatter(logging.Formatter):
    """Render application logs as one safe JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for field in _CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging() -> None:
    """Install one JSON stdout/stderr handler for Hotieler loggers."""

    configured_logger = logging.getLogger("hotieler")
    level_name = os.getenv("HOTIELER_LOG_LEVEL", "INFO").strip().upper()
    level = logging.getLevelNamesMapping().get(level_name, logging.INFO)
    configured_logger.setLevel(level)
    configured_logger.propagate = False

    handler = next(
        (
            candidate
            for candidate in configured_logger.handlers
            if candidate.name == "hotieler-json"
        ),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler()
        handler.name = "hotieler-json"
        handler.setFormatter(JsonLogFormatter())
        configured_logger.addHandler(handler)
    handler.setLevel(level)


def request_id_from_scope(scope: Scope) -> str:
    """Reuse a safe caller ID or generate a new opaque correlation identifier."""

    candidate = Headers(scope=scope).get(REQUEST_ID_HEADER, "").strip()
    if _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return str(uuid4())


def request_id_from_state(request_state: Any) -> str | None:
    """Read a request ID without coupling error handlers to middleware internals."""

    value = getattr(request_state, "request_id", None)
    return value if isinstance(value, str) else None


class RequestLoggingMiddleware:
    """Attach request correlation and emit one structured completion/failure event."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._logger = logging.getLogger("hotieler.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = request_id_from_scope(scope)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        started_at = perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        except Exception:
            self._logger.exception(
                "http_request_failed",
                extra=self._context(scope, request_id, 500, started_at),
            )
            raise
        else:
            self._logger.info(
                "http_request_completed",
                extra=self._context(scope, request_id, status_code, started_at),
            )

    @staticmethod
    def _context(
        scope: Scope,
        request_id: str,
        status_code: int,
        started_at: float,
    ) -> dict[str, str | int | float]:
        route = scope.get("route")
        route_path = getattr(route, "path", None)
        if not isinstance(route_path, str):
            route_path = "<unmatched>"
        return {
            "request_id": request_id,
            "http_method": str(scope.get("method", "unknown")),
            "http_route": str(route_path),
            "http_status_code": status_code,
            "duration_ms": round((perf_counter() - started_at) * 1000, 3),
        }
