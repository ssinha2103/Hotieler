"""Stable HTTP error envelopes for application and validation failures."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from hotieler.api.observability import REQUEST_ID_HEADER, request_id_from_state
from hotieler.domain.errors import (
    ConflictError,
    DomainValidationError,
    HotielerError,
    ResourceNotFoundError,
)

logger = logging.getLogger("hotieler.api.errors")

_HTTP_ERRORS = {
    400: ("BAD_REQUEST", "The request body could not be parsed."),
    413: ("PAYLOAD_TOO_LARGE", "The request body is too large."),
    404: ("ROUTE_NOT_FOUND", "The requested route was not found."),
    405: ("METHOD_NOT_ALLOWED", "The HTTP method is not allowed for this route."),
}


def _envelope(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def _route_path(request: Request) -> str:
    route_path = getattr(request.scope.get("route"), "path", None)
    return route_path if isinstance(route_path, str) else request.url.path


def _public_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Return the stable, JSON-safe subset of Pydantic validation errors."""

    return [
        {
            "type": str(error.get("type", "value_error")),
            "loc": [
                part if isinstance(part, (str, int)) else str(part) for part in error.get("loc", ())
            ],
            "msg": str(error.get("msg", "Invalid value.")),
        }
        for error in exc.errors()
    ]


async def hotieler_error_handler(request: Request, exc: HotielerError) -> JSONResponse:
    if isinstance(exc, ResourceNotFoundError):
        status_code = 404
    elif isinstance(exc, ConflictError):
        status_code = 409
    elif isinstance(exc, DomainValidationError):
        status_code = 422
    else:
        status_code = 500
    logger.warning(
        "http_request_rejected",
        extra={
            "request_id": request_id_from_state(request.state),
            "http_method": request.method,
            "http_route": _route_path(request),
            "http_status_code": status_code,
            "error_code": exc.code,
        },
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(_envelope(exc.code, exc.message, exc.details)),
    )


async def request_validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    is_invalid_json = any(error.get("type") == "json_invalid" for error in exc.errors())
    status_code = 400 if is_invalid_json else 422
    error_code = "BAD_REQUEST" if is_invalid_json else "REQUEST_VALIDATION_ERROR"
    message = (
        "The request body could not be parsed."
        if is_invalid_json
        else "The request could not be validated."
    )
    logger.warning(
        "http_request_rejected",
        extra={
            "request_id": request_id_from_state(request.state),
            "http_method": request.method,
            "http_route": _route_path(request),
            "http_status_code": status_code,
            "error_code": error_code,
        },
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(
            _envelope(
                error_code,
                message,
                {"errors": _public_validation_errors(exc)},
            )
        ),
    )


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    error_code, message = _HTTP_ERRORS.get(
        exc.status_code,
        ("HTTP_ERROR", "The HTTP request could not be completed."),
    )
    logger.warning(
        "http_request_rejected",
        extra={
            "request_id": request_id_from_state(request.state),
            "http_method": request.method,
            "http_route": _route_path(request),
            "http_status_code": exc.status_code,
            "error_code": error_code,
        },
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(error_code, message, {}),
        headers=exc.headers,
    )


async def unexpected_error_handler(request: Request, _exc: Exception) -> JSONResponse:
    """Hide internal exception details behind the stable public error contract."""

    request_id = request_id_from_state(request.state)
    headers = {REQUEST_ID_HEADER: request_id} if request_id is not None else None
    return JSONResponse(
        status_code=500,
        content=_envelope(
            "INTERNAL_SERVER_ERROR",
            "An unexpected internal error occurred.",
            {},
        ),
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HotielerError, hotieler_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unexpected_error_handler)
